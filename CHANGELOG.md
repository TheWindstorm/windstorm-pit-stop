# Changelog

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
