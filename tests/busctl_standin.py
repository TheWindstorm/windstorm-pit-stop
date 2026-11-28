"""A stand-in for `busctl --user --json=short get-property org.freedesktop.systemd1 <unit> <interface> <property>`
in tests, answering in systemd 255's own form (fixtures/systemd-255-bus-answers.json, recorded 2026-11-26).

ANSWERS chooses (or a dict of property -> choice): "file" (default) reads the unit file setup wrote under the home's unit folder and its drop-ins, as
systemd reads them, and answers in the recorded frame; a key of the fixture's "answers" gives that recorded answer
word for word, its home swapped for this home; "no bus" gives the recorded no-bus words and exit code."""
import json
import os
import re
import subprocess
import sys

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "systemd-255-bus-answers.json")
REC = json.load(open(FIXTURE, encoding="utf-8"))


def words(line):
    out, i = [], 0
    while i < len(line):
        if line[i] in " \t":
            i += 1
            continue
        w = []
        while i < len(line) and line[i] not in " \t":
            if line[i] in "\"'":
                q, i = line[i], i + 1
                while line[i] != q:
                    if line[i] == "\\":
                        i += 1
                    w.append(line[i])
                    i += 1
                i += 1
            elif line[i] == "\\":
                w.append(line[i + 1])
                i += 2
            else:
                w.append(line[i])
                i += 1
        out.append("".join(w).replace("%%", "%"))
    return out


def unit_of(obj):
    return re.sub(r"_([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), obj.rsplit("/", 1)[1])


def from_file(unit, prop, home, unit_dir=None):
    ud = unit_dir or os.path.join(home, ".config", "systemd", "user")
    frag = os.path.join(ud, unit)
    if not os.path.isfile(frag):
        return REC["answers"]["not-there-at-all.service, not there"][prop]
    drops = sorted(os.path.join(ud, unit + ".d", f) for f in os.listdir(os.path.join(ud, unit + ".d"))
                   if f.endswith(".conf")) if os.path.isdir(os.path.join(ud, unit + ".d")) else []
    exec_, env = [], []
    for f in [frag] + drops:
        for line in open(f, errors="surrogateescape").read().splitlines():
            if line.startswith("ExecStart="):
                w = words(line[len("ExecStart="):])
                exec_ = [] if not w else exec_ + [[w[0], w, False, 0, 0, 0, 0, 0, 0, 0]]
            elif line.startswith("Environment="):
                for a in words(line[len("Environment="):]):
                    n = a.split("=", 1)[0]
                    at = [i for i, e in enumerate(env) if e.split("=", 1)[0] == n]
                    if at:
                        env[at[0]] = a
                    else:
                        env.append(a)
    data = {"LoadState": "loaded", "FragmentPath": frag, "ExecStart": exec_, "Environment": env, "DropInPaths": drops}
    kind = REC["answers"]["pit-stop-restart.service, as 0.6.3's setup writes it"][prop]["type"]
    return {"type": kind, "data": data[prop]}


def recorded(key, prop, home):
    """The recorded answer for KEY's PROP, the made account's home (written <home> in the fixture) swapped for HOME;
    a word systemd split at the home's space is swapped part for part."""
    text = json.dumps(REC["answers"][key][prop], ensure_ascii=False)
    first, _, second = home.rpartition(" ") if " " in home else (home, "", "")
    for token, value in (("<home>", home), ("<home's first word>", first), ("<home's second word>", second)):
        text = text.replace(token, json.dumps(value, ensure_ascii=False)[1:-1])
    return json.loads(text)


def answer(cmd, home=None, answers="file", unit_dir=None):
    """A subprocess.CompletedProcess as busctl's own answer to CMD."""
    home = home or os.environ["HOME"]
    if answers == "no bus":
        return subprocess.CompletedProcess(cmd, REC["no bus"]["exit with --json=short"], "", REC["no bus"]["words"] + "\n")
    unit, prop = unit_of(cmd[-3]), cmd[-1]
    if isinstance(answers, dict):           # one recorded answer per property, the rest from the file
        answers = answers.get(prop, "file")
    if answers == "file":
        got = from_file(unit, prop, home, unit_dir)
    else:
        got = recorded(answers, prop, str(home))
    return subprocess.CompletedProcess(cmd, 0, json.dumps(got, separators=(",", ":"), ensure_ascii=False) + "\n", "")


def plant(stub_dir, answers="file"):
    """Put a busctl stand-in in STUB_DIR (for a setup run as a program, with STUB_DIR its PATH)."""
    p = os.path.join(stub_dir, "busctl")
    with open(p, "w") as f:
        f.write(f"#!{sys.executable} -IB\nimport sys\nsys.dont_write_bytecode = True\nsys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})\n"
                f"import busctl_standin as b\nr = b.answer(sys.argv[1:], answers={answers!r})\n"
                "sys.stdout.write(r.stdout)\nsys.stderr.write(r.stderr)\nsys.exit(r.returncode)\n")
    os.chmod(p, 0o755)


def bus_label(name):
    return "".join(c if c.isascii() and c.isalnum() and not (i == 0 and c.isdigit()) else f"_{ord(c):02x}"
                   for i, c in enumerate(name))
