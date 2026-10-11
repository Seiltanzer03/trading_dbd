# Historical family forecasts — finite implementation

Spec: `docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md`, sections4–7; user's
2026-10-11 instruction to train on existing history and make actual forecasts
work without waiting for new trades. Execute inline using executing-plans.

Baseline: local c1c68f7 / main87725b627196c1368522d7397ff78c9cbe0832f4,
identical tree c50333894dc836f9be07f2b89220f49aaeedad12. Existing isolated
worktree and dependencies reused; no unchanged full-suite repeat.

## Architecture and boundary

Extend the existing off-host mathematical publication with separately identified
family market-response models. Forecast direction and movement from genuine
market history, then use bounded preferences inside the existing mathematical
budget. The common evaluator continues computing each action's economics and
risk. A market forecast is not a trained action-profit estimate and does not
require broker fills/cost history. Existing net-action models remain intact.

Session: instrument-local clock/DST periodicity learned only on observed bars;
does not assert an exchange or CFD opening calendar. Intermarket: synchronized
Coinbase USD completed returns as related-market drivers, never target-price
equivalence. Use the same frozen5m/lag feature computation in history and runtime.
Other six families may use genuine previously captured feature histories through
the same chronological trainer; no substitution of price bars for missing macro,
news, tape, positioning, carry or option observations.

One fixed small search over15/30/60/120m, nonoverlapping observed targets,
purged chronological validation and untouched final test. Working admission is
the already authorized historical manual policy; no relaxation of G.1, fake
probabilities, forced positive edge, automatic orders, or new broker-cost work.

## Task1 — real historical training

- Write failing tests for causal/nonoverlapping labels, DST/session history,
  exact intermarket windows, generic observed family histories, no synthetic
  training inputs, frozen test boundary and explicit missing/negative results.
- Implement bounded `edge_family_market.py` training with recorded sources,
  scaling, validation, hashes, target roles and no invented net returns.
- Run actual downloaded exact-SHA history, retain factual matrix in docs.

## Task2 — runtime and decision integration

- RED tests for exact publication/model hash, fresh matching source clocks,
  horizon/direction, synthetic and proxy rejection; baseline survives failures.
- Extend existing collector with aligned completed5m related returns; attach
  bounded local model context before current LLM, no request-time download/fit.
- Include source facts, trained forecasts, actual pool contributions and family
  ablations in the existing eight-family UI/report; retain one decision path.
- Extend existing math workflow, publisher and profile tests as necessary.

## Task3 — release

- One final independent read-only review; one fix pass for material findings.
- Mandatory final-tree Python/frontend/WebKit CI, then authorized merge/deploy.
- Actual exact-SHA server/readiness/smoke/public and math/source publication
  receipts; inspect trained-model counters, not workflow dispatch success.
- Stop at this finite boundary. Record any genuinely absent histories explicitly;
  do not describe all eight as empirically ready if observations are missing.

## Review focus

Future labels/features, purging and test-based model selection; historical/public
bar availability versus actually recorded receipts; fitted feature window versus
runtime window; target/proxy/venue and DST identity; malformed model isolation;
snapshot byte budgets; real family contributions and ablations; descriptive
probability versus action Expected/CVaR; no simulated evidence or forced weights.
