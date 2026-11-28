"""A filed row has one form: the five cases the filed form asks of every skill's gate, each on planted rows,
each seen red before the change (the record names how). The gate's own functions are in test_promises.py; the whole-run
need list and the twice-defined check are tests/_gate.py's."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _gate  # noqa: E402
import test_promises as tp  # noqa: E402

TEXT = "**G1.1 A codon.** Proof: `test_G1_1_built`.\n"
HAVE = {"test_G1_1_built"}
NOW = (0, 6, 3)
BY_MAINTAINER = "By the maintainer's word, 2026-11-29: waits for a real report. A what-if from reading; no machine has shown it."
BY_OWNER = "By the owner's word, 2026-11-29: waits for a real report. Not seen on a real machine."


def row(**k):
    r = {"id": "P1", "codon": "G1.1", "owner": "maintainer", "first_ship_in": "0.6.3", "ship_in": "0.6.3",
         "seen": "s", "promise": "p", "test": "", "proven": "", "moved_why": "", "moved_by_person": ""}
    r.update(k)
    return r


def test_FILED_1_a_filed_text_without_its_opening_or_its_date_fails():
    for text in ("waits for a real report.", "By the maintainer's word: waits for a real report.",
                 "By the maintainer's word, 2026-13-40: waits.", "By the reader's word, 2026-11-29: waits.",
                 "by the maintainer's word, 2026-11-29: waits."):
        assert tp.filed_problems([row(filed=text)]), text
    assert tp.filed_problems([row(filed=BY_MAINTAINER)]) == []
    assert tp.filed_problems([row(filed=BY_OWNER)]) == []
    assert tp.filed_problems([row(filed="")]) == [] and tp.filed_problems([row()]) == []


def test_FILED_2_a_persons_row_filed_by_the_maintainers_word_fails():
    assert tp.filed_problems([row(owner="person", filed=BY_MAINTAINER)])
    assert tp.filed_problems([row(owner="person", filed=BY_OWNER)]) == []


def test_FILED_3_an_unbuilt_row_due_now_fails_and_the_same_row_filed_passes():
    assert tp.due_problems([row()], NOW, HAVE, TEXT)
    assert tp.due_problems([row(filed=BY_MAINTAINER)], NOW, HAVE, TEXT) == []
    assert tp.due_problems([row(ship_in="0.6.2", first_ship_in="0.6.2", filed=BY_MAINTAINER)], NOW, HAVE, TEXT) == []
    assert tp.proof_problems([row(ship_in="0.6.2", filed=BY_MAINTAINER)], NOW) == []   # unbuilt and filed: due in none


def test_FILED_4_a_built_filed_rows_test_is_refused_as_any_built_rows_is(tmp_path):
    missing = row(test="test_G1_1_gone", filed=BY_MAINTAINER)
    assert tp.due_problems([missing], NOW, HAVE, TEXT), "a built filed row whose test is missing"
    assert tp.due_problems([row(test="test_G9_9_elsewhere", filed=BY_MAINTAINER)], NOW, HAVE | {"test_G9_9_elsewhere"}, TEXT), \
        "a built filed row whose test is not its codon's Proof"
    (tmp_path / "DNA-STRAND-MASTER-PLAN.md").write_text("")
    (tmp_path / "PROMISES.json").write_text(json.dumps({"rows": [row(test="test_G1_1_built", filed=BY_MAINTAINER)]}))
    assert _gate.required(str(tmp_path)) == {"test_G1_1_built": "row P1"}, "the runner must report it PASSED"
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_G1_1_built():\n    assert 0\n\n\ndef test_G1_1_built():\n    pass\n")
    assert _gate.twice_defined(str(tests)), "named twice is refused for a filed row's test too"


def test_FILED_5_a_built_row_with_no_proven_line_fails_and_the_same_row_filed_passes():
    shipped = dict(ship_in="0.6.2", first_ship_in="0.6.2", test="test_G1_1_built")
    assert tp.proof_problems([row(**shipped)], NOW)
    assert tp.proof_problems([row(filed=BY_MAINTAINER, **shipped)], NOW) == []
    assert tp.due_problems([row(filed=BY_MAINTAINER, **shipped)], NOW, HAVE, TEXT) == []   # its test still stands as built
