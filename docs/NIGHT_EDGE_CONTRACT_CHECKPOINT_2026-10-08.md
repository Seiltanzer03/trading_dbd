# Overnight P2–P6 contract checkpoint — 2026-10-08

## Scope and accepted baseline

Authorized finite package: inspect fresh existing evidence, close causal admission
and common-economics defects in P2/P4/P5/P6, then one integrated review, final CI
and exact-SHA production acceptance. No new source, instrument, action, search
budget or policy authority. Baseline production/main b92cb2846b9f260cb88593a074999aec6e04b952
(PR418); tree c8fe1ebea659bc5781b42889568a8502016b370d.

## Changes

| Package | Defect and resulting contract | Regression evidence |
| --- | --- | --- |
| P2 | Supplied train_label_end_ts must be finite and strictly between train_end_ts and validation_start_ts in packaging and pinned runtime admission. Omitted legacy field remains compatible. Runtime OOS sample/fold counts require actual integers, matching packager. | Invalid supplied clock and fractional pinned counts reproduced before fix; valid supplied clock retained. |
| P4 | Current order-book child observed_ts must equal the admitted envelope clock, as existing owned producers emit. A fresh wrapper cannot give hour-old child book data a new age. Independent valid tape inputs remain separately admissible. | Two rejected-book cases failed before fix; valid book/source profiles passed. |
| P5 | Carry schedule must match explicitly declared executing broker/account/trade/direction and currency/quantity/risk basis. Missing required quote binding fails closed, including already-included costs. Undeclared legacy identity is not invented. | Ten live pricing refusals plus two malformed-context cases failed before fix; exact match and inclusion controls pass. |
| P6 | Recheck native expiry at prediction write admission, including the SQLite writer wait. No native horizon reduction or Q-to-P authority promotion. | Computation-time boundary regressions failed; final contention regression and review evidence are recorded in PR. |

Final integrated profile: 558 passed in5.79s, one existing Starlette deprecation
warning. Review found that a Python lock does not acquire a SQLite write
reservation: fixed with BEGIN IMMEDIATE (or no-row writer promotion in a
caller-owned transaction) before the clock. Real second-connection contention
and caller rollback regressions failed before fix and now pass. Malformed
matching direction arrays fail closed without TypeError. Initial full CI found
four comparison positive-fixture failures: snapshot trade_id was declared but
its synthetic quote omitted the matching ID. Updated fixture and wrong-trade
observed replay refusal regression:58passed. No production guard relaxed.
Final CI/deploy/publication results belong
in the package PR; local profile results do not assert production acceptance.

## Fresh evidence inspected without repeating research

Existing EDE discovery run37687814068 produced pass1 discovery and uploaded
artifact11515062560 before its runner received shutdown and transition audit
was cancelled. The cause is not established. Discovery ZIP digest verified:
c54db61dc255425da19070bf98e1141659933cf5b8c557cdc285d8e60a03ffd9.
This is completed discovery, not completed transition/publication.

Real dataset digest e24bf882525e94c5c6b7f4eb27a8709cf979087265f14f9b31834273735f6196:
14291 resolved input rows, 8063 eligible baseline rows, 6228 excluded;
15/30/60m eligible counts 2986/2424/2653 across ten instruments.
3960 fixed hypotheses; 119 outer-evaluated candidates. Joint positive incremental
effect:34; nominal p<=.10:6; aggregate sample-ready:0; aggregate FDR q<=.10:0;
fold-stable 3/4:0; passed all historical gates:0. Maturity:117 INSUFFICIENT_DATA,
1 EARLY_CONTEXT, 1 RESEARCH_SIGNAL, 0 PROVISIONAL, 0 ROBUST.
These are G1S/EDE price labels, not broker-unit net-action training evidence.
No production authority, auto promotion or fabricated outcomes.

Current baseline inventory37696953612 completed successfully at22:59:09Z:
78195 G1S observations, 22552 resolved; 96 canonical features, 65 data-ready,
27 insufficient independent evidence, 2 G1M-only, 2 quality-only.
Numeric macro features have 4–5 independent releases; FOMC tone family has3,
rate fields1 and dissent/statement-change2. Repeated snapshots do not increase
release independence. Data coverage is not an edge result or trained model.
The existing downstream chain continues automatically; no duplicate dispatch.

## Remaining global boundary

P1: independent off-host scheduler remains unavailable/unapproved; no consecutive
600s receipts or native materialization claim. TTL3600 is unchanged.
P2: real pre-release consensus and sufficient independent outcome evidence are
still required. P3: verified synchronized mapping and OOS net-action models.
P4: authorized sequential real venue data and OOS effect/cost evidence; a proxy
is not a broker CFD book. P5: actual executing quotes and dated real positioning
remain required; missing inputs stay null. P6: physical OOS calibration and
validated identity remain evidence-gated; crypto/JPY100 are not promoted.
Observed comparisons, modeled scenarios and prospective decisions remain separate.
