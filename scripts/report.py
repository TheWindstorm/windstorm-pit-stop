#!/usr/bin/env python3
"""Pit Stop report: one read-only line per running Claude Code session (or per folder).

  report.py                 every running session found in ~/.claude/sessions
  report.py --project DIR   just that folder

Columns: session name, folder, running version vs newest installed, state,
compaction point set for the folder, memory index lines (200 = load limit),
date of the folder's last /pit-stop optimize. Changes nothing.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402


def folder_window(folder):
    for f in (os.path.join(folder, ".claude", "settings.local.json"),
              os.path.join(folder, ".claude", "settings.json")):
        d = pc.read_json(f, {}) or {}
        v = (d.get("env") or {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") if isinstance(d, dict) else None
        if v:
            return int(v)
    return None


def memory_lines(folder):
    p = pc.memory_index(folder)
    return open(p).read().count("\n") if os.path.exists(p) else 0


def running_sessions():
    out = []
    for f in glob.glob(os.path.join(pc.CLAUDE_DIR, "sessions", "*.json")):
        s = pc.read_json(f, {}) or {}
        pid = s.get("pid")
        if not pid or not os.path.exists(f"/proc/{pid}") and not _alive(pid):
            continue
        if s.get("kind") not in (None, "interactive"):
            continue
        out.append(s)
    return out


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


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
        sessions = running_sessions()
        if not sessions:
            print("No running sessions found in ~/.claude/sessions; use --project DIR.")
            return 1
    for s in sorted(sessions, key=lambda x: (x.get("name") or "", x.get("cwd") or "")):
        folder = s.get("cwd") or ""
        prof = pc.read_json(os.path.join(folder, ".claude", "pit-stop.json"), {}) or {}
        rows.append({
            "name": (s.get("name") or "-")[:28], "folder": folder.replace(pc.HOME, "~"),
            "version": s.get("version") or "-", "newest": newest or "-",
            "update_waiting": bool(newest and s.get("version") not in (None, "-", newest)),
            "state": s.get("status") or "-", "window": folder_window(folder),
            "memory_lines": memory_lines(folder), "optimized": (prof.get("updated") or "-")[:10]})
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'session':28} {'version':>9} {'state':>6} {'window':>7} {'memory':>7} {'optimized':>10}  folder")
    for r in rows:
        ver = r["version"] + ("*" if r["update_waiting"] else "")
        mem = f"{r['memory_lines']}" + ("!" if r["memory_lines"] > pc.MEMORY_LOAD_LINES else "")
        print(f"{r['name']:28} {ver:>9} {r['state']:>6} {pc.fmt_k(r['window']):>7} {mem:>7} {r['optimized']:>10}  {r['folder']}")
    waiting = sum(r["update_waiting"] for r in rows)
    cut = sum(r["memory_lines"] > pc.MEMORY_LOAD_LINES for r in rows)
    print(f"\n{len(rows)} sessions; newest installed {newest}; {waiting} waiting for a restart (*); "
          f"{cut} with a memory index past the 200-line load limit (!)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
