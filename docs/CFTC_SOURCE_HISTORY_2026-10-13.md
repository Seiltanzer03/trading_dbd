# CFTC source preservation and EDE checkpoint — 2026-10-13

Original unified-edge A→B→C→D plan remains binding. Core manager/actions/weights was accepted on b96cca4; this milestone does not claim all eight forecast families trained.

## CFTC source history

PR465: https://github.com/Seiltanzer03/trading_dbd/pull/465
Head a290a9c4952b41fc428d51bf5feb279b6e55bdcc, source tree9a4d95d5781f7e4238cd283af6779e92a3116133.
CI mergecandidate b95ddffbca4901eecf44c41ae2a2c32e461bd2e8, run38438991671:3955passed/5skipped/13subtests; pinnedSQLite1passed; frontend/realWebKit/ci-fullwebkit success. Owning357passed. Fresh final review:no Critical/Important, isolated parser/helper checks; reviewer pytest unavailable.

Authorized one-off operation:38439350922/job115441558650, opsSHA9d29d4c5f33243402e3b19ea12af678976c638f5. Branch creation alone did not create push run; a workflow-only comment commit triggered exactly one run. No production mutations or additional source collector requests. Operation may precede full CI because it runs isolated off-host source storage; production integration still requires green final-tree CI. Contracts43passed. Actual acquisition/archive succeeded 2026-10-13T17:32:15Z with0source errors. All three markets have128actual rows, report dates2024-04-30 through2026-10-06; private gzip total32603bytes. All receipts verified_full_get=true, with original packet/compressed SHA+size and cloud manifest readback. Receipt artifact11833509774/digestd77e0a77a71f78bf44e70be31f979447b8d206b0c2600733116b851b77ee98a0; retained30days. Durable receipt copied beside this checkpoint.

Raw bodies/private packets remain in existing trading-dbd-backups-2026 bucket. Source-created/updated clocks are revision metadata, not proved historical publication or first receipt. publication_verified=False, historical_forecast_admission=False, production_authority=False, models_produced=0. History is now preserved, positioning model remains open pending actual causal availability proof and validated CFTC→CFD association; no inferred calendar backdating.

Deferred minors: optional parser float conversion can round tiny fractional strings; malformed timezone offset minutes can normalize through datetime. Exact raw bodies are retained and authority remains false; no incidental polish pass. Ruling: actual private-cloud acceptance comes from observed receipts, not code review. If unavailable it must stay incomplete; historical model admission remains separate.

## EDE result

Run38404251655 fully succeeded including publish115359635980 and active-edge handoff115360185821. Published exact b96cca4 at14:44:05Z: EDE audit16163614bytes, frozen evidence406547, transition audit7356937, registrygzip42072071/receipt648. Yandex ledger readback preserved; no local bytes retired in this publish.
Actual accepted dataset8565rows, source before baseline15032; excluded6467. Selective search3960hypotheses,21FDR passes,0stable candidates; maturity2EARLY_CONTEXT/119INSUFFICIENT_DATA/0RESEARCH_SIGNAL/0PROVISIONAL/0ROBUST. NO MATERIAL SELECTIVE EDGE FOUND. Transition1392hypotheses/16FDR passes/0stability passes;4EARLY_CONTEXT candidates allproduction_authority=False/shadow_eligible=False. The diagnostic transition verdict is not grounds to promote those candidates or increase decision weights.

Off-host backup manifest artifact11822855707: immutable live DB18265706496bytes, SHAbe9466073efd08b7ebb26019a122b033f67452a0d16ca0b736d7e8732901c3e5; private daily-slot-5 compressed3693084517bytes/ec9adbd6337e290174654856816fa3ee075b1d81a3c41cac58dc78d515a2f236. Manifest proves HEAD+boundary GET, not a new full-cloud restore; do not overclaim. Source snapshot was verified before research. Research artifact11823747526 contains actual audits/ledgers.

## Release and next boundary

PR464 merged as708b13662693a91a94de64a476fa17c4fa1ef581 after prior fullgreen; mainCI38438844045green, deploy38439446953 underway at this checkpoint. CFTC PR465 ready for integration after this deployment gate; do not overlap exact-SHA releases. EDE/Active Edge continuation on previous b96 completed before this merge.

Remaining original-plan B chains: official macro archive too sparse/missing causal head; events lack pre-event consensus/history; order flow lacks admissible target history/transfer; positioning lacks publicavailability/validatedassociation; value/carry lacks PIT fundamentals/carry history. Current intermarket/session/option models are the functioning families. Preserving bytes is not fitting, fitting is not validated forecast, and forecasts are not strategy profit. Work only on these concrete source/history/model gaps; don't reopen completed manager/weights or loosen gates to manufacture eight green statuses.
