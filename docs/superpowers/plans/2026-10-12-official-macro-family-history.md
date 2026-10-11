# Official macro family history implementation plan

> **For agentic workers:** Use executing-plans inline; one final read-only reviewer. The user explicitly requested immediate autonomous execution while the prior server audit continues.

**Goal:** Train the existing macro expert on verified official historical publications, preserving actual retrieval times and using no future event collection.
**Architecture:** Reuse the existing BLS/ISM historical bundle validator and canonical numeric feature names. A separate offline archive object supplies publication-time states; ordinary captured-receipt validation remains unchanged. Fit a finite set of individual macro features through the existing purged chronological selection and untouched final test, then expose supported forecasts through the existing runtime adapter.
**Tech Stack:** Existing Python/NumPy, official bundle transport, runner training and GitHub publication.
**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, sections 4–8; locked A/B/C/D plan and user request to start the next task without waiting for the previous audit.

## Global constraints

- Local baseline5010f1a/tree b045b07c equals accepted main ca9699b5/PR450. Previous exact-SHA server audit stays in its existing chain.
- Current actual result:24 trained/14 supported model groups, including4 option fits and1 supported XAG15m direction model. Macro has no fitted supported head yet.
- Use only existing official BLS/ISM archives and their existing parser/hash/calendar validation. No new providers, paid calls, broker-cost work or invented consensus/surprise.
- Archived public availability and present retrieval are separate clocks. Do not rename retrieval as an old receipt or make retrospective data a prospective observation.
- Preserve source age, configured target series, nonoverlapping labels, release-group purging, fixed horizons,40 training/10 validation minimums and existing working admission. No G.1/promotion/execution changes.
- Fit off-host only. Current runtime still consumes actual fresh macro facts and frozen coefficients; no request-time archive/network/training.
- A negative historical result stays negative. Do not guarantee a usable edge from implementation or fixtures.

## Review focus

- Tampered bundle, calendar, parsed values or source identity must reject before constructing training inputs.
- Releases published after a label origin cannot enter that origin; late retrieval remains explicit, never backdated.
- Missing/revised/ambiguous values cannot become zero or a fabricated complete vector.
- Shared release dependencies must remain excluded between fitting and scoring; feature selection must not use final test.
- Existing captured history, other families and runtime freshness checks must retain their behavior.

## Task1: validated archive timeline

Files: create `seiltanzer/edge_family_macro_archive.py`, `tests/test_edge_family_macro_archive.py`.
Interface: `verified_macro_archive(bundle, *, expected_sha, captured_ts, acceptance_run_id=None)` validates the complete existing official bundle and returns a bounded offline archive object. It exposes canonical numeric feature states at a past T0, with original publication/source identities and actual retrieval clocks.

- [x] RED: original publication gates visibility; future release excluded, tampered hash rejected, unsupported/missing numeric values absent, actual fetched_at retained.
- [x] Implement with existing canonical validators/parsers and fixed CPI/NFP/ISM feature shortlist.
- [x] Run owning tests, record actual results and commit.

## Task2: finite causal macro training and runtime contract

Files: modify `seiltanzer/edge_family_market.py`; tests in `tests/test_edge_family_macro_archive.py` and existing market tests.
Interface: `train_market_families(..., macro_archive=None)` accepts the validated offline object separately from captured JSON history. Evaluate the fixed individual canonical feature states and existing horizons using purged folds; the selected head records its actual feature/input digest and archive origin. Runtime matches the selected canonical feature to ordinary fresh facts.

- [x] RED: usable historical states fit actual finite coefficients; future append is invariant; release purging and final-test separation remain; ordinary late captured receipts still reject.
- [x] Implement bounded macro path without altering other-family fitting or freshness validation.
- [x] Run the owning profile, record results and commit.

## Task3: runner integration and release

Files: `scripts/run_mathematical_edge.py`, `scripts/export_official_macro_archive.py`, `seiltanzer/macro_historical_offhost_bundle.py`, `.github/workflows/mathematical-edge.yml`, roadmap and owning tests.
Interface: optional `--historical-macro-bundle` plus original source SHA feeds Task1/2; read the already-installed public archive over authenticated SSH, max16MB, no source refetch or server writes. No raw private history published.

Ruling: delivery TTL is separate from offline historical-copy validation. The existing validator retains strict fresh delivery by default; only the new offline archive factory explicitly disables delivery-age enforcement while preserving all parser/hash/calendar checks and original clocks. Historical origins cannot exceed the original archive window end; source feature age45days remains unchanged. Original source SHA stays distinct from the new model publication SHA. This avoids duplicate official acquisition inside the existing10-minute math runner budget.

- [x] RED: runner passes verified archive separately; missing/invalid official source is explicit and does not stop unaffected families.
- [x] Wire the existing bundle builder and offline trainer; retain trusted same-repository PR restriction and main-only publication.
- [ ] One final independent review, changed-area checks and final mandatory CI. Verify actual historical fit results and exact-SHA server delivery; prior-chain status does not hold up local implementation.

Final review returned three concrete defects, all reproduced: final-test class availability changed feature selection, optional SSH failure aborted training, and malformed archive structure escaped isolation. A separate pretest selection pass now freezes feature/horizon winners; absent final test does not select a runner-up. Original release groups purge against final-test origins independently of movement labels. Optional transport emits an unavailable-source packet; archive structural exceptions normalize to validation errors. Changed-area profile135passed9.41s. Mandatory CI and actual historical publication remain outstanding.
