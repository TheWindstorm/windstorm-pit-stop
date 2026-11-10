import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "precompact_wait_for_break.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))


def usage(n):
    return {"input_tokens": 10, "cache_read_input_tokens": n, "output_tokens": 5}


def transcript(tmp, name, entries):
    p = tmp / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(p)


def a(kind, n=200000):
    return {"type": "assistant", "message": {"content": [{"type": kind}], "usage": usage(n)}}


def run_hook(payload, tmp, env_extra=None):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp / "cfg"))
    env.pop("PIT_STOP_CEILING", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "-I", HOOK], input=payload if isinstance(payload, str) else json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


def test_hook_decisions(tmp_path):
    tr_result = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}
    prompt = {"type": "user", "message": {"content": "next task"}}
    cases = [
        ("mid_result", [a("tool_use"), tr_result], {}, 2),
        ("tool_asked", [a("tool_use")], {}, 2),
        ("new_prompt", [a("text"), prompt], {}, 0),
        ("turn_ended", [a("text")], {}, 0),
        ("env_ceiling", [a("tool_use", 800000)], {"PIT_STOP_CEILING": "700000"}, 0),
        ("under_env_ceiling", [a("tool_use", 96000)], {"PIT_STOP_CEILING": "700000"}, 2),
    ]
    for name, entries, env, want in cases:
        p = transcript(tmp_path, name, entries)
        code, out = run_hook({"trigger": "auto", "transcript_path": p, "session_id": name}, tmp_path, env)
        assert code == want, name
        assert out == "", name


def test_hook_always_allows_manual_bad_and_missing(tmp_path):
    p = transcript(tmp_path, "m", [a("tool_use")])
    assert run_hook({"trigger": "manual", "transcript_path": p}, tmp_path) == (0, "")
    assert run_hook({"trigger": "auto", "transcript_path": str(tmp_path / "none.jsonl")}, tmp_path) == (0, "")
    assert run_hook("not json", tmp_path) == (0, "")


def test_hook_default_flex_ceiling(tmp_path):
    # no configured ceiling: first hold at 160K -> ceiling 176K; 170K holds, 180K allows
    p1 = transcript(tmp_path, "f1", [a("tool_use", 160000)])
    assert run_hook({"trigger": "auto", "transcript_path": p1, "session_id": "flex"}, tmp_path)[0] == 2
    p2 = transcript(tmp_path, "f2", [a("tool_use", 170000)])
    assert run_hook({"trigger": "auto", "transcript_path": p2, "session_id": "flex"}, tmp_path)[0] == 2
    p3 = transcript(tmp_path, "f3", [a("tool_use", 180000)])
    assert run_hook({"trigger": "auto", "transcript_path": p3, "session_id": "flex"}, tmp_path)[0] == 0


def test_hook_profile_ceiling(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "pit-stop.json").write_text(json.dumps({"ceiling": 300000}))
    p = transcript(tmp_path, "pc", [a("tool_use", 310000)])
    assert run_hook({"trigger": "auto", "transcript_path": p, "session_id": "pc", "cwd": str(proj)}, tmp_path)[0] == 0


def test_reading_trim_keeps_every_link():
    import reading_check as rc
    lines = ["- READ FIRST: free text that must stay exactly as it is."]
    for i in range(120):
        lines.append(f"- [Mind rule {i}](feedback_mind_rule{i}.md) — hook text number {i} " + "x" * 150)
    for i in range(90):
        lines.append(f"- [Misc {i}](reference_item{i}_x.md) — a reference")
    lines.append("- [Mind rule 3](feedback_mind_rule3.md) — duplicate")
    text = "\n".join(lines) + "\n"
    new = rc.trim(text)
    before = {m[1] for m in rc.LINK.findall(text)}
    after = {m[1] for m in rc.LINK.findall(new)}
    assert before == after
    assert lines[0] in new
    assert new.count("\n") < text.count("\n")


def test_optimizer_recommends_within_bounds(tmp_path, monkeypatch):
    import pitstop_common as pc
    import optimize as op
    monkeypatch.setattr(pc, "PROJECTS", str(tmp_path / "projects"))
    proj = tmp_path / "work"
    proj.mkdir()
    d = tmp_path / "projects" / pc.project_key(str(proj))
    d.mkdir(parents=True)
    ents = []
    t = 0
    for cycle in range(3):
        for i in range(20):
            t += 60
            ts = f"2026-11-0{1 + cycle}T10:{i:02d}:00Z"
            ents.append({"type": "user", "message": {"content": "do it"}, "timestamp": ts})
            ents.append({"type": "assistant", "timestamp": ts, "message": {
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": f"/f{i % 5}.py"}}],
                "usage": usage(300000 + i * 10000)}})
        ents.append({"type": "system", "subtype": "compact_boundary", "timestamp": ts})
    (d / "s1.jsonl").write_text("\n".join(json.dumps(e) for e in ents) + "\n")
    m = op.analyze(str(proj), None)
    assert m["compactions"] == 3
    rec = op.recommend(m)
    assert 300000 <= rec["window"] <= 700000
    assert rec["window"] < rec["ceiling"] <= 850000
    assert rec["model_effort_suggestions"]


def test_hook_clamps_a_huge_project_ceiling(tmp_path):
    # a cloned repo ships ceiling 10M: clamped to 1.5x the first hold (and 95% of the window)
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "pit-stop.json").write_text(json.dumps({"ceiling": 10_000_000}))
    pay = {"trigger": "auto", "session_id": "clamp", "cwd": str(proj)}
    p1 = transcript(tmp_path, "c1", [a("tool_use", 160000)])
    assert run_hook(dict(pay, transcript_path=p1), tmp_path)[0] == 2      # first hold at 160K
    p2 = transcript(tmp_path, "c2", [a("tool_use", 189000)])
    assert run_hook(dict(pay, transcript_path=p2), tmp_path)[0] == 2      # cap = min(240K, 190K)
    p3 = transcript(tmp_path, "c3", [a("tool_use", 190000)])
    assert run_hook(dict(pay, transcript_path=p3), tmp_path)[0] == 0      # never past 190K
    log = (tmp_path / "cfg" / "pit-stop" / "precompact.log").read_text()
    assert "profile-clamped" in log


def test_hook_log_rotates(tmp_path):
    state = tmp_path / "cfg" / "pit-stop"
    state.mkdir(parents=True)
    (state / "precompact.log").write_text("x" * 5_000_001)
    run_hook({"trigger": "manual"}, tmp_path)
    assert (state / "precompact.log.1").stat().st_size == 5_000_001
    assert (state / "precompact.log").stat().st_size < 1000


def test_restart_helper_reads_the_input_box(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    import importlib
    import pitstop_common
    importlib.reload(pitstop_common)
    import restart_at_break as rab
    importlib.reload(rab)
    border = "─" * 40
    dim = "\x1b[2mTry \"fix the tests\"\x1b[0m"
    assert rab.input_box_text([border, "❯ " + dim, border, "  status line"]) == ""
    text = "[AUTOMATED note] " + "word " * 30
    wrapped = [border, "❯ " + text[:60], "  " + text[60:120], "  " + text[120:], border, "  status"]
    assert rab.same_text(rab.input_box_text(wrapped), text)
    mixed = [border, "❯ " + text[:60] + "hello", "  " + text[60:], border]
    assert not rab.same_text(rab.input_box_text(mixed), text)
    assert rab.input_box_text(["no prompt here"]) is None


def _rab(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    import importlib
    import pitstop_common
    importlib.reload(pitstop_common)
    import restart_at_break as rab
    importlib.reload(rab)
    return rab


def test_restart_rules_at_a_break(tmp_path, monkeypatch):
    rab = _rab(tmp_path, monkeypatch)
    cfg = dict(rab.CFG)
    # working sessions are never restarted
    assert rab.at_a_break("busy", 9999, 50_000, 10, cfg)[0] is False
    # (a) just compacted: a big context is fine once idle >= 60 s
    assert rab.at_a_break("idle", 61, 250_000, 600, cfg)[0] is True
    ok, why = rab.at_a_break("idle", 30, 250_000, 600, cfg)
    assert ok is False and "compacted" in why
    # a compaction older than the window does not count
    assert rab.at_a_break("idle", 120, 250_000, 1801, cfg)[0] is False
    # (b) small context still needs the long idle
    assert rab.at_a_break("idle", 301, 90_000, None, cfg)[0] is True
    assert rab.at_a_break("idle", 120, 90_000, None, cfg)[0] is False
    # (c) cold cache: a long idle allows any size
    assert rab.at_a_break("idle", 3600, 400_000, None, cfg)[0] is True
    # every refusal says why
    ok, why = rab.at_a_break("idle", 120, 250_000, None, cfg)
    assert ok is False and "needs:" in why
    assert rab.at_a_break("idle", -1, None, None, cfg) == (False, "no transcript found")
    # shell sessions pass the break test; the background-jobs note handles them after
    assert rab.at_a_break("shell", 61, 250_000, 600, cfg)[0] is True


def test_restart_reads_the_last_compaction_time(tmp_path, monkeypatch):
    rab = _rab(tmp_path, monkeypatch)
    entries = [a("text"),
               {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-10T12:00:00.000Z"},
               a("text"),
               {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-10T12:38:07.398Z"},
               {"type": "user", "message": {"content": "compact_boundary mentioned in text only"}},
               a("text")]
    p = transcript(tmp_path, "s1", entries)
    now = rab.utc_ts("2026-11-10T13:08:07Z")
    assert int(rab.last_compact_age(p, now=now)) == 1800
    # the marker found past a tail cut, and no compaction at all
    assert rab.last_compact_age(transcript(tmp_path, "s2", [a("text")]), now=now) is None
    assert rab.last_compact_age(str(tmp_path / "missing.jsonl")) is None
    assert rab.utc_ts("2026-07-01T00:00:00Z") == 1782864000  # summer: no DST shift


def test_restart_backs_off_after_a_typing_mismatch(tmp_path, monkeypatch):
    rab = _rab(tmp_path, monkeypatch)
    cfg = dict(rab.CFG)
    # 30 min, doubling per miss in a row, never more than 12 h
    assert [rab.mismatch_wait(n, cfg) for n in (1, 2, 3)] == [1800, 3600, 7200]
    assert rab.mismatch_wait(10, cfg) == 43200
    monkeypatch.setattr(rab, "LIVE", True)
    mmf = str(tmp_path / "s.mismatch")
    rab.note_typing(mmf, False, "mismatch", 100.0)
    rab.note_typing(mmf, False, "mismatch", 200.0)
    assert json.load(open(mmf)) == {"n": 2, "at": 200.0}
    # a busy screen is not a mismatch: the count stays
    rab.note_typing(mmf, False, "busy", 300.0)
    assert json.load(open(mmf))["n"] == 2
    # a send clears it
    rab.note_typing(mmf, True, "", 400.0)
    assert not os.path.exists(mmf)
    # dry runs never write state
    monkeypatch.setattr(rab, "LIVE", False)
    rab.note_typing(mmf, False, "mismatch", 500.0)
    assert not os.path.exists(mmf)


def test_reading_topic_reads_hyphen_and_underscore_names():
    import reading_check as rc
    assert rc.topic("feedback_mind_rule3.md") == "mind"            # the older underscore style
    assert rc.topic("feedback-board-line-format-2026-11-10.md") == "board"
    assert rc.topic("grant-decision-windy-only-scope-2026-11-09.md") == "decision"
    assert rc.topic("notes.md") is None and rc.topic("user-role.md") is None
    # hyphen names group like underscore names: 3+ with one topic share lines, and every link is kept
    lines = [f"- [Board lesson {i}](feedback-board-lesson{i}-2026-11-10.md) — hook {i} " + "x" * 120 for i in range(30)]
    text = "\n".join(lines) + "\n"
    new = rc.trim(text)
    assert {m[1] for m in rc.LINK.findall(new)} == {m[1] for m in rc.LINK.findall(text)}
    assert "**board" in new and new.count("\n") < text.count("\n")


def test_hook_command_falls_back_to_python_when_python3_is_a_stub(tmp_path):
    """Windows can ship a `python3` that is only a Microsoft Store stub. The hook command must then run `python`,
    and must pass the hook's own exit code through (2 = hold the compaction) on every system."""
    import pytest
    import shutil
    if os.name == "nt" or not shutil.which("sh"):
        pytest.skip("needs a POSIX sh")
    command = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))["hooks"]["PreCompact"][0]["hooks"][0]["command"]
    stub = tmp_path / "stubbin"
    stub.mkdir()
    (stub / "python3").write_text("#!/bin/sh\necho 'Python was not found; run without arguments to install from the Microsoft Store' >&2\nexit 9\n")
    (stub / "python3").chmod(0o755)
    os.symlink(sys.executable, stub / "python")
    hold = transcript(tmp_path, "hold", [a("tool_use")])
    done = transcript(tmp_path, "done", [a("text")])
    for path_first in (str(stub), os.path.dirname(sys.executable)):   # a stub python3 first, then a real one
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"), CLAUDE_PLUGIN_ROOT=ROOT,
                   PATH=path_first + os.pathsep + "/usr/bin:/bin")
        env.pop("PIT_STOP_CEILING", None)
        for tr, want in ((hold, 2), (done, 0)):
            r = subprocess.run(["sh", "-c", command], input=json.dumps({"trigger": "auto", "transcript_path": tr, "session_id": "t"}),
                               capture_output=True, text=True, env=env)
            assert r.returncode == want, (path_first, want, r.stderr)
            assert r.stdout == ""
