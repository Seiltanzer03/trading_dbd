# Review of the two actual19October AI reports

Scope: saved reviews397/398 for trade138, captured14:16:40.481UTC and
14:42:13.556UTC. Display clocks differ by approximately2.5s from stored rows;
the first empty timestamp lookup was not evidence of missing reports.
Report/continuity corrections were released through PR493; PR494 fixes
premature recovery of a still-starting service. PR495 scopes ACK trigger DDL
to the writable main database during off-host history compaction.

| Item | Verified classification | Result / remaining condition |
| --- | --- | --- |
| 1 LLM timeout | Real provider connectivity/configuration fault; original6s timing alone did not locate it | Configured SOCKS connection refused/code111; directauth403 without interpreting its body. Existing fully bootstrapped Tor auth200/912.8ms. Corrected systemd EnvironmentFile order after openrouter.conf; real structured runtime response still requires its own receipt. |
| 2 REDUCE_TAKE sign reversal | Reproduced difference in execution methods | Same saved inputs and1200paths/seed: piecewise delta−0.05526580R, bridge+0.08632273R. Original common600/seed−0.04291585266585R and independent1200/seed+0.08632272588523R reproduced. Interpretation between path steps is sufficient to reverse the sign; costs or sample count alone are not the explanation. Both mandatory admission gates remain. |
| 3 CLOSE sacrifice reason | Reporting defect: omitted comparison reference | First report best eligibleTIGHTEN_STOP Expected0.597756456R. CLOSE10/25/50 sacrifice0.143210783/0.127228468/0.100591277R, exceeding0.030R even with positive delta versusHOLD. Report now exposes reference, sacrifice and budget. |
| 4 BE versus TIGHTEN / cached base choice | Clipped-score tie and misleading causal reporting; input-order dependence corrected | Normalized raw utilities1.362371413/1.532213828 both clip1.0. Fixed declared policy ordering resolves ties. BE Expected sacrifice0.020381R is inside0.030R band. Cached original optimizer selection is separately labelled, not explained using refreshed numbers. |
| 5 Stop continuity | Real missing prior-review lookup/reporting; observed stop changes were legitimate manual ACKs | Event106 and ACK14:34:17.307UTC confirm firstBE decision; event107 and ACK14:43:22.577UTC confirm secondTIGHTEN. Lookup now uses indexed compatible ledgers and preserves ACK state. Recorded geometry execution prices are not fills; position fraction stayed1.0. |
| 6 Broker quote difference | Unproven stale-decision allegation | The quoted29615 broker price has no timestamp. No timestamp, fill or stale-price conclusion was invented. Fresh execution preflight and geometry/version invalidation remain mandatory. |
| 7 Tail protection versus Expected | Permitted risk intervention, not a demonstrated Expected gain | SecondTIGHTEN common lower delta−0.006383428R fits−0.030R sacrifice budget; lower tail gain+0.126373626R exceeds0.050R. Independent Expected lower bound−0.02215R does not establish positive Expected. Reports distinguish bounded tail protection from robust positive Expected. Historical profit remains unproved. |
| 8 Small math weight / zero action contribution | Actual abstention, not necessarily disconnected models | Mathematical pool2.8178%/2.0559%, but selected stop-action contribution0: admitted option preference abstained on those actions; session had no admitted action preference. Neither forecast presence nor a nonzero pool weight guarantees an opinion on every action. No forced votes. |
| 9 Snapshot exclusion marker | Cumulative marker misinterpreted as final fact loss | Stored final second snapshot contains macro_context_v1 and macro availabilitytrue. Earlier12914-byte root exceeded8000-byte local budget before restoration. Frozen expert-input lineage is retained through quote-only refresh;96 wholly null per-action placeholders removed without removing real zero opinions/candidates/family statuses. |
| 10 Model precision / no slippage | Explicit model limitation; no historical profitability proof | Common-bank Expected/CVaR remain conditional scenario economics. Missing execution-MC geometry is not replaced with zero or model event frequencies labelled physical calibratedP. Bridge/piecewise disagreement and delayed proxy limits remain visible. |
| 11 Eight-family readiness | Missing inputs/causal validation/runtime matching, not eight proven action models | Existing archived25fits/13supported groups are separately measured in FAMILY_READINESS_CHECKPOINT_2026-10-20.md. No training without new usable observations, fabricated consensus/book/carry, lowered OOS gates or artificial weights. Current runtime facts/forecasts/contributions still need exact-SHA receipts. |

Neither the independent bridge pass nor a high common score cancels another
mandatory refusal. These simulated deltas do not establish which execution
model predicts actual NAS100 trading better. No broker action, ACK or fill was
created to make the audit pass.


## P0 runtime evidence after PR496 — 2026-10-20 18:52 UTC

Read-only diagnostic39478524504/job118820697417 (no model call, ACK, order or database mutation) correlated an already saved live review at18:49:54UTC, request `ai-5210d2c0b82c474982d7`:

- Single provider worker started in0.351ms. Provider render3.120ms and preparation2.919ms. Thus this case is not the former119.7s blocked SOCKS handshake or an occupied queue.
- Input23140B/request29536B, prompt7343 tokens. HTTP returned in6684.787ms, finish_reason=stop, completion409 tokens/output1328B. The configured model did answer.
- Existing outer guard expired after6006.097ms, about0.68s before HTTP completion. Public response remained deterministic fallback/HTTP200, total12195.698ms; roughly5.4s preceded provider work.
- Late `provider_validate` also raised RuntimeError after0.560ms. Exact failure code is NOT present in the current safe telemetry. It could be payload validation or the final report-integrity check; format/schema failure is not yet proven. Do not weaken either validation or hard-risk and do not assume merely increasing the outer timeout fixes both defects.
- Next bounded fix package: add code-owned allowlisted rejection reason (no raw response, prompt, URL or credentials); reproduce the actual rejection; compact redundant provider facts/output while preserving every real input, all12 action preferences and eight family roles; prove accepted structured response inside the existing deadline. Final exact-SHA live acceptance must show actual LLM mode/admitted structured preference. No weight forcing or new training sweep.

Current environment cannot execute local tests; browser unavailable after automatic approval review usage-limit failure. No untested product-code change, new deploy or physical cutover was attempted. Diagnostics and confirmed proof pins are checkpointed in the ops branch.
