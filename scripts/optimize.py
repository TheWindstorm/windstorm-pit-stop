#!/usr/bin/env python3
"""Pit Stop Optimizer: read a folder's session history and recommend its pit stop.

  optimize.py [--project DIR] [--days 14] [--context-limit 1000000|200000]
  optimize.py ... --json        machine-readable result
  optimize.py ... --apply       write the two points (settings.local.json env
                                CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS and
                                CLAUDE_CODE_AUTO_COMPACT_WINDOW) and the
                                profile (~/.claude/pit-stop/folders/); a
                                backup of the settings file first
  optimize.py ... --lanes       also work out each recent session of the
                                folder by itself; with --apply, write one lane
                                profile per session (~/.claude/pit-stop/lanes/)
  optimize.py ... --max-steps N move each point at most N steps from where it
                                is now (the daily tune uses 1)
  optimize.py ... --budget economy|balanced|roomy
                                how hard to hold down usage (default: "budget"
                                in ~/.claude/pit-stop/config.json, else
                                balanced). A folder that reads a lot from the
                                cache every day gets a lower window; roomy
                                tunes for memory only.

Two points per folder:
  - the IDLE POINT: Claude Code compacts a session by itself once it has gone
    quiet, shortly before its prompt cache expires, if its context is at least
    this big. That is the pit stop at a break. (Claude Code's default is 200K
    and it never goes under 100K.)
  - the WINDOW: the hard point. A session that never goes quiet compacts here,
    at the next break the hook finds, or at the ceiling at the latest.

What it measures (counts only; message text is never printed):
  - compaction cycles: peak context before each compaction, hours and turns per cycle
  - re-reads after compaction: files read again in the first steps after a
    compaction that were already read in the cycle before (a sign the
    compaction point was too early and the session lost what it needed)
  - cache reuse: share of input served from the prompt cache
  - idle gaps between prompts (long gaps = cold cache = long contexts cost more),
    and the tokens paid again on those cold wake-ups
  - topic change: how much of the set of folders in play turns over from one
    stretch of work to the next (paths are compared, never printed)
  - when compactions happened: while idle, as a prompt arrived, or mid-task
  - work mix by tool: editing, shell, reading, messaging, web
  - trouble signals: tool errors

What it recommends:
  - idle point, window and hard ceiling: applied with --apply, within bounds
  - model and effort: SUGGESTIONS only, never applied. A person decides those,
    because they change quality and cost.
These are heuristics, not guarantees. Re-run now and then; the profile records why.
"""
import argparse
import json
import re
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402

EDIT = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL = {"Bash", "BashOutput", "KillShell", "Monitor"}
READ = {"Read", "Grep", "Glob", "LS"}
MSG = {"SendMessage", "ListAgents", "Agent", "Task"}
WEB = {"WebFetch", "WebSearch"}
REREAD_WINDOW = 15  # tool calls after a compaction
SHELL_READ = re.compile(r"^\s*(cat|sed|head|tail|less|grep|rg|ugrep|ls|find|wc|jq|awk|diff)\b")
SHELL_MSG = re.compile(r"\b(gh|ssh|curl|git)\b")
PATHLIKE = re.compile(r"(?:^|\s)([~/.][^\s'\"|;&<>]*\.[A-Za-z0-9]{1,6})(?=$|[\s'\"|;&<>])")
MIN_PROMPTS_FOR_SUGGESTIONS = 50
DRIFT_WINDOW = 30       # file-touching tool calls per stretch of work
COLD_GAP = 3600         # a prompt after this long finds the prompt cache cold
# planned re-reading after a compaction is not lost context
ROUTINE_READ = re.compile(r"(?i)(claude|memory|todo|start[-_]here|handoff|readme|status|changelog)[^/]*\.md$")


def drift_of(paths):
    """Topic change in one session: split the folders it worked in, in order,
    into stretches of DRIFT_WINDOW file-touching steps, and return how
    different each stretch's set of folders is from the one before
    (0 = the same folders, 1 = all new)."""
    chunks = [set(paths[i:i + DRIFT_WINDOW]) for i in range(0, len(paths) - DRIFT_WINDOW + 1, DRIFT_WINDOW)]
    return [1 - len(a & b) / len(a | b) for a, b in zip(chunks, chunks[1:]) if a | b]


def analyze(project, days, only=None):
    files = [only] if only else pc.transcripts(project, days)
    cycles, cur = [], None
    prompts, gaps = [], []
    tools = {"edit": 0, "shell": 0, "read": 0, "msg": 0, "web": 0, "other": 0}
    errors = tool_results = 0
    cache_read = cache_new = fresh_in = 0
    max_ctx = 0
    rereads = reread_checks = 0
    by_comp = []           # one [time, tokens before, re-read checks, re-reads] per automatic compaction
    sessions = boundaries = 0
    outs = []
    drifts, cold_tokens, cold_wakes, t_first, t_last = [], 0, 0, None, None
    when = {"idle": 0, "prompt": 0, "mid_task": 0, "instant": 0}
    comp_log = []          # one [time, kind, trigger] per compaction, for /pit-stop checkup's own time windows
    calls, read_once, seen_ids = 0, 0, set()   # one model call can span several transcript rows: count it once
    busiest = None                             # the most cache reads per day of any one session with a day of history
    for path in files:
        sessions += 1
        prev_reads, cur_reads, after_compact, comp_rec = set(), set(), None, None
        touched, cold_pending, last_kind, last_ts = [], False, None, None
        s_read, s_first, s_last = 0, None, None
        cur = {"peak": 0, "turns": 0, "start": None, "end": None}
        for e in pc.iter_entries(path):
            ts = pc.parse_ts(e.get("timestamp"))
            if ts:
                t_first, t_last = t_first or ts, ts
            if pc.is_compact_boundary(e):
                boundaries += 1
                quiet = ts - last_ts if ts and last_ts else 0
                # "instant": Claude Code wrote the summary ahead of time, in the
                # background, and swapped it in here in milliseconds with the
                # newest messages kept word for word. Nobody waits and the work
                # in hand is kept, so it is not counted as a mid-task compaction.
                kind = ("instant" if (e.get("compactMetadata") if isinstance(e.get("compactMetadata"), dict) else {}).get("precomputed")
                        else "mid_task" if last_kind == "tool" else "idle" if last_kind == "reply" and quiet >= 1800
                        else "prompt")
                when[kind] += 1
                trig = (e.get("compactMetadata") if isinstance(e.get("compactMetadata"), dict) else {}).get("trigger")
                if ts:
                    comp_log.append([ts, kind, trig if trig in ("auto", "manual") else None])
                if cur["turns"]:
                    cur["compacted"] = True
                    cycles.append(cur)
                cur = {"peak": 0, "turns": 0, "start": ts, "end": ts}
                prev_reads, cur_reads, after_compact = cur_reads, set(), 0
                md = e.get("compactMetadata") if isinstance(e.get("compactMetadata"), dict) else {}
                comp_rec = None
                if isinstance(md.get("preTokens"), int) and md.get("trigger") == "auto" and ts:
                    comp_rec = [ts, md["preTokens"], 0, 0]
                    by_comp.append(comp_rec)
                continue
            t = e.get("type")
            if t == "user" and not e.get("isSidechain"):
                kinds = pc.content_kinds(e)
                if "tool_result" in kinds:
                    for x in pc.message(e).get("content") or []:
                        if isinstance(x, dict) and x.get("type") == "tool_result":
                            tool_results += 1
                            errors += 1 if x.get("is_error") else 0
                elif ts:
                    if prompts:
                        gaps.append(ts - prompts[-1])
                        if last_ts and ts - last_ts > COLD_GAP:
                            cold_pending = True
                            cold_wakes += 1
                    prompts.append(ts)
                last_kind, last_ts = ("tool" if "tool_result" in kinds else "prompt"), ts or last_ts
            if t == "assistant" and not e.get("isSidechain"):
                u = pc.usage(e)
                total = pc.usage_total(e)
                mid = pc.message(e).get("id")
                mid = mid if isinstance(mid, str) else None      # an odd row never stops the read
                if total and (mid is None or mid not in seen_ids):
                    seen_ids.add(mid)
                    calls += 1
                    read_once += pc.num(u.get("cache_read_input_tokens"))
                    s_read += pc.num(u.get("cache_read_input_tokens"))
                    if ts:
                        s_first, s_last = s_first or ts, ts
                if total:
                    cur["peak"] = max(cur["peak"], total)
                    max_ctx = max(max_ctx, total)
                    cur["turns"] += 1
                    cache_read += pc.num(u.get("cache_read_input_tokens"))
                    cache_new += pc.num(u.get("cache_creation_input_tokens"))
                    fresh_in += pc.num(u.get("input_tokens"))
                    outs.append(pc.num(u.get("output_tokens")))
                    if cold_pending:  # the first answer after a cold wake-up pays for the context again
                        cold_tokens += pc.num(u.get("cache_creation_input_tokens")) + pc.num(u.get("input_tokens"))
                        cold_pending = False
                last_kind = "tool" if pc.tool_uses(e) else "reply" if "text" in pc.content_kinds(e) else last_kind
                last_ts = ts or last_ts
                cur["start"] = cur["start"] or ts
                cur["end"] = ts or cur["end"]
                for tu in pc.tool_uses(e):
                    n = tu.get("name") or ""
                    inp = tu.get("input") if isinstance(tu.get("input"), dict) else {}
                    k = ("edit" if n in EDIT else "shell" if n in SHELL else "read" if n in READ
                         else "msg" if n in MSG else "web" if n in WEB else "other")
                    paths = []
                    if n in READ and (inp.get("file_path") or inp.get("path")):
                        paths = [inp.get("file_path") or inp.get("path")]
                    if (n in READ or n in EDIT) and inp.get("file_path"):
                        touched.append(os.path.dirname(str(inp["file_path"])))
                    if n == "Bash":
                        cmd = str(inp.get("command") or "")
                        found = PATHLIKE.findall(cmd)[:3]
                        touched.extend(os.path.dirname(x) for x in found[:1])
                        if SHELL_READ.match(cmd):
                            k = "read"
                            paths = found
                        elif SHELL_MSG.search(cmd.split("|")[0]):
                            k = "msg"
                    tools[k] += 1
                    for fp in [x for x in paths if not ROUTINE_READ.search(x)]:
                        if after_compact is not None and after_compact < REREAD_WINDOW:
                            reread_checks += 1
                            rereads += 1 if fp in prev_reads else 0
                            if comp_rec is not None:
                                comp_rec[2] += 1
                                comp_rec[3] += 1 if fp in prev_reads else 0
                        cur_reads.add(fp)
                    if after_compact is not None:
                        after_compact += 1
        if cur["turns"]:
            cycles.append(cur)
        drifts.extend(drift_of(touched))
        if s_first and s_last and s_last - s_first >= 86400:
            busiest = max(busiest or 0, int(s_read / ((s_last - s_first) / 86400)))
    compacted = [c for c in cycles if c.get("compacted")]
    hours = [(c["end"] - c["start"]) / 3600 for c in compacted if c["start"] and c["end"]]
    span_days = max((t_last - t_first) / 86400, 1 / 24) if t_first and t_last else None
    total_in = cache_read + cache_new + fresh_in
    ntools = sum(tools.values()) or 1
    return {
        "project": os.path.abspath(project), "days": days, "sessions": sessions,
        "transcripts": len(files), "compactions": boundaries,
        "max_context": max_ctx,
        "median_peak_before_compaction": int(statistics.median([c["peak"] for c in compacted])) if compacted else None,
        "median_hours_per_cycle": round(statistics.median(hours), 1) if hours else None,
        "reread_after_compaction": round(rereads / reread_checks, 2) if reread_checks else None,
        "cache_reuse": round(cache_read / total_in, 2) if total_in else None,
        "median_gap_minutes": round(statistics.median(gaps) / 60, 1) if gaps else None,
        "long_gap_share": round(sum(1 for g in gaps if g > 3600) / len(gaps), 2) if gaps else None,
        "cold_wakes_per_day": round(cold_wakes / span_days, 1) if span_days else None,
        "cold_wake_tokens_per_day": int(cold_tokens / span_days) if span_days else None,
        "topic_change": round(statistics.median(drifts), 2) if len(drifts) >= 3 else None,
        # a rate per day from less than a day of history would be a guess: then there is none
        "calls_per_day": int(calls / span_days) if span_days and span_days >= 1 else None,
        "cache_read_per_day": int(read_once / span_days) if span_days and span_days >= 1 else None,
        # usage is judged by the folder's busiest single session: the window is a per-session limit, and a
        # folder shared by many light sessions is not a heavy one
        "busiest_session_read_per_day": busiest,
        "compactions_when": when,
        "compaction_log": comp_log,
        "compaction_rereads": by_comp,
        "work_mix": {k: round(v / ntools, 2) for k, v in tools.items()},
        "tool_error_rate": round(errors / tool_results, 3) if tool_results else None,
        "prompts": len(prompts),
        "median_output_tokens": int(statistics.median(outs)) if outs else None,
        "current_window": current_window(project),
        "current_idle_point": pc.env_int(project, pc.IDLE_ENV),
    }


def start_of(window):
    """Where Claude Code starts an automatic compaction for WINDOW (about WINDOW_MARGIN under it)."""
    return window - WINDOW_MARGIN if window and window > WINDOW_MARGIN else None


def at_point(m, lo=None, hi=None, since=None, until=None):
    """How the automatic compactions with tokens-before in [LO, HI) and a time
    in [SINCE, UNTIL) went: (count, re-read checks, re-read rate or None).
    The caller picks bounds that only one window can have produced (see
    check_last_move): a compaction's size alone does not tell its window,
    because a Pit Stop hold can carry it well past where the window starts it,
    and a session keeps the window it started with until its next restart."""
    rows = [r for r in m.get("compaction_rereads") or []
            if isinstance(r, list) and len(r) == 4
            and (lo is None or r[1] >= lo) and (hi is None or r[1] < hi)
            and (since is None or r[0] >= since) and (until is None or r[0] < until)]
    checks, hits = sum(r[2] for r in rows), sum(r[3] for r in rows)
    return len(rows), checks, (round(hits / checks, 2) if checks else None)


def clean_history(h):
    """A tune history as autotune.py writes it, with anything else dropped:
    {"moves": [{"at": number, "from": int or None, "to": int}], "floor": {"value": int, "until": number}}.
    A hand-edited or damaged file never stops a folder's tuning. Pure."""
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)          # noqa: E731
    whole = lambda x: isinstance(x, int) and not isinstance(x, bool) and x > 0        # noqa: E731
    h = h if isinstance(h, dict) else {}
    moves = [x for x in (h.get("moves") if isinstance(h.get("moves"), list) else [])
             if isinstance(x, dict) and num(x.get("at")) and whole(x.get("to"))
             and (x.get("from") is None or whole(x.get("from")))]
    out = {"moves": moves}
    fl = h.get("floor")
    if isinstance(fl, dict) and whole(fl.get("value")) and num(fl.get("until")):
        out["floor"] = {"value": fl["value"], "until": fl["until"]}
    return out


def current_window(project):
    return pc.env_int(project, pc.WINDOW_ENV)


def toward(now, target, step, max_steps):
    """Move from NOW toward TARGET by at most MAX_STEPS steps (None = all the way)."""
    if now is None or max_steps is None:
        return target
    lim = step * max_steps
    return max(now - lim, min(now + lim, target))


BUDGETS = ("economy", "balanced", "roomy")
HEAVY, VERY_HEAVY = 100_000_000, 250_000_000   # tokens read from the cache per day, in one folder
SUMMARY_KEEPS, WINDOW_MARGIN = 20_000, 33_000  # what a compaction leaves; how far under the window it starts


def usage_cap(m, budget, big, rr):
    """(cap or None, class, sentence) for the window from how much this folder
    reads. Almost all of a long session's usage is the context being read
    again from the cache on every model call, so usage follows context size
    times calls. The window is the one setting that moves context size.
      roomy     no cap: tune for memory only (the 0.2.x behaviour)
      balanced  a heavy folder (100M+ tokens read a day) is capped at 500K, a
                very heavy one (250M+) at 400K
      economy   every folder is capped at 400K, a heavy one at 300K, and no
                window is raised (unless sessions re-read a lot, see below)
    "Reads a day" is the folder's busiest single session, with at least a day
    of history. A folder whose sessions re-read a lot after compacting (30%+)
    gets one step more: compacting it harder would cost reads of its own. A
    capped folder's window is also never raised above where it is now."""
    reads = m.get("busiest_session_read_per_day")
    if not big or budget == "roomy":
        return None, None, None
    if reads is None:
        # No session here has a day of history, so there is no usage to judge. Balanced then has
        # nothing to cap; economy caps every folder whatever it reads.
        if budget != "economy":
            return None, None, None
        return 400_000, None, "usage: under the 'economy' budget every window is capped at 400K"
    cls = "very heavy" if reads >= VERY_HEAVY else "heavy" if reads >= HEAVY else "light"
    cap = {"balanced": {"very heavy": 400_000, "heavy": 500_000},
           "economy": {"very heavy": 300_000, "heavy": 300_000, "light": 400_000}}[budget].get(cls)
    if cap is None:
        return None, cls, None
    if rr is not None and rr >= 0.30:
        cap += 100_000
    return cap, cls, (f"usage: this folder's busiest session reads about {reads / 1e6:.0f}M tokens a day from the "
                      f"cache ({cls}); under the '{budget}' budget its window is capped at {pc.fmt_k(cap)}")


def read_saving(old, new):
    """Rough share of cache reads saved by moving the window from OLD to NEW:
    between compactions the context runs from what a summary keeps up to the
    window, so the average context is about halfway. Negative when the window
    goes UP: a raise costs reads, and the answer says how much."""
    if not old or not new or new == old or old <= WINDOW_MARGIN or new <= WINDOW_MARGIN:
        return 0.0
    return round(1 - (SUMMARY_KEEPS + new - WINDOW_MARGIN) / (SUMMARY_KEEPS + old - WINDOW_MARGIN), 2)


EVIDENCE_COMPACTIONS, EVIDENCE_CHECKS = 3, 10     # what a move needs at its new point before the next one
EVIDENCE_WAIT = 14 * 86400                        # a move with no evidence after this long no longer holds things up
UNDO_FLOOR = 14 * 86400                           # after an undone lowering, not lower again for this long


def check_last_move(m, history, window, now):
    """The tuner's own feedback loop: did its last move help? HISTORY is what
    autotune.py keeps for the folder (see clean_history). Returns (window, why
    lines, floor or None).
    Which compactions count. A session keeps the window it started with, so
    after a move both windows run for a while. Only compactions that the NEW
    window alone can have produced count for it: after the move, and for a
    lowering, smaller than where the old window would start one (for a raise,
    or a first move, at least where the new one starts). The old window's
    record is the compactions between the move before and this one.
      - Too little evidence for the new point yet (fewer than 3 such automatic
        compactions, or under 10 re-read checks): stay where it is and wait, so
        one step is measured before the next is taken (at most 14 days).
      - It LOWERED the window and the sessions now re-read much more after a
        compaction (30% or more, and 15 points above the old point when that
        has a record): it cost context, so it is undone, and the window is not
        lowered again for 14 days.
      - Otherwise it held up, and the usual recommendation goes ahead.
    A move the user has since overridden (the window is not where the move
    left it) is not judged. Pure, for tests."""
    h = clean_history(history)
    moves = h["moves"]
    last, cur, why, floor = (moves[-1] if moves else None), m.get("current_window"), [], None
    if last and cur == last["to"] and last["from"] != last["to"]:
        new_start, old_start = start_of(last["to"]), start_of(last["from"])
        lowered = bool(last["from"]) and last["to"] < last["from"]
        lo, hi = new_start * 0.95, (old_start * 0.95 if lowered else None)
        n_new, ch_new, r_new = at_point(m, lo, hi, since=last["at"])
        prev_at = moves[-2]["at"] if len(moves) > 1 else None
        n_old, _, r_old = (at_point(m, old_start * 0.95, None, since=prev_at, until=last["at"]) if old_start
                           else (0, 0, None))
        if n_new < EVIDENCE_COMPACTIONS or ch_new < EVIDENCE_CHECKS:
            if now - last["at"] < EVIDENCE_WAIT and window != cur:
                return cur, [f"waiting for evidence: {n_new} automatic compaction(s) at {pc.fmt_k(cur)} since the move "
                             f"from {pc.fmt_k(last['from'])} (each move is measured before the next; needs "
                             f"{EVIDENCE_COMPACTIONS})"], None
        elif (lowered and r_new is not None and r_new >= 0.30
              and (r_old is None or n_old < EVIDENCE_COMPACTIONS or r_new >= r_old + 0.15)):
            floor = {"value": last["from"], "until": now + UNDO_FLOOR}
            return last["from"], [f"undo: at {pc.fmt_k(cur)}, {int(r_new * 100)}% of early reads after a compaction "
                                  f"re-read files from before it" + (f" ({int(r_old * 100)}% at {pc.fmt_k(last['from'])})"
                                                                      if r_old is not None and n_old >= EVIDENCE_COMPACTIONS else "")
                                  + f": the lower point cost context, so back to {pc.fmt_k(last['from'])}, and not lower "
                                  f"again for 14 days"], floor
        else:
            why.append(f"the last move ({pc.fmt_k(last['from'])} -> {pc.fmt_k(cur)}) held up: {n_new} compactions there"
                       + (f", {int(r_new * 100)}% re-reads after them" if r_new is not None else ""))
    fl = h.get("floor") or {}
    if fl and fl["until"] > now and window < fl["value"]:
        why.append(f"not lower than {pc.fmt_k(fl['value'])} until {time.strftime('%Y-%m-%d', time.gmtime(fl['until']))} "
                   f"(an earlier lowering was undone)")
        window = fl["value"]
    return window, why, floor


def recommend(m, context_limit=None, max_steps=None, budget=None, history=None, now=None):
    budget = budget or pc.config().get("budget") or "balanced"
    if budget not in BUDGETS:
        budget = "balanced"
    limit = context_limit or (1_000_000 if m["max_context"] > 200_000 else 200_000)
    big = limit >= 1_000_000
    step = 100_000 if big else 20_000
    window = 500_000 if big else 150_000
    lo, hi = (300_000, 700_000) if big else (100_000, 170_000)
    why = [f"context limit {pc.fmt_k(limit)}: start at {pc.fmt_k(window)}"]
    rr, gap, mix, drift = m["reread_after_compaction"], m["long_gap_share"], m["work_mix"], m.get("topic_change")
    enough = m["compactions"] >= 2
    if rr is not None and rr >= 0.30:
        window += step
        why.append(f"{int(rr*100)}% of early reads after a compaction re-read files from before it: compact later")
    elif rr is not None and rr <= 0.10 and enough:
        why.append(f"few re-reads after compaction ({int(rr*100)}%): the current point loses little")
    if gap is not None and gap >= 0.40:
        window -= step
        why.append(f"{int(gap*100)}% of prompts come after an hour+ idle (cold cache): a long context costs more each time")
    if mix["msg"] + mix["read"] >= 0.60:
        window += step
        why.append("mostly reading and coordinating: long threads benefit from more history")
    if mix["edit"] >= 0.35 and (rr is None or rr < 0.30):
        window -= step
        why.append("mostly editing: fresh context after each task usually helps")
    if drift is not None and drift >= 0.70:
        window -= step
        why.append(f"topics change often ({int(drift*100)}% of the folders in play are new from one stretch of work to the next): "
                   f"old context is mostly noise")
    window = max(lo, min(hi, window))
    if not enough:
        # Not enough to judge: the window stays exactly where the user has it; with none set, the
        # neutral start (no adjustment from thin evidence).
        if m["current_window"]:
            window = m["current_window"]
            why = [f"only {m['compactions']} compaction(s) in this history: not enough to tune the window; "
                   f"keep {pc.fmt_k(window)}"]
        else:
            window = 500_000 if big else 150_000
            why = [f"only {m['compactions']} compaction(s) in this history: not enough to tune the window; "
                   f"start at the neutral {pc.fmt_k(window)}"]
    else:
        window = max(lo, min(hi, toward(m["current_window"], window, step, max_steps)))
    # Usage: a folder that reads a lot gets a lower window, whatever the memory signals say.
    cap, usage_class, cap_why = usage_cap(m, budget, big, rr)
    if cap is not None:
        cur = m["current_window"]
        target = min(cap, cur or cap)                        # hold at the cap, and never above where it is now
        if window > target:
            # Same limits as every other move: one step at a time when asked, never above the range's
            # top. The range's bottom does not lift a window the user already has lower than that.
            floor = min(lo, cur) if cur else lo
            window = max(floor, min(hi, toward(cur, target, step, max_steps) if cur else target))
            why.append(cap_why + (f"; it stays at {pc.fmt_k(window)}, where it already is" if cur and window == cur
                                  else f"; moving one step at a time, {pc.fmt_k(window)} now" if window > cap else ""))
    if (budget == "economy" and m["current_window"] and window > m["current_window"]
            and not (rr is not None and rr >= 0.30)):
        window = m["current_window"]
        why.append(f"economy budget: a window is not raised unless sessions re-read a lot after compacting; "
                   f"it stays at {pc.fmt_k(window)}")
    floor_set = None
    if history is not None and enough:
        window, mwhy, floor_set = check_last_move(m, history, window, now or time.time())
        if mwhy and mwhy[0].startswith(("waiting", "undo")):
            why = why[:1] + mwhy                          # the other reasons do not apply this time
        else:
            why += mwhy
    saving = read_saving(m["current_window"], window)
    if saving >= 0.05:
        why.append(f"a window of {pc.fmt_k(window)} instead of {pc.fmt_k(m['current_window'])} should cut this folder's "
                   f"cache reads by roughly {int(saving * 100)}% (an estimate), for more frequent compactions")
    elif saving <= -0.05:
        why.append(f"a window of {pc.fmt_k(window)} instead of {pc.fmt_k(m['current_window'])} should raise this folder's "
                   f"cache reads by roughly {int(-saving * 100)}% (an estimate), for fewer compactions")

    # The idle point: where a session that has gone quiet compacts by itself.
    istep = 50_000
    ilo, ihi = pc.IDLE_FLOOR, (300_000 if big else pc.IDLE_FLOOR)
    idle = pc.IDLE_DEFAULT if big else pc.IDLE_FLOOR
    iwhy = [f"idle point: Claude Code's default is {pc.fmt_k(pc.IDLE_DEFAULT)}"] if big else [
        f"idle point: {pc.fmt_k(pc.IDLE_FLOOR)} is the lowest Claude Code allows, and the right one for a {pc.fmt_k(limit)} context"]
    if big:
        cold = m.get("cold_wakes_per_day")
        if (gap is not None and gap >= 0.40) or (cold is not None and cold >= 3):
            idle -= istep
            iwhy.append(f"cold wake-ups ({cold} a day, about {pc.fmt_k(m.get('cold_wake_tokens_per_day'))} tokens a day paid "
                        f"again): each one pays for the whole context, so make the idle pit stop sooner")
        if drift is not None and drift >= 0.70:
            idle -= istep
            iwhy.append("topics change often: little of the old context is needed after a break")
        if rr is not None and rr >= 0.30:
            idle += istep
            iwhy.append("sessions re-read a lot after compacting: keep more before the idle pit stop")
        elif rr is not None and rr <= 0.10 and m["compactions"] >= 3:
            idle -= istep
            iwhy.append("sessions rarely look back after compacting: an earlier idle pit stop loses little")
        if drift is not None and drift <= 0.35 and rr is not None and rr > 0.10:
            idle += istep
            iwhy.append("long, connected work on the same files: keep more before the idle pit stop")
        idle = max(ilo, min(ihi, idle))
        idle = max(ilo, min(ihi, toward(m.get("current_idle_point") or pc.IDLE_DEFAULT, idle, istep, max_steps)))
    # Keep a step between the idle point and the window. Claude Code never goes under its floor,
    # so with a window the user set very low the floor wins.
    idle = max(ilo, min(idle, window - step))
    why += iwhy
    ceiling = max(min(window + 2 * step, int(limit * (0.85 if big else 0.90))), window)
    base = {"context_limit": limit, "idle_point": idle, "window": window, "ceiling": ceiling, "why": why,
            "budget": budget, "usage_class": usage_class, "est_read_saving": saving, "floor_set": floor_set}
    er = m["tool_error_rate"]
    if m["prompts"] < MIN_PROMPTS_FOR_SUGGESTIONS:
        return dict(base, model_effort_suggestions=[
            f"only {m['prompts']} prompts of history: no model or effort suggestion yet"])
    suggestions = []
    if er is not None and er >= 0.10:
        suggestions.append(f"tool error rate {int(er*100)}%: keep the strongest model; consider higher effort")
    mo = m.get("median_output_tokens") or 0
    if mo < 200 and mix["edit"] < 0.05 and mix["msg"] < 0.05 and (er is None or er < 0.02):
        suggestions.append("short answers, no editing or coordination, almost no errors: a lighter model or lower effort may do the same job for less (try it on one task first)")
    if mix["edit"] >= 0.35 and (er is not None and er >= 0.05):
        suggestions.append("heavy editing with errors: a stronger model or higher effort may save rework")
    if not suggestions:
        suggestions.append("no change suggested to model or effort")
    return dict(base, model_effort_suggestions=suggestions)


def lane_recs(project, days, context_limit=None, max_steps=None, newer_than_days=3, budget=None):
    """One recommendation per recent session of the folder, keyed by session id.
    A session with too little history of its own is left out (it follows the folder)."""
    out = {}
    cutoff = time.time() - newer_than_days * 86400
    for path in pc.transcripts(project, days):
        if os.path.getmtime(path) < cutoff:
            continue
        m = analyze(project, days, only=path)
        if m["prompts"] < MIN_PROMPTS_FOR_SUGGESTIONS:
            continue
        r = recommend(m, context_limit, max_steps, budget)
        out[os.path.basename(path)[:-len(".jsonl")]] = {
            "idle_point": r["idle_point"], "window": r["window"], "ceiling": r["ceiling"], "why": r["why"],
            "prompts": m["prompts"], "compactions": m["compactions"], "topic_change": m["topic_change"]}
    return out


def apply(project, rec, metrics, lanes=None):
    s = os.path.join(project, ".claude", "settings.local.json")
    data, err = pc.read_json_strict(s)
    if err:  # never rewrite a file we could not read: that would drop everything else in it
        raise SystemExit(f"refusing: {s} does not read as JSON ({err}); fix it or apply by hand")
    if not isinstance(data, dict):
        raise SystemExit(f"refusing: {s} is not a JSON object")
    if not isinstance(data.get("env", {}), dict):
        raise SystemExit(f"refusing: \"env\" in {s} is not a JSON object")
    b = pc.backup(s)
    data.setdefault("env", {})[pc.WINDOW_ENV] = str(rec["window"])
    data["env"][pc.IDLE_ENV] = str(rec["idle_point"])
    mode = os.stat(s).st_mode & 0o777 if os.path.exists(s) else 0o600
    pc.write_json_atomic(s, data, mode)
    prof = pc.folder_profile_path(project)
    pc.write_json_atomic(prof, {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "idle_point": rec["idle_point"], "window": rec["window"], "ceiling": rec["ceiling"],
        "context_limit": rec["context_limit"],
        "why": rec["why"], "model_effort_suggestions": rec["model_effort_suggestions"],
        "folder": os.path.abspath(project), "metrics": metrics}, 0o600)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for sid, lane in (lanes or {}).items():
        pc.write_json_atomic(os.path.join(pc.LANES, pc.safe_id(sid) + ".json"),
                             dict(lane, updated=stamp, folder=os.path.abspath(project)), 0o600)
    return s, b, prof


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--context-limit", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--lanes", action="store_true")
    ap.add_argument("--max-steps", type=int)
    ap.add_argument("--budget", choices=BUDGETS)
    a = ap.parse_args()
    m = analyze(a.project, a.days)
    if not m["transcripts"]:
        print(f"No session history for {m['project']} in the last {a.days} days.")
        return 1
    rec = recommend(m, a.context_limit, a.max_steps, a.budget)
    lanes = lane_recs(a.project, a.days, a.context_limit, a.max_steps, budget=a.budget) if a.lanes else {}
    if a.json:
        print(json.dumps({"metrics": m, "recommendation": rec, "lanes": lanes}, indent=2))
    else:
        print(f"Folder: {m['project']}  ({m['transcripts']} transcripts, {m['prompts']} prompts, last {a.days} days)")
        print(f"  compactions {m['compactions']}, peak context {pc.fmt_k(m['max_context'])}, "
              f"median peak before compaction {pc.fmt_k(m['median_peak_before_compaction'])}, "
              f"median hours per cycle {m['median_hours_per_cycle']}")
        print(f"  re-reads after compaction {m['reread_after_compaction']}, cache reuse {m['cache_reuse']}, "
              f"idle >1h share {m['long_gap_share']}, tool error rate {m['tool_error_rate']}")
        print(f"  work mix {m['work_mix']}")
        w = m["compactions_when"]
        print(f"  compactions happened: {w['idle']} while idle, {w['prompt']} as a prompt arrived, {w['mid_task']} mid-task, "
              f"{w.get('instant', 0)} swapped in instantly (summary prepared ahead by Claude Code)")
        print(f"  model calls per day {m['calls_per_day']}, tokens read from the cache per day "
              f"{pc.fmt_k(m['cache_read_per_day'])}, busiest session {pc.fmt_k(m['busiest_session_read_per_day'])} "
              f"(budget: {rec['budget']}"
              + (f", this folder is {rec['usage_class']}" if rec["usage_class"] else "") + ")")
        print(f"  topic change {m['topic_change']}, cold wake-ups per day {m['cold_wakes_per_day']}, "
              f"tokens paid again on cold wake-ups per day {pc.fmt_k(m['cold_wake_tokens_per_day'])}")
        print(f"Recommended idle point: {pc.fmt_k(rec['idle_point'])} (now {pc.fmt_k(m['current_idle_point'] or pc.IDLE_DEFAULT)})")
        print(f"Recommended window: {pc.fmt_k(rec['window'])} (now {pc.fmt_k(m['current_window'])}; hard ceiling {pc.fmt_k(rec['ceiling'])})")
        for w in rec["why"]:
            print(f"  - {w}")
        print("Model / effort (suggestions only, a person decides):")
        for s in rec["model_effort_suggestions"]:
            print(f"  - {s}")
        for sid, lane in lanes.items():
            print(f"Lane {sid[:8]}: idle point {pc.fmt_k(lane['idle_point'])}, window {pc.fmt_k(lane['window'])} "
                  f"({lane['prompts']} prompts, {lane['compactions']} compactions, topic change {lane['topic_change']})")
    if a.apply:
        s, b, prof = apply(a.project, rec, m, lanes)
        print(f"Applied: {s} (backup {b}); profile {prof}"
              + (f"; {len(lanes)} lane profile(s) in {pc.LANES}" if lanes else "")
              + ". Both points take effect at each session's next start (a restart counts); to move the window of a "
                f"running session now, type /autocompact {rec['window']} in it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
