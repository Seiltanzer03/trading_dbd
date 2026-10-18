# History image transport implementation plan

> Execute inline with executing-plans, one final independent read-only review.
> The user authorized autonomous implementation; no new approval menu.

Goal: supply bounded authenticated-content streams for the already proven
transaction interface without adding a production invocation.
Spec: docs/superpowers/specs/2026-10-18-history-image-transport-design.md.
Base: local4f0910f, same tree as runtime PR489 candidate409f77a.

## Task1 — strict private stream adapter

Create scripts/history_image_transport.py and
tests/test_history_image_transport.py. Interface:
`SignedImageTransport(private_inputs, *, deadline, opener=None)`;
`open_image(kind)` context manager and `verify_image(kind)->dict`.
private_inputs maps original/hot to url and receipt; the receipt binds run_id,
kind, bucket, object_key, codec, database_sha256/database_size_bytes and
compressed_sha256/compressed_size_bytes. Contract: sqlite-history-image-v1.

- [x] RED actual SQLite/gzip full roundtrip, separate raw/compressed corruption,
  truncation/excess, HTTP length/status/encoding, expired/stalled read deadline,
  caller mutation, secret-free errors/repr, early-close and origin/redirect
  refusal. Run only this profile and observe failures before implementation.
- [x] Implement strict input validation, canonical key/origin, nonredirecting
  opener, bounded raw/compressed stream hashing and monotonic timeout checks.
  No URL in receipts and no automatic retry/drain on failure.
- [x] GREEN profile plus transaction integration using actual cloud-format
  streams; source remains unchanged and interrupted stream recovers original.
- [ ] Commit exact owned files; final independent review and mandatory CI on
  the final integration revision. Do not repeat CI for an unchanged tree.

## Task2 — exact off-host image publication

Create scripts/publish_history_image.py and tests/test_publish_history_image.py.
Interface publish_image(database, identity, *, run_id, kind, client, deadline,
opener=None)->dict returns Task1 pins plus full_get_verified=true after actual
readback. Client factory uses exact Yandex path-style SigV4 endpoint and finite
request limits. This helper owns no live service/capture authority.

- [x] RED real SQLite/gzip publication and source hash/size, source mutation,
  staging capacity, wrong endpoint/config, HEAD/body corruption and failure
  cleanup cases. Cloud I/O alone is injected; byte/hash behavior is real.
- [x] Implement bounded packing and source identity, deterministic addressed
  key, single-thread16MiB upload, strict metadata and Task1 full GET validation.
- [ ] GREEN changed profile, commit, then one final whole-package read-only
  review. Full CI belongs to the final production-owner integration revision;
  this prerequisite may be preserved as explicitly unmerged WIP meanwhile.

## Following durable-owner tasks, not completed by these helpers

- [ ] Publish fresh original/hot images to exact content-addressed keys and
  perform real complete cloud readback. Preserve known catalog partitions.
- [ ] Integrate a finite local owner holding the existing maintenance lock and
  persistent start guard. Prove frozen source capture below the live reserve as
  a separate offline operation; never relax live snapshot admission.
- [ ] Verify actual hot size, runtime capability and restore/rollforward before
  an authorized live invocation. After commit, never restore old over new writes.

Pre-flight: stream adapter consumes no runtime-reader internals. Controller
already consumes open_image(kind), retaining its own whole raw SHA/size proof.
No production mutation is involved in this transport development.

Task1 development evidence: the new adapter was absent at the initial run
(23 missing-module failures); after implementation23cases passed in0.88s.
Two additional real-transaction regressions preserve the exact original after a
corrupted hot gzip stream and propagate FencingError unchanged through context
cleanup. Combined transport/controller/upload helper profile:62passed in2.43s.
No actual cloud full-GET receipt or production transport is claimed.

Task2 development evidence: initial13missing-module cases were observed before
implementation. Actual source packing, both digest identities, addressed keys,
HEAD/body corruption, mutation, reserve and request configuration now pass.
Combined publication/transport/controller/upload profile:75passed in2.84s.
No cloud upload or live source capture was invoked; real source/epoch proof,
independent final review and integration CI remain separate requirements.

Final independent read-only review of8c2729130647bec2dcc22aac32614d29fb5f9654
against4f0910f found no Critical/Important findings. Reviewer independently
reran75tests in2.73s and confirmed clean diff/working tree. Code is a reviewed
prerequisite; production-owner integration and final full CI remain pending.
Runtime PR489 meanwhile merged as44678536c5612b14db5b9d3c4bc24289f2c061a7
after CI39116215068 (4439passed/5skipped/13subtests, real systemd, pinned SQLite,
WebKit and aggregate acceptance green). Its production deploy39116843707 is
still starting; delivery is not yet a full research/disk acceptance claim.
