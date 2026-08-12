# G.1S Short-Horizon Physical Learning

## Why this layer exists

Native option-Q and intraday trade forecasting answer different questions.  G.1C
keeps the option market's risk-neutral terminal distribution at the source option
expiry.  G.1S instead measures what the traded market actually does over fixed
15/30/60/120/240 **trading-minute** horizons.

The existing passive collector already froze these fixed-horizon T0 records before
future prices were known. G.1S therefore does not create a second market collector;
it incrementally materializes the existing prospective ledger and consumes only
outcomes produced by the independent passive resolver.

## Evidence admission

A row may become G.1S training evidence only when all of the following hold:

- fixed `horizon_kind=fixed_trading_time`;
- supported G.1S horizon;
- not retrospective replay;
- background collector origin;
- current passive feature/forecast contracts;
- source `evidence_eligible=1`;
- direct T0 target price;
- T0 price quality >= 0.90.

Old rows that demonstrably existed prospectively before G.1S activation are
classified `PREEXISTING_PROSPECTIVE`. Rows after activation are
`LIVE_PROSPECTIVE`. Test/replay/manual observations cannot silently become training
evidence.

The frozen feature and forecast bytes are hashed at materialization. Resolution
revalidates those hashes before any label is admitted.

## Outcome clock

G.1S V1 horizons:

- H15
- H30
- H60
- H120
- H240

The source passive resolver owns future-price truth. G.1S never reaches into a
live feed to reconstruct an old outcome. It imports only independently resolved
source rows and derives terminal log return, UP/DOWN/FLAT and observed MFE/MAE.

Option expiry is irrelevant to this fast outcome clock.

## Dependency and effective N

Overlapping horizons are not treated as independent. Observations are grouped by
instrument, horizon and non-overlapping horizon-sized time bucket. Fit readiness
uses dependency groups rather than raw row count.

The initial shadow-fit gate is:

- raw resolved >= 120
- effective N >= 60
- UP >= 20
- DOWN >= 20
- >= 3 trading days

These are research gates, not promotion gates.

## Baselines and challengers

Every horizon reports at least:

- constant 0.5 direction baseline;
- chronological prior-only base-rate baseline.

The first learned challenger is deterministic L2-regularized logistic regression.
V1 freezes two feature sets:

- `PRICE_ONLY_V1`
- `PRICE_OPTIONS_V1`

The option feature challenger exists specifically to answer whether frozen option
state improves short-horizon loss over a simpler price/volatility model. No neural
network or automatic model selection is introduced.

Historical chronological diagnostics use purge before the test segment and are
explicitly labelled non-prospective. True prospective OOS metrics use only model
artifacts created before a future observation T0. There is no retrospective
prediction backfill.

## Real-trade relevance

G.1S-T links a real trade only to a same-instrument G.1S observation that already
existed before trade entry and is within the frozen maximum-age contract. Real
trades are primarily a relevance/validation set, not the sole training source.

This allows future research to answer separately:

1. does the market model beat simple baselines;
2. do option features add anything;
3. is that edge present around the user's actual entries;
4. does using it improve trade/management economics.

## G.1-M.1 local feedback

Terminal G.1-M remains the economic truth for the full trade. G.1-M.1 adds frozen
15/30/60/120 trading-minute windows after each management decision and reuses the
same authoritative `counterfactual_replay` on the observed path truncated at the
window.

Therefore the system can distinguish:

- `LOCAL_DECISION_QUALITY`: e.g. a partial close protected the next 60 minutes;
- `TERMINAL_MANAGEMENT_EDGE`: e.g. that same partial close later surrendered too
  much terminal upside.

The two are never pooled into one edge claim.

## Slow option-Q diagnostics

`/api/research/g1/q/audit` classifies native-expiry Q attempts as:

- `NOT_DUE_YET`
- `RESOLVED`
- `DUE_BUT_NOT_RESOLVED`
- `RESOLUTION_BLOCKED`
- `CONTRACT_REJECTED`

A Q observation past its target for which authoritative market coverage exists but
resolution is still pending is a contract failure, not normal waiting.

## Runtime scalability

G.1E.2 makes status/read APIs bounded. Request paths do not launch full-history
G.1A/B/C scans. The system exposes materialized short-horizon status and fast-gates
G.1C refitting when even the minimum required Q sample is impossible.

Storage health is decoupled from research health; `/api/system/storage/status`
does not call Q aggregation.

The authoritative SQLite database is published explicitly via
`/api/system/database-authority` and `database_authority.json`, so backup and
operations do not guess among multiple `trades.db` files.

## Authority

All G.1S and G.1-M.1 outputs are research-only:

- production authority: false
- automatic execution: false
- policy promotion: false
- edge claim: false
- OOS validated: false until a later evidence-gated phase proves it

A future G.2 promotion contract is required before any learned output may alter the
deterministic production decision policy.
