"""Shared helpers for Pit Stop. Standard library only; reads local files only.

Nothing here sends data anywhere. Transcripts are read for COUNTS (token
usage, tool names, timestamps, file paths); message text is never printed.
"""
import glob
import json
import os
import re
import subprocess
import time

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(HOME, ".claude"))
PROJECTS = os.path.join(CLAUDE_DIR, "projects")
STATE_DIR = os.path.join(CLAUDE_DIR, "pit-stop")
VERSIONS = os.path.join(HOME, ".local", "share", "claude", "versions")
SESSIONS = os.path.join(CLAUDE_DIR, "sessions")
CONFIG = os.path.join(STATE_DIR, "config.json")    # user-level switches, never read from a project folder
LANES = os.path.join(STATE_DIR, "lanes")           # one profile per session id
FOLDERS = os.path.join(STATE_DIR, "folders")       # one profile per folder (0.1.x kept it in <folder>/.claude/pit-stop.json)
ASKS = os.path.join(STATE_DIR, "asks")             # background-jobs asks waiting for a session's Stop hook
SEEN = os.path.join(STATE_DIR, "seen")             # sessions whose Stop hook has run (the plugin is loaded there)

# Claude Code's own idle compaction (settings key idleCompaction, on by default)
# compacts a session that has gone quiet, shortly before its prompt cache
# expires, once its context is at least CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS
# (default 200K; Claude Code never goes under 100K).
IDLE_ENV = "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS"
WINDOW_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
IDLE_DEFAULT = 200_000
IDLE_FLOOR = 100_000

# Claude Code loads only the first 200 lines of a project's MEMORY.md index.
MEMORY_LOAD_LINES = 200
MEMORY_WARN_LINES = 180
# Claude Code also stops reading MEMORY.md at about 24.4 KB (its own notice, 2.1.322), whichever limit comes first.
MEMORY_LOAD_BYTES = 24_400
MEMORY_WARN_BYTES = 20_000


def project_key(path):
    """Claude Code stores each folder's transcripts and memory under a key made
    from the absolute path with every non-alphanumeric character turned into '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def project_dir(path):
    return os.path.join(PROJECTS, project_key(path))


def folder_profile_path(path):
    return os.path.join(FOLDERS, project_key(path) + ".json")


def folder_profile(path):
    """A folder's Pit Stop profile: the user-level file first, then the 0.1.x
    file inside the folder. Returns {} when there is none."""
    for f in (folder_profile_path(path), os.path.join(path, ".claude", "pit-stop.json")):
        d = read_json(f, None)
        if isinstance(d, dict):
            return d
    return {}


def memory_index(path):
    return os.path.join(project_dir(path), "memory", "MEMORY.md")


def transcripts(path, days=None):
    d = project_dir(path)
    if not os.path.isdir(d):
        return []
    out = []
    cutoff = time.time() - days * 86400 if days else 0
    for f in os.listdir(d):
        if f.endswith(".jsonl"):
            p = os.path.join(d, f)
            if os.path.getmtime(p) >= cutoff:
                out.append(p)
    return sorted(out, key=os.path.getmtime)


def iter_entries(path, tail_bytes=None):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if tail_bytes and size > tail_bytes:
            f.seek(size - tail_bytes)
            f.readline()
        for raw in f:
            try:
                e = json.loads(raw)
            except Exception:
                continue
            if isinstance(e, dict):  # a transcript line is an object; anything else is skipped
                yield e


def message(entry):
    """An entry's message as a dict ({} when it is missing or not an object)."""
    m = entry.get("message")
    return m if isinstance(m, dict) else {}


def usage(entry):
    u = message(entry).get("usage")
    return u if isinstance(u, dict) else {}


def num(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def usage_total(entry):
    u = usage(entry)
    return sum(num(u.get(k)) for k in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))


def is_compact_boundary(entry):
    return entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"


def content_kinds(entry):
    c = message(entry).get("content")
    if isinstance(c, str):
        return ["text"]
    if isinstance(c, list):
        return [x.get("type") for x in c if isinstance(x, dict)]
    return []


def tool_uses(entry):
    c = message(entry).get("content")
    if not isinstance(c, list):
        return []
    return [x for x in c if isinstance(x, dict) and x.get("type") == "tool_use"]


def read_json_strict(path):
    """(data, None) for a file that parses, ({}, None) when it does not exist,
    (None, reason) when it exists but cannot be read as JSON: callers that are
    about to REWRITE the file must refuse then, or they would drop its contents."""
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path) as f:
            return json.load(f), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"


def parse_ts(s):
    if not s:
        return None
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-pitstop"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def backup(path):
    """Copy a file next to itself as <name>.bak-pitstop-<UTC time>; returns the backup path."""
    if not os.path.exists(path):
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dst = f"{path}.bak-pitstop-{stamp}"
    with open(path, "rb") as a, open(dst, "wb") as b:
        b.write(a.read())
    os.chmod(dst, os.stat(path).st_mode & 0o777)
    return dst


def newest_installed_version():
    try:
        vs = [v for v in os.listdir(VERSIONS) if re.fullmatch(r"\d+\.\d+\.\d+", v)]
    except OSError:
        return None
    return max(vs, key=lambda v: tuple(int(x) for x in v.split("."))) if vs else None


def fmt_k(n):
    if n is None:
        return "-"
    return f"{n/1000:.0f}K" if n < 1_000_000 else f"{n/1_000_000:.2f}M"


def config():
    """User-level switches in ~/.claude/pit-stop/config.json (all optional):
      auto_tune            true = re-tune each folder weekly in the background (set by /pit-stop setup)
      compact_instructions true (default), false, or a string of extra notes for the summary
      checkpoint_break     false = during a hold, a finished git commit or push does not count as a break
      nudge                false = never show the "an update is waiting" line
      budget               "economy", "balanced" (default) or "roomy": how hard tuning holds down usage
    """
    d = read_json(CONFIG, {})
    return d if isinstance(d, dict) else {}


def vkey(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except Exception:
        return ()


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def pid_version(pid):
    """The Claude Code version a process runs, read from its program file
    (.../claude/versions/2.1.322), or None."""
    if not pid:
        return None
    exe = None
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        try:  # macOS has no /proc
            exe = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=10).stdout.strip()
        except Exception:
            exe = None
    base = os.path.basename((exe or "").replace(" (deleted)", ""))
    return base if re.fullmatch(r"\d+\.\d+\.\d+", base) else None


def registry_sessions():
    """Rows from Claude Code's local session registry (~/.claude/sessions/*.json).
    Not a public interface: it carries what the documented listing lacks
    (version, the time of the last status change)."""
    out = {}
    for f in glob.glob(os.path.join(SESSIONS, "*.json")):
        s = read_json(f, {})
        if isinstance(s, dict) and s.get("sessionId") and s.get("pid"):
            out[s["sessionId"]] = s
    return out


def cli_sessions(timeout=30):
    """Rows from `claude agents --json`, the documented listing of running
    sessions (cwd, kind, name, pid, sessionId, startedAt, status), or None."""
    try:
        r = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        rows = json.loads(r.stdout)
    except Exception:
        return None
    if isinstance(rows, dict):
        rows = rows.get("agents") or rows.get("sessions")
    if not isinstance(rows, list):
        return None
    return [x for x in rows if isinstance(x, dict) and x.get("sessionId") and x.get("pid")]


def live_sessions(use_cli=True):
    """(rows, source) for every running interactive session.

    The local registry is read first because it is cheap and has more fields.
    If it gives nothing usable (the folder moved, or its rows no longer carry
    a status), the documented `claude agents --json` listing is used instead,
    so a change to the registry does not blind Pit Stop. Each row has: pid,
    sessionId, cwd, name, status, version (from the process itself when it
    can be read), statusUpdatedAt (registry only, else None)."""
    reg = registry_sessions()
    rows = [s for s in reg.values() if pid_alive(s.get("pid"))]
    source = "registry"
    if use_cli and not any(s.get("status") for s in rows):
        cli = cli_sessions()
        if cli is not None:
            rows = [dict(reg.get(x["sessionId"], {}), **x) for x in cli if pid_alive(x.get("pid"))]
            source = "claude agents"
    out = []
    for s in rows:
        if s.get("kind") not in (None, "interactive"):
            continue
        s = dict(s)
        s["version"] = pid_version(s.get("pid")) or s.get("version")
        s["name"] = (s.get("name") or "").strip()
        out.append(s)
    return out, source


def session_version(session_id):
    """The version a session runs, from the registry row for that session id."""
    s = registry_sessions().get(session_id) or {}
    if s.get("pid") and pid_alive(s["pid"]):
        return pid_version(s["pid"]) or s.get("version")
    return None


def safe_id(session_id):
    return "".join(ch for ch in str(session_id) if ch.isalnum() or ch == "-")[:64] or "unknown"


def env_int(folder, name):
    """A whole-number env value set for a folder in its Claude Code settings, or None."""
    for f in (os.path.join(folder, ".claude", "settings.local.json"), os.path.join(folder, ".claude", "settings.json")):
        d = read_json(f, {})
        v = (d.get("env") or {}).get(name) if isinstance(d, dict) and isinstance(d.get("env"), dict) else None
        if v is not None and str(v).isdigit():
            return int(v)
    return None


def points(folder, session_id=None):
    """The compaction points in force for a session: its own lane profile
    first, then the folder's settings and profile. Returns a dict with
    idle_point, window, ceiling (any may be None) and source."""
    out = {"idle_point": None, "window": None, "ceiling": None, "source": None}
    lane = read_json(os.path.join(LANES, safe_id(session_id) + ".json"), {}) if session_id else {}
    prof = folder_profile(folder) if folder else {}
    lane = lane if isinstance(lane, dict) else {}
    for k in ("idle_point", "window", "ceiling"):
        for src, d in (("lane", lane), ("folder", prof)):
            try:
                v = int(d.get(k) or 0)
            except Exception:
                v = 0
            if v > 0:
                out[k] = v
                out["source"] = out["source"] or src
                break
    if folder:
        out["window"] = out["window"] or env_int(folder, WINDOW_ENV)
        out["idle_point"] = out["idle_point"] or env_int(folder, IDLE_ENV)
    return out


def plugin_installed(claude_dir=None):
    """(version, epoch seconds) of the Pit Stop plugin as Claude Code records it
    in installed_plugins.json: the time it was last installed or updated. A
    session whose process started before that time still runs the hooks it
    loaded at its start (or none). (None, None) when it cannot be read."""
    import calendar
    try:
        with open(os.path.join(claude_dir or CLAUDE_DIR, "plugins", "installed_plugins.json")) as f:
            plugins = json.load(f).get("plugins") or {}
        if not isinstance(plugins, dict):
            raise ValueError("plugins is not an object")
    except Exception:
        return None, None
    rows = [r for k, v in plugins.items() if k.split("@")[0] == "windstorm-pit-stop" and isinstance(v, list)
            for r in v if isinstance(r, dict)]
    rows.sort(key=lambda r: r.get("scope") != "user")
    for r in rows:
        when = r.get("lastUpdated") or r.get("installedAt")
        try:
            return r.get("version"), calendar.timegm(time.strptime(str(when)[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
    return None, None


PLUGIN_NAME = "windstorm-pit-stop"


def hook_files_signature(root):
    """sha256 over the plugin's hook files (hooks/hooks.json and hooks/*.py),
    names and bytes, in name order. None if there is no hooks.json."""
    import hashlib
    d = os.path.join(root or "", "hooks")
    if not root or not os.path.isfile(os.path.join(d, "hooks.json")):
        return None
    h = hashlib.sha256()
    for name in sorted(n for n in os.listdir(d) if n == "hooks.json" or n.endswith(".py")):
        with open(os.path.join(d, name), "rb") as f:
            h.update(name.encode() + b"\0" + f.read().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def hook_generation(root):
    """The HOOK GENERATION of a plugin folder: one whole number, kept in
    hooks/generation.json, that a release raises only when a running session
    must reload to work properly. A release that leaves the hooks alone, or
    changes them in a way an older running session can live with, keeps it.
    A folder with hooks but no such file (0.1.x) is generation 1. None when
    the folder or its hooks.json is gone: a session whose hooks ran from there
    now has none that work."""
    if not root or not os.path.isfile(os.path.join(root, "hooks", "hooks.json")):
        return None
    g = (read_json(os.path.join(root, "hooks", "generation.json"), {}) or {})
    g = g.get("generation") if isinstance(g, dict) else None
    return g if isinstance(g, int) and not isinstance(g, bool) and g > 0 else 1


def plugin_versions(claude_dir=None):
    """Every Pit Stop version folder in Claude Code's plugin cache, oldest
    first: [(installed epoch, root)]. The time is that of the folder's
    .claude-plugin/plugin.json, which Claude Code writes when it fetches the
    version (the folder's own time moves whenever Python caches a module in it)."""
    out = []
    for root in glob.glob(os.path.join(claude_dir or CLAUDE_DIR, "plugins", "cache", "*", PLUGIN_NAME, "*")):
        try:
            out.append((os.path.getmtime(os.path.join(root, ".claude-plugin", "plugin.json")), root))
        except OSError:
            continue
    return sorted(out)


def root_loaded_at(when, versions):
    """The version folder a session that loaded its plugins at WHEN got: the
    newest one already there at that time. None if Pit Stop was not installed yet."""
    hits = [root for t, root in versions if t <= when]
    return hits[-1] if hits else None


def plugin_root(claude_dir=None):
    """The folder of the installed plugin, as Claude Code records it (user scope first), or None."""
    try:
        with open(os.path.join(claude_dir or CLAUDE_DIR, "plugins", "installed_plugins.json")) as f:
            plugins = json.load(f).get("plugins") or {}
        if not isinstance(plugins, dict):
            raise ValueError("plugins is not an object")
    except Exception:
        return None
    rows = [r for k, v in plugins.items() if k.split("@")[0] == PLUGIN_NAME and isinstance(v, list)
            for r in v if isinstance(r, dict)]
    rows.sort(key=lambda r: r.get("scope") != "user")
    for r in rows:
        if r.get("installPath") and os.path.isdir(r["installPath"]):
            return r["installPath"]
    return None


def fleet_active(max_age=3600):
    """True when the restart helper has run in the last hour (fleet mode is on)."""
    try:
        return time.time() - os.path.getmtime(os.path.join(STATE_DIR, "restart", "restart.log")) < max_age
    except OSError:
        return False


def append_log(path, msg, max_bytes=5_000_000):
    """One line with a UTC time; the file rotates to <name>.1 at 5 MB."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
        with open(path, "a") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + msg + "\n")
    except Exception:
        pass


def prune_backups(path, keep=3):
    """Keep only the newest KEEP <path>.bak-pitstop-* files (the weekly tune would pile them up)."""
    old = sorted(glob.glob(glob.escape(path) + ".bak-pitstop-*"))[:-keep]
    for f in old:
        try:
            os.remove(f)
        except OSError:
            pass
