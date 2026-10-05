# Task 1 — native FOMC policy-sentence novelty

Status: **DONE_WITH_CONCERNS** — scoped implementation and verification complete;
independent review, root broad checks and official final-tree release gates remain.

Base: `a6130ba` (plan only), accepted product `4608dfa` / tree
`73f9496465c78938260322a93db9c55a0415b50a`, accepted main
`e574f4622639b09517ce7ee51c8eb536a7ede2cd` (PR #398).
Product commit: `249be40` — `Freeze native received FOMC policy sentence novelty`.
This report and both completed preimplementation audits are persisted in the
following documentation/evidence commit. No remote operation was performed.

## Delivered behavior

- `FOMCDeterministicReleaseStore.latest_received_text_pair` selects newest
  publication at/before capture, newest created vintage for a tied publication,
  then the exact stored predecessor ID. Validation is after selection, so an
  invalid newest event cannot fall back to an older pair. One store lock covers
  table discovery, metadata, guarded SQL text reads, decoding and validation;
  nonblocking contention has an explicit unavailable reason. Positive finite
  clocks, original body SHA, native ID derivation, official dated URL/date,
  native contract and linked publication order are checked. Both first HTTP
  fetch and first local creation must precede capture; availability is their
  maximum. Publication starts the unchanged four-hour event age, including
  changed old URL vintages. Historical and existing HTTP-only readers are intact.
- `edge_family_event_novelty.py` fixes the contract
  `edge-family-fomc-policy-sentence-novelty-v1`, feature
  `event.fomc.policy_sentence_lexical_distance`, region
  `FOMC_TARGET_RATE_DECISION_SENTENCE_V1`, tokenizer
  `ASCII_WORD_DECIMAL_SET_V1`, metric
  `UNSIGNED_TOKEN_SET_JACCARD_DISTANCE_V1`, unit
  `DIMENSIONLESS_JACCARD_DISTANCE`. Original complete sentences retain terminal
  punctuation and character offsets; decimal periods do not split them.
  Missing, ambiguous, incomplete or over-bound regions refuse. Token sets are
  complete, unique and untruncated; equal sets give zero, the hand-counted changed
  fixture has 13 shared tokens / 17 union tokens and distance `1 - 13/17`.
- Native records retain `fetched_at` and `created_ts`. Frozen supports use
  `received_ts` for the actual original HTTP receipt, retain `created_ts`,
  and `available_at=max(received_ts,created_ts)`. Both original normalized-body
  digests and independent `FROZEN_POLICY_SENTENCE_UTF8_SHA256` digests remain.
  Each source has a distinct `novelty:` ID and the same `release:` dependency
  group as reaction/current-release evidence. It is global observed official
  text context with no causal-effect/broker/outcome authority.
- Strict extension/root/support allowlists, versions, identities, flags,
  applicability, clocks and value-aware exact normalized proof validation apply
  in the module, adapter, runtime and trainer. Both explicit event extensions
  validate before a record inserts reaction, novelty or surprise features.
  Unknown reserved feature names and renamed novelty metadata reject. Trainer
  reconstruction recomputes the retained sentence hash and metric, compares
  canonical normalized metadata, and binds common original URL/body hash/
  publication/HTTP receipt across reaction and novelty supports without requiring
  equal local-availability/projection schemas. Surprise still needs matching
  strict prepublication consensus; only validated reaction/novelty is exempt.
- Store text is limited to 65,536 UTF-8 bytes before Python SQL materialization;
  sentences to 768 bytes and 128 unique tokens; extension/root to 4,096 bytes,
  normalized metadata to 8,000, nesting to depth 8. Compact UTF-8 and conservative
  escaped/default JSON proof budgets are both enforced. Combined selected facts
  remain at most 8,000 bytes and 128 records/family; old facts are never evicted.
- The existing bounded reaction API remains the only private capture owner:
  one 0.25-second deadline including input copying and executor queue, one engine
  admission lock and one timely commit. Both producers have independent audits;
  novelty runs without valid price/feed/reaction support, and reaction survives
  novelty refusal. Timeout, queued abandonment, cancellation and running overlap
  explicitly refuse both optional outcomes; surviving workers retain admission
  until exit and cannot mutate live review bytes. Synchronous reaction remains
  unchanged. Its former whole-snapshot equality test removes only the new novelty
  audit, separately verifies that audit and compares the entire remaining result.
- Existing dataset retention was sufficient; no dataset production changes were
  necessary. Frozen replay admits the retained source, produces reproducible
  features/provenance and trains admission without consulting today's store or
  future path observations. The feature alone gives zero model votes.

## Verification and actual evidence

All tests used `../trading_quotes/.venv/bin/python -m pytest`; native receipt
fixtures used isolated in-memory SQLite databases and explicit ingestion clocks.
No production data, network/provider calls, real positions or orders were used.

| Stage | Actual observed result | Evidence |
| --- | --- | --- |
| Initial reader/projection RED | 27 failed in 2.68s; missing reader/proof assertions | `red-reader.log` and original tool output |
| Reader/projection GREEN | 27 passed in 0.69s | `green-reader.log` |
| Adapter/runtime/trainer RED | 5 failed, 57 passed in 1.12s | `red-admission.log` and tool output |
| Frozen admission/binding GREEN | 63 passed in 0.86s | `green-bindings.log` |
| Capture RED | 6 failed, 63 passed in 1.78s | Original captured tool output; **saved `red-capture.log` is partial** |
| Capture plus existing latency GREEN | 99 passed, 1 warning in 4.94s | `green-capture.log` |
| Self-review hardening RED | 5 failed, 71 passed in 2.41s | `red-hardening.log` and tool output |
| Hardening GREEN | 76 passed in 2.13s | `green-hardening.log` |
| Missing-table reason RED | 1 failed in 0.76s | `red-missing-table.log` |
| Final affected profile | **616 passed, 1 warning in 9.94s**, command exit 0 | `green-profile.log` and execution-session completion |
| Diff check | Clean, exit 0 | `git diff --check` |

`red-capture.log` currently ends during the queued-executor failure traceback
(8,294 bytes) without its summary, although the original execution tool returned
the complete actual failure summary above. That saved file is not represented
as a complete raw transcript, and no RED rerun or invented reconstruction was
made after implementation. Logs remain in the preserved task workspace; the
report captures their results and provenance. Initial parameter IDs printed
oversized fixture values; later tests use concise named IDs.

The final command was:

```sh
../trading_quotes/.venv/bin/python -m pytest \
  tests/test_edge_family_event_novelty.py \
  tests/test_macro_fomc_deterministic_bootstrap.py \
  tests/test_macro_fomc_extraction_refinement.py \
  tests/test_edge_family_event_reaction.py tests/test_ai_request_latency.py \
  tests/test_edge_family_adapters.py tests/test_edge_family_source_runtime.py \
  tests/test_edge_family_dataset.py tests/test_edge_family_training.py -q
```

The warning is the preexisting Starlette/httpx TestClient deprecation, emitted
by `test_materializer_and_api_correlate_preflight_route_and_final_status`.
No dependency was changed. No broad Python/frontend profile was run by this
writer; root owns final broad verification and official final-tree CI. The known
unchanged Python 3.12 sparse-WAL baseline failure is not recategorized as green.

Fixture/debugging corrections were limited to the test contract: independently
hand-counting the lexical union; constructing an incomplete qualifying sentence
at the document end; retaining a harmless Committee mention so the native
normalizer can ingest the absent-anchor fixture; and constructing coherent cost,
position/path fixtures at the native publication clock instead of shifting a
completed hashed proof. The frozen dataset failure was traced to the shifted
fixture identity, not bypassed by weakening production admission.

## Author self-review

Reviewed the complete coupled product diff against the spec and both audits.
The self-review reproduced and fixed an unterminated second qualifying region
hidden after a complete one, stripping explicit native synthetic declarations,
renamed retained metadata, numeric/boolean normalized equality and imprecise
missing-table refusal. New proof-cap and unique-token bound cases pass. Examined
full lock coverage, no-fallback selection, distinct hash meanings, all-or-none
extension insertion, immutable replay, common original support binding, separate
capture audits and timely-only source commit. No remaining substantive finding
was identified by the author. This is an author review, not independent review;
root will dispatch the fresh task review and Astra whole-feature review.

## Rulings and preserved boundaries

- Followed the existing approval-pause waiver, isolation and preservation rulings
  in `progress.md`. No new approval pause, worktree cleanup, remote write or
  competing writer was introduced. Root owns release and independent reviews.
- Native/proof field placement, explicit metadata version names and deterministic
  tied-vintage ordering were incidental schema choices pinned by focused tests;
  no substantive design ambiguity required a root ruling. Frozen offsets are
  character positions in the stored normalized text; digest bases are UTF-8.
- The prior audit's full-body hash caveat remains: only the native store verifies
  the full normalized-body SHA. Frozen snippets independently verify their own
  projection digest/value and retain original lineage, without authenticating
  the original document or original publication vintage.
- Training floors remain exactly the existing configuration: 20 training groups,
  two untouched validation blocks of 10 groups each, existing purge/OOS/positive
  gain and feature/geometry/cost/risk gates. Repeated trades for one FOMC release
  are not claimed to be independent events or sufficient novelty calibration.

## Concerns / residuals / root handoff

No actual native linked runtime pair, timely live coverage, whole-statement
semantics, calibrated event model or predictive/causal/profit effect was
established. The source audit's daily offhost generation, hourly local polling
and bundle-validity limits remain liveness blockers for timely coverage.
Licensed PIT consensus, real source mappings, genuine independent observations
and model calibration remain residuals. No sample floor, refresh/acquisition,
provider guard, smoke/transport limit, model vote, expert pool, weight, risk,
cost, broker/outcome authority or dependency was changed. All temporary logs
and task evidence are preserved for root. PR #398 acceptance supersedes the
old pending latency note in the roadmap; the new feature remains pending release.

## Fix round 1 — Important I1 from fresh task review

Fix base: `74ea4e3fcaa816a776f547b3e6e1ce26348139cd`. Review evidence is retained
in `task-review-report.md`. The original author self-review missed this case;
fresh task review correctly identified that clearing only top-level novelty
dispatch fields let retained novelty root proof be renamed into consensus-backed
surprise. No root release gate or independent review is claimed complete here.

Reproduced the exact mutation of an admitted frozen EXIT row: renamed its feature
to `event.fomc.surprise`, removed all nine fields named in I1, installed independent
valid legacy consensus metadata, and retained its original novelty root authority
and identity. Before the fix, `_row_reason` returned `None`; the regression now
requires `FEATURE_NOVELTY_PROVENANCE_INVALID`. Related variants remove top-level
novelty source identity too and independently retain root authority/source ID/
contract/extension or support sentence/hash/basis/contract markers. Both module
dispatch and trainer admission must reject, including proof containers retained
under root/support provenance.

The fix is confined to `edge_family_event_novelty.py` dispatch. It checks exact
novelty declarations before allowing another feature namespace, traversing only
the known `root_provenance` and `constituent_provenance` paths. Fixed-key membership,
depth at most eight and an at-most-128-node inspection budget prevent arbitrary
dictionary recursion, large list iteration and cyclic/deep proof bypass. An
oversized known proof container refuses before iteration. Exact-feature byte,
schema, version, scope and frozen reconstruction validation remain unchanged.
No trainer consensus logic or unrelated admission path was modified. Positive
legacy consensus remains admitted; receipt at publication still rejects.

Actual verification (same isolated fixtures and existing Python interpreter):

| Check | Command | Actual result |
| --- | --- | --- |
| Exact I1 and direct retained root/support RED | `../trading_quotes/.venv/bin/python -m pytest tests/test_edge_family_event_novelty.py -q -k test_i1` | 9 failed, 1 passed, 77 deselected in 0.99s |
| Expanded retained-path/bounds RED before implementation | Same command; saved `red-i1.log` and captured tool summary | **12 failed, 1 passed, 77 deselected in 1.02s** |
| Amended-code GREEN | `../trading_quotes/.venv/bin/python -m pytest tests/test_edge_family_event_novelty.py tests/test_edge_family_training.py -q` | **214 passed in 4.29s**, exit 0, no warnings; `green-i1.log` |
| Diff check | `git diff --check` | Clean, exit 0 |

The prior 616-test affected profile was not repeated: only novelty dispatch and
its regression tests changed, and root explicitly requested the amended-code
profile. The original live-pair/cadence/semantic/efficacy and release residuals
are unchanged. Product and report/reviewer evidence are committed locally for
root's fresh scoped fix review; no remotes, subagents or new dependencies.
