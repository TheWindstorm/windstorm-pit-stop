"""G19.1 and G19.3: count what the test runner reports as PASSED, and fail a whole-suite run in which a built
codon's Proof or a PROMISES row's test did not pass. A skip is listed as `not run here: <test> (<reason>)` and
never counts as a pass: outside CI it does not fail the run, and in CI (CI=true) a built proof that did not pass
does. A skip with no reason fails any run. There is no switch."""
import os
import shutil
import site
import sys
import tempfile

import pytest

# Rule 17, A TEST NEVER USES THE HOME OF THE ACCOUNT RUNNING THE SUITE (G19.5): before anything imports the tool,
# the whole run gets its own temporary home, Claude Code folder and tmux folder, and no line to this account's
# systemd or session bus. The tool types into tmux panes; the account running the suite may hold real sessions.
try:
    import pwd
    REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
except (ImportError, KeyError):
    REAL_HOME = os.path.realpath(os.path.expanduser("~"))
os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())   # the runner's own pytest stays importable in a child
REAL_TMUX = sorted({os.path.join(d, f"tmux-{os.getuid()}") for d in ("/tmp", os.environ.get("TMUX_TMPDIR") or "/tmp")})
_SESSION_TMP = tempfile.mkdtemp(prefix="pitstop-tests-")
os.environ.update(HOME=os.path.join(_SESSION_TMP, "home"), CLAUDE_CONFIG_DIR=os.path.join(_SESSION_TMP, "home", ".claude"),
                  TMUX_TMPDIR=os.path.join(_SESSION_TMP, "tmux"))
for _k in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "TMUX", "TMUX_PANE"):
    os.environ.pop(_k, None)
os.makedirs(os.environ["HOME"])
os.makedirs(os.environ["TMUX_TMPDIR"])
_SYSTEM = ("tmux", "claude", "crontab", "systemctl", "loginctl", "launchctl")
# A temporary home does not isolate what answers for the account itself (crontab, systemctl --user, loginctl,
# launchctl, tmux's default folder): those names resolve first to stubs that fail loudly; a test brings its own.
_STUBS = os.path.join(_SESSION_TMP, "stubs")
os.makedirs(_STUBS)
for _n in _SYSTEM:
    with open(os.path.join(_STUBS, _n), "w") as _f:
        _f.write(f'#!/bin/sh\necho "rule 17 stub: {_n} $* (a test brings its own {_n})" >&2\nexit 97\n')
    os.chmod(os.path.join(_STUBS, _n), 0o755)
os.environ["PATH"] = _STUBS + os.pathsep + os.environ.get("PATH", "")
_WATCH = [os.path.join(REAL_HOME, x) for x in (".claude", ".config", os.path.join("Library", "LaunchAgents"))] + REAL_TMUX
_seen = {"test": None, "hits": []}


def _under(p, roots):
    try:
        p = os.path.realpath(os.fsdecode(p))
    except (TypeError, ValueError):
        return False
    return any(p == r or p.startswith(r + os.sep) for r in roots)


def _audit(event, args):
    if not _seen["test"]:
        return
    if event in ("open", "os.listdir", "os.scandir", "glob.glob", "os.chmod", "os.remove", "os.rename", "os.mkdir",
                 "os.rmdir", "shutil.rmtree", "os.utime"):
        if args and isinstance(args[0], (str, bytes, os.PathLike)) and _under(args[0], _WATCH):
            _seen["hits"].append(f"{event} {os.fsdecode(args[0])}")
    elif event == "subprocess.Popen":
        _exe, argv, _cwd, env = args
        env = os.environ if env is None else env
        argv = [os.fsdecode(a) for a in (argv or [])] if not isinstance(argv, (str, bytes)) else [os.fsdecode(argv)]
        home, tdir = env.get("HOME"), env.get("TMUX_TMPDIR")
        if not home or os.path.realpath(home) == REAL_HOME:
            _seen["hits"].append(f"a child with {'no HOME' if not home else 'the real HOME'}: {' '.join(argv)[:100]}")
        if not tdir or any(os.path.join(os.path.realpath(tdir), f"tmux-{os.getuid()}") == r for r in REAL_TMUX):
            _seen["hits"].append(f"a child that would look in the real tmux folder (TMUX_TMPDIR "
                                 f"{'not set' if not tdir else tdir}): {' '.join(argv)[:100]}")
        if argv and os.path.basename(argv[0]) in _SYSTEM:
            found = shutil.which(argv[0], path=env.get("PATH"))
            if found and not _under(found, [os.path.realpath(tempfile.gettempdir()), "/tmp", _SESSION_TMP]):
                _seen["hits"].append(f"this machine's own {os.path.basename(argv[0])}: {' '.join(argv)[:100]}")


sys.addaudithook(_audit)


def _guard_says(hits):
    hits = list(dict.fromkeys(hits))
    return ("rule 17: this test reached the home or the tools of the account running the suite: "
            + "; ".join(hits[:5]) + (f"; and {len(hits) - 5} more" if len(hits) > 5 else ""))


@pytest.fixture(autouse=True)
def _own_home_only(request):
    """The guard (G19.5): a test that opens, lists or changes anything in the real home's .claude, .config or
    LaunchAgents or the real tmux folder, starts a child with the real HOME or none or without its own tmux
    folder (TMUX_TMPDIR), or runs this machine's own
    tmux, claude, crontab, systemctl, loginctl or launchctl (found outside a temporary folder), fails: in its
    own result when it happened in the test, else at teardown."""
    _seen.update(test=request.node.nodeid, hits=[])
    yield
    hits, _seen["test"] = _seen["hits"], None
    if hits:
        pytest.fail(_guard_says(hits), pytrace=False)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if call.when == "call" and _seen["hits"] and rep.passed:
        rep.outcome, rep.longrepr = "failed", _guard_says(_seen["hits"])
        _seen["hits"] = []                              # said once, in the test's own result


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
    shutil.rmtree(_SESSION_TMP, ignore_errors=True)
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
