"""G7.6, G7.7, G7.10: the catalog installs released, tagged commits over https, one version is stated in every place,
and new code gets a new version. Tags and commits are read from GitHub with `git ls-remote`. Outside CI, a machine
with no road to github.com lists these as not run here (a condition of the machine, G19.3); in CI they must run."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _gate  # noqa: E402
import release_check  # noqa: E402

OWN = "windstorm-pit-stop"
OWN_URL = "https://github.com/TheWindstorm/windstorm-pit-stop.git"
SHA = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def catalog():
    return json.loads(_read(".claude-plugin", "marketplace.json"))["plugins"]


def version():
    return json.loads(_read(".claude-plugin", "plugin.json"))["version"]


def github_reachable():
    try:
        socket.create_connection(("github.com", 443), timeout=8).close()
        return True
    except OSError:
        return False


def need_github():
    """Outside CI, no road to GitHub is a condition of the machine: listed as not run here. In CI it must run."""
    if not shutil.which("git") or (not github_reachable() and not _gate.in_ci(os.environ)):
        pytest.skip("no git, or no road to github.com from this machine")


def tag_commit(url, tag):
    """The commit a tag names on its remote (an annotated tag's ^{} line wins), or None when there is no such tag."""
    out = subprocess.run(["git", "ls-remote", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], capture_output=True,
                         text=True, timeout=60, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    assert out.returncode == 0, f"git ls-remote {url}: {out.stderr.strip()}"
    refs = dict(reversed(ln.split("\t")) for ln in out.stdout.splitlines() if "\t" in ln)
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


def entry_form_problems(entries):
    """G7.6: every entry but Pit Stop's own (G7.7) is an https url with a tag ref and a 40-hex sha."""
    out = []
    for e in entries:
        if e.get("name") == OWN:
            continue
        s = e.get("source")
        if not isinstance(s, dict) or s.get("source") != "url":
            out.append(f"{e.get('name')}: source is not a url entry ({s!r})")
            continue
        if not str(s.get("url", "")).startswith("https://") or "git@" in str(s.get("url", "")):
            out.append(f"{e.get('name')}: url is not https ({s.get('url')})")
        if not TAG.match(str(s.get("ref", ""))):
            out.append(f"{e.get('name')}: ref is not a tag v<x.y.z> ({s.get('ref')})")
        if not SHA.match(str(s.get("sha", ""))):
            out.append(f"{e.get('name')}: sha is not 40 hex ({s.get('sha')})")
    return out


def own_entry_problems(entries, version, tag_sha):
    """G7.7: once the tag v<version> exists (tag_sha), Pit Stop's own entry pins it; before, the old state passes."""
    own = next((e for e in entries if e.get("name") == OWN), None)
    if own is None:
        return ["Pit Stop's own entry is missing"]
    if tag_sha is None:
        return []
    want = {"source": "url", "url": OWN_URL, "ref": f"v{version}", "sha": tag_sha}
    return [] if own.get("source") == want else [f"the tag v{version} exists ({tag_sha[:8]}) and waits for its pin: "
                                                 f"Pit Stop's own entry is {own.get('source')!r}"]


def changelog_release(text):
    m = re.search(r"^## (\d+\.\d+\.\d+) ", text, re.M)
    return m.group(1) if m else None


def test_G7_6_every_catalog_entry_installs_over_https_with_no_key():
    assert entry_form_problems(catalog()) == []
    readme = _read("README.md")
    assert re.search(r"no GitHub key[^.\n]*Claude Code 2\.1\.280", readme), \
        "the README names the oldest Claude Code version its install lines were seen to work on with no GitHub key"
    pb = {"name": "x", "source": {"source": "url", "url": "https://github.com/a/b.git", "ref": "v1.2.3", "sha": "a" * 40}}
    assert entry_form_problems([pb]) == []
    bad = [dict(pb, source={"source": "github", "repo": "a/b", "ref": "v1.2.3", "sha": "a" * 40}),
           dict(pb, source=dict(pb["source"], url="git@github.com:a/b.git")),
           dict(pb, source=dict(pb["source"], ref="main")),
           dict(pb, source=dict(pb["source"], sha="abc"))]
    assert [len(entry_form_problems([b])) for b in bad] == [1, 1, 1, 1]
    assert entry_form_problems([{"name": OWN, "source": "./"}]) == []          # G7.7 governs Pit Stop's own


def test_G7_7_pit_stops_catalog_entry_pins_a_release_tag():
    need_github()
    v = version()
    assert own_entry_problems(catalog(), v, tag_commit(OWN_URL, f"v{v}")) == []
    pinned = {"name": OWN, "source": {"source": "url", "url": OWN_URL, "ref": "v9.9.9", "sha": "b" * 40}}
    assert own_entry_problems([{"name": OWN, "source": "./"}], "9.9.9", None) == []        # before the tag
    assert len(own_entry_problems([{"name": OWN, "source": "./"}], "9.9.9", "b" * 40)) == 1  # a tag waits for its pin
    assert own_entry_problems([pinned], "9.9.9", "b" * 40) == []                            # pinned
    assert len(own_entry_problems([pinned], "9.9.9", "c" * 40)) == 1                         # pinned to another commit


def _repo(tmp_path, ver):
    r = tmp_path / "repo"
    (r / ".claude-plugin").mkdir(parents=True)
    (r / "scripts").mkdir()
    (r / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": ver}))
    (r / "scripts" / "a.py").write_text("x = 1\n")
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@localhost"]
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "one"], check=True)
    subprocess.run(g + ["tag", f"v{ver}"], check=True)
    return r, g


def test_G7_10_one_version_in_every_place_and_new_code_gets_a_new_one(tmp_path):
    need_github()
    v, entries = version(), catalog()
    assert changelog_release(_read("CHANGELOG.md")) == v, "the CHANGELOG's top release heading is plugin.json's version"
    for e in entries:
        s = e.get("source")
        if isinstance(s, dict) and s.get("source") == "url":
            assert tag_commit(s["url"], s["ref"]) == s["sha"], f"{e['name']}: {s['ref']} is not commit {s['sha'][:8]}"
            if e["name"] == OWN and tag_commit(OWN_URL, f"v{v}") is not None:
                assert s["ref"] == f"v{v}", "once its tag exists, Pit Stop's pinned entry names plugin.json's version"
    assert changelog_release("## Plan 1.1 (x)\n## Unreleased\n## 0.6.1 (2026-11-20)\n") == "0.6.1"
    # release_check.py on a planted repository: new code under the tag's version is refused, a new version passes
    r, g = _repo(tmp_path, "1.0.0")
    assert release_check.check(str(r))[0] is True
    (r / "scripts" / "a.py").write_text("x = 2\n")
    subprocess.run(g + ["commit", "-qam", "two"], check=True)
    ok, msg = release_check.check(str(r))
    assert ok is False and "scripts/a.py" in msg and "still names 1.0.0" in msg
    (r / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1.0.1"}))
    subprocess.run(g + ["commit", "-qam", "three"], check=True)
    assert release_check.check(str(r))[0] is True
    out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "scripts", "release_check.py"), str(r)],
                         capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.startswith("release check: OK"), out.stdout
