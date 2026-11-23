#!/usr/bin/env python3
"""Pit Stop conformance kit: does Pit Stop work in this Claude Code surface?

Run it on a test machine, never on a computer whose Claude Code someone relies on:
it installs an older Pit Stop and turns on auto-update for the thewindstorm marketplace.

  python3 kit/conformance.py [--from REF] [--start cli|none] [--since ISO] [--window N]
                             [--model M] [--turns N] [--wait-update MIN] [--out FILE]
                             [--evidence FILE] [--dry-run]

It is a test tool, not part of Pit Stop: it lives in kit/, and it is the only file here that starts
`claude -p` (Pit Stop itself never does: INV6).

Steps
  1. install   adds the thewindstorm marketplace if needed, turns its auto-update on, sets
               its copy back to REF (default: the v* tag before the newest; open question 7)
               and installs Pit Stop from it, so the copy looks as it did when REF was new.
  2. session   --start cli: one `claude -p` session in a scratch folder with
               CLAUDE_CODE_AUTO_COMPACT_WINDOW=N, reading filler files during a tool run.
               After each turn it reads the session's own transcript; it gives another turn
               of new files (up to --turns) until precompact.log shows a HOLD and an ALLOW,
               or the context stops growing. Seen 2026-11-22: two prompts reached only 25K
               of a 100K window, and nothing said so.
               --start none: the person or agent drives a session in the surface under
               test (an editor extension, the desktop app) and runs the kit again with
               --since, the time before that session started.
  3. checks    read files only and print one line each: `<check>: PASS|FAIL <evidence file>`.
               fill     the session's own transcript shows its context passed the window
                        (the largest input of any of its model calls); FAIL here means the
                        KIT did not fill the session, so hold and allow say nothing about Pit Stop
               hold     a HOLD line in pit-stop/precompact.log since the start
               allow    an ALLOW line there since the start
               stop     a pit-stop/seen/<session_id> stamp newer than the start (the Stop hook ran)
               updated  plugins/installed_plugins.json names a Pit Stop version newer than the
                        one step 1 installed (Claude Code's own plugin auto-update ran); with
                        --wait-update the kit checks again each 30 s for that many minutes
Evidence: the peak the kit read, turn by turn, with times and no content, is printed as it goes and
written to --evidence (default: next to --out, else conformance-evidence-<time>.jsonl in the folder the
kit was started from). Run the kit over ssh and keep its output on the machine you drive it from: a
test account's files are cleaned after the run, and with them the transcript.
Exit code 0 when every check passes, 1 when one fails, 2 when a step before the checks fails, 3 when
the kit did not fill the session past the window (then hold and allow say nothing about Pit Stop).
"""
import glob
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

MARKET = "thewindstorm"
REPO = "TheWindstorm/windstorm-pit-stop"
PLUGIN = "windstorm-pit-stop@" + MARKET
CHECKS = ("fill", "hold", "allow", "stop", "updated")
LOG_LINE = re.compile(r"^(\S+) session=(\S+) trigger=auto tokens=\S+ (HOLD|ALLOW)\b")


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def parse_ts(s):
    try:
        return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.+-]", str(v or "0"))[:3])


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---- checks: read files only -------------------------------------------------------------

def installed_version(cfg):
    d = read_json(os.path.join(cfg, "plugins", "installed_plugins.json"), {}) or {}
    rows = (d.get("plugins") or {}).get(PLUGIN) or []
    vs = [r.get("version") for r in rows if isinstance(r, dict) and r.get("version")]
    return max(vs, key=vkey) if vs else None


def check_log(cfg, since, decision, sessions=None):
    """PASS when precompact.log (or its rotated copy) has a `decision` line at or after `since`."""
    state = os.path.join(cfg, "pit-stop")
    for name in ("precompact.log", "precompact.log.1"):
        p = os.path.join(state, name)
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = LOG_LINE.match(line)
                    if not m or m.group(3) != decision:
                        continue
                    t = parse_ts(m.group(1))
                    if t is None or t + 1 < since:
                        continue
                    if sessions and not any(s[:8] == m.group(2)[:8] for s in sessions):
                        continue
                    return True, p
        except OSError:
            continue
    return False, os.path.join(state, "precompact.log")


def model_input(e):
    """The context of one model call: its input tokens, read from the cache or not. None for other rows."""
    m = e.get("message") if isinstance(e.get("message"), dict) else {}
    u = m.get("usage") if isinstance(m.get("usage"), dict) else None
    if e.get("type") != "assistant" or e.get("isSidechain") or not u:
        return None
    return sum(u.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
               if isinstance(u.get(k), int) and not isinstance(u.get(k), bool))


def is_prompt(e):
    m = e.get("message") if isinstance(e.get("message"), dict) else {}
    c = m.get("content")
    return (e.get("type") == "user" and not e.get("isMeta") and not e.get("isSidechain")
            and not (isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)))


def peaks_by_turn(path):
    """[peak context of each turn] of one transcript, a turn starting at each prompt. Reads only."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                if is_prompt(e) or not out:
                    out.append(0)
                n = model_input(e)
                if n:
                    out[-1] = max(out[-1], n)
    except OSError:
        return []
    return [p for p in out if p] if any(out) else []


def transcripts(cfg, since, sessions=None):
    """The session transcripts this run made: by session id, else every transcript written since SINCE."""
    if sessions:
        return [p for s in sessions for p in glob.glob(os.path.join(cfg, "projects", "*", glob.escape(s) + ".jsonl"))]
    out = []
    for p in glob.glob(os.path.join(cfg, "projects", "*", "*.jsonl")):
        try:
            if os.path.getmtime(p) + 1 >= since:
                out.append(p)
        except OSError:
            continue
    return sorted(out)


def check_fill(cfg, since, window, sessions=None):
    """PASS when a transcript of this run shows a model call whose context reached WINDOW; the evidence is
    the transcript with the largest one (the projects folder when there is none)."""
    best = max(((max(peaks_by_turn(p), default=0), p) for p in transcripts(cfg, since, sessions)), default=None)
    if not best:
        return False, os.path.join(cfg, "projects")
    return bool(window and best[0] >= window), best[1]


def check_stop(cfg, since, sessions=None):
    seen = os.path.join(cfg, "pit-stop", "seen")
    try:
        names = os.listdir(seen)
    except OSError:
        return False, seen
    for n in sorted(names):
        if sessions and n not in sessions:
            continue
        p = os.path.join(seen, n)
        try:
            if os.path.getmtime(p) + 1 >= since:
                return True, p
        except OSError:
            continue
    return False, seen


def check_updated(cfg, base_version):
    p = os.path.join(cfg, "plugins", "installed_plugins.json")
    v = installed_version(cfg)
    return bool(v and base_version and vkey(v) > vkey(base_version)), p


def run_checks(cfg, since, base_version, sessions=None, window=100_000):
    """[(check, ok, evidence file)] in CHECKS order. Opens files for reading only."""
    out = []
    for c in CHECKS:
        if c == "fill":
            ok, ev = check_fill(cfg, since, window, sessions)
        elif c == "hold":
            ok, ev = check_log(cfg, since, "HOLD", sessions)
        elif c == "allow":
            ok, ev = check_log(cfg, since, "ALLOW", sessions)
        elif c == "stop":
            ok, ev = check_stop(cfg, since, sessions)
        else:
            ok, ev = check_updated(cfg, base_version)
        out.append((c, ok, ev))
    return out


def format_line(check, ok, evidence):
    return f"{check}: {'PASS' if ok else 'FAIL'} {evidence}"


# ---- steps 1 and 2: install and session (these write) ------------------------------------

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)


def git(clone, *args):
    return run(["git", "-C", clone, *args])


def previous_tag(clone):
    tags = [t for t in git(clone, "tag", "-l", "v*").stdout.split() if re.match(r"^v\d+\.\d+\.\d+$", t)]
    tags.sort(key=lambda t: vkey(t[1:]))
    return tags[-2] if len(tags) >= 2 else None


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def install(cfg, ref, dry):
    """Returns (version installed, the REF used). Writes only Claude Code's plugin files."""
    km = os.path.join(cfg, "plugins", "known_marketplaces.json")
    if MARKET not in (read_json(km, {}) or {}):
        if dry:
            print(f"would run: claude plugin marketplace add {REPO}")
        else:
            r = run(["claude", "plugin", "marketplace", "add", REPO])
            if r.returncode:
                fail(f"install: marketplace add failed: {r.stderr.strip()[:300]}")
    d = read_json(km, {}) or {}
    clone = (d.get(MARKET) or {}).get("installLocation") or os.path.join(cfg, "plugins", "marketplaces", MARKET)
    if dry and not os.path.isdir(clone):
        return None, ref
    # Claude Code clones a marketplace with only its newest commit; older releases need the history
    shallow = git(clone, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    if not dry:
        git(clone, "fetch", "-q", "--tags", *(["--unshallow"] if shallow else []), "origin")
    ref = ref or previous_tag(clone)
    if not ref:
        fail("install: no previous v* release tag; pass --from with a commit or tag")
    branch = git(clone, "rev-parse", "--abbrev-ref", "origin/HEAD").stdout.strip().split("/")[-1] or "main"
    if dry:
        print(f"would set {clone} to {ref} on branch {branch}, turn autoUpdate on in {km}, "
              f"and run: claude plugin install {PLUGIN}")
        return None, ref
    # the copy looks as it did when REF was the newest release: branch at REF, tracking origin
    for args in (("checkout", "-q", "-B", branch, ref), ("branch", "-q", "--set-upstream-to", f"origin/{branch}")):
        r = git(clone, *args)
        if r.returncode:
            fail(f"install: git {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    d = read_json(km, {}) or {}
    if isinstance(d.get(MARKET), dict) and d[MARKET].get("autoUpdate") is not True:
        d[MARKET]["autoUpdate"] = True
        with open(km, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    if installed_version(cfg):
        run(["claude", "plugin", "uninstall", PLUGIN])
    r = run(["claude", "plugin", "install", PLUGIN])
    if r.returncode:
        fail(f"install: plugin install failed: {r.stderr.strip()[:300]}")
    v = installed_version(cfg)
    if not v:
        fail("install: Pit Stop is not in installed_plugins.json after the install")
    return v, ref


FILLER_LINE = "Pit Stop conformance filler line {n:06d}: the quick brown fox jumps over the lazy dog.\n"


PER_FILE = 800                                   # lines; about 18K tokens a file, under the Read limit


def write_files(work, first, count):
    for i in range(first, first + count):
        with open(os.path.join(work, f"part{i:02d}.txt"), "w", encoding="utf-8") as f:
            for n in range(PER_FILE):
                f.write(FILLER_LINE.format(n=i * PER_FILE + n))


def turn_prompt(first, count):
    return (f"Read each of the files part{first:02d}.txt to part{first + count - 1:02d}.txt in this folder, one Read call "
            "each, in order and in full, with no offset and no limit. Then say how many lines they have in total.")


def evidence_line(turn, peak, window, path):
    """One evidence record: numbers, a time and a file name, never content."""
    return {"at": stamp(time.time()), "turn": turn, "peak": peak, "window": window, "transcript": path}


def session_cli(cfg, window, model, turns, since, dry, record=None):
    """One headless session, turn by turn, until precompact.log shows a HOLD and an ALLOW (files only),
    the context stops growing, or TURNS run out. RECORD(evidence) is called after each turn."""
    work = tempfile.mkdtemp(prefix="pitstop-conformance-")
    files = max(4, int(window * 1.6) // 18_000)
    if dry:
        print(f"would write {files} filler files a turn in {work} and run up to {turns} `claude -p` turns "
              f"with CLAUDE_CODE_AUTO_COMPACT_WINDOW={window} on {model}")
        return []
    env = dict(os.environ, CLAUDE_CODE_AUTO_COMPACT_WINDOW=str(window))
    env.pop("CLAUDECODE", None)
    base = ["claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose",
            "--allowedTools", "Read", "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit"]
    sessions, last = [], 0
    for turn in range(1, turns + 1):
        first = (turn - 1) * files
        write_files(work, first, files)
        r = subprocess.run(base + (["--continue"] if turn > 1 else []) + [turn_prompt(first, files)], cwd=work, env=env,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1800)
        for line in r.stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") == "system" and e.get("subtype") == "init" and e.get("session_id"):
                if e["session_id"] not in sessions:
                    sessions.append(e["session_id"])
        paths = transcripts(cfg, since, sessions)
        peak = max((max(peaks_by_turn(p), default=0) for p in paths), default=0)
        if record:
            record(evidence_line(turn, peak, window, paths[0] if paths else None))
        if check_log(cfg, since, "HOLD", sessions)[0] and check_log(cfg, since, "ALLOW", sessions)[0]:
            break
        if turn > 1 and peak <= last:
            break                                # the context stopped growing: more turns will not fill it
        last = max(last, peak)
    shutil.rmtree(work, ignore_errors=True)
    return sessions


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="ref", help="commit or tag to install first (default: the v* tag before the newest)")
    ap.add_argument("--start", choices=("cli", "none"), default="cli")
    ap.add_argument("--since", help="with --start none: checks count lines and stamps from this UTC time")
    ap.add_argument("--base-version", help="with --start none: the Pit Stop version step 1 installed")
    ap.add_argument("--window", type=int, default=100_000)
    ap.add_argument("--turns", type=int, default=6, help="with --start cli: at most this many turns of filler")
    ap.add_argument("--evidence", help="where the turn-by-turn peaks go (JSON lines, no content)")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--wait-update", type=float, default=0, help="minutes to wait for the plugin update")
    ap.add_argument("--out", help="also write the check lines to this file")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = config_dir()
    ev_path = a.evidence or ((a.out + ".evidence.jsonl") if a.out else
                             f"conformance-evidence-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl")

    def record(ev):
        print(f"turn {ev['turn']}: peak {ev['peak']} of window {ev['window']} ({ev['transcript']})", file=sys.stderr,
              flush=True)                                  # stdout is one line per check (G16.2)
        with open(ev_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    if a.start == "none":
        since = parse_ts(a.since or "")
        if since is None or not a.base_version:
            ap.error("--start none needs --since (UTC, YYYY-MM-DDTHH:MM:SS) and --base-version")
        base, sessions = a.base_version, None
    else:
        if not shutil.which("claude"):
            print("claude is not on PATH", file=sys.stderr)
            return 2
        since = time.time()
        base, ref = install(cfg, a.ref, a.dry_run)
        print(f"installed Pit Stop {base} from {ref} at {stamp(since)}", flush=True)
        sessions = session_cli(cfg, a.window, a.model, max(1, a.turns), since, a.dry_run, record)
        if a.dry_run:
            return 0
        print(f"sessions: {' '.join(sessions) or 'none'}", flush=True)
        if not sessions:
            print("session: no session id in the output", file=sys.stderr)
            return 2
    if a.start == "none":                       # a session someone else drove: its peaks, turn by turn, from its transcript
        for p in transcripts(cfg, since, sessions):
            for i, peak in enumerate(peaks_by_turn(p), 1):
                record(evidence_line(i, peak, a.window, p))
    res = run_checks(cfg, since, base, sessions, a.window)
    deadline = time.time() + a.wait_update * 60
    while not res[-1][1] and time.time() < deadline:
        time.sleep(30)
        res = run_checks(cfg, since, base, sessions, a.window)
    lines = [format_line(*r) for r in res]
    print("\n".join(lines))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if all(r[1] for r in res):
        return 0
    if not res[0][1]:
        print(f"the kit did not fill the session past the window ({a.window}): hold and allow say nothing about "
              f"Pit Stop; the peaks are in {ev_path}", file=sys.stderr)
        return 3
    return 1


if __name__ == "__main__":
    sys.exit(main())
