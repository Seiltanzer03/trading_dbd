# Observed postpublication event reaction

## Intent and baseline

Implement the remaining observed-reaction part of original specification §7.
Reaction is elapsed market context, not surprise, causal effect, future profit or
an action rule. Accepted PR396 main0481fae4/tree6e6d196c is local9c8fde4.
Task/admission and source audits are complete; baseline292 adapter/runtime/dataset
tests passed. Existing event surprise requires prepublication consensus and stays
strict. No actual qualifying frozen release/target-price pair was established.

Use one coupled writer and independent reviews. Existing user execution authority
waives repeated document pauses; all review/CI/exact-SHA release gates remain.

## Chosen scope and alternatives

An imported-only adapter would leave the prospective producer unwired. A broad
archive migration cannot recover missing old identities/receipts. Choose a small
prospective producer reading the existing first-ingested FOMC deterministic store
and existing locked intraday feed, then freeze into new AI review snapshots.
No new GET, LLM/extraction call, DB backfill or archive rewrite. Existing macro
reconstruction APIs retain their semantics; add a separate actual-receipt reader.

V1 admits only exact configured direct market context: configured Binance crypto
symbols and Yahoo EURUSD=X/CAD=X. Preserve provider/symbol/base/quote/orientation
and authority role; do not rename USDT to USD or call these broker fills/prices.
Yahoo index/futures/offset bars and Coinbase related-market series cannot become
target reaction. Broader mapping support is deferred, not silently inferred.

## Pure contract

Create `seiltanzer/edge_family_event_reaction.py` with:

- `event_reaction_features(source: dict, cutoff: float, target_instrument: str) -> dict`
- `reaction_provenance_reason(feature: str, value: float, meta: dict,
  captured: float, horizon: float, instrument: str) -> str | None`
- A pure deterministic hash helper for the frozen close-observation projection.
- A lightweight prospective builder consuming already received release and feed
  observations; engine-facing attachment may live in this module or a small
  separate producer module if needed for clear responsibilities.

Exact producer interfaces: `reaction_observation_sha256(series: dict) -> str`,
`build_received_event_reaction_source(release: dict, feed, cutoff: float,
instrument: str) -> dict` returning `source` (packet or None) and `rejections`,
and `attach_observed_event_reaction(engine, snapshot: dict) -> None`.
Store addition: `FOMCDeterministicReleaseStore.latest_received(captured_ts: float)
-> dict`, a separate actual-receipt read, never replacing latest_admissible.

Result has `features`, `feature_provenance`, `rejections`. Malformed input returns
no features and a bounded reason rather than raising into the caller.
Absence of every extension field preserves legacy event facts. Any explicit
partial/null/unknown extension refuses the whole source before legacy fallback.

Extension fields: `event_reaction_contract="edge-family-event-reaction-v1"`,
`reaction_release`, `reaction_series`. Projection ≤4096 UTF-8 compact JSON bytes,
depth≤8. Strings/IDs bounded (128 bytes; source URL≤2048). No nonfinite/bool nums.
Unchanged bundle≤1,000,000 bytes and selected facts≤8,000 bytes.

`reaction_release` retains source_id, release_id, event_type, source_url,
published_at, received_ts, available_at, body_sha256, source_verified,
publication_basis="VERIFIED_PUBLICATION_TIMESTAMP", and hash_kind explicitly
declaring the official normalized document digest. Root release_id/event_type/
published_at must agree. Auto FOMC uses the stored parsed official-page timestamp,
normalized body digest and actual first fetched_at, never available_at=publication.
Keep OFFICIAL_DATED_PAGE_NOT_VERSIONED/historical_reconstruction declarations:
this is known-at-capture event context, not a frozen publication-time text vintage.

`reaction_series` retains actual configured authority identity/source_id/provider/
source_symbol/source_instrument/target_instrument, explicit base/quote/orientation,
price_basis equal to the existing direct authority role, source_verified,
direct_source, derived/proxy/broker_execution_bars flags, received_ts/available_at,
and 2–6 samples `[bar_start_ts, bar_end_ts, close, received_ts]`.
Hash kind is `FROZEN_CLOSE_OBSERVATIONS_SHA256`; observation_sha256 recomputes from
canonical sorted compact JSON of retained identity, receipt and samples. It proves
the frozen observation projection, not a raw HTTP response. Precisely document
the projection. Explicit role is CONFIGURED_MARKET_CONTEXT_NOT_BROKER_PRICE and
target price equivalence is never asserted.

Hash projection keys exactly: source_id, provider, source_symbol, source_instrument,
target_instrument, base_currency, quote_currency, orientation, price_basis,
source_verified, direct_source, derived, proxy, broker_execution_bars,
received_ts, available_at, samples. SHA256 of UTF-8 JSON with sort_keys=True,
ensure_ascii=False, allow_nan=False, separators=(',',':'). observation_sha256 and
hash_kind are excluded to avoid a circular digest; declarations bind separately.

Release publication≤actual receipt≤parent receipt≤cutoff; each completed endpoint
≤its actual batch receipt≤parent receipt≤cutoff. available_at and received_ts, if
both declared, agree. Parent receipt is max(release receipt, series receipt).
Root observed_ts equals last retained completed endpoint. Receipt inherited from
the actual feed authority available_at is not bar time or archive insertion time.
Reject future/malformed/unverified/synthetic/demo/context-only constituents and
contradictory identities, hashes, root clocks or horizons. Keep all applicability
declarations; undeclared parent horizon cannot erase child restriction.

## Grid and observed features

Use first fully postpublication minute bar: start=ceil(published_at/60)*60.
Samples begin at start and advance exactly60s, end=start+60. First sample's close
is the baseline; later samples are consecutive completed closes. No interpolation,
fill-forward, duplicate/straddling bars, provider splicing or prepublication close.
2–5 samples permit only the 1m measurement; six permit both1m and5m.

For validated event_type `[a-z][a-z0-9_]{0,31}` emit:

- `event.<kind>.reaction_return_1m` = log(close[1])-log(close[0]).
- `event.<kind>.reaction_return_5m` = log(close[5])-log(close[0]), only six samples.
- `event.<kind>.reaction_start_delay_seconds` = first bar start-publication.

Delay exposes the omitted subminute interval; first baseline close is another
completed minute later, not an instantaneous release quote. `return_interval_seconds`
is60/300, while conservative `window_seconds` is supporting endpoint-publication:
second end-publication, sixth end-publication, or first end-publication for delay.
All are positive even when delay=0. Supporting receipt is max actual release/price
receipt. All transforms and surprise share `dependency_group="release:"+release_id`.
No extra independent correlated vote or assumed causal sign.

## Prospective producer and integration

Add separate first-receipt store reader filtering publication AND fetched_at by
capture. Do not change latest_admissible research reconstruction or old snapshots.
Engine-facing producer reads stored release and copies already fetched feed rows
under its lock. Validate full actual configured authority and completed OHLC before
retaining close samples; reject gaps/conflicts/offset/demo/proxy/wrong-symbol feeds.
No network refresh, archive mutation or first-receipt manufacture. Fail with
explicit audit reason when no suitable release/window exists.

Attach bounded validated event records to new AI snapshot edge_family_sources
without replacing existing records, after macro capture and before regime/event
refinement/model review identity. Never attach a receipt later than snapshot T0.
Existing runtime bundle imports validate reaction at immutable bundle capture,
and adapters recompute at review. Dataset replay reads frozen packet only.

Adapter reaction runs independently of consensus-only surprise. Invalid/missing
consensus still rejects surprise; valid reaction remains observed data. Explicit
invalid reaction extension cannot fall back into valid surprise from that source.
Trainer recognizes only exact reaction feature names/contract and validates
bounded normalized release/price proof, IDs/hash manifest, target/basis, receipts,
applicability, conservative windows and recomputed values before fitting. Missing/
unknown/partial proof and conflicting source identity across correlated features
reject even with a valid recomputed dataset checksum. Consensus remains required
for all legacy/surprise features; reaction recognition must not be a bypass.

Normalized proof keeps both supports/hash kinds, compact samples and declarations;
hash-manifest IDs are data keys, not scope declaration names. Runtime applicability
also remains binding. Risk/cost/action/geometry/OOS/purge/sample floors unchanged.
Observed data stays DATA_AVAILABLE_MODEL_PENDING/zero vote without validated model.

## Verification and residuals

RED→GREEN: numerical1m/5m/delay with on/off-grid publication; progressive window;
no consensus; unchanged surprise; every clock boundary including capture vs later
review; partial/unknown/null/deep/oversized proof; invalid closes/hash/identity/
orientation/provider/quote/mapping/global bypass; gapped/duplicate/straddling bars;
root/child applicability; frozen immutability/future path noninterference; forged
dataset checksum/value/window/support proof; real stored receipt reader and locked
feed producer, no additional calls, source caps and snapshot nonreplacement.
Focused tests first; one root broad run and mandatory official final-tree CI.
Fresh task review and fresh most-capable whole-feature review before merge.

This does not create historical receipts, broker price equivalence, genuine event
observations, consensus or models. General text novelty beyond existing FOMC,
event causal effects, broader proxy mappings, value/carry and learned conditioning
remain separate. Missing qualifying input is explicit, never synthetic authority.

## Implementation details, 2026-10-05

- The separate `latest_received` SQL predicate is
  `published_at<=T0 AND fetched_at>=published_at AND fetched_at<=T0`.
  It retains the immutable first stored fetch, parsed official publication clock,
  normalized document digest, historical reconstruction and non-versioned-page
  declarations. `latest_admissible` and all existing research reconstruction
  calls remain unchanged.
- The prospective builder copies authority and OHLC rows together under the
  existing feed lock, using the feed batch's actual `available_at` as receipt.
  Only the first 2–6 completed consecutive postpublication minute bars are kept;
  no archive insertion time, refresh or request-path network work is used.
  The actual feed quality is retained, not promoted.
- Normalized feature proof uses `reaction_contract_version`, `root_provenance`,
  two `constituent_provenance` supports, source-ID keyed hash/hash-kind manifests,
  `applicability_provenance` and conservative windows. Trainer reconstructs the
  frozen packet, validates its closed fields, recomputes the value and checks
  identical support bindings across correlated reaction features. Only a valid
  exact reaction name/proof routes past consensus-only surprise admission;
  genuine legacy history and surprise checks remain unchanged.
  Both boundaries apply the 4096-byte cap to the same three-field reaction
  extension projection, not to extension plus reconstructed root. Closed root
  proof remains separately bounded to 4096 bytes and closed normalized feature
  metadata to 8000 bytes, with depth/string/ID limits preserved. The deterministic
  `release:` dependency prefix is checked in addition to the bounded release ID.
- Attachment is after local bundle and macro capture, before event refinement,
  active-management/model review identity. Appending checks the combined
  `edge_family_sources` envelope with the unchanged 8,000-byte selected-facts cap.
  On overflow it preserves every existing fact and records refusal of the new
  packet; it does not delete or compact previous facts.
- The six-close artificial contract fixture produces a 1,673-byte compact source,
  a 1,425-byte extension and a 1,800-byte selected event envelope. Normalized
  feature metadata is 2,454–2,457 compact bytes. These prove the actual code path
  fits the caps, not that a genuine qualifying production observation exists.
- Saved RED/GREEN and exact scoped commands are in
  `.superpowers/sdd/2026-10-05-observed-event-reaction/task-1-report.md`.
  Independent task/whole-feature review, root broad tests and mandatory official
  final-tree CI/release remain separate gates.
