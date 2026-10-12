# Fitted family continuity implementation plan

> **For agentic workers:** Execute inline, one root writer and one final read-only reviewer. Previous historyPR452 is merged; exact-SHA deployment continues independently.

**Goal:** Keep an existing genuine compatible fitted family model when a bounded new history read cannot refit it, preserving its actual original evidence.
**Architecture:** A bounded off-host model cache and read-only existing publication seed supply previous frozen models. Restore only absent/insufficient training results after the existing complete model validator passes; retain original coefficients, model hash, fit/test/trained clocks and admission. A newly trained result, including unsupported/negative OOS, always wins. Runtime still requires current fresh features and source identity.
**Tech Stack:** Existing Python model validator, authenticated read-only export, trusted runner cache and mathematical-edge workflow.
**Spec:** docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md §§4–8, locked global plan, user's autonomous historical-now functional request.

## Global constraints

- Base local23c74b6/tree39c73598 equals acceptedmain c302cb9f/PR452. No changes to that released tree. No fake training, new providers, paid calls, broker-cost work or real orders.
- Actual chronological proof: accepted ca969 math publication has4 fitted option models, including supportedXAG; later db201 publication omits all4 after shorter bounded native export. Those original models still pass _model_reason at the later cutoff. This is input insufficiency, not a new negative fit. Later PR452 actual fit24/15 is separate evidence and cannot be substituted into an earlier decision.
- Model input/cache bounded8MB, configured13×8 family matrix only; existing model contracts, hashes, coefficient/feature dimensions, source mapping, fit/test clocks, working admission and expiry unchanged.
- Preserve original TRAINED times and OOS; no refit/backdate/new-observation claim. Current fresh source/feature checks continue to govern availability and weights.
- Trusted same-repository PRs may restore/read/test only; only main can save cache/publish. No raw private observations added to model cache or public source files.

## Review focus

- A current trained unsupported/negative model cannot be replaced by an older positive one.
- Wrong target/provider/feature contract, corrupt hash, future/stale/synthetic model must reject using existing validator.
- Restore provenance cannot alter any model byte/hash or original clock; current-run fit and restored-fit counters stay separate.
- Missing/failed optional prior-model read/cache must preserve unaffected fresh training; bounds and main-only writes unchanged.
- Multiple prior candidates use newest eligible original trained_at, never held-out score or desired action. Runtime fresh facts remain mandatory.

## Task1: bounded restoration contract

Files: new seiltanzer/edge_family_model_retention.py; new tests/test_edge_family_model_retention.py.
Interface: retain_fitted_families(current_models, previous_reports, captured_ts) -> (model_matrix,audit). Only known input/history insufficiency without a current fitted head permits retention. SOURCE_UNAVAILABLE identity/hash failures do not become bypasses. Audit identifies original model/hash/clocks/reason; models unchanged.
- [x] RED: actual fitted learner fixture survives insufficient new dataset; trainednegative wins; corruption/target/future/stale/synthetic reject; chronological selection and byte equality.
- [x] Implement using _model_reason and fixed configured matrix/bounds. No current decision or fitting code changes.
- [x] Verify owning profile, record result and commit.

## Task2: off-host read/cache and report integration

Files: scripts/export_mathematical_edge_sources.py; scripts/run_mathematical_edge.py; .github/workflows/mathematical-edge.yml; owning tests.
Interface: read-only exporter optionally includes only bounded existing fitted-model publication fields; runner optionally restores hash-verified compatible cached matrix. Freshly trained report wins; summaries distinguish current_run_fitted from retained_fitted and total_fitted. Keep original model objects/hash.
- [x] RED: incomplete native inputs retain a usable oldfit without inventing input rows; malformed/missing optional model source leaves fresh report; no current trainednegative substitution.
- [x] Wire trusted restore/main-only save and publish; cache keyed to actual model/feature/config compatibility, not desired performance.
- [ ] Verify owning profile and real trusted-PR fit/cache counts; record exact originals and commit.

## Task3: bounded review and release

- [ ] One independent final reviewer, one reproduced fix pass if needed; final mandatory Python/SQLite/frontend/WebKit CI.
- [ ] Actual trusted-PR restored-model check, merge after green checks; exact-SHA deploy and actual publication with original clock/hash preservation.
- [ ] Update roadmap and stop this package. Missing CPI/NFP and untrained event/flow/position/carry families are not filled by cache retention.

Task1 local checkpoint:10retention contracts observed RED for missing module, then58owning tests passed7.34s with existing market-model profile. Original objects/hash/clock unchanged; current negative fit wins; invalid/future/stale/synthetic/wrongtarget reject. Task2 integration/cache is not implemented yet; no production retention claim.

Task2 local checkpoint:3runner/cache API failures and2missing publication-seed failures observed RED, then integrated. Owning229passed27.60s before two narrow optional-read/write isolation adjustments; final changed profile required. Actual offline replay of existing finalPR452 price bars with native history deliberately absent and genuine earlier accepted ca969 option fits:20currentfits+4retained=24total/15supported; all4model objects/hashes/fit clocks exactly unchanged, no network/production writes. Trusted-PR/production receipts remain outstanding for this new block.
