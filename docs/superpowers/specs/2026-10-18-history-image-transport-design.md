# Bounded private cutover image transport

Continue Task3 of the durable-history plan using existing Yandex storage only.
The current disposable controller remains disposable and keeps its production
root refusal. This prerequisite supplies its byte-stream interface; it does not
stop the service, truncate a file or authorize production cutover.

Provide a stdlib-only `SignedImageTransport` for separately supplied private
original/hot signed URLs. Receipts bind exact raw SQLite SHA/size and gzip
SHA/size to `research/sqlite-cutover/v1/<run>/<kind>/<raw-sha>/<gzip-sha>.sqlite3.gz`
in trading-dbd-backups-2026. Receipts contain no URL or credential and do not
claim cloud verification merely because they were parsed. A complete read must
verify both representations and gzip integrity before reporting success.

Allow only exact https://storage.yandexcloud.net bucket/key paths: no userinfo,
port, fragment, alternate host, path normalization, escaping or redirects. URL
errors and network/decode errors expose stable codes, without provider text or
signed queries. HTTP status200, exact Content-Length, no content encoding and
a ten-second maximum socket timeout are required. Each decoded read is bounded
to1MiB; an overall monotonic deadline covers opening and all reads. Reject
truncation, extra decoded/compressed bytes, either wrong hash and expired time.
Aborted consumption closes the response without draining or minting a receipt.
The transaction controller retains its separate fence and raw identity checks.

`verify_image(kind)` drains one actual stream and returns a safe full-read
receipt only after EOF verification. `open_image(kind)` yields the verified
bounded reader expected by the existing controller. Pin immutable copies of
caller inputs; private URLs are never part of repr, result, journal or logs.
No arbitrary upload or source/SQLite validity assertion is added. Original/hot
publication, actual full-cloud receipts, local owner/freeze and measured reserve
remain the separate following tasks. Fast tests use actual SQLite bytes/gzip
and injected HTTP byte streams; real network proof remains explicitly pending.

## Task2 — off-host publication, same narrow content contract

Add publish_image(database, identity, *, run_id, kind, client, deadline, opener=None)
in scripts/publish_history_image.py. Refuse the production root, nonregular or
changing source and sidecars. Require worker staging reserve of raw size plus
32MiB. Compress one immutable off-host source to an owned0600 temporary gzip,
hashing original and compressed bytes under the deadline; reject a mismatched
source before upload. Use the two hashes in the content-addressed key above.

Only the exact Yandex endpoint is allowed. Use a single-thread16MiB multipart
upload, SDK request connect/read limits10/120seconds and one total attempt;
the overall deadline is checked during compression/upload and between requests.
The native owner will provide its separate hard process bound. Verify exact
HEAD length/normalized metadata and perform full signed GET through Task1.
Return only the safe full-read receipt. Remove only owned staging files even
on failure. No delete API, rotating slot, SQLite validity/model assertion or
service operation is added. Real Yandex publication remains pending until the
fresh frozen original/hot epoch is prepared under the finite local owner.
