#!/usr/bin/env python3
"""Pit Stop setup: one command to switch everything on.

  setup.py                    show the plan; change nothing
  setup.py --apply            do it
  setup.py --fleet [--apply]  also set up fleet mode (tmux): the launcher and
                              a timer that runs the pit stop every few minutes
Options:
  --no-auto-tune              leave tuning off
  --plugin-updates pit-stop|all|off
                              fleet mode: what the helper keeps up to date
                              before it restarts sessions (default pit-stop;
                              written only into a NEW restart.json)
  --no-tune-now               do not tune the folders of the running sessions now
  --no-self-update            leave Claude Code's auto-update for Pit Stop's
                              marketplace as it is
  --interval MIN              minutes between fleet runs (default 5)
  --days N                    days of history to read when tuning (default 14)
  --budget economy|balanced|roomy
                              how hard tuning holds down usage (kept in
                              config.json; default balanced). economy: every
                              window at most 400K, a heavy folder 300K, none
                              raised. balanced: only heavy folders are capped.
                              roomy: tune for memory only.

What --apply does:
  1. Tuning ON ("auto_tune": true in ~/.claude/pit-stop/config.json):
     each folder's two compaction points are re-fitted to its own history once
     a day, one step at a time, in the background; each step is measured
     before the next, and a lowering that cost context is undone.
  2. Tunes the folder of every running session now (and the current folder).
     Settings files are backed up first; only Pit Stop's two keys change.
  3. Switches on Claude Code's own auto-update for the marketplace Pit Stop
     came from ("autoUpdate": true on its entry under extraKnownMarketplaces in
     ~/.claude/settings.json; backed up first). Claude Code leaves it off for
     marketplaces other than Anthropic's, so without this an install stays on
     the release it was installed with (one machine sat on 0.1.4 for days).
With --fleet it also:
  4. Copies the launcher to ~/.claude/pit-stop/bin/pit-stop-restart, so the
     timer keeps working across plugin updates.
  5. Writes ~/.claude/pit-stop/restart.json ONLY if there is none (it finds
     which tmux server holds your sessions, and sets plugin_updates: the
     helper then brings this plugin, or all plugins, up to date with Claude
     Code's own commands before it restarts anyone, so one restart brings the
     newest Claude Code and the newest plugins). An existing file is never
     changed.
  6. Installs a timer (systemd user timer on Linux, launchd on macOS) that
     runs the launcher with --live. If something already schedules
     restart_at_break.py, it changes nothing and prints the one line to edit.
Nothing here needs sudo. Undo: delete the timer files it names, and set
"auto_tune": false (and "plugin_updates": "off" in restart.json).
"""
import argparse
import glob
import os
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402
import optimize as op  # noqa: E402

BIN = os.path.join(pc.STATE_DIR, "bin", "pit-stop-restart")
UNIT_DIR = os.path.join(pc.HOME, ".config", "systemd", "user")
PLIST = os.path.join(pc.HOME, "Library", "LaunchAgents", "ai.thewindstorm.pit-stop-restart.plist")


def run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def is_wsl():
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def tmux_servers():
    """{socket name or None: set of pane ttys} for every tmux server of this user."""
    out = {}
    names = [None]
    for d in glob.glob(f"/tmp/tmux-{os.getuid()}/*") + glob.glob(os.path.join(os.environ.get("TMUX_TMPDIR", "/nonexistent"), f"tmux-{os.getuid()}", "*")):
        n = os.path.basename(d)
        if n != "default" and n not in names:
            names.append(n)
    for n in names:
        r = run(["tmux"] + (["-L", n] if n else []) + ["list-panes", "-a", "-F", "#{pane_tty}"], 15)
        if r and r.returncode == 0:
            out[n] = set(r.stdout.split())
    return out


def session_tty(pid):
    try:
        return os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        r = run(["ps", "-o", "tty=", "-p", str(pid)], 10)
        t = r.stdout.strip() if r else ""
        return f"/dev/{t}" if t and t != "??" else None


def already_scheduled():
    """Paths of timers or cron lines that already run restart_at_break.py or the launcher."""
    hits = []
    for f in glob.glob(os.path.join(UNIT_DIR, "*.service")) + glob.glob(os.path.join(pc.HOME, "Library", "LaunchAgents", "*.plist")):
        try:
            with open(f, errors="replace") as fh:
                t = fh.read()
        except OSError:
            continue
        if "restart_at_break.py" in t or "pit-stop-restart" in t:
            hits.append(f)
    r = run(["crontab", "-l"], 10)
    if r and r.returncode == 0 and ("restart_at_break.py" in r.stdout or "pit-stop-restart" in r.stdout):
        hits.append("crontab")
    return hits


def timer_path():
    dirs = [os.path.dirname(p) for p in (shutil.which("tmux"), shutil.which("claude")) if p]
    out = []
    for d in dirs + ["/usr/local/bin", "/usr/bin", "/bin"]:
        if d not in out:
            out.append(d)
    return ":".join(out)


def install_timer(interval, apply):
    py = sys.executable
    if platform.system() == "Darwin":
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.thewindstorm.pit-stop-restart</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>-I</string><string>{BIN}</string><string>--live</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{timer_path()}</string></dict>
  <key>StartInterval</key><integer>{interval * 60}</integer>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
"""
        print(f"  timer: {PLIST} (launchd, every {interval} min)")
        if apply:
            os.makedirs(os.path.dirname(PLIST), exist_ok=True)
            with open(PLIST, "w") as f:
                f.write(body)
            r = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", PLIST])
            print("    loaded" if r and r.returncode == 0 else f"    written; load it with: launchctl bootstrap gui/{os.getuid()} {PLIST}")
        return
    svc, tmr = os.path.join(UNIT_DIR, "pit-stop-restart.service"), os.path.join(UNIT_DIR, "pit-stop-restart.timer")
    print(f"  timer: {tmr} (systemd user timer, every {interval} min)")
    if not apply:
        return
    os.makedirs(UNIT_DIR, exist_ok=True)
    with open(svc, "w") as f:
        f.write(f"[Unit]\nDescription=Windstorm Pit Stop: restart at the break (fleet mode)\n\n[Service]\nType=oneshot\n"
                f"Environment=PATH={timer_path()}\nExecStart={py} -I {BIN} --live\nTimeoutStartSec=600\nNice=10\n")
    with open(tmr, "w") as f:
        f.write(f"[Unit]\nDescription=Windstorm Pit Stop every {interval} minutes\n\n[Timer]\nOnBootSec=3min\n"
                f"OnUnitActiveSec={interval}min\nAccuracySec=20s\n\n[Install]\nWantedBy=timers.target\n")
    ok = all((r := run(c)) and r.returncode == 0 for c in (["systemctl", "--user", "daemon-reload"],
                                                           ["systemctl", "--user", "enable", "--now", "pit-stop-restart.timer"]))
    print("    enabled and started" if ok else "    written; start it with: systemctl --user daemon-reload && "
                                               "systemctl --user enable --now pit-stop-restart.timer")


SETTINGS = os.path.join(pc.CLAUDE_DIR, "settings.json")


def own_marketplace(claude_dir=None):
    """(name, source) of the marketplace this plugin was installed from for the user, or (None, None)."""
    claude_dir = claude_dir or pc.CLAUDE_DIR
    rows = (pc.read_json(os.path.join(claude_dir, "plugins", "installed_plugins.json"), {}) or {}).get("plugins")
    for key, v in sorted((rows or {}).items()) if isinstance(rows, dict) else []:
        if isinstance(key, str) and key.startswith("windstorm-pit-stop@") and any(
                isinstance(r, dict) and r.get("scope", "user") == "user" for r in (v if isinstance(v, list) else [])):
            name = key.split("@", 1)[1]
            known = pc.read_json(os.path.join(claude_dir, "plugins", "known_marketplaces.json"), {}) or {}
            src = known.get(name, {}).get("source") if isinstance(known.get(name), dict) else None
            return name, src if isinstance(src, dict) else None
    return None, None


def self_update(apply, settings_path=None, claude_dir=None):
    """Switch on Claude Code's auto-update for Pit Stop's marketplace. Returns one line saying what it found or did."""
    settings_path = settings_path or SETTINGS
    name, src = own_marketplace(claude_dir)
    if not name:
        return "not installed from a marketplace for this user; nothing to switch on"
    cfg, err = pc.read_json_strict(settings_path) if os.path.exists(settings_path) else ({}, None)
    if err or not isinstance(cfg, dict):
        return f"NOT changed: {settings_path} does not read as a JSON object ({err or 'not an object'})"
    extra = cfg.get("extraKnownMarketplaces")
    entry = extra.get(name) if isinstance(extra, dict) else None
    if isinstance(entry, dict) and entry.get("autoUpdate") is True:
        return f"already on for marketplace {name}"
    if isinstance(entry, dict) and entry.get("autoUpdate") is False:
        return f"left OFF for marketplace {name}: settings.json says autoUpdate false (your choice; change it there)"
    if not isinstance(entry, dict) and not src:
        return f"NOT changed: cannot read where marketplace {name} comes from (known_marketplaces.json)"
    if extra is not None and not isinstance(extra, dict):
        return "NOT changed: extraKnownMarketplaces in settings.json is not an object"
    if apply:
        if os.path.exists(settings_path):
            pc.backup(settings_path)
        new = dict(entry) if isinstance(entry, dict) else {"source": src}
        new["autoUpdate"] = True
        cfg["extraKnownMarketplaces"] = dict(extra or {}, **{name: new})
        mode = os.stat(settings_path).st_mode & 0o777 if os.path.exists(settings_path) else 0o600
        pc.write_json_atomic(settings_path, cfg, mode=mode)
        return f"switched ON for marketplace {name} (Claude Code checks it at start and updates the plugin)"
    return f"would switch ON for marketplace {name} (autoUpdate: true in {settings_path.replace(pc.HOME, '~')})"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fleet", action="store_true")
    ap.add_argument("--no-auto-tune", action="store_true")
    ap.add_argument("--no-tune-now", action="store_true")
    ap.add_argument("--no-self-update", action="store_true")
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--budget", choices=op.BUDGETS)
    ap.add_argument("--plugin-updates", choices=("pit-stop", "all", "off"), default="pit-stop")
    a = ap.parse_args()
    word = "DOING" if a.apply else "PLAN (nothing is changed; add --apply)"
    sessions, source = pc.live_sessions()
    newest = pc.newest_local_version()
    behind = [s for s in sessions if newest and s.get("version") and pc.vkey(s["version"]) < pc.vkey(newest)]
    print(f"Pit Stop setup: {word}")
    print(f"  found {len(sessions)} running session(s) (from {source}); newest installed Claude Code: {newest or 'not found'}; "
          f"{len(behind)} behind it")
    try:
        import install_method as im
        mach = im.machine(os.getcwd(), write=a.apply)
        for line in im.describe(mach):
            print(f"  {line}")
    except Exception as e:
        mach = None
        print(f"  (could not read how Claude Code is installed here: {type(e).__name__})")
    if mach and not mach.get("restart_ok"):
        print("  NOTE: Claude Code's /restart works only with Anthropic's native installer on macOS or Linux (not WSL), so "
              "Pit Stop cannot restart sessions onto a new version here; holding refreshes for a break and tuning work.")

    budget = a.budget or (pc.config().get("budget") if pc.config().get("budget") in op.BUDGETS else None) or "balanced"
    print("1. Tuning: " + ("left off (--no-auto-tune)" if a.no_auto_tune else "ON (auto_tune: true), daily")
          + f"; usage budget: {budget}" + ("" if a.budget else " (change it with --budget economy|balanced|roomy)"))
    if a.apply and (a.budget or not a.no_auto_tune):
        cfg, err = pc.read_json_strict(pc.CONFIG)
        if err or not isinstance(cfg, dict):
            print(f"   NOT written: {pc.CONFIG} does not read as a JSON object ({err or 'not an object'}); fix it and run again")
        else:
            if not a.no_auto_tune:
                cfg["auto_tune"] = True
            if a.budget:
                cfg["budget"] = a.budget
            pc.write_json_atomic(pc.CONFIG, cfg)

    folders = sorted({s.get("cwd") for s in sessions if s.get("cwd")} | {os.getcwd()})
    if a.no_tune_now:
        print("2. Tune now: skipped (--no-tune-now)")
    else:
        print(f"2. Tune now: {len(folders)} folder(s)")
        for d in folders:
            if not os.path.isdir(d):
                continue
            try:
                m = op.analyze(d, a.days)
            except Exception as e:
                print(f"   {d.replace(pc.HOME, '~')}: could not read its history ({type(e).__name__}); left as it is")
                continue
            if not m["transcripts"]:
                print(f"   {d.replace(pc.HOME, '~')}: no history in {a.days} days; left as it is")
                continue
            rec = op.recommend(m, budget=budget)
            print(f"   {d.replace(pc.HOME, '~')}: idle point {pc.fmt_k(m['current_idle_point'] or pc.IDLE_DEFAULT)} -> "
                  f"{pc.fmt_k(rec['idle_point'])}, window {pc.fmt_k(m['current_window'])} -> {pc.fmt_k(rec['window'])}"
                  f"  ({m['compactions']} compactions, {m['compactions_when']['mid_task']} mid-task"
                  + (f"; {rec['usage_class']} use" if rec.get("usage_class") in ("heavy", "very heavy") else "")
                  + (f"; about {int(rec['est_read_saving'] * 100)}% fewer cache reads" if rec.get("est_read_saving", 0) >= 0.05
                     else f"; about {int(-rec['est_read_saving'] * 100)}% more cache reads" if rec.get("est_read_saving", 0) <= -0.05
                     else "")
                  + ")")
            if a.apply:
                try:
                    s, _, _ = op.apply(d, rec, m)
                    pc.prune_backups(s)
                except SystemExit as e:
                    print(f"     {e}")
        print("   New points take effect at each session's next start (a pit-stop restart counts).")

    print("3. Pit Stop's own updates: " + ("left as they are (--no-self-update)" if a.no_self_update
                                          else self_update(a.apply)))

    if not a.fleet:
        print("Fleet mode: not asked for. If you run several sessions in tmux, add --fleet to see that plan.")
        return 0
    print("Fleet mode:")
    if not shutil.which("tmux"):
        print("  tmux is not installed or not on PATH: fleet mode needs it. Nothing done.")
        return 1
    servers = tmux_servers()
    ttys = {session_tty(s.get("pid")) for s in sessions}
    best = max(servers, key=lambda n: len(servers[n] & ttys)) if servers else None
    in_tmux = len(servers.get(best, set()) & ttys) if servers else 0
    print(f"  {in_tmux} of {len(sessions)} session(s) run in tmux" + (f" (server: {best or 'default'})" if servers else ""))
    print(f"4. launcher: {BIN}")
    if a.apply:
        os.makedirs(os.path.dirname(BIN), exist_ok=True)
        shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py"), BIN)
        os.chmod(BIN, 0o755)
    rj = os.path.join(pc.STATE_DIR, "restart.json")
    if os.path.exists(rj):
        print(f"5. {rj}: exists, left exactly as it is")
    else:
        print(f"5. {rj}: new, with tmux_socket = {best!r} and plugin_updates = {a.plugin_updates!r} (add allow_names, "
              f"deny_names, deny_name_globs or deny_paths there to keep sessions out)")
        if a.apply:
            pc.write_json_atomic(rj, {"tmux_socket": best, "allow_names": [], "deny_names": [],
                                      "deny_name_globs": [], "deny_paths": [], "plugin_updates": a.plugin_updates})
    hits = already_scheduled()
    if hits:
        print("6. timer: something already schedules the restart helper; nothing changed:")
        for h in hits:
            print(f"     {h}")
        print(f"   To follow plugin updates by itself, make its command: {sys.executable} -I {BIN} --live")
    else:
        print("6. timer:")
        install_timer(max(2, min(a.interval, 30)), a.apply)
    print(f"   First look at what it would do: {sys.executable} -I {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'restart_at_break.py')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
