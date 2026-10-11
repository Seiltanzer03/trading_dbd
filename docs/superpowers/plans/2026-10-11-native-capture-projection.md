# Native capture projection implementation plan

> **For agentic workers:** Use executing-plans inline; one final independent review.

**Goal:** Read more existing native history within the same private transport budget, without inventing observations or weakening admission.
**Architecture:** Verify both original immutable records first. Project only existing family roots, original option scalars/quality and producer markers before transport. Bind the projection hash to the original source hash; extraction retains original clocks and original source identity.
**Tech Stack:** Existing Python/SQLite/SSH runner and family trainer.
**Spec:** Original unified-edge spec §§4–8 and user request for actual historical training now.

## Global constraints

- Accepted baseline is local1e3956c/tree d4dabca6, identical deployed6efb898/PR448.
- Actual release read999 rows/537option observations, limited9instruments; option datasets39–72, no new option fit. These are measured transport limits, not exhaustive absence of historical data.
- Keep read-only indexed SQL, original/passive hash equality, original clocks, 1500rows/instrument,6MB projected/instrument,96MB private total and existing risk/training admission.
- No public raw records, new sources, waits for future captures, broker costs or runtime behavior changes.

## Review focus

- Original source corruption must fail before projection.
- Projected bytes, contract or original-source binding corruption must reject the record.
- Removing unrelated arrays must retain relevant source/proxy/quality/synthetic markers and produce identical extracted features.
- Same immutable option block supplies both IV and clock.
- Transport limits and source-read failures remain explicit.
- Projection metadata overflow falls back to the already-verified full original within the unchanged256KB consumer ceiling.
- Trusted same-repository PR reads actual native/private history before merge; fork PR is excluded by the existing job guard. Cache writes and production publication remain main-only; raw records never enter artifacts.

## Task: projected immutable capture transport

Files: new seiltanzer/native_family_capture_projection.py; modify scripts/export_native_family_history.py and seiltanzer/edge_family_native_history.py; tests/test_native_capture_projection.py; workflow paths and roadmap.
Interfaces: project_capture(raw, source_sha256) returns a private projected record or None. native_history accepts only hash-verified known projections or existing original records. Standalone remote program embeds the same stdlib projection/guard source used by tests.

- [x] RED: 260real-shaped immutable rows with unrelated40KB payload all survive6MB bound; original and projected extraction are identical; source/projection tampering and synthetic flags reject.
- [x] Implement projection after original/passive verification, count projected bytes and retain existing limits.
- [x] Owning tests, one independent review, final mandatory CI; authorized exact-SHA release and actual new fit counts. Do not claim models from fixture results.

## Actual read-budget correction before release

PR449 first CI3718passed/5skipped and WebKit green; actual math38161735266 exposed NATIVE_READ_FAILED0 after the unchanged25s SQL deadline. No expanded native history reached training. Do not merge this first revision. Existing sequential per-instrument traversal erased every previously verified row on interruption. Use32-row indexed fair passes,24s early stop inside the unchanged25s SQL ceiling, retain verified rows only for known deadline/SQLITE_INTERRUPT, and expose incomplete coverage/examined counters. Other read errors stay fail-closed. Two actual SQLite deadline/interrupt regressions reproduce the former loss RED→GREEN; owning242passed3.78s. Final changed-tree actual fit and mandatory CI remain required.

Accepted PR449/main0ab91592: finalCI3720passed/5skipped, deploy38162883193 delivery/readiness18:30:11/functional18:32:23/public18:32:25 allsuccessful. Isolated12actions/7extended,0orders/paidcalls/realpositionchanges. Math38164243133/source38164244753/FOMC38164246313 exactSHA published. Actual publication native1599records/901option, combined2525observations; four instruments explicitly incomplete under unchanged budget. Artifact11711367682 inspected20genuine fitted models/15supported,180finite coefficients with fit/test clocks and counters verified. No new macro/option models or all-eight readiness claimed. This package is closed; do not repeat unchanged search/CI.
