# G.1 Research Roadmap

## Core principle

Seiltanzer measures edge before increasing decision authority. Every learned
component remains research/shadow until a separate prospective OOS contract proves
that it adds value over frozen baselines.

The research system now has **two clocks**:

1. **Fast physical-market learning (G.1S)** — frozen 15/30/60/120/240 minute
   observations resolve from the real future market path and provide feedback that
   matches intraday trade horizons.
2. **Slow structural option-Q learning (G.1C)** — native option-expiry risk-neutral
   Q remains separate and resolves only at its mathematically correct expiry before
   Q→P calibration.

Neither clock automatically changes AI Verdict or production management.

## Current roadmap

| Phase | Purpose | State |
|---|---|---|
| F.3.2a | strict terminal measurement / PIT / no-lookahead | DONE |
| G.1A | immutable prospective dataset integrity | DONE |
| G.1B | baselines, Brier/log-loss/reliability/PIT | DONE |
| G.1B.1 | real option-Q capture and capability diagnostics | DONE |
| G.1C | slow native-expiry Q→P shadow calibration | ACTIVE · COLLECTING |
| G.1E-0 | durable SQLite, verified backup/restore | DONE |
| G.1E | Intelligence Lab | DONE |
| G.1E.1 | production/runtime cleanup | DONE |
| **G.1E.2** | bounded/materialized research runtime | **CURRENT** |
| **G.1S** | short-horizon physical learning | **CURRENT** |
| **G.1S-T** | validation on real user trades | **CURRENT / EVIDENCE-GATED** |
| G.1S.OOS | purged prospective short-horizon OOS | EVIDENCE-GATED |
| G.1-M | terminal management-edge measurement | ACTIVE · COLLECTING |
| **G.1-M.1** | 15/30/60/120m local management feedback | **CURRENT** |
| G.1-M.OOS | purged management-policy validation | EVIDENCE-GATED |
| G.1D | terminal option-Q purged walk-forward OOS | EVIDENCE-GATED |
| G.2 | policy promotion research | FUTURE |

## G.1S evidence lifecycle

`COLLECTING → EARLY → SHADOW_FIT_ALLOWED → prospective shadow predictions → G.1S.OOS`

Initial shadow-fit gate is versioned and conservative:

- raw resolved >= 120;
- dependency-adjusted effective N >= 60;
- positive >= 20;
- negative >= 20;
- >= 3 trading days.

G.1S uses chronological/purged evaluation only. Random shuffle is forbidden for
edge claims because horizons overlap in time.

Real user trades are primarily a **relevance/validation set**. Market observations
provide learning volume; trades answer whether that market forecast actually
helps the user's entries and holding horizons.

## G.1C / G.1D remain unchanged

Native option-expiry Q is not shortened merely to obtain faster labels. G.1C may
fit only from strict G.1A `q_to_p_eligible` observations. G.1D readiness remains:

- raw >= 200;
- effective N >= 100;
- positive >= 30;
- negative >= 30;
- >= 3 temporal periods;
- >1 expiry cluster;
- no critical contract errors.

G.1D starts only when the evidence contract says it is ready. G.1S may reach its
own OOS stage earlier without weakening G.1C.

## G.1-M and G.1-M.1

G.1-M remains the terminal economic truth for management decisions: production
policy versus HOLD / partial exits / EXIT / ORIGINAL PLAN on the complete realized
trade path.

G.1-M.1 adds a separate diagnostic clock at 15/30/60/120 trading minutes. It may
show that a defensive action helped the next hour while still hurting terminal
upside. Therefore local decision quality never replaces terminal management edge.

## Production authority boundary

For G.1C, G.1S and learned G.1-M components:

- `production_authority = false`
- `auto_execution_allowed = false`
- `policy_promotion_allowed = false`
- `edge_claim_allowed = false`

A future G.2 promotion phase must explicitly decide whether a validated model may
influence deterministic production policy, on which instruments/horizons/regimes,
and with what fail-closed rollback rules.
