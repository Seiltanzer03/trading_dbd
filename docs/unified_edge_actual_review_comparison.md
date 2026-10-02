# Comparison on frozen actual reviews

`unified-edge-comparison` runs on the PR's exact head SHA or an explicitly
dispatched immutable SHA. It exports at most 32 existing decision snapshots from
the latest 512 records, distributing the sample across instruments and capture
times. Missing instruments are listed; this sample does not establish results
for all configured instruments.

Production only performs read-only, bounded SQLite queries (25-second progress
limit, 2 MB per snapshot/replay, 6,000 path points per review). The export runs
within a read transaction. It queries the **stored** `horizon_minutes`, not an
assumed intraday duration: historical horizons can span days. Every observed
point through that horizon is retained, plus the first real observation after
it to bracket an interpolated endpoint, within the same 6,000-point bound.
Long paths remain an explicitly truncated chronological prefix; they are never
downsampled, because dropping intermediate points can change stop/BE/take order.
No backup, fitting, new provider requests, active LLM
calls, publication, database mutation or production configuration changes occur.
The runner receives the stored snapshot string and SHA separately from retained
post-decision path points and replay records. Future observations never enter
the voting input. Invalid SHA, identity or post-capture observation timestamps
reject a review.

The comparison uses balanced, llm20, quant100 and the actually stored production
choice. It reuses `llm_shadow_decision` from each snapshot. Independence requires
the explicit `independent-llm-transport-v1` selection-masked input contract,
matching frozen capture clocks and hashed original provider input/response.
The presence of `policy_scores` alone cannot retroactively establish independence.
Older shadows lacking that contract are labeled accordingly rather than described
as independent fresh LLM evidence. No missing family models or signals are
created: the report retains component availability, lineage and missing family
reasons from the frozen snapshot.

Two separate sections have different meanings:

* Model scenarios report selected Expected/CVaR and intervention frequency.
  They describe the current comparison model, not historical profit.
* Observed-path replay applies frozen candidate parameters and explicit cost
  estimates to held-out measured R points. It includes stop/take modifications,
  spike triggers and time stops using the shared execution simulator. Time stops
  use actual timestamps, including irregular sampling. Barrier fills between
  observations use piecewise-linear interpolation without slippage or impact;
  the maximum observation gap is reported. Retained closed-trade endpoints are
  marked separately from complete forecast horizons. An extended action needing
  unobserved continuation is unavailable, not a zero-profit result.

A truncated prefix is usable only when the action settles within it: an
observed stop/take/BE barrier, an observed TIME_STOP deadline, or full EXIT at
the measured capture price. EXIT does not need a later tick, but does need the
actual capture observation and explicit frozen execution costs. A later stored
original-trade resolution never justifies settling a nonterminal truncated
counterfactual at the last retained price. Partial immediate closes still need
a resolved or fully observed continuation for their remaining exposure.

An explicitly supplied, causal broker rollover quote is replayed against the
same execution quantity/timestamp timeline, including ladder/spike fills and
exact TIME_STOP deadlines. Immediate full EXIT has no future rollover exposure;
partial closes scale it by remaining quantity. The quote must cover the frozen
horizon, identify instrument/currency/risk units, and declare event ordering and
whether base costs already include it. Invalid declared quotes fail closed.
Absent quotes remain explicitly unavailable (`broker_rollover_cost_r=null`),
not confirmed zero carry; `net_cost_scope` identifies that such reported net
figures include only the existing frozen execution-cost estimate.

Observed aggregate comparisons use one common complete cohort for every scheme,
and report descriptive lower-tail CVaR, paired delta against quant100 and the
fraction of interventions that did not improve net replay against HOLD and
against quant100, separately. Lack of net gain does not establish that a risk
reduction was useless. Several
reviews may belong to one trade; distinct trade counts and this dependency are
explicit. The sample is unsuitable for independent statistical or causal claims.
Original-position net outcomes require explicit remaining size, prior gross R
and known prior realized costs (`realized_costs_status=AVAILABLE` with
`realized_costs_r_weighted`). No-prior-fill positions may explicitly declare
costs `NOT_APPLICABLE`. Otherwise total net stays null. Prior gross plus future
modeled net has a separate, explicitly mixed-basis diagnostic field. The older
stored replay's `net_realized_r` uses that mixed basis; comparisons against it
are labeled accordingly, rather than validating ledger net profit.

Settled ledger profit, verified fees, slippage, missed executions and continuations
after actual trade closure are not exported. Historical economic completeness
therefore remains **unavailable**, with null ledger profit. The workflow produces
review artifacts only and cannot authorize automatic promotion or change weights.

Run locally against an actual exported file:

```bash
python -m scripts.run_unified_edge_comparison \
  --reviews unified_actual_reviews.json --output unified_edge_comparison.json
```

The exporter reads `SSH_PASSWORD` from its environment, never a command-line
password. All expensive scenario repricing occurs on the runner.

## Actual retained sample re-run (2026-10-02)

The same previously exported 32 actual reviews (263 eligible metadata records,
four observed instruments: NAS100, UK100, USDCAD, XAU) were re-run locally with
the terminal-prefix rule. No additional production observations were fetched.
The common fully evaluable cohort increased from **one to four reviews/four
distinct trades**: three additional retained prefixes contain real terminal
barrier resolutions. The balanced, llm20 and quant100 choices in that cohort
remain HOLD, and their paired difference is zero; this is not evidence that a
particular weight scheme is more profitable. Their remaining unavailable
reviews are 20 missing current-model candidate selections from historical
contracts, seven missing actual capture/future paths, and one nonterminal
truncated prefix. Missing costs/geometry in those old contracts are not imputed.
Verified ledger profit remains unavailable. A fresh export will exercise the
new horizon-bounded query; local re-analysis of the old export cannot recover
points that were not exported.
