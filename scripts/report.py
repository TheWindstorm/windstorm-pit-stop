#!/usr/bin/env python3
"""Pit Stop report: one read-only line per running Claude Code session (or per folder).

  report.py                 every running session
  report.py --project DIR   just that folder

Columns: session name, running version vs newest installed, state, the idle
point and the window in force (a lane's own profile first, then its folder's),
memory index lines (200 = load limit), date of the last tune, folder.
Changes nothing.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402


def memory_lines(folder):
    p = pc.memory_index(folder)
    return open(p).read().count("\n") if os.path.exists(p) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    newest = pc.newest_installed_version()
    rows = []
    if a.project:
        sessions = [{"name": "-", "cwd": os.path.abspath(a.project), "version": "-", "status": "-"}]
    else:
        sessions, _ = pc.live_sessions()
        if not sessions:
            print("No running sessions found; use --project DIR.")
            return 1
    for s in sorted(sessions, key=lambda x: (x.get("name") or "", x.get("cwd") or "")):
        folder = s.get("cwd") or ""
        prof = pc.folder_profile(folder)
        pts = pc.points(folder, s.get("sessionId"))
        ver = s.get("version")
        rows.append({
            "name": (s.get("name") or "-")[:28], "folder": folder.replace(pc.HOME, "~"),
            "version": ver or "-", "newest": newest or "-",
            "update_waiting": bool(newest and ver not in (None, "-") and pc.vkey(ver) < pc.vkey(newest)),
            "state": s.get("status") or "-", "idle_point": pts["idle_point"], "window": pts["window"],
            "points_from": pts["source"],
            "memory_lines": memory_lines(folder), "optimized": (prof.get("updated") or "-")[:10]})
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'session':28} {'version':>9} {'state':>7} {'idle pt':>7} {'window':>7} {'memory':>7} {'tuned':>10}  folder")
    for r in rows:
        ver = r["version"] + ("*" if r["update_waiting"] else "")
        mem = f"{r['memory_lines']}" + ("!" if r["memory_lines"] > pc.MEMORY_LOAD_LINES else "")
        idle = pc.fmt_k(r["idle_point"]) + ("'" if r["points_from"] == "lane" else "")
        print(f"{r['name']:28} {ver:>9} {r['state']:>7} {idle:>7} {pc.fmt_k(r['window']):>7} {mem:>7} {r['optimized']:>10}  {r['folder']}")
    waiting = sum(r["update_waiting"] for r in rows)
    cut = sum(r["memory_lines"] > pc.MEMORY_LOAD_LINES for r in rows)
    print(f"\n{len(rows)} sessions; newest installed {newest}; {waiting} waiting for a restart (*); "
          f"{cut} with a memory index past the 200-line load limit (!). "
          f"Idle pt '-' = Claude Code's default ({pc.fmt_k(pc.IDLE_DEFAULT)}); ' = the lane's own profile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
