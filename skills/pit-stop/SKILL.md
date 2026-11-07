---
name: pit-stop
description: Pit Stop, a context optimizer for Claude Code. Use when the user says "pit stop", "/pit-stop report", "/pit-stop optimize", "/pit-stop reading" or "/pit-stop restart", or asks to tune when a session auto-compacts, pick a compaction point (300K/500K/700K), check why sessions are not on the newest Claude Code after compacting, check or trim MEMORY.md / CLAUDE.md so they load fully, or suggest a model and effort for a session's job.
---

# Pit Stop

A race car stops at a pit stop, never mid-corner. A Claude Code session does the same: at a break between tasks it refuels (compacts), changes tyres (moves to the newest Claude Code), and gets a clean windshield (re-reads only short, relevant instructions).

The scripts live in this plugin's `scripts/` folder, two levels above this skill's base directory (`<base>/../../scripts/`). They use only the Python standard library, read local files only, and print counts, never message text. Run them with `python3 -I`.

## Commands

### `/pit-stop report` (read-only)
Run `scripts/report.py` (no arguments, or `--project DIR`). Show the table. Point out sessions waiting for a restart (`*`) and memory indexes past the 200-line load limit (`!`).

### `/pit-stop optimize [DIR]`
1. Run `scripts/optimize.py --project DIR` (default: the current folder). Add `--context-limit 1000000` or `200000` if you know the session's model context size; otherwise it is inferred from history.
2. Explain the recommendation in plain words: the compaction point, the hard ceiling, and each reason.
3. Model and effort lines are SUGGESTIONS. Never change the model or effort yourself; ask the user.
4. Only after the user agrees, run it again with `--apply`. That writes `env.CLAUDE_CODE_AUTO_COMPACT_WINDOW` into `DIR/.claude/settings.local.json` (backup first, everything else in the file kept) and the profile `DIR/.claude/pit-stop.json` (the hook reads its `ceiling`). The new point takes effect at the session's next start; say so.
5. Suggest re-running weekly. The profile records the date and the reasons.

### `/pit-stop reading [DIR]` (the clean reload)
1. Run `scripts/reading_check.py --project DIR`. It lists the instruction files the folder's sessions load, and the memory index size: Claude Code loads only the first 200 lines of `MEMORY.md`, so entries past that never load.
2. If the index is near or past the limit, first look for lines that belong to OTHER projects or folders (another product, another team's lane). Propose moving those memories to the folder that owns them, rather than squeezing them in. Every line a session re-reads should be about that session's own work.
3. Then run `--trim` (dry run) and show the result. With the user's OK, run `--trim --write`. Nothing is deleted: duplicate links are dropped, related memories are grouped on shared lines, free-text lines stay exactly as they were, and a backup is written first. It refuses if any link would be lost or if the result is still over 180 lines.
4. Instruction files over ~40 KB: suggest moving detail into separate files that are read on demand.

### `/pit-stop restart` (fleet mode, optional)
For people who run several sessions in tmux. Explain what `scripts/restart_at_break.py` does (read its docstring). Run it WITHOUT `--live` first and show the dry-run decisions. Only with the user's OK, help them schedule `--live` every 10 minutes (cron, launchd or a systemd user timer) and write `~/.claude/pit-stop/restart.json` if they want allow or deny lists. It never restarts a session with text in its input box, a dialog on screen, work running, background jobs it has not agreed to stop, or a large context.

## The compact-at-a-break hook (always on when the plugin is enabled)
`hooks/precompact_wait_for_break.py` holds AUTOMATIC compaction while a tool call is in flight and lets it run at the next break. It never blocks a manual `/compact`, allows anything at or above the ceiling, and allows on any error. Its log has counts only: `~/.claude/pit-stop/precompact.log`.

## Rules
- Read before you change anything; show the dry run first.
- Back up every file before writing it; change only the keys Pit Stop owns.
- Never print message text from transcripts, secrets, or settings values other than the compaction point.
- Model, effort and anything that costs money are the user's decision.
