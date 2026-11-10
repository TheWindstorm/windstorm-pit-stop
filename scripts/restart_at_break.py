#!/usr/bin/env python3
"""Pit Stop, part 2: the pit stop itself (fleet mode, optional, Linux/macOS + tmux).

Claude Code downloads updates by itself, but a running session keeps its old
version until it restarts, and only a /restart TYPED at its prompt does that.
This helper types into a session's tmux pane ONLY when that is cheap and
safe, so every session moves to the newest version at a break.

  restart_at_break.py                dry run: print decisions, change nothing
  restart_at_break.py --live         act (run it every 2-10 minutes from a
                                     systemd user timer, launchd or cron;
                                     /pit-stop setup installs one)
  restart_at_break.py --only NAME    look at one session only (a canary)

WHAT IT COSTS. A restart by itself is free: no turn starts. A new version
does rebuild the prompt cache from the top at the session's next turn, so the
cost of a restart grows with the context. Anything TYPED with Enter starts a
turn. So the helper restarts when the context is small or the cache is cold
anyway, and never types a note into a big session whose cache has gone cold.

A session behind the newest version is handled like this (first match):
  1. WORKING (Claude Code does not report it idle, text in the input box, a
     dialog or work on screen): skipped.
  2. BACKGROUND JOBS (Claude Code refuses /restart while they run): the helper
     leaves an ask for the session. The plugin's Stop hook hands it over at
     the end of the session's next turn; Claude sorts its jobs, writes the
     ones it will start again into a file, stops them, and goes idle. A
     session that has not loaded the plugin's hooks gets the same ask typed
     once, only while its cache is warm or its context small. Asked again
     after ask_again seconds (12 h).
  3. AT A BREAK, which is ONE of:
       (a) it COMPACTED in the last compact_window seconds (1800) and has been
           idle >= min_idle_after_compact (60): the context is as small as it
           will get;
       (b) small context (< ctx_max, 100K) and idle >= min_idle (300);
       (c) idle >= cold_idle (3600): the cache is cold anyway, and since no
           note is typed the restart costs nothing until its next turn;
       (d) it wrote its re-arm list and stopped its jobs (step 2), idle >= 60 s
     -> /restart. Then it confirms the SAME session id on the newest version.
     Only if the session listed jobs to start again does it type one note
     saying so. With no list nothing is typed.
  4. BIG AND WARM (context >= ctx_max, no compaction lately, idle between
     min_idle_before_compact (300) and 80% of cache_ttl (3600), so the cache
     still holds): the helper types /compact. Summarizing from a warm cache is
     cheap, and the next run finds the session at break (a) and restarts it on
     a small context. compact_before_restart: false switches this off; then
     big sessions wait for their own compaction or for (c).
  5. Anything else: skipped, with the reason in the log.
A session on the newest version is left alone, unless idle_compact_after is
set (seconds, off by default): then a quiet session over its idle point (from
/pit-stop optimize) gets /compact typed while its cache is warm. Claude Code
does this by itself shortly before the cache expires (about 55 minutes with
the 1-hour cache); this is for fleets whose sessions are woken more often
than that and so never get there.

Typing is checked: the helper presses Enter only if the input box holds
exactly what it typed; otherwise it clears the line and backs off that
session (30 min, doubling, at most 12 h). At most max_per_run restarts and
max_per_run compactions per run (default 1 each). After a /restart (done,
refused or not confirmed) a session is left alone for restart_cooldown
seconds (6 h). A session that stopped its jobs for a restart the helper then
could not do is told, after consent_timeout (30 min), to start them again.

Config (optional) ~/.claude/pit-stop/restart.json:
  {"tmux_socket": null,            # tmux -L name; null = default server
   "allow_names": [],              # if set, ONLY these session names
   "deny_names": [], "deny_name_globs": [], "deny_paths": [],
   "ctx_max": 100000, "min_idle": 300, "max_per_run": 1,
   "compact_window": 1800, "min_idle_after_compact": 60, "cold_idle": 3600,
   "compact_before_restart": true, "min_idle_before_compact": 300,
   "cache_ttl": 3600, "compact_retry": 21600, "idle_compact_after": null,
   "ask_again": 43200, "ask_fallback_after": 1800, "consent_timeout": 1800,
   "restart_cooldown": 21600,
   "mismatch_backoff": 1800, "mismatch_backoff_max": 43200,
   "notify_command": null}         # e.g. ["notify-send", "pit-stop"]; gets the message as last arg
Sessions come from Claude Code's local registry (~/.claude/sessions/*.json,
not a public interface); if it gives nothing usable the documented
`claude agents --json` listing is used. A session it cannot read is skipped.
"""
import calendar
import fnmatch
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402

LIVE = "--live" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv[:-1] else None
CFG = {"tmux_socket": None, "allow_names": [], "deny_names": [], "deny_name_globs": [], "deny_paths": [],
       "ctx_max": 100000, "min_idle": 300, "max_per_run": 1, "notify_command": None,
       "compact_window": 1800, "min_idle_after_compact": 60, "cold_idle": 3600,
       "compact_before_restart": True, "min_idle_before_compact": 300, "cache_ttl": 3600,
       "compact_retry": 21600, "idle_compact_after": None,
       "ask_again": 43200, "ask_fallback_after": 1800, "consent_timeout": 1800, "restart_cooldown": 21600,
       "mismatch_backoff": 1800, "mismatch_backoff_max": 43200}
CFG.update(pc.read_json(os.path.join(pc.STATE_DIR, "restart.json"), {}) or {})
BASE = os.path.join(pc.STATE_DIR, "restart")
for d in ("state", "ok"):
    os.makedirs(os.path.join(BASE, d), exist_ok=True)
LOG = os.path.join(BASE, "restart.log")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DIM_SEG = re.compile(r"\x1b\[2m.*?(?=\x1b\[(?:0|22)?m|$)")
PROMPT = "❯"
WARM_SHARE = 0.8        # the cache counts as warm for this share of cache_ttl
COMPACT_WAIT = 600      # after typing /compact, give the summary this long before doing anything else


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    """Print the line; a live run also keeps it in restart.log (a dry run writes nothing)."""
    print(f"{ts()} {msg}")
    if not LIVE:
        return
    if os.path.exists(LOG) and os.path.getsize(LOG) > 5_000_000:
        os.replace(LOG, LOG + ".1")  # keep one old log; never grow without bound
    with open(LOG, "a") as f:
        f.write(f"{ts()} {msg}\n")


def tmux(*args):
    base = ["tmux"] + (["-L", CFG["tmux_socket"]] if CFG["tmux_socket"] else [])
    return subprocess.run(base + list(args), capture_output=True, text=True, timeout=15)


def panes():
    r = tmux("list-panes", "-a", "-F", "#{pane_tty}\t#{session_name}:#{window_index}.#{pane_index}")
    return dict(l.split("\t") for l in r.stdout.splitlines() if "\t" in l)


def pid_tty(pid):
    try:
        return os.readlink(f"/proc/{pid}/fd/0")
    except OSError:
        r = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True)
        t = r.stdout.strip()
        return f"/dev/{t}" if t and t != "??" else None


def screen(target, escapes=False):
    args = ["capture-pane", "-p", "-t", target] + (["-e"] if escapes else [])
    rows = [l for l in tmux(*args).stdout.splitlines() if ANSI.sub("", l).strip()]
    return rows[-40:]


def input_box_text(rows):
    """The text in the input box: from the last prompt row down to the box's
    bottom border, dimmed suggestion text removed, as one string."""
    rows = [ANSI.sub("", DIM_SEG.sub("", l.replace("\u00a0", " ").replace("\u202f", " "))) for l in rows]
    starts = [i for i, l in enumerate(rows) if l.startswith(PROMPT)]
    if not starts:
        return None
    out = []
    for l in rows[starts[-1]:]:
        if out and re.fullmatch(r"\s*[\u2500\u2501-]{10,}.*", l):
            break
        out.append(l)
    return " ".join(out)[len(PROMPT):].strip()


def same_text(a, b):
    """Equal once wrapping and spacing are ignored (the pane wraps long lines)."""
    return a is not None and "".join(a.split()) == "".join(b.split())


def input_empty(target):
    return input_box_text(screen(target, escapes=True)) == ""


def screen_busy(target):
    txt = "\n".join(screen(target))
    return bool(re.search(r"esc to interrupt|Do you want|" + PROMPT + r" \d+\.|Enter to confirm|\(y/n\)", txt))


def type_line(target, text):
    """Type TEXT and press Enter. Returns (sent, why): why is "busy" when the box
    was not empty or a dialog showed, "mismatch" when the box did not hold
    exactly our text after typing (then it is cleared and no Enter is pressed)."""
    if not input_empty(target) or screen_busy(target):
        log(f"{target}: input not empty or busy at send time; skipped")
        return False, "busy"
    if not LIVE:
        log(f"DRY would type into {target}: {text[:90]}")
        return True, ""
    tmux("send-keys", "-t", target, "-l", text)
    time.sleep(1)
    # Someone may have typed in that second: press Enter only if the box holds
    # EXACTLY our text; otherwise clear the line and skip.
    got = input_box_text(screen(target, escapes=True))
    if not same_text(got, text):
        tmux("send-keys", "-t", target, "C-u")
        read = "no input box found" if got is None else f"read {len(''.join(got.split()))} chars"
        log(f"{target}: input box did not match the typed text ({read}, typed {len(''.join(text.split()))}); "
            f"cleared it, skipped, no Enter")
        return False, "mismatch"
    tmux("send-keys", "-t", target, "Enter")
    return True, ""


def mismatch_wait(n, cfg):
    """Seconds to leave a session alone after N typing mismatches in a row."""
    return min(cfg["mismatch_backoff"] * 2 ** max(n - 1, 0), cfg["mismatch_backoff_max"])


def note_typing(mmf, sent, why, now):
    """Keep the mismatch count for one session: +1 on a mismatch, cleared on a send."""
    if not LIVE:
        return
    if sent:
        if os.path.exists(mmf):
            os.remove(mmf)
    elif why == "mismatch":
        n = (pc.read_json(mmf, {}) or {}).get("n", 0) + 1
        with open(mmf, "w") as f:
            json.dump({"n": n, "at": now}, f)


def notify(msg):
    if CFG["notify_command"] and LIVE:
        subprocess.run(list(CFG["notify_command"]) + [msg], timeout=30)


def transcript_path(s):
    return os.path.join(pc.project_dir(s.get("cwd") or ""), (s.get("sessionId") or "") + ".jsonl")


def ctx_and_idle(s):
    p = transcript_path(s)
    if not os.path.exists(p):
        return None, -1
    idle = time.time() - os.path.getmtime(p)
    ctx = None
    for e in pc.iter_entries(p, tail_bytes=3_000_000):
        if pc.is_compact_boundary(e):
            ctx = 0
        elif e.get("type") == "assistant" and not e.get("isSidechain") and pc.usage_total(e):
            ctx = pc.usage_total(e)
    return ctx, idle


def utc_ts(s):
    """'2026-11-10T12:38:07.398Z' -> epoch seconds, as UTC (no local-time or DST guess)."""
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def last_compact_age(path, tail_bytes=64_000_000, now=None):
    """Seconds since the transcript's last compaction, or None. Scans the raw
    bytes for the marker first (big transcripts), so only a few lines are parsed."""
    if not path or not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > tail_bytes:
            f.seek(size - tail_bytes)
            f.readline()
        last = None
        for raw in f:
            if b"compact_boundary" not in raw:
                continue
            try:
                e = json.loads(raw)
            except Exception:
                continue
            if pc.is_compact_boundary(e):
                t = utc_ts(e.get("timestamp"))
                if t:
                    last = t
    return None if last is None else (now or time.time()) - last


def at_a_break(status, idle, ctx, compact_age, cfg, consented=False):
    """(True, why) when a restart is cheap and safe by the rules in the docstring,
    else (False, the reason it is not). Pure: no reads, so it is easy to test."""
    if status not in ("idle", "shell"):
        return False, f"status={status} (working)"
    if idle < 0:
        return False, "no transcript found"
    if compact_age is not None and compact_age <= cfg["compact_window"]:
        if idle >= cfg["min_idle_after_compact"]:
            return True, f"compacted {int(compact_age)}s ago, idle {int(idle)}s"
        return False, f"compacted {int(compact_age)}s ago; idle {int(idle)}s < {cfg['min_idle_after_compact']}s"
    if consented:
        if idle >= cfg["min_idle_after_compact"]:
            return True, f"it wrote its re-arm list and stopped its jobs, idle {int(idle)}s"
        return False, f"it wrote its re-arm list; idle {int(idle)}s < {cfg['min_idle_after_compact']}s"
    if ctx is not None and ctx < cfg["ctx_max"] and idle >= cfg["min_idle"]:
        return True, f"small context {ctx}, idle {int(idle)}s"
    if idle >= cfg["cold_idle"]:
        return True, f"idle {int(idle)}s >= cold_idle (cache cold anyway)"
    if ctx is None:
        return False, "context unreadable"
    return False, (f"idle {int(idle)}s, context {ctx}"
                   f" (needs: a compaction in the last {cfg['compact_window']}s, or context < {cfg['ctx_max']}"
                   f" and idle >= {cfg['min_idle']}s, or idle >= {cfg['cold_idle']}s)")


def cache_warm(idle, cfg):
    return 0 <= idle <= cfg["cache_ttl"] * WARM_SHARE


def plan(l, cfg):
    """What to do with one session. Pure, so it is easy to test.

    l: status, idle, ctx, compact_age, behind, consented (its re-arm list
    exists), consent_age, compact_ask_age (seconds since /compact was typed,
    or None), idle_point, blocked (why the helper may not restart it for now:
    a restart was tried lately, or typing keeps failing; None otherwise).
    Returns (action, why); action is one of
      restart   type /restart
      compact   type /compact (a restart or nothing follows on a later run)
      ask       it has background jobs: leave the ask (the caller picks the channel)
      wait      a step is in progress; do nothing this run
      rearm     it stopped its jobs for a restart that never came: tell it to start them again
      skip      not now (why says what is missing)
      none      up to date and nothing to do
    """
    st, idle, ctx, cage = l["status"], l["idle"], l["ctx"], l["compact_age"]
    recent = cage is not None and cage <= cfg["compact_window"]
    asked = l.get("compact_ask_age")
    if st not in ("idle", "shell"):
        return ("skip" if l["behind"] else "none"), f"status={st} (working)"
    if idle < 0:
        return ("skip" if l["behind"] else "none"), "no transcript found"
    compact_ok = (st == "idle" and ctx is not None and not recent and cache_warm(idle, cfg)
                  and (asked is None or asked >= cfg["compact_retry"]))
    if not l["behind"]:
        after, point = cfg.get("idle_compact_after"), l.get("idle_point")
        if after and point and compact_ok and ctx >= point and idle >= after:
            return "compact", f"quiet {int(idle)}s with context {ctx} over its idle point {point}"
        return "none", ""
    if st == "shell":
        if l["consented"]:
            return "wait", "re-arm list written; waiting for it to stop its background jobs"
        return "ask", "background jobs running"
    if l.get("blocked"):
        # The helper cannot restart it for now (a restart was just tried, or typing keeps failing).
        if l["consented"] and l.get("consent_age", 0) >= cfg["consent_timeout"]:
            return "rearm", f"it stopped its jobs {int(l['consent_age'])}s ago and no restart came ({l['blocked']})"
        return "skip", l["blocked"]
    if asked is not None and asked < COMPACT_WAIT and not recent:
        return "wait", f"/compact typed {int(asked)}s ago; waiting for the summary"
    ok, why = at_a_break(st, idle, ctx, cage, cfg, l["consented"])
    if (cfg["compact_before_restart"] and compact_ok and ctx >= cfg["ctx_max"]
            and idle >= (cfg["min_idle_after_compact"] if l["consented"] else cfg["min_idle_before_compact"])):
        return "compact", f"context {ctx}, idle {int(idle)}s, cache still warm: compact first, restart after"
    if ok:
        return "restart", why
    return "skip", why


def allowed(name, cwd, cfg):
    if ONLY is not None:
        return name == ONLY
    if cfg["allow_names"] and name not in cfg["allow_names"]:
        return False
    if name in cfg["deny_names"] or any(fnmatch.fnmatchcase(name, g) for g in cfg["deny_name_globs"]):
        return False
    return not any(cwd.startswith(os.path.expanduser(p)) for p in cfg["deny_paths"])


def age_of(path, now):
    """Seconds since the time written in a state file, or None."""
    try:
        with open(path) as f:
            return now - float(f.read().strip())
    except Exception:
        return None


def stamp(path, now):
    with open(path, "w") as f:
        f.write(str(now))


def ask_note(newest, ver, okf):
    return (f"[AUTOMATED pit-stop restart, not your user] Claude Code {newest} is installed; you run {ver}. "
            f"Claude Code will not /restart while background jobs run. Sort your background jobs: (A) watchers, "
            f"monitors and loops you can simply start again; (B) real work in progress (a build, a test run, a deploy). "
            f"If there is any B, do nothing: you will be asked again later. If there is only A: (1) write one line per "
            f"job you will start again into {okf} (an empty file if none); (2) stop those jobs yourself. The helper "
            f"restarts you within a few minutes once you are idle with no jobs, and tells you to start them again. "
            f"No reply needed.")


def handle_ask(s, key, target, ver, newest, okf, l, now, can_type=True):
    """Background jobs: leave the ask for the session's Stop hook; type it only
    for a session that has not loaded the hook, and only when that is cheap."""
    sid = pc.safe_id(s.get("sessionId"))
    name = s["name"]
    askf = os.path.join(pc.ASKS, sid + ".json")
    typedf = os.path.join(BASE, "state", key + ".asked")
    a = pc.read_json(askf, None)
    a = a if isinstance(a, dict) else None
    if a and (a.get("newest") != newest or now - float(a.get("at") or 0) >= CFG["ask_again"]):
        a = None  # a newer version, or the last ask is old: ask afresh
    if a is None:
        if LIVE:
            pc.write_json_atomic(askf, {"newest": newest, "ver": ver, "okf": okf, "at": now, "delivered": None})
        log(f"{name}: {ver}->{newest} background jobs: ask left for its Stop hook" + ("" if LIVE else " (DRY)"))
        return
    if a.get("delivered"):
        log(f"{name}: {ver}->{newest} skip: asked by its Stop hook {int(now - float(a['delivered']))}s ago; "
            f"its jobs still run (asks again after {CFG['ask_again']}s)")
        return
    if os.path.exists(os.path.join(pc.SEEN, sid)):
        log(f"{name}: {ver}->{newest} skip: ask waits for the end of its next turn (its Stop hook is loaded)")
        return
    if not can_type:
        log(f"{name}: {ver}->{newest} skip: ask left; not typing it now ({l.get('blocked')})")
        return
    typed_age = age_of(typedf, now)
    if typed_age is not None and typed_age < CFG["ask_again"]:
        log(f"{name}: {ver}->{newest} skip: ask typed {int(typed_age)}s ago; its jobs still run")
        return
    if now - float(a.get("at") or now) < CFG["ask_fallback_after"]:
        log(f"{name}: {ver}->{newest} skip: ask left {int(now - float(a['at']))}s ago; typing it only after "
            f"{CFG['ask_fallback_after']}s (this session has not loaded the plugin's hooks)")
        return
    small = l["ctx"] is not None and l["ctx"] < CFG["ctx_max"]
    if not (small or cache_warm(l["idle"], CFG)) or l["idle"] < CFG["min_idle"]:
        log(f"{name}: {ver}->{newest} skip: ask not typed now (idle {int(l['idle'])}s, context {l['ctx']}: a typed "
            f"note starts a turn, so only while the cache is warm or the context small, and after {CFG['min_idle']}s idle)")
        return
    if not input_empty(target) or screen_busy(target):
        log(f"{name}: {ver}->{newest} skip: text in the input box or a dialog on screen")
        return
    sent, how = type_line(target, ask_note(newest, ver, okf))
    note_typing(os.path.join(BASE, "state", key + ".mismatch"), sent, how, now)
    if sent and LIVE:
        stamp(typedf, now)


def confirm_restart(s, newest):
    """True once the SAME session id runs the newest version (waits up to 2 minutes)."""
    for _ in range(24):
        time.sleep(5)
        d = pc.registry_sessions().get(s.get("sessionId")) or {}
        if d.get("pid") and (pc.pid_version(d["pid"]) or d.get("version")) == newest:
            return True
        for x in (pc.cli_sessions(timeout=20) or []) if not d else []:
            if x.get("sessionId") == s.get("sessionId") and pc.pid_version(x.get("pid")) == newest:
                return True
    return False


def rearm_list(okf):
    """The jobs a session said it will start again (the non-empty lines of its file), or []."""
    try:
        with open(okf) as f:
            return [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return []


def main():
    newest = pc.newest_installed_version()
    if not newest:
        log("no installed versions found; exit")
        return
    sessions, source = pc.live_sessions()
    if not sessions:
        log("no running sessions found (registry and `claude agents --json` both empty); exit")
        return
    tty_map, now = panes(), time.time()
    restarts = compacts = 0
    for s in sorted(sessions, key=lambda x: x["name"]):
        pid, name, cwd = s.get("pid"), s["name"], s.get("cwd") or ""
        target = tty_map.get(pid_tty(pid) or "")
        if not target or not name or not allowed(name, cwd, CFG):
            continue
        ver = s.get("version")
        if not ver:
            continue
        behind = pc.vkey(ver) < pc.vkey(newest)
        if not behind and not CFG.get("idle_compact_after"):
            continue
        tag = f"{name}: {ver}->{newest}" if behind else f"{name}:"
        key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        st_dir = os.path.join(BASE, "state")
        lastf, mmf = os.path.join(st_dir, key + ".last"), os.path.join(st_dir, key + ".mismatch")
        compf, okf = os.path.join(st_dir, key + ".compact"), os.path.join(BASE, "ok", key)
        last_age = age_of(lastf, now)
        blocked = None
        if behind and last_age is not None and last_age < CFG["restart_cooldown"]:
            blocked = f"a restart was tried by this helper {int(last_age)}s ago (waits {CFG['restart_cooldown']}s)"
        mm = pc.read_json(mmf, {}) or {}
        if not blocked and mm.get("n"):
            left = mismatch_wait(mm["n"], CFG) - (now - float(mm.get("at", 0)))
            if left > 0:
                blocked = (f"typed text did not match {mm['n']} time(s) in a row; backing off, "
                           f"next try in {int(left)}s")
        status = s.get("status")
        ctx, idle = ctx_and_idle(s)
        if s.get("statusUpdatedAt") and idle >= 0:
            idle = min(now - s["statusUpdatedAt"] / 1000, idle)
        consented = os.path.exists(okf)
        consent_age = now - os.path.getmtime(okf) if consented else 0
        if consented and status == "shell" and consent_age >= CFG["ask_again"]:
            if LIVE:
                os.replace(okf, os.path.join(st_dir, f"{key}.stale-{int(now)}"))
            consented = False  # it never stopped its jobs: the list is stale, ask again
        l = {"status": status, "idle": idle, "ctx": ctx, "behind": behind,
             "compact_age": last_compact_age(transcript_path(s), now=now),
             "consented": consented, "consent_age": consent_age, "compact_ask_age": age_of(compf, now),
             "idle_point": pc.points(cwd, s.get("sessionId"))["idle_point"], "blocked": blocked}
        if blocked and not behind:
            continue  # a typing backoff also covers the optional idle compaction
        action, why = plan(l, CFG)
        if action == "none":
            continue
        if action in ("skip", "wait"):
            log(f"{tag} {action}: {why}")
            continue
        if action == "ask":
            handle_ask(s, key, target, ver, newest, okf, l, now, can_type=not blocked)
            continue
        if not input_empty(target):
            log(f"{tag} skip: text in the input box")
            continue
        if screen_busy(target):
            log(f"{tag} skip: a dialog or work on screen")
            continue
        if action == "rearm":
            log(f"{tag} {why}: telling it to start its jobs again")
            jobs = rearm_list(okf)
            sent, how = type_line(target, "[AUTOMATED pit-stop restart, not your user] The restart you stopped your "
                                  "background jobs for did not happen. Start them again now"
                                  + (f" (your list: {'; '.join(jobs)[:600]})" if jobs else "") + ". No reply needed.")
            note_typing(mmf, sent, how, now)
            if sent and LIVE:
                os.replace(okf, os.path.join(st_dir, f"{key}.gave-up-{int(now)}"))
            continue
        if action == "compact":
            if compacts >= CFG["max_per_run"]:
                log(f"{tag} run limit reached for /compact; next run")
                continue
            log(f"{tag} /compact: {why}" + ("" if LIVE else " (DRY)"))
            sent, how = type_line(target, "/compact")
            note_typing(mmf, sent, how, now)
            if sent:
                compacts += 1
                if LIVE:
                    stamp(compf, now)
            continue
        # action == "restart"
        log(f"{tag} at a break: {why}")
        if restarts >= CFG["max_per_run"]:
            log(f"{name}: run limit reached; next run")
            continue
        if not LIVE:
            log(f"DRY would /restart {name} ({ver} -> {newest}, ctx {ctx})")
            restarts += 1
            continue
        sent, how = type_line(target, "/restart")
        note_typing(mmf, sent, how, now)
        if not sent:
            continue
        restarts += 1
        time.sleep(6)
        stamp(lastf, now)  # tried: whatever happens next, leave it alone for restart_cooldown
        if any("Can't restart" in r for r in screen(target)):
            log(f"{name}: /restart refused by Claude Code (see its pane); next try after {CFG['restart_cooldown']}s")
            notify(f"pit-stop: {name} refused /restart; see its pane")
            continue
        if not confirm_restart(s, newest):
            log(f"{name}: RESTART NOT CONFIRMED on {newest} within 2 minutes")
            notify(f"pit-stop: {name} /restart not confirmed; check its pane")
            continue
        jobs = rearm_list(okf)
        log(f"{name}: RESTARTED {ver} -> {newest}, same session"
            + (f"; {len(jobs)} job(s) to start again" if jobs else "; nothing typed after (no re-arm list)"))
        if os.path.exists(okf):
            os.replace(okf, okf + ".done")
        if jobs:  # only now is a note worth a turn: its watchers are down until it starts them
            time.sleep(10)
            type_line(target, f"[AUTOMATED pit-stop restart, not your user] You were restarted at {ts()} onto Claude "
                              f"Code {newest} (was {ver}); same conversation. The background jobs you stopped for it "
                              f"are down: start them again now (your list is in {okf}.done). Scheduled session crons "
                              f"survive a restart. No reply needed.")
        for f in (compf, os.path.join(pc.ASKS, pc.safe_id(s.get("sessionId")) + ".json"),
                  os.path.join(st_dir, key + ".asked")):
            if os.path.exists(f):
                os.remove(f)


if __name__ == "__main__":
    main()
