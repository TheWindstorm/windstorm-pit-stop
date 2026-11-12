---
name: pit-stop
description: Windstorm Pit Stop, a context optimizer for Claude Code. Use when the user says "pit stop", "/pit-stop setup", "/pit-stop status", "/pit-stop report", "/pit-stop optimize", "/pit-stop reading" or "/pit-stop restart", or asks to tune when a session auto-compacts, set an idle point or a compaction window (300K/500K/700K), stop sessions compacting mid-task, check why sessions are not on the newest Claude Code, keep many sessions updated without watching them, check or trim MEMORY.md / CLAUDE.md so they load fully, or suggest a model and effort for a session's job.
---

# Windstorm Pit Stop

A race car stops at a pit stop, never mid-corner. A Claude Code session does the same: at a break between tasks it refuels (compacts), changes tires (moves to the newest Claude Code), and gets a clean windshield (re-reads only short, relevant instructions).

The scripts live in this plugin's `scripts/` folder, two levels above this skill's base directory (`<base>/../../scripts/`). They use only the Python standard library, read local files only, and print counts, never message text. Run them with `python3 -I`.

## Two points

Claude Code compacts in two ways, and each folder gets a point for each:
- the **idle point** (`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`): a session that has gone quiet compacts by itself, shortly before its prompt cache expires, if its context is at least this big. Claude Code's default is 200K; it never goes under 100K.
- the **window** (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`): the hard point for a session that never goes quiet. The hook holds that compaction until the next break, up to a ceiling.

## Commands

### `/pit-stop setup` (start here)
1. Run `scripts/setup.py` with no arguments. It prints a plan and changes nothing. Show the plan.
2. If the user runs several sessions in tmux, run `scripts/setup.py --fleet` too and show that part. Then run `scripts/restart_at_break.py` (no `--live`) and show what the pit stop would do right now.
3. Only after the user agrees, run it again with `--apply` (and `--fleet` if they want it). That switches daily tuning on, fits the folder of every running session, and for fleet mode copies the launcher and installs a timer. It never changes an existing `restart.json` and never adds a second timer.
4. Say plainly: new points take effect at each session's next start; fleet mode types `/restart`, `/compact` and at most one note into idle tmux panes.

### `/pit-stop status` (read-only)
Run `python3 -I <scripts>/needs_you.py` and show its output as it is. It is the short list of what needs a person: sessions waiting on a question or permission prompt for 2 hours or more, sessions that have been behind the newest Claude Code for a day or more and why, and sessions that never loaded the plugin's hooks. If it says "Nothing needs you.", say so in one line.

### `/pit-stop report` (read-only)
Run `scripts/report.py` (no arguments, or `--project DIR`). Show the table. Point out sessions waiting for a restart (`*`) and memory indexes past the 200-line load limit (`!`).

### `/pit-stop optimize [DIR]`
1. Run `scripts/optimize.py --project DIR` (default: the current folder). Add `--context-limit 1000000` or `200000` if you know the session's model context size; otherwise it is inferred from history. Add `--lanes` when several sessions share the folder.
2. Explain the recommendation in plain words: the idle point, the window, the hard ceiling, and each reason. Mention when the folder's compactions happened (idle, as a prompt arrived, mid-task) and what cold wake-ups cost it.
3. Model and effort lines are SUGGESTIONS. Never change the model or effort yourself; ask the user.
4. Only after the user agrees, run it again with `--apply`. That writes the two env keys into `DIR/.claude/settings.local.json` (backup first, everything else in the file kept) and the profile in `~/.claude/pit-stop/folders/` (the hook reads its `ceiling`). The new points take effect at the session's next start; say so. To move the window of a running session at once, the user can type `/autocompact <tokens>` in it.
5. Daily tuning (`/pit-stop setup`) repeats this by itself, one measured step at a time, and undoes a lowering that cost context. Without it, suggest re-running now and then.

### `/pit-stop reading [DIR]` (the clean reload)
1. Run `scripts/reading_check.py --project DIR`. It lists the instruction files the folder's sessions load, and the memory index size: Claude Code loads only the first 200 lines or about 24 KB (whichever comes first) of `MEMORY.md`, so entries past that never load.
2. If the index is near or past the limit, first look for lines that belong to OTHER projects or folders (another product, another team's lane). Propose moving those memories to the folder that owns them, rather than squeezing them in. Every line a session re-reads should be about that session's own work.
3. Then run `--trim` (dry run) and show the result. With the user's OK, run `--trim --write`. Nothing is deleted: duplicate links are dropped, related memories are grouped on shared lines, free-text lines stay exactly as they were, and a backup is written first. It refuses if any link would be lost or if the result is still over 180 lines.
4. Instruction files over ~40 KB: suggest moving detail into separate files that are read on demand.

### `/pit-stop restart` (fleet mode, optional)
For people who run several sessions in tmux. Explain what `scripts/restart_at_break.py` does (read its docstring). Run it WITHOUT `--live` first and show the dry-run decisions; `--only NAME` looks at one session. Only with the user's OK, set it up with `scripts/setup.py --fleet --apply`, and write allow or deny lists into `~/.claude/pit-stop/restart.json` if they want them (`allow_names`, `deny_names`, `deny_name_globs`, `deny_paths`).
Tell them plainly:
- Compaction never changes the version; only a restart does, and Claude Code accepts `/restart` only when it is typed.
- A restart is free until the session's next turn, but a new version rebuilds the prompt cache, so the cost grows with the context. The helper therefore compacts a big session first while its cache is warm (`/compact`), restarts it on the next run, and restarts a session whose cache is already cold without typing anything after.
- It never types into a session with text in its input box, a dialog on screen, or work running. A session with background jobs is asked through the plugin's Stop hook to sort them (watchers to stop and start again, or real work to leave alone).
- Its log (`~/.claude/pit-stop/restart/restart.log`) says why each session was skipped.

## The hooks (on when the plugin is enabled)
- `hooks/precompact_wait_for_break.py` holds AUTOMATIC compaction while a tool call is in flight and lets it run at the next break. It never blocks a manual `/compact`, allows anything at or above the ceiling, and allows on any error. When an automatic compaction goes ahead it prints a keep-list for the summary. Log, counts only: `~/.claude/pit-stop/precompact.log`.
- `hooks/session_start.py` adds a three-line checklist after a compaction, and starts the daily tune in the background when it is switched on.
- `hooks/stop_pit_stop.py` hands over a waiting background-jobs ask once, or shows the user one line when an update is waiting and fleet mode is not running.
- Switches, in `~/.claude/pit-stop/config.json`: `auto_tune`, `compact_instructions` (false, or a string of extra notes), `after_compact_note`, `nudge`.

## If you are the session being asked
A message that starts "Pit Stop (this plugin's own hook, set up by your user)" at the end of your turn comes from this plugin's Stop hook. Do what it says: sort your background jobs; if any is real work in progress, change nothing and end your turn; otherwise list the ones you will start again in the file it names, stop them, and end your turn. Do not type `/restart` yourself.

## Rules
- Read before you change anything; show the dry run first.
- Back up a settings file before writing it; change only the keys Pit Stop owns.
- Never print message text from transcripts, secrets, or settings values other than the compaction point.
- Model, effort and anything that costs money are the user's decision.
