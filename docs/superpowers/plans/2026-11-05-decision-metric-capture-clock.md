# Current metric capture clock

Execute inline. Productionb9234 is accepted, but runtime metrics oscillate26→3 while the same actual completed native minute bars and cached configured QQQ observations produce41 fields in an independent read-only capture. Diagnostic42028041232/job127247095022. `DecisionMetricRuntime.refresh` uses the cached canonical tick clock despite copying later received feed inputs, then overwrites the copied quote with the old tick quote.

Scope: choose the actual bounded frozen input receipt clock for the existing metric producer; use the quote already copied in that same capture. Canonical execution quote, policy pricing,92definitions, source clocks, model gates and broker receipts remain unchanged. No new source, cache restamping, training or live trade action. Trade142 has unresolvedCLOSE_10 and must not be ACKed.

1. Reproduce lagged canonical tick versus received minute/option inputs and verify the later context cannot be consumed by an earlier snapshot. Retain wrong-instrument, stale/future input and writer-contention tests.
2. Correct only the producer capture boundary; adjust unit fixtures to control the actual capture clock, not the removed cached-tick dependency.
3. One final independent review of this narrow diff, owning checks and mandatory full final-tree CI; authorized PR/merge/normal exact-SHA deployment.
4. Verify repeated runtime samples across source refreshes and original option/price timestamps, exact-SHA publications and live quote latency. Do not call a source probe or a single positive runtime sample complete. Full current LLM still requires actual broker reconciliation of the existingCLOSE_10; no fabricated fill.
