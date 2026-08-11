# G.1A Prospective Dataset Contract

## Versioned contracts

- Dataset: `g1-prospective-dataset-v1`
- Cohort: `g1-cohort-v1`
- Horizon buckets: `g1-horizon-buckets-v1`
- Effective N: `effective-n-nonoverlap-v1`
- Cut manifest: `g1-dataset-cut-manifest-v1`

The source observation contract, F.3.2a measurement runtime contract, G.1
eligibility contract and future calibrator contracts are distinct provenance
layers.

## Eligibility

Forecast-evaluation eligibility requires a current F.3.2 source record, current
F.3.2a runtime, resolved prospective background origin, no replay, evidence
eligibility, direct T0 price, and a clean authoritative no-lookahead terminal
outcome at or before the target with the current terminal-age tolerance.

Terminal Q→P eligibility additionally requires a frozen mathematically valid
risk-neutral terminal Q CDF, valid PIT, option-native expiry horizon, exact
capture-to-expiry clock alignment, and explicit Q source/target/proxy-transform
provenance. Terminal Q is never treated as first-passage Q.

Fixed-horizon observations can be forecast-evaluation eligible while remaining
Q→P ineligible.

## Horizon buckets

Fixed trading horizons use their exact configured minute bucket. Option-native
expiry observations use deterministic ACT/365 DTE buckets:

- `LT_1D`
- `1D_3D`
- `3D_7D`
- `7D_14D`
- `14D_30D`
- `GE_30D`

## Cohorts

A canonical JSON cohort includes instrument, forecast family, horizon kind and
bucket, probability measure, Q source and target instruments, relation,
proxy transform, Q/expiry/runtime contract provenance, then receives a SHA-256
cohort ID. Regime and session remain separate strata to avoid premature sample
fragmentation.

## Dependency and effective N

`dependency_group_id` is the immutable passive `anchor_group_id`. Seven horizons
from one T0 anchor are not seven independent decisions.

For each cohort, anchors are collapsed to their observed `[captured_ts,target_ts]`
information windows. Within each instrument, the versioned conservative
algorithm sorts windows chronologically and increments effective N only when the
next captured timestamp is not earlier than the end of the last accepted
window. Aggregate effective N first collapses all horizons/families from the
same instrument+anchor, preventing multi-horizon inflation.

Reports expose raw task membership N, unique observation N, unique anchor N and
effective N separately.

## Source hashes and mutation

Membership is materialized only after an observation leaves pending state. A
canonical post-resolution source record SHA-256 is stored with the immutable
membership. Any later mismatch raises persistent `SOURCE_MUTATED` evidence and
excludes the observation from new pristine cuts; the stored admission record is
never silently rewritten.

## Dataset cuts

A cut contains only forecast-evaluation-eligible observations whose `captured_ts`
and `resolved_ts` were both known by the requested cutoff. Member IDs, source
hashes, cohort/dependency metadata and `UNASSIGNED` roles form a deterministic
canonical manifest SHA-256. Cut creation is transactional; partial cuts are
rolled back. Cuts and members are immutable.

G.1A does not assign TRAIN/VALIDATION/TEST. That belongs to G.1D chronological
purged walk-forward validation.

## Authority

G.1A is `research_only`. It cannot publish physical P, fit a calibrator, replace
a production forecast or promote a policy regardless of sample count.
