#!/usr/bin/env python3
"""Pit Stop, part 1: hold AUTOMATIC compaction until a break in the action.

Claude Code runs this PreCompact hook just before every compaction, with JSON
on stdin.
  exit 0 and NO stdout -> compaction goes ahead (stdout on exit 0 would become
                          compaction instructions, so it always stays empty)
  exit 2               -> compaction is skipped this time; Claude Code asks
                          again after the next step

Rules:
  - manual /compact                          -> always go ahead
  - at a BREAK                               -> go ahead. A break = the newest
    transcript entry is a new prompt, or Claude's turn ended with plain text.
  - MID-ACTION (newest entry is a tool call or a tool result, or thinking)
                                             -> hold
  - CEILING reached                          -> go ahead anyway. The ceiling is,
    in order: env PIT_STOP_CEILING; "ceiling" in <project>/.claude/pit-stop.json
    (written by /pit-stop optimize); else 110% of the context size at the
    first hold of this compaction (a safe default for any model size).
  - anything unexpected (no transcript, bad JSON, any error) -> go ahead
One log line per decision, counts only: ~/.claude/pit-stop/precompact.log
"""
import datetime as dt
import json
import os
import sys

TAIL_BYTES = 4_000_000
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
STATE = os.path.join(CLAUDE_DIR, "pit-stop")
LOG = os.path.join(STATE, "precompact.log")
DEFAULT_FLEX = float(os.environ.get("PIT_STOP_DEFAULT_FLEX", "1.10"))


def log(session, trigger, tokens, decision, why):
    try:
        os.makedirs(STATE, exist_ok=True)
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


def ceiling_for(payload, tokens):
    env = os.environ.get("PIT_STOP_CEILING")
    if env and env.isdigit():
        return int(env), "env"
    cwd = payload.get("cwd") or ""
    prof = os.path.join(cwd, ".claude", "pit-stop.json") if cwd else ""
    if prof and os.path.exists(prof):
        try:
            c = int(json.load(open(prof)).get("ceiling") or 0)
            if c > 0:
                return c, "profile"
        except Exception:
            pass
    # default: 110% of the context size at this compaction's first hold
    p = hold_state_path(payload.get("session_id"))
    first = None
    try:
        first = int(open(p).read().strip())
    except Exception:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(str(tokens))
            first = tokens
        except Exception:
            return tokens, "default-unwritable"  # cannot track: never hold
    return int(first * DEFAULT_FLEX), "default"


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
    return code


if __name__ == "__main__":
    sys.exit(main())
