# Windstorm Pit Stop

**A context optimizer for Claude Code, by The Windstorm.**

<img src=".claude-plugin/icon.png" width="120" alt="The Windstorm">

A race car never stops mid-corner. It waits for the pit stop, then refuels, changes tires and goes back out. Pit Stop gives Claude Code sessions the same rhythm (the command is `/pit-stop`). At a break between tasks, and never in the middle of one, a session:

1. **Refuels.** It compacts its context at a break, at a compaction point tuned to that folder's work.
2. **Changes tires.** It moves onto the newest installed Claude Code. *(Optional fleet mode.)* Compaction alone never does this: a running session keeps its version until it restarts.
3. **Gets a clean windshield.** After every compaction it re-reads only short, relevant instructions and memory that load in full.

## Install

```text
/plugin marketplace add TheWindstorm/windstorm-pit-stop
/plugin install windstorm-pit-stop@thewindstorm
```

Requirements: Claude Code, plus `python3` (3.10+, standard library only). Fleet mode also needs tmux on Linux or macOS.

## What you get

| Part | What it does | On by default? |
|---|---|---|
| **Compact at a break** (hook) | Holds *automatic* compaction while a tool call is in flight, and lets it run at the next break. It always allows a manual `/compact`, anything at or above a hard ceiling, and any error case. | Yes, when the plugin is enabled |
| **`/pit-stop optimize`** | Reads a folder's own session history and recommends a compaction point, for example 300K, 500K or 700K, plus a hard ceiling. It also suggests (never applies) a model and effort level. You apply it with your OK. | You run it |
| **`/pit-stop reading`** | Checks the instruction files and memory index a folder's sessions re-read. Claude Code loads only the first **200 lines** of `MEMORY.md`, so an index that grows past that silently drops its newest entries. It trims without deleting anything. | You run it |
| **`/pit-stop report`** | One read-only line per running session: version vs newest, state, compaction point, memory size. | You run it |
| **Restart at the break** (fleet mode) | For tmux users with many sessions. Moves sessions onto the newest Claude Code at a break, one at a time: right after a compaction (any size, once idle 60 s), or with a small context, or after an hour idle (the prompt cache is cold anyway). Sessions with background jobs are asked first. Every skip is logged with its reason. | Off; dry run unless `--live` |

## How the optimizer decides

It reads the folder's transcripts and keeps counts only; message text is never printed or sent anywhere.

| Signal | What it suggests |
|---|---|
| Files re-read right after a compaction that were already read before it | The point was too early: compact later |
| Long idle gaps before prompts (cold prompt cache) | A long context costs more each time: compact earlier |
| Mostly reading and coordinating | Long threads benefit from more history |
| Mostly editing | Fresh context after each task usually helps |
| Tool error rate, work mix | Model and effort *suggestions* for a person to decide |

The compaction point is written to the folder's `.claude/settings.local.json` (`env.CLAUDE_CODE_AUTO_COMPACT_WINDOW`), with a backup first. A profile with the reasons goes to `.claude/pit-stop.json`. The hook reads its ceiling from there, so a session that is mid-task can run past the compaction point up to the ceiling, but not beyond it. Re-run the optimizer weekly.

These are heuristics, not guarantees. Model and effort changes affect quality and cost, so Pit Stop only suggests them.

## Safety

- **Fails safe.** The hook allows compaction on any error. The restart helper treats anything it cannot read as busy and skips it.
- **Never mid-task.** It never restarts a session with text in its input box, a dialog open, work running, or background jobs it hasn't agreed to stop. A large context waits for its next compaction or a long idle. Claude Code itself refuses `/restart` while background work runs.
- **Checks before Enter.** After typing, the restart helper presses Enter only if the input box holds exactly what it typed; otherwise it clears the line and skips.
- **Capped ceilings.** A ceiling from a project file or the environment is clamped to 1.5x the first hold and 95% of the model's context size, so a cloned repo cannot hold compaction until the context overflows.
- **Backups first.** Every file is backed up before it is written. Only the keys Pit Stop owns are changed.
- **Local only.** No network calls. Logs hold counts only and rotate at 5 MB.

Schedule fleet mode every 2 to 10 minutes; the shorter the interval, the sooner it catches the break after a compaction. Timers start with a bare PATH: under launchd or cron, set PATH so `tmux` is found (on macOS with Homebrew, include `/opt/homebrew/bin`), and set `tmux_socket` in `restart.json` if your sessions run on a named tmux server. Fleet mode relies on Claude Code's local session registry (`~/.claude/sessions/`), which is not a public interface. If it changes, sessions read as busy and nothing restarts.

## Tested

- Hook decisions: mid-action holds, a break allows, the ceiling allows, a manual `/compact` allows, bad input allows. Stdout is always empty.
- A hook added to a running session's project settings is picked up live, with no restart needed.
- `/restart` on Claude Code 2.1.318 → 2.1.319 keeps the same session id, conversation, model and effort. Scheduled session crons survive. The first turn after the restart read about 47K tokens from cache and wrote under 500.

Run the tests: `python3 -m pytest tests/`

## Credits

The hook and the restart helper were first built by **Windy Boss**, an AI agent in The Windstorm's Windy fleet, on a Mac mini in November 2026. They were then ported to Linux and packaged here.

## License

[Apache License 2.0](LICENSE). "Windstorm Pit Stop" and "The Windstorm" are names of The Windstorm and are not licensed as trademarks; see [NOTICE](NOTICE).
