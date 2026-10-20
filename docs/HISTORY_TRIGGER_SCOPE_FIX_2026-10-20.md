# Current-source hot builder: trigger schema resolution

Diagnostic-only run39387875131 on b0490169fa22411c5034042aae259d50a066d4fe
captured the actual19330887680-byte database and returned the unchanged original
via ABORTED_ORIGINAL/service_started=true/HTTP200 before cloud work.
The original image was uploaded to the existing trading-dbd-backups-2026 bucket
and completely read back: database SHA4983135f5a94d76f710af8c1e6e7bcd0c44cce456e5b66cc10abb333dcae1293,
compressed SHAac0ab38e04a5e1738c6b9ea1da19d8ebd909216fb6e15fae768cc4257f97e31e.
No production rows were retired. Public proof artifact12250953901 has ZIP
SHA1c1a75450baae78392a364a5fa5e12b2f42c5ff2b9435a103e4ebd9c4faa4bc4.

At08:47:56UTC the off-host builder failed with SQLITE_READONLY/code8 in
ai_decision_state.ensure_connection_schema, at the unqualified DROP TRIGGER.
The new main database had tables but not yet the source's guard triggers.
SQLite therefore resolved the absent trigger in the attached immutable src
database. Older historical sources lacked that trigger, explaining why their
successful proof did not reproduce this current-source failure.

The three ACK guard DROP statements now explicitly target main. Source remains
read-only; migration, orphan-ACK preservation, target update guards, rowids,
retirement dependency closure, deadlines and all integrity checks are unchanged.
New real-builder regressions first failed at the exact production statement;
a legacy source with guard triggers also failed at the earlier migration DROP.
The corrected focused cutover/ACK/trade-lifecycle profile passed19tests in1.18s.
Tests verify unchanged source SHA, surviving ACKs/rowids, active orphan update
guards and complete typed cold-history reconstitution, including post-catalog rows.

The saved current image permits the next off-host proof without another
production outage. This correction is not itself a successful production cutover,
restored disk reserve, complete full-union proof or LLM availability claim.
