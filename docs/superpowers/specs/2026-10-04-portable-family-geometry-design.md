# Portable family geometry: affine R equivalence

## Intent and authority

Continue the original unified-edge plan and the recorded portable-geometry code
gap. User authorization to implement, review, push, green-CI merge and exact-SHA
deployment persists; document approval pauses were explicitly waived in the
existing execution record. This is an optional producer/consumer contract,
not proof of efficacy or a new risk admission rule.

Baseline: production PR394 main `29bbb28a692d2ef37cae4129b6c632735a52c579`,
tree `cd29e8e22835d40f81a8bc69c1f1ec7a416ecf25`. Local equivalent `f13777f`.

## Choice and limits

Implement `edge-family-affine-r-geometry-v1` for CLOSE_10, CLOSE_25, CLOSE_50,
EXIT and the existing TIME_STOP relative binding. HOLD remains the replay
control. Price-parameterized actions retain exact geometry and their concrete
IDs. Normalize known price fields in execution geometry, exposure and the
complete candidate matrix by signed original risk `(entry-original_stop)`.
Retain instrument, direction, all execution R values/rungs, horizon, exposure,
barrier types and nonprice parameter presence/values. Normalize R ratios to
12 decimal places to remove affine floating subtraction noise; do not apply
this rounding to risk/cost calculations or concrete replay parameters.

Only descriptor equality admits a portable forecast. Varying current/max R,
exposure, rule topology or normalized action parameters cannot pool or
extrapolate. Learned conditioning/support ranges remain a distinct subsequent
step; this release removes absolute-price partitioning only. It does not
claim to eliminate all cohort starvation.

## Shared contract

Create `seiltanzer/edge_family_geometry.py` with pure lightweight functions:
`portable_geometry(snapshot: dict) -> dict`, canonical descriptor hash,
bounded frozen geometry evidence projection, row/artifact validation and
runtime admission. Reuse the existing exact geometry validator before any
normalization. No source fetch, model fit, scenario replay or live mutation.
Unknown versions, malformed geometry, nonfinite values, missing proof,
inconsistent hash/instrument/action/time or unsupported portable actions fail
closed. Unknown fields are not silently erased before the exact validation.

Extension fields: `geometry_contract` is the exact version string,
`geometry_descriptor` is the normalized descriptor, `geometry_sha256` is its
canonical hash. Portable rows additionally have `exact_geometry_sha256` and
`geometry_evidence` (bounded original geometry snapshot projection). Evidence
limit is 48 KiB; descriptor limit 32 KiB. Artifacts omit the raw proof and
exact-price hash. Existing geometry_sha256 without an explicit contract keeps
legacy exact meaning. Shared interfaces: `geometry_descriptor_sha256(dict)
-> str`, `geometry_evidence(snapshot: dict) -> dict`,
`portable_row_reason(row: dict) -> str | None`,
`portable_artifact_reason(artifact: dict) -> str | None`, and
`portable_geometry_matches(artifact: dict, snapshot: dict) -> bool`.

Portable rows retain original concrete candidate and unchanged observed-path
counterfactual labels, an independently recomputable bounded geometry proof,
original exact geometry hash and the normalized descriptor/hash. Trainer
independently validates proof and original candidate against it before pooling.
Artifacts carry only normalized descriptor/version/hash, not raw account or
broker-position data. Runtime recomputes normalized geometry from current
frozen input, requires equality and existing horizon/source/model admission,
then applies the existing TIME_STOP resolver if needed. Legacy rows/artifacts
without the extension keep exact behavior. Explicit unknown extensions may
never fall through as legacy artifacts.

Dataset produces portable rows for supported actions by default; extended
price actions remain exact. Unsupported/malformed portable proof must be an
explicit exclusion, not a silent forecast promotion. Packaging validates the
extension and preserves all existing artifact/JSON/model bounds.

## Invariants and verification

No production candidate-ID/order/execution changes; no altered cost provenance,
independent position identity, net labels, whole-trade purge, 40-group floor,
20 validation groups/two folds, untouched holdouts or risk gates. Every replay
uses original prices/candidate, never reconstructed normalized fills.

RED→GREEN: equivalent affine long/short snapshots and different capture clocks
share a descriptor, train one supported cohort, admit the current concrete
action. Changed R/exposure/horizon/instrument/direction/topology, forged proof,
unknown versions, inconsistent action IDs, ambiguous TIME_STOP, malformed and
nonfinite inputs reject. Extended actions remain exact; old artifacts retain
behavior. Full official Python3.11/WebKit CI and independent final review are
mandatory before merge; exact production readiness/smoke/public/source/math
publication follow. Genuine inputs and statistical effectiveness stay explicit.


## Implemented descriptor/proof schema

The v1 descriptor has exactly `version`, `instrument`, `direction`,
`execution_inputs`, `prices`, `active_risk_barrier_type`, `exposure` and
`candidate_matrix`. Execution inputs and rungs retain original R values.
`prices` has entry=0, original_stop=-1 and normalized current/active barrier/
final take. Known exposure prices (`active_stop_price`, `original_stop`,
`original_take`, `take`) and candidate prices (`stop_price`, `take_price`,
`trigger_price`) use signed original risk. Presence and values of other known
parameters remain unchanged. The matrix is sorted after normalization; unknown
fields/schema, nonfinite arithmetic and noncanonical descriptors reject.
Descriptor validation uses the strict existing geometry validator on unit-risk
geometry without scenario replay, source retrieval or label reconstruction.

The frozen evidence contains original capture/trade identity, instrument/
direction, complete trade geometry and position state, original active candidates,
and the policy manager inputs/policies/selection/risk/gate/input audit consumed
by exact geometry validation. It omits cost models and trade/account context.
Rows independently reproduce the exact and portable hashes and verify concrete
candidate membership. Original entry, stop, remaining exposure and direction
must also agree with the already-required independent position/cost evidence.
Proof/hash validity alone cannot authorize an action absent from the matrix.
Artifacts verify supported actions and TIME_STOP binding presence against their
normalized matrix. Legacy and portable hash namespaces are separate cohorts.

Implemented local verification is recorded in the task report; independent
review, full suite and official CI/release evidence remain integration steps.
