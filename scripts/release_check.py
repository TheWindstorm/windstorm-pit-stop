#!/usr/bin/env python3
"""G7.10: new code gets a new version. Run by CI on the release pull request (head branch release-*) and by hand
before a tag is made. Exits 1 and names the files when hooks/, scripts/ or skills/ differ from the newest tag
v<x.y.z> while .claude-plugin/plugin.json still names <x.y.z>; exits 0 otherwise. Reads git and files only.

G7.14, the release step (--tag): the tag v<x.y.z> for the commit at REPO's head is made LOCALLY in a fresh clone of
that commit from the remote, the whole suite runs there, and only on its exit code 0 with the G19 gate's own
"PASSED" line read is the real tag made in REPO and pushed to the remote. On anything else no tag is made or pushed,
and the line says which test or line stopped it.

  python3 -I scripts/release_check.py [REPO]
  python3 -I scripts/release_check.py --tag [--remote NAME] [REPO]
"""
import json
import os
import re
import subprocess
import sys
sys.dont_write_bytecode = True   # rule 23: no __pycache__ beside the plugin's code (G12.9)
os.umask(os.umask(0) | 0o022)   # rule 28: nothing we make has group or other write (G12.12)

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
WATCHED = ("hooks/", "scripts/", "skills/")


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def newest_tag(repo):
    tags = [t for t in git(repo, "tag", "--list", "v*").split() if TAG.match(t)]
    return max(tags, key=lambda t: tuple(int(x) for x in TAG.match(t).groups())) if tags else None


def check(repo):
    """(ok, message)."""
    with open(os.path.join(repo, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    tag = newest_tag(repo)
    if not tag:
        return True, f"no v* tag yet; plugin.json names {version}"
    changed = [p for p in git(repo, "diff", "--name-only", tag, "HEAD", "--", *WATCHED).split() if p]
    if tag == f"v{version}" and changed:
        return False, (f"{len(changed)} file(s) under hooks/, scripts/ or skills/ differ from {tag}, and plugin.json "
                       f"still names {version}: give the release a new version. " + ", ".join(changed[:20]))
    return True, f"newest tag {tag}; plugin.json {version}; {len(changed)} code file(s) changed since the tag"


# The checks that read the tag from GitHub (`git ls-remote`): a tag made only in the clone is not seen by them, so
# the step's run is their state before the tag; they act on the tag once it is pushed (named in the release list).
ONLY_ON_GITHUB = ("test_G7_7_pit_stops_catalog_entry_pins_a_release_tag",
                  "test_G7_10_one_version_in_every_place_and_new_code_gets_a_new_one")
SUITE = ("-B", "-m", "pytest", "-q", "-p", "no:cacheprovider")


def first_line(text):
    return ((text or "").strip().splitlines() or [""])[0][:200]


def tag_step(repo, remote="origin", runner=subprocess.run):
    """G7.14: (exit code, lines). Makes and pushes no tag unless the whole suite, run in a fresh clone of the commit
    with the tag made there, exits 0 and its G19 gate's own PASSED line is read."""
    import shutil
    import tempfile
    ok, msg = check(repo)
    if not ok:
        return 1, [f"release step: no tag: the release check refused: {msg}"]
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        return 1, ["release step: no tag: the tree has changes that are not in a commit"]
    with open(os.path.join(repo, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
        tag = "v" + json.load(f)["version"]
    sha = git(repo, "rev-parse", "HEAD").strip()
    url = git(repo, "remote", "get-url", remote).strip()
    if git(repo, "tag", "--list", tag).strip():
        return 1, [f"release step: no tag: {tag} already exists in this repository"]
    there = runner(["git", "ls-remote", url, f"refs/tags/{tag}"], capture_output=True, text=True)
    if there.returncode:
        return 1, [f"release step: no tag: {remote} could not be read (git ls-remote: {first_line(there.stderr)})"]
    if there.stdout.strip():
        return 1, [f"release step: no tag: {tag} is already on {remote}"]
    tmp = tempfile.mkdtemp(prefix="pit-stop-tag-")
    try:
        clone = os.path.join(tmp, "clone")
        r = runner(["git", "clone", "--quiet", "--no-checkout", url, clone], capture_output=True, text=True)
        if r.returncode:
            return 1, [f"release step: no tag: the fresh clone of {remote} failed: {first_line(r.stderr)}"]
        r = runner(["git", "-C", clone, "checkout", "--quiet", "--detach", sha], capture_output=True, text=True)
        if r.returncode:
            return 1, [f"release step: no tag: commit {sha[:12]} is not on {remote}; push it first"]
        runner(["git", "-C", clone, "tag", tag, sha], capture_output=True, text=True, check=True)
        s = runner([sys.executable, *SUITE], cwd=clone, capture_output=True, text=True,
                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = (s.stdout or "").splitlines()
    gate = [l for l in out if l.startswith("G19 gate PASSED: whole run;")]
    failed = [l.split()[1] for l in out if l.startswith("FAILED ") and len(l.split()) > 1]
    if s.returncode != 0 or not gate:
        why = (f"exit code {s.returncode}" + (f"; failed: {', '.join(failed[:10])}" if failed else "")
               + ("" if gate else "; the G19 gate's PASSED line was not read"))
        return 1, [f"release step: no tag: the whole suite, with {tag} at its head in a fresh clone of {remote}: {why}"]
    lines = [f"release step: the whole suite, with {tag} at its head in a fresh clone of {remote}: exit 0; {gate[-1]}",
             f"release step: not seen by that run: {', '.join(ONLY_ON_GITHUB)} read the tag from GitHub, so they act "
             f"on {tag} once it is pushed"]
    git(repo, "tag", tag, sha)
    p = runner(["git", "-C", repo, "push", "--quiet", remote, f"refs/tags/{tag}"], capture_output=True, text=True)
    if p.returncode:
        return 1, lines + [f"release step: {tag} made here on {sha[:12]}; its push to {remote} failed: "
                           f"{first_line(p.stderr)}"]
    return 0, lines + [f"release step: {tag} made on {sha[:12]} and pushed to {remote}"]


def main(argv):
    args = argv[1:]
    if args[:1] == ["--tag"]:
        remote = "origin"
        if args[1:2] == ["--remote"] and len(args) > 2:
            remote, args = args[2], args[3:]
        else:
            args = args[1:]
        repo = args[0] if args else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        try:
            rc, lines = tag_step(repo, remote)
        except (RuntimeError, OSError, KeyError, ValueError, subprocess.CalledProcessError) as e:
            print(f"release step: no tag: could not read the repository: {e}")
            return 2
        for line in lines:
            print(line)
        return rc
    repo = argv[1] if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        ok, msg = check(repo)
    except (RuntimeError, OSError, KeyError, ValueError) as e:
        print(f"release check: could not read the repository: {e}")
        return 2
    print(("release check: OK, " if ok else "release check: REFUSED, ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
