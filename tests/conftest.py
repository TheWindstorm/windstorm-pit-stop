"""G19.1 and G19.3: count what the test runner reports as PASSED, and fail a whole-suite run in which a built
codon's Proof or a PROMISES row's test did not pass. A skip is listed as `not run here: <test> (<reason>)` and
never counts as a pass: outside CI it does not fail the run, and in CI (CI=true) a built proof that did not pass
does. A skip with no reason fails any run. There is no switch."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _gate  # noqa: E402

_run = {"names": {}, "collected": set(), "deselected": 0, "results": {}, "lines": [], "whole": None}


@pytest.fixture(autouse=True)
def _a_persons_session(monkeypatch):
    """Every test runs as a person's session, also when the suite itself is run by `claude -p` or an SDK
    (G2.26: a headless entrypoint switches tuning off); a test that wants a headless one sets it."""
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)


def _top_level_name(item):
    """The function name of a top-level test in tests/test_*.py (parametrized cases share it), else None."""
    path = str(getattr(item, "path", getattr(item, "fspath", "")))
    if os.path.dirname(os.path.abspath(path)) != HERE or not os.path.basename(path).startswith("test_"):
        return None
    if getattr(item, "cls", None) is not None or not hasattr(item, "originalname"):
        return None
    return item.originalname


def pytest_deselected(items):
    _run["deselected"] += len(items)


def pytest_collection_finish(session):
    _run["names"] = {i.nodeid: _top_level_name(i) for i in session.items}
    _run["collected"] = {n for n in _run["names"].values() if n}


def pytest_runtest_logreport(report):
    name = _run["names"].get(report.nodeid)
    if not name:
        return
    cases = _run["results"].setdefault(name, {})
    case = report.nodeid
    if report.failed:
        cases[case] = ("failed",)
    elif report.skipped:
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr or "")
        if reason.startswith("Skipped: "):
            reason = reason[len("Skipped: "):]
        elif reason.strip() == "Skipped":              # pytest.skip() with no message
            reason = ""
        if hasattr(report, "wasxfail"):
            reason = ""                                # an expected failure is never a reason
        cases[case] = ("skipped", reason)
    elif report.when == "call" and case not in cases:
        cases[case] = ("passed",)


def pytest_sessionfinish(session, exitstatus):
    if exitstatus not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
        return                                         # interrupted, usage error, nothing collected: not counted
    results = {n: list(c.values()) for n, c in _run["results"].items()}
    whole, problems, here = _gate.verdict(ROOT, HERE, _run["collected"], _run["deselected"], results,
                                          ci=_gate.in_ci(os.environ))
    _run["whole"] = whole
    _run["lines"] = here + (["partial run: rows and codons not checked"] if not whole else []) + \
        [f"G19 gate: {p}" for p in problems]
    if problems:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    if _run["lines"]:
        terminalreporter.section("G19: what ran")
        for line in _run["lines"]:
            terminalreporter.write_line(line)
