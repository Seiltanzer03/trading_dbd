# Evidence cache memory implementation plan

> Execute inline; one final fresh review, one correction pass.

Goal: eliminate duplicate retained report graphs while preserving exact report semantics.
Spec: docs/superpowers/specs/2026-11-04-evidence-cache-memory-design.md
Constraints: no resource-threshold, data, risk, model or report-content changes.

Task1 owns g1_short_horizon_evidence_nonblocking.py and tests/test_g1s_evidence_cache_memory.py. Consumes existing _cache_report/_present_report/_present_report_json; produces identical public outputs with single encoded cache.
- [ ] Add real tracemalloc retention regression and exact decoded/HTTP output, isolation and invalid cache tests. Watch retention RED on baseline.
- [ ] Remove parsed snapshot retention; decode only for legacy dict reads, fail closed. Preserve preencoded HTTP path and fresh age. Bump cache version.
- [ ] Run regression and evidence/nonblocking/materialization/production-resource profiles. Expect allPASS.
- [ ] One final review; one Critical/Important fix pass with regression. Full mandatory CI once; authorized push/PR/merge/deploy and exact-SHA model refresh/runtime observation.
Review focus: large nested arrays, corrupt/missing cache, stale replacement, age/authority flags, HTTP no re-encoding or DB access.
