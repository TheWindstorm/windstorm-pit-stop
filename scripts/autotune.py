#!/usr/bin/env python3
"""Pit Stop tune: keep a folder's two compaction points fitted to its own history.

  autotune.py --project DIR           do nothing unless tuning is on
                                      ("auto_tune": true in ~/.claude/pit-stop/config.json,
                                      set by /pit-stop setup) and the folder was
                                      last tuned a day or more ago ("tune_every_days",
                                      1 to 30, default 1)
  autotune.py --project DIR --force   tune now, whatever the switch and the date
  autotune.py --project DIR --dry     say what it would do, write nothing

The SessionStart hook starts this in the background, so it never slows a
session down. It runs the same analysis as /pit-stop optimize, moves each
point at most ONE step from where it is now (so one odd day cannot swing a
folder), writes the settings (a backup first, the newest 3 kept) and the
profile, and adds one line to ~/.claude/pit-stop/autotune.log. Folders with
fewer than 2 compactions in the history keep their window. The new points
take effect at each session's next start.

It checks its own work. Each move of the window is kept in
~/.claude/pit-stop/tuned/<folder key>.json. The next move waits until the last
one has run at least 3 automatic compactions at its new point; a lowering after
which sessions re-read much more of what the compaction dropped is undone (see
optimize.check_last_move).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402
import optimize as op  # noqa: E402

DAY = 86400
LOG = os.path.join(pc.STATE_DIR, "autotune.log")
KEEP_MOVES = 20


def every(cfg=None):
    """Seconds between tunes of one folder: "tune_every_days" (1 to 30, default 1)."""
    v = (pc.config() if cfg is None else cfg).get("tune_every_days", 1)
    v = v if isinstance(v, (int, float)) and not isinstance(v, bool) else 1
    return max(1, min(30, v)) * DAY


def due(stamp, now, force):
    if force:
        return True
    try:
        return now - os.path.getmtime(stamp) >= every() - 600   # a little slack: a daily run never slips a day
    except OSError:
        return True


def history_path(project):
    return os.path.join(pc.STATE_DIR, "tuned", pc.project_key(project) + ".json")


def record(hist, was, rec, now):
    """The folder's tune history after this run: a window move is added (the
    newest KEEP_MOVES kept), and a floor set by an undo is kept. Pure."""
    hist = op.clean_history(hist)
    moves = hist["moves"]
    if rec["window"] != was:     # a first move (no window set before) is recorded too: it is measured like any other
        moves.append({"at": int(now), "from": was, "to": rec["window"]})
    hist["moves"] = moves[-KEEP_MOVES:]
    if rec.get("floor_set"):
        hist["floor"] = rec["floor_set"]
    return hist


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
    try:
        m = op.analyze(project, a.days)
        if not m["transcripts"]:
            pc.append_log(LOG, f"{project}: no history in {a.days} days; nothing to tune")
            return 0
        hist = op.clean_history(pc.read_json(history_path(project), {}))
        rec = op.recommend(m, max_steps=1, history=hist, now=now)
        several = len(pc.transcripts(project, 3)) > 1
        lanes = op.lane_recs(project, a.days, max_steps=1) if several else {}
    except Exception as e:
        pc.append_log(LOG, f"{project}: could not read its history ({type(e).__name__}: {str(e)[:100]}); left as it is")
        return 0
    was_i, was_w = m["current_idle_point"], m["current_window"]
    line = (f"{project}: idle point {pc.fmt_k(was_i)} -> {pc.fmt_k(rec['idle_point'])}, "
            f"window {pc.fmt_k(was_w)} -> {pc.fmt_k(rec['window'])}, {len(lanes)} lane profile(s); "
            f"{m['prompts']} prompts, {m['compactions']} compactions")
    if a.dry:
        print("DRY " + line)
        for w in rec["why"]:
            print("  - " + w)
        return 0
    try:
        s, _, _ = op.apply(project, rec, m, lanes)
    except SystemExit as e:
        pc.append_log(LOG, f"{project}: not written: {e}")
        print(e)
        return 1
    pc.prune_backups(s)
    pc.write_json_atomic(history_path(project), record(hist, was_w, rec, now), 0o600)
    pc.append_log(LOG, line + ("; " + rec["why"][1] if len(rec["why"]) > 1 and rec["why"][1].startswith(("waiting", "undo"))
                               else ""))
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
