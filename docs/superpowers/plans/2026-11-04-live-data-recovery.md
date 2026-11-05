# Decision-model data recovery implementation plan

> Execute inline; one fresh final reviewer of the whole branch.

Goal: restore current decision inputs independently of delayed research captures.
Spec: docs/superpowers/specs/2026-11-04-live-data-recovery-design.md

## Task1 — current complete decision metrics

Files: decision_metric_inputs.py; existing V2/V3 feature builders (optional captured rows); ai_frozen_expert_inputs.py; edge_discovery/ai_context.py; active_edge_ai_integration.py; llm_decision_shadow.py; app.py; g1_short_horizon_routes.py; tests/test_decision_metric_inputs.py.
Interfaces: bounded live source capture/refresh -> canonical feature map and pinned frozen context -> existing matchers and readable independent LLM projection. Same92 definitions, clocks, source quality and existing15-minute maximum age. No history writes/network on the request path.
- [x] RED→GREEN: current bars produce current real metrics despite old/missing G.1 rows; all10 instruments; source receipt/future/stale data excluded; no fabricated missing values, no training writes; instrument switch cannot publish wrong data.
- [x] RED→GREEN: pinned metric vector survives later refresh and every compaction tier, is exactly decoded for matching/LLM; corrupt/wrong-instrument/future blobs fail closed, stale vectors cannot match. Blocked refresh does not block prices. Optional malformed feeds cannot erase valid bars/chain.
- [x] Implement minimal producer and consumers. Owning profile182 passed before final review. The one review found archive-writer and option-cache contention gaps; four reproductions failed RED, then passed after the integrated correction. The common configured feature TTL is pinned for LLM and rule matching too. Final profile/release follows.

## Task2 — expert publication continuity

Files: ai_frozen_expert_inputs.py; llm_edge_lifecycle.py; relevant release/publication code only if proven necessary; owning tests.
- [x] RED→GREEN: each of6 Active Edge report deliveries and changed historical hypothesis decisions cause existing materializer invalidation; unchanged clocks alone do not force repeated builds.
- [x] Existing current-SHA runs41902279938/41903781133 completed successfully. Preserve model/source clocks and exact-SHA gates; await normal post-deploy six-report publication for the next revision, no cross-SHA carry-forward or duplicate search.

## Final

- [ ] One fresh final review, one Important/Critical correction pass only, mandatory final-tree CI.
- [ ] Authorized PR/merge/exact-SHA deployment and actual model-input trace; report incomplete current-position live test explicitly.

Deferred by user steering: VDAX/EVZ work. Broad-source refresh design/RED tests retained in this plan scratch, not shipped as unfinished tests. Existing source bundle41906401502 was refreshed successfully, no scheduled background service installed.
