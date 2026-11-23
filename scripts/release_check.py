#!/usr/bin/env python3
"""G7.10: new code gets a new version. Run by CI on the release pull request (head branch release-*) and by hand
before a tag is made. Exits 1 and names the files when hooks/, scripts/ or skills/ differ from the newest tag
v<x.y.z> while .claude-plugin/plugin.json still names <x.y.z>; exits 0 otherwise. Reads git and files only.

  python3 -I scripts/release_check.py [REPO]
"""
import json
import os
import re
import subprocess
import sys

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


def main(argv):
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
