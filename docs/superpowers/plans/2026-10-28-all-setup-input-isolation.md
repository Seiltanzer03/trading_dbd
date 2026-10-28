# All-setup input isolation implementation plan

> **For agentic workers:** Use superpowers:executing-plans. One implementer and
> one final whole-branch reviewer; existing user authorization covers execution
> and exact-tree green-CI release without another planning approval.

**Goal:** Keep all 10 configured noncrypto instruments and 16 setups on their
own coherent inputs through switching, refresh, snapshot and AI review.

**Architecture:** Run each instrument-specific slow refresh against a private
copy with its captured instrument and generation. Publish only to that same
generation under the existing short market lock; network stays outside the
lock. Freeze option inputs together with price/reference inputs. Existing
models, risk/source gates, costs, providers and execution authority remain.

**Tech Stack:** Existing Python/SQLite/pytest and GitHub CI/deploy.

**Spec:** `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md` §§1–5,7–12;
`docs/LOCKED_GLOBAL_PLAN_2026-10-07.md`; user 2026-10-28 all instrument/setup scope.

## Global constraints

- All configured setup instruments are covered; crypto expansion stays paused.
- No changing real active trades, broker orders/ACKs, source timestamps, gates,
  confidence or model validation to manufacture readiness or a9/10 rating.
- Existing caches may retain correctly named source observations. No additional
  fetching, fitting, servers, sources or full DB transfers for diagnostics.
- Native execution, one final review, mandatory final-tree CI and exact release.

## Evidence and decisions

- Production inventory40746286619:10 instruments/16 setups;58/80 fitted noncrypto
  families. Event and flow unavailable for all10; JPY100 also option/positioning.
- Current loaded global76 includes existing crypto; fitted does not mean a
  live supported preference. Preserve every missing-input reason.
- Baseline isolated demo diagnostic: all16 full snapshots built in32.9s. This
  proves code paths only, not observed source coverage or profitability.
- Root cause: adaptive/base/resilience refresh methods mutate the shared feed
  after unbounded network waits, while set_instrument clears that same object.
  Daily cache keys can even change between fetch and cache write.
- Root cause: capture_market_inputs reads chain after releasing the bar lock
  and after reading optional Treasury context, allowing another instrument's
  chain to enter an otherwise frozen original-instrument snapshot.
- Ruling: use one publication boundary around existing final refresh methods,
  rather than editing every provider branch. It preserves adapters but requires
  reapplying wrappers after the existing resilience installer replaces methods.
- Ruling: source-specific model history and quality remain evidence limits;
  research assumptions cannot become measured model samples or broker prices.

## Review focus

- Switch A→B→A while a provider is blocked: old completion never overwrites A.
- Two same-channel refreshes complete in reverse order: retain newer request.
- A fresh stream proxy arrives during chain fetch: chain cannot downgrade it.
- Cache fallback and network failure after a switch cannot corrupt new state.
- Optional Treasury work switches instrument: frozen chain remains original.

### Task1: coherent instrument refresh and frozen inputs

**Files:** Create `seiltanzer/data/instrument_refresh.py`; modify
`seiltanzer/data/__init__.py`, `seiltanzer/option_feed_resilience.py`,
`seiltanzer/ai_frozen_expert_inputs.py`; test
`tests/test_instrument_refresh_isolation.py`,
`tests/test_ai_treasury_input_freeze.py`.

**Interfaces:** `install_instrument_refresh_isolation(market_class)` wraps
proxy, intraday, daily, chain and IV refreshes idempotently. Existing signatures
stay unchanged, no network under market lock. Private refresh view prevents
nested wrappers and preserves original provider/cache identity. Per-channel
request generations reject older completion; instrument revision rejects ABA.

- [x] Add deterministic provider-blocked switch and same-channel race tests,
  fresh-proxy precedence and Treasury-switch freeze regressions; observe RED.
- [x] Implement private view and atomic generation-checked publication, install
  after adaptive and reapply after resilience. Capture chain inside original
  lock before frozen_inputs_captured_ts; optional bad chain stays isolated.
- [x] Run owning source/freeze/option tests; compare actual failures and PASS.
- [x] Commit implementation and regressions.

### Task2: every configured setup through the real decision builder

**Files:** Create `tests/test_ai_all_setups.py`; modify
`scripts/production_model_consumption.py`,
`tests/test_production_model_consumption.py`, `seiltanzer/ai_verdict_base.py`,
`seiltanzer/ai_verdict_v18.py`.

Actual all-setup test found four missing required-filter contexts (setups5,6,7,11):
all evidence compaction tiers can omit filter observations. Preserve a small
source-bound required-filter projection under strategy, which already survives
all tiers and reaches the independent provider. Preserve manual confirmation
and existing decision_weight=false; never treat delayed15m chips as a complete
multi-timeframe pattern. Report displays this same projection.

**Interfaces:** `verify_setup_coverage(report, expected_sha, captured_ts)` uses
actual configured16→10 mappings, validates each trained model with existing
validator, keeps eight per-family statuses/reasons and separates fit/support
from current inference. Add this readonly receipt to existing model acceptance.

- [x] Write failing coverage test: no missing instrument/family may be silently
  accepted; trained counts exclude crypto, source mismatch is rejected.
- [x] Implement bounded16→10 coverage receipt without live input/PNL claims.
- [x] Add full real Engine temporary-DB cases for all16 configured setups,
  both FX directions, exact geometry,12coverage groups, strategy filters and
  fallback report. No provider calls/real positions changed.
- [ ] Run owning tests, one final review and native mandatory full CI; merge
  authorized exact head and verify production release plus all-setup receipt.
- [ ] Record actual remaining data/operational limits, never a fabricated rating.

## Verification before final review

- Refresh/freeze defects:7RED/2PASS baseline;44PASS after fix. Expanded installed
  resilience cases cover the final nested adapter path as well.
- All-setup coverage:5RED before verifier; independent full setup builder found
 4RED required filter contexts, then report projection was corrected at the
  actualv18 rendering owner rather than the unused legacy renderer.
- Final owning suite155PASS52.37s includes all16 real decision builders,
  complete model coverage, provider packet preservation, feed races, quote worker
  isolation and snapshot budget. Local nativeyfinance import is shimmed only
  in the test command for the existing Python3.12 curl crash; mandatory native
  Python3.11/full WebKit CI remains required.

## Final review and one fix pass

- One independent final reviewer:0Critical,2Important,0Minor; independent30PASS.
- Important cache bypass:5-case review RED run reproduced shared-cache overwrite,
  rejected chain cache writes, ABA cache writes and mismatched filter timestamp.
  Stage adapter cache writes with their original receipt time, accept under a
  separate per-channel publication lock, and recheck generation before live
  publication. Cache SQLite work never holds the live market lock.
- Important filter clock: copy value/source/timeframe/ts into the original chip
  from one feed receipt. Strategy never reads the mutable global volatility map
  to attach a newer timestamp to an older value.
- Both fixes7PASS0.82s; final whole owning suite156PASS55.65s, diff check clean.
- Final ruling: nativePython3.11/WebKit and server acceptance are owned by the
  mandatory CI/release chain; local fixtures do not establish those results.
- Final ruling: actual feed availability/profitability remains evidence-bound;
  all80 cells retain explicit statuses instead of inventing observations.
- Final ruling: paused crypto and unrelated broker/quote conventions remain
  outside this change; original feed ts is preserved without claiming a broker
  clock or automatic confirmation of multi-timeframe setup structure.
- No deferred minor findings. No re-review or repeated full local suite needed;
  full exact-tree mandatory CI and exact-SHA production receipts remain.
