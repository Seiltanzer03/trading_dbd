# P2: bounded private archive transport

Accepted baseline: remote main77407e8, local68cfff4, matching treee0a39202.
Authority: LOCKED_GLOBAL_PLAN_2026-10-07.md, P2, and original unified-edge spec.

The public repository correctly refuses to export private frozen reviews to
public GitHub artifacts. Existing private Object Storage already retains the
verified database. This package prepares a bounded archive transport there;
it does not change that refusal or enable an actual export workflow yet.

Use the existing bucket trading-dbd-backups-2026 and a distinct
edge-family/v1 prefix. Two alternating archive slots and one latest receipt
bound retained objects. The caller must serialize writers; wiring a constant
workflow concurrency group is a separate acceptance prerequisite. No bucket,
ACL, IAM, source, model template, research budget or production policy changes.

1. Transport the existing archive.json bytes unchanged. Verify the contract,
   <=512 episodes, dataset hash, duplicate/nonfinite JSON rejection and the
   existing 96MB raw ceiling. Verify compressed and raw hashes and sizes on
   restore; bound reads and decompression before JSON parsing. RED: missing
   implementation, corruption, oversized body/bomb, bad contract/hash. GREEN:
   exact-byte roundtrip retaining source clocks and history SHA.
2. Read latest first; only explicit NoSuchKey means INITIAL. Permission/network
   failures and malformed latest fail closed without selecting an older slot.
   Store into the inactive slot, verify a full readback, then publish latest.
   Require a strictly newer run generation and compare the previously restored
   pointer before writes. Interrupted inactive-slot upload preserves latest.
   RED: verification failure/stale generation/changed pointer/foreign key.
   GREEN: prior committed archive remains restorable and generation alternates.
3. Profile checks: transport + archive/dataset/training/pipeline/admission.
   One fresh whole-package review, one final exact-tree CI. No credentials in
   tests; no real export, upload or duplicate mathematical search in this stage.

Interfaces: restore(client) -> (raw bytes or None, receipt or None);
store(client, raw, source_sha, generation, previous_receipt, uploaded_ts) ->
receipt. Source SHA belongs to this archive generation; upload time is storage
metadata only and never modifies source availability/capture/label clocks.
Transport receipt grants neither runtime/model activation nor backup retirement.

Review focus: oversized/truncated streaming bodies, gzip bombs/trailing members,
duplicate/nonfinite JSON, pointer/slot mismatch, source lineage preservation,
partial writes, missing versus inaccessible storage, unsynchronized callers.
Completion limit: tested transport ready for controlled workflow integration;
actual private-data accumulation/model availability remain unproven.
