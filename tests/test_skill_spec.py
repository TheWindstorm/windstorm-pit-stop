"""Every SKILL.md must load in strict YAML parsers and follow the Agent Skills spec (agentskills.io),
not only in Claude Code's lenient reader. Standard library only; CI also runs a real YAML parser
and `gh skill publish --dry-run`."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(path):
    text = open(path, encoding="utf-8").read()
    assert text.startswith("---\n"), f"{path}: must start with a --- line"
    end = text.index("\n---", 4)
    return text[4:end].split("\n")


def plain_scalar_problems(value):
    """Why an UNQUOTED single-line YAML value would not load in a strict parser (empty if fine)."""
    v = value.strip()
    if not v or v[0] in "'\"|>":
        return []
    out = []
    if ": " in v or v.endswith(":"):
        out.append("contains ': ' (a strict parser reads a second key); quote it or drop the colon")
    if " #" in v:
        out.append("contains ' #' (a strict parser starts a comment there)")
    if v[0] in "!&*{}[],%@`?-":
        out.append(f"starts with {v[0]!r}, a YAML indicator")
    return out


def skills():
    found = glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))
    assert found, "no skills/*/SKILL.md found"
    return found


def test_frontmatter_loads_strictly_and_follows_the_spec():
    for path in skills():
        fields = {}
        for line in frontmatter(path):
            if not line.strip() or line.startswith((" ", "\t", "#")):
                continue
            key, sep, value = line.partition(":")
            assert sep, f"{path}: line without a key: {line[:60]!r}"
            fields[key.strip()] = value
            problems = plain_scalar_problems(value)
            assert not problems, f"{path}: {key}: " + "; ".join(problems)
        name = fields.get("name", "").strip()
        desc = fields.get("description", "").strip().strip("'\"")
        assert NAME_RE.match(name) and len(name) <= 64, f"{path}: name {name!r} breaks the naming rules"
        assert name == os.path.basename(os.path.dirname(path)), f"{path}: name must match its folder"
        assert 0 < len(desc) <= 1024, f"{path}: description must be 1 to 1024 characters (is {len(desc)})"
        if "allowed-tools" in fields:
            assert not fields["allowed-tools"].strip().startswith("["), f"{path}: allowed-tools must be a string"


def test_the_checker_catches_what_strict_parsers_reject():
    assert plain_scalar_problems(" Use it for the user: run the checkup")       # the 0.5.2 bug
    assert plain_scalar_problems(" a note # not a comment in Claude Code")
    assert plain_scalar_problems(" - starts like a list")
    assert not plain_scalar_problems(" Use it for the user, to run the checkup (300K/500K)")
    assert not plain_scalar_problems(' "quoted: is fine"')
