#!/usr/bin/env python3
"""Pit Stop, part 3: the clean reload.

After every compaction a session re-reads its instruction files (CLAUDE.md)
and its memory index (MEMORY.md). Claude Code loads only the first 200 lines
of MEMORY.md, so an index that grows past that silently drops its NEWEST
entries. This tool checks what a folder's sessions re-read and can trim the
memory index without deleting any memory file.

  reading_check.py [--project DIR]            report (read-only)
  reading_check.py [--project DIR] --trim     show the trimmed index (dry run)
  reading_check.py [--project DIR] --trim --write   write it (backup first)

Trim rules: one line per memory; duplicate links dropped; memories whose file
names share a topic (feedback_<topic>_..., project_<topic>_...) and have 3+
members are grouped on shared lines; free-text lines are kept exactly; every
link is kept; the result must stay under 180 lines or nothing is written.
"""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pitstop_common as pc  # noqa: E402

LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+\.md)\)")


def instruction_files(project):
    """CLAUDE.md files a session started in PROJECT loads (user, ancestors, project)."""
    found = []
    user = os.path.join(pc.CLAUDE_DIR, "CLAUDE.md")
    if os.path.exists(user):
        found.append(user)
    p = os.path.abspath(project)
    chain = []
    while True:
        chain.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    for d in reversed(chain):
        for name in ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"), "CLAUDE.local.md"):
            f = os.path.join(d, name)
            if os.path.exists(f) and f != user:
                found.append(f)
    return found


def parse_index(text):
    items, plain, seen, dupes = [], [], set(), 0
    for line in text.splitlines():
        if not line.strip() or line.startswith("# "):
            continue
        m = LINK.search(line)
        if line.startswith("- [") and m and line.index(m.group(0)) == 2:
            title, f = m.group(1), m.group(2)
            hook = line[m.end():].strip().lstrip("—-:").strip()
            if f in seen:
                dupes += 1
                continue
            seen.add(f)
            items.append((title, f, hook))
        else:
            plain.append(line)
    return items, plain, dupes


def short(t, n):
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"


def topic(fname):
    parts = os.path.splitext(fname)[0].split("_")
    return parts[1] if len(parts) > 2 else None


def trim(text, keep_single=()):
    items, plain, _ = parse_index(text)
    groups = collections.defaultdict(list)
    for t, f, h in items:
        groups[topic(f)].append((t, f, h))
    out = ["# Memory index: one line per memory or per topic group; detail lives in each file. Keep under 180 lines.", ""]
    out += plain
    if plain:
        out.append("")
    singles, grouped = [], []
    for key, lst in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        if key and len(lst) >= 3:
            rest = [x for x in lst if x[1] in keep_single]
            singles += rest
            lst = [x for x in lst if x[1] not in keep_single]
            for i in range(0, len(lst), 6):
                part = " · ".join(f"[{short(t, 48)}]({f})" for t, f, _ in lst[i:i + 6])
                grouped.append(f"- **{key}{' (cont.)' if i else ''}:** {part}")
        else:
            singles += lst
    for t, f, h in singles:
        out.append(f"- [{short(t, 48)}]({f}) — {short(h, 110)}" if h else f"- [{short(t, 48)}]({f})")
    out += [""] + grouped
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--trim", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--keep-single", action="append", default=[], help="memory file to keep on its own line")
    a = ap.parse_args()

    print(f"Folder: {os.path.abspath(a.project)}")
    print("Instruction files this folder's sessions load:")
    for f in instruction_files(a.project):
        n = sum(1 for _ in open(f, errors="replace"))
        flag = "  <- large" if os.path.getsize(f) > 40000 else ""
        print(f"  {os.path.getsize(f)/1024:6.1f} KB {n:5d} lines  {f}{flag}")
    mi = pc.memory_index(a.project)
    if not os.path.exists(mi):
        print("Memory index: none (this folder has no saved memories)")
        return 0
    text = open(mi).read()
    items, plain, dupes = parse_index(text)
    lines = text.count("\n")
    missing = sorted(f for f in {m[1] for m in LINK.findall(text)} if not os.path.exists(os.path.join(os.path.dirname(mi), f)))
    long_lines = sum(1 for l in text.splitlines() if len(l) > 200)
    state = ("CUT OFF: entries past line 200 never load" if lines > pc.MEMORY_LOAD_LINES
             else "near the limit" if lines > pc.MEMORY_WARN_LINES else "ok")
    all_links = {m[1] for m in LINK.findall(text)}
    print(f"Memory index: {lines} lines, {len(text)/1024:.1f} KB, {len(all_links)} memories linked, "
          f"{dupes} duplicate links, {len(missing)} links to missing files, {long_lines} lines over 200 chars -> {state}")
    for f in missing[:10]:
        print(f"  missing: {f}")
    if not a.trim:
        return 0
    new = trim(text, set(a.keep_single))
    before, after = {f for _, f, _ in items}, {m[1] for m in LINK.findall(new)}
    lost = before - after
    n_new = new.count("\n")
    print(f"Trimmed index: {n_new} lines, {len(new)/1024:.1f} KB, links kept {len(after & before)}/{len(before)}")
    if lost:
        print(f"REFUSING: {len(lost)} links would be lost: {sorted(lost)[:5]}")
        return 1
    if n_new > pc.MEMORY_WARN_LINES:
        print("REFUSING: still over 180 lines after trimming; move memories that belong to other projects to those projects first.")
        return 1
    if not a.write:
        print("(dry run; add --write to save it, a backup is made first)\n")
        print(new)
        return 0
    b = pc.backup(mi)
    tmp = mi + ".tmp-pitstop"
    with open(tmp, "w") as f:
        f.write(new)
    os.replace(tmp, mi)
    print(f"Written. Backup: {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
