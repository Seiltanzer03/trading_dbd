# Task 1 report: observed postpublication event reaction

Status: **DONE_WITH_CONCERNS** — scoped implementation and proof complete;
fresh independent task/whole-feature review, root broad checks and mandatory
official exact-tree CI/release remain mandatory. This is not product completion,
deployment acceptance, predictive efficacy or genuine-data availability.

## Scope and commits

- Worktree: `/workspace/scratch/7364059e79d5/edge_event`.
- Branch: `feat/edge-observed-event-reaction`.
- Recorded task BASE: `de1a075af8a9abb33e13a40c321ea64454ff0335`.
- Implementation/code/tests/docs commit:
  `1553de8498930451e246fb87067c4eac2aee0f9e`.
- This report is a subsequent documentation-only local commit; root receives its
  exact hash with the handoff. Review the entire recorded BASE..final HEAD,
  not HEAD~1.
- Sole coupled writer. No subagents, remote operations, other worktree edits,
  destructive operations, refresh calls, archive writes or broad suite were used.
  Local scoped commits only; root owns broad/official CI and release authority.

## Implemented behavior

1. Added the bounded pure reaction contract, exact configured direct-context
   admission, observation hash helper and value-aware normalized-proof validator.
   Projection is <=4096 compact UTF-8 bytes, depth<=8, strings/IDs<=128 bytes
   except source URL<=2048 bytes; one 2–6 sample consecutive completed minute
   series. Malformed/partial/null/unknown proof returns bounded refusal.
2. Added separate `FOMCDeterministicReleaseStore.latest_received`. SQL checks
   publication and first stored fetched_at against capture, rejects a fetch
   preceding publication and keeps immutable first receipt. Legacy
   `latest_admissible` and research reconstruction semantics are unchanged.
   Actual receipt, parsed publication timestamp, normalized document hash,
   historical_reconstruction and OFFICIAL_DATED_PAGE_NOT_VERSIONED survive.
3. The prospective builder copies already-fetched authority/OHLC under the existing
   feed lock, validates identity/OHLC/clock continuity and preserves actual batch
   receipt and quality. It never refreshes or substitutes archive created_ts,
   bar time, quote timestamps, reconstructed publication availability or proxy.
4. V1 admits configured Binance BTCUSDT/ETHUSDT/SOLUSDT and Yahoo EURUSD=X/CAD=X
   context only. Source instrument is the configured application code, actual
   base/quote/symbol/provider remain explicit, price_basis is the existing feed
   authority role, and authority_role is
   CONFIGURED_MARKET_CONTEXT_NOT_BROKER_PRICE. No broker equivalence, Coinbase,
   offset bars, inferred mappings or USDT-to-USD relabeling.
5. Grid is ceil(publication/60)*60. First completed close is the baseline, not an
   instantaneous publication quote. Publication1000, start1020 and six closes
   [100,101,102,103,104,105] yield 1m=.009950330853167877,
   5m=.04879016416943127, delay20; conservative windows140/380/80 seconds.
   Delay's return_interval_seconds is explicitly0; returns are60/300.
   Two through five samples emit 1m/delay only. All transforms share release:ID.
6. New AI snapshots attach after local bundle and macro capture, before event
   refinement, active-management/model review identity. Appending never replaces
   existing sources. Combined edge_family_sources, not just the new projection,
   is checked against the unchanged8KB selected-facts cap; overflow preserves old
   facts and records explicit refusal. Existing family record128 cap is retained.
7. Runtime validates reaction at immutable bundle capture; adapter recomputes at
   review. Valid reaction is independent of consensus-only surprise. An explicit
   invalid extension blocks the entire source, including surprise fallback.
   Legacy surprise/age still require matching strictly prepublication consensus.
8. Frozen dataset uses the existing real adapter and recursive original-scope
   guard. No dataset product edit was needed: positive producer→adapter→dataset
   fixtures, child horizon refusal, frozen immutability and future-path feature/
   provenance noninterference are exercised through that actual code path.
9. Trainer reconstructs bounded root/two-support proof, checks closed normalized
   fields, support/hash-kind manifests, configured identity, receipts, horizon,
   conservative windows and exactly recomputed values. Correlated reaction
   supports with the same source ID cannot change identity/body/observations.
   Exact reaction names lacking proof and reaction flags on legacy/history names
   refuse; genuine legacy history keeps its prior validator. Consensus routing
   is skipped only after successful exact reaction validation, never by a flag.

No risk, cost, action, geometry, OOS, purge or sample floor was changed. Observed
data remains DATA_AVAILABLE_MODEL_PENDING with zero vote and no trained artifact
for the one-trade deterministic fixture (five admissible non-HOLD rows).

## Actual test evidence

All commands below ran from the task worktree using
`../trading_quotes/.venv/bin/python`. Logs are in this report's directory.
Each saved test pipeline used `set -o pipefail` and `tee` to the named log.

| Phase | Exact pytest arguments after `-m pytest -q` | Actual result | Saved log |
| --- | --- | --- | --- |
| Producer RED | `tests/test_edge_family_event_reaction.py` | 42 failed, missing interface assertion | `producer-red.log` |
| Producer GREEN | `tests/test_edge_family_event_reaction.py` | 42 passed,0.56s | `producer-green.log` |
| Integration RED | `tests/test_edge_family_event_reaction.py tests/test_macro_fomc_deterministic_bootstrap.py` | 18 failed,50 passed | `integration-red.log` |
| Boundary RED | `tests/test_edge_family_event_reaction.py::test_runtime_reaction_hash_is_validated_before_fact_selection tests/test_ai_management_api.py::test_prospective_reaction_unavailability_is_frozen_before_provider_review_identity` | 2 failed,1 baseline warning | `boundary-red.log` |
| Integration GREEN | `tests/test_edge_family_event_reaction.py tests/test_macro_fomc_deterministic_bootstrap.py tests/test_ai_management_api.py::test_prospective_reaction_unavailability_is_frozen_before_provider_review_identity` | 70 passed,1 baseline warning,7.28s | `integration-green.log` |
| Preliminary hardening | `tests/test_edge_family_event_reaction.py` | 8 failed,64 passed:3 product failures plus5 test-fixture DiskCache directory/filename errors | `hardening-red.log` |
| Confirmed hardening RED | `tests/test_edge_family_event_reaction.py` | 3 failed,69 passed after test-only cache filename repair | `hardening-red-confirmed.log` |
| Hardening GREEN | `tests/test_edge_family_event_reaction.py` | 72 passed,0.88s | `hardening-green.log` |
| First scoped GREEN | full12-file command below | 690 passed,1 baseline warning,58.33s | `scoped-green.log` |
| Self-review string bound RED | `tests/test_edge_family_event_reaction.py` | 2 failed,72 passed | `string-bound-red.log` |
| String bound GREEN | `tests/test_edge_family_event_reaction.py` | 74 passed,0.90s | `string-bound-green.log` |
| Final scoped GREEN | full12-file command below | **692 passed,1 baseline warning,61.81s** | `scoped-final-green.log` |

Final exact command:

```bash
set -o pipefail; ../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_event_reaction.py tests/test_edge_family_adapters.py tests/test_edge_family_history.py tests/test_edge_family_source_runtime.py tests/test_edge_family_dataset.py tests/test_edge_family_training.py tests/test_edge_family_pipeline.py tests/test_macro_fomc_deterministic_bootstrap.py tests/test_ai_management_api.py tests/test_ai_authoritative_price_preflight.py tests/test_ai_verdict_api.py tests/test_edge_regime.py | tee .superpowers/sdd/2026-10-05-observed-event-reaction/scoped-final-green.log
```

`git diff --check` and `git diff --cached --check` returned exit0 with no output.
The final run covers all changed Python surfaces and direct changed-scope callers,
including existing source/history/dataset/trainer/pipeline regressions. It is
deliberately not a broad-suite, frontend or official CI claim.

An intermediate integration attempt reported69 passed/1 failed: the actual dataset
packet was rejected as FEATURE_HISTORY_PROVENANCE_INVALID because generic history
validation recognized shared constituent_provenance fields. Root cause was traced
to proof-family routing; the fix routes only a successfully validated reaction
past history/consensus checks. Existing history controls and missing reaction proof
tests are green. The final integration log supersedes that intermediate attempt.

The one warning is the pre-existing StarletteDeprecationWarning for using httpx
with starlette.testclient, already observed in the baseline. No dependency change,
warning suppression or test/guard removal was made.

## Compact bytes and prospective publisher behavior

Measured the actual builder output for the six-close artificial fixture:

| Projection | Compact/selected JSON bytes |
| --- | ---: |
| Complete compact source | 1673 |
| Reaction extension | 1425 |
| Selected event envelope using current selected-facts JSON encoding | 1800 |
| Normalized1m metadata | 2456 |
| Normalized5m metadata | 2457 |
| Normalized delay metadata | 2454 |

The tests use the actual received reader, actual MarketData configured-authority
capture for all five admitted instruments, locked feed builder, pure adapter,
immutable local bundle import, real frozen dataset and trainer. Artificial fixture
observations never leave tests or become source/model publications.

The AI route regression proves the prospective audit is present before provider
review and consistent with the frozen canonical review identity. Positive additive
attachment/immutability/cap tests exercise the real attachment implementation.
Operationally the source attaches only to a new qualifying review while the
existing feed still holds the first completed postpublication window. It does not
refresh to find missing bars, synthesize a release, enrich old reviews or activate
a model. Unconfigured store, unsupported instrument, future receipt, absent first
bar, incomplete/gapped/conflicting OHLC or cap overflow is explicit unavailability.

## Self-review and remaining concerns

- Re-read the full task brief/spec and inspected all changed product boundaries.
  Additional covered findings were feed-quality promotion, inconsistent child
  horizons without a parent declaration and retained declaration string bounds;
  each actual failure was saved before its product fix.
- Reviewed exact grid endpoints, max actual release/series receipt, hash projection
  keys/encoding, root/support ID binding, child declarations, global-context
  bypass, strict legacy surprise and shared history routing. No known unresolved
  implementation finding is asserted closed without the forthcoming reviews.
- Prior source audit established no genuine qualifying frozen release/target-price
  pair. This task creates no actual data, historical receipt/vintage, broker
  equivalence, lawful PIT consensus, trained model or profit/causal-effect proof.
- The existing feed's finite live history must contain the first postpublication
  bars; an old FOMC record alone is insufficient. Receipt-aware known-at-capture
  document context is not publication-time versioned text.
- General event text novelty, broader/proxy mappings, learned conditioning,
  value/carry, executing-broker position/cost inputs and sufficient independent
  observations remain outside this increment.
- PR396 accepted baseline evidence is recorded in the roadmap from the root's
  existing baseline-acceptance.json; no new remote lookup was performed. Main
 0481fae4/tree6e6d196c/local9c8fde4, official2757 passed/4 skipped plus real WebKit,
 exact-SHA production/source/math acceptance and zero family models remain the
 accepted prior release, not acceptance of this increment.
- Root must dispatch fresh task review and fresh most-capable whole-feature
  review using recorded BASE, then broad Python/frontend and mandatory official
  final-tree Python3.11/real WebKit. PR/merge/deploy/exact-SHA production checks
  are root-owned; this worker performed none and remains available for fixes.

## Task-review fix round 1

Status: **DONE_WITH_CONCERNS** — the Important bound mismatch is reproduced and
covered by scoped GREEN evidence; independent scoped re-review and root's later
whole-feature/broad/official release gates remain pending.

Fix BASE: `7004a5094187a478e06053d3c5b851b5a64d86b1`. One scoped local fix commit
contains the code, regressions, specification clarification and this report
append; its exact hash is supplied to root at handoff. No other worktree, remote,
broad run or subagent was used.

The complete finding was read from `task-review-findings.md` and verified before
the product edit. The exact reviewer fixture was reproduced independently:
permitted 128-byte IDs, a 2048-byte URL and matching 240-minute scopes produced a
3662-byte extension, no producer rejection, and a 4122-byte reconstructed root
packet rejected by trainer proof admission. The failure was not an invalid source
or window: producer and trainer were applying the same number to different bytes.

Fix: both producer `_validate` and trainer proof admission now use the same
three-field `_reaction_projection` (contract, release, series), bounded to4096
compact UTF-8 bytes. The reconstructed root is checked separately against the
existing closed root-key schema and a4096-byte bound; normalized metadata is
checked against its existing closed schema and an8000-byte bound. All retain
depth<=8, source strings/IDs<=128 and URL<=2048. The normalized deterministic
`release:` prefix is additionally bounded to its fixed8 bytes plus the original
128-byte release ID, then required to equal the recomputed group; this adds no
arbitrary schema field or unbounded string exception. Source selected-facts8KB,
bundle1MB, proof fields, applicability, receipts, values and model floors are
unchanged.

Covering tests are in `tests/test_edge_family_event_reaction.py`:

- Exact3662-byte extension/4122-byte reconstructed-root unchanged round-trip.
- Exact4096-byte extension admitted by the real already-received feed builder and
  proof validator;4097 bytes refused at both boundaries.
- Real4096-byte received producer→frozen dataset→full trainer admission: five
  actionable rows admitted, then all refused after adding one byte to each
  retained release URL and recomputing the dataset checksum.
- Unknown/oversized/deep root and normalized metadata remain refused.

Measured exact4096-byte received fixture: root466 bytes, normalized metadata
6269/6271/6272 bytes, selected source envelope4733 bytes. Thus the edge case stays
within all independent caps; it is still artificial contract evidence, not an
actual qualifying release/market observation or trained model.

Exact RED command and result:

```bash
set -o pipefail; ../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_event_reaction.py | tee .superpowers/sdd/2026-10-05-observed-event-reaction/fix-round-1-red.log
```

Actual: **3 failed,79 passed in1.16s**. The failures are the3662-byte round-trip,
the4096-byte admitted boundary and the real dataset/full trainer round-trip
(0 accepted versus expected5). Over-bound and closed-root/metadata controls passed
before the fix. Product edit followed this saved actual RED.

Exact focused GREEN command:

```bash
set -o pipefail; ../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_event_reaction.py | tee .superpowers/sdd/2026-10-05-observed-event-reaction/fix-round-1-green.log
```

Actual: **82 passed in0.94s**.

Exact final covering-scope command:

```bash
set -o pipefail; ../trading_quotes/.venv/bin/python -m pytest -q tests/test_edge_family_event_reaction.py tests/test_edge_family_training.py tests/test_edge_family_dataset.py | tee .superpowers/sdd/2026-10-05-observed-event-reaction/fix-round-1-scoped-green.log
```

Actual: **337 passed in4.72s**, exit0, no warnings. Existing dataset and trainer
files were included because this correction specifically changes producer-proof
admission at offline import; no12-file rerun or broad run was performed. After the
covering run only explanatory docstring/spec/report text changed. `git diff
--check` and staged diff checks are clean. The prior Starlette/httpx warning is
still deferred unchanged (not exercised by this narrower command).

Self-review confirms identical extension projection at both boundaries, unchanged
source/ID/schema and envelope caps, bounded root/full metadata, literal near-limit
within/over behavior and source immutability. Genuine data/model limitations and
all root-owned acceptance requirements above remain unchanged. Await scoped
independent re-review; the worker remains available for concrete findings.
