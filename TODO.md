# TODO

## Now
- [ ] 2026-11-08 Restart at the break: add a second step for big contexts. Today a session at or over `ctx_max` (default 100K) is never moved to the newest Claude Code, so a busy long-running session can sit on an old version for days. Keep the rule: no unattended restart above `ctx_max`. Add: when an update is waiting and a session is over `ctx_max`, type ONE labeled note asking it to reach a safe break, write its re-arm list, stop its own background jobs, and reply `READY FOR RESTART`; restart only on that reply (or a file it writes), then send the re-arm note. Ask again at most every 12 h. Tests: over-limit session gets one note, no restart without the reply, restart after it.

## Done
