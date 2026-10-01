# Comparison on frozen actual reviews

`unified-edge-comparison` runs on the PR's exact head SHA or an explicitly
dispatched immutable SHA. It exports at most 32 existing decision snapshots from
the latest 512 records, distributing the sample across instruments and capture
times. Missing instruments are listed; this sample does not establish results
for all configured instruments.

Production only performs read-only, bounded SQLite queries (25-second progress
limit, 2 MB per snapshot/replay, 6,000 path points per review). The export runs
within a read transaction. No backup, fitting, new provider requests, active LLM
calls, publication, database mutation or production configuration changes occur.
The runner receives the stored snapshot string and SHA separately from retained
post-decision path points and replay records. Future observations never enter
the voting input. Invalid SHA, identity or post-capture observation timestamps
reject a review.

The comparison uses balanced, llm20, quant100 and the actually stored production
choice. It reuses `llm_shadow_decision` from each snapshot. Old single-policy
shadows are explicitly identified as quant-anchored sparse opinions, rather
than independent fresh LLM evidence. No missing family models or signals are
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
