# Remaining unified-edge release plan

Base release: main `9a5174241d4e165603ed485417d2f04f87c1427e` (PR #389).
This is a finite follow-up release, not a claim that every source has profitable
predictive value. Risk gates and manual execution remain unchanged.

| Work | Acceptance criterion |
| --- | --- |
| Actual LLM transport | The real provider boundary hides the selected quantitative winner, retains all 12 structured scores, and saves bounded immutable request/response evidence. Receipt time is distinguished from frozen market-input time. |
| Fresh mathematical inputs | A bounded off-host collector attempts all 13 configured instruments, validates completed causal bars, never fills gaps or splices providers, and publishes actual coverage or explicit provider failure. Refit uses the existing finite chronological search. |
| Eight source families | A bounded source bundle contains independently verified actual observations, receipt times and hashes. Every family has a readiness row; missing consensus, broker quotes, validated proxy mapping or fitted effects remain unresolved, not synthetic forecasts. |
| Local integration | Reviews read only a bounded, exact-production-SHA source bundle. No network requests or fitting are added to the review path. Freshness, instrument identity and provenance gates continue to apply. |
| Comparison completeness | Preserve observed event order within a finite export bound. A truncated trace can be used only for a policy that demonstrably terminates within that prefix. Missing continuation and costs remain unavailable. |
| Measured execution costs | An explicitly supplied broker rollover schedule is charged once against the actual remaining quantity on each replay path. Missing quotes remain an explicit limit; no universal broker swap, slippage or latency is invented. |
| Deployed contract coverage | An isolated fixture exercises the twelve-action contract even without an active user position. It does not create user trades, execute orders or masquerade as an observed live trading result. |
| Release | Focused tests, then full Python/frontend/WebKit CI and read-only research workflows on the exact final revision. Merge only after green CI; automatic deploy, readiness, smoke and public HTTP must pass on the merged SHA. |

## Work order

Independent LLM transport, fresh bar collection, family sources and historical
comparison run in parallel. Contracts are integrated sequentially. Tests are not
repeated without a changed implementation or a new failure cause. While final CI
is running, its code is frozen.

## Explicit limits

Public market APIs can fail or restrict availability. A provider denial is not
circumvented. Licensed pre-release consensus, broker-specific carry and calibrated
CFD proxy mappings cannot be manufactured from public candles. Raw macro facts,
correlations, positioning and option-implied distributions are not independently
validated net-action forecasts. Limited evidence does not establish optimal
weights or historical trading profit.

The next actual user review remains the live end-to-end observation; the release
does not open a real position just to obtain it. New calibration/source research
after the bounded release is a separately scoped version.

## Implementation checkpoint (2026-10-02)

The actual provider-route integration now retains twelve scores, masks the quant
winner, and journals bounded canonical request/input JSON plus the original model
response, with hashes. The
local family-source reader is integrated before scoring/provider projection and
requires exact SHA, bundle-capture chronology, review freshness and existing
proxy gates. Both source workflows collect off-host; deployment explicitly checks
their dispatch outcomes. An isolated production smoke fixture exercises all
twelve actions and journal integrity without changing real trades.

The retained historical sample's common evaluable cohort is four distinct trades,
not a statistically adequate profitability study. Missing rollover quotes remain
unknown. Optional measured schedules now affect the same remaining-quantity
timelines used by candidate and observed replay, with no duplicate charges.

Local full Python check: 1,879 passed, one unchanged SQLite/WAL sparse-backup
baseline failure on the local Python 3.12 environment; that baseline was already
reproduced before these changes. It is not suppressed or relabelled green.
Production Python 3.11 CI, real WebKit and exact-head source/comparison workflows
must be green before merge. Final provenance hardening receives additional
focused checks on the exact release revision.
