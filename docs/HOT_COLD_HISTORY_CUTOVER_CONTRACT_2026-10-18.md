# Hot/Cold History Cutover Contract (2026-10-18)

## 1. Objective & Scope

This contract governs the physical hot/cold cutover of the live SQLite database (`/opt/seiltanzer/data/trades.db`) and its off-host disposable proof (Package B of `docs/superpowers/plans/2026-10-16-terminal-completion-and-ai-review.md`).

The cutover solves the chronic disk exhaustion on the 25 GB production root filesystem (`/`), where `trades.db` grew to `19.28 GB` (`passive_market_observations` = `7.80 GB`, `g1s_observations` = `4.94 GB`) and left only `~0.70 GB` free (`< 1.03 GiB` required by `seiltanzer/background_disk_admission.py`).

## 2. Pinned Cold History Authority

Cold history is anchored to the verified Yandex Object Storage complete selected-table catalog produced in workflow run `38949123354` (`docs/FULL_SELECTED_HISTORY_CATALOG_2026-10-16.md`):

- **Bucket:** `trading-dbd-backups-2026`
- **Source Database SHA-256:** `d6e8c423f21585f68cd28b0d837eb08c944d2b273bbba59ad963927e33434535`
- **Catalog SHA-256:** `7b78d25d13f249f1ab967828675419a6656fe6b69787e5b78644076a54411a63`
- **Catalog Object Key:** `research/sqlite-history/v1/d6e8c423f21585f68cd28b0d837eb08c944d2b273bbba59ad963927e33434535/catalogs/7b78d25d13f249f1ab967828675419a6656fe6b69787e5b78644076a54411a63.json`
- **Catalog Coverage:**
  - `passive_market_observations`: `132,923` rows (`rowid 1..132,923`) across 266 verified cloud partitions (`logical_sha256 = 29050ca2bf04bb86ef2a3548321ba0f889f749c4b0030b81db5214d9ba7b89c8`).
  - `g1s_observations`: `86,100` rows (`rowid 1..86,100`) across 173 verified cloud partitions (`logical_sha256 = 95a7e83d1db8a63c48bd32dd19d80025a3494649c0020ca6e91a311e25e49fa2`).

## 3. Dependency-Closed Hot/Cold Partition Rules

### 3.1 What Remains 100% in the Hot Database (`trades.db`)
1. **All tables outside `passive_market_observations`** (`g1s_observations`, `trades`, `ede_v13_decision_ledger`, `g1_dataset_membership`, `g1_dataset_cuts`, `g1_dataset_cut_members`, `g1_q_capture_attempts`, `g1c_shadow_predictions`, `virtual_position_observations`, `ai_decision_acks`, `trade_lifecycle_identities`, etc.) remain 100% intact in the hot database.
2. **All `resolved` and `pending` rows in `passive_market_observations`** remain 100% in the hot database.
3. **All post-catalog rows (`rowid > catalog_max_rowid`)** in `passive_market_observations` remain 100% in the hot database.
4. **Both the catalog boundary row (`rowid = catalog_max_rowid`) and the global maximum row (`MAX(rowid)`)** in `passive_market_observations` remain in the hot database so watermark monotonicity is preserved across both cataloged and post-catalog epochs.
5. **All dependency-referenced rows in `passive_market_observations`** remain in the hot database, even if their `resolution_status` is `'insufficient_future_data'`:
   - Referenced by `g1_q_capture_attempts.created_observation_id`
   - Referenced by `g1_dataset_cut_members.observation_id`
   - Referenced by `g1c_shadow_predictions.observation_id`
   - Referenced by `virtual_position_observations.observation_id` where `resolution_status = 'pending'`
   - Referenced by `g1_dataset_membership.observation_id` where `forecast_eval_eligible = 1 OR q_to_p_eligible = 1`

### 3.2 What Is Retired from Hot Storage to Cold Storage
Only rows in `passive_market_observations` that satisfy **all** of the following conditions are retired from the hot SQLite file:
1. `rowid < catalog_max_rowid` (`132,923`), and the row's `rowid` is proven present in the pinned catalog's verified partitions (`validate_full_catalog`).
2. `rowid < (SELECT MAX(rowid) FROM passive_market_observations)`.
3. `resolution_status = 'insufficient_future_data'`.
4. The source database's catalog prefix (`rowid <= catalog_max_rowid`) is verified against the catalog's exact `row_count` and typed `logical_sha256` root before compaction.
5. The row's `observation_id` is not in the dependency exclusion set (§3.1 item 5).

This retires `~91,000+` terminal dead-end `insufficient_future_data` rows (`~5.25 GB` physical pages), shrinking `trades.db` from `19.28 GB` to `~14.03 GB` and restoring `~5.95 GB` (`~5.54 GiB`) of free root disk space on the VPS (`> 5x` the `1.03 GiB` admission reserve).

## 4. Rowid-Preserving Physical Compaction Invariant

1. **Standard SQLite `VACUUM` is forbidden** on `passive_market_observations` and `g1s_observations` after row retirement because both tables declare `observation_id TEXT PRIMARY KEY` (without `INTEGER PRIMARY KEY`). A standard `VACUUM` renumbers implicit `rowid` values sequentially from `1..N`, which would corrupt:
   - `g1s_materialization_state.source_watermark`
   - `g1s_status_materialization_state.observation_rowid_watermark`
   - Cold catalog partition `rowid` mapping (`sqlite_history_catalog.py` / `sqlite_history_union.py`).
2. Physical compaction in `scripts/sqlite_history_cutover.py` builds a fresh SQLite database file (`journal_mode=DELETE`, `freelist_count=0`) by:
   - Applying `seiltanzer.ai_decision_state.ensure_connection_schema` so pre-tombstone `ai_decision_acks` rows (`trade_id IN (77, 96)`) are bound to `trade_lifecycle_identities` and pass `PRAGMA foreign_key_check`.
   - Recreating all tables, indexes, triggers, views, `PRAGMA user_version`, and `sqlite_sequence` entries in exact schema order.
   - Copying every row of every `rowid` table with explicit `INSERT INTO main."table"(rowid, col1, ...) SELECT rowid, col1, ... FROM src."table"`, preserving every surviving row's exact `rowid`.
   - Verifying `PRAGMA quick_check == ('ok',)` and `PRAGMA foreign_key_check == []` before atomic publication.

## 5. Post-Catalog Hydration & Full Reconstitution (`sqlite_history_union.py`)

When `sqlite_history_union.hydrate_snapshot` hydrates a compacted hot database that may also contain post-catalog rows (`rowid > catalog_max_rowid`):
1. Before merging cold partitions, `hydrate_snapshot` records any post-catalog suffix (`WHERE rowid > catalog_max_rowid`) count and typed `_logical_hash` per cataloged table.
2. During merge, `_merge` inserts missing cold rows (`WHERE rowid NOT IN (SELECT rowid FROM main."table")`) with triggers denied via `set_authorizer` and verifies each partition's typed `logical_sha256`.
3. After merging all partitions, `verify_full_tables(conn, catalog)` verifies:
   - The catalog prefix (`WHERE rowid <= catalog_max_rowid`) has exact row count `== proof['row_count']` and exact typed `_logical_hash == proof['logical_sha256']`.
   - The post-catalog suffix (`WHERE rowid > catalog_max_rowid`) is unchanged in both count and typed `_logical_hash`.
   - Global `PRAGMA quick_check == ('ok',)` and `PRAGMA foreign_key_check == []`.
