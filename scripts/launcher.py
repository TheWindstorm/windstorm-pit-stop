#!/usr/bin/env python3
"""Pit Stop launcher: one fixed path for timers, whatever version is installed.

Claude Code keeps each plugin version in its own folder
(~/.claude/plugins/cache/<marketplace>/windstorm-pit-stop/<version>/), so a
timer that names that folder keeps running the old version after an update.
/pit-stop setup copies this file to ~/.claude/pit-stop/bin/pit-stop-restart
and points the timer at the copy. Each run it finds the installed plugin and
runs its scripts/restart_at_break.py with the same arguments:

  1. the install path Claude Code records in installed_plugins.json
     (the user-scope install first);
  2. else the highest version folder in the plugin cache.
A timer that names a version folder directly is not affected by this file:
it stays pinned until someone changes it. Standard library only.
"""
import glob
import json
import os
import re
import sys

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(os.path.expanduser("~"), ".claude"))
PLUGIN = "windstorm-pit-stop"
SCRIPT = os.path.join("scripts", "restart_at_break.py")


def vkey(path):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", os.path.basename(path.rstrip("/")))
    return tuple(int(x) for x in m.groups()) if m else (-1,)


def plugin_root(claude_dir=CLAUDE_DIR):
    try:
        with open(os.path.join(claude_dir, "plugins", "installed_plugins.json")) as f:
            plugins = json.load(f).get("plugins") or {}
    except Exception:
        plugins = {}
    rows = [r for k, v in plugins.items() if k.split("@")[0] == PLUGIN and isinstance(v, list)
            for r in v if isinstance(r, dict)]
    rows.sort(key=lambda r: (r.get("scope") != "user", [-x for x in vkey(r.get("installPath") or "")]))
    for r in rows:
        p = r.get("installPath") or ""
        if os.path.exists(os.path.join(p, SCRIPT)):
            return p
    found = [p for p in glob.glob(os.path.join(claude_dir, "plugins", "cache", "*", PLUGIN, "*"))
             if os.path.exists(os.path.join(p, SCRIPT))]
    return max(found, key=vkey) if found else None


def main():
    root = plugin_root()
    if not root:
        sys.stderr.write("pit-stop launcher: the windstorm-pit-stop plugin is not installed\n")
        return 1
    os.execv(sys.executable, [sys.executable, "-I", os.path.join(root, SCRIPT)] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
