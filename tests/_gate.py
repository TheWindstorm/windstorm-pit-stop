"""G19: a proof is a test that ran. Read by tests/conftest.py and by the G19 tests; standard library only.

What counts is what the test runner itself reports as PASSED in this run, never a name found in a file.
The static checks here catch the ways a test can pass under its name without its body running.
"""
import ast
import collections
import json
import os
import re

CODON = re.compile(r"^\*\*(G\d+\.\d+|INV\d+) ([^*]+?)\.\*\*(.*)$")
PROOF = re.compile(r"Proof: `(test_\w+)`")
MACHINE = frozenset({"os", "sys", "platform", "shutil", "sysconfig"})  # a skip may depend on these only
NO_REASON = ("", "unconditional skip")                                 # what pytest writes for a skip with none


def test_files(tests_dir):
    return sorted(os.path.join(tests_dir, n) for n in os.listdir(tests_dir)
                  if n.startswith("test_") and n.endswith(".py"))


def _tree(path):
    with open(path, encoding="utf-8") as f:
        return ast.parse(f.read())


def _tests_in(tree):
    return [d for d in tree.body if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and d.name.startswith("test_")]


def top_level_tests(tests_dir):
    """Every top-level test_* function in tests/test_*.py: what a whole-suite run must collect."""
    return {d.name for p in test_files(tests_dir) for d in _tests_in(_tree(p))}


def twice_defined(tests_dir):
    """G19.2: a name defined twice in one file. Only the last definition runs, and it passes under that name."""
    out = []
    for p in test_files(tests_dir):
        count = collections.Counter(d.name for d in _tests_in(_tree(p)))
        out += [f"{os.path.basename(p)}: {n} is defined {c} times, and only the last one runs"
                for n, c in sorted(count.items()) if c > 1]
    return out


def _on_the_machine(cond):
    """True when a skip's condition reads the machine (os, sys, platform, shutil, sysconfig) and is no constant."""
    if isinstance(cond, ast.Constant):
        return False
    return any(isinstance(n, ast.Name) and n.id in MACHINE for n in ast.walk(cond))


def _mark(dec):
    """'skip', 'skipif' or 'xfail' for a pytest mark decorator (called or not), else None; with its call."""
    f = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(f, ast.Attribute) and f.attr in ("skip", "skipif", "xfail") \
            and isinstance(f.value, ast.Attribute) and f.value.attr == "mark":
        return f.attr, (dec if isinstance(dec, ast.Call) else None)
    return None, None


def _reason_of(call):
    for k in call.keywords:
        if k.arg in ("reason", "msg") and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
            return k.value.value
    for a in call.args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            return a.value
    return ""


def _is_skip_call(node):
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr in ("skip", "skipTest", "xfail")) or \
           (isinstance(f, ast.Name) and f.id in ("skip", "xfail"))


def _body_skips(stmts, guarded, out, name):
    """A skip or xfail called in a test's body must sit under an `if` that reads the machine, with a reason."""
    for st in stmts:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue                                  # a nested helper is not the test's own body
        if isinstance(st, ast.If):
            _body_skips(st.body, guarded or _on_the_machine(st.test), out, name)
            _body_skips(st.orelse, guarded, out, name)
            continue
        inner = [getattr(st, f) for f in ("body", "orelse", "finalbody") if isinstance(getattr(st, f, None), list)]
        inner += [h.body for h in getattr(st, "handlers", [])]
        if inner:                                     # with, for, while, try: look inside, keeping the guard
            for block in inner:
                _body_skips(block, guarded, out, name)
            continue
        for node in ast.walk(st):
            if _is_skip_call(node):
                if not guarded:
                    out.append(f"{name}: skips with no condition of the machine")
                elif not _reason_of(node).strip():
                    out.append(f"{name}: skips with no reason")


def disguised_skips(tests_dir):
    """G19.3: every way a test can be kept from running on every machine while its name stays in the file:
    a skip or xfail mark, a skipif whose condition does not read the machine, a module-level pytestmark,
    a skip in the body not under a machine condition, and a `return` among the test's own statements."""
    out = []
    for p in test_files(tests_dir):
        tree, base = _tree(p), os.path.basename(p)
        for st in tree.body:
            if isinstance(st, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in st.targets):
                if any(_mark(n)[0] for n in ast.walk(st.value)):
                    out.append(f"{base}: a pytestmark skips or xfails the whole file")
        for d in _tests_in(tree):
            name = f"{base}: {d.name}"
            for dec in d.decorator_list:
                kind, call = _mark(dec)
                if kind in ("skip", "xfail"):
                    out.append(f"{name}: a {kind} mark keeps it from running")
                elif kind == "skipif":
                    if call is None or not call.args or not _on_the_machine(call.args[0]):
                        out.append(f"{name}: a skipif whose condition does not read the machine")
                    elif not _reason_of(call).strip():
                        out.append(f"{name}: a skipif with no reason")
            if any(isinstance(st, ast.Return) for st in d.body):
                out.append(f"{name}: a return among its own statements ends it early")
            _body_skips(d.body, False, out, name)
    return out


def plan_proofs(plan_text):
    """The Proof of every built codon and every invariant (a codon marked (open) or removed is not built)."""
    out = {}
    for line in plan_text.split("\n"):
        m = CODON.match(line)
        if not m or m.group(2).startswith("Removed in plan") or m.group(2).endswith("(open)"):
            continue
        p = PROOF.search(line)
        if p:
            out[p.group(1)] = m.group(1)
    return out


def required(root):
    """{test name: what needs it}: built codons' and invariants' proofs, and every PROMISES row that names a test."""
    with open(os.path.join(root, "DNA-STRAND-MASTER-PLAN.md"), encoding="utf-8") as f:
        need = {t: f"codon {c}" for t, c in plan_proofs(f.read()).items()}
    with open(os.path.join(root, "PROMISES.json"), encoding="utf-8") as f:
        for r in json.load(f).get("rows", []):
            if r.get("test"):
                need.setdefault(r["test"], f"row {r['id']}")
    return need


def outcome(cases):
    """One name's result from all its cases: failed beats skipped beats passed."""
    for kind in ("failed", "skipped"):
        hit = next((c for c in cases if c[0] == kind), None)
        if hit:
            return hit
    return ("passed",) if cases else None


def in_ci(environ):
    """G19.3: the run is in CI when the environment variable CI is `true` (GitHub Actions sets it)."""
    return str(environ.get("CI", "")).strip().lower() == "true"


def verdict(root, tests_dir, collected, deselected, results, ci=False):
    """(whole, problems, not_run_here) for one run. `collected`: top-level test names the runner collected;
    `results`: {name: [(kind, reason?)...]} from its reports. Problems only from a whole-suite run, except a skip
    with no reason, which fails any run. A built proof skipped on a condition of the machine is listed, and fails
    the run only in CI, where every built proof must pass."""
    got = {n: outcome(c) for n, c in results.items()}
    no_reason = [f"{n}: skipped with no reason" for n, o in sorted(got.items()) if o and o[0] == "skipped"
                 and o[1].strip() in NO_REASON]
    here = [f"not run here: {n} ({o[1]})" for n, o in sorted(got.items()) if o and o[0] == "skipped"
            and o[1].strip() not in NO_REASON]
    defined = top_level_tests(tests_dir)
    whole = deselected == 0 and defined <= set(collected)
    if not whole:
        return False, no_reason, here
    problems = list(no_reason) + twice_defined(tests_dir) + disguised_skips(tests_dir)
    for t, why in sorted(required(root).items()):
        o = got.get(t)
        if o is None:
            problems.append(f"{t} ({why}) did not run: it is not a test the runner collects")
        elif o[0] == "failed":
            problems.append(f"{t} ({why}) failed")
        elif o[0] == "skipped" and o[1].strip() not in NO_REASON and ci:   # with no reason: already named above
            problems.append(f"{t} ({why}) was not run in CI ({o[1]}), and a skip never counts as passed")
    return True, problems, here
