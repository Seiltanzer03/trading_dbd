# History runtime projection

The user authorized autonomous completion of the original terminal plan. This
package closes the runtime-reader prerequisite of hot/cold storage; it does not
authorize fabricated observations, new model authority or live trading.

## Evidence and approach

Read-only covering-index run39112917244/job117632650451 took2003.4ms, touched no
payload pages and changed no database rows. On production919d524 all91,376
old-prefix IFD rows already have current G1 membership;62,460 also have a G1S
source link.28,916 lack that link before every other exclusion. These counts
are not byte-verified retirement eligibility or reclaimed capacity. Evidence:
`docs/HOT_COLD_RUNTIME_REFERENCE_PROOF_2026-10-18.json`.

Keeping every G1 member leaves no retirement candidates. Removing its source
without a reader change drops exclusion and status accounting. Replacing source
features/forecasts with metadata stubs would misrepresent source authority.
Use a distinct immutable metadata projection for already-evaluated rejected
rows, plus exact additive diagnostic rollups computed from the real rows.
Full-source readers continue to require the verified Yandex history union.

## Retention and eligibility

Keep all G1S source parents, all Q/cut/prediction references, all virtual-position
parents, resolved/pending rows, catalog/global boundaries, post-catalog rows,
the actual latest1000 observations per instrument including boundary ties,
latest15m trigger anchors, latest eligible health anchors and recent48h inputs.
Keep every row missing current membership, with any eligible membership, or
whose canonical full source hash differs from the current membership hash.
Retirement additionally requires complete cold-root/capture verification and
exact full typed equality with its pinned cold row under the existing budget.

This is intentionally conservative. If the resulting off-host image does not
reclaim enough physical capacity, refuse live cutover. Do not replace the
measured result with the earlier91k estimate or relax the reserve.

## Derived data and owner changes

For each actually retired row, store its original rowid/id, instrument, horizon,
capture/target timestamps, feature-contract version, real terminal IFD status,
and canonical source hash in a separately named immutable archive index. No
features_json/forecast_json/outcome_json substitutes and no invented labels.
Existing domain memberships/ACKs/trades remain intact. The index is an explicit
derived storage object, not a complete passive source table or a training row.

G1 exclusions and readiness use the index only for rejected-membership metadata.
Measurement validity remains false for the real IFD status. Mutation checking
compares the verified archived source hash to immutable membership; retained
sources still use their full live canonical record. Unprocessed sources stay
hot, preserving future membership synchronization. Eligible/model/cut readers
continue to use retained full source rows.

Compute resolution/feature-version totals, legacy passive diagnostics and the
existing measurement-runtime counters over the retired rows only, using the
owning calculation semantics. Runtime status adds these exact rollups to hot
counts, with explicit physical/historical scope. Reuse a streaming pure counter
function rather than duplicating its formula or fetching all payloads at once.
All denominators and error counters remain equal on causal before/after fixtures.

## Integrity and capability

Version the embedded storage contract to bind archive index schema/count/root
and rollup payload/hash, retaining strict v1 transport compatibility. Validate
exact retired-id coverage, no hot/archive overlap, IFD-only status, ineligible
matching membership and current counter/dataset contracts. Immutable triggers
protect the derived objects. Normal backup/restore propagates the bound state.
The complete current union verifies and consumes derived storage objects after
rehydration so full readers cannot double-count them.

The literal runtime capability remains absent until the real readers, state,
transport and causal regressions are implemented. Old code and incomplete
projections must remain blocked by the external startup guard. A receipt never
equates reference retention with complete reader equivalence.

## Acceptance and release

Use actual PassiveLearningEngine/G1 readers against a frozen full source and its
compacted output: recent observations, trigger input, G1 sync/exclusions/cohorts/
eligible cuts, mutation detection, raw/current/legacy/measurement counts, and
native G1S source joins. Compare semantic outputs while excluding timestamps and
physical file-size fields. Test missing/mutated projection rows/pins, eligible or
unprocessed retirement refusal, full union without double counting and unchanged
source bytes. Focused tests while editing; one independent final review and full
mandatory CI at the release boundary. Finite production owner, authenticated
fresh source/image receipts and actual physical capacity remain separate steps.
