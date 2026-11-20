#!/usr/bin/env python3
"""Trigger trials for the pit-stop skill: does its description make Claude load it?

Modeled on
skill-creator's run_eval.py: the description is offered as a project command with a unique
name, `claude -p QUERY` runs in a scratch folder of its own, and the saved stream says
whether the skill was read.

Usage: trigger_trials.py CASE_DIR SKILL_DIR SCRATCH_DIR RUNS MODEL

CASE_DIR holds queries.json ([{"query", "should_trigger"}]). Results go to
CASE_DIR/results/<date>-<model>.json; transcripts stay in SCRATCH_DIR.

Scores, per trial:
  any_call    the Skill tool names the stand-in at any point in the run, or the first tool
              call Reads it. A later Read does not count: in a near-empty folder an agent that
              explores finds the stand-in file and reads it as a repository file (2026-11-20,
              near miss 3, "Write a CLAUDE.md", 3 of 3 runs);
  first_call  the first tool call loads it (skill-creator's rule, a floor);
  installed   the installed windstorm-pit-stop skill was called instead (should be never:
              the installed plugin is switched off for the run, and init_has_installed
              records whether it was visible anyway).
A query fires when any_call is true in at least half its runs.

Safety: one trial at a time; the child runs with Bash, Edit, Write and NotebookEdit
switched off and in the default permission mode, so it can read but change nothing.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import uuid

PLUGIN_KEY = "windstorm-pit-stop@thewindstorm"
OFF = json.dumps({"enabledPlugins": {PLUGIN_KEY: False}})
NO_WRITE = ["Bash", "Edit", "Write", "NotebookEdit"]


def description(skill_dir):
    text = open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8").read()
    head = text.split("\n---", 1)[0]
    m = re.search(r"^description: (.*)$", head, re.M)
    value = m.group(1).strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def command(query, model):
    """The argv of one trial: installed Pit Stop off, no tool that changes anything, default mode."""
    return ["claude", "-p", query, "--model", model, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "default", "--settings", OFF, "--disallowedTools", *NO_WRITE]


def trial_paths(scratch, qi, r):
    """(folder the trial runs in, its transcript). One folder per trial; the transcript is outside it."""
    return os.path.join(scratch, f"q{qi:02d}-r{r}"), os.path.join(scratch, f"q{qi:02d}-r{r}.jsonl")


def trial(qi, q, r, desc, scratch, model):
    tag = uuid.uuid4().hex[:8]
    name = f"pit-stop-skill-{tag}"
    cwd, out = trial_paths(scratch, qi, r)
    os.makedirs(os.path.join(cwd, ".claude", "commands"), exist_ok=True)
    cmd_file = os.path.join(cwd, ".claude", "commands", name + ".md")
    with open(cmd_file, "w", encoding="utf-8") as f:
        f.write("---\ndescription: |\n  " + desc + "\n---\n\n# pit-stop\n\nThis skill handles: " + desc + "\n")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    cmd = command(q["query"], model)
    timed_out = False
    try:
        with open(out, "w", encoding="utf-8") as fo:
            subprocess.run(cmd, stdout=fo, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                           cwd=cwd, env=env, timeout=150)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        os.remove(cmd_file)
    calls, init_has_installed, init_has_ours = [], None, None
    for line in open(out, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            listed = json.dumps(e.get("skills", [])) + json.dumps(e.get("slash_commands", []))
            init_has_installed = "windstorm-pit-stop:" in listed
            init_has_ours = name in listed
        if e.get("type") == "assistant":
            for c in e.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    calls.append((c.get("name"), json.dumps(c.get("input", {}))))
    hit = [n in ("Skill", "Read") and name in i for n, i in calls]
    skill_hit = any(n == "Skill" and name in i for n, i in calls)
    installed = any(n == "Skill" and "windstorm-pit-stop:" in i for n, i in calls)
    return {"q": qi, "run": r, "should_trigger": q["should_trigger"], "any_call": skill_hit or bool(hit and hit[0]),
            "first_call": bool(hit and hit[0]), "installed": installed,
            "first_tool": calls[0][0] if calls else None, "init_has_ours": init_has_ours,
            "init_has_installed": init_has_installed, "timed_out": timed_out, "query": q["query"]}


def main():
    case_dir, skill_dir, scratch, runs, model = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
    qs = json.load(open(os.path.join(case_dir, "queries.json"), encoding="utf-8"))
    desc = description(skill_dir)
    scratch = os.path.join(scratch, os.path.basename(os.path.normpath(case_dir)))
    os.makedirs(scratch, exist_ok=True)
    res = []
    for qi, q in enumerate(qs, 1):
        for r in range(1, runs + 1):
            x = trial(qi, q, r, desc, scratch, model)
            print(json.dumps({k: x[k] for k in ("q", "run", "any_call", "first_call", "installed", "first_tool")}), flush=True)
            res.append(x)
    per_query = []
    for qi, q in enumerate(qs, 1):
        rs = [x for x in res if x["q"] == qi]
        n = sum(x["any_call"] for x in rs)
        fired = n * 2 >= len(rs)
        per_query.append({"q": qi, "query": q["query"], "should_trigger": q["should_trigger"],
                          "any_call": n, "first_call": sum(x["first_call"] for x in rs), "runs": len(rs),
                          "fired": fired, "pass": fired == q["should_trigger"]})
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {"queries": len(qs), "passed": sum(p["pass"] for p in per_query),
               "trials": len(res), "installed_calls": sum(x["installed"] for x in res),
               "installed_visible": sum(bool(x["init_has_installed"]) for x in res),
               "ours_visible": sum(bool(x["init_has_ours"]) for x in res),
               "timed_out": sum(x["timed_out"] for x in res)}
    os.makedirs(os.path.join(case_dir, "results"), exist_ok=True)
    out = os.path.join(case_dir, "results", stamp[:10] + "-" + model + ".json")
    json.dump({"finished": stamp, "model": model, "claude_code": subprocess.run(["claude", "--version"],
               capture_output=True, text=True).stdout.strip(), "description": desc, "summary": summary,
               "per_query": per_query, "trials": res}, open(out, "w", encoding="utf-8"), indent=1)
    print(json.dumps(summary))
    print(out)


if __name__ == "__main__":
    main()
