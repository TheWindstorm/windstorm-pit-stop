"""PROMISES.json: what real machines showed must change, and the version each change is due in.

A lesson is a gate, not a note: these tests refuse a version while a promise due in it is not built, and the
version after it until the promise was seen to work on a real machine. Copied from Windstorm Tardigrade's class
Promises (the Windstorm skill standard, 2026-11-22), with the plan's codon form and plugin.json's version."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    with open(os.path.join(ROOT, "PROMISES.json"), encoding="utf-8") as f:
        return json.load(f)


def v(s):
    return tuple(int(x) for x in s.split("."))


def version():
    with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        return v(json.load(f)["version"])


def plan():
    with open(os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md"), encoding="utf-8") as f:
        return f.read()


def defined_tests():
    names = set()
    for n in os.listdir(os.path.join(ROOT, "tests")):
        if n.startswith("test_") and n.endswith(".py"):
            with open(os.path.join(ROOT, "tests", n), encoding="utf-8") as f:
                names |= set(re.findall(r"^def (test_\w+)\(", f.read(), re.M))
    return names


def test_PROMISES_every_row_is_whole_and_its_codon_is_in_the_plan():
    rows, text = load()["rows"], plan()
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "two rows share an id"
    for r in rows:
        for k in ("id", "seen", "promise", "codon", "owner", "first_ship_in", "ship_in"):
            assert r.get(k), f"{r.get('id')}: '{k}' is empty"
        assert r["owner"] in ("person", "maintainer"), r["id"]
        assert re.search(r"^\*\*" + re.escape(r["codon"]) + r" ", text, re.M), f"{r['id']}: codon {r['codon']} is not in the plan"


def test_PROMISES_a_lesson_not_taken_is_written_down_with_its_reason():
    for d in load().get("declined", []):
        assert d.get("heard") and d.get("why"), "a declined lesson needs what was heard and why it was not taken"


def test_PROMISES_no_version_ships_while_a_promise_due_in_it_is_not_built():
    have, text, now = defined_tests(), plan(), version()
    for r in load()["rows"]:
        if v(r["ship_in"]) <= now:
            assert r.get("test") in have, (f"{r['id']} ({r['promise'][:60]}...) is due in {r['ship_in']} and has no test. "
                                           "Build it, or have its date moved (see how_to_use in PROMISES.json).")
            assert f"Proof: `{r['test']}`" in text, f"{r['id']}: its test is not its codon's Proof in the plan"


def test_PROMISES_the_version_after_needs_proof_on_a_real_machine():
    now = version()
    for r in load()["rows"]:
        if v(r["ship_in"]) < now:
            assert r.get("proven"), f"{r['id']} shipped in {r['ship_in']} and no real machine has proven it yet"


def test_PROMISES_a_date_moves_only_with_a_reason_and_a_persons_row_only_by_their_word():
    for r in load()["rows"]:
        if r["ship_in"] != r["first_ship_in"]:
            assert r.get("moved_why"), f"{r['id']}: its date moved with no reason written"
            if r["owner"] == "person":
                assert r.get("moved_by_person"), f"{r['id']}: only the person who saw it can move its date"
