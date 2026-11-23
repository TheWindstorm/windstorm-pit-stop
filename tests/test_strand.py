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
        if os.path.basename(p) in ("test_strand.py", "test_gate.py"):   # these files name the patterns they look for
            continue
        src = open(p, encoding="utf-8").read()
        assert not re.search(r"mark\.(skip|skipif|xfail)|pytest\.xfail\(", src), f"{p}: a skipped test"
        for m in re.finditer(r"pytest\.skip\(([^)]*)\)", src):
            assert m.group(1) in ('"needs a POSIX sh"', '"no git, or no road to github.com from this machine"'), \
                f"{p}: pytest.skip({m.group(1)})"
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


# G19.4: the plan's own form, checked here and in CI on every push and pull request (written in this
# repository; the vague-word list stays the lane's hand check, not CI's). Each check takes the plan's text,
# so the test can plant a bad copy and see it refused.
ID = re.compile(r"(?<![\w.])(G\d+\.\d+|INV\d+)(?![\w])")
WORD_CAP = 80


def _sections(text):
    """{'## heading': [(line number, line), ...]} for each level-2 part of the plan."""
    out, current = {}, None
    for n, line in enumerate(text.split("\n"), 1):
        if line.startswith("## "):
            current = line
            out[current] = []
        elif current:
            out[current].append((n, line))
    return out


def _part(text, word):
    return next((lines for head, lines in _sections(text).items() if word in head), [])


def plan_form_problems(text, changelog):
    problems = []
    # 1. the version line
    m = re.search(r"^\*\*Plan version (\d+\.\d+) · (\d{4}-\d{2}-\d{2}) · ", text, re.M)
    if not m:
        problems.append("no version line '**Plan version X.Y · YYYY-MM-DD · ...**'")
    # 2, 3, 5. genes under Genes, codons of the right form under their own gene, numbers 1, 2, 3 with no gaps
    genes, gene, codons, seen_genes = {}, None, [], []
    for n, line in _part(text, "Genes"):
        g = re.match(r"^### G(\d+) ", line)
        if g:
            gene = int(g.group(1))
            if gene in genes:
                problems.append(f"line {n}: gene G{gene} is named twice")
            genes[gene] = []
            seen_genes.append(gene)
            continue
        if re.match(r"^\*\*G\d", line):
            c = CODON.match(line)
            if not c:
                problems.append(f"line {n}: starts like a codon but is not '**G<n>.<m> Title.** ...'")
                continue
            if gene is None or int(c.group(1)[1:]) != gene:
                problems.append(f"line {n}: {c.group(1)}.{c.group(2)} is not under the heading of gene {c.group(1)}")
            else:
                genes[gene].append(int(c.group(2)))
            codons.append((n, f"{c.group(1)}.{c.group(2)}", c.group(3), line))
    for g in [x for x in re.findall(r"^### G(\d+) ", text, re.M) if not any(n for n, ln in _part(text, "Genes")
                                                                              if ln.startswith(f"### G{x} "))]:
        problems.append(f"gene G{g} is not under a heading that contains 'Genes'")
    if seen_genes != list(range(1, len(seen_genes) + 1)):
        problems.append(f"genes do not run G1, G2, G3 with no gaps or repeats: {seen_genes}")
    for g, nums in genes.items():
        if nums != list(range(1, len(nums) + 1)):
            problems.append(f"codons of G{g} do not run 1, 2, 3 with no gaps or repeats: {nums}")
    # 4, 5. every invariant has a Proof, numbered 1, 2, 3
    invs = [(n, int(i.group(1)), line) for n, line in _part(text, "Invariants")
            for i in [re.match(r"^\*\*INV(\d+) ", line)] if i]
    for n, num, line in invs:
        if not re.search(r"Proof: (`test_INV" + str(num) + r"(_\w+)?`|by hand)", line):
            problems.append(f"line {n}: INV{num} has no Proof: naming test_INV{num} or 'by hand'")
    if [num for _, num, _ in invs] != list(range(1, len(invs) + 1)):
        problems.append("invariants do not run INV1, INV2, INV3 with no gaps or repeats")
    # 6. every codon and invariant named anywhere exists
    have = {cid for _, cid, _, _ in codons} | {f"INV{num}" for _, num, _ in invs}
    for n, line in enumerate(text.split("\n"), 1):
        for name in ID.findall(line):
            if name not in have:
                problems.append(f"line {n}: {name} is named but not in the plan")
    # 7. Needs: names codons that exist, and no chain loops back
    needs = {}
    for n, cid, _, line in codons:
        nd = re.search(r"Needs: (.*?)(?:\. Proof:|$)", line)
        if nd:
            targets = ID.findall(nd.group(1))
            if not targets:
                problems.append(f"line {n}: Needs: in {cid} names no codon")
            needs[cid] = [t for t in targets if t in have]
    state = {}

    def loops(c, path):
        if state.get(c) == "done":
            return None
        if c in path:
            return path[path.index(c):] + [c]
        for t in needs.get(c, []):
            hit = loops(t, path + [c])
            if hit:
                return hit
        state[c] = "done"
        return None
    for cid in sorted(needs):
        hit = loops(cid, [])
        if hit:
            problems.append("Needs: loops: " + " -> ".join(hit))
            break
    # 8. every gene is in Milestones or Open questions
    later = "\n".join(ln for part in ("Milestones", "Open questions") for _, ln in _part(text, part))
    for g in seen_genes:
        if not re.search(rf"(?<![\w.])G{g}(?![\d])", later):
            problems.append(f"gene G{g} is not in Milestones or Open questions")
    # 9. every open question has ASSUMES:
    for n, line in _part(text, "Open questions"):
        if re.match(r"^\d+\. ", line) and "ASSUMES:" not in line:
            problems.append(f"line {n}: open question has no ASSUMES:")
    # 10. the changelog's newest Plan entry is the plan's version
    newest = re.search(r"^## Plan (\d+\.\d+) ", changelog, re.M)
    if not newest:
        problems.append("CHANGELOG.md has no '## Plan X.Y' heading")
    elif m and newest.group(1) != m.group(1):
        problems.append(f"CHANGELOG.md's newest Plan entry is {newest.group(1)}, the plan is {m.group(1)}")
    # 11. the word cap: a codon's words, code spans blanked
    for n, cid, _, line in codons:
        words = len(re.sub(r"`[^`\n]*`", " ", line).split())
        if words > WORD_CAP:
            problems.append(f"line {n}: {cid} has {words} words, over the cap of {WORD_CAP}")
    return problems


def test_G19_4_ci_runs_the_plan_check():
    """CI runs this file on every push and pull request; the real plan has no problem; a planted fault of each kind
    is refused."""
    with open(os.path.join(ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8") as f:
        ci = f.read()
    assert re.search(r"^on: \[push, pull_request\]$", ci, re.M), "CI must run on every push and pull request"
    assert re.search(r"-m pytest .*\btests/", ci), "CI must run the tests folder, which holds this file"
    text = plan_text()
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        changelog = f.read()
    assert plan_form_problems(text, changelog) == []
    first = codons()[0][2]                                       # the G1.1 line
    inv1 = next(ln for ln in text.split("\n") if ln.startswith("**INV1 "))
    planted = {
        "no version line": text.replace("**Plan version ", "**Plan ", 1),
        "starts like a codon": text.replace(first, first.replace(".**", "**", 1)),
        "is not under the heading of gene": text.replace(first, first.replace("**G1.1 ", "**G2.1 ", 1)),
        "do not run 1, 2, 3": text.replace(first, first.replace("**G1.1 ", "**G1.99 ", 1)),
        "has no Proof: naming test_INV1": text.replace(inv1, re.sub(r" Proof: `[^`]+`\.?", "", inv1)),
        "G99.1 is named but not in the plan": text.replace(first, first + " See G99.1."),
        "Needs: loops": text.replace(first, first.replace(" Proof:", " Needs: G1.1. Proof:", 1)),
        "is not in Milestones or Open questions": text.replace("- M0 (released 0.1 to 0.5): G1,", "- M0: ,"),
        "open question has no ASSUMES:": text.replace("## Part 6. Open questions\n",
                                                      "## Part 6. Open questions\n\n99. A question?\n", 1),
        "newest Plan entry": text.replace("**Plan version ", "**Plan version 9", 1),
        "over the cap of 80": text.replace(first, first + " word" * 90),
    }
    for want, bad in planted.items():
        assert bad != text, want
        got = plan_form_problems(bad, changelog)
        assert any(want in p for p in got), (want, got)
