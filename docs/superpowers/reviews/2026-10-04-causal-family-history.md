# Causal family history integration evidence

Accepted baseline: main `9733ca81e8f81925c85274566a87c07b8de1ec6f`, tree
`3fedfe84e12c03a48290a522d2175bb8bea13cf3`, local equivalent `9fd060b`.
Original user specification is unchanged.

Implementation commits: `3e489a1`, mapped/context admission fix `343f53f`,
trainer historical-proof admission fix `e421213`.

Actual test-first evidence: initial pure 37 RED; integration 8 RED; close
scope/overflow/publication/diagnostic regressions; task-review 7 RED;
whole-feature trainer 36 RED and consistency 3 RED. Initial focused 493 GREEN,
task fix 355 GREEN, final changed scope 526 GREEN (6.19s, no warnings).

The actual collector fixture preserves the original 12 GETs. Source bundle
101,678 bytes, selected NAS100 facts 3,149/8,000 bytes, historical intermarket
extension 1,801/4,096 bytes and COT extension 801/2,048 bytes. Large original COT
history remains intact and explicitly refuses the unchanged selected budget.
Producer→frozen dataset and future-path noninterference are covered. Actual
source hashes/receipts are retained; no earlier publication/receipt is invented.

Task review found mapped own-asset exclusion and context-only positioning
admission defects. Both were fixed and fresh scoped review approved. Whole
feature Astra review found trainer admission did not validate nested historical
proof on independently supplied valid-checksum datasets. The final fix checks
recognized bounded proof/version, clocks/max receipt, source/hash identities,
windows/pairs/basket target and recursive applicability before fitting. Valid
actual producer→dataset fixtures admit five non-HOLD rows; one trade still cannot
produce a model. Fresh final scoped review approved: finding addressed, no new Critical/Important breakage.

Root broad Python on `343f53f`: 2,715 passed, 4 skipped, one previously confirmed
Python3.12 baseline SQLite/WAL failure in
`test_quiescent_sparse_clone_replays_wal_only_into_backup`, 145.41s. Guard/test
are unchanged. One local Starlette TestClient/httpx deprecation warning.
All 42 frontend syntax/smoke commands passed. Final fix has its own affected
scope GREEN; mandatory full official Python3.11/real WebKit must pass the exact
published final tree before merge. Remote release is pending.

Decisions retained:

- Implement deterministic positioning/related history first; event/valuation and
  learned conditioning have separate contracts. Wrong priority costs later
  reordering, not invented data.
- Keep compact proof under unchanged runtime bounds; broader transforms require
  another bounded projection. This limits v1 history scope.
- One coupled writer, independent read-only audits/reviews and root remote
  integration; existing user authorization removes duplicate document pauses.
- Extend trainer recognized-history admission to bind the same retained contract
  at dataset import. Wrong strictness costs refused rows or validator maintenance;
  legacy absence stays compatible.
- Preserve the one local broad run and check final fix scope, then require full
  official final-tree CI. Local-only regression discovery may be deferred to that
  mandatory merge-blocking CI; the known unrelated storage failure is not hidden.

Residuals: no empirical learned lead-lag/geometry conditioning, general event
reaction/novelty, valuation/forwards or index constituent breadth. Genuine
flow/OI, validated CFTC-to-CFD mapping, executing-broker costs/positions, lawful
PIT consensus, sufficient independent samples and closed off-host calibration
remain real-data requirements. No efficacy or profitability claim.
