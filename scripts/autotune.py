#!/usr/bin/env python3
"""Pit Stop weekly tune: keep a folder's two compaction points fitted to its own history.

  autotune.py --project DIR           do nothing unless weekly tuning is on
                                      ("auto_tune": true in ~/.claude/pit-stop/config.json,
                                      set by /pit-stop setup) and the folder was
                                      last tuned 7+ days ago
  autotune.py --project DIR --force   tune now, whatever the switch and the date
  autotune.py --project DIR --dry     say what it would do, write nothing

The SessionStart hook starts this in the background, so it never slows a
session down. It runs the same analysis as /pit-stop optimize, moves each
point at most ONE step from where it is now (so one odd week cannot swing a
folder), writes the settings (a backup first, the newest 3 kept) and the
profile, and adds one line to ~/.claude/pit-stop/autotune.log. Folders with
fewer than 2 compactions in the history keep their window. The new points
take effect at each session's next start.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402
import optimize as op  # noqa: E402

WEEK = 7 * 86400
LOG = os.path.join(pc.STATE_DIR, "autotune.log")


def due(stamp, now, force):
    if force:
        return True
    try:
        return now - os.path.getmtime(stamp) >= WEEK
    except OSError:
        return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    project = os.path.abspath(a.project)
    if not a.force and not a.dry and pc.config().get("auto_tune") is not True:
        return 0
    stamp = os.path.join(pc.STATE_DIR, "tuned", pc.project_key(project))
    now = time.time()
    if not due(stamp, now, a.force or a.dry):
        return 0
    if not a.dry:  # stamp first: twenty sessions starting together tune the folder once
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(int(now)))
    try:
        os.nice(19)
    except Exception:
        pass
    m = op.analyze(project, a.days)
    if not m["transcripts"]:
        pc.append_log(LOG, f"{project}: no history in {a.days} days; nothing to tune")
        return 0
    rec = op.recommend(m, max_steps=1)
    several = len([p for p in pc.transcripts(project, 3)]) > 1
    lanes = op.lane_recs(project, a.days, max_steps=1) if several else {}
    was_i, was_w = m["current_idle_point"], m["current_window"]
    line = (f"{project}: idle point {pc.fmt_k(was_i)} -> {pc.fmt_k(rec['idle_point'])}, "
            f"window {pc.fmt_k(was_w)} -> {pc.fmt_k(rec['window'])}, {len(lanes)} lane profile(s); "
            f"{m['prompts']} prompts, {m['compactions']} compactions")
    if a.dry:
        print("DRY " + line)
        for w in rec["why"]:
            print("  - " + w)
        return 0
    s, _, _ = op.apply(project, rec, m, lanes)
    pc.prune_backups(s)
    pc.append_log(LOG, line)
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
