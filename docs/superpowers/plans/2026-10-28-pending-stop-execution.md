# Pending stop execution and economic tradeoffs

Baseline: accepted main12b0518 (PR541). User asks to address the supplied6/10
review quickly. Native implementation; existing isolated worktree, one final
independent review, mandatory exact-tree CI and automatic release.

## Task 1: one pending stop command

Reproduce repeated identical stop recommendations changing IDs in real SQLite.
Reuse only the latest pending stop command with the same policy, effective stop
and current geometry; retain first issuance, original ledger receipt and current
validation separately. Changed stop/geometry and explicit refusal create a new
command. Publication rollback must preserve reused published commands.

## Task 2: explicit unresolved protection

Derive an execution context from recorded position, strongest unresolved stop,
original issuance and current snapshot quote. Publish recorded and proposed stop,
distance in price/R, elapsed time and crossed status. Unknown quote stays null.
Surface this in final report, execution UI and read-only position endpoint even
when a later action is HOLD. No inferred broker acknowledgement or automatic order.

## Task 3: explain the Expected budget

Preserve existing risk/benefit/sacrifice gates. For exclusion by the0.030R budget,
show the reference's tail difference and whether both Expected and tail dominate
or there is a genuine tradeoff. Label the threshold as configured policy rather
than historical calibration. No arbitrary threshold increase to improve rating.

## Validation / review focus

Behavioral RED→GREEN: same stop across reviews/reload, changed geometry, refused
commands, failed publication, long/short/crossed/missing quote; report and API
expose original issuance and separate recorded protection. UI behavior checked by
existing DOM/WebKit harness. One final reviewer, full mandatory CI then actual
exact-SHA production receipt without extra diagnostic LLM calls.

Ruling: broker integration/actual stop cannot be inferred from recommendation;
human receipts remain required. Cost: terminal cannot itself guarantee protection.
Ruling: do not invent confidence, action profitability or change0.030R gate;
show the economic tradeoff. Cost: EXIT can remain excluded by explicit policy.

## Final evidence

- Original behaviors observed RED; final owning174PASS9.11s, one upstream
  Starlette deprecation warning. Real DOM execution/ACK and audit smokes PASS.
- One independent reviewer:0Critical,2Important fixed in one pass with RED→GREEN:
  third identical review keeps previous command continuity; ACK returns fresh
  protection context and UI clears/replaces the obsolete warning.
- Final: minor (deferred): for budget exclusions without a positive comparable
  tail tradeoff, generic policy-threshold explanation is not repeated beside
  each row. Machine threshold basis remains explicit and gates are unchanged.
- Final: Ruling: actual broker state/profit/calibration cannot be inferred from
  code/tests. Existing human receipts remain authoritative; costs are inability
  to guarantee installed protection or historical net-action advantage.
- Broad earlier local run98PASS/5FAIL143s: three old reduction wording assertions
  repaired and included in final owning green. Two provider fixtures exhausted
  unchanged budget in cached_snapshot before provider11.8/12.35s on this host;
  native Python3.11 full CI is required. Host test-only yfinance import shim
  used for known native curl failure; no production shim or deadline changes.
- Local WebKit execution unavailable because ESM cannot resolve the runtime
  Playwright package; actual browser execution remains mandatory in CI.
- Full exact-tree CI, merge and actual production receipt pending.
