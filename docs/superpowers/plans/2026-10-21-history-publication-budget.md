# History publication budget implementation plan

> **For agentic workers:** Use superpowers:executing-plans for this bounded continuation.

**Goal:** Finish immutable image publication within the existing native preparation budget.
**Architecture:** Keep sequential images and all full readbacks. Use fastest lossless gzip and four bounded off-host multipart workers instead of one. Add per-phase public timing without object URLs or credentials.
**Tech Stack:** Python, zlib, boto3.
**Spec:** docs/TERMINAL_REMAINING_WORK_2026-10-20.md and docs/AI_AUDIT_AND_HISTORY_RECOVERY_2026-10-21.md.

## Global constraints

Native preparation5400s, owner10800s, VPS capacity, all hashes/readbacks, rollback and installed-SHA fences remain unchanged. No broker action. No new source/model or weight change.

## Review focus

- Multipart request timeouts/retries remain finite; deadline callback still aborts.
- Compression bytes change, raw byte identity remains exact.
- Failed publication never authorizes handoff.
- Staging remains one compressed image at a time; additional memory limited to four16MiB parts.
- Timings contain fixed phase/kind/numbers only.

### Task1: Bounded off-host publication

Files: scripts/publish_history_image.py; tests/test_publish_history_image.py.
Consumes: existing publish_image signature. Produces: unchanged receipt and full verified GET.
- [ ] Add actual lossless fastest-gzip assertion and update injected endpoint's transfer-resource contract; watch existing implementation fail.
- [ ] Set level1, max_concurrency4/use_threadsTrue, print fixed public phase timings.
- [ ] Run publication/transport/complete-driver/owner regressions; expect all pass.
- [ ] Final read-only review, one mandatory full CI, publish exact worker revision.
- [ ] Start cutover only after green worker CI and verifying original terminal active; native owner remains installed bd615fbd.
- [ ] Verify actual outcome, then ordinary deploy and report acceptance if committed.

## Evidence and ruling

Run39558165859 built valid hot image17,808,883,712bytes from19,347,144,704bytes. Original/hot publication took23m10s/22m06s; handoff93m25s after native start, abort PREPARATION_EXPIRED. Read-only runner sample39581068650:64MiB GET6.753s;128MiB gzip6=2.128s/21,064,353bytes, gzip1=.996s/24,303,900bytes; both lossless. This is a sample, not proof of full-file latency or upload throughput. Four multipart workers address serial network waits; timings will distinguish remaining phases on the full file. Native limits are preserved; no success claim until actual commit.
