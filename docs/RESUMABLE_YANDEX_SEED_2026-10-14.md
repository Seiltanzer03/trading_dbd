# Bounded Yandex seed transport

Runtime/main fdffdbde030164bf4cd00fba24ef8f6d0d133121 remains accepted unchanged.
EDE38614610139 failed twice before live replication or research: first at
3204448256/3743553479 compressed bytes, then671088640/3743553479 bytes.
Both attempts passed the unchanged production capacity/identity/API preflight.
Preparation succeeded; no audit/publication/Active Edge result is asserted.

One ops-only correction uses existing boto3 managed multipart download,64MiB
parts, two concurrent requests and five bounded stream attempts per part.
The classic SDK transfer path retries broken streams and uses object identity
conditions on multipart reads. Source manifest selection, HEAD metadata,
compressed/raw byte counts and SHA256, gzip EOF/trailer, SQLite quick_check,
critical-table checks and historical seed clocks remain in the unchanged
accepted restore_verified_snapshot module. The transport invokes that canonical
module from the accepted checkout; its helper comes from a separately identified
sparse ops checkout, not an invented runtime SHA or fresh snapshot manifest.

Compressed staging is private/temporary and happens only on the worker. Before
transfer it requires compressed bytes + restored database bytes +2GiB free;
transfer errors close the staging file. Stream/gzip/hash failures remove invalid
restored bytes; SQLite validation failures withhold receipts and can leave the
worker database for diagnosis, as in the unchanged verifier. No VPS data, broker orders,
weights, sources, history, risk limits, provider or scheduler are changed.

31 owning tests passed, including actual SDK retry of urllib3 ProtocolError on a
stubbed multipart response with the same Range/IfMatch identity, original byte
equality and unchanged epoch, corrupted hash rejection, failed-transfer cleanup
and early staging capacity refusal. No network/paid model was used in tests.
One independent scoped review found no Critical/Important issues; its sole minor
documentation finding was corrected. Actual corrected research launch follows. Native
scheduled/main configuration is not claimed updated by an ops-only branch.
