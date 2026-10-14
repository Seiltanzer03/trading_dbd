# Managed seed transport — 2026-10-14

Standalone addendum; user handoff/specification and accepted runtime fdffdbde030164bf4cd00fba24ef8f6d0d133121 are unchanged.

Original EDE38614610139 failed twice before live replication/research. Attempt1 lost the stream at3204448256 bytes; attempt2 audit116066899889 failed at671088640 bytes of3743553479. Its latest preflight passed unchanged reserve with1168273408 available bytes. No audit/publish/Active Edge success is asserted.

One bounded ops-only correction is committed as2ac7b4ae5939b5941949f11e74b6ae4d28ab4f86 on ops/resumable-yandex-seed-fdffdbd-20261014. Existing boto3 classic managed multipart transfer downloads64MiB parts, with2 concurrent requests and5 bounded stream attempts per part; compressed staging plus raw size plus2GiB is reserved on worker. A separate sparse helper checkout delegates all verification to the accepted canonical restore module; manifest/HEAD/hash/byte counts/gzip/SQLite/seed epoch/receipt authority remain unchanged. No main deployment, source/risk gate relaxation, broker action or paid AI call is involved.

31 owning tests passed, including real SDK retry of a ProtocolError with identical Range/IfMatch followed by canonical SQLite/byte/epoch verification; corrupt hashes, exhausted transfer cleanup and low staging capacity refuse receipts. Coordinator shell/YAML validation pins accepted SHA and genuine run owner and rejects a wrong push ref. One independent scoped review found no Critical/Important; its minor documentation correction was applied.

Actual corrected run38633800303 is in progress. Its final live download/export/research/publication result is pending. Native main/scheduled transport was not replaced by this ops-only branch; that distinction is retained rather than claiming a permanent main release or eight ready forecast families.
