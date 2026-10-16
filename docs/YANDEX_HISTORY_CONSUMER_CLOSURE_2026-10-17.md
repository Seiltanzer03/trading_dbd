# Selected-history consumer closure — actual next migration boundary

Source tree: 42b549d58b49fe370b027497ef94c501601aeb40 (main a2a48108cd0fd924e5fcf684be16af5043e48388). Original locked plan and source specification unchanged.

The completed Yandex receipt verifies all439parts/219023rows of two selected tables. It does not authorize live history retirement. This inventory advances Package B's reader/writer contract; it is not equivalence proof.

## Measured code inventory

Tracked Python literal references:158 in53files;122read,13write/schema,23identity/dynamic. The attached JSON records each file/function/line and table. Classification is a lexical aid, not a complete SQL call graph. Dynamic table interpolation, indirect readers, cached projections and SQLite dependencies remain to be closed.

| Owner | Required migration invariant | Current result |
| --- | --- | --- |
| passive_learning.capture_observation; g1_q_collector._insert_native_q_only | New rows and highest source rowid stay hot; no implicit rowid reuse | No writer migration performed |
| passive_learning.resolve_due/_resolve_one; measurement_path_runtime/refinement | Pending rows and every still-mutable outcome remain local; no archived frozen outcome modified silently | Eligibility/dependency closure pending |
| g1_short_horizon_runtime.materialize_new/resolve_new | Original source_rowid, source IDs and raw T0 feature/forecast hashes survive joins and watermarks | Full paired source/G1S row roots verified; production join equivalence pending |
| G1S immutable update/delete triggers (_ensure_tables) | Preserve immutability contract; ordinary DELETE is explicitly prohibited | No trigger removed or bypassed |
| g1_dataset_runtime; G1C/Q/shadow/cut/model consumers | Full cut membership, source mutations, frozen model links and cohort sizes remain equal | Metadata-only row stubs are insufficient |
| G1S horizon/calibration/metrics/champion/evidence consumers | Complete history on off-host worker; browser continues bounded frozen materializations | All full-history callers still need explicit routing proof |
| LLM researcher/prospective journal; edge_discovery; native family exporter | Historical identifiers, clocks, exact values and cohorts remain available | No source/admission clock reset |
| storage manifests/backup and operational management consumers | Full restore scope distinguished from selected-history proof; no changed live decisions or ACK | Retirement false |

## Actual foreign-key refusal, bounded read-only probe

Run38983756795/job117250333236 on installed011b inspected105tables in2055.1ms with a2MiB SQLite cache, read-only connection and bounded per-table queries. Twelve tables declare foreign keys; only eight were checked within the deliberate probe scope.

- ai_decision_acks -> trades: at least one violation found.
- ai_verdicts, decision_replays, decision_snapshots, g1_dataset_cut_members, g1c_shadow_models, g1c_shadow_predictions: per-table check passed.
- decision_path_points: query interrupted by its bound; unavailable, not clean.
- Four remaining FK tables were not checked. Global FK integrity is not established.
- This is the CURRENT live database, not the pinned original archive epoch. It does not prove the precise cause of the earlier archived union's failure. Full union FK gate remains unchanged.

Current Journal.delete_trade is already installed from trade_delete_lifecycle and retains the parent tombstone. A separate isolated fixture with executed/not_executed ACKs verified exact ACK preservation and passing foreign_key_check after deletion (two cases). No current deletion defect reproduced; historical origin/external mutation of the existing orphan remains unproven.

Do not delete an authentic ACK, fabricate its missing trade, mark the database clean, or weaken the global FK gate. Locate an authentic parent/retained provenance before any data repair. No production row was changed by the probe or fixtures.

## Concrete next sequence

1. Resolve provenance of the existing FK violation and establish complete applicable integrity; retain original audit bytes.
2. Classify each actual consumer and dynamic dependency as hot-only, complete verified off-host union, or materialized projection, with explicit result equivalence. Keep all pending/current management and dependent cut/model rows.
3. Prove a physical replacement/rollback on a disposable copy, including post-swap writes, original rowids/byte clocks and every unchanged reader. Selected-root proof alone is insufficient.
4. Only then authorize/execute the proven cutover under the existing serial lease and verify actual sustained source reserve1107296256B. Source remains local SQLite; Yandex holds immutable history.
5. Resume the blocked EDE stage only after real headroom; publish current applicable forecasts/contributions or exact source/model blockers. No unchanged fit/restore repetition.

production_authority=false; full_production_reader_equivalence_proven=false; writer_closure_verified=false; data_retirement_authorized=false; production_mutations=0.
