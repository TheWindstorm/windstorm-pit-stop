# Changelog

## Plan 1.7 (2026-11-22)
- **The README's window sentence** (plan change; G1.16 added, open). The README said that above the window Claude Code wants to compact at once. In the overnight stress test it asked a little before the context reached the window. No invariant and no Part 0 rule changes.

## Plan 1.6 (2026-11-22)
- **G3.9 corrected** (plan change; still open). Plan 1.5 said settings backups were never removed. That was wrong. `prune_backups()` keeps the newest three (G2.11), and daily tuning and setup run it. Only a hand-run `optimize.py --apply` does not, and that is where 55 writes left 56 copies. G3.9 now asks that path to prune too, with three as everywhere else, not five. No invariant and no Part 0 rule changes.

## Plan 1.5 (2026-11-22)
- **Backups kept to the newest five** (plan change; G3.9 added, open). Every settings write leaves a backup that was never removed: seen in the overnight stress test, 56 next to one file. No invariant and no Part 0 rule changes.

## Plan 1.4 (2026-11-22)
- **Exit and resume only with a transcript** (plan change; G15.1, still open, gets one sentence). A session that never took a turn has no conversation to resume: seen in the overnight stress test, where two such sessions ended after a restore that also dropped a `--plugin-dir` flag. No invariant and no Part 0 rule changes.

## Plan 1.3 (2026-11-22)
- **The tuner's context limit from the model** (plan change; G2.25 added, open). On a fresh folder the tuner guessed a 200K context for a model that has 1M, and said so. No invariant and no Part 0 rule changes.

## Plan 1.2 (2026-11-22)
- **Two open codons from the overnight stress test** (plan change; G9.7 and G12.7 added, both open). G9.7: the checkup names an older Pit Stop install that wins over a newer one in another scope. G12.7: `setup.py --undo` puts back what setup changed. No invariant and no Part 0 rule changes.

## Unreleased
- **A hand-run `optimize.py --apply` keeps three backups** (built: G3.9). Daily tuning and setup already kept the newest three copies of a settings file. The hand-run path did not, and 55 writes in the overnight stress test left 56. PROMISES.json row P6 is corrected: it first said Pit Stop never removes backups, which was wrong.
- README: the Window row says Claude Code asks to compact a little before the context reaches the window, with the one measurement seen (built: G1.16, row P7). It said 'at once' above it.
- README: a "Staying up to date" section. It says how Pit Stop is updated (Claude Code's auto-update for its marketplace, which `/pit-stop setup` switches on from 0.5.2, and the fleet helper's `plugin_updates`). It also gives the two commands that update an older install once, and says that `/pit-stop setup`, typed after them, switches auto-update on.

## 0.6.1 (2026-11-20)
- **Sessions in Herdr, opt-in** (built: G4.35 to G4.38, G8.9). With `"herdr": true` (or a path to the binary) in restart.json, a session with no tmux pane of its own that Herdr names by its session id is reached through Herdr: `herdr pane read`, `send-text`, and `send-keys enter` only after an exact read-back, with `ctrl+u` to clear. These are the same breaks and the same typing rules as in tmux. tmux comes first. A missing or silent Herdr changes nothing. The needs-you list counts a Herdr pane as a pane. Off by default, because a Herdr pane can belong to someone else's agent. The key names were checked on a scratch Herdr 0.9.5 session (open question 10).
- **Trigger evals for the skill's description** (built: G12.4 to G12.6). `evals/` holds 10 queries that must load the pit-stop skill and 10 near misses that must not, the runner, and the recorded Sonnet results for the 0.6.0 description: 10 of 10 fired and 10 of 10 stayed out, 3 runs each. `tests/test_trigger_evals.py` fails CI when the recorded results are for another description, other queries or another run count. The eval itself runs `claude -p`, so it is run by hand, never in CI.
- README: the measured numbers counted again on November 20, with dates. Mid-task refreshes are 19% with Pit Stop over November 10 to 19 (148 of 762), against 46% before it and 74% in sessions without it. Prepared-ahead swaps are counted apart. The rise from the first count's 11% came on days of long tool runs: 113 ceiling releases against 120 mid-task refreshes, November 14 to 19. Also a section on the Herdr option.
- Hooks unchanged (generation 2).

## Plan 1.1 (2026-11-20)
- **The restart helper can reach sessions in Herdr, opt-in** (plan change; codons G4.35 to G4.38 and G8.9 added, all open; the G4 intro names Herdr; open question 10 added). Off unless `"herdr": true` (or a path) is in `restart.json`. Then a session with no tmux pane that Herdr names by its session id gets the same break rules and typing rules through `herdr pane read`, `send-text` and `send-keys`. tmux stays first. No invariant and no Part 0 rule changes.
- **Trigger evals gate the skill's description** (plan change; codons G12.4, G12.5 and G12.6 added, all open). Ten queries must load the pit-stop skill and ten near misses must not, each in at least 2 of 3 Sonnet runs or at most 1 of 3. Their tests fail CI when the recorded results are for another description, other queries or another run count, so a wording change needs a new run. The trials run with the installed plugin and every tool that changes files switched off. No invariant and no Part 0 rule changes.

## Plan 1.0 (2026-11-20)
- **The DNA Strand Master Plan** (`DNA-STRAND-MASTER-PLAN.md`): Pit Stop described in arrears as 18 genes, G1 to G18, made of codons, and 12 invariants. Each codon is one claim with one test named after it. G1 to G12 are built, their tests renamed `test_G<n>_<m>_...` with no other change. G13 to G18 are open. New `tests/test_strand.py` proves the invariants and fails CI when a script, hook or helper setting has no codon, or a built codon has no test. From now on a change starts as a plan change.

## 0.6.0 (2026-11-17)
- **Pit Stop can keep Claude Code itself current, however it was installed.** New `"claude_updates": "auto"` in restart.json (off by default; it is tried on one machine first): when this machine's Claude Code is behind the newest release, the helper runs the one update its install method allows (`claude update`, `brew upgrade --cask`, or moving mise's global pin), checks that the new version is there, and logs it; a failure is logged and goes to `notify_command`. It waits two hours after a release appears (Claude Code's own updater first) and tries one release at most every six hours. Never sudo, never a project's own pin, never against `"hold_claude_version"` in config.json or a switched-off updater. Sessions then move onto it at a break, as before.
- **A switched-off updater stops it for every install method.** `DISABLE_UPDATES` or `DISABLE_AUTOUPDATER`, in the environment or in `~/.claude/settings.json`, now stops Pit Stop's Claude Code update for mise and Homebrew too, not only for native and npm installs. Found while writing the DNA strand's invariant tests.
- **mise and asdf shims.** When `claude` on PATH is a version manager's shim, its real path is the manager itself; that read as "installed in a way Pit Stop does not know" on two Linux machines. Now it is mise (or asdf), with the version from `claude --version`.
- **Installs only an administrator can change.** An npm install under a system folder (seen under /usr/lib) was told "claude update", which cannot work there. Pit Stop now says it needs an administrator, gives the sudo command for that person and the native installer (which needs none) as the other way, and never runs either itself.
- README: how the Claude Code update works and how to say no; a link to the Pit Stop page on thewindstorm.ai.
- Hooks unchanged (generation 2).

## 0.5.3 (2026-11-17)
- **The skill loads in strict parsers.** The skill's description had an unquoted ": " ("for the user: run the checkup"). Claude Code reads it, but strict YAML parsers (the Agent Skills validator `gh skill publish`, and other tools that read skills) rejected the whole skill. The colon is gone.
- **It cannot come back.** New `tests/test_skill_spec.py` checks every SKILL.md against the Agent Skills rules (name, folder, description length, values a strict parser would misread), and CI now also loads each one with a real YAML parser and runs `gh skill publish --dry-run`.
- The skill names its license (Apache-2.0, as the repo), which the Agent Skills validator recommends.
- Hooks unchanged (generation 2).

## 0.5.2 (2026-11-15)
- **Behind, however Claude Code was installed.** Pit Stop read only the native installer's folder (`~/.local/share/claude/versions`). On any other install it found no "newest version" at all, so it never judged a session or the machine behind. On one machine, a mise config held Claude Code at 2.1.322 while 2.1.325 was out, and nothing said so for five days. On another (npm), the checkup said "newest: not found". New `scripts/install_method.py` follows the `claude` a new session would start (PATH, then links), reads its version there (folder name, npm's package file, or `claude --version`), names the method (native, npm, Homebrew, WinGet, apt, dnf, apk, mise, asdf, nix, or unknown), finds a version manager's pin (nearest folder first, then the global file), and compares with the newest release on the install's channel (`latest` or `stable`). Tested against fake installs of each method, and run read-only on four real machines: native (Linux and Apple Silicon), npm, and mise.
- **The needs-you list names the machine.** Once this machine's Claude Code has been behind the newest release for a day (counted from the first time Pit Stop saw it behind, not from the newest release, so a machine held for weeks reads as weeks), needs-you.md says what holds it and the one command that moves it, and `notify_command` hears it once. The checkup shows the same, with no file paths on the `--share` page.
- **One version number from the network.** The newest release is read from the address Anthropic's install script uses, at most every six hours, with a generic client name; nothing about the machine is sent. Off with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_UPDATES`, or `"release_check": false` in config.json. The README's "Local only" line says so.
- **The helper keeps working without a native install.** It used to stop at "no installed versions found", so on npm and other installs fleet mode also skipped idle compaction, reloads, the needs-you list and tuning. Now only restarts are skipped, and sessions behind are named with how to restart them by hand (`/restart` needs the native install on macOS or Linux).
- **Pit Stop keeps itself current.** `/pit-stop setup` switches on Claude Code's auto-update for Pit Stop's marketplace (off by default for marketplaces other than Anthropic's; one machine was still on 0.1.4). `--no-self-update` leaves it alone; an explicit `false` is kept.
- A checkup test used today's date against fixed November 1 history and failed from November 15; it now uses a fixed day.
- Hooks unchanged (generation 2).

## 0.5.1 (2026-11-14)
- **A typing check that fails now makes sure the box is empty.** When the restart helper types a note or a command and the input box does not hold exactly that text, it presses no Enter and clears the box. It used to press Ctrl-U once and not look again. On a Mac mini still on 0.1.4, a long background-jobs note (497 characters) read back as 258, one Ctrl-U did not empty the box, and the note sat in a session's input box until a person pressed Enter. Now the helper re-reads the box after each Ctrl-U and presses it again, up to 6 times, only while what is left is part of its own text; text someone else typed is never cleared further. If the box is still not empty, restart.log says COULD NOT CLEAR and `notify_command` hears it once, so a person looks before pressing Enter. Notes have been at most 560 characters since 0.3.x, and the background-jobs ask goes through the session's own Stop hook, so this case is rarer now, but a long wrapped note or a slow screen can still cause it.

## 0.5.0 (2026-11-13)
- **`/pit-stop checkup`: the first thing to run.** One report on every Claude Code session on the computer, from its own history: sessions waiting on a question for 2 hours or more, sessions behind the newest Claude Code and for how long, sessions not running this release's hooks, automatic refreshes in the last 14 days and how many landed in the middle of a task, re-reads after a refresh, cold wake-ups, each project's refresh point now and the one its history suggests, and the modelled cached-read cost of each at list prices for the model the project last used (labelled an estimate). Printed, and saved as a page with charts (`~/.claude/pit-stop/checkup/checkup.html`; `--share` writes one without project or session names). Counts and times only; calls no model; sends nothing. The skill runs it first, before setup, and tells an agent that installs Pit Stop for its user to show it.
- **Your own before and after.** The first checkup keeps a baseline. A checkup 6 or more days later adds "Since your first checkup": refreshes mid-task, sessions behind, sessions waiting, then and now. A week after the first one, the Stop hook tells the person once (`"checkup_reminder": false` switches that off).
- optimize.analyze keeps each compaction's time and kind (`compaction_log`), so a report can count over its own window.

## 0.4.0 (2026-11-12)
The goal, in the owner's words: install it and stop worrying. It picks each session's compaction point and keeps re-fitting it as the work changes, waits for a break to compact, and keeps Claude Code and the plugins up to date at breaks, also when background jobs have been in the way for days.

- **Daily tuning that checks its own last move.** Tuning ran once a week; now once a day per folder (`"tune_every_days"`, 1 to 30), still one step at a time. Each automatic compaction is now kept with its size and how many of the reads right after it went back to files from before it, so the tuner can tell how each window did within one history. A move is measured before the next: the tuner waits until the new window has run at least 3 automatic compactions (up to 14 days). A lowering after which sessions re-read 30% or more of what the compaction dropped, and 15 points more than at the old window, is undone, and that folder is not lowered again for 14 days. Each folder's moves are kept in `~/.claude/pit-stop/tuned/<key>.json`; autotune.log says when it waits or undoes.
- **Background jobs that block an update for days.** After `"jobs_escalate_days"` (3) days since the session was first asked about its jobs (for the version it still runs), the ask changes from "if anything is real work, do nothing" to "at the next point where nothing is lost, stop everything that can be started again, real work too". Only a job that cannot stop without loss keeps the session waiting. The Stop hook passes the stronger ask on (the ask file carries the days, checked like its other fields); the typed form stays under 560 characters. The session stops its own jobs; the helper never kills a process.
- **Plugins kept up to date before the restarts** (`"plugin_updates"`: `"off"` by default in an existing restart.json, `"pit-stop"` in one written by `/pit-stop setup --fleet`, or `"all"`). The helper runs Claude Code's own `claude plugin marketplace update` and `claude plugin update` once a day, and at most hourly while sessions are waiting to be restarted, before it restarts anyone, so one restart brings the newest Claude Code and the newest plugins. Only plugins installed for the user, names checked before they reach a command, 5 minutes per run at most, a failure is a log line and a notify message. A helper that runs from a pinned version folder says so in its log: to follow its own updates, its timer must run the launcher.
- **Sessions kept off the helper's list are named once a week.** A session not on `allow_names` (or on a deny list) is never restarted by the helper, so it can sit on an old Claude Code for weeks. On one machine, a server-admin session started on Nov 4 was still on 2.1.318 on Nov 12 and nobody was told. Once a session has been behind for a week or more, the helper now names it to `notify_command` once a week, so keeping it off stays a choice someone made. The owner can write the reason once, `"off_list_reasons": {"Session name": "why"}` in restart.json; it is shown in needs-you.md and in the notice.
- **Tuning needs no timer and no watching session.** The Stop hook now also starts the daily tune of its session's folder in the background when tuning is on and due (a file-date check when it is not), as the SessionStart hook already did at a session's start. A session left open for weeks is tuned too, on any machine with the plugin, fleet mode or not. A new point still takes effect at the session's next start.
- **Tune only a test group** (`"tune_folders"` in config.json: a list of folders). With a list, tuning (the hooks, the helper and autotune itself) touches only those folders; without one, every folder, as before. Anything but a list of folder names counts as no list, so a typo can fail to narrow tuning but never switches it off. Made for comparing tuned sessions with untouched ones on one machine.
- Hooks: the Stop hook's ask has its stronger form, and it starts the daily tune; same hook generation (an older Stop hook ignores the new field and simply does not start tunes).

An independent review read 0.4.0 before it was pushed for merge and found defects, all fixed here with tests:
- Compactions were matched to a window by size alone. A Pit Stop hold carries a compaction well past where its window starts it, and a session keeps its old window until it restarts. So evidence for a new window counted compactions the old one made, and undos almost never fired. Now a compaction counts for the new window only if it came after the move and is a size the old window could not have produced. The old window's record is the compactions between the move before and this one, so on 200K-context folders no compaction counts for two windows.
- A first move (no window set before) was not recorded, so two moves could happen back to back.
- A damaged history file stopped that folder's tuning for good.
- The stronger jobs ask counted days from when a newer Claude Code came, not from the first ask about jobs. A session busy for days would have got it as its first ask.
- The plugin-update time budget could be overrun by one command's timeout.
- A plugin name starting with "-" passed the name check.

The stronger ask is on by default (3 days) in an existing restart.json too; `"jobs_escalate_days": 0` switches it off.

Not proven live yet: an undo on a real folder (it needs a lowering, then 3 compactions); the stronger ask in a session that has been stuck for days; `claude plugin update` run by the timer.

## 0.3.4 (2026-11-12)
- **A hook whose plugin folder is gone lets Claude Code go on.** Claude Code deletes an old version folder 14 days after an update (the `.orphaned_at` marker). A session that loaded its hooks from that folder keeps running the same command. Until now, python then exits 2 on the missing script, and for PreCompact exit 2 means "hold": that session could never auto-compact again. Each hook command now checks its file first and exits 0 (do nothing) if it is gone. Same hook generation. The fix reaches a session only once it loads this release's hooks (restart or `/reload-plugins`), so sessions still on hooks from a folder marked `.orphaned_at` should be moved before that folder is deleted.
- **`/pit-stop status` tells "no hook" apart from "an older release's hooks".** It used to count both as "no working Pit Stop hook". On one machine that read 91, when 21 had none and 70 ran older hooks that still wait for breaks.

## 0.3.3 (2026-11-12)
Closes the gap left in 0.3.2 (from the review of 0.3.2): the 560-character limit had no fallback of its own.

- **Each typed note has a short last form.** The "start your jobs again" note that names the list's file, and the typed background-jobs ask, now fall back to a short form when a long path would push them over 560 characters. The full ask had room for a re-arm path of only about 110 characters (Veron's longest is 78); a long home folder or a long session name would have had the ask refused every run. Now both notes fit with a path of up to 300 characters. Past that, `type_line` still refuses and logs it.
- **The name part of a session's state key is cut at 40 letters** (`KEY_NAME_MAX`). The key is part of the path typed in those notes. Keys of names up to 40 letters are unchanged (all 96 sessions on Veron); a session with a longer name gets a new key, so an ask it has open at the moment of the update is asked again later.

## 0.3.2 (2026-11-12)
One fix, found on the first live restarts of 0.3.1.

- **No typed note is longer than 560 characters.** Claude Code folds long typed text into a "[Pasted text]" block. The helper then no longer sees its own text in the input box, clears the box and presses no Enter. That is the safe way to fail, but the first session restarted with a two-job list (a note of about 930 characters) was never told to start its jobs again: its watchers stayed down until a person sent the note by hand, and every later try would have failed the same way. Now the "start your jobs again" note carries the list only when the whole note fits; otherwise it names the file the list is kept in (`restart/ok/<session>.done-<time>`) and the session reads it. A list is never cut short any more (0.3.1 cut it at 600 characters, possibly mid-job). After a typing mismatch the next try names the file instead of carrying the list. The typed background-jobs ask is shorter (about 530 characters, was 690). `type_line` refuses to type anything over the limit and logs it.
- Hooks unchanged: same hook generation, no session needs a reload for this.

Not proven live yet: a note that names the file, typed into a real session.

## 0.3.1 (2026-11-12)
From a read of one machine's whole fleet 37 hours after install (95 sessions, 163 automatic compactions). Where the hook was loaded, 65 of 81 compactions landed at a break; the misses were mostly forced cuts in turns that run for hours.

- **Checkpoint break** (PreCompact hook). Once a hold has used half of its room (from the first hold up to the ceiling), a `git commit` or `git push` that just finished without failing counts as a break, if no other tool call is still running. Every forced cut in the read was a turn that ran on for 0.5 to 2.5 hours and 300K+ tokens after it, so more room alone would not have saved it; 6 of the 8 had a commit or push inside the hold. Replayed against that machine's real holds: 6 of 8 forced cuts become a cut at a commit, 37 of the 40 holds that reached a real break are unchanged, and 3 are cut a little earlier. The summary is told the turn is still in progress. The command is matched, never logged; the log line reads `checkpoint:git-commit` or `checkpoint:git-push`. `"checkpoint_break": false` in `~/.claude/pit-stop/config.json` switches it off. Same hook generation: a session running the 0.2.x or 0.3.0 hook still works properly and gets the rule at its next start.
- **Fleet-wide weekly tune** (fleet mode). Weekly tuning used to look at a folder only when a session started there, so a session that runs for weeks was never tuned again; on that machine no large session had a point of its own. With weekly tuning on, the helper now starts at most one tune per run for the folder tuned longest ago, among the running sessions it may touch: in the background, lowest priority, not while the last one still runs, and not while the machine's 1-minute load is above one per processor. `"fleet_tune": false` in `restart.json` switches it off. As before, a tune moves each point one step at most; the ceiling works at once, the two points from the session's next start.
- **A raised window shows its cost.** `est_read_saving` is now negative when the recommended window is higher than the current one, and the reasons and `/pit-stop setup` say "about N% more cache reads". Before, a raise showed 0.

## 0.3.0 (2026-11-11)
Three things the first fleet read asked for. Usage, because the account the fleet runs on hit its limit that day; a list for the person, because stuck sessions were silent; and the quiet-session pit stop, because cold wake-ups were re-paying for whole contexts.

- **Usage-aware tuning, with one dial.** The optimizer now measures how many tokens a folder's busiest session reads from the cache each day, and a `"budget"` in `config.json` (or `/pit-stop setup --budget`, or `optimize.py --budget`) says how hard to hold that down: `roomy` (memory only, as before), `balanced` (default: heavy folders capped at 500K, very heavy at 400K, neither raised) or `economy` (every folder at most 400K, heavy ones 300K, none raised). Sessions that re-read a lot after compacting get one step more. It prints a rough estimate of the reads saved. 0.2.x would have raised the window of the fleet's heaviest session (616M tokens read a day) from 500K to 600K; `balanced` now takes it to 400K.
- **`/pit-stop status`, the short list of what needs a person** (`scripts/needs_you.py`): sessions with a question on screen for 2 hours or more, sessions behind the newest Claude Code for a day or more with the reason, and sessions that never loaded the hooks. In fleet mode the helper keeps it in `~/.claude/pit-stop/needs-you.md` about once an hour and tells `notify_command` when the names on it change (`"needs_you": false` switches that off). Names, versions and durations only.
- **The quiet-session pit stop reaches more sessions** (`idle_compact_after`, still off by default): it now also compacts quiet sessions that have background jobs or are behind the newest version, and uses Claude Code's 200K default where no idle point is tuned.
- New optimizer output: model calls a day, cache reads a day, the busiest session's reads a day. A model call that spans several transcript rows is counted once. A rate needs at least a day of history.

An independent review read this release before it was pushed and found defects, all fixed here with tests: the quiet-session pit stop judged "cache still warm" from the newest dated row rather than the last model call (it could have compacted a cold session); the budget's cap could exceed the range's top and said "stays" while raising; `economy` skipped folders with no usage history; the needs-you list measured "behind for a day" from the newest release's install, so the list emptied after every update, and it announced lists that only got shorter.

Not proven on a live fleet yet: the budget's estimate against real usage after a week; the quiet-session pit stop typing `/compact` into a session with background jobs.

## 0.2.2 (2026-11-11)
**Sessions that never loaded the hooks.** A session loads a plugin's hooks when its process starts. On the machine 0.2.1 was measured on, 28 of 95 sessions had started before Pit Stop was installed and so had no hook at all (one compacted mid-task 16 times in 31 hours), and a session already on the newest Claude Code is never restarted, so it would never get one. Running sessions also keep the hooks of the version they loaded, through every later plugin update.

- New fleet-mode rule, **off by default** (`"reload_plugins": true` in `restart.json` switches it on): the helper types `/reload-plugins` into a session whose Pit Stop hooks are **missing or wrong**, not merely older. That means one of three things: the session started before the plugin was installed and has no hook; or the version folder its hooks run from has been removed (they would fail silently); or the plugin's **hook generation** is not the installed one. The generation is one number in `hooks/generation.json` that a release raises only when a running session must reload to work properly; a test fails when the hook files change without that file being looked at. 0.1.x counts as generation 1, 0.2.x is generation 2. So a release that leaves the hooks alone makes no session due.
- Which hooks a session runs is taken from the stamp the plugin's own Stop hook leaves for it (a time and the folder it ran from), or worked out from when the session started or was last reloaded and which version folder was in the plugin cache then.
- `/reload-plugins` is a local command, so no turn starts. It is typed only at a break (at the prompt, input box empty, no dialog), and only when it is cheap: right after a compaction, on a context under `ctx_max`, or when the cache has gone cold. A reload can make the next turn write the context to the prompt cache again, so a big session with a warm cache waits; `"reload_warm": true` lifts that.
- `"reload_only_names": [...]` limits it to those sessions, for a staged roll-out.
- Confirmed by the `Reloaded:` line on screen; one per run (`max_per_run`); a try is not repeated for `reload_retry` (6 h). A session restarted in the same run is not also reloaded (the restart loads the plugin).
- **Two clocks, from an independent review of this branch.** 0.2.1 counts a session's quiet time from the newest dated transcript row. That is right for "is it at a break", but rows such as `/restart`, `/reload-plugins`, `/model` and the away summary are dated without being model calls, so quiet time is not the age of the prompt cache. The helper now also keeps the age of the last model call, and uses it wherever the question is "is the cache warm or cold": before typing `/compact`, for the hour-idle restart, and for the reload rule. On 0.2.1, keep `"compact_before_restart": false`.
- Also from that review: a session the restart path wants in a run (even one waiting for the run's one restart slot) is not reloaded instead; `/reload-plugins` is confirmed only by the answer that follows the command just typed, not by an old `Reloaded:` line still on screen; `--not-before=TIME` is read, and a run with an option the helper does not know does nothing; a number in `restart.json` that is not a number (`"idle_compact_after": true`) goes back to its default and the run says so; a reload that cannot be typed because the input box is in use no longer writes to `events.log` every run.
- Proven on one real session (Claude Code 2.1.322, started before the install): `/reload-plugins` answered "Reloaded: 1 plugin · 1 skill · 7 agents · 1 hook" and `/hooks` then listed the plugin's PreCompact hook. Not yet measured: what a reload costs a big session with a warm cache on its next turn.

## 0.2.1 (2026-11-11)
From a read of the first 31 hours of 0.1.4 on a 95-session machine (its hook log, its restart log, and 7 days of session history), and from the reviewer's notes on 0.2.0.

Fixed:
- **The hour-idle rule never applied.** The restart helper took a session's idle time from its transcript file's modification time. On quiet sessions that time was seen moving about once an hour with nothing appended, so the clock went back to zero before it reached `cold_idle` (3600): in 31 hours no session ever got there, and one sat quiet for 7 hours on an old version with 374K of context. Idle is now counted from the newest transcript entry that carries a time. The same clock decides whether the cache is still warm before `/compact` is typed, so on 0.2.0 keep `"compact_before_restart": false`.
- **The Stop hook checks the ask before it prints it.** The ask's text goes to Claude as an instruction. Its fields must now be what the helper writes: two plain version numbers, and a re-arm list path directly inside `~/.claude/pit-stop/restart/ok/`. Anything else is put aside as `<file>.refused`, logged, and not passed on.
- **Compactions swapped in instantly are counted as what they are.** Claude Code can prepare the summary ahead of time (it asks the PreCompact hook then, at about 82% of the window) and swap it in later in milliseconds, in the middle of a tool call, keeping the newest messages word for word, with no second hook call. The optimizer counted these as mid-task compactions; they are now their own count ("swapped in instantly"). When this is on, Pit Stop's hold picks where the summary ends; the swap itself is not held and does not need to be.

New:
- `restart_at_break.py --not-before 2026-11-11T20:45:00Z`: a run does nothing before that time (for a booked test window). A time it cannot read also means nothing is done.
- `~/.claude/pit-stop/restart/events.log`: only what was done, typed, refused or asked. `restart.log` gets a line per session every run and rotates within days on a big machine, taking the restarts with it.
- `.gitattributes`: LF line endings on checkout, so a Windows clone has the same bytes as everyone else's.
- README: an hour-idle restart types no note.

Tested: the hook smoke test on a real Windows machine, for 0.2.0 and 0.1.4 (see README, Tested). 56 tests.

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
