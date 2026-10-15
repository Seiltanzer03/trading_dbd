# AI review correction — release evidence, 15 October 2026

User scope: reported review `ai-944fd92fac764c5f97af`; decision
`decision-b91c4fe28247268c86b95a8a2791`. Original unified-edge plan and handoff
remain unchanged. This is a separate factual addendum.

## Code and checks

- Report correction PR [473](https://github.com/Seiltanzer03/trading_dbd/pull/473),
  then necessary latency follow-up PR [474](https://github.com/Seiltanzer03/trading_dbd/pull/474).
- Final head `535665dfc5f5dbee8d09e20821ab4afe15031db7`;
  merged release `588edd8d9093717848530ece5f9bbcea5bed08ed`;
  identical tree `9534897b216b6d3c6cd62ac76b69a50144b292ce`.
- Final independent review: no Critical/Important findings. One post-review CI
  fixture dependency was corrected and verified without changing product code
  or weakening assertions.
- PR CI [38763049941](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38763049941):
  success, 4099 Python tests passed, 5 skipped, 13 subtests passed; pinned SQLite,
  frontend smoke and real WebKit passed. One existing deprecation warning.
- Additional lattice and unified comparison checks passed.
- Automatic exact-release main CI
  [38763839057](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38763839057): success.
- Latency final PR CI
  [38770095769](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38770095769):
  success,4108passed/5skipped/13subtests, pinned SQLite, frontend and WebKit passed.
  Required mathematical, lattice and frozen-comparison profiles also passed.
- Narrow supplemental review of the newly required latency diff: no
  Critical/Important findings;44tests and2000 extra complete outcome comparisons
  exactly match baseline. Owning200tests and26API/report tests passed.

## Production boundary

Preceding2319 deploy [38764566899](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38764566899)
passed readiness (Q1664ms/3000ms), then failed AI smoke12940ms/12000ms. It
correctly skipped source/model handoffs. Actual LLM succeeded with15.789% weight;
request ai-a550dba363184cf6b23e and review390 retained. CPU profiling identified
scalar execution replay. New code batches masks≤256paths and reuses exact HOLD
drivers within one selection; all gates, seeds and complete cash flows remain.
Pure-host review390 comparison38769672660:4.6478s→2.8036s, full ensemble audit
and independent admission rows exactly equal. No provider call/publication.

Final588edd8 main CI/deploy pending; no full production success claimed yet.
Source/model dispatch is not delivery. Final read-only artifact-loader and
saved-smoke-review evidence pending. Slow cold startup was measured672.989s,
mostly mandatory SQLite/schema verification; preserved, deferred separately.

## Decision and limits

The original report hit a real 6025ms provider timeout. The installed one-call
adapter was present. Fixes preserve a valid structured preference independently
of optional prose, retain a normalized unavailable reason, correct report
authority/counts/blocked-anchor causes, and use aggregate-equivalent Q readiness.
No source clock, risk/utility threshold, six-second provider guard or storage
reserve was weakened. Missing observations and probabilities remain unavailable.
Lower Expected with better CVaR is permitted by the existing bounded protective
utility; no weights were fitted to this trade. No broker execution/ACK occurred.

Deferred cosmetic item: extra EOF blank line in
`tests/test_ai_report_reported_regressions.py`; no behavioral effect.
