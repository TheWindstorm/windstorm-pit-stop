# Trigger evals for the pit-stop skill

Do the words in `skills/pit-stop/SKILL.md`'s description make Claude load the skill when it should,
and only then? Codons G12.4 to G12.6 (plan 1.1).

- `cases/g12-4-should-fire/`: 10 queries that must load the skill, some naming Pit Stop and some only describing its jobs.
- `cases/g12-5-near-miss/`: 10 queries that share its words (pit stop, update, compact, CLAUDE.md, hook, model, memory, schedule) but need something else.
- `tools/trigger_trials.py`: the runner. It is modeled on skill-creator's `run_eval.py`.

Run it from a scratch folder, one case at a time:

    python3 -I evals/tools/trigger_trials.py evals/cases/g12-4-should-fire skills/pit-stop SCRATCH 3 claude-sonnet-5-5

Each trial offers the description as a project command with a unique name, in a scratch folder of its own. It then runs `claude -p` with the query and reads the saved stream.

A query fires when the command is read in at least half its runs.

The installed Pit Stop plugin is switched off for the run, so only the description under test competes. Bash, Edit, Write and NotebookEdit are off too, so a trial can read but change nothing.

Results go to `cases/<case>/results/<date>-<model>.json`: the summary, each query's count and every trial. Transcripts stay in the scratch folder.

The eval runs `claude -p`, so it costs model usage and is run by hand. It never runs in CI, and it is not part of Pit Stop: no script or hook calls it (INV6).

## Results, 2026-11-20, Sonnet (claude-sonnet-5-5), Claude Code 2.1.329, description of 0.6.0 at 543108fc

- Should fire: 10 of 10 queries fired, 30 of 30 runs, every one with Skill as the first tool call. The file was made under run 1's rule; re-scored under the fixed rule, the counts are the same.
- Near miss: 10 of 10 stayed out, 0 of 30 runs (re-run 03:05:14Z to 03:11:09Z under the fixed rule).

  Run 1 (old rule) scored 9 of 10: "Write a CLAUDE.md" counted 3 of 3. It was a harness artifact, not a load: the agent explored the empty folder and read the stand-in file. Its file is kept as `results/2026-11-20-claude-sonnet-5-5-run1-old-rule.json`.
- In all 90 trials, the installed plugin was visible 0 times, the stand-in was visible every time, and 0 trials timed out. In run 1's 60 trials, Bash was tried 26 times and refused every time.
