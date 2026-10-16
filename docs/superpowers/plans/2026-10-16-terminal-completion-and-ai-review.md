# Terminal completion and reported AI review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> One implementer, one final read-only review per product package. The user
> explicitly requested autonomous plan execution; no additional approval stage.

**Goal:** Correct reproduced defects in the attached AI review, then continue
the remaining storage/EDE/family directions to explicit finite outcomes.

**Architecture:** Retain the existing unified selector, scenario bank, snapshot
refresh and archive machinery. Separate the bounded AI correction from the
architectural storage migration; do not change admission merely to fill blanks.

**Tech Stack:** Existing Python/NumPy/SQLite, JavaScript, GitHub Actions and
private Yandex Object Storage (`trading-dbd-backups-2026`).

**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, original handoff,
`docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`; user report dated2026-10-16 06:40:16UTC,
decision66926fd5c761e5261ebc1b7a4488. Original attachments are read-only.

## Global Constraints

- Accepted baseline55fe3f784604ce8007e98e074a73ff33fc672466, tree6a4d672f.
- One manual decision, twelve existing actions, mandatory costs and hard risk.
- Missing input remains null/unavailable. No invented live quote, consensus,
  book, broker tariff, probability calibration, fill or historical profit.
- Original source clocks, identity/mapping, purging and statistical gates remain.
- Existing1GiB source reserve and resource-pressure safeguards remain.
- No repeated search/training without new usable input; no paid LLM ablations.
- Changed profile once, one final review/fix pass, one full CI on final tree;
  authorized merge -> automatic deploy -> exact-SHA acceptance/publications.
- Preserve unrelated worktrees and untracked work; add exact files only.

## Review Focus

- Missing quantitative score must abstain even when other experts rate a
  blocked action; all per-action weights stay in[0,1] and total0or1.
- Weighted outcomes, zero-mass paths, ties and costs must define the same
  scenario statistics; scenario frequencies never become validated physical P.
- A live quote refresh must not retain median/probabilities from old outcomes.
- Unsupported/stale source data must remain unavailable; report labels must
  distinguish current applicable forecasts from the number of fitted models.
- Independent action refusal and actual expert selection remain unchanged by
  added diagnostics; input snapshots and real positions remain unmodified.

## Package A — attached AI review (current implementation)

### Task A1: reproduce the reported contracts

**Files:** existing `tests/test_unified_edge_ensemble.py`,
`tests/test_unified_candidate_economics.py`,
`tests/test_ai_snapshot_market_refresh.py`, and report/renderer regressions.

- [x] Reproduce400% quantitative weight on unavailable MOVE_TO_BE/gamma/take
  economics using real `rank_candidates`/ensemble, not fabricated audit output.
- [x] Pin weighted median, profit/loss and0.25/0.50R giveback from the actual
  common-bank net outcomes, including costs, zero weights and exact ties.
- [x] Reproduce the refresh dropping these computable statistics; preserve
  input immutability, quote clocks and unavailable old execution-MC geometry.
- [x] Reproduce misleading unavailable-expert quality and trained-vs-applicable
  family wording with the real text and frontend renderers.

### Task A2: minimal owner corrections

**Files:** `seiltanzer/unified_edge_ensemble.py`,
`seiltanzer/unified_candidate_economics.py`,
`seiltanzer/ai_snapshot_market_refresh.py`, report/audit modules and
`seiltanzer/web/js/management_ui.js` only where regressions require it.

- [x] Set abstaining score weights to zero before per-action redistribution;
  retain selected policies and all original risk/confirmation exclusions.
- [x] Publish scalar distribution statistics from the same existing net
  vector/weights, under explicit comparison-model semantics; no new simulation.
- [x] Carry fresh statistics into refreshed base policies and report integrity;
  label their bank/basis without reviving old bridge/MC event probabilities.
- [x] Explain current forecast availability and missing quality precisely.
  Investigate mathematical/source rejection; change it only if valid existing
  inputs are demonstrably lost, never by loosening clocks or inventing bars.
- [x] Run changed ensemble/economics/refresh/report/API profile and renderer
  smoke. Record unsupported inputs separately from implementation defects.

### Task A3: finite release

- [x] One final independent read-only review of the whole package.
- [x] One Critical/Important correction pass: none required; no blocking findings.
- [ ] Canonical final-tree CI, authorized PR/merge, automatic deploy and
  exact-SHA readiness/API/public/model/source acceptance. No blind redeploy.

## Package B — sustainable Yandex history storage

**Existing owners:** `scripts/ede_ledger_archive.py`,
`scripts/restore_verified_snapshot.py`, `scripts/offhost_sqlite_snapshot.py`,
`seiltanzer/storage_runtime.py`, `seiltanzer/storage_refinement.py`, actual passive/G1S history readers and native exporters.

- [ ] Establish a written narrow hot/cold contract before migration code:
  immutable eligible rows/blobs, dependency closure, original bytes/clocks,
  content hashes/manifests and all current readers.
- [ ] Use existing verified off-host evidence to quantify reclaimable space;
  do not repeat whole19GB scans/restores without a concrete new question.
- [ ] Prove upload/full readback/restore and consumer compatibility on a
  disposable fixture/copy before any production local retirement.
- [ ] Implement only that proven contract. Active SQLite stays local; no S3
  filesystem mount, blanket deletion, unknown-row pruning or forced checkpoint.
- [ ] Verify sustained headroom and API/worker health. Existing active runner
  bin/externals2.337.0 are not cleanup candidates. Small log cleanup is not
  considered a sustainable solution.

## Package C — EDE/Active Edge continuation

- [x] Existing inventory38816180567 completed; ActiveEdge38853293377 completed
  all five structured horizons, ML and publications. Do not repeat them.
- [ ] After source reserve is actually restored, continue the failed exact-SHA
  EDE stage with current consistent input. Keep original source epochs and
  official delivery TTL; reuse only still-valid completed products.
- [ ] Record actual audit/publication or the exact refusal. Research maturity
  and working interpretation do not automatically promote scientific policies.

## Package D — explicit eight-family completion matrix

- [ ] Preserve the verified25 fitted/19 historically supported combinations
  from math38815534340; current applicability is a separate measurement.
- [ ] For each configured family/instrument record source -> historical sample
  -> fitted model -> current forecast -> actual contribution or exact blocker.
- [ ] Existing candle pressure/release-change/financing assumptions remain
  labelled preliminary inputs, separate from observed book/consensus/tariffs.
- [ ] Missing official release depth, prepublication consensus, mapped flow,
  positioning/carry inputs and empirical calibration close with explicit
  dependencies; no unchanged model search to manufacture positive results.
- [ ] Update the final comparison/remaining-work addendum from new receipts;
  reliable FOMC cadence and prospective effectiveness remain explicit.

## Progress

2026-10-16: plan recorded on request. Attachment read;400% excluded-candidate
weights traced to per-action redistribution retaining missing quant weight.
Refresh explicitly rebuilds Expected/CVaR but drops other computable common-bank
statistics. Missing authoritative MC geometry is intentional after quote refresh.
No production position/action or research gate has been changed.

2026-10-16: A1/A2 complete. Ten new regression cases observed RED then GREEN.
The changed Python profile passed311 tests; both unified-audit and management
renderer smoke tests passed; diff check clean. Weighted median uses the lower
inverse empirical CDF, ignoring zero-mass paths. No extra simulation, provider
call, source admission, execution-MC event probability or real trade mutation.
A3 remains pending one final review and canonical final-tree CI/release.

Final independent review: no Critical/Important/product Minor findings;143
additional independent checks and frontend audit smoke passed. Reviewed semantic
limits remain: empirical calibration/profitability, live input freshness and
production deployment require their own receipts. Corrected the obsolete storage
owner filename in this document; no product changes after review.
