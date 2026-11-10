#!/usr/bin/env python3
"""Pit Stop setup: one command to switch everything on.

  setup.py                    show the plan; change nothing
  setup.py --apply            do it
  setup.py --fleet [--apply]  also set up fleet mode (tmux): the launcher and
                              a timer that runs the pit stop every few minutes
Options:
  --no-auto-tune              leave weekly tuning off
  --no-tune-now               do not tune the folders of the running sessions now
  --interval MIN              minutes between fleet runs (default 5)
  --days N                    days of history to read when tuning (default 14)

What --apply does:
  1. Weekly tuning ON ("auto_tune": true in ~/.claude/pit-stop/config.json):
     each folder's two compaction points are re-fitted to its own history once
     a week, one step at a time, in the background.
  2. Tunes the folder of every running session now (and the current folder).
     Settings files are backed up first; only Pit Stop's two keys change.
With --fleet it also:
  3. Copies the launcher to ~/.claude/pit-stop/bin/pit-stop-restart, so the
     timer keeps working across plugin updates.
  4. Writes ~/.claude/pit-stop/restart.json ONLY if there is none (it finds
     which tmux server holds your sessions). An existing file is never changed.
  5. Installs a timer (systemd user timer on Linux, launchd on macOS) that
     runs the launcher with --live. If something already schedules
     restart_at_break.py, it changes nothing and prints the one line to edit.
Nothing here needs sudo. Undo: delete the timer files it names, and set
"auto_tune": false.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fleet", action="store_true")
    ap.add_argument("--no-auto-tune", action="store_true")
    ap.add_argument("--no-tune-now", action="store_true")
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    word = "DOING" if a.apply else "PLAN (nothing is changed; add --apply)"
    sessions, source = pc.live_sessions()
    newest = pc.newest_installed_version()
    behind = [s for s in sessions if newest and s.get("version") and pc.vkey(s["version"]) < pc.vkey(newest)]
    print(f"Pit Stop setup: {word}")
    print(f"  found {len(sessions)} running session(s) (from {source}); newest installed Claude Code: {newest or 'not found'}; "
          f"{len(behind)} behind it")
    if not newest:
        print("  NOTE: no native install found in ~/.local/share/claude/versions. /restart needs the native macOS or "
              "Linux install; the compaction parts still work.")
    if is_wsl() or platform.system() == "Windows":
        print("  NOTE: Claude Code has no /restart on Windows or WSL. Compaction tuning works; fleet mode does not.")

    print("1. Weekly tuning: " + ("left off (--no-auto-tune)" if a.no_auto_tune else "ON (auto_tune: true)"))
    if a.apply and not a.no_auto_tune:
        cfg, err = pc.read_json_strict(pc.CONFIG)
        if err or not isinstance(cfg, dict):
            print(f"   NOT switched on: {pc.CONFIG} does not read as a JSON object ({err or 'not an object'}); fix it and run again")
        else:
            cfg["auto_tune"] = True
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
            rec = op.recommend(m)
            print(f"   {d.replace(pc.HOME, '~')}: idle point {pc.fmt_k(m['current_idle_point'] or pc.IDLE_DEFAULT)} -> "
                  f"{pc.fmt_k(rec['idle_point'])}, window {pc.fmt_k(m['current_window'])} -> {pc.fmt_k(rec['window'])}"
                  f"  ({m['compactions']} compactions, {m['compactions_when']['mid_task']} mid-task)")
            if a.apply:
                try:
                    s, _, _ = op.apply(d, rec, m)
                    pc.prune_backups(s)
                except SystemExit as e:
                    print(f"     {e}")
        print("   New points take effect at each session's next start (a pit-stop restart counts).")

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
    print(f"3. launcher: {BIN}")
    if a.apply:
        os.makedirs(os.path.dirname(BIN), exist_ok=True)
        shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py"), BIN)
        os.chmod(BIN, 0o755)
    rj = os.path.join(pc.STATE_DIR, "restart.json")
    if os.path.exists(rj):
        print(f"4. {rj}: exists, left exactly as it is")
    else:
        print(f"4. {rj}: new, with tmux_socket = {best!r} (add allow_names, deny_names, deny_name_globs or deny_paths "
              f"there to keep sessions out)")
        if a.apply:
            pc.write_json_atomic(rj, {"tmux_socket": best, "allow_names": [], "deny_names": [],
                                      "deny_name_globs": [], "deny_paths": []})
    hits = already_scheduled()
    if hits:
        print("5. timer: something already schedules the restart helper; nothing changed:")
        for h in hits:
            print(f"     {h}")
        print(f"   To follow plugin updates by itself, make its command: {sys.executable} -I {BIN} --live")
    else:
        print("5. timer:")
        install_timer(max(2, min(a.interval, 30)), a.apply)
    print(f"   First look at what it would do: {sys.executable} -I {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'restart_at_break.py')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
