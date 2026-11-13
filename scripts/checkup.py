#!/usr/bin/env python3
"""Pit Stop checkup: the state of every Claude Code session on this computer, in one report.

  checkup.py                 print the report, write a page with charts, keep a snapshot
  checkup.py --days 7        look back 7 days instead of 14
  checkup.py --share         the page without project or session names (to show others)
  checkup.py --no-save       print only; write no page and no snapshot

What it reads, all on this computer: the list of running sessions, each
session's version, and the history Claude Code keeps for each project folder
(counts, sizes and times only, never what was said). Nothing is sent anywhere.
No AI model is called.

What it reports:
  - sessions waiting on you (a question or permission prompt on screen 2 hours or more)
  - sessions behind the newest Claude Code installed here, and for how long
  - sessions that run without Pit Stop's hooks yet (they load them at their next start)
  - per project: automatic refreshes ("compactions") and how many landed in the
    middle of a task, re-reads after a refresh, cold wake-ups, the refresh point
    in force and the one its history suggests, and the MODELLED cached-read cost
    of each (an estimate, at Anthropic list prices for the model it last used)
  - memory indexes past the 200 lines Claude Code loads
  - what Pit Stop does about each

The first run keeps a baseline (~/.claude/pit-stop/checkup/baseline.json).
A run 6 or more days later adds "Since your first checkup": the same numbers
then and now, from this computer's own history.
"""
import argparse
import html
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402
import optimize as op  # noqa: E402

DIR = os.path.join(pc.STATE_DIR, "checkup")
BASELINE = os.path.join(DIR, "baseline.json")
WAITING_AFTER = 2 * 3600
COMPARE_AFTER = 6 * 86400
# list price per million cached-input tokens read, by model (as published 2026-11-03)
READ_PRICE = [("fable-5-2", 0.20), ("fable-5-1", 0.25), ("opus-5-5", 0.20), ("opus-5", 0.50),
              ("sonnet-5-5", 0.10), ("sonnet-5", 0.20), ("haiku-5-5", 0.01)]
MODEL_RE = re.compile(rb'"model"\s*:\s*"(claude-[a-z0-9.-]{3,40})"')


def last_model(folder):
    """The model a project's newest session last used (from the tail of its newest transcript), or None."""
    files = pc.transcripts(folder, 30)
    if not files:
        return None
    try:
        with open(files[-1], "rb") as f:
            f.seek(max(0, os.path.getsize(files[-1]) - 400_000))
            found = MODEL_RE.findall(f.read())
        return found[-1].decode() if found else None
    except OSError:
        return None


def read_price(model):
    for k, v in READ_PRICE:
        if model and k in model:
            return v
    return None


def folder_stats(folder, since, days, budget=None):
    """One project's numbers since SINCE (a time), from its own history. Pure but for reading it."""
    m = op.analyze(folder, days)
    log = [r for r in m.get("compaction_log") or [] if isinstance(r, list) and len(r) == 3 and r[0] >= since]
    auto = [r for r in log if r[2] != "manual"]
    kinds = {k: sum(1 for r in auto if r[1] == k) for k in ("mid_task", "prompt", "idle", "instant")}
    rr = [r for r in m.get("compaction_rereads") or [] if isinstance(r, list) and len(r) == 4 and r[0] >= since]
    checks, hits = sum(r[2] for r in rr), sum(r[3] for r in rr)
    out = {"folder": folder, "compactions": len(auto), "kinds": kinds,
           "reread": round(hits / checks, 2) if checks >= 10 else None,
           "cold_wakes_per_day": m.get("cold_wakes_per_day"), "reads_per_day": m.get("cache_read_per_day"),
           "window": m.get("current_window"), "idle_point": m.get("current_idle_point"),
           "fitted_window": None, "model": None, "saving_tokens_day": None, "saving_usd_month": None,
           "memory_lines": 0}
    try:
        p = pc.memory_index(folder)
        out["memory_lines"] = open(p).read().count("\n") if os.path.exists(p) else 0
    except OSError:
        pass
    if m["compactions"] >= 2:
        try:
            rec = op.recommend(m, budget=budget)
            out["fitted_window"] = rec["window"]
            share = op.read_saving(m.get("current_window"), rec["window"])
            if share and share > 0 and m.get("cache_read_per_day"):
                out["saving_tokens_day"] = int(share * m["cache_read_per_day"])
                out["model"] = last_model(folder)
                price = read_price(out["model"])
                if price is not None:
                    out["saving_usd_month"] = round(out["saving_tokens_day"] * price / 1e6 * 30)
        except Exception:
            pass
    return out


def session_rows(sessions, newest, now):
    rows = []
    for s in sessions:
        ver, st = s.get("version"), s.get("status")
        behind_s = None
        if newest and ver and pc.vkey(ver) < pc.vkey(newest):
            since = pc.behind_since(ver)
            behind_s = max(0, now - since) if since else 0
        age = now - s["statusUpdatedAt"] / 1000 if isinstance(s.get("statusUpdatedAt"), (int, float)) else None
        rows.append({"name": s.get("name") or "(no name)", "folder": os.path.abspath(s.get("cwd") or "."),
                     "version": ver, "behind_s": behind_s,
                     "waiting_s": age if st == "waiting" and age is not None and age >= WAITING_AFTER else None})
    return rows


def hookless(sessions, now):
    """Names of sessions that do not run this release's Pit Stop hooks (none, or an older set), from needs_you."""
    try:
        import needs_you as ny
        return ny.collect(sessions, pc.newest_installed_version(), now).get("unloaded") or []
    except Exception:
        return []


def collect(days=14, now=None, sessions=None):
    now = now or time.time()
    if sessions is None:
        sessions, _ = pc.live_sessions()
    newest = pc.newest_installed_version()
    since = now - days * 86400
    budget = pc.config().get("budget")
    rows = session_rows(sessions, newest, now)
    folders = sorted({r["folder"] for r in rows if os.path.isdir(r["folder"])})
    stats = []
    for f in folders:
        try:
            stats.append(folder_stats(f, since, days, budget))
        except Exception as e:   # one unreadable project never stops the report
            stats.append({"folder": f, "error": type(e).__name__, "compactions": 0,
                          "kinds": {"mid_task": 0, "prompt": 0, "idle": 0, "instant": 0}})
    k = {x: sum(s["kinds"][x] for s in stats) for x in ("mid_task", "prompt", "idle", "instant")}
    judged = k["mid_task"] + k["prompt"] + k["idle"]
    cfg = pc.config()
    return {"at": now, "days": days, "newest": newest, "sessions": rows, "folders": stats,
            "hookless": hookless(sessions, now),
            "totals": {"sessions": len(rows), "on_newest": sum(1 for r in rows if r["behind_s"] is None and r["version"]),
                       "behind": sum(1 for r in rows if r["behind_s"] is not None),
                       "waiting": sum(1 for r in rows if r["waiting_s"] is not None),
                       "compactions": sum(s["compactions"] for s in stats), "kinds": k,
                       "mid_task_share": round(k["mid_task"] / judged, 2) if judged else None,
                       "saving_usd_month": sum(s.get("saving_usd_month") or 0 for s in stats),
                       "saving_tokens_day": sum(s.get("saving_tokens_day") or 0 for s in stats)},
            "pitstop": {"auto_tune": cfg.get("auto_tune") is True, "fleet": pc.fleet_active()}}


def label(folder):
    """A project's short name: its folder's own name ("~" for the home folder)."""
    f = os.path.abspath(folder)
    return "~" if f == os.path.abspath(pc.HOME) else os.path.basename(f) or f


def tokens(n):
    """A token count people can read at a glance: 1.6 billion, 640 million."""
    return f"{n / 1e9:.1f} billion" if n >= 1e9 else f"{n / 1e6:.0f} million"


def hours(sec):
    return f"{sec / 3600:.0f} hours" if sec < 72 * 3600 else f"{sec / 86400:.0f} days"


def pct(x):
    return "-" if x is None else f"{round(x * 100)}%"


def compare(base, cur):
    """'Since your first checkup' lines, or [] when the baseline is too new."""
    if not base or cur["at"] - base.get("at", cur["at"]) < COMPARE_AFTER:
        return []
    b, c = base.get("totals") or {}, cur["totals"]
    days = int((cur["at"] - base["at"]) // 86400)
    return [f"Since your first checkup {days} days ago:",
            f"- refreshes in the middle of a task: {pct(b.get('mid_task_share'))} then, {pct(c.get('mid_task_share'))} now",
            f"- sessions behind the newest Claude Code: {b.get('behind', '-')} then, {c['behind']} now",
            f"- sessions waiting on you for 2 hours or more: {b.get('waiting', '-')} then, {c['waiting']} now"]


def render_text(d, cmp_lines):
    t = d["totals"]
    out = [f"PIT STOP CHECKUP ({time.strftime('%a %Y-%m-%d %H:%M', time.localtime(d['at']))}, last {d['days']} days, your sessions on this computer only)",
           "",
           f"{t['sessions']} Claude Code sessions running; {t['on_newest']} on the newest version installed ({d['newest'] or 'not found'})."]
    waiting = sorted((r for r in d["sessions"] if r["waiting_s"] is not None), key=lambda r: -r["waiting_s"])
    if waiting:
        out += ["", "Waiting on you (a question or permission prompt on screen):"]
        out += [f"- {r['name']}: {hours(r['waiting_s'])}" for r in waiting]
    behind = sorted((r for r in d["sessions"] if r["behind_s"] is not None), key=lambda r: -r["behind_s"])
    if behind:
        out += ["", f"Behind the newest Claude Code ({d['newest']}):"]
        out += [f"- {r['name']}: on {r['version']}" + (f", for {hours(r['behind_s'])}" if r["behind_s"] else "") for r in behind]
    if d["hookless"]:
        out += ["", f"{len(d['hookless'])} session(s) do not run this release's Pit Stop hooks yet; they load them at their next start "
                    f"(or type /reload-plugins in each): {', '.join(d['hookless'][:8])}{', ...' if len(d['hookless']) > 8 else ''}"]
    k = t["kinds"]
    out += ["", f"Automatic refreshes (compactions) in the last {d['days']} days: {t['compactions']}"]
    if t["compactions"]:
        out.append(f"- in the middle of a task: {k['mid_task']} ({pct(t['mid_task_share'])} of those it could judge)")
        out.append(f"- as a new prompt arrived: {k['prompt']}; while idle: {k['idle']}; prepared ahead and swapped in: {k['instant']}")
    busy = sorted((s for s in d["folders"] if s.get("compactions")), key=lambda s: -s["compactions"])[:12]
    if busy:
        out += ["", "By project (most refreshes first):",
                f"  {'project':34} {'refreshes':>9} {'mid-task':>8} {'re-reads':>8} {'window':>7} {'fitted':>7} {'modelled saving':>16}"]
        for s in busy:
            judged = s["kinds"]["mid_task"] + s["kinds"]["prompt"] + s["kinds"]["idle"]
            mt = pct(s["kinds"]["mid_task"] / judged) if judged else "-"
            sav = (f"${s['saving_usd_month']:,}/month" if s.get("saving_usd_month") else
                   f"{s['saving_tokens_day'] / 1e6:.0f}M tok/day" if s.get("saving_tokens_day") else "-")
            out.append(f"  {label(s['folder'])[:34]:34} {s['compactions']:>9} {mt:>8} {pct(s.get('reread')):>8} "
                       f"{pc.fmt_k(s.get('window')):>7} {pc.fmt_k(s.get('fitted_window')):>7} {sav:>16}")
    if t["saving_usd_month"] > 0:
        out += ["", f"Modelled: moving every project to its fitted refresh point would cut cached reads by about "
                    f"{tokens(t['saving_tokens_day'])} tokens a day, about ${t['saving_usd_month']:,} a month at list prices. "
                    f"An estimate from your own history, not a measurement; daily tuning moves one step at a time and checks each step."]
    mem = [s for s in d["folders"] if s.get("memory_lines", 0) > pc.MEMORY_LOAD_LINES]
    if mem:
        out += ["", f"{len(mem)} project(s) have a memory index past the {pc.MEMORY_LOAD_LINES} lines Claude Code loads: "
                    f"run /pit-stop reading in each."]
    out += ["", "What Pit Stop does now:",
            "- holds an automatic refresh that arrives mid-task until the next break whenever it can (in every session with its hooks)",
            ("- daily tuning is ON: each project moves one step toward its fitted point and the step is measured"
             if d["pitstop"]["auto_tune"] else "- daily tuning is OFF: /pit-stop setup switches it on"),
            ("- fleet mode is ON: sessions behind are restarted onto the newest Claude Code at a break"
             if d["pitstop"]["fleet"] else "- sessions behind: type /restart in each at a break (or set up fleet mode, for tmux)")]
    if waiting:
        out.append("- sessions waiting on you: only you can answer them")
    if cmp_lines:
        out += [""] + cmp_lines
    return "\n".join(out) + "\n"


CSS = """:root{--bg:#f6f7f5;--fg:#1c2124;--mut:#5d676c;--ln:#d9dedc;--acc:#c2410c;--good:#1f7a55;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#15191b;--fg:#e8ecea;--mut:#9aa5a9;--ln:#313a3e;--acc:#f08a4b;--good:#4cc08d;--card:#1d2326}}
body{background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif;margin:0}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px;display:grid;gap:28px}
h1{font-size:30px;margin:0}h2{font-size:20px;margin:0 0 8px}p{margin:0}.mut{color:var(--mut)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--ln);border-radius:6px;padding:14px}
.tile b{display:block;font-size:30px}.bad b{color:var(--acc)}.ok b{color:var(--good)}
.card{background:var(--card);border:1px solid var(--ln);border-radius:6px;padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--ln);white-space:nowrap}th:first-child,td:first-child{text-align:left}
svg text{fill:var(--fg);font:12px -apple-system,"Segoe UI",Helvetica,Arial,sans-serif}"""


def render_html(d, cmp_lines, share=False):
    t, e = d["totals"], html.escape
    names = {}

    def nm(x, kind):
        if not share:
            return label(x) if kind == "Project" else x
        return names.setdefault((kind, x), f"{kind} {sum(1 for k in names if k[0] == kind) + 1}")
    k = t["kinds"]
    total = sum(k.values()) or 1
    bars, x = [], 0
    for key, what, var in (("mid_task", "mid-task", "--acc"), ("prompt", "as a prompt arrived", "--mut"),
                            ("idle", "while idle", "--good"), ("instant", "prepared ahead", "--ln")):
        w = 760 * k[key] / total
        if w:
            bars.append(f'<rect x="{x:.1f}" y="10" width="{w:.1f}" height="34" style="fill:var({var})"/>')
            if w > 70:
                bars.append(f'<text x="{x + 6:.1f}" y="32">{what} {k[key]}</text>')
        x += w
    rows = []
    for s in sorted((s for s in d["folders"] if s.get("compactions")), key=lambda s: -s["compactions"])[:20]:
        judged = s["kinds"]["mid_task"] + s["kinds"]["prompt"] + s["kinds"]["idle"]
        mt = s["kinds"]["mid_task"] / judged if judged else 0
        rows.append(f"<tr><td>{e(nm(s['folder'], 'Project'))}</td><td>{s['compactions']}</td>"
                    f"<td><svg width='120' height='12'><rect width='120' height='12' style='fill:var(--ln)'/>"
                    f"<rect width='{120 * mt:.0f}' height='12' style='fill:var(--acc)'/></svg> {pct(mt) if judged else '-'}</td>"
                    f"<td>{pct(s.get('reread'))}</td><td>{pc.fmt_k(s.get('window'))}</td><td>{pc.fmt_k(s.get('fitted_window'))}</td>"
                    f"<td>{('$' + format(s['saving_usd_month'], ',') + '/mo') if s.get('saving_usd_month') else '-'}</td></tr>")
    people = "".join(f"<li>{e(nm(r['name'], 'Session'))}: {hours(r['waiting_s'])}</li>"
                     for r in d["sessions"] if r["waiting_s"] is not None)
    behind = "".join(f"<li>{e(nm(r['name'], 'Session'))}: on {e(r['version'] or '?')}"
                     + (f", for {hours(r['behind_s'])}" if r["behind_s"] else "") + "</li>"
                     for r in sorted(d["sessions"], key=lambda r: -(r["behind_s"] or 0)) if r["behind_s"] is not None)
    saving = (f"<p><b>Modelled:</b> moving every project to its fitted refresh point would cut cached reads by about "
              f"{tokens(t['saving_tokens_day'])} tokens a day, about ${t['saving_usd_month']:,} a month at list prices. "
              f"An estimate from your own history, not a measurement.</p>" if t["saving_usd_month"] > 0 else "")
    since = ("<div class='card'><h2>Since your first checkup</h2><ul>" + "".join(f"<li>{e(x[2:])}</li>" for x in cmp_lines[1:])
             + "</ul></div>") if cmp_lines else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pit Stop Checkup</title><style>{CSS}</style></head><body><main>
<div><h1>Pit Stop checkup</h1><p class="mut">{time.strftime('%A %Y-%m-%d %H:%M', time.localtime(d['at']))} · last {d['days']} days ·
your sessions on this computer only · counts and times, never what was said · nothing was sent anywhere</p></div>
<div class="tiles">
<div class="tile ok"><b>{t['on_newest']}/{t['sessions']}</b>sessions on the newest Claude Code</div>
<div class="tile {'bad' if t['behind'] else 'ok'}"><b>{t['behind']}</b>behind on an older version</div>
<div class="tile {'bad' if t['waiting'] else 'ok'}"><b>{t['waiting']}</b>waiting on you 2 hours or more</div>
<div class="tile {'bad' if (t['mid_task_share'] or 0) >= 0.2 else 'ok'}"><b>{pct(t['mid_task_share'])}</b>of refreshes landed mid-task</div>
</div>
<div class="card"><h2>Where your {t['compactions']} automatic refreshes landed</h2>
<svg viewBox="0 0 760 54" width="100%" role="img" aria-label="Where refreshes landed">{''.join(bars)}</svg>
<p class="mut">A refresh in the middle of a task summarises half-done work. Pit Stop holds it until the next break whenever it can.</p></div>
{since}
{'<div class="card"><h2>Waiting on you</h2><ul>' + people + '</ul></div>' if people else ''}
{'<div class="card"><h2>Behind the newest Claude Code (' + e(d['newest'] or '') + ')</h2><ul>' + behind + '</ul></div>' if behind else ''}
<div class="card"><h2>By project</h2><table><tr><th>Project</th><th>Refreshes</th><th>Mid-task</th><th>Re-reads after</th>
<th>Window now</th><th>Fitted</th><th>Modelled saving</th></tr>{''.join(rows)}</table>{saving}</div>
<p class="mut">Made by Windstorm Pit Stop (github.com/TheWindstorm/windstorm-pit-stop). Pit Stop is an independent plugin. It is not made or
endorsed by Anthropic. Prices: Anthropic list API prices as published on November 3, 2026.</p>
</main></body></html>
"""


def save(d, page, share_page):
    os.makedirs(DIR, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d", time.localtime(d["at"]))
    snap = {k: d[k] for k in ("at", "days", "newest", "totals")}
    pc.write_json_atomic(os.path.join(DIR, f"{stamp}.json"), snap)
    if not os.path.exists(BASELINE):
        pc.write_json_atomic(BASELINE, snap)
    paths = []
    for name, body in (("checkup.html", page), ("checkup-share.html", share_page)):
        if body:
            p = os.path.join(DIR, name)
            with open(p, "w") as f:
                f.write(body)
            paths.append(p)
    return paths


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--share", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    try:
        os.nice(10)
    except Exception:
        pass
    days = max(1, min(60, a.days))
    base = pc.read_json(BASELINE, None)
    d = collect(days)
    cmp_lines = compare(base if isinstance(base, dict) else None, d)
    sys.stdout.write(render_text(d, cmp_lines))
    if not a.no_save:
        paths = save(d, render_html(d, cmp_lines), render_html(d, cmp_lines, share=True) if a.share else None)
        print("\nThe same report with charts: " + " and ".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
