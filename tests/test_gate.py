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


def _pytest(root, *args, seconds=120, ci=False):
    """Run pytest on a planted world, in its own process group, stopped by its own group id at the limit.
    The planted run is in CI only when asked (`ci`), whatever this run is."""
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    env.update(COLUMNS="4096", **({"CI": "true"} if ci else {}))
    p = subprocess.Popen([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "console_output_style=classic",
                          *args, str(root / "tests")], cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True, env=env)
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
    # a pass is said by the gate itself, in its own line (a count alone is not a verdict)
    assert "G19 gate PASSED: whole run; 1 of 1 named proofs passed; 0 problems" in out, out
    code, out = _pytest(root, "-v", "-k", "good")
    assert code == 0 and "partial run: rows and codons not checked" in out and _gate_lines(out) == [], out
    assert "G19 gate PASSED" not in out, out
    code, out = _pytest(root, "-v")
    assert code == 1 and "G19 gate PASSED" not in out, out


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
    honest = text.split("def test_G1_3_")[0]
    # outside CI: an honest skip of a built proof is listed and does not fail the run
    root = _world(tmp_path / "honest", honest, {"1": "good", "2": "needs_a_program"})
    code, out = _pytest(root, "-v")
    assert "not run here: test_G1_2_needs_a_program (no such program here)" in out, out
    assert code == 0 and _gate_lines(out) == [], out
    # in CI: the same skip fails it, since every built proof must pass there
    code, out = _pytest(root, "-v", ci=True)
    assert code == 1 and _gate_lines(out) == ["test_G1_2_needs_a_program (codon G1.2) was not run in CI "
                                              "(no such program here), and a skip never counts as passed"], out
    # a skip with no reason fails every run, in CI or not, whole or partial
    root = _world(tmp_path / "bare", text, {"1": "good", "2": "needs_a_program"})
    for ci in (False, True):
        code, out = _pytest(root, "-v", ci=ci)
        lines = _gate_lines(out)
        assert code == 1 and "test_G1_3_skips_with_no_reason: skipped with no reason" in lines, out
        assert any("test_G1_3_skips_with_no_reason: skips with no reason" in ln for ln in lines), out
        code, out = _pytest(root, "-v", "-k", "no_reason", ci=ci)
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


GUARDED = '''import glob, os, pwd, shutil, subprocess, sys
REAL = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)

def test_reads_the_real_claude_folder():
    try:                                    # a name that is never there: the guard sees the try, nothing real is read
        open(os.path.join(REAL, ".claude", "no-such-file-pit-stop-guard-probe"))
    except OSError:
        pass

def test_reads_elsewhere_in_the_home():       # the tool also reads ~/.local/bin and ~/.local/share/claude
    try:
        open(os.path.join(REAL, ".local", "bin", "no-such-file-pit-stop-guard-probe"))
    except OSError:
        pass

def test_renames_into_the_home(tmp_path):
    (tmp_path / "x").write_text("x")
    try:                                    # a folder that is never there: the rename fails, the guard saw it
        os.rename(tmp_path / "x", os.path.join(REAL, "no-such-dir-pit-stop-guard-probe", "x"))
    except OSError:
        pass

def test_python_libraries_and_the_checkout_are_left_out():
    import site
    for d in (site.getusersitepackages(), os.path.dirname(os.path.abspath(__file__))):
        try:
            open(os.path.join(d, "no-such-file-pit-stop-guard-probe"))
        except OSError:
            pass

def test_starts_a_child_with_no_home():
    subprocess.run([sys.executable, "-I", "-c", "pass"], env={"PATH": os.environ["PATH"]})

def test_a_childs_path_reaches_this_machines_own_programs():
    subprocess.run([sys.executable, "-I", "-c", "pass"], env=dict(os.environ, PATH="/usr/bin:/bin"))

def test_runs_this_machines_own_program():
    real = shutil.which("systemctl", path="/usr/bin:/bin") or shutil.which("launchctl", path="/usr/bin:/bin")
    assert real, "neither systemctl nor launchctl here"
    subprocess.run([real, "--version" if real.endswith("systemctl") else "version"], capture_output=True)

def test_the_stub_answers_first():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    assert r.returncode == 97 and "rule 17 stub" in r.stderr

def test_stays_in_its_own_home(tmp_path):
    assert os.path.realpath(os.path.expanduser("~")) != REAL
    (tmp_path / "x").write_text("x")
'''


def test_G19_5_the_suite_never_uses_the_home_of_the_account_running_it(tmp_path):
    """Rule 17: conftest gives the run its own home, Claude Code folder and tmux folder, puts failing stubs for
    tmux, claude, crontab, systemctl, loginctl and launchctl first on PATH, and its guard fails a test that
    reaches the real home (its .claude, its .local, a rename into it), starts a child with no HOME or one whose PATH
    reaches this machine's own programs, or runs this machine's own systemctl or launchctl, and leaves out Python's own libraries and the checkout. Control: the same planted tests with the guard taken out all pass."""
    root = _world(tmp_path, GUARDED, {"1": "good"})
    code, out = _pytest(root, "-v")
    verdict = {n: ("PASSED" if f"::{n} PASSED" in out else "FAILED" if f"::{n} " in out and "PASSED" not in
                   [ln for ln in out.splitlines() if f"::{n} " in ln][0] else "?")
               for n in ("test_reads_the_real_claude_folder", "test_reads_elsewhere_in_the_home",
                         "test_renames_into_the_home", "test_python_libraries_and_the_checkout_are_left_out",
                         "test_starts_a_child_with_no_home", "test_a_childs_path_reaches_this_machines_own_programs",
                         "test_runs_this_machines_own_program", "test_the_stub_answers_first", "test_stays_in_its_own_home")}
    assert verdict == {"test_reads_the_real_claude_folder": "FAILED", "test_reads_elsewhere_in_the_home": "FAILED",
                       "test_renames_into_the_home": "FAILED",
                       "test_python_libraries_and_the_checkout_are_left_out": "PASSED",
                       "test_starts_a_child_with_no_home": "FAILED",
                       "test_a_childs_path_reaches_this_machines_own_programs": "FAILED",
                       "test_runs_this_machines_own_program": "FAILED", "test_the_stub_answers_first": "PASSED",
                       "test_stays_in_its_own_home": "PASSED"}, out[-4000:]
    assert "6 failed, 3 passed" in out and "rule 17: this test reached the home or the tools" in out, out[-4000:]
    conf = root / "tests" / "conftest.py"                                # the control: no guard, nothing caught
    conf.write_text(conf.read_text().replace("sys.addaudithook(_audit)", "pass"))
    code, out = _pytest(root, "-v")
    assert "rule 17:" not in out and "test_reads_the_real_claude_folder PASSED" in out, out[-4000:]


def test_G19_6_undo_in_the_suite_reaches_only_its_made_home(tmp_path, monkeypatch):
    """Seen 2026-11-24 03:32:14Z, before G19.5's guard: a test that moved only STATE_DIR called setup's undo, which
    with no record turns daily tuning off in pc.CONFIG, and that was this account's real config.json (and the real
    settings.json's marketplace entry). Now: before undo is called, every place it writes, by the module's own name,
    resolves inside the run's made home, or the test stops having called nothing; undo then changes the made home's
    config.json, and the real config.json's and settings.json's time and size do not move (stat only, never opened).
    The check is shown to see a move: pointed at a stand-in outside the made home, the same undo moves that file.
    G19.5's hook REPORTS a reach into the real home AFTER it happens (the test fails); it does not refuse it.
    Not seen red on the code of 03:32Z: not run again, that run would be the fault itself; the control stands for red.
    The place check was seen red with setup.SETTINGS alone moved outside the made home, before undo, nothing written."""
    import json
    import pwd
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import pitstop_common as pc
    import setup as su
    real_home = pwd.getpwuid(os.getuid()).pw_dir
    real = os.path.join(real_home, ".claude", "pit-stop", "config.json")
    real_settings = os.path.join(real_home, ".claude", "settings.json")
    # Before undo is called, EVERY place it writes, each by the module's own name for it, lies INSIDE the run's made
    # home: a world that moved only some of them (the fault of 03:32:14Z) stops here, having written nothing.
    made = os.path.realpath(os.environ["HOME"])
    assert made != os.path.realpath(real_home), ("the run's home is the real one", made)
    places = {"pitstop_common.CONFIG": pc.CONFIG, "pitstop_common.STATE_DIR": pc.STATE_DIR,
              "setup.SETTINGS": su.SETTINGS, "setup.UNDO": su.UNDO, "setup.BIN": su.BIN,
              "setup.UNIT_DIR": su.UNIT_DIR, "setup.PLIST": su.PLIST,
              # and what it reads by: a world that moved STATE_DIR and not these would read the real profiles
              "pitstop_common.FOLDERS": pc.FOLDERS, "pitstop_common.CLAUDE_DIR": pc.CLAUDE_DIR}
    outside = {k: v for k, v in places.items() if not os.path.realpath(v).startswith(made + os.sep)}
    if os.path.realpath(pc.HOME) != made:                     # HOME is the made home itself, not inside it
        outside["pitstop_common.HOME"] = pc.HOME
    assert not outside, ("undo would write or read outside the run's made home; undo NOT called", outside)

    def mark(p):
        try:
            st = os.stat(p)
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None
    before = mark(real), mark(real_settings)
    os.makedirs(os.path.dirname(pc.CONFIG), exist_ok=True)
    with open(pc.CONFIG, "w") as f:
        json.dump({"budget": "economy", "auto_tune": True}, f)
    out = "\n".join(su.undo(ledger=str(tmp_path / "no-record.json"), run_cmd=lambda *c: (0, "")))
    assert json.load(open(pc.CONFIG)).get("auto_tune") is None, out       # the made home's file was put back
    assert (mark(real), mark(real_settings)) == before, ("the real config.json or settings.json moved", before)
    # the control: the same undo pointed at a stand-in outside the made home moves that file, and the check sees it
    stand_in = tmp_path / "elsewhere" / "config.json"
    stand_in.parent.mkdir()
    stand_in.write_text(json.dumps({"auto_tune": True}))
    os.utime(stand_in, ns=(1, 1))
    was = mark(str(stand_in))
    monkeypatch.setattr(pc, "CONFIG", str(stand_in))
    su.undo(ledger=str(tmp_path / "no-record-2.json"), run_cmd=lambda *c: (0, ""))
    assert mark(str(stand_in)) != was and json.loads(stand_in.read_text()).get("auto_tune") is None



def test_G19_7_the_suites_lists_do_not_lean_on_the_machines_python(tmp_path, monkeypatch):
    """Seen 2026-11-24 on a CI runner (row P40): its Python sat in a folder of mode 777, and setup's open-folder
    check, which walks the folders of the Python program Pit Stop runs, rightly named it; five tests that count
    those lines failed there, and no machine of ours could show it, as each keeps its Python in a closed folder.
    Here the six tests that count those lines run in a child suite whose Python is in a folder any account can
    reach (made directly in /tmp) and open: the child opens its own folder once it has started, so nothing is
    started from an open folder (rule 28). The test holds its own premise: after the child has ended, the check
    run here with that Python names that folder, so the test fails, never passes, where it cannot show its point."""
    import tempfile
    sys.path.insert(0, HERE)
    from test_pitstop import made_python
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import setup as su
    opened = tempfile.mkdtemp(prefix="pitstop-open-python-", dir="/tmp")
    try:
        made_python(opened)
        stubs = tmp_path / "no-programs"
        stubs.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST")}
        env["PATH"] = str(stubs)
        import pytest as _pt                           # the child finds pytest where this run found it
        env["PYTHONPATH"] = os.pathsep.join(x for x in (os.path.dirname(os.path.dirname(_pt.__file__)),
                                                        env.get("PYTHONPATH")) if x)
        code = ("import os, sys; os.chmod(os.path.dirname(sys.executable), 0o777); import pytest; "
                "sys.exit(pytest.main(sys.argv[1:]))")
        r = subprocess.run([os.path.join(opened, "python3"), "-c", code, "-q", "-p", "no:cacheprovider",
                            os.path.join(HERE, "test_pitstop.py"), "-k",
                            "G8_5 or G8_6 or G12_13 or G12_14 or G12_15 or G12_18"],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0 and "6 passed" in r.stdout, r.stdout[-1500:]
        assert os.stat(opened).st_mode & 0o777 == 0o777, oct(os.stat(opened).st_mode)   # the child opened it
        monkeypatch.setattr(su, "group_members", lambda gid: set())
        monkeypatch.setattr(sys, "executable", os.path.join(opened, "python3"))      # started nothing more from it
        named = [w for w in su.open_scan()["ways"] if w[0] == opened]
        assert named and named[0][2] == "the Python program Pit Stop runs as you", (opened, su.open_scan()["ways"])
    finally:
        shutil.rmtree(opened, ignore_errors=True)
