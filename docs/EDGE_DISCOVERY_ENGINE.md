# Edge Discovery Engine v1.1

EDE is an offline, research-only layer for finding interpretable conditional
market edge. It does not run in the web request path and cannot change the AI
verdict, a production model, or trading authority.

The logical feature registry routes features to existing T0 contracts instead
of reimplementing them. Missing values remain unavailable; staleness and source
quality are separate from the market value. Current 60-day P1B bars are labelled
discovery data, never pristine OOS.

The primary comparison is now incremental. `GLOBAL_RET5_PERSISTENCE` is fitted
on the complete causal train cut. A conditional rule may restrict only its own
train rows and the evaluation subset. Global and conditional ret5 predictions
are then scored on the exact same filtered validation/test rows. Constant 0.5,
causal base rate and ret15 momentum remain diagnostic sanity baselines, but no
primary gate uses them instead of global ret5.

The bounded audit evaluates ret5 persistence through at most three
conditions drawn from asset/family/session, train-only volatility and trend
quintiles, aligned cross-asset confirmation, and breadth. Each outer fold runs
its own purged inner discovery. Outer tests only evaluate selected rules.
Benjamini-Hochberg correction is applied to actually tested hypotheses. An
inner rule must pass both the sample gate and `q <= 0.10` before outer
evaluation. Interesting FDR failures remain visible as
`EXPLORATORY_FDR_FAIL` and can never become historical candidates.

Candidate and live-shadow ledgers are append-only. Hypothesis identity is
separate from evaluation identity: a stable signal/horizon/template receives
one `hypothesis_id`, while each new dataset/source hash receives a new immutable
`evaluation_id`. The reviewed ledger lives as the split gzip-compressed JSONL
files `docs/research/EDGE_DISCOVERY_NEGATIVE_REGISTRY.jsonl.gz.part-*` and can
be advanced only through a normal code-review PR. Actions has read-only
repository permission; its JSONL output is an append delta for review, not the
permanent registry. Splitting and compression change storage only; concatenating
and decompressing the parts restores the same append-only JSONL event stream.
Thirty-day artifacts are convenient report copies only. A historical candidate must
be explicitly frozen before future predictions can be written; outcomes cannot
be attached before their target timestamp. Rejected candidates remain recorded.

`ProspectiveFeatureAdapter` reads the existing immutable `g1s_observations` and
joins `g1s_resolutions` only after `target_ts`. It does not create another
collector. Every admitted feature must have `asof <= T0`; missing options remain
missing and no current snapshot is reconstructed backwards. Existing V3
captures supply price/volatility, IV, IV/RV, skew, term structure, Greeks, GEX,
cross-asset, macro and wavelet context. Sequential real T0 observations supply
causal velocity, acceleration, rolling rank/z-score and direction consistency
when enough history exists.

Market and family breadth are leave-one-out. The current instrument is removed
from each aggregate, and peer count, coverage, as-of and staleness metadata are
preserved. A missing external peer stays unavailable/neutral; it is never
replaced by the instrument's own return.

Heavy audits run on GitHub Actions. The default workflow fetches a fresh real
5m/60d set into a temporary research database, which becomes immutable for the
run and never touches production credentials. The CLI can alternatively read a
separately supplied offline P1B copy in SQLite read-only mode. Option, GEX, IV,
skew, Greek and derivative features are inventoried,
but the first P1B audit reports them unavailable unless genuine immutable T0
history has sufficient coverage. Ready prospective features replace a
deterministic tail of the original template set without increasing the hard cap
of 248 templates or three conditions. Availability routing uses no outcomes.
Synthetic or current-snapshot backfill is forbidden.
