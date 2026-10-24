# Cooperative shadow resource budget and current process status

Read-only production observation24Oct19:14MSK on installed392f648:
actual `/proc/3760121/status` VmRSS1448460kB, while worker status reported the
older479.32MiB admission sample as normal. The worker remained in
maintenance:ede_shadow, started19:00:49MSK, through19:20:49; actualRSS was then
1250440kB. This establishes a resource/telemetry problem, not the exact allocation
stack. In particular the preceding LLM lifecycle is outside shadow read bounds.
No new restart, unknown reader termination or data mutation was used to claim a
memory fix.

Two focused behavioral reproductions expose a concrete gap in the existing
owned shadow reader. SQLite progress callbacks do not run during Python work on
an already fetched batch: after the first row exhausted the30s budget,32rows
were converted before the next SQL interruption. Also, growing process pressure
was never checked within that extraction. The route independently returned a
stale64MiB worker sample when current process readings were128/1536MiB.

The private consistent reader now supplies a cooperative budget callback to the
adapter. It checks before/after feature conversion, during causal-bar dictionary
construction and between aggregate transform stages. The same30s deadline and
1GiB disk reserve remain; fresh process pressure is sampled at the existing
quarter-second resource cadence and uses the unchanged soft threshold. Refusal
closes the owned reader, discards incomplete rows and publishes no partial
shadow ledger/cache. Normal readers without this callback retain the same full
cohort and feature fingerprint. It is cooperative, not preemption of one long
Python call, and does not bound the preceding LLM lifecycle or establish the
cause of every production allocation.

Worker-status now samples the current process through the existing procfs
reader on each response copy, without SQLite, runtime locks or mutation of the
worker's admission/control state. The timestamp identifies this new sample;
`memory_pause_active` continues to describe the actual worker state.

RED: three consumer-visible failures (late Python cutoff, absent memory cutoff,
stale endpoint memory). GREEN owning profile:30passed7.03s, one existing
Starlette/httpx deprecation warning. Existing real-WAL consistency, full-cohort
fingerprint, failure cleanup and no partial publication tests remain green.
Independent review and mandatory final CI are separate release requirements.
Keep this change isolated until the current d2222a deployment/research chain no
longer depends on its exact installed SHA. This is not an eight-family/LLM or
historical-profit completion claim.
