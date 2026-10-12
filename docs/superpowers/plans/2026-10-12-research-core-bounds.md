# Research core bounds recovery

Goal: preserve real worker progress while isolating full-history maintenance and avoiding bulk JSON allocation in the acceptance-visible core.
Base:23c74b6, tree39c73598, equivalent delivered mainc302cb9f. Production deploy38207773907 failed because first core had no completion within12minutes. API responded; logs do not identify its substage. No claim of a confirmed exclusive server root cause.
Spec: locked global plan and OPENBUILD_SUPERPOWERS_WORKFLOW; user authorizes autonomous fixes and release after full mandatory CI. No risk, source authority, research-evidence gates, acceptance timeout or trading changes.

Task1: materialize compact source-row metadata, fetch original full rows one at a time, limit one batch to16MiB accumulated JSON (allow one indivisible larger row so progress cannot stall), optional60second cooperative budget checked between rows. Preserve source-row watermark only through attempted rows; remaining rows are retried next cycle. Resolution SELECT omits unused frozen JSON. Write regressions first for byte/time progress and actual Python allocation.
Task2: worker resolution defers whole-history champion aggregation to its own low-priority maintenance phase; direct callers retain prior behavior. Expose current core stage and stage timestamp using existing lock-free status, print in failed acceptance diagnostics. Core completion/error gates unchanged. Tests pin real resolutions, later maintenance refresh and stage visibility.
Task3: one independent final read-only review, one reproduced fix pass, owning tests, full mandatory CI, authorized merge/deploy/exact-SHA acceptance. Do not declare the old deploy accepted or rerun it blindly.

Review focus: skipped/ineligible source rows must not disappear behind an overadvanced watermark; huge single records must not cause starvation; errors cannot become success counts; deferred progress must actually refresh through maintenance; JSON allocation bound is cooperative and not a hard per-row/SQL wall timeout.
