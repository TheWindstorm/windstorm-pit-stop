# Windstorm Pit Stop

**A context optimizer for Claude Code, by The Windstorm.**

<img src=".claude-plugin/icon.png" width="120" alt="The Windstorm">

A race car never stops mid-corner. It waits for the pit stop, then refuels, changes tires and goes back out. Pit Stop gives Claude Code sessions the same rhythm (the command is `/pit-stop`). At a break between tasks, and never in the middle of one, a session:

1. **Refuels.** It compacts its context at a break, at points fitted to that folder's own work.
2. **Changes tires.** It moves onto the newest installed Claude Code. *(Fleet mode, for people who run sessions in tmux.)* Compaction alone never does this: a running session keeps its version until it restarts.
3. **Gets a clean windshield.** The summary is told what to keep, and after every compaction the session gets a short checklist and re-reads only short, relevant instructions and memory that load in full.

The aim: you stop thinking about when to refresh context and which sessions are on an old version.

## Install

```text
/plugin marketplace add TheWindstorm/windstorm-pit-stop
/plugin install windstorm-pit-stop@thewindstorm
```

Then, once:

```text
/pit-stop setup
```

It shows a plan first and changes nothing until you say yes. It switches weekly tuning on, fits every running session's folder now, and (if you use tmux) offers fleet mode.

Requirements: Claude Code, plus Python 3.10+ (standard library only) as `python3`, or as `python` where `python3` is not a real interpreter (Windows, where `python3` can be the Microsoft Store stub: the hooks then run `python`). The hook commands need a POSIX shell, which Claude Code uses on macOS, Linux and Windows with Git Bash. Fleet mode needs tmux on Linux or macOS, and Claude Code's `/restart`, which needs the native macOS or Linux install (not WSL or Windows) and a paid Claude plan.

## Two points, not one

Claude Code compacts in two ways, and Pit Stop sets a point for each:

| Point | What happens there | Setting |
|---|---|---|
| **Idle point** | A session that has gone quiet compacts by itself, shortly before its prompt cache expires (about 55 minutes with the 1-hour cache), if its context is at least this big. This is the pit stop at a break. Claude Code's default is 200K and it never goes under 100K. | `CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS` |
| **Window** | The hard point, for a session that never goes quiet. Above it, Claude Code wants to compact at once; Pit Stop's hook holds that until the next break, up to a ceiling. | `CLAUDE_CODE_AUTO_COMPACT_WINDOW` |

So a session set to 200K / 500K compacts at 200K or more when it gets a quiet hour, and at about 500K if it never does: a little past 500K if it is mid-task, at the next break.

**When Claude Code prepares the summary early.** Claude Code can write the summary ahead of time, in the background: it asks the hook at about 82% of the window, and when the window is reached it swaps the finished summary in within milliseconds, keeping the newest messages word for word. That swap can land in the middle of a tool call and the hook is not asked again. So with this on, Pit Stop's hold decides where the summary *ends* (at a break, so the summary covers finished steps), and the swap itself costs no wait. Whether it is on is Anthropic's rollout and the `precomputeCompactionEnabled` setting; on the fleet this was measured on, 96 of 537 automatic compactions in a week were swaps of this kind. `/pit-stop optimize` counts them separately.

## What you get

| Part | What it does | On by default? |
|---|---|---|
| **Compact at a break** (hook) | Holds *automatic* compaction while a tool call is in flight, and lets it run at the next break. It always allows a manual `/compact`, anything at or above a hard ceiling, and any error case. | Yes, when the plugin is enabled |
| **Keep-list for the summary** (hook) | When an automatic compaction goes ahead, tells the summary what to keep: the task in progress and its next step, open to-dos, background jobs and how to start them again, files being changed, decisions. You can add a note of your own or switch it off. | Yes |
| **Checklist after a compaction** (hook) | Three lines added to the session's context: check your background jobs, re-read only what the next step needs, say so if the next step is unclear. | Yes |
| **Update line** (hook) | When a newer Claude Code is installed than a session runs, you see one line, once per version: type `/restart` at a break. Claude does not see it and no turn starts. Silent when fleet mode is running. | Yes |
| **`/pit-stop setup`** | One command: weekly tuning on, every running session's folder fitted now, and fleet mode if you want it. | You run it once |
| **`/pit-stop optimize`** | Reads a folder's own session history and recommends its idle point, window and ceiling, with the reasons. It also suggests (never applies) a model and effort level. `--lanes` works out each session of a shared folder by itself. | You run it, or weekly tuning does |
| **Weekly tuning** | Once a week per folder, in the background, moves each point at most one step toward what the last two weeks of history say. | Off until `/pit-stop setup` |
| **`/pit-stop reading`** | Checks the instruction files and memory index a folder's sessions re-read. Claude Code loads only the first **200 lines or about 24 KB** of `MEMORY.md`, whichever comes first, so an index that grows past that silently drops its newest entries. It trims without deleting anything. | You run it |
| **`/pit-stop report`** | One read-only line per running session: version vs newest, state, idle point, window, memory size. | You run it |
| **The pit stop** (fleet mode) | For tmux users with many sessions. At a break it moves each session onto the newest Claude Code, one at a time: a big session whose cache is still warm is compacted first and restarted after; a small one, or one idle so long its cache is cold, is restarted directly. Sessions with background jobs are asked to sort them first. Every skip is logged with its reason. | Off; dry run unless `--live` |

## How the optimizer decides

It reads the folder's transcripts and keeps counts only; message text is never printed or sent anywhere.

| Signal | What it suggests |
|---|---|
| Cold wake-ups (a prompt after an hour or more of quiet finds the prompt cache gone, so the whole context is paid for again) | The biggest cost of a long context: a lower idle point |
| Topic change (how much of the set of folders in play is new from one stretch of work to the next) | Old context is mostly noise: lower points |
| Files re-read right after a compaction that were already read before it | The point was too early: compact later |
| Almost no re-reading after compactions | Compacting loses little here: an earlier idle pit stop is cheap |
| The same folders for long stretches, with some re-reading | Long, connected work: keep more |
| Mostly reading and coordinating | Long threads benefit from more history |
| Mostly editing | Fresh context after each task usually helps |
| Tool error rate, work mix | Model and effort *suggestions* for a person to decide |

It also reports when the folder's compactions happened: while idle, as a prompt arrived, or mid-task.

A rule of thumb that surprised us: "it does the same thing over and over, so it can run long" is not what the history shows. What costs most is how often a session wakes up cold. A session that repeats one job and never looks back after a compaction can run *low* and lose nothing; the ones worth a high point do long, connected work and keep re-reading files after each compaction.

The two points are written to the folder's `.claude/settings.local.json` (under `env`), with a backup first. A profile with the reasons goes to `~/.claude/pit-stop/folders/` (your own folder, never the project). The hook reads its ceiling from there, so a session that is mid-task can run past the window up to the ceiling, but not beyond it. New points take effect at a session's next start; a pit-stop restart counts. To move the window of a running session at once, type `/autocompact <tokens>` in it.

Several sessions in one folder share that folder's settings. `--lanes` also writes a profile per session (`~/.claude/pit-stop/lanes/`); fleet mode can use it for early idle compaction (`idle_compact_after`).

These are heuristics, not guarantees. Model and effort changes affect quality and cost, so Pit Stop only suggests them.

## Fleet mode: what it types, and what it costs

Claude Code accepts `/restart` only when it is typed at the prompt, so fleet mode types into tmux panes. It types three things, each only into an idle session with an empty input box and no dialog:

- `/restart`: free by itself, since no turn starts. A new version does rebuild the prompt cache from the top at the session's next turn, so a restart costs more the bigger the context is. That is why it happens right after a compaction, on a small context, or when the cache has gone cold anyway.
- `/compact`: only for a big session whose cache is still warm (quiet between 5 and 48 minutes with the 1-hour cache), so the summary is read from the cache. The restart follows on the next run. Switch off with `"compact_before_restart": false`.
- One note, in two cases only: a session that stopped its background jobs for the restart is told to start them again; and a session that has not loaded the plugin's hooks yet gets the background-jobs ask typed, while its cache is warm or its context small. After a restart with no jobs to start again, nothing is typed. That includes the hour-idle restart (`cold_idle`): the session comes back on the new version and stays quiet until its next prompt, so no full-context turn is spent.

**Background jobs.** Claude Code refuses `/restart` while background work runs. The helper leaves an ask for that session; the plugin's own Stop hook hands it over at the end of the session's next turn. Claude sorts its jobs: watchers it can simply start again are listed and stopped, and the restart follows; if anything is real work in progress, it changes nothing and is asked again in 12 hours. If a session stopped its jobs and no restart came within 30 minutes, it is told to start them again.

Settings live in `~/.claude/pit-stop/restart.json` (see the top of `scripts/restart_at_break.py`): `allow_names`, `deny_names`, `deny_name_globs` (for example `"* Boss"`), `deny_paths`, and the timings. `/pit-stop setup --fleet` never changes an existing file.

## Safety

- **Fails safe.** Every hook allows on any error. The restart helper skips anything it cannot read.
- **Never mid-task.** It never types into a session with text in its input box, a dialog open, or work running, and never restarts one with background jobs it hasn't agreed to stop. Claude Code itself refuses `/restart` while background work runs.
- **Never a cold, costly turn.** Nothing that starts a turn is typed into a big session whose cache has gone cold.
- **Checks before Enter.** After typing, the restart helper presses Enter only if the input box holds exactly what it typed; otherwise it clears the line, skips, and backs off that session.
- **Asks through its own hook.** The background-jobs ask reaches Claude through the plugin's Stop hook, once, and never twice in a row.
- **One step a week.** Weekly tuning moves a point at most one step, and keeps the newest three backups of the settings file.
- **Your folder, not the project's.** Profiles, switches and extra summary notes are read from `~/.claude/pit-stop/` only, so a cloned repo cannot steer the summary. A ceiling from a 0.1.x project file is still read, and clamped.
- **Capped ceilings.** A ceiling from a profile or the environment is clamped to 1.5x the first hold and 95% of the model's context size, so a cloned repo cannot hold compaction until the context overflows.
- **Backups first.** Every file is backed up before it is written. Only the keys Pit Stop owns are changed.
- **Local only.** No network calls. Logs hold counts only and rotate at 5 MB.

`/pit-stop setup --fleet` installs a timer (systemd user timer or launchd) that runs a small launcher every 5 minutes; the launcher finds the installed plugin each run, so the timer keeps working across plugin updates. A timer you wrote yourself that names a version folder stays pinned to that version until you change it. Timers start with a bare PATH: setup puts the folders of `tmux` and `claude` in it. Fleet mode reads Claude Code's local session registry (`~/.claude/sessions/`), which is not a public interface; if that gives nothing usable it falls back to the documented `claude agents --json`.

## Tested

- Hook decisions: mid-action holds, a break allows, the ceiling allows, a manual `/compact` allows, bad input allows. A hold prints nothing; an allowed automatic compaction prints only the keep-list.
- The Stop hook hands the background-jobs ask to Claude once, in a real session on Claude Code 2.1.322 (exit 2 passes through the `python3`/`python` probe line).
- The restart plan as a pure function, and the helper in live mode against a faked pane: compact first then restart, cold restart with nothing typed, the ask left for the Stop hook, a late restart still getting its start-your-jobs note, stale lists put away, nothing typed during a typing backoff.
- The helper's dry run against a live fleet of 94 sessions (it types and writes nothing).
- `/restart` on Claude Code 2.1.318 → 2.1.319 keeps the same session id, conversation, model and effort. Scheduled session crons survive. In that test the first turn after the restart read about 47K tokens from cache; do not count on that, since a new version normally rebuilds the cache from the top.
- Live on one real session (Claude Code 2.1.319 → 2.1.322, 111K context, cache warm): `/compact` typed, compacted 39 s later; on the next run `/restart` typed, same session id on the newest version 12 s later, nothing typed after.
- Not yet proven on a real machine: the keep-list's effect on summaries, the Stop-hook ask followed by a restart in a session with real background jobs, and the launchd timer written by `/pit-stop setup`.

Run the tests: `python3 -m pytest tests/`. On any machine, `sh tests/smoke_hooks.sh` runs each hook's real command line in a throwaway folder and says PASS or FAIL per check (it starts no session and installs nothing).

## Credits

The hook and the restart helper were first built by **Windy Boss**, an AI agent in The Windstorm's Windy fleet, on a Mac mini in November 2026. They were then ported to Linux and packaged here.

## License

[Apache License 2.0](LICENSE). "Windstorm Pit Stop" and "The Windstorm" are names of The Windstorm and are not licensed as trademarks; see [NOTICE](NOTICE).
