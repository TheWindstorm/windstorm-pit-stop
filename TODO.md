# TODO

## Now
- [ ] 2026-11-10 Prove 0.2.0 live on one real session (book it with Test Boss): `restart_at_break.py --live --only NAME` on a big, warm, idle session; expect `/compact`, then `/restart` on the next run, same session id, nothing typed after.
- [ ] 2026-11-10 Check what the keep-list does to real summaries: compare 5 compactions with and without it (does the next turn re-read less? is the next step stated?).
- [ ] 2026-11-10 Run `/pit-stop setup --fleet --apply` on a clean macOS account and record that the launchd timer loads and runs.
- [ ] 2026-11-10 The window could follow each session, not each folder: `/autocompact <tokens>` changes a running session, but check where it saves before fleet mode types it (if it saves for the whole user, it must not be typed per session).
- [ ] 2026-11-10 Sub-task breaks inside one long turn (a commit, a finished to-do list, a passing test run) so the hook can let a compaction through earlier than the ceiling. 29% of one fleet's compactions were mid-task.

## Done
- [x] 2026-11-08 -> 2026-11-10 (0.2.0) Big contexts that never compact and never idle an hour: compact first while the cache is warm, then restart; background jobs asked through the Stop hook, whatever the context size.
