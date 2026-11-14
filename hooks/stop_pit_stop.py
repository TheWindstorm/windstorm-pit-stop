#!/usr/bin/env python3
"""Pit Stop, part 3: at the end of a turn, pass on what is waiting.

Claude Code runs this Stop hook each time a session finishes a turn. It does
at most one thing, and almost always nothing:

  1. THE BACKGROUND-JOBS ASK (fleet mode). Claude Code refuses /restart while
     background work runs. When the restart helper finds a session on an old
     version with background jobs, it leaves a small file for that session
     instead of typing into its terminal. This hook finds the file at the end
     of the session's next turn and hands the ask to Claude (exit 2, the ask
     on stderr), once. Claude then sorts its jobs: watchers it can start again
     are listed and stopped; real work in progress is left alone.
  2. THE UPDATE LINE (no fleet mode). When a newer Claude Code is installed
     than the one this session runs, the person sees one line, once per
     version: type /restart at a break. Claude does not see it and no turn
     starts. Switch it off with "nudge": false in ~/.claude/pit-stop/config.json.

A week after the first /pit-stop checkup it shows the person one line, once,
saying the before-and-after is ready. It also starts the daily tune of the session's folder in the background when
tuning is on and due (see pitstop_common.start_tune): a session left open for
weeks is tuned too, with no timer.

It never blocks twice in a row (stop_hook_active), allows the stop on any
error, and leaves a stamp per session so the restart helper knows the plugin
is loaded there. Reads and writes only under ~/.claude/pit-stop/.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import pitstop_common as pc  # noqa: E402

LOG = os.path.join(pc.STATE_DIR, "stop.log")


def ask_text(a):
    okf = a.get("okf") or ""
    days = a.get("days") or 0
    if days:  # behind for days because the jobs never stop: the helper asks for everything that can be restarted
        return (f"Pit Stop (this plugin's own hook, set up by your user): Claude Code {a.get('newest')} is installed and "
                f"this session runs {a.get('ver')}. For {days} days its background jobs have kept it from restarting "
                f"(Claude Code will not restart a session while they run). It has waited long enough. At the next point where "
                f"nothing is lost (a run has finished, work is saved, a job can pick up where it stopped), stop ALL your "
                f"background jobs, real work too if it can be started again: (1) write one line per job, with how to "
                f"start it again, into {okf}; (2) stop them yourself; (3) end your turn. The restart helper then restarts "
                f"this session within a few minutes, same conversation, and tells you to start your jobs again. Only if a "
                f"job cannot be stopped at all without losing work, change nothing and end your turn: you will be asked "
                f"again later. Do not type /restart yourself. No reply to the user is needed for this.")
    return (f"Pit Stop (this plugin's own hook, set up by your user): Claude Code {a.get('newest')} is installed and this "
            f"session runs {a.get('ver')}. Claude Code will not restart a session while background work runs, and you have "
            f"background jobs. Sort them now: (A) watchers, monitors and loops that you can simply start again; "
            f"(B) real work in progress (a build, a test run, a deploy, a download). If there is any B, change nothing and "
            f"end your turn: you will be asked again later. If there is only A: (1) write one line per job you will start "
            f"again into {okf} (an empty file if none); (2) stop those jobs yourself; (3) end your turn. The restart helper "
            f"then restarts this session within a few minutes, same conversation, and tells you to start your jobs again. "
            f"Do not type /restart yourself. No reply to the user is needed for this.")


VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,6}")
OK_DIR = os.path.join(pc.STATE_DIR, "restart", "ok")


def ask_ok(a):
    """True when every field the ask prints is what the restart helper writes:
    two plain version numbers, a whole number of days (or none), and a re-arm list path directly inside
    ~/.claude/pit-stop/restart/ok/ made of letters, digits, '.', '_' and '-'.
    The text goes to Claude as an instruction, so a file with anything else in
    it (someone else's writing, a path elsewhere, a line break) is not passed on."""
    if not all(isinstance(a.get(k), str) and VERSION.fullmatch(a[k]) for k in ("newest", "ver")):
        return False
    days = a.get("days", 0)
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 3650):
        return False
    okf = a.get("okf")
    if not isinstance(okf, str) or len(okf) > 400:
        return False
    name = os.path.basename(okf)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name) or name in (".", ".."):
        return False
    norm = lambda x: os.path.normcase(os.path.normpath(x))  # noqa: E731
    return norm(okf) == norm(os.path.join(OK_DIR, name))


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def touch(path):
    """The stamp says when this session's Stop hook last ran and from which plugin folder: the restart
    helper reads it to know which version's hooks the session runs."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(f"{int(time.time())} {ROOT}")
    except Exception:
        pass


def week_note(now=None):
    """Once, a week after the first /pit-stop checkup: one line telling the person the before-and-after is ready.
    Never again after that (a stamp), never when "checkup_reminder" is false in config.json."""
    base = os.path.join(pc.STATE_DIR, "checkup", "baseline.json")
    told = os.path.join(pc.STATE_DIR, "checkup", "week-told")
    if os.path.exists(told) or pc.config().get("checkup_reminder") is False:
        return None
    b = pc.read_json(base, None)
    at = b.get("at") if isinstance(b, dict) else None
    if not isinstance(at, (int, float)) or isinstance(at, bool) or (now or time.time()) - at < 7 * 86400:
        return None
    try:
        with open(told, "w") as f:
            f.write(str(int(now or time.time())))
    except OSError:
        return None   # cannot record it: do not say it, or it would repeat every turn
    return ("Pit Stop: a week has passed since your first checkup. Type /pit-stop checkup to see this computer's "
            "before and after.")


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    sid = payload.get("session_id")
    if not sid or payload.get("stop_hook_active"):
        return 0
    key = pc.safe_id(sid)
    touch(os.path.join(pc.SEEN, key))
    pc.start_tune(payload.get("cwd"))   # the daily tune, when on and due: a stat, or one background start
    askf = os.path.join(pc.ASKS, key + ".json")
    a = pc.read_json(askf, None)
    if isinstance(a, dict) and not a.get("delivered"):
        ver = pc.session_version(sid)
        if ver and pc.vkey(ver) >= pc.vkey(a.get("newest")):
            try:  # the session already runs that version: the ask is stale
                os.remove(askf)
            except OSError:
                pass
            return 0
        if not ask_ok(a):
            pc.append_log(LOG, f"session={key[:8]} ASK not passed on: a field is not what the helper writes")
            try:
                os.replace(askf, askf + ".refused")
            except OSError:
                pass
            return 0
        a["delivered"] = time.time()
        try:
            pc.write_json_atomic(askf, a)
        except Exception:
            return 0  # cannot record it: do not ask, or it would repeat every turn
        pc.append_log(LOG, f"session={key[:8]} ASK delivered ({a.get('ver')}->{a.get('newest')})")
        sys.stderr.write(ask_text(a) + "\n")
        return 2
    note = week_note()
    if note:
        sys.stdout.write(json.dumps({"systemMessage": note}))
        return 0
    if pc.config().get("nudge") is False or pc.fleet_active():
        return 0
    newest = pc.newest_installed_version()
    if not newest:
        return 0
    stamp = os.path.join(pc.STATE_DIR, "nudged", key)
    try:
        with open(stamp) as f:
            if f.read().strip() == newest:
                return 0
    except OSError:
        pass
    ver = pc.session_version(sid)
    if not ver or pc.vkey(ver) >= pc.vkey(newest):
        return 0
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write(newest)
    except Exception:
        return 0
    pc.append_log(LOG, f"session={key[:8]} NUDGE {ver}->{newest}")
    sys.stdout.write(json.dumps({"systemMessage": f"Pit Stop: Claude Code {newest} is installed; this session runs {ver}. "
                                                  f"Type /restart at a break to move onto it (same conversation)."}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
