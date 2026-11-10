"""Shared helpers for Pit Stop. Standard library only; reads local files only.

Nothing here sends data anywhere. Transcripts are read for COUNTS (token
usage, tool names, timestamps, file paths); message text is never printed.
"""
import json
import os
import re
import time

HOME = os.path.expanduser("~")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(HOME, ".claude"))
PROJECTS = os.path.join(CLAUDE_DIR, "projects")
STATE_DIR = os.path.join(CLAUDE_DIR, "pit-stop")
VERSIONS = os.path.join(HOME, ".local", "share", "claude", "versions")

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
                yield json.loads(raw)
            except Exception:
                continue


def usage_total(entry):
    u = (entry.get("message") or {}).get("usage") or {}
    return sum(int(u.get(k) or 0) for k in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))


def is_compact_boundary(entry):
    return entry.get("type") == "system" and entry.get("subtype") == "compact_boundary"


def content_kinds(entry):
    c = (entry.get("message") or {}).get("content")
    if isinstance(c, str):
        return ["text"]
    if isinstance(c, list):
        return [x.get("type") for x in c if isinstance(x, dict)]
    return []


def tool_uses(entry):
    c = (entry.get("message") or {}).get("content")
    if not isinstance(c, list):
        return []
    return [x for x in c if isinstance(x, dict) and x.get("type") == "tool_use"]


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
