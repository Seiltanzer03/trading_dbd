# Canonical managed Yandex restore, 2026-10-15 Moscow

## Problem and change

Two research download attempts failed before export or computation when a single
multi-gigabyte HTTP response ended early: runs 38614610139, jobs 116030864361
and 116066899889. A plain full-object stream had no bounded recovery of the
part already being read.

The reviewed managed downloader from ops commit
2ac7b4ae5939b5941949f11e74b6ae4d28ab4f86 now supplies the default SDK client
in scripts/restore_verified_snapshot.py. Existing scheduled and explicit CLI
calls receive the transport through the canonical entry point; no temporary
push bridge or sparse checkout is added to main.

Downloads use 64 MiB parts, two concurrent transfers, and five attempts per
download part. Compressed staging and the restored database share the
destination filesystem, with compressed size + raw size + 2 GiB required before
download. Temporary staging closes after transfer failure or verifier consumption.
Decompression or hash failures remove the destination; SQLite validation failure
withholds the receipt and may leave the disposable worker database.

Manifest, HEAD metadata, compressed and raw hashes/sizes, gzip EOF, SQLite
quick_check, critical table counts, source epochs and restore authority remain
under the existing canonical verifier. Explicit injected clients remain unchanged.
The CI test installation includes boto3 so mandatory CI executes the real SDK
retry test. The shared dev extra stays unchanged to avoid triggering unrelated
ML research for a transport dependency change.

## Verification

- New default-client regression failed before wiring: managed staging was absent.
- Owning restore and transport tests: 32 passed.
- Restore, transport, restore-drill and production offload contract tests: 58 passed.
- Real SDK test interrupts a body and verifies identical retried Range/IfMatch,
  original restored bytes and original source epoch.
- Scoped independent read-only review: no Critical or Important findings.

## Completed research receipt

Run 38633800303 succeeded on expected runtime
fdffdbde030164bf4cd00fba24ef8f6d0d133121 using the managed ops downloader.
Verified seed download, fresh live snapshot, private Yandex storage, immutable
input verification, discovery, transition audit and publication all succeeded.
Publish job 116123010178 recorded EDE_OFFLOAD_PUBLISH_OK=1 at
2026-10-14T21:49:12Z. Handoff job 116123453226 dispatched ActiveEdge on the
same SHA; downstream run 38643171332 was queued when this receipt was written.

Discovery evaluated 8,707 eligible rows and 3,960 hypotheses. Seventeen inner
FDR passes do not imply final admission: zero candidates passed all historical
gates, with one EARLY_CONTEXT and 116 INSUFFICIENT_DATA. Verdict:
NO MATERIAL SELECTIVE EDGE FOUND. Transition audit tested 1,392 hypotheses,
recorded 14 inner FDR passes and zero stability passes, with three EARLY_CONTEXT
and 99 INSUFFICIENT_DATA. Neither audit establishes production edge or historical
profit, and no new decision weights were admitted.

Full PR CI, merge, deployment and exact-SHA acceptance of the permanent wiring
are separate pending release checks. Complete the old-SHA ActiveEdge handoff
before changing the installed version. No extra research run is required merely
to repeat the completed download.
