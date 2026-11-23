"""G19: a proof is a test that ran. Each gate is checked on the real repository and against planted bad cases,
so the gate itself cannot go soft. The planted runs use a copy of tests/conftest.py and tests/_gate.py in a
temporary folder of their own, in their own process group with a time limit."""
import os
import re
import shutil
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _gate  # noqa: E402

PLANTED = '''import os
import sys

import pytest


def test_G1_1_good():
    assert True


def _helper():
    return None

    def test_G1_2_after_a_return():
        assert True


def test_G1_3_renamed_away():
    assert True


if False:
    def test_G1_4_under_if_false():
        assert True


@pytest.mark.skip(reason="later")
def test_G1_5_skipped():
    assert True


def test_G1_6_shadowed():
    assert False


def test_G1_6_shadowed():
    pass


_TEXT = """
def test_G1_7_in_a_string():
    assert True
"""


@pytest.mark.skipif(1 == 2 or True, reason="any reason")
def test_G1_8_skipif_on_nothing_of_the_machine():
    assert True


def test_G1_9_returns_before_its_first_line():
    return
    assert False


def test_G1_10_skips_under_a_constant():
    if True:
        pytest.skip("not today")


def test_G1_11_fails():
    assert False
'''
# test_G1_3 is planted under another name: the file defines test_G1_3_renamed_away, the plan wants _real.
PROOFS = {"1": "good", "2": "after_a_return", "3": "real", "4": "under_if_false", "5": "skipped", "6": "shadowed",
          "7": "in_a_string", "8": "skipif_on_nothing_of_the_machine", "9": "returns_before_its_first_line",
          "10": "skips_under_a_constant", "11": "fails"}


def _world(tmp_path, tests_text, proofs, rows=()):
    root = tmp_path / "world"
    (root / "tests").mkdir(parents=True)
    for name in ("conftest.py", "_gate.py"):
        shutil.copy(os.path.join(HERE, name), root / "tests" / name)
    (root / "tests" / "test_planted.py").write_text(tests_text)
    lines = [f"**G1.{n} Planted {n}.** Text. Proof: `test_G1_{n}_{p}`." for n, p in proofs.items()]
    (root / "DNA-STRAND-MASTER-PLAN.md").write_text("# Plan\n\n" + "\n\n".join(lines) + "\n")
    rows = [{"id": f"P{i}", "codon": "G1.1", "test": t} for i, t in enumerate(rows, 1)]
    (root / "PROMISES.json").write_text('{"rows": %s}' % str(rows).replace("'", '"'))
    return root


def _pytest(root, *args, seconds=120):
    """Run pytest on a planted world, in its own process group, stopped by its own group id at the limit."""
    p = subprocess.Popen([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "console_output_style=classic",
                          *args, str(root / "tests")], cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True, env=dict(os.environ, COLUMNS="4096"))
    try:
        out = p.communicate(timeout=seconds)[0].decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        if os.getpgid(p.pid) == p.pid and p.pid != os.getpgrp():
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
        p.communicate()
        raise AssertionError(f"the planted run did not finish in {seconds} s") from None
    return p.returncode, out


def _gate_lines(out):
    return [ln[len("G19 gate: "):] for ln in out.splitlines() if ln.startswith("G19 gate: ")]


def test_G19_1_a_built_proof_that_did_not_pass_fails_the_whole_run(tmp_path):
    """Ten ways a built codon's Proof can stay in a file without passing; each fails the whole run. Only G1.1 counts."""
    root = _world(tmp_path, PLANTED, PROOFS)
    code, out = _pytest(root, "-v")
    assert code == 1, out
    lines = _gate_lines(out)
    for n, p in PROOFS.items():
        name = f"test_G1_{n}_{p}"
        hit = [ln for ln in lines if name in ln]
        assert (hit == []) == (n == "1"), (name, hit, out[-3000:])
    assert "test_planted.py: test_G1_6_shadowed is defined 2 times, and only the last one runs" in lines
    assert any("test_G1_8_skipif_on_nothing_of_the_machine: a skipif whose condition does not read the machine" in ln
               for ln in lines)
    assert any("test_G1_9_returns_before_its_first_line: a return among its own statements" in ln for ln in lines)
    assert any("test_G1_10_skips_under_a_constant: skips with no condition of the machine" in ln for ln in lines)
    # a clean world passes, and a partial run checks nothing and says so
    good = _world(tmp_path / "good", "def test_G1_1_good():\n    assert True\n", {"1": "good"}, rows=["test_G1_1_good"])
    code, out = _pytest(good, "-v")
    assert code == 0 and _gate_lines(out) == [] and "partial run" not in out, out
    code, out = _pytest(root, "-v", "-k", "good")
    assert code == 0 and "partial run: rows and codons not checked" in out and _gate_lines(out) == [], out


def test_G19_1_a_rows_test_counts_the_same_way(tmp_path):
    """A PROMISES row's test is held like a codon's Proof, at any version."""
    root = _world(tmp_path, "def test_G1_1_good():\n    assert True\n", {"1": "good"}, rows=["test_G1_1_missing"])
    code, out = _pytest(root, "-v")
    assert code == 1 and _gate_lines(out) == ["test_G1_1_missing (row P1) did not run: it is not a test the runner collects"], out


def test_G19_2_no_test_file_defines_a_name_twice(tmp_path):
    assert _gate.twice_defined(HERE) == []
    (tmp_path / "test_x.py").write_text("def test_a():\n    assert 0\n\n\ndef test_a():\n    pass\n")
    assert _gate.twice_defined(str(tmp_path)) == ["test_x.py: test_a is defined 2 times, and only the last one runs"]


def test_G19_3_a_skip_is_listed_and_never_counts_as_passed(tmp_path):
    assert _gate.disguised_skips(HERE) == []
    text = ('import os\nimport shutil\n\nimport pytest\n\n\n'
            'def test_G1_1_good():\n    assert True\n\n\n'
            '@pytest.mark.skipif(not shutil.which("no-such-program-pit-stop"), reason="no such program here")\n'
            'def test_G1_2_needs_a_program():\n    assert True\n\n\n'
            'def test_G1_3_skips_with_no_reason():\n    if os.name:\n        pytest.skip()\n')
    root = _world(tmp_path, text, {"1": "good", "2": "needs_a_program"})
    code, out = _pytest(root, "-v")
    assert "not run here: test_G1_2_needs_a_program (no such program here)" in out, out
    assert code == 1, out
    lines = _gate_lines(out)
    assert "test_G1_2_needs_a_program (codon G1.2) was not run here (no such program here), " \
           "and a skip never counts as passed" in lines
    assert "test_G1_3_skips_with_no_reason: skipped with no reason" in lines
    assert any("test_G1_3_skips_with_no_reason: skips with no reason" in ln for ln in lines)
    # a skip with no reason fails even a partial run
    code, out = _pytest(root, "-v", "-k", "no_reason")
    assert code == 1 and "test_G1_3_skips_with_no_reason: skipped with no reason" in _gate_lines(out), out


def test_G19_3_the_ci_log_names_every_test_and_every_skip_reason():
    """CI runs pytest with -v and without -q (-q cancels -v; -rA folds skips into a count), wide COLUMNS, and the
    classic output style, or a long test id pushes a skip's reason off the line."""
    with open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8") as f:
        ci = f.read()
    runs = [ln.strip() for ln in ci.splitlines() if re.search(r"-m pytest\b", ln)]
    assert runs, "CI runs no pytest"
    for ln in runs:
        args = ln.split()
        assert "-v" in args and "-q" not in args and "-rA" not in args, ln
        assert "console_output_style=classic" in ln, ln
    cols = re.findall(r"COLUMNS:\s*\"?(\d+)", ci)
    assert cols and min(int(c) for c in cols) >= 1000, "CI needs a wide COLUMNS for its pytest step"
