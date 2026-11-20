"""G12.4 to G12.6: the trigger evals' recorded results match the queries and the description as
they are now. Reads files only; runs no model (the evals themselves are run by hand)."""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALS = os.path.join(ROOT, "evals")
sys.path.insert(0, os.path.join(EVALS, "tools"))
import trigger_trials as tt  # noqa: E402

RUNS = 3


def newest_sonnet(case):
    """The file in results/ with the latest `finished` among files whose `model` starts with claude-sonnet."""
    files = []
    for p in glob.glob(os.path.join(EVALS, "cases", case, "results", "*.json")):
        d = json.load(open(p, encoding="utf-8"))
        if str(d.get("model", "")).startswith("claude-sonnet"):
            files.append((d["finished"], p, d))
    assert files, f"{case}: no Sonnet results file"
    return max(files)[2]


def check_case(case, fires):
    d = newest_sonnet(case)
    queries = json.load(open(os.path.join(EVALS, "cases", case, "queries.json"), encoding="utf-8"))
    assert d["description"] == tt.description(os.path.join(ROOT, "skills", "pit-stop")), \
        f"{case}: results are for another description; run the evals again"
    assert [p["query"] for p in d["per_query"]] == [q["query"] for q in queries], \
        f"{case}: results are for other queries; run the evals again"
    for p in d["per_query"]:
        assert p["runs"] == RUNS, f"{case} q{p['q']}: {p['runs']} runs, not {RUNS}"
        if fires:
            assert p["any_call"] * 2 >= RUNS, f"{case} q{p['q']}: loaded in {p['any_call']} of {RUNS}"
        else:
            assert p["any_call"] * 2 < RUNS, f"{case} q{p['q']}: loaded in {p['any_call']} of {RUNS}"


def test_G12_4_should_fire_queries_load_the_skill():
    check_case("g12-4-should-fire", True)


def test_G12_5_near_miss_queries_do_not_load_the_skill():
    check_case("g12-5-near-miss", False)


def test_G12_6_trigger_trials_run_isolated_and_read_only():
    argv = tt.command("a query", "claude-sonnet-5-5")
    assert argv[:3] == ["claude", "-p", "a query"]
    s = argv.index("--settings")
    assert json.loads(argv[s + 1]) == {"enabledPlugins": {"windstorm-pit-stop@thewindstorm": False}}
    d = argv.index("--disallowedTools")
    assert argv[d + 1:d + 5] == ["Bash", "Edit", "Write", "NotebookEdit"]
    m = argv.index("--permission-mode")
    assert argv[m + 1] == "default"
    folders = set()
    for qi in (1, 2):
        for r in (1, 2):
            cwd, transcript = tt.trial_paths("/scratch", qi, r)
            assert not os.path.commonpath([cwd, transcript]) == cwd, "transcript inside the trial folder"
            folders.add(cwd)
    assert len(folders) == 4, "two trials share a folder"
    files = glob.glob(os.path.join(EVALS, "cases", "*", "results", "*.json"))
    assert files
    for p in files:
        s = json.load(open(p, encoding="utf-8"))["summary"]
        assert s["installed_visible"] == 0 and s["ours_visible"] == s["trials"] and s["timed_out"] == 0, p
