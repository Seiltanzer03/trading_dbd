# P1: repeated prospective capture materialization

Baseline `b2050f15483b7a2dacc123ef7356f14cbbbc49ca` (PR #408). Re-reading
the already accepted capture unnecessarily entered IN_PROGRESS and waited on
the shared native SQLite lock held by research. A real-store threaded regression
failed on that baseline. The worker now fully revalidates an unchanged accepted
capture without native DB access and preserves its first materialization clock.
Changed captures still take the normal immutable ingestion path; exact-SHA,
body/digest/predecessor checks and 3600s TTL remain mandatory. Invalid/missing
captures still refuse admission. Profile: 27 passed. Independent read-only review
found no blockers. Official final-head CI and production acceptance pending.
Independent off-host scheduler/cadence receipts remain the explicit P1 blocker;
this fix does not establish continuous ten-minute acquisition.
