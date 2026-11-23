#!/usr/bin/env python3
"""Pit Stop launcher: one fixed path for timers, whatever version is installed.

Claude Code keeps each plugin version in its own folder
(~/.claude/plugins/cache/<marketplace>/windstorm-pit-stop/<version>/), so a
timer that names that folder keeps running the old version after an update.
/pit-stop setup copies this file to ~/.claude/pit-stop/bin/pit-stop-restart
and points the timer at the copy. Each run it finds the installed plugin and
runs its scripts/restart_at_break.py with the same arguments: the install
path Claude Code records in installed_plugins.json (the user-scope install
first); if no recorded path holds the helper, the highest version folder in
the plugin cache. It never runs code from a folder Claude Code marked
orphaned (an .orphaned_at file).

Once the plugin is gone it runs nothing and says so in the helper's log:
seen 2026-11-23 (Claude Code 2.1.280), `claude plugin uninstall` empties the
plugin's row in installed_plugins.json but leaves its cache folder, marked
.orphaned_at. A registry that cannot be read is not "gone": that pass runs
nothing, and the next one reads it again.
A timer that names a version folder directly is not affected by this file:
it stays pinned until someone changes it. Standard library only.
"""
import glob
import json
import os
import re
import sys
import time

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(os.path.expanduser("~"), ".claude"))
PLUGIN = "windstorm-pit-stop"
SCRIPT = os.path.join("scripts", "restart_at_break.py")
ORPHANED = ".orphaned_at"   # the file Claude Code leaves in a plugin folder it no longer uses
SAY = {
    "gone": "Pit Stop is not installed (Claude Code's plugin registry has no windstorm-pit-stop row)",
    "orphaned": "this Pit Stop folder was left by Claude Code after an uninstall or update (it carries .orphaned_at)",
    "unreadable": "Claude Code's plugin registry could not be read; no session changed this pass, the next pass reads it again",
    "missing": "no folder Claude Code recorded for Pit Stop holds its helper",
}


def vkey(path):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", os.path.basename(path.rstrip("/")))
    return tuple(int(x) for x in m.groups()) if m else (-1,)


def registry_rows(claude_dir=None):
    """Pit Stop's rows in plugins/installed_plugins.json: a list, empty when the file reads fine and has none
    (or there is no file: no plugin is installed), or None when it could not be read."""
    try:
        with open(os.path.join(claude_dir or CLAUDE_DIR, "plugins", "installed_plugins.json")) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None
    return [r for k, v in plugins.items() if k.split("@")[0] == PLUGIN and isinstance(v, list)
            for r in v if isinstance(r, dict)]


def orphaned(path):
    return bool(path) and os.path.exists(os.path.join(path, ORPHANED))


def state(root=None, claude_dir=None):
    """May code in ROOT act on sessions now? "installed"; or "orphaned" (Claude Code marked ROOT), "gone" (the
    registry reads fine and has no Pit Stop row), "unreadable" (it could not be read)."""
    if orphaned(root):
        return "orphaned"
    rows = registry_rows(claude_dir)
    if rows is None:
        return "unreadable"
    return "installed" if rows else "gone"


def find(claude_dir=None):
    """(folder to run, None) or (None, why): why is a key of SAY."""
    claude_dir = claude_dir or CLAUDE_DIR
    rows = registry_rows(claude_dir)
    if rows is None:
        return None, "unreadable"
    if not rows:
        return None, "gone"
    rows.sort(key=lambda r: (r.get("scope") != "user", [-x for x in vkey(r.get("installPath") or "")]))
    paths = [r.get("installPath") or "" for r in rows]
    for p in paths:
        if os.path.exists(os.path.join(p, SCRIPT)) and not orphaned(p):
            return p, None
    found = [p for p in glob.glob(os.path.join(claude_dir, "plugins", "cache", "*", PLUGIN, "*"))
             if os.path.exists(os.path.join(p, SCRIPT)) and not orphaned(p)]
    if found:
        return max(found, key=vkey), None
    return None, ("orphaned" if any(orphaned(p) for p in paths) else "missing")


def timer_off():
    """The one command that takes off the timer /pit-stop setup --fleet installed: the one `setup.py --undo` runs
    (held to setup's own words by the G12.8 test). On macOS none has been run as printed yet: None."""
    if sys.platform == "darwin":
        return None
    return "systemctl --user disable --now pit-stop-restart.timer"


def gone_advice():
    """What a run that found Pit Stop gone tells the person, after saying so."""
    return ("(a copy run with --plugin-dir or from a working folder has no row either). If a timer runs this, take it off: "
            + (timer_off() or "see \"Leaving Pit Stop\" in Pit Stop's README"))


def plugin_root(claude_dir=None):
    return find(claude_dir)[0]


def note(line, claude_dir=None):
    """Print LINE; a live run also keeps it in the helper's own log (restart.log), as the helper would."""
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + line
    print(line)
    if "--live" not in sys.argv:
        return
    try:
        base = os.path.join(claude_dir or CLAUDE_DIR, "pit-stop", "restart")
        os.makedirs(base, exist_ok=True)
        log = os.path.join(base, "restart.log")
        if os.path.exists(log) and os.path.getsize(log) > 5_000_000:
            os.replace(log, log + ".1")
        with open(log, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main():
    root, why = find()
    if not root:
        note(f"pit-stop launcher: nothing run: {SAY[why]}" + (" " + gone_advice() if why == "gone" else ""))
        return 0
    env = dict(os.environ, PIT_STOP_LAUNCHER="1")   # the helper then knows it follows plugin updates
    os.execve(sys.executable, [sys.executable, "-I", os.path.join(root, SCRIPT)] + sys.argv[1:], env)


if __name__ == "__main__":
    sys.exit(main())
