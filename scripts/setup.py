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
Every run also removes group and other write from what Pit Stop wrote, if an
earlier setup under an open umask (000) left it open, and names any folder on
the way that it cannot tell it made.
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
os.umask(os.umask(0) | 0o022)   # rule 28: nothing we make has group or other write (G12.12)

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


def missing_chain(path):
    """The folders that do not exist yet on the way to PATH (PATH included), deepest first."""
    out, d = [], os.path.abspath(path)
    while not os.path.lexists(d) and os.path.dirname(d) != d:
        out.append(d)
        d = os.path.dirname(d)
    return out


def note_made(dirs, ledger=None):
    """Record in the ledger each folder setup is about to make (it did not exist), so --undo removes it, with
    rmdir only, when it is empty again (rule 23: by the record only; G12.17)."""
    ledger = ledger or UNDO
    if not dirs:
        return
    d = pc.read_json(ledger, {}) if os.path.exists(ledger) else {}
    d = d if isinstance(d, dict) else {}
    made = d.get("made_dirs") if isinstance(d.get("made_dirs"), list) else []
    made += [x for x in dirs if x not in made]
    d["made_dirs"] = made
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    pc.write_json_atomic(ledger, d, 0o600)


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
    for d in glob.glob(os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", f"tmux-{os.getuid()}", "*")):   # where tmux looks
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
            note_made(missing_chain(os.path.dirname(PLIST)))
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
    note_made(missing_chain(UNIT_DIR))
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


def accounts_in_group(gid, passwd, listed, me):
    """The accounts that hold group GID, by user NUMBER (rule 28, AN ACCOUNT IS ITS NUMBER, NOT ITS NAME): every
    entry of PASSWD [(name, uid, primary gid)] whose primary group it is, and each name LISTED in the group
    (a listed name with no passwd entry stays as its name: another account we cannot place). Leaves out ME (the
    number the tool runs as, under any of its names) and 0 (the administrator)."""
    uid_of = {}
    for n, u, _ in passwd:
        uid_of.setdefault(n, u)
    out = {u for _, u, g in passwd if g == gid} | {uid_of.get(n, n) for n in listed}
    return out - {me, 0}


def group_members(gid):
    """accounts_in_group for this system, or None when the group cannot be read (no such group entry, or no
    grp/pwd here)."""
    try:
        import grp
        import pwd
        passwd = [(p.pw_name, p.pw_uid, p.pw_gid) for p in pwd.getpwall()]
        listed = grp.getgrgid(gid).gr_mem
    except (ImportError, KeyError, OSError):
        return None
    return accounts_in_group(gid, passwd, listed, os.getuid())


OTHER = "(an account in none of these groups)"


def exposure(path, mode):
    """None when no account other than the owner can reach PATH (every folder from the root down passable for
    it) and write it; else the note for its line: "" when known, or which group's members could not be read.
    The group's bits count only for an account in that folder's group, and only when the group has a member
    whose user number is not the owner's (rule 28, WHEN A PERSON'S FOLDER IS NAMED): a private group of one is not another
    account. Everyone else goes by the "other" bits."""
    chain, d = [], os.path.dirname(os.path.abspath(path))
    while True:
        try:
            chain.append((os.stat(d), 0o1))                   # a folder above: x lets an account through
        except OSError:
            return None
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    gid = None
    try:
        gid = os.lstat(path).st_gid
    except OSError:
        return None
    steps = [(st.st_gid, st.st_mode, need) for st, need in chain] + [(gid, mode, 0o2)]   # the thing itself: w
    groups, unread = {}, []
    for g, _, _ in steps:
        if g not in groups:
            groups[g] = group_members(g)
            if groups[g] is None:
                unread.append(g)
    who = {OTHER} | {n for m in groups.values() if m for n in m} | {f"(group {g})" for g in unread}
    for c in who:
        ok = True
        for g, m, need in steps:
            inside = c != OTHER and (c == f"(group {g})" or c in (groups[g] or ()))
            if not m & (need << 3 if inside else need):
                ok = False
                break
        if ok:
            return f" (the members of group {c[7:-1]} could not be read)" if isinstance(c, str) and c.startswith("(group ") else ""
    return None


def writable_by_others(path, mode):
    """True when an account other than the owner can reach PATH and write it (exposure)."""
    return exposure(path, mode) is not None


def open_scan(home=None):
    """Rule 28, A FILE IS ONLY AS CLOSED AS THE FOLDERS ABOVE IT (G12.13, G12.14). Returns a dict:
      mine:  [(path, mode)] what Pit Stop wrote that has group or other write: the state folder and, inside it,
             the names Pit Stop writes (everything under its own folders), and the launcher, unit, timer and
             plist when ours. Setup closes these whoever can reach them; symlinks and other owners' are left.
      ways:  [(path, mode, what it is or holds, note, owner number)] every other folder from the home down to each
             thing that runs or decides what runs (the settings, the launcher, the timer files, the installed code,
             installed_plugins.json, the session list, the claude, tmux and Python programs; those files too)
             that another account can reach and write. Not ours to change (rule 23): named with the one command.
      copies: [(installed code folder, how many entries in it another account can reach and write)], this copy
             first, then every other copy in the plugin cache (the launcher may run any of them).
      unread: [path] what could not be read (rule 6)."""
    home = os.path.realpath(home or pc.HOME)
    mine, ways, unread, seen = [], [], [], set()

    def add(p):
        try:
            st = os.lstat(p)
        except FileNotFoundError:                       # gone since it was listed: nothing there to be open
            return
        except OSError:
            unread.append(p)
            return
        if not os.path.islink(p) and st.st_uid == os.getuid() and st.st_mode & 0o022:
            mine.append((p, st.st_mode & 0o7777))

    if os.path.isdir(pc.STATE_DIR) and not os.path.islink(pc.STATE_DIR):
        add(pc.STATE_DIR)
        try:
            names = sorted(os.listdir(pc.STATE_DIR))
        except OSError:
            names = []
            unread.append(pc.STATE_DIR)
        for name in names:
            if not pc.state_name_is_ours(name):
                continue
            top = os.path.join(pc.STATE_DIR, name)
            add(top)
            if os.path.isdir(top) and not os.path.islink(top):
                for d, dirs, files in os.walk(top, followlinks=False, onerror=lambda e: unread.append(e.filename)):
                    for n in sorted(dirs) + sorted(files):
                        add(os.path.join(d, n))
    timer = [os.path.join(UNIT_DIR, "pit-stop-restart.service"), os.path.join(UNIT_DIR, "pit-stop-restart.timer"), PLIST]
    for f in [BIN] + timer:
        if ours(f):
            add(f)
    code = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # what runs, and what decides what runs (rule 28): (path, what it is, check the file itself too)
    runs = [(pc.STATE_DIR, "Pit Stop's settings and state, which decide what it runs", False)] \
        + ([(BIN, "Pit Stop's launcher, which runs as you", False)] if os.path.exists(BIN) else []) \
        + [(f, ("Pit Stop's launch agent" if f == PLIST else "Pit Stop's systemd timer") + ", which runs as you", False)
           for f in timer if ours(f)] \
        + [(os.path.join(code, "hooks"), "Pit Stop's installed code, which Claude Code runs as you", False),
           (os.path.join(pc.CLAUDE_DIR, "plugins", "installed_plugins.json"),
            "the plugin list that tells Pit Stop's launcher which code to run", True),
           (os.path.join(pc.SESSIONS, "-"), "Claude Code's list of sessions, which tells Pit Stop's helper where to type",
            False)]
    copies = [code] + sorted(v for v in glob.glob(os.path.join(pc.CLAUDE_DIR, "plugins", "cache", "*", "windstorm-pit-stop", "*"))
                             if os.path.isdir(v) and os.path.realpath(v) != os.path.realpath(code))
    runs += [(os.path.join(v, "scripts"), "another installed copy of Pit Stop, which its launcher may run as you", False)
             for v in copies[1:]]
    for name, p in (("claude", shutil.which("claude")), ("tmux", shutil.which("tmux")), ("Python", sys.executable)):
        if p:
            what = f"the {name} program Pit Stop runs as you"
            runs.append((p, what, True))
            if os.path.realpath(p) != os.path.abspath(p):
                runs.append((os.path.realpath(p), what, True))
    state = os.path.realpath(pc.STATE_DIR)
    for target, what, itself in runs:
        d = os.path.dirname(os.path.abspath(target))
        chain = []
        while True:
            chain.append(d)
            r = os.path.realpath(d)
            if r == home or not r.startswith(home + os.sep) or os.path.dirname(d) == d:
                break
            d = os.path.dirname(d)
        for d in list(reversed(chain)) + ([os.path.abspath(target)] if itself else []):   # from the home down
            r = os.path.realpath(d)
            if r in seen or r == state or r.startswith(state + os.sep):
                continue
            seen.add(r)
            try:
                st = os.lstat(d)
            except FileNotFoundError:                   # not there (no Pit Stop state yet): nothing to be open
                continue
            except OSError:
                unread.append(d)
                continue
            note = None if os.path.islink(d) else exposure(d, st.st_mode)
            if note is not None:
                ways.append((d, st.st_mode & 0o7777, what, note, st.st_uid))
    counts = []
    for c in copies:
        n = 0
        for d, dirs, files in os.walk(c, followlinks=False, onerror=lambda e: unread.append(e.filename)):
            dirs[:] = [x for x in dirs if x != ".git"]
            for x in dirs + files:
                p = os.path.join(d, x)
                try:
                    st = os.lstat(p)
                except FileNotFoundError:
                    continue
                except OSError:
                    unread.append(p)
                    continue
                n += not os.path.islink(p) and writable_by_others(p, st.st_mode)
        counts.append((c, n))
    return {"mine": mine, "ways": ways, "copies": counts, "unread": unread}


def owner_name(uid):
    """Who owns a folder, by number (rule 28, AN ACCOUNT IS ITS NUMBER): 0 is the administrator."""
    if uid == 0:
        return "the administrator (root, number 0)"
    try:
        import pwd
        return f"account {pwd.getpwuid(uid).pw_name} (number {uid})"
    except (ImportError, KeyError):
        return f"account number {uid}"


def open_lines(scan, first=()):
    """The numbered lines that name what Pit Stop may not change (rule 28): which folder, what it means, the one
    command. Setup prints them; /pit-stop status repeats them until they are closed."""
    short = lambda p: p.replace(pc.HOME, "~", 1) if p.startswith(pc.HOME) else p
    out = list(first)
    for d, m, what, note, owner in scan["ways"]:
        folder = os.path.isdir(d) and not os.path.islink(d)
        head = (f"{short(d)} ({oct(m)[2:]}) can be written by another account{note}, by its permission bits: "
                + (f"it could delete and replace what is in it, and below it is {what}." if folder else f"it is {what}."))
        if owner == os.getuid():
            out.append(f"{head} " + ("Pit Stop cannot tell whether it made this folder, so it stays."
                                     if folder else "It is not Pit Stop's, so it stays.") + f" To close it: chmod go-w {d}")
        else:                                   # rule 28: no command the person cannot run (G12.16)
            out.append(f"{head} It belongs to {owner_name(owner)}, not to you, so only an administrator can close it.")
    for i, (code, n) in enumerate(scan["copies"]):
        if n:
            which = "Pit Stop's installed code" if i == 0 else "another installed copy of Pit Stop"
            out.append(f"{n} file(s) or folder(s) in {which} "
                       f"({short(code)}) can be written by another account, and it runs as you. To close them: chmod -R go-w {code}")
    out += [f"could not be read, so not checked: {short(p)}" for p in scan["unread"]]
    return [f"{i}. {x}" for i, x in enumerate(out, 1)]


def close_open(apply, home=None):
    """Rule 28 for what an earlier run left open (G12.13): a setup run under a wide-open umask (000, as some job
    queues start) left Pit Stop's state folder 0777 and its timer files 0666. Removes group and other write from
    what Pit Stop wrote (open_scan's "mine"); only narrows, never widens; --undo does not open them again. The
    folders on the way that are not Pit Stop's are named (open_lines), not changed. Returns the lines to print."""
    scan = open_scan(home)
    mine = scan["mine"]
    short = lambda p: p.replace(pc.HOME, "~", 1) if p.startswith(pc.HOME) else p
    if not mine:
        out = ["   none of what Pit Stop wrote has group or other write in its permission bits (access lists are not read)"]
    elif apply:
        out = []
        for p, m in mine:
            try:
                os.chmod(p, m & ~0o022)
                out.append(f"   closed {short(p)}: {oct(m)[2:]} -> {oct(m & ~0o022)[2:]}")
            except OSError as e:
                out.append(f"   NOT closed {short(p)} ({oct(m)[2:]}): {e}")
    else:
        out = [f"   would close {short(p)}: {oct(m)[2:]} -> {oct(m & ~0o022)[2:]}" for p, m in mine]
    return out + ["   " + x for x in open_lines(scan)]


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
    cf = rec.get("config_file") if isinstance(rec.get("config_file"), dict) else {}
    made_cfg = cf.get("existed") is False and os.path.exists(pc.CONFIG)
    rest = None
    if made_cfg and not theirs:
        rest, err = pc.read_json_strict(pc.CONFIG)
        if not err and isinstance(rest, dict):
            put_cfg(rest)
    if made_cfg and (sha(pc.CONFIG) == cf.get("set") or rest == {}):
        os.remove(pc.CONFIG)                               # setup made it and nothing of yours is in it (G12.17)
        out.append(f"3. config.json: removed {home(pc.CONFIG)} (setup made it; nothing of yours was in it)")
    elif cfg_changes and os.path.exists(pc.CONFIG):
        why = _put_back(pc.CONFIG, put_cfg)
        out.append("3. config.json: " + (why or ", ".join(
            [f"{k} {'back to ' + json_word(v.get('value')) if v.get('had') else 'removed (it was not set)'}"
             for k, v in cfg_changes.items()] + said)) + ("; it stays: it holds your settings" if made_cfg else ""))
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
    # 6. what Pit Stop made that holds nothing of yours goes; history stays, by name (G12.17)
    if os.path.exists(ledger):
        os.remove(ledger)
    gone = []
    for x in ("released.json", "machine-behind.json"):          # Pit Stop's own reading of the newest release
        p = os.path.join(pc.STATE_DIR, x)
        if os.path.isfile(p) and not os.path.islink(p):
            os.remove(p)
            gone.append(x)
    if cf.get("existed") is False:                               # a backup of the config.json setup made: only
        for b in sorted(glob.glob(glob.escape(pc.CONFIG) + ".bak-pitstop-*")):   # Pit Stop's keys in it
            data = pc.read_json(b, None)
            if (os.path.isfile(b) and not os.path.islink(b) and isinstance(data, dict)
                    and set(data) <= {"auto_tune", "budget"} and pc.state_name_is_ours(os.path.basename(b))):
                os.remove(b)
                gone.append(os.path.basename(b))
    if gone:
        out.append(f"6. removed what Pit Stop made that held nothing of yours: {', '.join(gone)}")
    names = sorted(x for x in os.listdir(pc.STATE_DIR) if x not in ("config.json", "restart.json")) if os.path.isdir(
        pc.STATE_DIR) else []
    left = [x for x in names if pc.state_name_is_ours(x)]
    others = len(names) - len(left)
    out.append(f"{'7' if gone else '6'}. left in place (history, not settings): "
               + (", ".join(home(os.path.join(pc.STATE_DIR, x)) for x in left) if left else "nothing"))
    if others:   # G12.10: not ours, so never named as Pit Stop's and never offered for deletion
        out.append(f"   Also in {home(pc.STATE_DIR)}: {others} other file(s) or folder(s) this version of Pit Stop "
                   f"did not write; they stay.")
    # the folders this setup made, by the record only, with rmdir only: never a recursive remove (rule 23)
    n = 8 if gone else 7
    for d in sorted((x for x in (rec.get("made_dirs") or []) if isinstance(x, str)), key=len, reverse=True):
        if not os.path.isdir(d) or os.path.islink(d):
            continue
        try:
            os.rmdir(d)
            out.append(f"{n}. removed the folder {home(d)} (setup made it; empty now)")
        except OSError:
            try:
                inside = sorted(os.listdir(d))
            except OSError:
                inside = []
            out.append(f"{n}. kept the folder {home(d)} (setup made it): it holds {len(inside)} item(s): "
                       f"{', '.join(inside[:8])}{', ...' if len(inside) > 8 else ''}")
        n += 1
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
    if a.apply:   # before anything is written: what this setup will make that was not here (G12.17)
        made = missing_chain(pc.STATE_DIR)
        cfg_was = os.path.lexists(pc.CONFIG)
        note_made(made)
        note(UNDO, "config_file", {"existed": cfg_was})
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
            wrote(UNDO, "config_file", sha(pc.CONFIG))

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

    print("Write access for other accounts (an earlier setup under an open umask):")
    for line in close_open(a.apply):
        print(line)

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
        note_made(missing_chain(os.path.dirname(BIN)))
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
