#!/bin/sh
# Pit Stop hook smoke test for a real machine (macOS, Linux, Windows with Git Bash).
# It runs each hook's command line exactly as hooks.json gives it to Claude Code,
# in a throwaway folder, and checks the exit codes. It starts no Claude Code
# session, installs nothing, and deletes its folder at the end.
#   sh tests/smoke_hooks.sh            (from the plugin folder)
# Exit 0 = every check passed. Each line says PASS or FAIL.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/pitstop-smoke-$$")
mkdir -p "$T/cfg" "$T/home"
trap 'rm -rf "$T"' EXIT
fails=0
say() { printf '%s\n' "$*"; }
check() { # name, want, got
  if [ "$2" = "$3" ]; then say "PASS  $1"; else say "FAIL  $1 (wanted $2, got $3)"; fails=$((fails + 1)); fi
}

say "plugin: $(grep '"version"' "$ROOT/.claude-plugin/plugin.json" | tr -d ' ,')  folder: $ROOT"
say "system: $(uname -a 2>/dev/null | cut -c1-80)"
say "python3 --version: $(python3 --version 2>&1 | head -1 | cut -c1-90) (exit $?)"
say "python --version:  $(python --version 2>&1 | head -1 | cut -c1-90)"
# the same probe line the hooks use
P=python3; python3 -I -c pass </dev/null >/dev/null 2>&1 || P=python
say "the probe picks: $P -> $("$P" -I -c 'import sys; print(sys.version.split()[0], sys.executable)' 2>&1 | head -1)"
"$P" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null
check "a Python 3.10+ is reachable through the probe" 0 $?

# transcripts and the hook command lines, written with the Python the probe picked
"$P" -I - "$ROOT" "$T" <<'PY'
import json, os, sys
root, t = sys.argv[1], sys.argv[2]
u = {"input_tokens": 10, "cache_read_input_tokens": 200000, "output_tokens": 5}
def tr(name, kind):
    with open(os.path.join(t, name), "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": kind}], "usage": u}}) + "\n")
tr("hold.jsonl", "tool_use")
tr("done.jsonl", "text")
hooks = json.load(open(os.path.join(root, "hooks", "hooks.json")))["hooks"]
for event, rows in hooks.items():
    with open(os.path.join(t, event + ".cmd"), "w") as f:
        f.write(rows[0]["hooks"][0]["command"])
def payload(name, d):
    with open(os.path.join(t, name), "w") as f:
        json.dump(d, f)
payload("hold.json", {"trigger": "auto", "transcript_path": os.path.join(t, "hold.jsonl"), "session_id": "smoke"})
payload("done.json", {"trigger": "auto", "transcript_path": os.path.join(t, "done.jsonl"), "session_id": "smoke"})
payload("manual.json", {"trigger": "manual", "transcript_path": os.path.join(t, "hold.jsonl"), "session_id": "smoke"})
payload("stop.json", {"session_id": "smoke-ask"})
payload("start.json", {"source": "compact", "cwd": t})
os.makedirs(os.path.join(t, "cfg", "pit-stop", "asks"))
payload(os.path.join("cfg", "pit-stop", "asks", "smoke-ask.json"),
        {"newest": "9.9.9", "ver": "0.0.1", "okf": os.path.join(t, "rearm"), "at": 1, "delivered": None})
# a memory index over the 24 KB load limit, for the reading check
key = "".join(c if c.isalnum() else "-" for c in os.path.abspath(os.path.join(t, "proj")))
mem = os.path.join(t, "cfg", "projects", key, "memory")
os.makedirs(mem)
os.makedirs(os.path.join(t, "proj"))
with open(os.path.join(mem, "MEMORY.md"), "w") as f:
    for i in range(150):
        f.write(f"- [Memory {i}](feedback_item{i}.md) — " + "x" * 160 + "\n")
PY
check "test files written" 0 $?

run() { # event, payload file -> sets code, out
  out=$(CLAUDE_PLUGIN_ROOT="$ROOT" CLAUDE_CONFIG_DIR="$T/cfg" HOME="$T/home" sh -c "$(cat "$T/$1.cmd")" < "$T/$2" 2> "$T/err.txt")
  code=$?
}
if [ -f "$T/PreCompact.cmd" ]; then
  run PreCompact hold.json;   check "PreCompact holds mid-tool-call (exit 2)" 2 $code; check "PreCompact prints nothing on a hold" "" "$out"
  run PreCompact done.json;   check "PreCompact allows at a break (exit 0)" 0 $code
  case "$out" in *"Python was not found"*) check "no Store-stub text leaks into the summary instructions" clean leaked ;; *) check "no Store-stub text leaks into the summary instructions" clean clean ;; esac
  say "      PreCompact stdout at a break: $(printf '%s' "$out" | cut -c1-60)..."
  run PreCompact manual.json; check "PreCompact never blocks a manual /compact" 0 $code
fi
if [ -f "$T/Stop.cmd" ]; then
  run Stop stop.json;  check "Stop hook hands over a waiting ask (exit 2)" 2 $code
  grep -q "background jobs" "$T/err.txt"; check "the ask text is on stderr" 0 $?
  run Stop stop.json;  check "Stop hook does not ask twice" 0 $code
fi
if [ -f "$T/SessionStart.cmd" ]; then
  run SessionStart start.json; check "SessionStart exits 0 after a compaction" 0 $code
  case "$out" in "Pit Stop:"*) check "SessionStart prints the checklist" yes yes ;; *) check "SessionStart prints the checklist" yes no ;; esac
fi
rc=$(CLAUDE_CONFIG_DIR="$T/cfg" HOME="$T/home" "$P" -I "$ROOT/scripts/reading_check.py" --project "$T/proj" 2>&1)
case "$rc" in *"CUT OFF"*) check "reading check flags a MEMORY.md over 24 KB" yes yes ;; *) check "reading check flags a MEMORY.md over 24 KB" yes no ;; esac
say "      $(printf '%s' "$rc" | grep -i 'Memory index' | cut -c1-150)"

if [ "$fails" -eq 0 ]; then say "ALL PASS"; else say "$fails FAILED"; fi
exit "$fails"
