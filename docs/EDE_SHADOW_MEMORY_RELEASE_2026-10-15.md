# EDE live-shadow memory correction — 2026-10-15 UTC

Original unified-edge plan and handoff remain unchanged. This is an operational
addendum for the bounded correction in PR #475, not a replacement specification.

## Confirmed defect and correction

The live shadow materializer used the shared SQLite connection, whose adapter
intentionally fetched the raw cohort atomically. Raw frozen JSON remained in
memory while transformed feature rows accumulated. The correction opens an
owned read-only WAL transaction and uses the existing 32-row streaming path.
The derived cohort, transformations, order, fingerprint and selection horizons
remain unchanged. The shared live adapter's atomic fallback is unchanged.

The private reader has a 2 MiB SQLite cache, a cooperative 30-second SQL budget,
the unchanged 1 GiB free-space reserve, and a fresh final resource check before
publication. It closes before ledger/cache writes on success and every failure.
The in-memory/demo contract remains atomic. No new provider, service, scheduler,
execution authority, weight admission or mathematical search was added.

## Verification and integration

- Primary regressions were observed failing on the original implementation.
- Broader EDE/worker/resource profile: 195 passed, 6 subtests.
- Final strengthened regressions: 10 passed, covering all five horizons,
  145-row cohort/fingerprint equality, the unchanged 87-row selective subset,
  concurrent writer isolation, resource changes, real SQL interruption and
  reader closure without partial publication.
- Raw-extraction regression: original peak 12,649,761 bytes; corrected peak
  below the fixed 6 MiB test bound. This is not a total process RSS measurement.
- Independent final review: no Critical, Important or Minor findings; reviewer
  independently passed the 10 new and four existing runtime regressions.
- PR CI 38808213793: 4,118 passed, 5 skipped, 13 subtests; real pinned SQLite,
  frontend and real WebKit checks passed; ci/full-webkit success.
- Reviewed/tested tree: 6a4d672f84543650c3f86d693190a70706def35f.
- PR head: dd751197d8fe685324923aad556703f31213fb19.
- Authorized merge: 55fe3f784604ce8007e98e074a73ff33fc672466, same tree.
- Main CI 38812874530: full CI success, including ci/full-webkit.
- Automatic deploy 38813363531: in progress; acceptance is not yet claimed.

## Prior incident and remaining limits

The kernel confirmed a global OOM of the earlier process on the 2 GiB host;
systemd restarted it automatically. The complete allocation path was not
captured before termination. This correction fixes a reproduced raw-payload
retention defect; it does not prove that every cause of that OOM is eliminated.
The budget is cooperative SQL plus a final check, not hard interruption of
pure Python work or a bound on overall process memory.

The separate headroom operation preserved 138,116,161 bytes from 13 closed
files in existing Yandex Object Storage, with full readback/hash verification
before local retirement. No source history, active SQLite database, receipts
or model evidence was pruned. See EDE_HEADROOM_RECOVERY_2026-10-15.md on
ops/ede-yandex-headroom-588edd8-20261015 for the operation and recovery receipts.

No new manual paid AI verdict was requested for this correction. The mandatory
deployment smoke remains required. Eight trained/admitted families, optimal
weights, source maturity and proven profitability are not claimed by this fix.
