# Native owner metadata polling correction

> Continue with superpowers:executing-plans. The preceding publication package remains unchanged.

Goal: external polling survives atomic archival of a terminal result between lstat/open.
Spec: docs/TERMINAL_REMAINING_WORK_2026-10-20.md, same frozen epoch and finite native authority.
Files: scripts/production_history_cutover.py; tests/test_production_history_cutover.py.

- [x] Actual rename between stat/open reproduces FileNotFoundError from run39582128571.
- [x] Two RED tests: archived result should mean pending; unreadable state should fail closed with stable public reason.
- [x] Treat ENOENT from either stat/open as pending; retain private-mode/owner/size checks, canonical JSON and all other failures.
- [x] Same-epoch driver/owner/control profile: 63passed/1skipped; no clock renewal, no native changes.
- [ ] Independent final review and one mandatory exact-tree CI.
- [ ] Resume39582128571 with existing native expected bd615fbd only if its durable state permits; never recapture accepted transfer.

Review focus: wrong epoch/noncanonical JSON/private permissions remain errors; disappearance is not positive completion or handoff; resume retains original native monotonic deadline. Failed ordinary deploy while native holds EX lock is not permission to bypass it.
