# Changelog

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
