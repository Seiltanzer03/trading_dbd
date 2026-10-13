# CFTC revision history implementation plan

> Execute inline with superpowers:executing-plans; one final read-only reviewer.

Goal: preserve real existing CFTC historical rows and their source revision clocks
before extending positioning training. Spec: original unified-edge plan §7 and
user13.10.2026 request to finish all eight input/model/forecast chains.
Architecture: same bounded existing GET selects required economic/system fields;
an optional validated packet retains exact received bytes and canonical versions.
Tech stack: Python stdlib and existing source bundle; no new provider/dependency.
Baseline6f5183e/tree8dbc01df equals accepted production b96cca4 tree.

Constraints:128rows/64KB body/128KB packet; no extra collector requests, no backdated received/publication
time, no proxy mapping promotion, fitting, model votes, broker/cost/position writes.
Socrata created/updated timestamps are source revision times, not publication
receipts. A revision clock alone cannot grant historical forecast admission.
Existing net/OI collection continues when optional archive metadata is missing.

Review focus: missing/timezone-free/future/reversed clocks; duplicate row/date;
wrong market; mutated raw/canonical bytes; optional failure hiding valid net/OI;
bounded query/bundle size; changed lookup key between actual source/model clocks.

Task1 — validated source version packet.
Files: new seiltanzer/cftc_revision_history.py and tests/test_cftc_revision_history.py.
Interfaces: capture_versions(body, contract, received_ts, source_id) -> packet;
validate_versions(packet) -> same canonical packet after raw-byte reparse.
- [x] RED: source clocks retained separately; exact body/hash; mutation/future/naive
  clocks rejected; missing metadata refuses optional packet; OI absence stays null.
- [x] Implement typed bounded parser/validator; publication_verified=False and
  historical_forecast_admission=False remain explicit.
- [x] GREEN owning parser regressions and commit.

Task2 — existing collector integration.
Files: edge_family_sources.py; same new tests and existing CFTC/source profiles.
- [x] RED: one existing GET selects revision fields and attaches validated packet;
  missing metadata retains original net/OI and original first-seen receipt.
- [x] Wire packet once outside live scoring records; optional archive failure exposes safe reason.
- [x] Run owning CFTC/history/source/market profiles; final independent review.
- [ ] Push isolated PR and exact-tree CI; no deploy invalidation of EDE38404251655.

Task3 — one bounded actual source archive operation.
Files: scripts/archive_cftc_revisions.py; cftc-revision-history-audit.yml.
- [x] RED→GREEN: full Yandex byte restore of a validated packet; corruption refuses receipt.
- [x] Reuse existing lossless private archive helper and YC_STORAGE Actions secrets;
  fixed3GETs with90s budget, no SSH/VPS/database writes. No recurring acquisition.
- [ ] Preserve all3 actual source packets with full cloud GET/receipt proof. Only
  receipts are public artifacts; raw packet bytes remain in existing private bucket.

Progress:24baseline CFTC tests passed. New collector regression observed RED;
13source/history regressions GREEN. Initial330owning profile exposed one legacy
fixture missing optional metadata; optional archive diagnosis is now separate
from source collection errors, with an explicit unavailable packet. Full packet
was moved to bundle root to avoid inflating current review inputs; live source
records and first-seen clocks remain unchanged. Final owning profile331passed.
Actual one existing-source probe validated128Gold rows/47233body bytes,
SHA25622d24360744e4d2627ae25f5ea41cc7b5d8bdbfd3b6cc7a606c9d75fcb29d194.
No past publication or forecast admission is inferred from this source revision clock.

Next boundary: use retained actual versions plus independently verified public
availability to build positioning train/validation/test inputs. Dates inferred
from an intended calendar must not be called observed releases. Do not call this
source-preservation milestone a trained positioning forecast or eight-family
completion. Other missing-family packages remain on the original global plan.

Final verification:357 owning tests passed in4.90s; git diff --check clean.
Fresh final reviewer found no Critical/Important issues; isolated parser checks,
archive restore/corruption checks and diff validation passed. Reviewer pytest
was unavailable in its execution environment;357 was verified by implementer.
Final: minor (deferred): float count parsing can round tiny fractional strings.
Final: minor (deferred): malformed offset minutes can normalize through datetime.
Both affect malformed optional packets only; raw bytes retained and all authority
flags remain False. No additional polish pass; genuine source availability and
CFTC→CFD training association still require independent proof.
Final: Ruling: archive execution/private credentials are verified by the actual
one-off workflow, not inferred from review; source publication/model admission
remain explicitly outside this preservation package. Cost if wrong: no cloud
completion claim and no admissible forecast until observed receipts exist.
