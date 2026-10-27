# Research-backed reference fallback implementation plan

> **For agentic workers:** root executes inline in the existing isolated worktree. One focused regression profile, one final independent review, one mandatory release pipeline; no repeated old checks without a failure.

**Goal:** Make existing manual research calculations usable on all ten noncrypto configured reference feeds, and supply an actual range-volatility estimate when an option input cannot be observed.

**Architecture:** Reuse the already frozen configured reference bars and their strict identity/hash/receipt proof if the native unshifted price lane is unavailable. Preserve existing preliminary budget and price-path dependence. Compute the Rogers–Satchell estimator from twelve completed 5m bars, expose its actual units/sample and assumptions to the LLM and audit. It is realized historical volatility, never fabricated implied volatility, dealer positioning or a fitted action forecast.

**Tech Stack:** existing Python modules, no new dependency/provider/DB schema.

**Spec:** User2026-10-27 asks to finish urgently, use published research where direct inputs cannot be obtained, reduce redundant checks; existing AGENTS.md risk/cost/source truth requirements remain. Baseline accepted8a4896f9c35664815f54ccdb06b0432bdd759bd2, localff46db7 identical treeae072e00.

## Global constraints

- Noncrypto first; do not add crypto-specific research.
- Keep original clocks, reference hashes/identity/current complete bar and900s bound,60KB snapshot/8KB research context,0.25 preliminary policy factor and existing shared expert budget.
- No request-time network/fitting, extra paid LLM probe, source-generation bypass, simulated fills, unknown consensus or broker charges presented as observed.
- Monthly AQR evidence is not M15 calibration. Actual book OFI requires bid/ask events; candle pressure remains a price proxy. Future OOS/net profit remain unproved.

## Research sources verified2026-10-27

- Rogers–Satchell(1991), DOI10.1214/aoap/1177005835, author paper https://www.skokholm.co.uk/wp-content/uploads/2016/01/R_Satchell_HLOC.pdf , equations2–3: log-high*log(high/close)+log-low*log(low/close). Apply its basic uncorrected estimator per bar; no made-up tick-mesh correction, annualization or accuracy. Brownian-with-drift assumption and discretization bias disclosed.
- AQR original TSMOM data https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data :12month lookback/1month holding. Existing15m momentum remains an adapted hypothesis, not AQR-proven performance.
- Cont/Kukanov/Stoikov https://arxiv.org/abs/1011.6402 : OFI uses best bid/ask events; cannot recover it from OHLC.
- FRBSF USMPD https://www.frbsf.org/research-and-insights/data-and-indicators/us-monetary-policy-event-study-database/ : real historical30m statements/70m conferences returns and rate changes. Useful new research data, but simultaneous event-window returns cannot be used as predictors of those same returns or relabelled as missing pre-release consensus. No new importer/training cycle in this finite package.

## Review focus

- A valid unrelated symbol, altered reference hash, receipt after original freeze or stale bar must not vote.
- Cached quote refresh cannot replace or retime the original research inputs.
- Range estimator is finite, per5m log-return units, realized not implied; no false annualization/confidence.
- Existing native context and genuine trained models retain their behavior/priority.
- Prompt, compact audit and bounded frozen snapshot retain measurement/source roles.

### Task1: Reference source bridge and estimator

- [x] Reproduce missing research fallback on all ten valid configured reference fixtures and missing estimator, plus original freeze vs older UI tick.
- [x] Add bounded reference/native input selection in research_working_context.py, validating full reference then retaining existing runtime proof; do not shift execution quote or native model identity.
- [x] Compute sigma5m=sqrt(mean(log(H/O)*log(H/C)+log(L/O)*log(L/C))) for12 completed bars. Expose option.realized_range_volatility_5m only as manual_preliminary/context_only, with source price_path lineage; leave forecast_available false.
- [x] Preserve new estimator in prompt and compact audit and explain roles in report. Negative clock/hash/symbol/gap cases and literal numerical estimator checks.
- [x] Run one focused owning profile. Commit only this package, preserve unrelated archive.

### Task2: Finish once

- [x] One final read-only independent review; fix actual blocking findings without re-review.
- [ ] Final mandatory full PR CI→exact-tree merge→mainCI→automatic deploy/all7core plus actual reference-backed research receipt. No redundant standalone smoke/retraining.
- [ ] Record finite technical completion and honest external evidence limits. Prior8a live proof:4XAU forecasts(option/position/session/carry), option/carry qualified; later2 when option inputs unavailable; localfeedback1108→1109/errornull. Counts remain capture-specific.

### Confirmed provider input correction (same final package)

Read-only existing trace(headc4d47b20, job122197299192):48125 input bytes/17581 prompt tokens; real response285tokens returned after6.743s, but6s provider deadline had already discarded it. Preserve6s provider/12s route/four-second completion reserve. Share only exactly identical repeated feature-provenance dictionaries through an explicit lookup table in the provider wire. All source clocks/values/masking and archived input hash remain exact; add prompt instruction to resolve references. No response reuse or synthetic LLM answer. Round-trip, actual independent input integration, size reduction and future-clock validation regressions before code. Live latency improvement is measured after release, not promised from fixtures.
