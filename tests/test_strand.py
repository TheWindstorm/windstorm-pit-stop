"""The DNA strand's invariants (DNA-STRAND-MASTER-PLAN.md, Part 3), and the checks that keep the
plan and the code from drifting apart. Standard library only; reads files, runs nothing."""
import ast
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

CODON = re.compile(r"^\*\*(G\d+)\.(\d+) ([^*]+?)\.\*\*(.*)$")
PROOF = re.compile(r"Proof: `(test_\w+)`")


def plan_text():
    return open(PLAN, encoding="utf-8").read()


def codons():
    """(id, title, text, is_open) for every live codon in the plan."""
    out = []
    for line in plan_text().split("\n"):
        m = CODON.match(line)
        if m and not m.group(3).startswith("Removed in plan"):
            out.append((f"{m.group(1)}.{m.group(2)}", m.group(3), line, m.group(3).endswith("(open)")))
    return out


def code_files():
    out = []
    for pat in ("scripts/*.py", "hooks/*.py", "hooks/*.json", "skills/*/SKILL.md"):
        out += [os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, pat))]
    return sorted(out)


def py_trees():
    for rel in code_files():
        if rel.endswith(".py"):
            yield rel, ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())


def defined_test_names():
    names = set()
    for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
        for n in ast.parse(open(p, encoding="utf-8").read()).body:
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_"):
                names.add(n.name)
    return names


def first_str(node):
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        e = node.elts[0]
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return e.value
    return None


def test_INV1_no_command_contains_sudo():
    for rel, tree in py_trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                for e in node.elts:
                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                        assert os.path.basename(e.value) not in ("sudo", "su", "doas"), \
                            f"{rel}:{node.lineno} builds a command with {e.value}"


def test_INV2_every_hook_is_guarded_and_capped():
    hooks = json.load(open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8"))["hooks"]
    n = 0
    for event, groups in hooks.items():
        for g in groups:
            for h in g["hooks"]:
                n += 1
                assert '[ -f "$F" ] || exit 0' in h["command"], f"{event}: no missing-file guard"
                assert 0 < h.get("timeout", 999) <= 10, f"{event}: timeout over 10 s"
    assert n >= 3
    exits2 = set()
    for rel, tree in py_trees():
        if not rel.startswith("hooks/"):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value == 2:
                exits2.add(rel)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple) and node.value.elts \
                    and isinstance(node.value.elts[0], ast.Constant) and node.value.elts[0].value == 2:
                exits2.add(rel)
    assert exits2 <= {"hooks/precompact_wait_for_break.py", "hooks/stop_pit_stop.py"}, exits2


def test_INV3_all_typing_goes_through_type_line_or_clear_ours():
    tree = ast.parse(open(os.path.join(ROOT, "scripts", "restart_at_break.py"), encoding="utf-8").read())
    found = 0
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and node.value == "send-keys":
                found += 1
                assert fn.name in ("type_line", "clear_ours"), f"send-keys in {fn.name}()"
    src = open(os.path.join(ROOT, "scripts", "restart_at_break.py"), encoding="utf-8").read()
    assert found == src.count('"send-keys"') and found >= 2


def test_INV4_note_max_is_560():
    tree = ast.parse(open(os.path.join(ROOT, "scripts", "restart_at_break.py"), encoding="utf-8").read())
    vals = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "NOTE_MAX" for t in n.targets)]
    assert vals == [560]


ALLOWED_URLS = {"https://downloads.claude.ai/claude-code-releases/"}
NOT_CONTACTED = re.compile(r"^http://www\.apple\.com/DTDs/")   # a plist doctype, never fetched
NET_MODULES = {"urllib", "urllib.request", "http", "http.client", "socket", "ssl", "requests", "httpx"}


def test_INV5_one_web_address_only():
    for rel, tree in py_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for url in re.findall(r"https?://[^\s\"'<>)]+", node.value):
                    if NOT_CONTACTED.match(url) or any(url.startswith(a) for a in ALLOWED_URLS):
                        continue
                    assert False, f"{rel}: web address {url}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for mod in mods:
                    if mod in NET_MODULES or mod.split(".")[0] in ("requests", "httpx"):
                        assert rel == "scripts/install_method.py", f"{rel} imports {mod}"


def test_INV6_no_model_calls():
    for rel, tree in py_trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for mod in mods:
                    assert mod.split(".")[0] not in ("anthropic", "openai", "claude_agent_sdk"), f"{rel} imports {mod}"
            if isinstance(node, (ast.List, ast.Tuple)):
                strs = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if strs and os.path.basename(strs[0]) == "claude":
                    assert not {"-p", "--print"} & set(strs), f"{rel}:{node.lineno} starts claude -p"


def test_INV7_every_no_is_kept():
    import install_method as im
    w = lambda n: "/usr/bin/" + n   # noqa: E731
    m = {"behind": True, "released": "2.1.330", "method": "mise", "pin": [None, "2.1.322"], "path": "/usr/bin/claude",
         "updater": "on", "channel": "latest", "admin": False}
    assert im.update_plan(m, cfg={}, which=w, env={}, settings={})[0]
    for cfg, env, settings in (({"hold_claude_version": True}, {}, {}), ({}, {"DISABLE_UPDATES": "1"}, {}),
                               ({}, {"DISABLE_AUTOUPDATER": "1"}, {}), ({}, {}, {"env": {"DISABLE_UPDATES": "1"}})):
        for meth in ("native", "npm", "homebrew", "mise"):
            assert im.update_plan(dict(m, method=meth), cfg=cfg, which=w, env=env, settings=settings)[0] is None
    proj = dict(m, pin=["/work/proj/mise.toml", "2.1.322"])
    assert im.update_plan(proj, cfg={}, which=w, env={}, settings={})[0] is None


def test_INV8_every_file_and_setting_has_a_codon():
    text = plan_text()
    spans = re.findall(r"`([^`\n]+)`", text)
    named = set(re.findall(r"(?<![\w/.])((?:scripts|hooks|skills|tests|docs)/[\w./*<>-]+\w)", " ".join(spans)))
    for rel in code_files():
        assert any(rel in sp for sp in spans), f"{rel} has no codon"
    open_text = "\n".join(t for _, _, t, is_open in codons() if is_open)
    for rel in named:
        if "*" in rel or "<" in rel:
            continue
        if not os.path.exists(os.path.join(ROOT, rel)):
            open_spans = re.findall(r"`([^`\n]+)`", open_text)
            assert any(rel in sp for sp in open_spans), f"the plan names {rel}, which does not exist"
    tree = ast.parse(open(os.path.join(ROOT, "scripts", "restart_at_break.py"), encoding="utf-8").read())
    cfg = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "CFG" for t in n.targets)]
    keys = [k.value for k in cfg[0].value.keys]
    missing = [k for k in keys if not re.search(r"(?<![A-Za-z0-9_])" + re.escape(k) + r"(?![A-Za-z0-9_])", text)]
    assert not missing, f"restart.json keys with no codon: {missing}"


def test_INV9_every_built_codon_has_its_test():
    names = defined_test_names()
    for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
        if os.path.basename(p) == "test_strand.py":   # this file names the patterns it looks for
            continue
        src = open(p, encoding="utf-8").read()
        assert not re.search(r"mark\.(skip|skipif|xfail)|pytest\.xfail\(", src), f"{p}: a skipped test"
        for m in re.finditer(r"pytest\.skip\(([^)]*)\)", src):
            assert m.group(1) == '"needs a POSIX sh"', f"{p}: pytest.skip({m.group(1)})"
    cs = codons()
    assert len(cs) >= 100
    for cid, _, line, is_open in cs:
        m = PROOF.search(line)
        assert m, f"{cid} names no test"
        prefix = "test_" + cid.replace(".", "_")
        assert m.group(1) == prefix or m.group(1).startswith(prefix + "_"), f"{cid}: proof {m.group(1)} is not {prefix}_..."
        if not is_open:
            assert m.group(1) in names, f"{cid} is marked built but {m.group(1)} does not exist"


BANNED = ("the first to", "first ever", "identical to", "the same algorithm", "a law of", "law of nature",
          "the ribosome reads the DNA")


def test_INV10_wording():
    for p in glob.glob(os.path.join(ROOT, "**", "*.md"), recursive=True):
        if "/.git/" in p:
            continue
        t = open(p, encoding="utf-8").read()
        t = re.sub(r"```.*?```", "", t, flags=re.S)
        t = re.sub(r"`[^`\n]*`", "", t)
        for phrase in BANNED:
            words = r"\s+".join(map(re.escape, phrase.split()))
            assert not re.search(r"\b" + words + r"\b", t, re.I), f"{os.path.relpath(p, ROOT)}: {phrase!r}"
