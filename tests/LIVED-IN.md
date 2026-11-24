# Lived-in round: what a person's machine commonly carries (family rule 2, part 3)

A stock machine is empty, a fleet box is full of our things, a person's machine is full of theirs. Before a tag,
after the stock round, one round runs on the same stock VM carrying the lines below. A proven line says "stock" or
"lived-in" and which lines were in it; a lived-in pass never stands in for the stock one. Add a line the day a
person's machine shows one this list lacked.

All values are made up for the round: no real token, key or address is ever put on the VM.

## Claude Code
1. `~/.claude/settings.json` with many keys: `permissions` (allow and deny lists), `model`, `includeCoAuthoredBy`,
   `cleanupPeriodDays`, and an `env` block naming several variables (`BASH_DEFAULT_TIMEOUT_MS`,
   `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `DISABLE_TELEMETRY`, a made-up `GITHUB_TOKEN`).
2. A status line of the person's own (`statusLine` running a script in `~/.claude/`).
3. Hooks of the person's own in `settings.json` on SessionStart, PreCompact and Stop: each prints a line and exits 0.
4. Another plugin enabled from another marketplace, whose hooks also run on SessionStart, PreCompact and Stop.
5. A `claude` of the person's own first on PATH (`~/bin/claude`, a wrapper that sets a variable and runs the real one).

## Shell and session
6. `~/.bashrc` (or `~/.zshrc`) exporting a made-up token and tool settings (`EDITOR`, `PAGER`, `LESS`), with
   `umask 002`.
7. `TMUX_TMPDIR` exported to a folder of the person's, and a `~/.tmux.conf` with `base-index 1`, `pane-base-index 1`,
   a changed prefix and `default-command` set.
8. Linux: `~/.config/environment.d/` with a file adding to PATH, so the systemd user manager carries it. Mac: a
   launch agent of the person's own beside Pit Stop's.
9. A crontab line of the person's own.
