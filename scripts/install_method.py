#!/usr/bin/env python3
"""Pit Stop: how Claude Code is installed on this machine, and whether it is current.

  install_method.py           print it (reads local files; asks Anthropic's
                              download server for the newest version number
                              unless that is switched off, see below)
  install_method.py --json    the same as JSON

Claude Code can be installed many ways: Anthropic's native installer, npm,
Homebrew, WinGet, apt/dnf/apk, or a version manager (mise, asdf, nix). Each
keeps it up to date differently, and some hold it at one version until a
person moves it. Pit Stop used to read only the native installer's folder,
so on any other install it could not tell when a machine fell behind
(2026-11-15: a mise pin held one machine at 2.1.322 for five days, and
nothing said so).

So this does not keep a list of install folders as the truth. It follows the
`claude` a new session would run (PATH, then links), reads its version from
there, and names the method only to say who updates it and the one command
that moves it. A method it does not know is reported as "unknown", never as
"not found".

The newest version: the same address Anthropic's install script reads
(https://downloads.claude.ai/claude-code-releases/<channel>), at most every
six hours, with a generic client name. Nothing about the machine or its
sessions is sent. It is not asked when CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
or DISABLE_UPDATES is set, or "release_check": false is in
~/.claude/pit-stop/config.json. Standard library only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402

VER = re.compile(r"\d+\.\d+\.\d+")
RELEASES = "https://downloads.claude.ai/claude-code-releases/"
RELEASED_FILE = os.path.join(pc.STATE_DIR, "released.json")
RELEASE_EVERY = 6 * 3600

# Who keeps each method current, and the one command that moves it. "self": Claude Code updates itself
# (unless its updater is off). "manager": a package manager, run by a person or by Pit Stop. "pin": a
# version manager that holds the version written in its config until that changes.
METHODS = {
    "native":   ("self",    "claude update"),
    "npm":      ("self",    "claude update"),
    "homebrew": ("manager", "brew upgrade --cask {cask}"),
    "winget":   ("manager", "winget upgrade Anthropic.ClaudeCode"),
    "apt":      ("manager", "sudo apt update && sudo apt upgrade claude-code"),
    "dnf":      ("manager", "sudo dnf upgrade claude-code"),
    "apk":      ("manager", "apk update && apk upgrade claude-code"),
    "mise":     ("pin",     "mise use -g claude@latest"),
    "asdf":     ("pin",     "asdf install claude latest, then set claude to that version in ~/.tool-versions"),
    "nix":      ("pin",     "update the nix profile or flake that provides claude-code"),
    "unknown":  ("unknown", ""),
}


def _norm(p):
    return (p or "").replace("\\", "/")


def classify(real, link=None, exists=os.path.exists):
    """The install method for a claude program at REAL (its path with links
    followed; LINK the path found on PATH). Pure but for EXISTS (for system
    packages, which file the package manager keeps)."""
    r, l = _norm(real), _norm(link)
    both = r + "\n" + l
    if "/.local/share/claude/versions/" in r:
        return "native"
    if "/mise/installs/claude" in both or "/mise/shims/claude" in l or os.path.basename(r) == "mise":
        return "mise"     # a mise shim resolves to the mise program itself (seen on two Linux machines, 2026-11-16)
    if re.search(r"/\.?asdf/(installs/claude|shims/claude)", both):
        return "asdf"
    if "/nix/store/" in both:
        return "nix"
    if "/node_modules/@anthropic-ai/claude-code" in r:
        return "npm"
    if "/Caskroom/claude-code" in r or "/Cellar/claude-code" in r or "/linuxbrew/" in both:
        return "homebrew"
    if "/WinGet/" in both or "/winget/" in both.lower():
        return "winget"
    if re.match(r"^/(usr|opt)/", r) and not re.match(r"^/(opt/homebrew|usr/local/Homebrew)/", r):
        for name, marker in (("apt", "/var/lib/dpkg/info/claude-code.list"),
                             ("apk", "/lib/apk/db/installed"),
                             ("dnf", "/var/lib/rpm")):
            if exists(marker):
                return name
    if re.search(r"/(\.local/bin|AppData/Local/Microsoft/WindowsApps)/claude(\.exe)?$", l) and \
            "/.local/share/claude/" in r:
        return "native"
    return "unknown"


def version_from_path(real, method):
    """The version, when the folder layout says it (no program run). Or None."""
    r = _norm(real)
    if method == "native":
        m = re.search(r"/versions/(\d+\.\d+\.\d+)(?:/|$)", r)
        return m.group(1) if m else None
    if method in ("mise", "asdf"):
        m = re.search(r"/installs/claude(?:-code)?/(\d+\.\d+\.\d+)/", r)
        return m.group(1) if m else None
    if method == "homebrew":
        m = re.search(r"/(?:Caskroom|Cellar)/claude-code(?:@\w+)?/(\d+\.\d+\.\d+)(?:[/,_]|$)", r)
        return m.group(1) if m else None
    if method == "npm":
        i = r.find("/node_modules/@anthropic-ai/claude-code")
        if i >= 0:
            pkg = pc.read_json(r[:i] + "/node_modules/@anthropic-ai/claude-code/package.json", {})
            v = pkg.get("version") if isinstance(pkg, dict) else None
            return v if isinstance(v, str) and VER.fullmatch(v) else None
    return None


def version_by_running(path, run=None):
    """`claude --version`, read as x.y.z; None when it fails or says something else."""
    run = run or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL))
    try:
        r = run([path, "--version"])
        m = VER.search(r.stdout or "") if r.returncode == 0 else None
        return m.group(0) if m else None
    except Exception:
        return None


def find_claude(path_env=None):
    """(path on PATH, real path) of the claude a new session would start, or (None, None)."""
    p = shutil.which("claude", path=path_env)
    if not p:
        for c in (os.path.join(pc.HOME, ".local", "bin", "claude"),):   # timers often run with a short PATH
            if os.path.exists(c):
                p = c
                break
    return (p, os.path.realpath(p)) if p else (None, None)


def tool_pin(method, folder=None, home=None):
    """For a version manager: (file, value) of the line that picks claude's
    version, nearest to FOLDER first, then the user's global file. (None, None)
    when no file names it."""
    home = home or pc.HOME
    if method == "mise":
        names = ("mise.toml", ".mise.toml", "mise.local.toml", ".tool-versions")
        glob_files = [os.path.join(home, ".config", "mise", "config.toml"), os.path.join(home, ".tool-versions")]
    elif method == "asdf":
        names = (".tool-versions",)
        glob_files = [os.path.join(home, ".tool-versions")]
    else:
        return None, None
    files = []
    d = os.path.abspath(folder) if folder else None
    while d:
        files += [os.path.join(d, n) for n in names]
        up = os.path.dirname(d)
        d = None if up == d else up
    for f in files + glob_files:
        try:
            with open(f, errors="replace") as fh:
                text = fh.read(200_000)
        except OSError:
            continue
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r'^"?claude(?:-code)?"?\s*=\s*"([^"]+)"', line) or \
                re.match(r"^claude(?:-code)?\s+(\S+)", line)
            if m:
                return f, m.group(1)
    return None, None


def needs_admin(method, real, access=os.access):
    """True when updating this install needs an administrator (Pit Stop never uses sudo): system
    packages always; npm or native when the folder holding Claude Code is not writable by this user
    (an npm install under /usr/lib, seen 2026-11-16). Pure but for ACCESS."""
    if method in ("apt", "dnf", "apk"):
        return True
    r = _norm(real)
    if method == "npm":
        i = r.find("/node_modules/@anthropic-ai/claude-code")
        return i >= 0 and not access(r[:i] + "/node_modules", os.W_OK)
    if method == "native":
        i = r.find("/versions/")
        return i >= 0 and not access(r[:i + len("/versions")], os.W_OK)
    return False


def channel(method, real=None, settings=None):
    """The release channel this install follows: "latest" or "stable"."""
    if method == "homebrew":
        return "latest" if "/claude-code@latest" in _norm(real) else "stable"
    if method in ("native", "npm"):
        s = settings if settings is not None else pc.read_json(os.path.join(pc.CLAUDE_DIR, "settings.json"), {})
        c = s.get("autoUpdatesChannel") if isinstance(s, dict) else None
        return c if c in ("latest", "stable") else "latest"
    return "latest"


def release_check_allowed(env=None, cfg=None):
    env = os.environ if env is None else env
    cfg = pc.config() if cfg is None else cfg
    if any(env.get(k) for k in ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_UPDATES")):
        return False
    return cfg.get("release_check", True) is not False


def fetch_release(chan, opener=None):
    """The version number Anthropic publishes for CHAN, or None."""
    import urllib.request
    opener = opener or (lambda req: urllib.request.urlopen(req, timeout=10))
    try:
        req = urllib.request.Request(RELEASES + chan, headers={"User-Agent": "pit-stop-release-check"})
        with opener(req) as r:
            body = r.read(64).decode("ascii", "replace").strip()
        return body if VER.fullmatch(body) else None
    except Exception:
        return None


def newest_released(chan, now=None, fetch=None, path=None, allowed=None, write=True):
    """The newest version on CHAN and when Pit Stop first saw it: (version, first_seen) or (None, None).
    Asked at most every RELEASE_EVERY; the last answer is kept for when the server cannot be reached.
    WRITE False: never write the cache file (a hand run that promises to write nothing)."""
    now = time.time() if now is None else now
    path = path or RELEASED_FILE
    data = pc.read_json(path, {})
    data = data if isinstance(data, dict) else {}
    row = data.get(chan) if isinstance(data.get(chan), dict) else {}
    fresh = isinstance(row.get("checked"), (int, float)) and now - row["checked"] < RELEASE_EVERY
    if not fresh and (release_check_allowed() if allowed is None else allowed):
        v = (fetch or fetch_release)(chan)
        if v:
            if v != row.get("version"):
                row = {"version": v, "first_seen": now}
            row["checked"] = now
            data[chan] = row
            try:
                if not write:
                    raise OSError("read-only run")
                pc.write_json_atomic(path, data, mode=0o644)
            except OSError:
                pass
    v = row.get("version")
    return (v, row.get("first_seen")) if isinstance(v, str) and VER.fullmatch(v) else (None, None)


def updater_state(method, env=None, settings=None):
    """Whether Claude Code updates itself here: "on", "off (<why>)", or "not used (<method>)"."""
    if METHODS.get(method, ("unknown",))[0] != "self":
        if os.environ.get("CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE") and method in ("homebrew", "winget"):
            return "on (CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE)"
        return f"not used ({method} decides)"
    env = os.environ if env is None else env
    for k in ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER"):
        if env.get(k):
            return f"off ({k})"
    s = settings if settings is not None else pc.read_json(os.path.join(pc.CLAUDE_DIR, "settings.json"), {})
    e = (s.get("env") or {}) if isinstance(s, dict) else {}
    for k in ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER"):
        if isinstance(e, dict) and e.get(k):
            return f"off ({k} in settings.json)"
    return "on"


def machine(folder=None, now=None, fetch=None, path_env=None, run=None, allowed=None, write=True):
    """Everything Pit Stop says about this machine's Claude Code, as a dict:
    method, path, real, installed (what a new session starts), channel,
    released (newest on that channel) and released_seen, behind (bool),
    pin (file, value) for a version manager, who ("self", "manager", "pin",
    "unknown"), update (the one command), updater, restart_ok."""
    link, real = find_claude(path_env)
    method = classify(real, link) if real else "unknown"
    installed = None
    if real:
        installed = version_from_path(real, method) or version_by_running(link, run)
    if not installed and method == "native":
        installed = pc.newest_installed_version()
    chan = channel(method, real)
    released, seen = newest_released(chan, now, fetch, allowed=allowed, write=write)
    pin = tool_pin(method, folder)
    who, cmd = METHODS.get(method, METHODS["unknown"])
    admin = bool(real) and needs_admin(method, real)
    if admin and method == "npm":
        who, cmd = "admin", ("sudo npm install -g @anthropic-ai/claude-code@latest (needs an administrator), "
                             "or move to Anthropic's installer, which needs none: claude install")
    elif admin and method == "native":
        who = "admin"
    if method == "homebrew":
        cmd = cmd.format(cask="claude-code@latest" if chan == "latest" else "claude-code")
    if method == "mise" and pin[0] and pin[0] != os.path.join(pc.HOME, ".config", "mise", "config.toml"):
        cmd = f"mise use claude@latest (in {os.path.dirname(pin[0]).replace(pc.HOME, '~')})"
    behind = bool(installed and released and pc.vkey(installed) < pc.vkey(released))
    import platform as _p
    wsl = False
    try:
        with open("/proc/version") as f:
            wsl = "microsoft" in f.read().lower()
    except OSError:
        pass
    restart_ok = method == "native" and _p.system() in ("Darwin", "Linux") and not wsl
    return {"method": method, "path": link, "real": real, "installed": installed, "channel": chan,
            "released": released, "released_seen": seen, "behind": behind, "pin": list(pin), "who": who,
            "update": cmd, "updater": updater_state(method), "restart_ok": restart_ok, "admin": admin}


def held_by_owner(cfg=None):
    """The owner's word to leave Claude Code's version alone: "hold_claude_version" in
    ~/.claude/pit-stop/config.json, true (hold whatever is installed) or a version string
    (hold that one). Returns the reason, or None."""
    cfg = pc.config() if cfg is None else cfg
    h = cfg.get("hold_claude_version")
    if h is True:
        return "held by its owner (hold_claude_version in ~/.claude/pit-stop/config.json)"
    if isinstance(h, str) and h.strip():
        return f"held at {h.strip()} by its owner (hold_claude_version)"
    return None


def switched_off_by_owner(env=None, settings=None):
    """DISABLE_UPDATES or DISABLE_AUTOUPDATER, in the environment or in the "env" of
    ~/.claude/settings.json: the owner's no to updates, whatever the install method.
    Returns the reason, or None."""
    env = os.environ if env is None else env
    for k in ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER"):
        if env.get(k):
            return f"its owner switched Claude Code's updates off ({k})"
    s = settings if settings is not None else pc.read_json(os.path.join(pc.CLAUDE_DIR, "settings.json"), {})
    e = (s.get("env") or {}) if isinstance(s, dict) else {}
    for k in ("DISABLE_UPDATES", "DISABLE_AUTOUPDATER"):
        if isinstance(e, dict) and e.get(k):
            return f"its owner switched Claude Code's updates off ({k} in settings.json)"
    return None


def update_plan(m, cfg=None, which=shutil.which, env=None, settings=None):
    """What Pit Stop may run to bring this machine's Claude Code to the newest release:
    (argv, None) or (None, why not). Never sudo, never a project's own pin, never against the
    owner's word (hold_claude_version, or Claude Code's updater switched off). Pure but for CFG
    and WHICH. The helper runs it (restart.json "claude_updates": "auto"); running sessions keep
    their version until their restart, which the helper does at a break."""
    if not m.get("behind"):
        return None, "current" if m.get("released") else "newest release not known (check off or offline)"
    held = held_by_owner(cfg) or switched_off_by_owner(env, settings)
    if held:
        return None, held
    method, rel = m.get("method"), m.get("released")
    if m.get("admin") or method in ("apt", "dnf", "apk"):
        return None, "needs an administrator; Pit Stop never uses sudo"
    if method in ("native", "npm"):
        if (m.get("updater") or "").startswith("off"):
            return None, f"its owner switched Claude Code's updates {m['updater']}"
        prog = m.get("path") or which("claude")
        return ([prog, "update"], None) if prog else (None, "no claude program found")
    if method == "homebrew":
        brew = which("brew")
        cask = "claude-code@latest" if m.get("channel") == "latest" else "claude-code"
        return ([brew, "upgrade", "--cask", cask], None) if brew else (None, "brew not found on PATH")
    if method == "mise":
        mise = which("mise") or (m.get("real") if os.path.basename(m.get("real") or "") == "mise" else None)
        if not mise:
            return None, "mise not found on PATH"
        f, v = (m.get("pin") or [None, None])[:2]
        if f and f not in (os.path.join(pc.HOME, ".config", "mise", "config.toml"),):
            return None, f"pinned in a project's own file ({f.replace(pc.HOME, '~')}); Pit Stop moves only the global pin"
        if v and not VER.fullmatch(v):   # "latest" or a prefix: install what it now resolves to
            return [mise, "upgrade", "claude"], None
        return [mise, "use", "-g", f"claude@{rel}"], None
    if method == "winget":
        return None, "WinGet updates run on Windows, where the helper does not run yet"
    return None, f"{method} installs are moved by a person ({m.get('update') or 'see its manager'})"


BEHIND_FILE = os.path.join(pc.STATE_DIR, "machine-behind.json")


def behind_for(m, now=None, path=None, write=True):
    """Seconds this machine's Claude Code has been behind the newest release,
    counted from the first time Pit Stop saw it behind (kept across new
    releases, so a machine held for weeks while releases come daily still
    reads as weeks), or None when it is current or cannot be told."""
    now = time.time() if now is None else now
    path = path or BEHIND_FILE
    row = pc.read_json(path, {})
    row = row if isinstance(row, dict) else {}
    if not m.get("behind"):
        if row and write:
            try:
                os.remove(path)
            except OSError:
                pass
        return None
    since = row.get("since") if isinstance(row.get("since"), (int, float)) else None
    if since is None:
        since = now
        if write:
            try:
                pc.write_json_atomic(path, {"since": now, "installed": m.get("installed")}, mode=0o644)
            except OSError:
                pass
    return max(0, now - since)


def describe(m, now=None, paths=True):
    """Plain lines about M (from machine()) for a person; the last line says what to do, when anything.
    PATHS False: no file paths (a page to show others: a project's own pin file names the project)."""
    now = time.time() if now is None else now
    name = {"native": "Anthropic's native installer", "npm": "npm", "homebrew": "Homebrew", "winget": "WinGet",
            "apt": "apt", "dnf": "dnf", "apk": "apk", "mise": "mise", "asdf": "asdf", "nix": "nix"}.get(m["method"])
    out = [f"Claude Code here: {m['installed'] or 'version unknown'}"
           + (f", installed with {name}" if name else ", installed in a way Pit Stop does not know")
           + (f" ({m['real'].replace(pc.HOME, '~')})" if m.get("real") and paths else "" if m.get("real")
              else " (no claude found on PATH)")]
    if m["released"]:
        out.append(f"Newest on the {m['channel']} channel: {m['released']}"
                   + (" (this machine has it)" if not m["behind"] else ""))
    else:
        out.append("Newest release: not checked (switched off, or the download server could not be reached)")
    if m["behind"]:
        age = (now - m["released_seen"]) if m.get("released_seen") else None
        held = m["pin"][0] and m["pin"][1] and VER.fullmatch(m["pin"][1] or "")
        why = (f"held at {m['pin'][1]} by " + (m['pin'][0].replace(pc.HOME, '~') if paths else f"a {m['method']} config file")
               if held else
               "it is installed where only an administrator can change it" if m.get("admin") else
               "Claude Code's own updater is " + m["updater"] if m["who"] == "self" else
               f"{name or 'its package manager'} updates it, not Claude Code")
        out.append(f"BEHIND: {why}" + (f"; Pit Stop has known of {m['released']} for {hours(age)}" if age and age >= 3600 else "")
                   + (f". To move it: {m['update']}" if m["update"] else ""))
    return out


def hours(sec):
    return f"{sec / 3600:.0f} hours" if sec < 72 * 3600 else f"{sec / 86400:.0f} days"


def main():
    m = machine(os.getcwd())
    if "--json" in sys.argv:
        print(json.dumps(m, indent=1))
    else:
        print("\n".join(describe(m)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
