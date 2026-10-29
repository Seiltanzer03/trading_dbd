# Lossless JSON history storage with existing Yandex recovery

User: use the existing Yandex storage; no money for larger Timeweb disk;
continue quickly, preserve original data/models and manual trading scope.

## Evidence and choice

Main4d35, actual DB19.59GB/free945MB. One real off-host page-only rebuild of the
verified28Oct19.50GB seed (40856228869) did NOT shrink it. PR545 closed unmerged;
do not try more page sizes or reuse its old seed as current data.

Read-only sample40861252226:32 early/latest passive/G1S JSON fields round-trip
byte-identically with SHA equality; encoded size including128B/row overhead
is39–69% smaller. This is sample evidence, not measured full-DB savings.
Use stdlib zlib+base64 to store only the two large immutable feature JSON
columns compactly. Keep exact original UTF-8 text, whitespace and hashes.
No observation, rowid, numeric feature, forecast, outcome, timestamp or model
authority changes. Plain historical files remain supported.

## Contracts

- `seiltanzer/sqlite_json_payloads.py`: pure stdlib bounded codec and immutable
  storage marker. Encode only when smaller; hard raw/packed/decompression bounds,
  strict version/length/hash and no ignored suffix. Separate fields remain
  ordinary SQLite TEXT; decoding returns the exact original string.
- Marker fixes codec/columns and binds an optional manifest SHA. It grants no
  trading/research authority. Startup guard requires the reviewed reader
  capability before a compressed DB can open; older checkouts fail closed.
- Passive/G1S writes compress only when the marker is present. Actual runtime
  connections install a column-aware decoding row factory AFTER their existing
  factory. Preserve sqlite3.Row/tuple behavior and all non-target columns.
  Owned SQL JSON/LIKE/length queries use an explicit decoding SQL function.
- Raw backups remain exact bytes of the compact physical source. New off-host
  backup contract/version requires codec pins; old readers reject that version.
  Existing plaintext v1 slots still restore normally. Private Yandex bucket,
  reserve, actual GET/hash verification and current hot/cold pins remain.
- Research preparation expands coded fields into a disposable worker copy,
  removes only its encoding marker, preserves every typed application row/rowid
  and old hot/cold state, then uses the existing full-history union. Research
  readers/model code receive ordinary exact JSON and original source clocks.
  The physical raw backup is retained for actual recovery, never replaced by
  an analytical expansion. No S3 filesystem mount or new cloud resource.

## Finite tasks

1. Codec/marker/reader: RED→GREEN strict corruption/bomb/suffix/version/hash,
   SQLite Row/tuple/aliases, plain compatibility, reserved-column isolation,
   source field SHA and round-trip. Defaults keep unmarked DB writes plain.
2. Wire actual live readers/writers and all owned SQL expressions; test capture,
   G1S source admission, forecasts/provenance, cached status and raw hashes.
   Reuse reviewed headroom admission from commitsc2183bb/1b30f2a unchanged.
3. Manifest/startup/backups/research expansion: reject missing/tampered pins,
   reject legacy capability, exact raw restore, immutable whole-file proof,
   complete analytical expansion/union and model-reader equivalence.
4. Off-host full current-image builder: retain page size, explicit all rowids,
   schemas/triggers/sequence/headers and every decoded typed table root. Add only
   the marker. Source unchanged; exclusive publication and owned-inode cleanup;
   refuse non-shrinking output, unsafe input or inadequate actual reserve.
5. One final scoped review/correction and mandatory integrated3.11/native/
   SQLite/WebKit CI; include the already reviewed macro transport PR544 for one
   release if unchanged. No duplicate source/model/paid-provider probes.
6. Current-source native operation: separate durable journal, canonical service
   guard and host EX lock; stop/close all writers, hash actual CURRENT source,
   archive original and compact images to existing Yandex and verify both actual
   GETs before transfer. Reviewed new code supports plain original and coded hot
   image; code-before-data installation is fenced and exact-SHA, dependencies
   unchanged. Independent finite owner handles SSH/driver loss; no partial DB
   starts; precommit original reserve/runtime validation and exact recovery.
   Existing committed cold epoch is not reset. No old seed/current-data rollback.
7. Actual canonical exact-SHA acceptance, source/model publication and one
   ordinary saved LLM receipt; only then report freed physical bytes and actual
   behavior. No orders/ACK, invented metrics, relaxed gates or forced weights.

Ruling: preserve real local runtime reads through a codec; expand on the existing
off-host research boundary rather than rewriting every model/historical reader.
The new physical encoding is explicit in manifest/guard contracts. Optional raw
research capture pauses below the reviewed headroom threshold; models, prices,
bars, existing outcomes and trade audit retain their previous behavior.

Progress: codec/immutable marker and runtime/native-export wiring implemented.
RED absent codec, reserved SQLite table name, missing marker rollback, old-reader
startup acceptance, omitted manifest pins and custom row-factory loss were
reproduced and corrected. Byte cache is bounded independently of Unicode width.
Owned SQL JSON/scalar/LIKE/length expressions decode explicitly; exporter keeps
both original/derived SHA checks. Pure and owning profiles:80PASS2.66s with one
upstream warning, test-process-only host yfinance import shim; native3.11 CI is
still pending for this new package. Backups, analytical expansion, full current
image/native protocol, integrated review/CI and actual release remain pending.
No live mutation occurred.

Resume 2026-10-29: recovered the original isolated worktree without repeating
the negative page-only measurement. Tasks3/4 now include the full-image
compression/expansion builder and research-boundary expansion before cold union.
Actual v1/v2 hot-history fixtures preserve typed rows, hidden rowids, schema,
source bytes and current outcomes. Local backup regressions demonstrated that
the old local contract silently accepted encoded data; local backup/restore now
requires the explicit v2 contract and matching embedded pin before touching the
destination. Final changed-boundary profile:59 passed in3.31s. Host-only test
yfinance shim remains outside the product; mandatory native3.11 CI is pending.
Native current-source transfer and production acceptance are still pending.

Final review 2026-10-29 of4d35f82..0d8eb86:0Critical/3Important.
One correction pass reproduced and fixed all three: low-disk sparse/quiescent
backups now bind the embedded pins and v2 contract before publication; encoded
databases cannot enter rowid-changing VACUUM backups; publication verifies its
still-open inode on success; final worker cleanup retains the original inode
token and never removes a recreated pathname or unowned sidecar. Seven new
regressions first failed; replacement fault controls passed without deleting
unrelated files. Changed-owner profile:210 passed, one pre-existing host failure
`test_quiescent_sparse_clone_replays_wal_only_into_backup`. The same failure was
reproduced with the unchanged4d35 baseline implementation: this host's SQLite
source integrity read changes WAL ctime (bytes/size/mtime unchanged). Do not
relax that signature check or disable the test; mandatory native3.11 CI must
verify the actual supported environment.

Final: minor (deferred): independent native-Q collector still writes compatible
plain JSON rather than packing that smaller separate capture stream.
Final: minor (deferred): builder keeps SQLite's default per-value limit, so an
unusually large non-feature BLOB can allocate substantial worker memory.
Final: Ruling: review did not judge task6/native cloud state or production
effectiveness because they are unbuilt/unmeasured; retain both as explicit
pending gates. Cost if wrong: premature deployment or an unsupported savings
claim; no such authority is granted by this package's tests/review.

Task6 implementation checkpoint: private frozen-source/transfer owners now
accept an explicit database with separate metadata; existing initial cold
defaults/refusals remain unchanged. New JSON owner binds the already committed
cold journal byte-for-byte, mirrors a canonical service guard, independently
verifies both original/encoded cloud streams before transfer, preserves hard
clocks and actual reserve/runtime precommit, and permits only verified terminal
start. A separate durable epoch link also blocks startup if the canonical guard
is removed while nested metadata is unfinished; offline runtime validation
bypasses only the three epoch paths, not source/codec capability validation.
Generated v2 fixture tests reproduced the missing isolation and missing startup
fence, then passed current-source replacement/partial-transfer recovery without
resetting old metadata. Owning integration162 passed/3 skipped in22.92s.
Two new real systemd driver-death/expiry cases remain enabled as a dedicated
native3.11 CI step; local host has no systemd proof environment. The off-host
current-source driver and canonical production-concurrency workflow are present.
One scoped final review of this task6 diff, correction, exact integrated CI,
actual code-before-data installation and live execution remain pending.

Task6 scoped final review a54a16a..135dacc:0Critical/2Important/1Minor.
One fix pass reproduced both Important findings. A safe terminal-original JSON
epoch now advances through a durable transition intent and atomic link update,
preserving prior JSON metadata and the cold journal; pending/fence-lost and
committed JSON epochs remain inadmissible. A new attempt captures actual current
bytes, including legitimate writes after the previous original restart. Owned
link creation interrupted before preparation can resume without replacing an
existing clock. Driver launch/polling validates terminal result, exact epoch,
accepted plan and journal before requiring an observed active unit, so a fast
terminal resume is reported correctly. These cases first failed, then passed;
owning integration165 passed/3 skipped in8.39s; extra original-restored versus
committed transition coverage also passed. Generated fixtures now enlarge only
one pending row, preserving source hashes and avoiding unnecessary test space.
Final: minor (deferred): native failure artifacts retain stage/exception class
but omit the narrower stable reason; private messages/URLs remain excluded.
Final: Ruling: scoped review declined real systemd/cloud throughput, complete
current-data timing and production effectiveness; require native CI and actual
operation receipts before treating these as verified. Cost if wrong: a false
readiness/savings claim; none is made from the fixture tests.
