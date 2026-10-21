# AI audit and history recovery — 21 October 2026

## Verified starting point

Main ee36c50d includes merged PR498. Its CI succeeded, but deploy39552338416
and SHA staging39552338357 failed while the native history owner held the host
maintenance lock. They did not install the new code.

History run39547191584 stopped at build_hot: SQLite INTERRUPT at the final
PRAGMA quick_check. The native owner processed the safe worker abort; durable
preparation/result are ABORTED_ORIGINAL, service_started=true,
abort_reason=WORKER_REQUESTED_SAFE_ABORT. Read-only run39556431332 observed
original deployed bd615fbd, service active, NRestarts0, HTTP200 and487612416free
bytes. No retirement or successful cutover is claimed.

Yandex was not replaced by the VPS. Original SQLite remained on the VPS while
immutable backups and cataloged cold rows were published to Yandex. Publishing
those copies never shrank the live database. The physical replacement has not
committed. SQLite needs a local writable hot file; object storage holds verified
archives and rollback images. Existing cloud union/runtime-reader contracts
must be retained when the smaller hot file is eventually installed.

## Attached report assessment

The supplied report has a valid structured LLM,15.8% effective weight and HOLD
preference; the admitted weighted result is CLOSE_10. Different preferences do
not alone prove a defect. Its +0.001R modeled Expected delta is within the
0.030R indifference band; bounded tail protection is the stated intervention
policy, not demonstrated historical profit. Original gate/admission remains
mandatory. No weights/risk/automatic execution are changed here.

Missing final TAKE/STOP probabilities after quote refresh are deliberate:
old bridge probabilities no longer describe the repriced quote. The common
comparison bank is not the authoritative execution-MC bank and must not be
relabeled as one. Delayed chains cannot authorize gamma/wall execution anchors.

Latest saved snapshot metadata from read-only39557212267 confirms0option audit
rows, stale_observation for market models and absent macro/source roots. This
is not evidence that all underlying sources/models are absent. Five historical
families still have separately documented missing causal data; fixing transport
does not train or authorize them.

## Bounded corrections

1. Strict compaction dropped the complete option derivative workspace even
   when numeric metric rows already existed. Preserve the existing21named
   audit rows (values, derivatives, availability, quality, units and source
   clocks), bounded12KB; keep authority labels and nulls. No raw research/debug
   replicas, new source clocks, invented zero derivatives or independent votes.
   Three behavior tests failed first, then pass through strict/emergency and
   evidence-only paths. This reproduces a real erasure path; it does not prove
   that every missing metric in the attached report was lost at that tier.
2. Final whole-image integrity inherited the almost exhausted30min copy budget.
   Reproduction uses actual SQLite progress callbacks, producing INTERRUPT
   despite completed copying. Admit a separate900s off-host integrity/hash
   phase only if copying completed inside its original deadline. quick_check,
   foreign_key_check, source identity, zero freelist and content pins remain
   mandatory. Hashing now checks that same final deadline before publication.
   A verification timeout publishes no database. Native90min preparation,
   three-hour owner, fencing, VPS reserve and rollback remain unchanged.

Owning profile51passed2.92s; surrounding runtime/owner profile99passed3.26s.
One final read-only review and mandatory exact-tree CI precede release. Actual
cutover COMMITTED/headroom/full union, exact-SHA deploy and current live report
are separate acceptance items, not established by these local tests.
