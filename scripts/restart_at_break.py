#!/usr/bin/env python3
"""Pit Stop, part 2: restart at the break (fleet mode, optional, Linux/macOS + tmux).

Claude Code downloads updates by itself, but a running session keeps its old
version until it restarts. This helper types /restart into a session's tmux
pane ONLY when that is cheap and safe, so sessions move to the newest version
at a break, usually right after their own compaction.

  restart_at_break.py            dry run: print decisions, change nothing
  restart_at_break.py --live     act (run it every 10 minutes from cron,
                                 launchd or a systemd user timer)

A session is restarted only when ALL hold:
  - it runs an older version than the newest installed one;
  - it is at a cheap, safe break, which is ONE of these (checked in order):
      (a) it COMPACTED in the last compact_window seconds (default 1800) and has
          been idle >= min_idle_after_compact seconds (default 60). A compaction
          happens only at a break, and the context is as small as it will get,
          so this is the best moment even when the context is still large;
      (b) its context is small (< ctx_max, default 100K) and it has been idle
          >= min_idle seconds (default 300);
      (c) it has been idle >= cold_idle seconds (default 3600): the prompt cache
          is cold anyway, so its next turn re-reads everything with or without
          a restart, and the restart costs almost nothing extra;
  - Claude Code reports it idle, its transcript has not moved for that long,
    the input box is empty (a dimmed suggestion counts as empty), and no dialog
    or work is on screen;
  - it has no background jobs. Claude Code refuses /restart while background
    work runs, so the helper types ONE note asking the session to write the
    jobs it will re-arm into a file and stop them itself; it asks again in 12 h;
  - this helper has not restarted it in the last 20 h.
Every skipped session gets one log line saying WHY (skip: <reason>), so a
session stuck on an old version is visible in restart.log.
At most max_per_run restarts per run (default 1). After /restart it confirms
the SAME session id on the newest version, then types one note telling the
session to re-arm its jobs. Same conversation, model and effort; scheduled
session crons survive /restart (tested on Claude Code 2.1.318 -> 2.1.319).
Compaction itself never changes the version: only a restart does. Run this
every 2-10 minutes; the shorter the interval, the sooner (a) catches a break.

Config (optional) ~/.claude/pit-stop/restart.json:
  {"tmux_socket": null,            # tmux -L name; null = default server
   "allow_names": [],              # if set, ONLY these session names
   "deny_names": [], "deny_paths": [],
   "ctx_max": 100000, "min_idle": 300, "max_per_run": 1,
   "compact_window": 1800, "min_idle_after_compact": 60, "cold_idle": 3600,
   "notify_command": null}         # e.g. ["notify-send", "pit-stop"]; gets the message as last arg
Relies on Claude Code's local session registry (~/.claude/sessions/*.json),
which is not a public interface: if it changes, sessions read as busy and
nothing restarts (fail closed).
"""
import calendar
import glob
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402

LIVE = "--live" in sys.argv
CFG = {"tmux_socket": None, "allow_names": [], "deny_names": [], "deny_paths": [],
       "ctx_max": 100000, "min_idle": 300, "max_per_run": 1, "notify_command": None,
       "compact_window": 1800, "min_idle_after_compact": 60, "cold_idle": 3600}
CFG.update(pc.read_json(os.path.join(pc.STATE_DIR, "restart.json"), {}) or {})
BASE = os.path.join(pc.STATE_DIR, "restart")
for d in ("state", "ok"):
    os.makedirs(os.path.join(BASE, d), exist_ok=True)
LOG = os.path.join(BASE, "restart.log")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DIM_SEG = re.compile(r"\x1b\[2m.*?(?=\x1b\[(?:0|22)?m|$)")
PROMPT = "❯"


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    if os.path.exists(LOG) and os.path.getsize(LOG) > 5_000_000:
        os.replace(LOG, LOG + ".1")  # keep one old log; never grow without bound
    with open(LOG, "a") as f:
        f.write(f"{ts()} {msg}\n")
    print(f"{ts()} {msg}")


def tmux(*args):
    base = ["tmux"] + (["-L", CFG["tmux_socket"]] if CFG["tmux_socket"] else [])
    return subprocess.run(base + list(args), capture_output=True, text=True, timeout=15)


def vkey(v):
    return tuple(int(x) for x in v.split("."))


def exe_version(pid):
    try:
        return os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        return None  # macOS: falls back to the registry's version field


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
    if not input_empty(target) or screen_busy(target):
        log(f"{target}: input not empty or busy at send time; skipped")
        return False
    if not LIVE:
        log(f"DRY would type into {target}: {text[:90]}")
        return True
    tmux("send-keys", "-t", target, "-l", text)
    time.sleep(1)
    # Someone may have typed in that second: press Enter only if the box holds
    # EXACTLY our text; otherwise clear the line and skip.
    if not same_text(input_box_text(screen(target, escapes=True)), text):
        tmux("send-keys", "-t", target, "C-u")
        log(f"{target}: input box did not match the typed text; cleared it, skipped, no Enter")
        return False
    tmux("send-keys", "-t", target, "Enter")
    return True


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


def at_a_break(status, idle, ctx, compact_age, cfg):
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
    if ctx is not None and ctx < cfg["ctx_max"] and idle >= cfg["min_idle"]:
        return True, f"small context {ctx}, idle {int(idle)}s"
    if idle >= cfg["cold_idle"]:
        return True, f"idle {int(idle)}s >= cold_idle (cache cold anyway)"
    if ctx is None:
        return False, "context unreadable"
    return False, (f"idle {int(idle)}s, context {ctx}"
                   f" (needs: a compaction in the last {cfg['compact_window']}s, or context < {cfg['ctx_max']}"
                   f" and idle >= {cfg['min_idle']}s, or idle >= {cfg['cold_idle']}s)")


def main():
    newest = pc.newest_installed_version()
    if not newest:
        log("no installed versions found; exit")
        return
    tty_map, now, done = panes(), time.time(), 0
    for sf in sorted(glob.glob(os.path.join(pc.CLAUDE_DIR, "sessions", "*.json"))):
        s = pc.read_json(sf, {}) or {}
        pid, name, cwd = s.get("pid"), (s.get("name") or "").strip(), s.get("cwd") or ""
        if not pid or s.get("kind") not in (None, "interactive"):
            continue
        try:
            os.kill(int(pid), 0)
        except Exception:
            continue
        target = tty_map.get(pid_tty(pid) or "")
        if not target or not name:
            continue
        if CFG["allow_names"] and name not in CFG["allow_names"]:
            continue
        if name in CFG["deny_names"] or any(cwd.startswith(os.path.expanduser(p)) for p in CFG["deny_paths"]):
            continue
        ver = exe_version(pid) or s.get("version")
        if not ver or vkey(ver) >= vkey(newest):
            continue
        key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        lastf = os.path.join(BASE, "state", key + ".last")
        if os.path.exists(lastf) and now - float(open(lastf).read()) < 72000:
            log(f"{name}: {ver}->{newest} skip: restarted by this helper < 20 h ago")
            continue
        status = s.get("status")
        st_age = now - (s.get("statusUpdatedAt") or now * 1000) / 1000
        ctx, idle = ctx_and_idle(s)
        idle = min(st_age, idle) if idle >= 0 else idle
        cage = last_compact_age(transcript_path(s), now=now)
        okf = os.path.join(BASE, "ok", key)
        ok_break, why = at_a_break(status, idle, ctx, cage, CFG)
        if not ok_break:
            log(f"{name}: {ver}->{newest} skip: {why}")
            continue
        if not input_empty(target):
            log(f"{name}: {ver}->{newest} skip: text in the input box")
            continue
        if screen_busy(target):
            log(f"{name}: {ver}->{newest} skip: a dialog or work on screen")
            continue
        log(f"{name}: {ver}->{newest} at a break: {why}")
        if status == "shell":
            askf = os.path.join(BASE, "state", key + ".asked")
            if os.path.exists(okf):
                log(f"{name}: re-arm list written; waiting for it to stop its background jobs")
            elif not os.path.exists(askf) or now - float(open(askf).read()) >= 43200:
                if type_line(target, f"[AUTOMATED pit-stop restart, not your user] Claude Code {newest} is installed; you run {ver}. "
                             f"You are at a break, so a restart now is cheap, but Claude Code will not /restart while "
                             f"background jobs run. When nothing important is in progress: (1) write one line per job you "
                             f"will re-arm into {okf} (an empty file if none); (2) stop your background jobs yourself. "
                             f"The helper restarts you within 10 minutes once you are idle with no jobs and tells you to "
                             f"re-arm. If now is a bad time, do nothing: it asks again in 12 hours. No reply needed.") and LIVE:
                    open(askf, "w").write(str(now))
            continue
        if done >= CFG["max_per_run"]:
            log(f"{name}: run limit reached; next run")
            continue
        if not LIVE:
            log(f"DRY would /restart {name} ({ver} -> {newest}, ctx {ctx})")
            continue
        if not type_line(target, "/restart"):
            continue
        done += 1
        time.sleep(6)
        if any("Can't restart" in r for r in screen(target)):
            log(f"{name}: /restart refused (background work); will ask it to stop its jobs")
            continue
        open(lastf, "w").write(str(now))
        ok = False
        for _ in range(24):
            time.sleep(5)
            for f in glob.glob(os.path.join(pc.CLAUDE_DIR, "sessions", "*.json")):
                d = pc.read_json(f, {}) or {}
                if d.get("sessionId") == s.get("sessionId") and (exe_version(d.get("pid")) or d.get("version")) == newest:
                    ok = True
            if ok:
                break
        if ok:
            log(f"{name}: RESTARTED {ver} -> {newest}, same session")
            time.sleep(10)
            rearm = f"your list in {okf}" if os.path.exists(okf) else "none listed"
            type_line(target, f"[AUTOMATED pit-stop restart, not your user] You were restarted at {ts()} onto Claude Code "
                              f"{newest} (was {ver}); same conversation. Background jobs from before were stopped: re-arm "
                              f"them now ({rearm}). Scheduled session crons survive a restart. No reply needed.")
            if os.path.exists(okf):
                os.replace(okf, os.path.join(BASE, "state", f"{key}.rearmed-{int(now)}"))
        else:
            log(f"{name}: RESTART NOT CONFIRMED on {newest} within 2 minutes")
            notify(f"pit-stop: {name} /restart not confirmed; check its pane")


if __name__ == "__main__":
    main()
