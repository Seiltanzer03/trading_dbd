# Causal positioning and related-market history

## Intent, authority and scope

Continue §7 of the original unified-edge specification with actual historical
feature producers. Baseline is accepted main
`9733ca81e8f81925c85274566a87c07b8de1ec6f`, tree
`3fedfe84e12c03a48290a522d2175bb8bea13cf3`, local equivalent `9fd060b`.
User has explicitly authorized continued implementation with agents, green-CI
merge and exact-SHA deployment. The preserved execution record waives repeated
design/document approval pauses; strict review/release gates remain.

This is architectural: new optional source contracts connect collector, frozen
adapter features, dataset provenance and runtime admission. The selected approach
is deterministic bounded transforms of genuinely received source observations.
Alternatives are a new external historical warehouse first (requires unavailable
inputs/access), or new learned geometry conditioning (a different task).

Implement positioning changes and related-market lagged returns, relative return
differences and peer-basket breadth. Preserve existing legacy facts. FOMC text
change already exists; general event novelty/reaction, valuations/forwards,
constituent index breadth and learned lead-lag usefulness remain distinct gaps.
No missing observations, historical receipts, price equivalence, models or broker
fills are synthesized. No network calls, fitting or new dependencies on review.

## Shared interfaces

Create `seiltanzer/edge_family_history.py` with pure interfaces:

- `position_history_features(source: dict, cutoff: float) -> dict`
- `intermarket_history_features(source: dict, cutoff: float) -> dict`

Each returns `features` (numeric name/value dict), `feature_provenance` (same keys,
supporting evidence dict) and `rejections` (bounded explicit reason records).
Malformed inputs return no derived feature and a reason; they do not raise to
callers. Absence of every extension field preserves legacy facts. Any partial,
null or unknown explicit extension rejects that source before legacy fallback.
Only the collector and one shared integration owner change existing files.

## Positioning contract

Optional `position_history_contract = edge-family-position-history-v1`.
`position_series` declares nonempty bounded `series_id`, `kind`, `unit`,
`category`, `venue` and lowercase 64-hex `body_sha256`; its kind matches the
existing source kind (`cot_report`, `fund_flow`, `observed_open_interest`).
`position_change_history` contains at most 8 strictly previous observations.
The extension projection is bounded to 2048 UTF-8 JSON bytes and depth 8.

Each prior observation retains `report_ts`, `net_position`, `available_at` and
`provenance`: `source_id`, the same series/kind/unit/category/venue, body hash,
`received_ts`, `published_at`, and `source_verified`. Availability and receipt
agree; report <= publication <= receipt <= parent packet availability <= cutoff.
All numeric inputs must be finite, nonboolean; identity/hash/clock mismatches,
unverified/synthetic/context-only constituents, ambiguous duplicate report dates
and unknown versions fail closed. Identical duplicate observations may dedupe;
conflicting duplicates reject. A declared constituent horizon must agree with
the parent declaration; undeclared parent cannot erase child applicability.

Select the greatest report date strictly below the current report, without
crossing category/unit/venue/series. Emit
`positioning.<kind>.change` = current net minus previous net and
`positioning.<kind>.previous_report_age_days` = report gap / 86400. Do not use
percent change for signed COT net or infer a reversal sign. Derived availability
is the latest actual supporting receipt; provenance retains both source IDs,
hashes, clocks, series/unit/category and `window_seconds` = report gap.

Public COT parsing computes the actual fetched body hash, declares contracts /
noncommercial futures category and exact CFTC series identity, and retains one
latest predecessor in the new proof. Existing historical_positions and first-seen
receipt/publication upper bounds remain unchanged. CFTC-to-CFD mapping remains
unvalidated; no new feature bypasses the existing _meta mapping guard. External
fund-flow/OI require genuine matching series/unit evidence; no new collector is
claimed for those inputs. Oversized source selection retains its existing refusal.

## Related-market contract

Optional `intermarket_history_contract = edge-family-intermarket-history-v1`.
`historical_series` retains a fixed universe of actual Coinbase BTC-USD, ETH-USD,
SOL-USD observations already fetched by the existing collector. Only received
series appear; no new GETs or provider substitution. The extension projection
is bounded to 4096 UTF-8 JSON bytes, depth 8 and at most 3 series.

Each series declares `source_id`, `provider=COINBASE`, exact product symbol,
`base_currency`, `quote_currency=USD`, direct orientation, actual `body_sha256`,
`available_at` and exactly 7 consecutive completed one-minute close observations
ending at the source observed timestamp. Compact `bars` entries are
`[bar_end_ts, close, received_ts]`; each receipt equals the series's actual
collection receipt. End <= receipt <= parent receipt <= cutoff. Positive finite
closes, exact intervals, unique endpoints and identity are mandatory. Root and
constituent synthetic/context/horizon declarations remain binding. No fill-forward,
interpolation, inverse-FX guessing, USD/USDT relabeling or body-hash substitution.

Emit each valid related series's five-minute return at a one-minute lag:
`intermarket.<series>.return_5m_lag_1m` = log(P[end-60]/P[end-360]). This is an
observed lagged return, not a fitted predictive lag. Its feature window is 360s.
For fixed ordered pairs with both series present, emit
`intermarket.<left>.<right>.relative_return_5m` = their same-window log-return
difference; window 300s. No valuation or mean-reversion claim.

Emit `intermarket.related_crypto.breadth_up_fraction_5m`, `observed_n`,
`expected_n` and `coverage` for the predeclared related crypto peer basket, only
with at least 2 admissible peers. Exclude the target's own crypto asset; for
noncrypto targets all three are related peers. Missing peer is excluded, not zero.
Expected count reflects that exclusion. This is related crypto basket breadth,
never index constituent breadth. All constituent histories must share exact
window endpoints. Pair transforms and breadth preserve constituent IDs/hashes,
receipt clocks, quote/orientation/universe/exclusion identity and a shared
dependency lineage; no extra vote is created by correlated transforms.

The existing collector requests 30 minutes. Retain only the 7 required completed
closes at a common endpoint; if a received series lacks the exact consecutive
window, omit it and record a reason rather than reconstructing it. Existing linked
returns and their observation-age lag_seconds keep their existing names/meaning.
Existing RELATED_VENUE_RETURNS_NOT_TARGET_BROKER_PRICE and false price-equivalence
declaration persist. The review's target price/venue is never substituted.

## Integration, bounds and verification

The adapter validates extensions before emitting source facts, then uses _add
with original metadata plus independently computed derived provenance. Existing
freshness, source mappings, family dependency grouping, source/model applicability,
risk/cost and OOS guards remain. Dataset uses its frozen source copy; transforms
never read future archive path_points or enrich old frozen snapshots. Supporting
declarations and every receipt must remain binding at both bundle capture and T0.

Do not increase source bundle / review / archive bounds: 1,000,000 byte source
bundle and 8,000 selected source bytes remain. Add a real consumer test proving a
valid compact history reaches load_family_source_context, adapter and dataset
within those bounds; over-budget facts explicitly refuse. Availability diagnostics
name the missing history/proof. No trained model or efficacy claim follows from CI.

RED tests cover correct deltas/lagged returns/relative difference/breadth, original
packet immutability, both constituent provenances/window sizes, future receipts,
gaps, duplicates, malformed/partial/unknown extension, category/unit/venue mismatch,
unvalidated mapping, own-asset exclusion, missing peers, nonfinite/zero/boolean
prices, oversized/deep packets, child applicability, capture-vs-review cutoffs and
future path noninterference. Legacy behavior remains pinned. Full official CI and
real WebKit are required before release; then exact production readiness/smoke/
public/source/math publication. Statistical usefulness and real inputs stay open.

## Implemented contract details (2026-10-04)

`edge_family_history.py` bounds the compact UTF-8 JSON extension projection at
2,048 / 4,096 bytes and depth eight before calculating. Numeric history fields
must be finite JSON numbers, excluding booleans. A malformed explicit extension
rejects the entire source, including legacy fallback. Empty extensions remain
missing; absent extensions keep their legacy behavior.

Position identity uses `series_id`, `kind`, `unit`, `category`, `venue` and
`body_sha256`; the COT producer uses `CFTC<contract>`, `contracts`, CFTC and the
existing noncommercial long-minus-short futures category. It retains one latest
predecessor only when one was actually received, with first-seen publication and
receipt upper bounds and the actual response SHA. The original
`historical_positions` packet is preserved. Thus large original COT histories
still encounter the unchanged selected-source refusal; they are not silently
compacted to manufacture admission.

Related-series identity uses `symbol=BTC-USD|ETH-USD|SOL-USD`,
`provider=COINBASE`, exact base and USD quote, `orientation=direct`, response hash
and seven `[bar_end_ts, close, received_ts]` observations. The collector uses the
legacy common completed endpoint, omits a gapped proof with an explicit history
error and keeps the original linked returns. Feature names retain the exact
`COINBASE<product>` identity, including its hyphen. Fixed ordered relative-return
pairs are BTC/ETH, BTC/SOL and ETH/SOL in that order. One-minute lag and return
length are 60 / 300 seconds; lagged-return metadata declares a 360-second feature
window and pair/breadth metadata a 300-second return window. Peer-basket metadata
retains the fixed universe and excluded own asset (configured USD/USDT target
identity does not relabel any Coinbase observation).

Position history rejects context-only declarations on the source, current series
identity, prior observation or prior proof before any feature/legacy admission.
Related breadth excludes the canonical admitted review target. The adapter passes
that target via the optional `target_instrument` keyword without modifying the
source packet; two-argument pure calls use only a proxy target admitted by the
existing mapping guard, otherwise the original source instrument.

Derived metadata includes `constituent_provenance`, `supporting_source_ids` and
`applicability_provenance`, preserving root, identity and constituent declarations
without a child overwriting another child's scope. Legacy features emitted from
an extended source also retain binding constituent applicability. Adapter
metadata keeps the existing dependency group. Runtime validates constituent
proof against immutable bundle capture before selection and the adapter
recomputes at review. Dataset keeps its existing recursive source declaration
and frozen-snapshot guards; no dataset path enrichment or changes were needed.

The integration fixture uses the actual collector (12 existing GETs), unchanged
local consumer and original frozen dataset replay. Verification and exact byte
measurements are recorded in the task report. Release, statistical usefulness,
real input coverage and model calibration remain separate gates.

### Trainer handoff implementation

Normalized derived provenance now declares `history_contract_version` and a
`supporting_body_sha256` manifest copied from its retained actual constituent
hashes. This is consistency evidence, not independent authentication of a source.
The shared `history_provenance_reason` validator recognizes the historical feature
names and explicit proof fields; partial, missing or unknown recognized metadata
fails closed at offline trainer dataset admission. Legacy feature rows without
history metadata remain compatible.

The validator reuses an 8,000-byte bounded normalized metadata envelope and depth
eight; requires two position supports or at most three distinct Coinbase series;
checks every finite constituent clock against row capture, exact maximum-receipt
aggregation, supporting IDs/hash manifests, position identity/report clocks/gap,
Coinbase USD identity/window/pair ordering and review-target peer exclusion.
Trainer also requires consistent body/series identity for a source ID across the
row's correlated transforms. Recursive applicability checks reject context-only,
synthetic and incompatible horizon declarations even in restored datasets with a
correctly recomputed serialization checksum. Manifest source IDs are data keys,
not applicability declarations. Existing row identity, costs, action binding,
geometry, whole-trade grouping, chronological split/purge and OOS floors remain.

Tests start with actual pure-producer→adapter→frozen-dataset output; valid output
admits five non-HOLD rows from the fixture's single trade and produces no model.
Invalid independently supplied copies with recomputed checksums are explicitly
excluded. This closes import admission; it does not assert a normal-builder leak,
new source authority or learned usefulness.
