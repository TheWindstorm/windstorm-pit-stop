"""G16.1 to G16.3: the conformance kit's checks read files only, print one line per check, and show its own fill.

The transcript these tests read is tests/fixtures/transcript-2.1.330-scrubbed.jsonl: 260 rows recorded from a
real Claude Code 2.1.330 session around one automatic compaction, with every piece of content, path, id and
name replaced (row kinds, times, model ids and token counts kept)."""
import builtins
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit"))
import conformance as cf  # noqa: E402

REAL = os.path.join(ROOT, "tests", "fixtures", "transcript-2.1.330-scrubbed.jsonl")
SID = "11111111-2222-3333-4444-555555555555"


def fake_config(tmp, version="0.5.3", sid=SID, t=None, transcript=REAL):
    cfg = tmp / "cfg"
    (cfg / "pit-stop" / "seen").mkdir(parents=True)
    (cfg / "plugins").mkdir()
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))
    (cfg / "pit-stop" / "precompact.log").write_text(
        f"{when} session={sid[:8]} trigger=auto tokens=120000 HOLD mid-action:tool-use ceiling=132000(default)\n"
        f"{when} session={sid[:8]} trigger=auto tokens=133000 ALLOW ceiling>=132000(default)\n")
    (cfg / "pit-stop" / "seen" / sid).write_text("")
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {cf.PLUGIN: [{"scope": "user", "version": version}]}}))
    if transcript:
        (cfg / "projects" / "-work-kit").mkdir(parents=True)
        shutil.copyfile(transcript, cfg / "projects" / "-work-kit" / (sid + ".jsonl"))
    return str(cfg), sid


def snapshot(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime_ns)
    return out


def test_G16_1_conformance_checks_read_files_only(tmp_path, monkeypatch):
    cfg, sid = fake_config(tmp_path, version="0.6.0")
    before = snapshot(cfg)
    opened = []
    real_open = builtins.open

    def spy_open(path, mode="r", *a, **k):
        opened.append((str(path), mode))
        return real_open(path, mode, *a, **k)

    def no_subprocess(*a, **k):
        raise AssertionError("a check started a process")

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(subprocess, "run", no_subprocess)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    res = cf.run_checks(cfg, time.time() - 60, "0.5.3", [sid])
    assert [r[0] for r in res] == list(cf.CHECKS) == ["fill", "hold", "allow", "stop", "updated"]
    assert all(r[1] for r in res), res
    assert res[0][2].endswith(sid + ".jsonl")                        # fill's evidence is the session's own transcript
    assert opened and all(m in ("r", "rb") for _, m in opened), opened
    assert snapshot(cfg) == before
    # each check fails when its file does not show it
    assert not cf.run_checks(cfg, time.time() + 3600, "0.6.0", [sid])[1][1]      # lines older than the start
    assert [r[1] for r in cf.run_checks(cfg, time.time() - 60, "0.6.0", [sid])] == [True, True, True, True, False]
    assert not any(r[1] for r in cf.run_checks(cfg, time.time() - 60, "0.6.0", ["99999999-x"])[:4])


def test_G16_1_missing_files_fail_not_crash(tmp_path):
    res = cf.run_checks(str(tmp_path / "nothing"), time.time(), "0.5.3")
    assert [r[1] for r in res] == [False] * len(cf.CHECKS)


def test_G16_3_the_kit_shows_its_own_fill(tmp_path):
    """Seen 2026-11-22: a kit session reached 25K of a 100K window and nothing said so, so hold and allow
    FAILed for the kit's reason, not Pit Stop's. The peak comes from the session's own transcript."""
    rows = [json.loads(x) for x in open(REAL, encoding="utf-8")]
    kinds = {r["type"] for r in rows}
    assert {"assistant", "user", "system", "attachment", "api-request"} <= kinds      # the real mix of row kinds
    # one real turn ran up to 544,817 tokens and compacted; the next prompt started at 73,324
    assert cf.peaks_by_turn(REAL) == [544_817, 73_324]
    assert [r.get("subtype") for r in rows].index("compact_boundary") < [cf.is_prompt(r) for r in rows].index(True)
    cfg, sid = fake_config(tmp_path)
    assert cf.check_fill(cfg, time.time() - 60, 500_000, [sid])[0] is True
    ok, ev = cf.check_fill(cfg, time.time() - 60, 600_000, [sid])
    assert ok is False and ev.endswith(sid + ".jsonl")
    # rows that are not model calls, a broken line and a sub-agent's call never count
    odd = tmp_path / "odd.jsonl"
    odd.write_text(open(REAL, encoding="utf-8").read() + "{not json\n" + json.dumps(
        {"type": "assistant", "isSidechain": True, "message": {"usage": {"input_tokens": 9_000_000}}}) + "\n")
    assert cf.peaks_by_turn(str(odd)) == [544_817, 73_324]
    assert cf.peaks_by_turn(str(tmp_path / "none.jsonl")) == []


def test_G16_3_the_kit_fills_turn_by_turn_and_keeps_its_evidence(tmp_path, monkeypatch):
    """Each turn reads the transcript; the kit stops when precompact.log shows HOLD and ALLOW, or when the
    context stops growing, and records the peak of every turn (numbers only) where it outlives the account."""
    cfg, sid = fake_config(tmp_path, transcript=None)
    log = os.path.join(cfg, "pit-stop", "precompact.log")
    os.remove(log)
    proj = os.path.join(cfg, "projects", "-work-kit")
    os.makedirs(proj)
    real = [json.loads(x) for x in open(REAL, encoding="utf-8")]
    call = next(r for r in real if r["type"] == "assistant" and cf.model_input(r))
    prompt = next(r for r in real if cf.is_prompt(r))
    plan = {"sizes": [30_000, 70_000, 125_000], "hold_at": 3}
    turns = []

    def fake_run(cmd, **kw):
        turns.append(cmd)
        n = len(turns)
        size = plan["sizes"][min(n, len(plan["sizes"])) - 1]
        with open(os.path.join(proj, sid + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(prompt) + "\n")
            c = json.loads(json.dumps(call))
            c["message"]["usage"] = {"input_tokens": 10, "cache_read_input_tokens": size - 10, "output_tokens": 5}
            f.write(json.dumps(c) + "\n")
        if n == plan["hold_at"]:
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with open(log, "a") as f:
                f.write(f"{when} session={sid[:8]} trigger=auto tokens=101000 HOLD mid-action:tool-use ceiling=132000\n"
                        f"{when} session={sid[:8]} trigger=auto tokens=125000 ALLOW ceiling>=110000\n")
        out = json.dumps({"type": "system", "subtype": "init", "session_id": sid})
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ev = []
    since = time.time() - 1
    assert cf.session_cli(cfg, 100_000, "m", 6, since, False, ev.append) == [sid]
    assert len(turns) == 3 and "--continue" not in turns[0] and "--continue" in turns[1]
    assert [e["turn"] for e in ev] == [1, 2, 3] and [e["peak"] for e in ev] == [30_000, 70_000, 125_000]
    assert all(set(e) == {"at", "turn", "peak", "window", "transcript"} for e in ev)
    assert "part08.txt to part15.txt" in turns[1][-1] and "no offset and no limit" in turns[0][-1]       # new files each turn
    # the context stops growing: the kit stops instead of running out its turns, and the fill check says so
    os.remove(log)
    os.remove(os.path.join(proj, sid + ".jsonl"))
    turns.clear()
    plan.update(sizes=[25_000, 25_000], hold_at=0)
    ev.clear()
    cf.session_cli(cfg, 100_000, "m", 6, since, False, ev.append)
    assert len(turns) == 2 and [e["peak"] for e in ev] == [25_000, 25_000]
    assert cf.check_fill(cfg, since, 100_000, [sid])[0] is False


def test_G16_2_conformance_output_format(tmp_path, capsys, monkeypatch):
    cfg, sid = fake_config(tmp_path, t=time.time())
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", cfg)
    out = tmp_path / "result.txt"
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 60))
    code = cf.main(["--start", "none", "--since", since, "--base-version", "0.5.2", "--out", str(out)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert len(lines) == len(cf.CHECKS)
    for line, check in zip(lines, cf.CHECKS):
        assert re.fullmatch(rf"{check}: (PASS|FAIL) \S.*", line), line
    assert out.read_text().splitlines() == lines
    # the evidence file sits next to the result: the session's peaks, turn by turn, and no content
    ev = [json.loads(x) for x in open(str(out) + ".evidence.jsonl")]
    assert [e["peak"] for e in ev] == [544_817, 73_324] and all(e["window"] == 100_000 for e in ev)
    code = cf.main(["--start", "none", "--since", since, "--base-version", "0.5.3", "--evidence", str(tmp_path / "ev1.jsonl")])
    assert code == 1 and capsys.readouterr().out.splitlines()[-1].startswith("updated: FAIL ")
    # not filled: its own exit code, and a line that says hold and allow say nothing about Pit Stop
    code = cf.main(["--start", "none", "--since", since, "--base-version", "0.5.2", "--window", "600000",
                    "--evidence", str(tmp_path / "ev2.jsonl")])
    cap = capsys.readouterr()
    assert code == 3 and cap.out.splitlines()[0].startswith("fill: FAIL ")
    assert "the kit did not fill the session past the window (600000)" in cap.err
    assert len(cap.out.strip().splitlines()) == len(cf.CHECKS)
