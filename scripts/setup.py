#!/usr/bin/env python3
"""Pit Stop setup: one command to switch everything on.

  setup.py                    show the plan; change nothing
  setup.py --apply            do it
  setup.py --fleet [--apply]  also set up fleet mode (tmux): the launcher and
                              a timer that runs the pit stop every few minutes
  setup.py --undo             put back what --apply and --fleet changed (below)
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
Nothing here needs sudo. --apply keeps what it found before each change in
~/.claude/pit-stop/setup-undo.json.

What --undo does (each step is listed as it goes):
  1. Stops and removes the timer it installed, and the launcher.
  2. Removes the restart.json it wrote, if nobody has changed it since.
  3. Puts "auto_tune" (and "budget") in config.json back as they were.
  4. Puts the marketplace entry in ~/.claude/settings.json back as it was
     (backed up first). After a setup from before 0.6.2, which kept no record,
     the entry is read from the newest settings.json backup that has it off.
  5. Puts back the two compaction points of each folder setup tuned
     (backed up first; daily tuning's later moves are named, not undone).
A value you changed after setup is yours: it is left as it is, and named.
It leaves the history files (logs, tune history, checkup pages, backups) in
place and names them; delete ~/.claude/pit-stop to remove them too.
"""
import hashlib
import argparse
import glob
import os
import platform
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True   # rule 23: no __pycache__ beside the plugin's code (G12.9)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402
import optimize as op  # noqa: E402

BIN = os.path.join(pc.STATE_DIR, "bin", "pit-stop-restart")
UNIT_DIR = os.path.join(pc.HOME, ".config", "systemd", "user")
PLIST = os.path.join(pc.HOME, "Library", "LaunchAgents", "ai.thewindstorm.pit-stop-restart.plist")
UNDO = os.path.join(pc.STATE_DIR, "setup-undo.json")

# What marks a timer file or the launcher as Pit Stop's when the ledger has no record of it (a setup before 0.6.2).
MARKS = ("Description=Windstorm Pit Stop", "<string>ai.thewindstorm.pit-stop-restart</string>",
         '"""Pit Stop launcher: one fixed path for timers')


def ours(path, ledger=None):
    """True when PATH is a file setup wrote: its sha256 is the one the ledger recorded for it, or (no record)
    it carries Pit Stop's own mark. A file of the same name with other content is not ours (rule 23, G12.11)."""
    if not os.path.isfile(path):
        return False
    rec = (pc.read_json(ledger or UNDO, {}) or {}).get("files")
    want = (rec.get(path) or {}).get("set") if isinstance(rec, dict) and isinstance(rec.get(path), dict) else None
    if want:
        return sha(path) == want
    try:
        with open(path, errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return False
    return any(m in head for m in MARKS)


def keep_record(path):
    """Record in the ledger the sha256 of a file setup just wrote, so --undo removes it only while unchanged."""
    wrote(UNDO, "files", sha(path), sub=path)


def note(ledger, key, value, sub=None):
    """Keep in LEDGER what was there before setup's first change of KEY (or of KEY's SUB): a later
    --apply never overwrites it, so --undo goes back to before the first setup. No LEDGER: no record."""
    if not ledger:
        return
    d = pc.read_json(ledger, {})
    d = d if isinstance(d, dict) else {}
    if sub is None:
        d.setdefault(key, value)
    else:
        box = d.get(key) if isinstance(d.get(key), dict) else {}
        box.setdefault(sub, value)
        d[key] = box
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    pc.write_json_atomic(ledger, d, 0o600)


def wrote(ledger, key, value, sub=None):
    """Keep in LEDGER what setup's newest change of KEY (or KEY's SUB) wrote: --undo puts a value back only
    while it is still this one, so a value the person changed since is theirs and stays."""
    if not ledger:
        return
    d = pc.read_json(ledger, {})
    d = d if isinstance(d, dict) else {}
    box = d.get(key) if isinstance(d.get(key), dict) else {}
    if sub is None:
        box["set"] = value
    else:
        inner = box.get(sub) if isinstance(box.get(sub), dict) else {}
        inner["set"] = value
        box[sub] = inner
    d[key] = box
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    pc.write_json_atomic(ledger, d, 0o600)


def sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


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
        if os.path.exists(PLIST) and not ours(PLIST):
            print(f"    {PLIST} exists and is not Pit Stop's: left as it is, no timer written")
            return
        if apply:
            os.makedirs(os.path.dirname(PLIST), exist_ok=True)
            with open(PLIST, "w") as f:
                f.write(body)
            keep_record(PLIST)
            r = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", PLIST])
            print("    loaded" if r and r.returncode == 0 else f"    written; load it with: launchctl bootstrap gui/{os.getuid()} {PLIST}")
        return
    svc, tmr = os.path.join(UNIT_DIR, "pit-stop-restart.service"), os.path.join(UNIT_DIR, "pit-stop-restart.timer")
    print(f"  timer: {tmr} (systemd user timer, every {interval} min)")
    theirs = [f for f in (svc, tmr) if os.path.exists(f) and not ours(f)]
    if theirs:
        print(f"    {', '.join(theirs)} exist(s) and are not Pit Stop's: left as they are, no timer written")
        return
    if not apply:
        return
    os.makedirs(UNIT_DIR, exist_ok=True)
    with open(svc, "w") as f:
        f.write(f"[Unit]\nDescription=Windstorm Pit Stop: restart at the break (fleet mode)\n\n[Service]\nType=oneshot\n"
                f"Environment=PATH={timer_path()}\nExecStart={py} -I {BIN} --live\nTimeoutStartSec=600\nNice=10\n")
    with open(tmr, "w") as f:
        f.write(f"[Unit]\nDescription=Windstorm Pit Stop every {interval} minutes\n\n[Timer]\nOnBootSec=3min\n"
                f"OnUnitActiveSec={interval}min\nAccuracySec=20s\n\n[Install]\nWantedBy=timers.target\n")
    keep_record(svc), keep_record(tmr)
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


def self_update(apply, settings_path=None, claude_dir=None, ledger=None):
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
        note(ledger, "marketplace", {"settings": settings_path, "name": name,
                                     "entry": entry if isinstance(entry, dict) else None})
        if os.path.exists(settings_path):
            pc.backup(settings_path)
        new = dict(entry) if isinstance(entry, dict) else {"source": src}
        new["autoUpdate"] = True
        cfg["extraKnownMarketplaces"] = dict(extra or {}, **{name: new})
        mode = os.stat(settings_path).st_mode & 0o777 if os.path.exists(settings_path) else 0o600
        pc.write_json_atomic(settings_path, cfg, mode=mode)
        return f"switched ON for marketplace {name} (Claude Code checks it at start and updates the plugin)"
    return f"would switch ON for marketplace {name} (autoUpdate: true in {settings_path.replace(pc.HOME, '~')})"


def folder_points(folder):
    """The two compaction points as FOLDER's settings.local.json has them now: {env name: value or None}."""
    d = pc.read_json(os.path.join(folder, ".claude", "settings.local.json"), {})
    env = d.get("env") if isinstance(d, dict) and isinstance(d.get("env"), dict) else {}
    return {k: env.get(k) for k in (pc.WINDOW_ENV, pc.IDLE_ENV)}


def _put_back(path, change):
    """Read PATH as a JSON object, back it up, apply CHANGE(data) and write it with its mode kept.
    Returns None, or why it was not changed. A file that does not read is never rewritten."""
    data, err = pc.read_json_strict(path)
    if err or not isinstance(data, dict):
        return f"NOT changed: {path} does not read as a JSON object ({err or 'not an object'})"
    pc.backup(path)
    change(data)
    pc.write_json_atomic(path, data, os.stat(path).st_mode & 0o777)
    return None


def undo(ledger=None, run_cmd=None):
    """Put back what --apply and --fleet changed. Returns the lines to print, one per step."""
    ledger = ledger or UNDO
    run_cmd = run_cmd or run
    rec = pc.read_json(ledger, {}) if os.path.exists(ledger) else {}
    rec = rec if isinstance(rec, dict) else {}
    out = ["Pit Stop setup --undo" + ("" if rec else " (no record of the first setup here: setup before 0.6.2 kept none; "
                                      "each step says what it could tell)")]
    home = lambda p: p.replace(pc.HOME, "~")            # noqa: E731

    # 1. timer and launcher: only files setup wrote (its record, or Pit Stop's mark); others are named and left (G12.11)
    svc, tmr = os.path.join(UNIT_DIR, "pit-stop-restart.service"), os.path.join(UNIT_DIR, "pit-stop-restart.timer")
    units = [f for f in (svc, tmr) if os.path.exists(f)]
    if units and all(ours(f, ledger) for f in units):
        run_cmd(["systemctl", "--user", "disable", "--now", "pit-stop-restart.timer"])
        for f in units:
            os.remove(f)
        run_cmd(["systemctl", "--user", "daemon-reload"])
        out.append(f"1. timer: stopped and removed {home(tmr)} and its .service")
    elif units:
        out.append(f"1. timer: {', '.join(home(f) for f in units)} not written by Pit Stop's setup: left as they are. "
                   f"To switch it off: systemctl --user disable --now pit-stop-restart.timer")
    elif os.path.exists(PLIST) and ours(PLIST, ledger):
        run_cmd(["launchctl", "bootout", f"gui/{os.getuid()}", PLIST])
        os.remove(PLIST)
        out.append(f"1. timer: stopped and removed {home(PLIST)}")
    elif os.path.exists(PLIST):
        out.append(f"1. timer: {home(PLIST)} not written by Pit Stop's setup: left as it is. "
                   f"To switch it off: launchctl bootout gui/{os.getuid()} {PLIST}")
    else:
        out.append("1. timer: none of setup's here")
    others = [h for h in already_scheduled() if h not in (svc, tmr, PLIST) or not ours(h, ledger)]
    if os.path.exists(BIN) and not ours(BIN, ledger):
        out.append(f"   launcher {home(BIN)} not written by Pit Stop's setup: left as it is")
    elif os.path.exists(BIN) and others:
        out.append(f"   launcher {home(BIN)} kept: something else still runs it: {', '.join(home(h) for h in others)}")
    elif os.path.exists(BIN):
        os.remove(BIN)
        try:
            os.rmdir(os.path.dirname(BIN))                 # only when nothing else is in it
        except OSError:
            pass
        out.append(f"   launcher: removed {home(BIN)}")
    # 2. restart.json
    rj = rec.get("restart_json") if isinstance(rec.get("restart_json"), dict) else None
    if rj and os.path.exists(rj.get("path") or "") and sha(rj["path"]) == rj.get("sha256"):
        os.remove(rj["path"])
        out.append(f"2. restart.json: removed {home(rj['path'])} (setup wrote it; unchanged since)")
    elif rj and os.path.exists(rj.get("path") or ""):
        out.append(f"2. restart.json: kept {home(rj['path'])}: changed since setup wrote it (your settings)")
    else:
        rjp = os.path.join(pc.STATE_DIR, "restart.json")
        out.append(f"2. restart.json: kept {home(rjp)}: setup did not write it" if os.path.exists(rjp)
                   else "2. restart.json: none")
    # 3. config.json
    cfg_changes = {k: rec[k] for k in ("auto_tune", "budget") if isinstance(rec.get(k), dict)}
    if not rec and pc.config().get("auto_tune") is True:
        cfg_changes = {"auto_tune": {"had": False, "set": True}}   # no record: tuning goes back to off, its default
    now_cfg = pc.config()
    theirs = [k for k, v in cfg_changes.items() if "set" in v and now_cfg.get(k, MISSING) != v["set"]]
    cfg_changes = {k: v for k, v in cfg_changes.items() if k not in theirs}

    def put_cfg(d):
        for k, v in cfg_changes.items():
            if v.get("had"):
                d[k] = v.get("value")
            else:
                d.pop(k, None)
    said = [f"{k} left as you set it since ({json_word(now_cfg.get(k, MISSING))})" for k in theirs]
    if cfg_changes and os.path.exists(pc.CONFIG):
        why = _put_back(pc.CONFIG, put_cfg)
        out.append("3. config.json: " + (why or ", ".join(
            [f"{k} {'back to ' + json_word(v.get('value')) if v.get('had') else 'removed (it was not set)'}"
             for k, v in cfg_changes.items()] + said)))
    else:
        out.append("3. config.json: " + (", ".join(said) if said else "nothing setup changed"))
    # 4. the marketplace entry
    mk = rec.get("marketplace") if isinstance(rec.get("marketplace"), dict) else None
    settings_path = (mk or {}).get("settings") or SETTINGS
    name = (mk or {}).get("name") or own_marketplace()[0]
    cur, _ = pc.read_json_strict(settings_path) if os.path.exists(settings_path) else (None, None)
    extra = cur.get("extraKnownMarketplaces") if isinstance(cur, dict) else None
    now_entry = extra.get(name) if isinstance(extra, dict) and name else None
    prior, found = None, bool(mk)
    if mk:
        prior = mk.get("entry")
    elif isinstance(now_entry, dict) and now_entry.get("autoUpdate") is True:
        for b in sorted(glob.glob(glob.escape(settings_path) + ".bak-pitstop-*"), reverse=True):
            old = pc.read_json(b, {})
            ent = (old.get("extraKnownMarketplaces") or {}).get(name) if isinstance(old, dict) and isinstance(
                old.get("extraKnownMarketplaces"), dict) else None
            if not (isinstance(ent, dict) and ent.get("autoUpdate") is True):
                prior, found = (ent if isinstance(ent, dict) else None), True
                break
    if not name or not isinstance(now_entry, dict):
        out.append("4. marketplace entry: nothing setup changed")
    elif now_entry.get("autoUpdate") is not True:
        out.append(f"4. marketplace entry {name}: left as you set it since (autoUpdate "
                   f"{json_word(now_entry.get('autoUpdate', MISSING))}); setup wrote true")
    elif not found:
        out.append(f"4. marketplace entry {name}: left ON: no record and no backup shows it before setup; "
                   f"to switch it off, set \"autoUpdate\": false on it in {home(settings_path)}")
    else:
        def put_entry(d):
            ex = d.setdefault("extraKnownMarketplaces", {})
            e = ex.get(name) if isinstance(ex.get(name), dict) else {}
            if prior is None and set(e) <= {"source", "autoUpdate"}:
                ex.pop(name, None)                         # setup made the whole entry: it goes
                if not ex:
                    d.pop("extraKnownMarketplaces", None)
            elif prior is not None and "autoUpdate" in prior:
                e["autoUpdate"] = prior["autoUpdate"]
            else:
                e.pop("autoUpdate", None)
        why = _put_back(settings_path, put_entry)
        out.append(f"4. marketplace entry {name}: " + (why or f"put back as it was before setup in {home(settings_path)} "
                                                       f"(backed up first); Claude Code no longer updates Pit Stop by itself"))
    # 5. the folders setup tuned
    folders = rec.get("folders") if isinstance(rec.get("folders"), dict) else {}
    if not folders:
        out.append("5. folders: " + ("no record of which folders setup tuned; their two points stay"
                                     if not rec else "setup tuned none"))
    for f, box in sorted(folders.items()):
        sp = os.path.join(f, ".claude", "settings.local.json")
        box = box if isinstance(box, dict) else {}
        pts = box.get("before") if isinstance(box.get("before"), dict) else {}
        # Pit Stop's own last write: daily tuning's profile when there is one, else what setup wrote
        prof = pc.read_json(pc.folder_profile_path(f), {})
        mine = ({pc.WINDOW_ENV: str(prof.get("window")), pc.IDLE_ENV: str(prof.get("idle_point"))}
                if isinstance(prof, dict) and prof.get("window") else box.get("set"))
        now_pts = folder_points(f)
        if isinstance(mine, dict) and any(str(now_pts.get(k)) != str(mine.get(k)) for k in (pc.WINDOW_ENV, pc.IDLE_ENV)):
            out.append(f"5. {home(f)}: window and idle point left as you set them since "
                       f"({now_pts.get(pc.WINDOW_ENV)}, {now_pts.get(pc.IDLE_ENV)})")
            continue

        def put_pts(d, pts=pts):
            env = d.setdefault("env", {}) if isinstance(d.get("env", {}), dict) else None
            if env is None:
                return
            for k in (pc.WINDOW_ENV, pc.IDLE_ENV):
                if pts.get(k) is None:
                    env.pop(k, None)
                else:
                    env[k] = pts[k]
            if not env:
                d.pop("env", None)
        why = _put_back(sp, put_pts) if os.path.exists(sp) else "its settings file is gone"
        out.append(f"5. {home(f)}: " + (why or "window and idle point put back as before setup (backed up first)"))
    tuned = sorted(x for x in os.listdir(pc.FOLDERS) if x.endswith(".json")) if os.path.isdir(pc.FOLDERS) else []
    if tuned:
        out.append(f"   daily tuning's own moves stay where they are; its record of each folder: {home(pc.FOLDERS)}")
    # what stays, by name
    if os.path.exists(ledger):
        os.remove(ledger)
    names = sorted(x for x in os.listdir(pc.STATE_DIR) if x not in ("config.json", "restart.json")) if os.path.isdir(
        pc.STATE_DIR) else []
    left = [x for x in names if pc.state_name_is_ours(x)]
    others = len(names) - len(left)
    out.append("Left in place (history, not settings): " + (", ".join(home(os.path.join(pc.STATE_DIR, x)) for x in left)
                                                          if left else "nothing"))
    if others:   # G12.10: not ours, so never named as Pit Stop's and never offered for deletion
        out.append(f"   Also in {home(pc.STATE_DIR)}: {others} other file(s) or folder(s) this version of Pit Stop "
                   f"did not write; they stay.")
    # rule 8: a foreign file does not stop a leave; the last line says it is done and where what stayed is named
    out.append(f"Done: what Pit Stop's setup changed is put back; anything that stayed is named above. "
               f"To remove Pit Stop itself: claude plugin uninstall windstorm-pit-stop@{name or 'thewindstorm'}")
    return out


MISSING = "<not set>"


def json_word(v):
    return "true" if v is True else "false" if v is False else str(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fleet", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--no-auto-tune", action="store_true")
    ap.add_argument("--no-tune-now", action="store_true")
    ap.add_argument("--no-self-update", action="store_true")
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--budget", choices=op.BUDGETS)
    ap.add_argument("--plugin-updates", choices=("pit-stop", "all", "off"), default="pit-stop")
    a = ap.parse_args()
    if a.undo:
        for line in undo():
            print(line)
        return 0
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
                note(UNDO, "auto_tune", {"had": "auto_tune" in cfg, "value": cfg.get("auto_tune")})
                wrote(UNDO, "auto_tune", True)
                cfg["auto_tune"] = True
            if a.budget:
                note(UNDO, "budget", {"had": "budget" in cfg, "value": cfg.get("budget")})
                wrote(UNDO, "budget", a.budget)
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
                    note(UNDO, "folders", {"before": folder_points(d)}, sub=os.path.abspath(d))
                    s, _, _ = op.apply(d, rec, m)
                    wrote(UNDO, "folders", folder_points(d), sub=os.path.abspath(d))
                    pc.prune_backups(s)
                except SystemExit as e:
                    print(f"     {e}")
        print("   New points take effect at each session's next start (a pit-stop restart counts).")

    print("3. Pit Stop's own updates: " + ("left as they are (--no-self-update)" if a.no_self_update
                                          else self_update(a.apply, ledger=UNDO if a.apply else None)))

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
    if os.path.exists(BIN) and not ours(BIN):
        print(f"   {BIN} exists and is not Pit Stop's: left as it is")
    elif a.apply:
        os.makedirs(os.path.dirname(BIN), exist_ok=True)
        shutil.copyfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "launcher.py"), BIN)
        os.chmod(BIN, 0o755)
        keep_record(BIN)
    rj = os.path.join(pc.STATE_DIR, "restart.json")
    if os.path.exists(rj):
        print(f"5. {rj}: exists, left exactly as it is")
    else:
        print(f"5. {rj}: new, with tmux_socket = {best!r} and plugin_updates = {a.plugin_updates!r} (add allow_names, "
              f"deny_names, deny_name_globs or deny_paths there to keep sessions out)")
        if a.apply:
            pc.write_json_atomic(rj, {"tmux_socket": best, "allow_names": [], "deny_names": [],
                                      "deny_name_globs": [], "deny_paths": [], "plugin_updates": a.plugin_updates})
            note(UNDO, "restart_json", {"path": rj, "sha256": sha(rj)})
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
