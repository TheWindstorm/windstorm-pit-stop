#!/usr/bin/env python3
"""Pit Stop Optimizer: read a folder's session history and recommend its pit stop.

  optimize.py [--project DIR] [--days 14] [--context-limit 1000000|200000]
  optimize.py ... --json        machine-readable result
  optimize.py ... --apply       write the compaction point (settings.local.json
                                env CLAUDE_CODE_AUTO_COMPACT_WINDOW) and the
                                profile (.claude/pit-stop.json); backups first

What it measures (counts only; message text is never printed):
  - compaction cycles: peak context before each compaction, hours and turns per cycle
  - re-reads after compaction: files read again in the first steps after a
    compaction that were already read in the cycle before (a sign the
    compaction point was too early and the session lost what it needed)
  - cache reuse: share of input served from the prompt cache
  - idle gaps between prompts (long gaps = cold cache = long contexts cost more)
  - work mix by tool: editing, shell, reading, messaging, web
  - trouble signals: tool errors

What it recommends:
  - compaction point (window) and hard ceiling: applied with --apply, within bounds
  - model and effort: SUGGESTIONS only, never applied. A person decides those,
    because they change quality and cost.
These are heuristics, not guarantees. Re-run weekly; the profile records why.
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
# planned re-reading after a compaction is not lost context
ROUTINE_READ = re.compile(r"(?i)(claude|memory|todo|start[-_]here|handoff|readme|status|changelog)[^/]*\.md$")


def analyze(project, days):
    files = pc.transcripts(project, days)
    cycles, cur = [], None
    prompts, gaps = [], []
    tools = {"edit": 0, "shell": 0, "read": 0, "msg": 0, "web": 0, "other": 0}
    errors = tool_results = 0
    cache_read = cache_new = fresh_in = 0
    max_ctx = 0
    rereads = reread_checks = 0
    sessions = boundaries = 0
    outs = []
    for path in files:
        sessions += 1
        prev_reads, cur_reads, after_compact = set(), set(), None
        cur = {"peak": 0, "turns": 0, "start": None, "end": None}
        for e in pc.iter_entries(path):
            ts = pc.parse_ts(e.get("timestamp"))
            if pc.is_compact_boundary(e):
                boundaries += 1
                if cur["turns"]:
                    cur["compacted"] = True
                    cycles.append(cur)
                cur = {"peak": 0, "turns": 0, "start": ts, "end": ts}
                prev_reads, cur_reads, after_compact = cur_reads, set(), 0
                continue
            t = e.get("type")
            if t == "user" and not e.get("isSidechain"):
                kinds = pc.content_kinds(e)
                if "tool_result" in kinds:
                    for x in (e.get("message") or {}).get("content") or []:
                        if isinstance(x, dict) and x.get("type") == "tool_result":
                            tool_results += 1
                            errors += 1 if x.get("is_error") else 0
                elif ts:
                    if prompts:
                        gaps.append(ts - prompts[-1])
                    prompts.append(ts)
            if t == "assistant" and not e.get("isSidechain"):
                u = (e.get("message") or {}).get("usage") or {}
                total = pc.usage_total(e)
                if total:
                    cur["peak"] = max(cur["peak"], total)
                    max_ctx = max(max_ctx, total)
                    cur["turns"] += 1
                    cache_read += int(u.get("cache_read_input_tokens") or 0)
                    cache_new += int(u.get("cache_creation_input_tokens") or 0)
                    fresh_in += int(u.get("input_tokens") or 0)
                    outs.append(int(u.get("output_tokens") or 0))
                cur["start"] = cur["start"] or ts
                cur["end"] = ts or cur["end"]
                for tu in pc.tool_uses(e):
                    n = tu.get("name") or ""
                    inp = tu.get("input") or {}
                    k = ("edit" if n in EDIT else "shell" if n in SHELL else "read" if n in READ
                         else "msg" if n in MSG else "web" if n in WEB else "other")
                    paths = []
                    if n in READ and (inp.get("file_path") or inp.get("path")):
                        paths = [inp.get("file_path") or inp.get("path")]
                    if n == "Bash":
                        cmd = str(inp.get("command") or "")
                        if SHELL_READ.match(cmd):
                            k = "read"
                            paths = PATHLIKE.findall(cmd)[:3]
                        elif SHELL_MSG.search(cmd.split("|")[0]):
                            k = "msg"
                    tools[k] += 1
                    for fp in [x for x in paths if not ROUTINE_READ.search(x)]:
                        if after_compact is not None and after_compact < REREAD_WINDOW:
                            reread_checks += 1
                            rereads += 1 if fp in prev_reads else 0
                        cur_reads.add(fp)
                    if after_compact is not None:
                        after_compact += 1
        if cur["turns"]:
            cycles.append(cur)
    compacted = [c for c in cycles if c.get("compacted")]
    total_in = cache_read + cache_new + fresh_in
    ntools = sum(tools.values()) or 1
    return {
        "project": os.path.abspath(project), "days": days, "sessions": sessions,
        "transcripts": len(files), "compactions": boundaries,
        "max_context": max_ctx,
        "median_peak_before_compaction": int(statistics.median([c["peak"] for c in compacted])) if compacted else None,
        "median_hours_per_cycle": round(statistics.median(
            [(c["end"] - c["start"]) / 3600 for c in compacted if c["start"] and c["end"]]), 1) if compacted else None,
        "reread_after_compaction": round(rereads / reread_checks, 2) if reread_checks else None,
        "cache_reuse": round(cache_read / total_in, 2) if total_in else None,
        "median_gap_minutes": round(statistics.median(gaps) / 60, 1) if gaps else None,
        "long_gap_share": round(sum(1 for g in gaps if g > 3600) / len(gaps), 2) if gaps else None,
        "work_mix": {k: round(v / ntools, 2) for k, v in tools.items()},
        "tool_error_rate": round(errors / tool_results, 3) if tool_results else None,
        "prompts": len(prompts),
        "median_output_tokens": int(statistics.median(outs)) if outs else None,
        "current_window": current_window(project),
    }


def current_window(project):
    for f in (os.path.join(project, ".claude", "settings.local.json"), os.path.join(project, ".claude", "settings.json")):
        d = pc.read_json(f, {}) or {}
        v = (d.get("env") or {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") if isinstance(d, dict) else None
        if v and str(v).isdigit():
            return int(v)
    return None


def recommend(m, context_limit=None):
    limit = context_limit or (1_000_000 if m["max_context"] > 200_000 else 200_000)
    big = limit >= 1_000_000
    step = 100_000 if big else 20_000
    window = 500_000 if big else 150_000
    lo, hi = (300_000, 700_000) if big else (100_000, 170_000)
    why = [f"context limit {pc.fmt_k(limit)}: start at {pc.fmt_k(window)}"]
    rr, gap, mix = m["reread_after_compaction"], m["long_gap_share"], m["work_mix"]
    if rr is not None and rr >= 0.30:
        window += step
        why.append(f"{int(rr*100)}% of early reads after a compaction re-read files from before it: compact later")
    elif rr is not None and rr <= 0.10 and m["compactions"] >= 2:
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
    window = max(lo, min(hi, window))
    if m["compactions"] < 2:
        keep = m["current_window"] or window
        why = [f"only {m['compactions']} compaction(s) in this history: not enough to tune; keep {pc.fmt_k(keep)}"]
        window = keep
    ceiling = min(window + 2 * step, int(limit * (0.85 if big else 0.90)))
    suggestions = []
    er = m["tool_error_rate"]
    if m["prompts"] < MIN_PROMPTS_FOR_SUGGESTIONS:
        return {"context_limit": limit, "window": window, "ceiling": max(ceiling, window), "why": why,
                "model_effort_suggestions": [f"only {m['prompts']} prompts of history: no model or effort suggestion yet"]}
    if er is not None and er >= 0.10:
        suggestions.append(f"tool error rate {int(er*100)}%: keep the strongest model; consider higher effort")
    mo = m.get("median_output_tokens") or 0
    if mo < 200 and mix["edit"] < 0.05 and mix["msg"] < 0.05 and (er is None or er < 0.02):
        suggestions.append("short answers, no editing or coordination, almost no errors: a lighter model or lower effort may do the same job for less (try it on one task first)")
    if mix["edit"] >= 0.35 and (er is not None and er >= 0.05):
        suggestions.append("heavy editing with errors: a stronger model or higher effort may save rework")
    if not suggestions:
        suggestions.append("no change suggested to model or effort")
    return {"context_limit": limit, "window": window, "ceiling": ceiling, "why": why,
            "model_effort_suggestions": suggestions}


def apply(project, rec, metrics):
    s = os.path.join(project, ".claude", "settings.local.json")
    data = pc.read_json(s, {}) if os.path.exists(s) else {}
    if os.path.exists(s) and not isinstance(data, dict):
        raise SystemExit(f"refusing: {s} is not a JSON object")
    b = pc.backup(s)
    data.setdefault("env", {})["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(rec["window"])
    mode = os.stat(s).st_mode & 0o777 if os.path.exists(s) else 0o600
    pc.write_json_atomic(s, data, mode)
    prof = os.path.join(project, ".claude", "pit-stop.json")
    pc.write_json_atomic(prof, {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window": rec["window"], "ceiling": rec["ceiling"], "context_limit": rec["context_limit"],
        "why": rec["why"], "model_effort_suggestions": rec["model_effort_suggestions"],
        "metrics": metrics}, 0o644)
    return s, b, prof


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--context-limit", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    m = analyze(a.project, a.days)
    if not m["transcripts"]:
        print(f"No session history for {m['project']} in the last {a.days} days.")
        return 1
    rec = recommend(m, a.context_limit)
    if a.json:
        print(json.dumps({"metrics": m, "recommendation": rec}, indent=2))
    else:
        print(f"Folder: {m['project']}  ({m['transcripts']} transcripts, {m['prompts']} prompts, last {a.days} days)")
        print(f"  compactions {m['compactions']}, peak context {pc.fmt_k(m['max_context'])}, "
              f"median peak before compaction {pc.fmt_k(m['median_peak_before_compaction'])}, "
              f"median hours per cycle {m['median_hours_per_cycle']}")
        print(f"  re-reads after compaction {m['reread_after_compaction']}, cache reuse {m['cache_reuse']}, "
              f"idle >1h share {m['long_gap_share']}, tool error rate {m['tool_error_rate']}")
        print(f"  work mix {m['work_mix']}")
        print(f"Recommended compaction point: {pc.fmt_k(rec['window'])} (hard ceiling {pc.fmt_k(rec['ceiling'])})")
        for w in rec["why"]:
            print(f"  - {w}")
        print("Model / effort (suggestions only, a person decides):")
        for s in rec["model_effort_suggestions"]:
            print(f"  - {s}")
    if a.apply:
        s, b, prof = apply(a.project, rec, m)
        print(f"Applied: {s} (backup {b}); profile {prof}. Takes effect at the session's next start.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
