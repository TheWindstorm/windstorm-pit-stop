#!/usr/bin/env python3
"""Pit Stop, part 1: hold AUTOMATIC compaction until a break in the action.

Claude Code runs this PreCompact hook just before every compaction, with JSON
on stdin.
  exit 0               -> compaction goes ahead; whatever is on stdout is added
                          to the summary's instructions (Claude Code reads up
                          to 10,000 characters)
  exit 2               -> compaction is skipped this time; Claude Code asks
                          again after the next step

Rules:
  - manual /compact                          -> always go ahead
  - at a BREAK                               -> go ahead. A break = the newest
    transcript entry is a new prompt, or Claude's turn ended with plain text.
  - MID-ACTION (newest entry is a tool call or a tool result, or thinking)
                                             -> hold
  - CEILING reached                          -> go ahead anyway. The ceiling is,
    in order: env PIT_STOP_CEILING; "ceiling" in the folder's profile
    (~/.claude/pit-stop/folders/, written by /pit-stop optimize; the 0.1.x
    file <project>/.claude/pit-stop.json is still read); else 110% of the context size at the
    first hold of this compaction (a safe default for any model size).
    Any configured ceiling is CLAMPED to at most 1.5x the first hold and 95% of
    the model's context size (1M if the first hold is over 200K, else 200K), so
    a project file shipped in a cloned repo can never hold compaction until the
    context overflows.
  - anything unexpected (no transcript, bad JSON, any error) -> go ahead
When an automatic compaction goes ahead at a break or at the ceiling, the hook
prints a short list of what the summary must keep (the task in progress, open
to-dos, background jobs and how to start them again, files being changed,
decisions). ~/.claude/pit-stop/config.json can switch that off
("compact_instructions": false) or add a note of your own (a string). It is
read from the user's own folder only, never from a project. A manual /compact
and every error case print nothing.
One log line per decision, counts only: ~/.claude/pit-stop/precompact.log
(rotated to precompact.log.1 at 5 MB).
"""
import datetime as dt
import json
import os
import re
import sys

TAIL_BYTES = 4_000_000
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
STATE = os.path.join(CLAUDE_DIR, "pit-stop")
LOG = os.path.join(STATE, "precompact.log")
DEFAULT_FLEX = float(os.environ.get("PIT_STOP_DEFAULT_FLEX", "1.10"))
MAX_FLEX = 1.5          # a configured ceiling is at most 1.5x the first hold
MAX_SHARE = 0.95        # ... and at most 95% of the model's context size
LOG_MAX_BYTES = 5_000_000
KEEP = ("Pit Stop notes for this summary. Keep, in this order: "
        "(1) the task in progress and its exact next step; "
        "(2) open to-dos and promises, with their dates and who they are owed to; "
        "(3) background jobs, watchers, timers and scheduled tasks this session started, and how to start each again; "
        "(4) files being changed (full paths), uncommitted work, and branch and pull request numbers; "
        "(5) decisions made and the user's standing instructions from this session, in the user's own words where short; "
        "(6) the names and ids needed to continue (session names, hosts, ticket and message ids). "
        "Leave out finished work that has no open follow-up.")
KEEP_MID = (" This compaction comes in the middle of a task: say which step was in flight and what the last "
            "tool call was doing, so the work can be picked up exactly there.")
EXTRA_MAX = 4000


def instructions(mid):
    """Text for stdout when an automatic compaction goes ahead; '' when switched off."""
    try:
        with open(os.path.join(STATE, "config.json")) as f:
            v = json.load(f).get("compact_instructions", True)
    except Exception:
        v = True
    if v is False:
        return ""
    out = KEEP + (KEEP_MID if mid else "")
    if isinstance(v, str) and v.strip():
        out += " Also: " + v.strip()[:EXTRA_MAX]
    return out


def log(session, trigger, tokens, decision, why):
    try:
        os.makedirs(STATE, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX_BYTES:
            os.replace(LOG, LOG + ".1")
        t = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG, "a") as f:
            f.write(f"{t} session={str(session)[:8]} trigger={trigger} tokens={tokens} {decision} {why}\n")
    except Exception:
        pass


def tail_messages(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]
    out = []
    for line in lines:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") in ("user", "assistant") and not o.get("isSidechain"):
            out.append(o)
    return out


def kinds(entry):
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return ["text"]
    if isinstance(c, list):
        return [x.get("type") for x in c if isinstance(x, dict)]
    return []


def context_tokens(msgs):
    for o in reversed(msgs):
        if o.get("type") == "assistant":
            u = (o.get("message") or {}).get("usage") or {}
            t = sum(int(u.get(k) or 0) for k in (
                "input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "output_tokens"))
            if t > 0:
                return t
    return None


def hold_state_path(session):
    safe = "".join(ch for ch in str(session) if ch.isalnum() or ch == "-")[:64] or "unknown"
    return os.path.join(STATE, "holds", safe)


def first_hold(payload, tokens):
    """Context size at this compaction's first hold, kept per session until the
    next ALLOW. A stale file left by a crash can only LOWER the next ceiling
    (it holds a smaller, older size), which is the safe direction."""
    p = hold_state_path(payload.get("session_id"))
    try:
        with open(p) as f:
            return int(f.read().strip())
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(str(tokens))
        return tokens
    except Exception:
        return None


def configured_ceiling(payload):
    env = os.environ.get("PIT_STOP_CEILING")
    if env and env.isdigit():
        return int(env), "env"
    cwd = payload.get("cwd") or ""
    if not cwd:
        return None, None
    # the user-level profile (0.2.0+), then the 0.1.x file inside the folder
    key = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd))
    for prof in (os.path.join(STATE, "folders", key + ".json"), os.path.join(cwd, ".claude", "pit-stop.json")):
        try:
            with open(prof) as f:
                c = int(json.load(f).get("ceiling") or 0)
            if c > 0:
                return c, "profile"
        except Exception:
            continue
    return None, None


def ceiling_for(payload, tokens):
    first = first_hold(payload, tokens)
    if first is None:
        return tokens, "unwritable"  # cannot track holds: never hold
    limit = 1_000_000 if first > 200_000 else 200_000
    cap = int(min(first * MAX_FLEX, limit * MAX_SHARE))
    c, src = configured_ceiling(payload)
    if c is None:
        return min(int(first * DEFAULT_FLEX), cap), "default"
    if c > cap:
        return cap, f"{src}-clamped"
    return c, src


def clear_hold(session):
    try:
        os.remove(hold_state_path(session))
    except Exception:
        pass


def decide(payload):
    trigger = payload.get("trigger", "?")
    if trigger != "auto":
        return 0, trigger, None, "manual-or-unknown"
    path = payload.get("transcript_path")
    if not path or not os.path.exists(path):
        return 0, trigger, None, "no-transcript"
    msgs = tail_messages(path)
    if not msgs:
        return 0, trigger, None, "no-messages"
    tokens = context_tokens(msgs)
    if tokens is None:
        return 0, trigger, None, "no-usage"
    last = msgs[-1]
    k = kinds(last)
    if last.get("type") == "user":
        mid = "tool_result" in k
        why = "mid-action:tool-result" if mid else "break:new-prompt"
    elif "tool_use" in k:
        mid, why = True, "mid-action:tool-use"
    elif "text" in k:
        mid, why = False, "break:turn-ended"
    else:
        mid, why = True, "mid-action:thinking"
    if not mid:
        return 0, trigger, tokens, why
    ceiling, src = ceiling_for(payload, tokens)
    if tokens >= ceiling:
        return 0, trigger, tokens, f"ceiling>={ceiling}({src})"
    return 2, trigger, tokens, f"{why} ceiling={ceiling}({src})"


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        log("?", "?", None, "ALLOW", "bad-input")
        return 0
    try:
        code, trigger, tokens, why = decide(payload)
    except Exception as e:  # never block on our own bug
        log(payload.get("session_id"), payload.get("trigger"), None, "ALLOW", f"error:{type(e).__name__}")
        return 0
    if code == 0:
        clear_hold(payload.get("session_id"))
    log(payload.get("session_id"), trigger, tokens, "HOLD" if code == 2 else "ALLOW", why)
    if code == 2:
        sys.stderr.write(f"pit-stop: holding auto-compact until a break (context ~{tokens} tokens)\n")
    elif trigger == "auto" and why.startswith(("break:", "ceiling")):
        sys.stdout.write(instructions(mid=why.startswith("ceiling")))
    return code


if __name__ == "__main__":
    sys.exit(main())
