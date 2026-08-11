# G.1-M Management Edge Engine

## Purpose

G.1-M measures whether management of an **already-open real position** adds economic value versus frozen alternatives. It is a research layer only. It does not modify AI Verdict, production policy, stops, takes, position size or broker execution.

The core question is:

> From the information and real position state available at T0, did the frozen production management action add value relative to HOLD, the original STOP/TAKE plan, and EXIT NOW on the market path that actually followed?

## Source of truth

G.1-M does not create a second execution simulator. It consumes:

- immutable `decision_snapshots` for T0;
- `management_decisions` for frozen recommendation/execution state;
- `decision_path_points` for the observed post-T0 path;
- authoritative `decision_replays`, which already use `execution-simulator-f0-v1` first-crossing STOP/TAKE/BE/ladder semantics;
- `position_management_events` as the real economic position ledger.

Existing decision records captured before G.1-M activation are materialized only as `RESEARCH_BACKFILL`; they cannot enter prospective policy-edge evidence.

## Observation contract

`g1m-management-observation-v1` is an immutable T0 record. It freezes:

- review/trade identity;
- source snapshot SHA256;
- production policy and policy version;
- current price/R;
- remaining position fraction;
- PnL already realised before T0;
- original/active stop and take geometry when available;
- measurement/policy/execution eligibility.

Origins are explicit:

- `LIVE_PROSPECTIVE` — eligible for policy-edge evidence if all contracts pass;
- `RESEARCH_BACKFILL` — visible but never prospective evidence;
- `TEST` — never production evidence.

The first persisted G.1-M activation timestamp is immutable and survives restart, so old snapshots cannot become prospective merely because the server was rebooted.

## Frozen action set

V1 deliberately uses a small interpretable action set:

- `HOLD`
- `CLOSE_10`
- `CLOSE_25`
- `CLOSE_50`
- `EXIT`
- `ORIGINAL_PLAN`
- `PRODUCTION_POLICY` (an immutable alias of the action actually recommended at T0)

For HOLD/partial close, continuation is the current authoritative managed strategy (future ladder/BE/STOP/TAKE). EXIT has no continuation. `ORIGINAL_PLAN` keeps the already-realised pre-T0 economics fixed and applies only original -1R STOP / final TAKE to the remaining opportunity, with no new BE/ladder management.

No future decision tree is optimised. Every T0 action has one frozen continuation contract.

## Ex-ante vs ex-post

T0 policy metrics are copied from the frozen policy-manager payload when available. They are never recomputed with a later model.

After `decision_replays` resolves, G.1-M records realised-path outcomes. This separation allows later analysis of whether a bad result came from management logic or from the scenario/forecast model.

## Economics

For each resolved policy:

`management_incremental_R = terminal_R - realised_R_before_T0`

Primary management value added:

`MVA_vs_HOLD = terminal_R(policy) - terminal_R(HOLD)`

Also stored:

- MVA vs `ORIGINAL_PLAN`;
- MVA vs `EXIT`;
- descriptive realised regret versus the best frozen comparator;
- downside saved versus HOLD;
- upside sacrificed versus HOLD.

`realized_best_action` is descriptive hindsight only. It is not a training target or production authority.

## Policy, execution and compliance

Recommendation and user action are deliberately separate.

- **Policy edge**: result of the frozen production recommendation versus comparators.
- **Execution edge**: result corresponding to what was actually executed when execution is known.
- **Compliance attribution**: difference between the frozen recommendation and the observed user action.

If production recommended `CLOSE_50` and the user marked it `recommended_not_executed`, production policy is still evaluated as CLOSE_50 while actual action is attributed to HOLD for that decision point. The policy is not falsely recorded as executed.

## Dependency and effective N

Repeated reviews of one trade are dependent observations. Under `g1m-trade-dependency-weight-v1`, all eligible observations within a trade share total weight 1.0. Consequently repeated decisions cannot manufacture independent evidence.

Every headline reports raw observations, unique trades and effective N.

## Readiness

`g1m-oos-readiness-v1` starts conservatively:

- raw prospective resolved observations >= 200;
- unique trades >= 100;
- effective N >= 80;
- >= 3 temporal periods;
- >= 20 positive MVA outcomes;
- >= 20 negative MVA outcomes;
- no critical contract errors.

States are only:

- `NO_EVIDENCE`
- `COLLECTING`
- `INSUFFICIENT_EVIDENCE`
- `DESCRIPTIVE_ONLY`
- `READY_FOR_OOS`

`READY_FOR_OOS` is **not** proof of edge and is **not** permission to modify production. It only means the dataset is mature enough for a future purged walk-forward management validation phase.

## Authority boundary

All G.1-M APIs publish:

- `research_only=true`
- `production_authority=false`
- `auto_execution_allowed=false`
- `policy_promotion_allowed=false`
- `oos_validated=false`
- `edge_claim_allowed=false`

No G.1-M statistic is used by AI Verdict in V1.

## Durability

G.1-M ledgers live in the same durable SQLite source of truth and are added to the verified backup manifest identity. They survive restart and are included in backup/restore verification.

## APIs / UI

Read-only endpoints:

- `/api/research/g1/management/status`
- `/api/research/g1/management/observations`
- `/api/research/g1/management/pending`
- `/api/research/g1/management/resolved`
- `/api/research/g1/management/policies`
- `/api/research/g1/management/cohorts`
- `/api/research/g1/management/edge`
- `/api/research/g1/management/decision/{observation_id}`

Research cockpit: `/management-edge`.

## Next phase

G.1-M does not promote anything. Once prospective management evidence is mature, the next separate phase is purged walk-forward / OOS management-policy validation.
