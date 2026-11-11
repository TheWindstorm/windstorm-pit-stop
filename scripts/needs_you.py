#!/usr/bin/env python3
"""Pit Stop: the short list of what needs a person.

  needs_you.py            print it now (reads only; writes nothing)
  needs_you.py --write    also keep ~/.claude/pit-stop/needs-you.md up to date
                          (in fleet mode the restart helper does this about
                          once an hour, and tells notify_command when the
                          list changes)

Pit Stop looks at every session every few minutes and mostly finds nothing to
do. What it cannot fix by itself used to scroll past in a log. This is that,
as a list:
  - WAITING FOR YOU: a session that has had a question or a permission prompt
    on screen for 2 hours or more. Only you can answer it.
  - CANNOT BE UPDATED: a session that has been behind the newest Claude Code
    for a day or more, and the reason (not on the list the helper may touch;
    background jobs that never stop; never quiet long enough).
  - NOT LOADED: sessions that started before the plugin was installed or last
    updated, so they run old hooks or none.
Names, versions and durations only: nothing a session says or shows is read
out. Standard library only.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402

OUT = os.path.join(pc.STATE_DIR, "needs-you.md")
NOT_LISTED = "not on the list the helper may touch"
WAITING_AFTER = 2 * 3600
STUCK_AFTER = 24 * 3600
EVERY = 3600


def _rab():
    import restart_at_break as rab   # reads its own options from sys.argv; imported late on purpose
    return rab


def hours(sec):
    return f"{sec / 3600:.0f} hours" if sec < 72 * 3600 else f"{sec / 86400:.0f} days"


def behind_since(ver):
    """When a session on VER fell behind: the time the first newer Claude Code
    was installed on this machine (the oldest file in its versions folder that
    is newer than VER). Old version files get pruned, so for a session far
    behind this is "at least since"; never the newest release's time, which
    would make every stuck session look freshly behind after each update."""
    times = []
    try:
        for name in os.listdir(pc.VERSIONS):
            if pc.vkey(name) and pc.vkey(name) > pc.vkey(ver):
                times.append(os.path.getmtime(os.path.join(pc.VERSIONS, name)))
    except OSError:
        return None
    return min(times) if times else None


def collect(sessions, newest, now, panes=None):
    """PANES: the helper's map of terminal -> tmux pane, when it has one. With it,
    a session the helper cannot type into (no tmux pane) is named as that."""
    rab = _rab()
    cfg = rab.CFG
    try:
        info = rab.plugin_info()
    except Exception:
        info = {"root": None, "gen": None, "versions": []}
    waiting, stuck, behind_new, unloaded = [], [], [], []
    for s in sorted(sessions, key=lambda x: x.get("name") or ""):
        name, ver, st = s.get("name") or "(no name)", s.get("version"), s.get("status")
        sid = pc.safe_id(s.get("sessionId"))
        key = rab.lane_key(name, sid)
        st_age = now - s["statusUpdatedAt"] / 1000 if s.get("statusUpdatedAt") else None
        if st == "waiting" and st_age is not None and st_age >= WAITING_AFTER:
            waiting.append((name, st_age))
        if newest and ver and pc.vkey(ver) < pc.vkey(newest):
            since = behind_since(ver)
            if not rab.allowed(name, s.get("cwd") or "", cfg):
                why = NOT_LISTED
            elif panes and not panes.get(rab.pid_tty(s.get("pid")) or ""):
                why = "it does not run in a tmux pane the helper can type into"
            elif st == "waiting":
                why = "it is waiting on a question or permission prompt"
            elif st == "shell":
                ask = pc.read_json(os.path.join(pc.ASKS, sid + ".json"), None)
                why = "its background jobs never stop, and Claude Code will not restart a session while they run"
                if isinstance(ask, dict) and ask.get("at"):
                    why += f" (asked to list and stop them {hours(max(0, now - float(ask['at'])))} ago)"
            elif st == "busy":
                why = "it is in the middle of a turn right now; the helper looks again every few minutes"
            else:
                why = "it is never quiet long enough with a small enough context (it waits for a compaction or an idle hour)"
            if since is not None and now - since >= STUCK_AFTER:
                stuck.append((name, ver, now - since, why))
            else:
                behind_new.append(name)
        if info["gen"]:
            try:
                reloaded = os.path.getmtime(os.path.join(rab.BASE, "state", key + ".reloaded"))
            except OSError:
                reloaded = None
            if rab.lane_hooks(s, info, reloaded)[1] != info["gen"]:   # missing or wrong, not merely older
                unloaded.append(name)
    on_newest = sum(1 for s in sessions if newest and s.get("version") and pc.vkey(s["version"]) >= pc.vkey(newest))
    return {"newest": newest, "total": len(sessions), "on_newest": on_newest, "waiting": waiting, "stuck": stuck,
            "behind_new": behind_new, "unloaded": unloaded, "reload_on": bool(cfg.get("reload_plugins"))}


def render(d, now):
    out = [f"# Pit Stop: what needs you ({time.strftime('%a %Y-%m-%d %H:%M %Z', time.localtime(now))})", ""]
    out.append(f"{d['on_newest']} of {d['total']} sessions are on the newest Claude Code ({d['newest'] or 'not found'}).")
    if d["waiting"]:
        out += ["", "Waiting for you (a question or permission prompt on screen):"]
        out += [f"- {n}: {hours(a)}" for n, a in sorted(d["waiting"], key=lambda x: -x[1])]
    if d["stuck"]:
        out += ["", "Cannot be updated by Pit Stop (behind for a day or more):"]
        out += [f"- {n} (on {v}, {hours(a)}): {why}" for n, v, a, why in sorted(d["stuck"], key=lambda x: -x[2])
                if why != NOT_LISTED]
        off = [n for n, _, _, why in d["stuck"] if why == NOT_LISTED]
        if off:
            out.append(f"- {len(off)} session(s) are not on the list the helper may touch (allow_names and the deny "
                       f"lists in restart.json): {', '.join(off[:12])}{', ...' if len(off) > 12 else ''}")
    if d["behind_new"]:
        out += ["", f"Behind for less than a day, nothing to do yet: {len(d['behind_new'])} "
                    f"({', '.join(d['behind_new'][:6])}{', ...' if len(d['behind_new']) > 6 else ''})."]
    if d["unloaded"]:
        out += ["", f"{len(d['unloaded'])} session(s) have no working Pit Stop hook: they started before it was installed, "
                    f"or run hooks this release replaced ({', '.join(d['unloaded'][:6])}"
                    f"{', ...' if len(d['unloaded']) > 6 else ''}). "
                + ("The helper reloads them at a break (reload_plugins is on)." if d["reload_on"] else
                   "Switch on \"reload_plugins\" in restart.json, or type /reload-plugins in each.")]
    if not (d["waiting"] or d["stuck"]):
        out += ["", "Nothing needs you."]
    return "\n".join(out) + "\n"


def headline(d):
    """One line for notify_command, or '' when nothing needs a person."""
    parts = []
    if d["waiting"]:
        n, a = max(d["waiting"], key=lambda x: x[1])
        parts.append(f"{len(d['waiting'])} session(s) waiting for you ({n}, {hours(a)})")
    stuck = [x for x in d["stuck"] if x[3] != NOT_LISTED]
    if stuck:
        parts.append(f"{len(stuck)} cannot be updated")
    return ("pit-stop: " + "; ".join(parts) + f"; see {OUT}") if parts else ""


def refresh(sessions, newest, now, force=False, panes=None):
    """Called by the restart helper at the end of a live run: rewrite the file
    about once an hour, and tell notify_command when a NEW name appears on it.
    A list that only gets shorter is not news. Sessions the owner keeps off the
    helper's list are shown in the file but never announced. A hand run
    (--write) rewrites the file and leaves the announcing to the helper."""
    try:
        if not force and now - os.path.getmtime(OUT) < EVERY:
            return None
    except OSError:
        pass
    rab = _rab()
    d = collect(sessions, newest, now, panes)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp-pitstop"
    with open(tmp, "w") as f:
        f.write(render(d, now))
    os.replace(tmp, OUT)
    if not rab.LIVE:
        return d
    names = sorted({n for n, _ in d["waiting"]} | {n for n, _, _, why in d["stuck"] if why != NOT_LISTED})
    lastf = os.path.join(rab.BASE, "state", "needs-you.last")
    try:
        with open(lastf) as f:
            before = [x for x in f.read().split("\n") if x]
    except OSError:
        before = []
    if names != before:
        os.makedirs(os.path.dirname(lastf), exist_ok=True)
        with open(lastf, "w") as f:
            f.write("\n".join(names))
        if set(names) - set(before) and headline(d):
            rab.notify(headline(d))
    return d


def main():
    now = time.time()
    sessions, _ = pc.live_sessions()
    newest = pc.newest_installed_version()
    try:
        panes = _rab().panes()
    except Exception:
        panes = None
    if "--write" in sys.argv:
        d = refresh(sessions, newest, now, force=True, panes=panes)
    else:
        d = collect(sessions, newest, now, panes)
    sys.stdout.write(render(d, now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
