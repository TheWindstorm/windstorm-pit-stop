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
THE QUIET-SESSION PIT STOP (idle_compact_after, seconds, off by default; 2400
is a good value). A session that has been quiet that long, over its idle
point (from /pit-stop optimize; else Claude Code's default, 200K), with its
cache still warm, gets /compact typed, whether or not it is on the newest
version and whether or not it has background jobs. Claude Code does this by
itself shortly before the cache expires (about 53 minutes with the 1-hour
cache), but on the fleet this was measured on it reached only about a third
of the big quiet sessions: in 7 days 422 wake-ups of 200K or more paid for
the whole context again. Otherwise a session on the newest version is left
alone.

Typing is checked: the helper presses Enter only if the input box holds
exactly what it typed; otherwise it clears the line and backs off that
session (30 min, doubling, at most 12 h). At most max_per_run restarts and
max_per_run compactions per run (default 1 each). After a /restart (done,
refused or not confirmed) a session is left alone for restart_cooldown
seconds (6 h). A session that stopped its jobs for a restart the helper then
could not do is told, after consent_timeout (30 min), to start them again.

RELOAD (reload_plugins: true; off by default). A session loads a plugin's
hooks when its process starts, and a session already on the newest Claude
Code is never restarted. The helper types /reload-plugins (a local command:
no turn starts) into a session whose Pit Stop hooks are MISSING or WRONG: it
started before the plugin was installed and has none; or the version folder
its hooks run from has been removed; or the plugin's hook generation
(hooks/generation.json, raised only when a running session must reload) is
not the installed one. A session that merely runs an older release's hooks
of the same generation is left alone. It is typed at a break, when that is
cheap: right after a compaction, on a small context, or when the cache has
gone cold. A reload can make the session's next turn write its context to the
prompt cache again, so a big session with a warm cache waits (reload_warm:
true lifts that). reload_only_names, if set, limits it to those names (for a
staged roll-out). It is confirmed by the "Reloaded:" line on screen; at most
max_per_run per run; a try is not repeated for reload_retry seconds (6 h).
A session the helper restarts in the same run is not also reloaded.

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
   "jobs_escalate_days": 3,        # behind this many days because background jobs never stop: the ask says to stop
                                   # ALL of them at the next point where nothing is lost, real work too (0 = never)
   "plugin_updates": "off",        # "pit-stop": keep this plugin up to date; "all": every plugin installed for the
                                   # user; with Claude Code's own commands, before restarting anyone (see update_plugins)
   "plugin_update_every": 86400,
   "reload_plugins": false, "reload_only_names": [], "reload_warm": false,
   "reload_retry": 21600,
   "needs_you": true,              # keep ~/.claude/pit-stop/needs-you.md (hourly) and tell notify_command when it changes
   "fleet_tune": true,             # when tuning is on (config.json "auto_tune"): tune the folder of every
                                   # running session the helper may touch once a day ("tune_every_days"), one folder per run, in the
                                   # background, and not while the machine is busy (see start_tune)
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
ONLY = None
if "--only" in sys.argv:
    _i = sys.argv.index("--only")
    ONLY = sys.argv[_i + 1].strip() if _i + 1 < len(sys.argv) else ""
    if not ONLY or ONLY.startswith("--"):
        sys.exit("--only needs a session name (nothing was done)")  # never fall back to the whole fleet
CFG = {"tmux_socket": None, "allow_names": [], "deny_names": [], "deny_name_globs": [], "deny_paths": [],
       "ctx_max": 100000, "min_idle": 300, "max_per_run": 1, "notify_command": None,
       "compact_window": 1800, "min_idle_after_compact": 60, "cold_idle": 3600,
       "compact_before_restart": True, "min_idle_before_compact": 300, "cache_ttl": 3600,
       "compact_retry": 21600, "idle_compact_after": None,
       "ask_again": 43200, "ask_fallback_after": 1800, "consent_timeout": 1800, "restart_cooldown": 21600,
       "jobs_escalate_days": 3, "plugin_updates": "off", "plugin_update_every": 86400,
       "reload_plugins": False, "reload_only_names": [], "reload_warm": False, "reload_retry": 21600,
       "needs_you": True, "fleet_tune": True,
       "mismatch_backoff": 1800, "mismatch_backoff_max": 43200}
_DEFAULTS = dict(CFG)
_user = pc.read_json(os.path.join(pc.STATE_DIR, "restart.json"), {})
CFG.update(_user if isinstance(_user, dict) else {})
CFG_WARNINGS = []


def _check_numbers(cfg, defaults, warnings):
    """A number in restart.json must be a number. `true` would count as 1 second
    and "2400" would raise on every session, so a value of the wrong kind goes
    back to its default (for idle_compact_after: off) and the run says so."""
    for k, dv in list(defaults.items()) + [("idle_compact_after", None)]:
        if k != "idle_compact_after" and (isinstance(dv, bool) or not isinstance(dv, (int, float))):
            continue
        v = cfg.get(k)
        if (v is None and dv is None) or (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
            continue
        warnings.append(f"restart.json: \"{k}\" must be a number of seconds or tokens (found {v!r}); using {dv!r}")
        cfg[k] = dv


_check_numbers(CFG, _DEFAULTS, CFG_WARNINGS)
BASE = os.path.join(pc.STATE_DIR, "restart")
LOG = os.path.join(BASE, "restart.log")
EVENTS = os.path.join(BASE, "events.log")   # only what was done, typed, refused or asked: small, so it lasts
QUIET = (" skip: ", " wait: ", " at a break: ", "run limit reached", "--not-before: ")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DIM_SEG = re.compile(r"\x1b\[2m.*?(?=\x1b\[(?:0|22)?m|$)")
PROMPT = "❯"
WARM_SHARE = 0.8        # the cache counts as warm for this share of cache_ttl
COMPACT_WAIT = 600      # after typing /compact, give the summary this long before doing anything else
NOTE_MAX = 560          # the longest note ever typed: Claude Code folds longer typed text into a "[Pasted text]"
                        # block, the box then no longer shows our text, and the note is (safely) never sent
KEY_NAME_MAX = 40       # letters of a session's name kept in its state key (the key is in a path that is typed)
REARM_MAX_AGE = 7200    # a re-arm list older than this is history, not a session waiting to be told
DIALOG = re.compile(r"Do you want|" + PROMPT + r" \d+\.|Enter to confirm|\(y/n\)")


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    """Print the line; a live run also keeps it in restart.log (a dry run writes nothing)."""
    print(f"{ts()} {msg}")
    if not LIVE:
        return
    os.makedirs(BASE, exist_ok=True)
    if os.path.exists(LOG) and os.path.getsize(LOG) > 5_000_000:
        os.replace(LOG, LOG + ".1")  # keep one old log; never grow without bound
    with open(LOG, "a") as f:
        f.write(f"{ts()} {msg}\n")
    # restart.log gets one line per session every run (about 20,000 lines in 31
    # hours on a 95-session machine), so it rotates within days and the
    # restarts scroll away with it. Everything that is not a skip is kept here too.
    if not any(q in msg for q in QUIET):
        pc.append_log(EVENTS, msg)


TYPED = set()   # panes typed into during this run: never two commands into one session in one run
PLANNED = set()  # panes the restart path wants this run (a restart, a compaction, a note), typed or waiting its turn


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


def rows_busy(rows):
    """True when the bottom of the screen shows work in progress or a dialog.
    Only the rows around the input box count: a question in Claude's last reply
    ("Do you want me to...?") further up is not a dialog. Pure, for tests."""
    rows = [ANSI.sub("", l.replace("\u00a0", " ").replace("\u202f", " ")) for l in rows]
    starts = [i for i, l in enumerate(rows) if l.startswith(PROMPT)]
    if not starts:  # no input box at all: something else owns the screen
        tail = "\n".join(rows[-15:])
        return "esc to interrupt" in tail or bool(DIALOG.search(tail))
    i = starts[-1]
    return "esc to interrupt" in "\n".join(rows[max(0, i - 4):]) or bool(DIALOG.search("\n".join(rows[i:])))


def pane_in_mode(target):
    """True when the pane is in tmux copy mode: the screen reads as usual but keys would go to tmux."""
    return tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() not in ("", "0")


def screen_busy(target):
    return pane_in_mode(target) or rows_busy(screen(target))


def type_line(target, text):
    """Type TEXT and press Enter. Returns (sent, why): why is "busy" when the box
    was not empty or a dialog showed, "mismatch" when the box did not hold
    exactly our text after typing (then it is cleared and no Enter is pressed),
    "too-long" when TEXT is over NOTE_MAX (then nothing is typed at all)."""
    if len(text) > NOTE_MAX:
        log(f"{target}: a note of {len(text)} chars is over the {NOTE_MAX} that Claude Code shows as typed text; not typed")
        return False, "too-long"
    if not input_empty(target) or screen_busy(target):
        log(f"{target}: input not empty or busy at send time; skipped")
        return False, "busy"
    TYPED.add(target)   # typed into (or would be, in a dry run) during this run
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


CALL_AGE = {}   # session id -> seconds since its last model call, filled by ctx_and_idle (None if no call was seen)


def call_age_of(s, idle):
    """Seconds since this session's last MODEL CALL: what the prompt cache's
    age follows. Quiet time (idle) is not the same thing: /restart,
    /reload-plugins, /model and the away summary all write dated rows without
    calling the model, so a session can look quiet for 20 minutes while its
    cache has been cold for hours. Falls back to idle when the transcript read
    saw no call (and in tests that replace ctx_and_idle)."""
    v = CALL_AGE.get(s.get("sessionId"))
    return idle if v is None else max(v, idle)


def ctx_and_idle(s):
    p = transcript_path(s)
    if not os.path.exists(p):
        return None, -1
    # Idle is counted from the newest entry that carries a time, not from the
    # file's modification time: on a quiet lane the file's time was seen moving
    # about once an hour with nothing appended (Veron, 2026-11-11), which reset
    # the idle clock and kept the hour-idle rule from ever applying.
    ctx, newest, last_call = None, None, None
    for e in pc.iter_entries(p, tail_bytes=3_000_000):
        t = utc_ts(e.get("timestamp") or "")
        if t and (newest is None or t > newest):
            newest = t
        if pc.is_compact_boundary(e):
            ctx = 0
            last_call = t or last_call      # a compaction is a model call too: it leaves a fresh cache entry
        elif e.get("type") == "assistant" and not e.get("isSidechain") and pc.usage_total(e):
            ctx = pc.usage_total(e)
            last_call = t or last_call
    now = time.time()
    idle = now - newest if newest is not None else now - os.path.getmtime(p)
    CALL_AGE[s.get("sessionId")] = None if last_call is None else max(0.0, now - last_call)
    return ctx, max(0.0, idle)


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


def at_a_break(status, idle, ctx, compact_age, cfg, consented=False, call_age=None):
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
    # (c) is about the cache: it is cold once the last MODEL CALL is cold_idle old. A dated row that is
    # not a call (a /reload-plugins, a /model) must not start that hour again; the session only has to
    # have been left alone for min_idle.
    cold = idle if call_age is None else call_age
    if cold >= cfg["cold_idle"] and idle >= min(cfg["min_idle"], cfg["cold_idle"]):
        return True, f"idle {int(cold)}s >= cold_idle (cache cold anyway)"
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
    exists and answers an ask), consent_age, compact_ask_age (seconds since
    /compact was typed, or None), idle_point, blocked (why the helper may not
    restart it for now: a restart was tried lately; None otherwise).
    Returns (action, why); action is one of
      restart   type /restart
      compact   type /compact (a restart or nothing follows on a later run)
      ask       it has background jobs: leave the ask (the caller picks the channel)
      wait      a step is in progress; do nothing this run
      rearm     it stopped jobs for a restart that is not coming: tell it to start them again
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
    call_age = l.get("call_age")
    call_age = idle if call_age is None else call_age       # tests and older callers give only idle
    compact_ok = (st == "idle" and ctx is not None and not recent and cache_warm(call_age, cfg)
                  and (asked is None or asked >= cfg["compact_retry"]))
    # The quiet-session pit stop (idle_compact_after): a session that has been
    # quiet that long, over its idle point, cache still warm, gets /compact now,
    # so its next wake-up does not pay for the whole context at the cache-write
    # price. Background jobs do not matter for a compaction (they do for a
    # restart), so status "shell" counts too. Without a tuned idle point,
    # Claude Code's own default is used.
    after, point = cfg.get("idle_compact_after"), l.get("idle_point") or pc.IDLE_DEFAULT
    # "Warm" is the age of the last model call (call_age); "quiet" is the newest dated row (idle). Both
    # must hold: a session restarted or reloaded 40 minutes ago whose last call is hours old is quiet
    # but cold, and summarizing it now would read the whole context uncached.
    quiet_stop = bool(after and ctx is not None and not recent and cache_warm(call_age, cfg)
                      and (asked is None or asked >= cfg["compact_retry"]) and ctx >= point and idle >= after)
    quiet_why = (f"quiet {int(idle)}s with context {ctx} over its idle point {point}, "
                 f"last model call {int(call_age)}s ago so the cache is still warm")
    if not l["behind"]:
        return ("compact", quiet_why) if quiet_stop else ("none", "")
    given_up = l["consented"] and l.get("consent_age", 0) >= cfg["consent_timeout"]
    if l.get("blocked"):
        # No restart for now (one was tried lately). Never ask it to stop its jobs for a restart
        # that cannot come; and if it already stopped them, do not leave it waiting.
        if given_up:
            return "rearm", f"it listed its jobs {int(l['consent_age'])}s ago and no restart is coming ({l['blocked']})"
        return "skip", l["blocked"]
    if st == "shell":
        if given_up:
            return "rearm", (f"it listed its jobs {int(l['consent_age'])}s ago but background work still runs, "
                             f"so it cannot be restarted")
        if l["consented"]:
            return "wait", "re-arm list written; waiting for it to stop its background jobs"
        if quiet_stop:
            return "compact", quiet_why + " (it cannot be restarted while its background jobs run)"
        return "ask", "background jobs running"
    if asked is not None and asked < COMPACT_WAIT and not recent:
        return "wait", f"/compact typed {int(asked)}s ago; waiting for the summary"
    ok, why = at_a_break(st, idle, ctx, cage, cfg, l["consented"], call_age)
    if (cfg["compact_before_restart"] and compact_ok and ctx >= cfg["ctx_max"]
            and idle >= (cfg["min_idle_after_compact"] if l["consented"] else cfg["min_idle_before_compact"])):
        return "compact", f"context {ctx}, idle {int(idle)}s, cache still warm: compact first, restart after"
    if ok:
        return "restart", why
    if quiet_stop:
        return "compact", quiet_why + "; the restart follows once it is at a break"
    return "skip", why


def allowed(name, cwd, cfg):
    if ONLY is not None:
        return name == ONLY
    if cfg["allow_names"] and name not in cfg["allow_names"]:
        return False
    if name in cfg["deny_names"] or any(fnmatch.fnmatchcase(name, g) for g in cfg["deny_name_globs"]):
        return False
    return not any(cwd.startswith(os.path.expanduser(p)) for p in cfg["deny_paths"])


def lane_key(name, session_id):
    """A file-safe key for one session's state: its name plus the start of its
    session id, so two names that reduce to the same letters (or to none) never
    share state. The session id stays the same across /restart. The name part
    is cut at KEY_NAME_MAX letters: the key is part of a path typed in a note."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:KEY_NAME_MAX].strip("-") or "session"
    return f"{slug}-{pc.safe_id(session_id)[:8]}"


def age_of(path, now):
    """Seconds since the time written in a state file, or None."""
    try:
        with open(path) as f:
            return now - float(f.read().strip())
    except Exception:
        return None


def stamp(path, now):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(now))


def ask_time(sid, typedf, now):
    """When this session was last asked to sort its background jobs (epoch seconds), or None."""
    times = []
    a = pc.read_json(os.path.join(pc.ASKS, sid + ".json"), None)
    if isinstance(a, dict) and a.get("at"):
        try:
            times.append(float(a["at"]))
        except Exception:
            pass
    typed_age = age_of(typedf, now)
    if typed_age is not None:
        times.append(now - typed_age)
    return max(times) if times else None


def consent_state(okf, asked_at, now):
    """(consented, age, stale) for a session's re-arm list. The list counts only
    if it was written AFTER the session was asked; one with no ask on record, or
    older than the ask, is left over from another time (stale). Pure but for the stat."""
    try:
        mtime = os.path.getmtime(okf)
    except OSError:
        return False, 0, False
    if os.path.isdir(okf):
        return False, 0, False
    if asked_at is None or mtime < asked_at - 5:
        return False, now - mtime, True
    return True, now - mtime, False


def ask_note(newest, ver, okf, days=0):
    """The background-jobs ask, typed. A long path (a long home folder) gets the
    short form. DAYS (from escalate_days): the session has been behind that long
    because of its jobs, so the ask is to stop everything that can be restarted."""
    if days:
        text = (f"[AUTOMATED pit-stop restart, not your user] Claude Code {newest} is in (you run {ver}); for {days} "
                f"days your background jobs have kept you from restarting. At the next point where nothing is lost, stop ALL of "
                f"them, real work too if it can be restarted: (1) one line per job, with how to start it again, into "
                f"{okf}; (2) stop them. The helper restarts you, then says to start them again. Only if a job cannot "
                f"stop without losing work, do nothing. No reply needed.")
        if len(text) <= NOTE_MAX:
            return text
    text = (f"[AUTOMATED pit-stop restart, not your user] Claude Code {newest} is installed; you run {ver}, and /restart "
            f"is refused while background jobs run. If any is real work (a build, a test, a deploy), do nothing: "
            f"you will be asked again. If all are watchers or loops you can start again: (1) write one line per job "
            f"into {okf} (an empty file if none); (2) stop them. The helper restarts you once you are idle, then "
            f"tells you to start them again. No reply needed.")
    if len(text) <= NOTE_MAX:
        return text
    return (f"[AUTOMATED pit-stop restart, not your user] Claude Code {newest} is in (you run {ver}); /restart waits "
            f"for background jobs. Real work running: do nothing. Else list them, one a line, in {okf} (empty if none), "
            f"and stop them; the helper restarts you, then says to start them again. No reply needed.")


def escalate_days(first, now, cfg=None):
    """Whole days since this session was FIRST asked about its background jobs
    (for the version it still runs), once that is jobs_escalate_days or more;
    else 0. Counted from the first ask, not from when it fell behind: a session
    that was simply busy for days has not been blocked by its jobs."""
    esc = (CFG if cfg is None else cfg).get("jobs_escalate_days")
    if not isinstance(esc, (int, float)) or isinstance(esc, bool) or esc <= 0:
        return 0
    if not isinstance(first, (int, float)) or isinstance(first, bool) or first > now:
        return 0
    days = int((now - first) // 86400)
    return days if days >= esc else 0


def handle_ask(s, key, target, ver, newest, okf, l, now):
    """Background jobs: leave the ask for the session's Stop hook; type it only
    for a session that has not loaded the hook, and only when that is cheap."""
    sid = pc.safe_id(s.get("sessionId"))
    name = s["name"]
    askf = os.path.join(pc.ASKS, sid + ".json")
    typedf = os.path.join(BASE, "state", key + ".asked")
    a = pc.read_json(askf, None)
    a = a if isinstance(a, dict) else None
    # the first ask for the version it still runs: how long its jobs have been in the way
    first = a.get("first", a.get("at")) if a and a.get("ver") == ver else None
    first = float(first) if isinstance(first, (int, float)) and not isinstance(first, bool) else now
    if a and (a.get("newest") != newest or now - float(a.get("at") or 0) >= CFG["ask_again"]):
        a = None  # a newer version, or the last ask is old: ask afresh
    if a is None:
        if LIVE:
            pc.write_json_atomic(askf, {"newest": newest, "ver": ver, "okf": okf, "at": now, "delivered": None,
                                        "first": first, "days": escalate_days(first, now)})
        log(f"{name}: {ver}->{newest} background jobs: ask left for its Stop hook" + ("" if LIVE else " (DRY)"))
        return
    if a.get("delivered"):
        log(f"{name}: {ver}->{newest} skip: asked by its Stop hook {int(now - float(a['delivered']))}s ago; "
            f"its jobs still run (asks again after {CFG['ask_again']}s)")
        return
    if os.path.exists(os.path.join(pc.SEEN, sid)):
        log(f"{name}: {ver}->{newest} skip: ask waits for the end of its next turn (its Stop hook is loaded)")
        return
    if l.get("typing_blocked"):
        log(f"{name}: {ver}->{newest} skip: ask left; not typing it now ({l['typing_blocked']})")
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
    if not (small or cache_warm(l.get("call_age", l["idle"]), CFG)) or l["idle"] < CFG["min_idle"]:
        log(f"{name}: {ver}->{newest} skip: ask not typed now (idle {int(l['idle'])}s, context {l['ctx']}: a typed "
            f"note starts a turn, so only while the cache is warm or the context small, and after {CFG['min_idle']}s idle)")
        return
    if not input_empty(target) or screen_busy(target):
        log(f"{name}: {ver}->{newest} skip: text in the input box or a dialog on screen")
        return
    sent, how = type_line(target, ask_note(newest, ver, okf, escalate_days(first, now)))
    note_typing(os.path.join(BASE, "state", key + ".mismatch"), sent, how, now)
    if sent and LIVE:
        stamp(typedf, now)


def confirm_restart(s, newest, wait=150):
    """True once the SAME session id runs the newest version (waits up to WAIT seconds)."""
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(5)
        d = pc.registry_sessions().get(s.get("sessionId")) or {}
        if d.get("pid") and (pc.pid_version(d["pid"]) or d.get("version")) == newest:
            return True
        for x in (pc.cli_sessions(timeout=10) or []) if not d else []:
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


def archive(okf, word, now):
    """Move a re-arm list out of the way (a dry run moves nothing)."""
    if LIVE and os.path.isfile(okf):
        os.replace(okf, f"{okf}.{word}-{int(now)}")


def rearm_note(head, jobs, tail, where, inline=True):
    """The "start your jobs again" note, never over NOTE_MAX and never with a
    cut list: the whole list in the note when it fits, else the name of the
    file that holds it (WHERE: where the list is once the note was sent)."""
    if not jobs:
        return head + tail
    text = f"{head} (your list: {'; '.join(jobs)}){tail}"
    if inline and len(text) <= NOTE_MAX:
        return text
    text = f"{head}: your list of {len(jobs)} is in the file {where} (read it){tail}"
    if len(text) <= NOTE_MAX:
        return text
    return (f"[AUTOMATED pit-stop restart, not your user] Start again the background jobs you stopped: the list is "
            f"in {where}. No reply needed.")   # a very long path: the short form (over the limit, type_line refuses it)


def tell_rearm(target, okf, mmf, head, tail, now):
    """Type the "start your jobs again" note. The list is put away ONLY once the
    note was sent; otherwise it stays and the next run tries again. After a
    typing mismatch the next try names the file instead of carrying the list
    (a list can hold text the input box shows differently)."""
    if not input_empty(target) or screen_busy(target):
        return False
    text = rearm_note(head, rearm_list(okf), tail, f"{okf}.done-{int(now)}", inline=not os.path.exists(mmf))
    sent, how = type_line(target, text)
    note_typing(mmf, sent, how, now)
    if sent:
        archive(okf, "done", now)
    return sent


def reload_due(l, cfg):
    """(True, why) when this session should get /reload-plugins now, else
    (False, why). Pure, for tests. l: name, status, idle, call_age, ctx, compact_age,
    want_gen (the installed plugin's hook generation, or None if it cannot be
    read), runs_root (the plugin folder this session's hooks run from, or None
    if it loaded none), runs_gen (that folder's hook generation, None if the
    folder or its hooks are gone), tried_age (seconds since the helper's last
    reload try, or None).
    A session is due only when the hooks it runs are MISSING or WRONG: it has
    none, or the folder they run from is gone, or their generation differs
    from the installed one. Merely older is not due (see hooks/generation.json)."""
    if not cfg.get("reload_plugins"):
        return False, "off"
    if not l.get("want_gen"):
        return False, "the installed plugin's hooks cannot be read"
    only = cfg.get("reload_only_names") or []
    if only and l["name"] not in only:
        return False, "not in reload_only_names"
    if l.get("runs_gen") == l["want_gen"]:
        return False, "loaded"
    if l.get("tried_age") is not None and l["tried_age"] < cfg["reload_retry"]:
        return False, f"a reload was tried {int(l['tried_age'])}s ago (waits {cfg['reload_retry']}s)"
    if l["status"] not in ("idle", "shell"):
        return False, f"status={l['status']} (working)"
    idle, ctx, cage = l["idle"], l["ctx"], l.get("compact_age")
    if idle < 0:
        return False, "no transcript found"
    what = ("it has no Pit Stop hook" if not l.get("runs_root") else
            "the folder its hooks run from is gone" if l.get("runs_gen") is None else
            f"it runs hook generation {l['runs_gen']}, the installed one is {l['want_gen']}")
    if cage is not None and cage <= cfg["compact_window"] and idle >= cfg["min_idle_after_compact"]:
        return True, f"{what}; compacted {int(cage)}s ago, idle {int(idle)}s"
    if ctx is not None and ctx < cfg["ctx_max"] and idle >= cfg["min_idle"]:
        return True, f"{what}; small context {ctx}, idle {int(idle)}s"
    cold = l.get("call_age")
    cold = idle if cold is None else cold
    if cold >= cfg["cache_ttl"] and idle >= min(cfg["min_idle"], cfg["cache_ttl"]):
        return True, f"{what}; last model call {int(cold)}s ago, cache cold"
    if cfg.get("reload_warm") and idle >= cfg["min_idle"]:
        return True, f"{what}; reload_warm is on, idle {int(idle)}s"
    return False, (f"{what}; context {ctx}, idle {int(idle)}s: big and warm, so it waits for a compaction, "
                   f"a context under {cfg['ctx_max']} or a cold cache")


def plugin_info():
    """What the reload rule needs to know about the installed plugin, read once per run."""
    root = pc.plugin_root()
    return {"root": root, "gen": pc.hook_generation(root), "versions": pc.plugin_versions()}


def lane_hooks(s, info, reloaded_at=None):
    """(folder, generation) of the Pit Stop hooks this session runs.
    Best evidence first: the stamp the plugin's own hooks leave for the session
    (~/.claude/pit-stop/seen/<id>: a time and the folder they ran from), if it is
    newer than the session's last start or reload. Otherwise it is worked out:
    a session loads its plugins when its process starts or on /reload-plugins,
    and gets the newest version folder that was in the cache at that moment."""
    loaded_at = max((s.get("startedAt") or 0) / 1000, reloaded_at or 0)
    root = None
    try:
        with open(os.path.join(pc.SEEN, pc.safe_id(s.get("sessionId")))) as f:
            parts = f.read().strip().split(" ", 1)
        if len(parts) == 2 and float(parts[0]) >= loaded_at and parts[1]:
            root = parts[1]
    except (OSError, ValueError):
        pass
    root = root or pc.root_loaded_at(loaded_at, info["versions"])
    return root, (pc.hook_generation(root) if root else None)


def reloaded_row(rows):
    """The "Reloaded: ..." row that answers the NEWEST /reload-plugins on screen,
    or None. A row left from an earlier reload does not count: it sits above
    the command just typed. Pure, for tests."""
    rows = [ANSI.sub("", r).strip() for r in rows]
    cmd = [i for i, r in enumerate(rows) if "/reload-plugins" in r and "Reloaded:" not in r]
    if not cmd:
        return None
    for r in rows[cmd[-1] + 1:]:
        if "Reloaded:" in r:
            return r[r.index("Reloaded:"):][:160]
    return None


def confirm_reload(target, wait=12):
    """Claude Code's answer to the /reload-plugins just typed, or None if it does not show in WAIT seconds."""
    for _ in range(wait):
        time.sleep(1)
        row = reloaded_row(screen(target))
        if row:
            return row
    return None


def maybe_reload(s, target, now, counts, info):
    """Type /reload-plugins into one session if reload_due says so. Called after
    handle(), and only for a session nothing was typed into in this run."""
    if not CFG.get("reload_plugins") or not info or target in TYPED or target in PLANNED:
        return   # (a session the restart path wants is left to it: the restart loads the plugin anyway)
    name, sid = s["name"], pc.safe_id(s.get("sessionId"))
    key, st_dir = lane_key(name, sid), os.path.join(BASE, "state")
    donef, tryf, mmf = (os.path.join(st_dir, key + x) for x in (".reloaded", ".reload_try", ".mismatch"))
    try:
        reloaded_at = os.path.getmtime(donef)
    except OSError:
        reloaded_at = None
    ctx, idle = ctx_and_idle(s)
    if s.get("statusUpdatedAt") and idle >= 0:
        idle = min(now - s["statusUpdatedAt"] / 1000, idle)
    runs_root, runs_gen = lane_hooks(s, info, reloaded_at)
    l = {"name": name, "status": s.get("status"), "idle": idle, "ctx": ctx, "call_age": call_age_of(s, idle),
         "compact_age": last_compact_age(transcript_path(s), now=now),
         "want_gen": info["gen"], "runs_root": runs_root, "runs_gen": runs_gen, "tried_age": age_of(tryf, now)}
    due, why = reload_due(l, CFG)
    if not due:
        if why not in ("off", "loaded", "not in reload_only_names") and not LIVE:
            log(f"{name}: reload wait: {why}")
        return
    mm = pc.read_json(mmf, {}) or {}
    if mm.get("n") and mismatch_wait(mm["n"], CFG) - (now - float(mm.get("at", 0))) > 0:
        log(f"{name}: skip: reload due, but typed text did not match {mm['n']} time(s) in a row; backing off")
        return
    if counts["reload"] >= CFG["max_per_run"]:
        if not LIVE:   # a live run stays quiet here: many sessions can be due at once, one line each per run adds up
            log(f"{name}: run limit reached for /reload-plugins; next run")
        return
    if not input_empty(target) or screen_busy(target):
        if not LIVE:   # quiet in a live run: this can be true run after run, and it is not an event
            log(f"{name}: reload wait: text in its input box, a dialog, or work on screen")
        return
    log(f"{name}: /reload-plugins: {why}" + ("" if LIVE else " (DRY)"))
    sent, how = type_line(target, "/reload-plugins")
    note_typing(mmf, sent, how, now)
    if not sent:
        return
    counts["reload"] += 1
    if not LIVE:
        return
    stamp(tryf, now)   # tried: whatever happens next, leave it alone for reload_retry
    row = confirm_reload(target)
    if row:
        stamp(donef, time.time())
        log(f"{name}: RELOADED plugins, no turn started ({row})")
    else:
        log(f"{name}: RELOAD NOT CONFIRMED (no 'Reloaded:' line on screen); next try after {CFG['reload_retry']}s")


def handle(s, newest, target, now, counts):
    """Everything for one session. Raises nothing the caller must understand:
    main() catches and logs, so one odd session never stops the run."""
    name, cwd, ver = s["name"], s.get("cwd") or "", s.get("version")
    sid = pc.safe_id(s.get("sessionId"))
    behind = pc.vkey(ver) < pc.vkey(newest)
    tag = f"{name}: {ver}->{newest}" if behind else f"{name}:"
    key = lane_key(name, sid)
    st_dir = os.path.join(BASE, "state")
    lastf, mmf = os.path.join(st_dir, key + ".last"), os.path.join(st_dir, key + ".mismatch")
    compf, okf = os.path.join(st_dir, key + ".compact"), os.path.join(BASE, "ok", key)
    typedf = os.path.join(st_dir, key + ".asked")
    status = s.get("status")
    typing_blocked = None
    mm = pc.read_json(mmf, {}) or {}
    if mm.get("n"):
        left = mismatch_wait(mm["n"], CFG) - (now - float(mm.get("at", 0)))
        if left > 0:
            typing_blocked = (f"typed text did not match {mm['n']} time(s) in a row; backing off, "
                              f"next try in {int(left)}s")
    consented, consent_age, stale = consent_state(okf, ask_time(sid, typedf, now), now)

    last_age = age_of(lastf, now)
    if not behind:
        # Up to date. A re-arm list still here means it was restarted (late, or by hand) and never told.
        if os.path.isfile(okf):
            mtime = os.path.getmtime(okf)
            started = (s.get("startedAt") or 0) / 1000   # Claude Code sets this again at every /restart
            restarted_after = started >= mtime or (last_age is not None and now - last_age >= mtime)
            jobs = rearm_list(okf)
            if not restarted_after or not jobs or now - mtime > REARM_MAX_AGE:
                log(f"{tag} re-arm list put away (old, empty, or not followed by a restart); nothing typed")
                archive(okf, "stale", now)
            elif typing_blocked:
                log(f"{tag} skip: it is on {ver} with {len(jobs)} job(s) to start again; not typing now ({typing_blocked})")
                return
            elif status not in ("idle", "shell"):
                log(f"{tag} skip: it is on {ver} with {len(jobs)} job(s) to start again; it is working, next run")
                return
            else:
                sent = tell_rearm(target, okf, mmf, (
                    f"[AUTOMATED pit-stop restart, not your user] You are now on Claude Code {ver}; same conversation. "
                    f"The background jobs you stopped for the restart are down: start them again now"),
                    ". Scheduled session crons survive a restart. No reply needed.", now)
                log(f"{tag} told to start its {len(jobs)} job(s) again" if sent else
                    f"{tag} skip: {len(jobs)} job(s) to start again, but its input box is not free; next run")
                if not sent:
                    return
            if LIVE:  # the restart is over: its ask and stamps are history
                for f in (compf, typedf, os.path.join(pc.ASKS, sid + ".json")):
                    if os.path.exists(f):
                        os.remove(f)
            return
        if not CFG.get("idle_compact_after") or typing_blocked:
            return
    elif stale:
        log(f"{tag} re-arm list put away: it does not answer an ask on record (left over from another time)")
        archive(okf, "stale", now)

    blocked = None
    if behind and last_age is not None and last_age < CFG["restart_cooldown"]:
        blocked = f"a restart was tried by this helper {int(last_age)}s ago (waits {CFG['restart_cooldown']}s)"
    ctx, idle = ctx_and_idle(s)
    if s.get("statusUpdatedAt") and idle >= 0:
        idle = min(now - s["statusUpdatedAt"] / 1000, idle)
    l = {"status": status, "idle": idle, "ctx": ctx, "behind": behind, "call_age": call_age_of(s, idle),
         "compact_age": last_compact_age(transcript_path(s), now=now),
         "consented": consented, "consent_age": consent_age, "compact_ask_age": age_of(compf, now),
         "idle_point": pc.points(cwd, s.get("sessionId"))["idle_point"],
         "blocked": blocked, "typing_blocked": typing_blocked}
    action, why = plan(l, CFG)
    if action in ("restart", "compact", "rearm"):
        PLANNED.add(target)   # this run has something to type here (now or next run): nothing else is typed into it
    if action == "none":
        return
    if action in ("skip", "wait"):
        log(f"{tag} {action}: {why}")
        return
    if action == "ask":
        handle_ask(s, key, target, ver, newest, okf, l, now)
        return
    if typing_blocked:  # everything below types
        log(f"{tag} skip: {typing_blocked}")
        if action == "rearm":
            notef = os.path.join(st_dir, key + ".notified")
            told = age_of(notef, now)
            if LIVE and (told is None or told >= CFG["restart_cooldown"]):  # once, not every run
                notify(f"pit-stop: {name} stopped its jobs for a restart that did not come, and its pane cannot be "
                       f"typed into; check it")
                stamp(notef, now)
        return
    if not input_empty(target):
        log(f"{tag} skip: text in the input box")
        return
    if screen_busy(target):
        log(f"{tag} skip: a dialog or work on screen, or the pane is in tmux copy mode")
        return
    if action == "rearm":
        jobs = rearm_list(okf)
        log(f"{tag} {why}: telling it to start its jobs again")
        tell_rearm(target, okf, mmf, (
            "[AUTOMATED pit-stop restart, not your user] The restart you listed your background jobs for is not "
            "happening for now. Start again any you stopped"), ". No reply needed.", now)
        return
    if action == "compact":
        if counts["compact"] >= CFG["max_per_run"]:
            log(f"{tag} run limit reached for /compact; next run")
            return
        log(f"{tag} /compact: {why}" + ("" if LIVE else " (DRY)"))
        sent, how = type_line(target, "/compact")
        note_typing(mmf, sent, how, now)
        if sent:
            counts["compact"] += 1
            if LIVE:
                stamp(compf, now)
        return
    # action == "restart"
    log(f"{tag} at a break: {why}")
    if counts["restart"] >= CFG["max_per_run"]:
        log(f"{name}: run limit reached; next run")
        return
    if not LIVE:
        log(f"DRY would /restart {name} ({ver} -> {newest}, ctx {ctx})")
        counts["restart"] += 1
        return
    sent, how = type_line(target, "/restart")
    note_typing(mmf, sent, how, now)
    if not sent:
        return
    counts["restart"] += 1
    stamp(lastf, now)  # tried: whatever happens next, leave it alone for restart_cooldown
    time.sleep(6)
    if any("Can't restart" in r for r in screen(target)):
        log(f"{name}: /restart refused by Claude Code (see its pane); next try after {CFG['restart_cooldown']}s")
        notify(f"pit-stop: {name} refused /restart; see its pane")
        return
    if not confirm_restart(s, newest):
        log(f"{name}: RESTART NOT CONFIRMED on {newest} yet; if it lands, the next run tells it to start its jobs again")
        notify(f"pit-stop: {name} /restart not confirmed; check its pane")
        return
    jobs = rearm_list(okf)
    log(f"{name}: RESTARTED {ver} -> {newest}, same session"
        + (f"; {len(jobs)} job(s) to start again" if jobs else "; nothing typed after (no re-arm list)"))
    if jobs:  # only now is a note worth a turn: its watchers are down until it starts them
        time.sleep(10)
        if not tell_rearm(target, okf, mmf, (
                f"[AUTOMATED pit-stop restart, not your user] You were restarted at {ts()} onto Claude Code {newest} "
                f"(was {ver}); same conversation. The background jobs you stopped for it are down: start them again "
                f"now"), ". Scheduled session crons survive a restart. No reply needed.", time.time()):
            log(f"{name}: its prompt was not ready for the start-your-jobs note; the next run sends it")
    else:
        archive(okf, "done", now)
    for f in (compf, typedf, os.path.join(pc.ASKS, sid + ".json")):
        if os.path.exists(f):
            os.remove(f)


PIT_STOP = "windstorm-pit-stop"
UPDATE_BUDGET = 300   # seconds one run may spend on plugin updates in all


def plugin_update_targets(scope, installed=None):
    """(marketplaces, plugins) the helper keeps up to date, from Claude Code's
    installed_plugins.json: for "pit-stop" this plugin and its marketplace; for
    "all" every plugin installed for the user (project and local installs are
    their project's business). Pure when INSTALLED is given."""
    if scope not in ("pit-stop", "all"):
        return [], []
    if installed is None:
        installed = pc.read_json(os.path.join(pc.CLAUDE_DIR, "plugins", "installed_plugins.json"), {}) or {}
    rows = installed.get("plugins") if isinstance(installed, dict) else None
    rows = rows if isinstance(rows, dict) else {}
    plugins = []
    for key, v in sorted(rows.items()):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*", key):
            continue
        if not any(isinstance(r, dict) and r.get("scope", "user") == "user" for r in (v if isinstance(v, list) else [])):
            continue
        if scope == "all" or key.split("@")[0] == PIT_STOP:
            plugins.append(key)
    return sorted({k.split("@")[1] for k in plugins}), plugins


def plugin_update_due(age, behind_sessions, cfg):
    """True when the plugins should be brought up to date now: once every
    plugin_update_every, and also (at most hourly) when sessions are about to be
    restarted onto a newer Claude Code, so that the same restart brings them the
    newest plugins too. Pure."""
    if cfg.get("plugin_updates") not in ("pit-stop", "all"):
        return False
    if age is None:
        return True
    return age >= cfg["plugin_update_every"] or (behind_sessions > 0 and age >= 3600)


def update_plugins(now, behind_sessions, run=None):
    """Bring the plugins up to date with Claude Code's own commands
    (claude plugin marketplace update, then claude plugin update), before this
    run restarts anyone. New versions land on disk; a running session keeps the
    version it loaded until its restart (or /reload-plugins). A dry run says
    what it would do. Returns the list of (plugin, ok, first line)."""
    run = run or (lambda cmd, t: subprocess.run(cmd, capture_output=True, text=True, timeout=t, stdin=subprocess.DEVNULL))
    stampf = os.path.join(BASE, "state", "plugin-update")
    if not plugin_update_due(age_of(stampf, now), behind_sessions, CFG):
        return []
    markets, plugins = plugin_update_targets(CFG["plugin_updates"])
    if not plugins:
        return []
    if not LIVE:
        log(f"DRY would update {len(markets)} marketplace(s) and {len(plugins)} plugin(s): {', '.join(plugins)[:200]}")
        return []
    stamp(stampf, now)   # first: a run that hangs is not repeated every two minutes
    start, out = time.time(), []
    for cmd in [["claude", "plugin", "marketplace", "update", m] for m in markets] + \
               [["claude", "plugin", "update", p] for p in plugins]:
        left = UPDATE_BUDGET - (time.time() - start)
        if left < 15:
            log(f"plugin update: time budget used; the rest next time ({' '.join(cmd[2:])} and after)")
            break
        try:
            r = run(cmd, min(180, left))
            ok, first = r.returncode == 0, ((r.stdout or r.stderr or "").strip().splitlines() or [""])[-1][:200]
        except Exception as e:
            ok, first = False, f"{type(e).__name__}: {str(e)[:120]}"
        log(f"plugin update: {' '.join(cmd[2:])}: {'ok' if ok else 'FAILED'}: {first}")
        if cmd[2] == "update":
            out.append((cmd[3], ok, first))
            if not ok:
                notify(f"pit-stop: plugin update failed for {cmd[3]}: {first}")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    newest = plugin_root_now()
    if newest and os.path.realpath(newest) != os.path.realpath(here) and not os.environ.get("PIT_STOP_LAUNCHER"):
        log(f"plugin update: this timer runs {here}, not the installed {newest}; to follow updates, point it at "
            f"{os.path.join(pc.STATE_DIR, 'bin', 'pit-stop-restart')} (/pit-stop setup does)")
    return out


def plugin_root_now():
    """The folder Claude Code has installed for this plugin now (see scripts/launcher.py), or None."""
    try:
        import launcher
        return launcher.plugin_root(pc.CLAUDE_DIR)
    except Exception:
        return None


def tune_every():
    """Seconds between two tunes of one folder ("tune_every_days" in config.json, default 1)."""
    v = pc.config().get("tune_every_days", 1)
    v = v if isinstance(v, (int, float)) and not isinstance(v, bool) else 1
    return max(1, min(30, v)) * 86400


def tune_due(sessions, now, cfg, tuned_dir=None, week=None):
    """The folder whose tune is due (WEEK: seconds between tunes, default
    tune_every()), or None: among the running sessions
    the helper may touch, the folder tuned longest ago (never tuned first).
    Pure but for the stats of the stamp files autotune.py leaves."""
    tuned_dir = tuned_dir or os.path.join(pc.STATE_DIR, "tuned")
    week = tune_every() - 600 if week is None else week
    best = None
    for s in sessions:
        cwd = s.get("cwd")
        if not cwd or not s.get("name") or not allowed(s["name"], cwd, cfg) or not os.path.isdir(cwd):
            continue
        folder = os.path.abspath(cwd)
        try:
            at = os.path.getmtime(os.path.join(tuned_dir, pc.project_key(folder)))
        except OSError:
            at = 0
        if now - at >= week and (best is None or (at, folder) < best):
            best = (at, folder)
    return best[1] if best else None


def machine_busy():
    """True when the 1-minute load is above one per processor (a tune can wait). Unknown = not busy."""
    try:
        return os.getloadavg()[0] > (os.cpu_count() or 1)
    except (AttributeError, OSError):
        return False


def start_tune(sessions, now):
    """Fleet-wide tune (daily by default). A session's folder used to be tuned only when a
    session started there, so a session that runs for weeks was never looked at
    again. Each run of the helper starts at most ONE tune (scripts/autotune.py,
    in the background, lowest priority): never while the last one still runs,
    never while the machine is busy. Autotune moves each point one step at most
    and writes its own log line; the ceiling works at once (the hook reads the
    profile every time), the two points from the session's next start."""
    runf = os.path.join(BASE, "state", "tune.pid")
    try:   # a tune still running (a number older than an hour is some other process by now)
        with open(runf) as f:
            if now - os.path.getmtime(runf) < 3600 and pc.pid_alive(int(f.read().strip())):
                return None
    except (OSError, ValueError):
        pass
    folder = tune_due(sessions, now, CFG)
    if not folder or machine_busy():
        return None
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autotune.py")
    p = subprocess.Popen([sys.executable, "-I", script, "--project", folder], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    with open(runf, "w") as f:
        f.write(str(p.pid))
    log(f"tune started for {folder.replace(pc.HOME, '~')} (in the background; result in autotune.log)")
    return folder


KNOWN_OPTIONS = ("--live", "--only", "--not-before")


def unknown_options(argv=None):
    """Options this helper does not know. A run with one does nothing: a
    misspelled --not-before must never turn into a run that starts at once."""
    argv = sys.argv if argv is None else argv
    return [a for a in argv[1:] if a.startswith("--") and a.split("=", 1)[0] not in KNOWN_OPTIONS]


def too_early(argv=None, now=None):
    """--not-before 2026-11-11T20:45:00Z (or --not-before=...): do nothing
    before that time (UTC). For a booked test window: a run started early by a
    few seconds once typed six seconds before its slot (2026-11-10). Returns
    the seconds still to wait (> 0), 0 when it is time or the option is
    absent, and -1 when the time given cannot be read (the run then does
    nothing: a booked test that cannot tell its start time must not start)."""
    argv = sys.argv if argv is None else argv
    given = [a for a in argv if a == "--not-before" or a.startswith("--not-before=")]
    if not given:
        return 0
    if len(given) > 1:
        return -1
    if "=" in given[0]:
        val = given[0].split("=", 1)[1]
    else:
        i = argv.index("--not-before")
        val = argv[i + 1] if i + 1 < len(argv) else ""
    t = utc_ts(val) if re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", val) else None
    if t is None:
        return -1
    return max(0, t - (time.time() if now is None else now))


def main():
    bad = unknown_options()
    if bad:
        log(f"unknown option(s) {' '.join(bad)}; nothing done (known: {' '.join(KNOWN_OPTIONS)})")
        return
    for w in CFG_WARNINGS:
        log(w)
    wait = too_early()
    if wait:
        log("--not-before: cannot read the time (want 2026-11-11T20:45:00Z); nothing done" if wait < 0
            else f"--not-before: {int(wait) + 1}s too early; nothing done")
        return
    newest = pc.newest_installed_version()
    if not newest:
        log("no installed versions found; exit")
        return
    sessions, _ = pc.live_sessions()
    if not sessions:
        log("no running sessions found (registry and `claude agents --json` both empty); exit")
        return
    if LIVE:
        for d in ("state", "ok"):
            os.makedirs(os.path.join(BASE, d), exist_ok=True)
    try:
        tty_map, now = panes(), time.time()
    except (OSError, subprocess.SubprocessError) as e:
        # tmux missing from PATH (a timer or a bare ssh shell without Homebrew's folder), or not answering:
        # say so in one line instead of a traceback every run. Nothing can be typed without it.
        log(f"tmux cannot be run ({type(e).__name__}: {str(e)[:80]}); nothing done. Fleet mode needs tmux on PATH: "
            f"/pit-stop setup --fleet writes a timer with a PATH that has it")
        return
    counts = {"restart": 0, "compact": 0, "reload": 0}
    if ONLY is None and CFG.get("plugin_updates") in ("pit-stop", "all"):
        try:   # before any restart: the restart that brings a session the newest Claude Code brings the newest plugins
            behind = sum(1 for s in sessions if s.get("version") and pc.vkey(s["version"]) < pc.vkey(newest)
                         and allowed(s.get("name") or "", s.get("cwd") or "", CFG))
            update_plugins(now, behind)
        except Exception as e:
            log(f"plugin update: skip: error {type(e).__name__}: {str(e)[:120]}")
    info = None
    if CFG.get("reload_plugins"):
        try:
            info = plugin_info()
        except Exception as e:   # an odd installed_plugins.json must not stop the restarts
            log(f"reload rule: skip this run: cannot read the installed plugin ({type(e).__name__})")
    TYPED.clear()
    PLANNED.clear()
    for s in sorted(sessions, key=lambda x: x["name"]):
        try:
            target = tty_map.get(pid_tty(s.get("pid")) or "")
            if not target or not s["name"] or not s.get("version") or not s.get("sessionId"):
                continue
            if not allowed(s["name"], s.get("cwd") or "", CFG):
                continue
            handle(s, newest, target, now, counts)
            maybe_reload(s, target, now, counts, info)
        except Exception as e:  # one odd session (an unreadable transcript, a vanished pane) never stops the run
            log(f"{s.get('name') or '?'}: skip: error {type(e).__name__}: {str(e)[:120]}")
    if LIVE and ONLY is None and CFG.get("needs_you", True):
        try:   # about once an hour: the short list of what needs a person (~/.claude/pit-stop/needs-you.md)
            import needs_you
            needs_you.refresh(sessions, newest, time.time(), panes=tty_map)
        except Exception as e:
            log(f"needs-you list: skip: error {type(e).__name__}: {str(e)[:120]}")
    if LIVE and ONLY is None and CFG.get("fleet_tune", True) and pc.config().get("auto_tune") is True:
        try:
            start_tune(sessions, time.time())
        except Exception as e:
            log(f"tune: skip: error {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    main()
