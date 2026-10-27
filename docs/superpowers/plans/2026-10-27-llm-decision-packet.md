# Independent LLM decision packet implementation plan

> **For agentic workers:** Use superpowers:executing-plans. One final independent review.

**Goal:** Give the LLM the decision inputs within its existing deadline while retaining the complete masked source audit locally.

**Architecture:** Source validation and future-clock checks operate on the full independent facts first. The provider receives the numerical features, forecasts, all twelve action economics, risk/cost/eligibility, reliability and observed/preliminary roles. Cryptographic source digests become deterministic short labels with an exact local mapping. Pure delivery/audit detail and already-validated byte proofs remain in the archived full input, rather than the model packet. Original authority and snapshots never mutate.

**Spec:** docs/TRADING_DBD_UNIFIED_EDGE_PLAN_2026-10-01.md; docs/LOCKED_GLOBAL_PLAN_2026-10-07.md; user's request to finish correct noncrypto AI review quickly without fabricated data.

**Approaches:** Whole-dictionary and nested lossless sharing in PR535/536 preserved all technical bytes but failed actual timeout acceptance. Increasing provider/SLA deadlines would mask the critical-path problem. Chosen integrator decision: separate decision facts from their cryptographic/delivery audit and archive both. Existing user instructions authorize implementation without another design approval round.

## Constraints

Keep provider model, timeout, 12s HTTP SLA, 4s completion reserve, risk/cost/source gates, winner masking and manual execution authority unchanged. No periodic paid inference, live order, database copy or crypto-specific work. Preserve all numeric features and their exact names, forecast origin, source instrument/symbol/provider, receipt/observation clocks, dependency and preliminary/proxy/assumption flags. Full original masked JSON and alias mapping must be stored alongside the exact transmitted JSON, each hash-bound. Validate full input before reducing technical proofs so a hidden future clock still rejects the request.

## Task 1: Implement and accept the decision packet

Files: new seiltanzer/ai_provider_decision_packet.py; seiltanzer/ai_runtime_report_v20.py; tests/test_ai_provider_decision_packet.py; existing provenance integration test.

- [x] Write tests for actual transport boundary, exact local full audit and aliases, retained numerical/risk/clock/role facts, mask/no mutation, distinct/repeated hash identities, hidden future clocks and materially smaller synthetic packet.
- [x] Observe expected RED against current provider input.
- [x] Implement pure packet projection and local archival in the owning transport path; keep public independent-input helper returning the actual packet.
- [x] Owning focused provider, masking, source interpretation and telemetry tests; one read-only measurement on saved review434. Aim for <=24KiB real packet; preserve facts rather than truncate if a future larger source exceeds that target. Existing60KB hard ceiling remains.
- [x] One final independent review; fix reproduced Important findings with regressions.
- [ ] One final applicable full PR CI, expected-head merge, main CI and automatic exact-SHA release.
- [ ] Read actual canonical smoke and saved structured LLM result. The defect is not resolved until actual current participation is verified.

## Review focus

Hash labels must preserve shared versus independent identity and have unambiguous local reverse mapping. Source roles and missing/null/numeric values cannot be invented or dropped. Full-input future clocks remain guarded even inside omitted technical proofs. The model cannot see a server-selected policy. Full and sent archive hashes must describe their own bytes.

## Evidence and ledger

Baseline main4dbe8339ee2d10c0acce8e25f37437e7a5ccaa29/tree36c24a5505f24711f64e20558886e3c3613636a0, PR536. Both full CI5057PASS; automaticdeploy40552419090 attempt2 succeeded and all7core statuses green. Actual AI434/traceai-45832d1f2e854d448327 still provider_timeout:42317B input; provider started1285.752ms, HTTP timed out8020.571ms later, route9142.518ms. No completion usage arrived. Five live forecasts, zero qualified preferences at18:00:56UTC; localfeedback1119/387eligible/errornull. Historical source profit not proved.

Free route probe40575942929/job122442279569: authenticated GET only, HTTP200 in666/113/309ms; no model completion or data upload. Saved434 reconstruction42746B: source facts14745B, shared metadata3206B, manager8508B, candidates5476B, research4286B, delivery audit2099B. This supports reducing the decision contract rather than another small general compression pass. Network health at probe time does not prove inference latency.

Pre-flight: packet builder, actual provider upload and local archival form one interface and ship together. Independent old worktree and unrelated dirty gzip remain untouched. New isolated worktree is based on exact accepted main.

Implementation ruling: the provider sees the admitted feature-level observation/receipt, quality and dependency envelope and all original feature/forecast values. Constituent byte/window lineage, supporting-input inventories and schema implementation versions stay in the full local audit, which is itself clock-validated before projection and on replay. The model is not asked to repeat the source adapter's byte/window verification. Cost: model prose cannot independently inspect that constituent audit; local source admission and the complete archived document remain authoritative.

Wire rows group identical column sets, preserving missing versus null and original sequence indices. Source identities/digests have collision-free deterministic labels; returned evidence labels are restored before claim validation. Exact quote audit, metric coverage, risk/gate/evidence/costs and original action parameters stay. Canonical provenance sharing occurs before row tables; deterministic ordering makes a rebuild from sorted archived JSON identical.

Envelope version is independent-llm-transport-v2; existing v1 archives remain accepted. v2 requires full-input hash/capture, exact deterministic packet reconstruction and alias-map equality. The existing clock validator restores original row paths, so valid future TIME_STOP deadlines remain allowed and future source clocks, including omitted constituent clocks, still fail. Malformed/unversioned row tables fail closed. No deadline exemption or risk threshold was broadened.

Observed REDs: source hashes/audit initially uploaded, absent full archive; model evidence labels not restored; table encoding missing; digest-named feature metadata renamed; TIME_STOP false future detection; omitted-full-clock and changed-policy replay accepted; malformed table accepted; provenance sharing depended on dict order; exact quote audit missing. These were fixed in the owner, not by changing deadlines. Latest narrow26PASS. Earlier owning138PASS then140PASS (2 native transport cases deferred to real Python3.11 CI); expanded225 profile had222PASS and3 old list-shape assertions, now adapted to inspect decoded candidates and local full audit. Final owning profile and real saved434 size still required.

Implementation complete: final owning227PASS/2native transport cases deselected/one upstream Starlette warning50.60s. Local Python3.12 uses a test-only yfinance import stub due the previously established native curl_cffi BusError; no production stub is introduced. Full real Python3.11 CI must cover both native cases and all dependencies. Diff check clean.

Read-only final size proof40581199014/job122460722001 on original saved434:42746B old wire,57860B complete local audit,27806B new wire (34.95% reduction). Exact numeric features, forecasts, all candidate parameters and policy/risk/gate/cost/evidence values agree after identity/row decoding; original full clocks and restored packet clocksPASS. Zero paid completions or DB writes. Intermediate35940B table-only form was larger than the preceding33442B because it duplicated constituent lineage; it was not released. Final format keeps that validated lineage in the full local audit and shares admitted envelopes canonically.

Ruling: soft24KiB goal was not reached (27.16KiB measured). Retain the complete decision facts instead of further lossy reductions or changing deadlines. Cost: actual inference may still exceed its fixed window; only canonical post-release participation can close the defect. This is a measured substantial reduction, not a latency guarantee.

Next: one fresh final whole-package review, required final CI and authorized exact-SHA release; inspect actual LLM status/weight, packet and full-audit integrity, prompt/completion/latency counters. No additional implementation or repeated tests unless a real failure or Important review finding justifies it.

Final review complete at6ce99ddac00fabe9d414c04b63a55bdfca05ab2f: reviewer review_llm_decision_packet_final (gpt6astra/high),0Critical/Important/Minor; independently27PASS0.63s and clean diff, no mutations. No fix pass or re-review. Reviewer declined actual inference/participation, full Python3.11/native CI, independent reproduction of server measurements and profit: the first three remain required release evidence; profitability remains outside technical-completion claims. Documented constituent-lineage omission and old helper shape weakness are accepted bounded-contract rulings, not hidden new findings.
