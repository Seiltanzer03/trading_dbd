# Bounded capture and production request trace review evidence

Underlying main4e0a5f58694bc657d7d5cb485909a6492ba17e43/tree94e79bfa5002031d825d2fdaef22eb46ff8c0b8e
passed mandatory official CI, delivery/readiness/isolated12-action tests but
deploy37269012264 timed out on actual AI HTTP POST. Public/source/math acceptance
was skipped. This incident's cause was not established from its saved log.

## Implementation and independent review

Productae3ce68 fixes independently reproduced optional-capture boundedness defects:
fixed0.25s queue-inclusive budget, private input/timely result commit, one surviving
worker per engine, store/feed nonblocking admission and bar bound before copying.
Safe warning diagnostics correlate actual request stages and thread queue/execution.
Risk/cost/geometry/proofs/OOS/provider6s default8s max/smoke12s transport14s unchanged.

Fresh task reviewer approved spec and quality without findings. Fresh Astra
whole-feature review found ImportantI1: outer production runtime guard probes
journal before the materializer trace exists. Actual event-controlled reproduction
had trace_events0/requestpendingtrue, then HTTP400no_active_trade/trace_events0.
Fix6f1b55b initializes trace before that probe, shares ID inward and assigns one
final actual status to its outer owner. Fresh scoped reviewer approved I1 addressed,
no open findings or new breakage. Other product scope passed the whole-feature review.

Capture actualRED8failed82passed→GREEN90passed; traceRED5failed8passed→112passed;
projectionRED4failed→99passed. Final original focused22-file781passed1knownwarning69.63s.
Final I1RED5failed1passed→GREEN6passed; covering95passed1knownwarning53.34s.
Tracked task-1-report.md and final-fix-report.md preserve exact commands/evidence
in this plan's `.superpowers/sdd/2026-10-05-ai-request-latency/` workspace.

## Root verification and release gates

Root broad on306af74 (report-only after reviewed6f1b55b):2870passed4skipped,
one known unchanged Python3.12/SQLite baseline failure
`test_quiescent_sparse_clone_replays_wal_only_into_backup`,151.75s; one existing
Starlette/httpx warning. Storage guard/test unchanged. This is not local all-green.
42frontend syntax/smoke commands passed exit0. Official exact-tree Python3.11/full
real WebKit and mandatory companion workflows must pass before authorized merge.
ExactSHA deployment/readiness/functional/public/source/math acceptance remains pending.

## Rulings and residuals

- Preserve existing user continuous execution/agent/worktree/document-pause authority;
  no repeated stage consent, and no live-order/paid-call/data-fabrication authority.
- Local optional-capture and outer guard findings are distinct from actual production
  timeout causality. Actual next deployment trace/latency/log collection must decide it.
- Python cannot terminate a running worker: it stays private and holds its sole
  admission until completion. Later optional captures explicitly refuse; shared SQLite
  busy timeout/connection are not interrupted or globally changed.
- Pre-existing nonreaction worker lifecycle/deadline redesign remains outside this
  narrow change. It is traced; a concrete production stall can motivate a later fix.
- No observations, broker authority, learned models, efficacy or profitability are
  inferred from these fixtures or CI. Previous genuine-data/model residuals persist.
- Preserve all worktrees/evidence and original user uv.lock. No cleanup or baseline
  storage-test suppression. User-authorized merge still requires full official green.
