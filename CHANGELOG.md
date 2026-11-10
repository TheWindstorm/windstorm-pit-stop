# Changelog

## 0.2.0 (2026-11-10)
From a review of 0.1.3 against a fleet of 94 sessions and Claude Code 2.1.322. In 4 days that fleet compacted 275 times: 39% by Claude Code's own idle compaction, 33% as a prompt arrived, 29% mid-task. 46 sessions sat on an old version, 34 of them for one reason: a big context and no compaction in the last 30 minutes.

Two points instead of one:
- The optimizer now recommends an **idle point** (`CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS`, where a quiet session compacts by itself) next to the window, and `--apply` writes both. 0.1.x set only the window, while the idle point decided 39% of real compactions.
- New signals: cold wake-ups per day and the tokens paid again on them, topic change (how much of the set of folders in play turns over), and when compactions happened (idle, as a prompt arrived, mid-task).
- `--lanes`: a profile per session for folders that several sessions share. `--max-steps N` limits how far a point moves.
- Profiles moved to `~/.claude/pit-stop/folders/` (no more untracked `.claude/pit-stop.json` in your repo). The 0.1.x file is still read.

Install and forget:
- `/pit-stop setup`: one command for weekly tuning, fitting every running session's folder, and fleet mode. It shows a plan first, never changes an existing `restart.json`, and never adds a second timer.
- Weekly tuning (`scripts/autotune.py`, started in the background by a SessionStart hook): at most one step per point per week. Off until setup switches it on.
- A launcher (`~/.claude/pit-stop/bin/pit-stop-restart`) so a timer no longer names a version folder. Timers that name one stay pinned until changed.

The pit stop (fleet mode):
- **Compact first, restart after.** A big session at a break whose cache is still warm gets `/compact`; the next run restarts it on a small context. `compact_before_restart: false` switches it off.
- **Nothing typed after a restart** unless the session listed jobs to start again, so restarting a session whose cache is cold costs nothing (asked for by Veron's HERDR Boss, who had switched `cold_idle` off because the typed note started a cold turn on a big context).
- **The background-jobs ask goes through the plugin's Stop hook**, at the end of the session's next turn, instead of being typed. It asks Claude to sort its jobs: watchers to stop and start again, or real work to leave alone. A session without the plugin's hooks gets it typed once, only while that is cheap. Sessions with background jobs are now asked whatever their context size (0.1.x asked only those already at a break).
- A session that wrote its re-arm list and stopped its jobs is restarted whatever its context size (0.1.x could leave it waiting with its watchers down). If no restart comes in 30 minutes it is told to start its jobs again.
- Optional `idle_compact_after`: compact a quiet session over its idle point while its cache is warm, for fleets whose sessions are woken too often for Claude Code's own idle compaction.
- `deny_name_globs`, `--only NAME` (a canary on one session), `restart_cooldown` (6 h, was a fixed 20 h). A dry run no longer writes to `restart.log`.
- Sessions are read from the local registry, with the documented `claude agents --json` as the fallback.

Hooks:
- PreCompact: when an automatic compaction goes ahead it prints a keep-list for the summary (and, at the ceiling, asks for the step that was in flight). Add your own note or switch it off in `~/.claude/pit-stop/config.json` (`compact_instructions`).
- SessionStart: a three-line checklist after a compaction (`after_compact_note: false` switches it off).
- Stop: the ask above, and for people without fleet mode one line, once per version, that an update is waiting (`nudge: false` switches it off).
- All three run through the 0.1.4 `python3`/`python` probe line; exit 2 passes through.

Hardening, from an independent review of this change before it was pushed:
- A restart that lands after the 2-minute confirm no longer loses the "start your jobs again" note: the list stays until the note is sent, and the next run sends it. A list is put away only after its note went out.
- A re-arm list counts as consent only if it was written after an ask on record; older ones are put away without typing. State files are now named by session name plus session id, so two names that reduce to the same letters (or to none) never share state. State files from 0.1.x (named by name only) are not read.
- No ask while a restart cannot follow (the cooldown), and no typed note of any kind while the typing backoff runs.
- A refused or unconfirmed `/restart` starts the cooldown (0.1.x typed it again every run).
- `--only` with no name exits instead of handling every session. One unreadable session no longer stops the run.
- A pane in tmux copy mode is skipped. A question in Claude's last reply ("Do you want me to...?") no longer reads as a dialog: only the rows around the input box count.
- `--apply` refuses a settings file it cannot read as JSON instead of rewriting it; setup does the same for `config.json`.
- With fewer than 2 compactions the window stays exactly where the user has it. Odd transcript lines (not an object, a string message) are skipped by every reader. The PreCompact hook allows on any stdin.
- A dry run creates no folders. The systemd unit allows 10 minutes.
- `tests/smoke_hooks.sh`: runs each hook's real command line on any machine, PASS or FAIL per check.

Proved live before release on one session of a 94-session fleet: `/compact`, then `/restart` 110 s later, same session id on the newest version, nothing typed after.

Docs: `/restart` needs the native macOS or Linux install and a paid plan; a new version normally rebuilds the prompt cache, so the "47K from cache" line is now marked as one test, not a promise.

## 0.1.4 (2026-11-10)
- Windows: the compact hook now runs `python` when `python3` is not a real interpreter (on Windows `python3` can be the Microsoft Store stub, which only prints a message). The hook's own exit code still passes through unchanged (2 = hold the compaction). Tested with a stub `python3` on a POSIX shell; a run on a real Windows machine is still to be recorded.
- `/pit-stop reading` knows the second limit: Claude Code stops reading `MEMORY.md` at about 24 KB as well as at 200 lines. The report says CUT OFF or "near the limit" for either, and `--trim` refuses a result over 24 KB.
- `--trim` groups memories named with hyphens (`feedback-<topic>-...`) the same way as the underscore style (`feedback_<topic>_...`).

## 0.1.3 (2026-11-10)
Restart helper: back off after a typing mismatch (found on Veron by its HERDR Boss: one pane got /restart typed and cleared 5 runs in a row, every 2 minutes, and each typing reset the session's idle clock):
- After the input box fails to match the typed text, that session is left alone for 30 minutes, then 1 h, 2 h... at most 12 h per miss in a row (`mismatch_backoff` 1800, `mismatch_backoff_max` 43200). A successful send clears the count; a busy screen does not count as a miss.
- The mismatch log line gives the number of characters read and typed (never the text read), so the cause can be found.

## 0.1.2 (2026-11-10)
Restart at the break now reaches busy, long-running sessions (found on the Mac mini: 4 of 5 sessions sat on 2.1.319 after 2.1.322 was installed, because none was ever idle 5 minutes with a context under 100K, not even right after compacting):
- A session that compacted in the last 30 minutes is restarted once idle 60 s, whatever its context size. A compaction only happens at a break, and the context is as small as it will get.
- A session idle for an hour is restarted whatever its size: its prompt cache is cold, so its next turn re-reads everything anyway.
- The small-context rule (< 100K, idle 5 minutes) stays.
- Every skipped session gets a log line with the reason.
- Docs say plainly that compaction never changes the version; only a restart does. Suggested schedule: every 2 to 10 minutes.
- New config keys: `compact_window` (1800), `min_idle_after_compact` (60), `cold_idle` (3600).

## 0.1.1 (2026-11-08)
Review fixes (thanks to Windy Boss):
- Restart helper: after typing, it presses Enter only if the input box holds exactly the typed text. If anyone typed in the same second, it clears the line, logs it and skips.
- Hook: a configured ceiling (env or a project's `.claude/pit-stop.json`) is clamped to at most 1.5x the first hold and 95% of the model's context size, so a file shipped in a cloned repo cannot hold compaction until the context overflows. The log says `clamped`.
- Logs rotate at 5 MB (one `.1` file kept).
- Hook closes the profile file properly; documented that a stale hold file can only lower the next ceiling.

## 0.1.0 (2026-11-07)
- Named Windstorm Pit Stop (plugin windstorm-pit-stop, command /pit-stop); The Windstorm logo as the icon.
- Compact-at-a-break PreCompact hook with ceilings (env, per-folder profile, or a safe 110% default).
- `/pit-stop optimize`: per-folder compaction point from the folder's own history; model/effort suggestions only.
- `/pit-stop reading`: instruction-file and memory-index check; lossless trim under the 200-line load limit.
- `/pit-stop report`: read-only status of running sessions.
- Restart-at-the-break fleet helper (tmux, dry run by default).
