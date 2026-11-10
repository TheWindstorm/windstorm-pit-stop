#!/usr/bin/env python3
"""Pit Stop, part 3: at the end of a turn, pass on what is waiting.

Claude Code runs this Stop hook each time a session finishes a turn. It does
at most one thing, and almost always nothing:

  1. THE BACKGROUND-JOBS ASK (fleet mode). Claude Code refuses /restart while
     background work runs. When the restart helper finds a session on an old
     version with background jobs, it leaves a small file for that session
     instead of typing into its terminal. This hook finds the file at the end
     of the session's next turn and hands the ask to Claude (exit 2, the ask
     on stderr), once. Claude then sorts its jobs: watchers it can start again
     are listed and stopped; real work in progress is left alone.
  2. THE UPDATE LINE (no fleet mode). When a newer Claude Code is installed
     than the one this session runs, the person sees one line, once per
     version: type /restart at a break. Claude does not see it and no turn
     starts. Switch it off with "nudge": false in ~/.claude/pit-stop/config.json.

It never blocks twice in a row (stop_hook_active), allows the stop on any
error, and leaves a stamp per session so the restart helper knows the plugin
is loaded there. Reads and writes only under ~/.claude/pit-stop/.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pitstop_common as pc  # noqa: E402

LOG = os.path.join(pc.STATE_DIR, "stop.log")


def ask_text(a):
    okf = a.get("okf") or ""
    return (f"Pit Stop (this plugin's own hook, set up by your user): Claude Code {a.get('newest')} is installed and this "
            f"session runs {a.get('ver')}. Claude Code will not restart a session while background work runs, and you have "
            f"background jobs. Sort them now: (A) watchers, monitors and loops that you can simply start again; "
            f"(B) real work in progress (a build, a test run, a deploy, a download). If there is any B, change nothing and "
            f"end your turn: you will be asked again later. If there is only A: (1) write one line per job you will start "
            f"again into {okf} (an empty file if none); (2) stop those jobs yourself; (3) end your turn. The restart helper "
            f"then restarts this session within a few minutes, same conversation, and tells you to start your jobs again. "
            f"Do not type /restart yourself. No reply to the user is needed for this.")


def touch(path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    sid = payload.get("session_id")
    if not sid or payload.get("stop_hook_active"):
        return 0
    key = pc.safe_id(sid)
    touch(os.path.join(pc.SEEN, key))
    askf = os.path.join(pc.ASKS, key + ".json")
    a = pc.read_json(askf, None)
    if isinstance(a, dict) and not a.get("delivered"):
        ver = pc.session_version(sid)
        if ver and pc.vkey(ver) >= pc.vkey(a.get("newest")):
            try:  # the session already runs that version: the ask is stale
                os.remove(askf)
            except OSError:
                pass
            return 0
        a["delivered"] = time.time()
        try:
            pc.write_json_atomic(askf, a)
        except Exception:
            return 0  # cannot record it: do not ask, or it would repeat every turn
        pc.append_log(LOG, f"session={key[:8]} ASK delivered ({a.get('ver')}->{a.get('newest')})")
        sys.stderr.write(ask_text(a) + "\n")
        return 2
    if pc.config().get("nudge") is False or pc.fleet_active():
        return 0
    newest = pc.newest_installed_version()
    if not newest:
        return 0
    stamp = os.path.join(pc.STATE_DIR, "nudged", key)
    try:
        with open(stamp) as f:
            if f.read().strip() == newest:
                return 0
    except OSError:
        pass
    ver = pc.session_version(sid)
    if not ver or pc.vkey(ver) >= pc.vkey(newest):
        return 0
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(newest)
    except Exception:
        return 0
    pc.append_log(LOG, f"session={key[:8]} NUDGE {ver}->{newest}")
    sys.stdout.write(json.dumps({"systemMessage": f"Pit Stop: Claude Code {newest} is installed; this session runs {ver}. "
                                                  f"Type /restart at a break to move onto it (same conversation)."}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
