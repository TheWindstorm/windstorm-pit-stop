#!/usr/bin/env python3
"""Pit Stop, part 4: the clean windshield, and the daily tune.

Claude Code runs this SessionStart hook when a session starts, resumes, or
comes back from a compaction. Whatever it prints is added to the session's
context; it never starts a turn.

  - After a COMPACTION it prints a three-line checklist: check your background
    jobs, re-read only what the next step needs, say so if the summary leaves
    the next step unclear. Switch it off with "after_compact_note": false in
    ~/.claude/pit-stop/config.json.
  - On a RESUME it writes the time to ~/.claude/pit-stop/started/<session id> (G1.17).
  - On a normal START or RESUME it prints nothing. If tuning is on
    ("auto_tune": true, set by /pit-stop setup), the folder is on
    "tune_folders" (if there is a list) and its tune is due ("tune_every_days",
    default 1), it starts scripts/autotune.py for this folder in the
    background and returns at once. The Stop hook does the same at the end of
    every turn, so a session that stays open for weeks is tuned too.
Any error: print nothing, exit 0.
"""
import json
import os
import sys
sys.dont_write_bytecode = True   # rule 23: no __pycache__ beside the plugin's code (G12.9)
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import pitstop_common as pc  # noqa: E402

AFTER_COMPACT = ("Pit Stop: this session's context was just compacted. Before you continue: "
                 "(1) check that the background jobs and watchers you rely on are still running, and start again any "
                 "that are not; (2) re-read only the files the next step needs; (3) if the summary leaves the next "
                 "step unclear, say so instead of guessing.")


def stamp_resume(session_id):
    """G1.17: record when this session was resumed, so the PreCompact hook can tell a tool call that a
    restart cut off (dated before this) from one still running. Only "resume": a fresh start, a /clear or a
    compaction never turns a running tool call into a break."""
    if not session_id:
        return
    try:
        d = os.path.join(pc.STATE_DIR, "started")
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, pc.safe_id(session_id) + ".tmp-pitstop")
        with open(tmp, "w") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
        os.replace(tmp, os.path.join(d, pc.safe_id(session_id)))
    except OSError:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    cfg = pc.config()
    source = payload.get("source")
    if source == "compact":
        if cfg.get("after_compact_note") is not False:
            sys.stdout.write(AFTER_COMPACT)
        return 0
    cwd = payload.get("cwd")
    if source == "resume":
        stamp_resume(payload.get("session_id"))
    if source in ("startup", "resume"):
        pc.start_tune(cwd, cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
