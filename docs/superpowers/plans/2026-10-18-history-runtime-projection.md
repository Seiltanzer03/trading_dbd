# History runtime reader closure — inline implementation

Spec: `docs/superpowers/specs/2026-10-18-history-runtime-projection-design.md`.
One integrator, one final read-only reviewer; user-authorized autonomous work.

1. [x] Inventory real SQL owners and measure current reference coverage using
   bounded covering indexes. Preserve all parents/recent/trigger/health rows in
   the current off-host builder; strict historical physical proof stays distinct.
   Initial conservative retention intentionally refuses a zero-candidate image.
2. [ ] Replace blanket rejected-member retention with a verified immutable
   metadata projection for already-evaluated, hash-matching IFD rows only.
   Keep every unprocessed/mutated/eligible row and all other reference guards.
3. [ ] Extract the streaming measurement-counter owner and persist exact retired
   diagnostic rollups. Bind index/rollups in backward-compatible versioned state;
   verify/consume them through backup, restore and the full current union.
4. [ ] Wire actual G1 and passive status/exclusion/mutation readers. Prove causal
   before/after equivalence with actual engines and negative corruption cases.
   Declare the runtime startup capability only after these checks pass.
5. [ ] Independent review, mandatory full CI and exact-SHA release. No live
   retirement before the separate finite owner, authenticated current images
   and measured physical reserve are accepted.

Development checkpoint:7 initial reference-retention REDs, then GREEN; the
five-file history profile passes63tests in2.81s after adding the expired-budget
case. The existing successful current-union fixture now carries real runtime
metadata and1000 recent rows and retains its G1S source parent. Reference
retention is only a partial prerequisite: no runtime capability or full reader
equivalence is claimed. The production coverage probe proves blanket membership
retention yields zero candidates, motivating the explicit projection above.

Task2 foundation: the unchanged canonical source-record owner was extracted to
stdlib-only `seiltanzer/g1_source_record.py`, with three pinned pre-extraction
hash vectors and a real `python -S` load. `history_runtime_projection.py` now
builds an immutable metadata-only index from actual PassiveLearningEngine rows,
requiring existing ineligible current membership and exact canonical source
hash. Real-source RED9 cases were GREEN after implementation; additional atomic
second-row failure, expired deadline and restored-guard corruption cases pass.
Combined G1/source/index/history profile:64passed in2.91s. This foundation is
not wired into a deployable hot state: full counters, transport binding and
reader acceptance remain pending, and no runtime capability is declared.

Task3 owner extraction: `measurement_path_runtime.runtime_counters_for_rows`
preserves the existing telemetry formula and runtime locking while allowing a
single-pass off-host iterator. A pinned real IFD fixture preserves its existing
horizon error and diagnostic pristine-candidate count without making it an
admitted outcome. `passive_history_statistics.py` owns the previous literal
LIKE/count predicates; legacy status now uses the same aggregate, with unchanged
NULL/JSON-whitespace semantics. RED3 then focused GREEN49 in2.07s. Rollup
persistence and reader wiring are not yet complete.

## Recovery checkpoint after local reader integration

The final local integration passed a 222-test profile and a separate 50-test
acceptance/orchestration/disk profile before the execution environment went
offline. These changes are not yet committed/pushed or finally reviewed; the
unchecked integration/release tasks above remain open in this remote plan.
See `docs/HISTORY_RUNTIME_RECOVERY_CHECKPOINT_2026-10-18.md` for the exact local
HEAD, owned file list and recovery sequence. PR488 delivery/HTTP succeeded, but
production research acceptance failed on unchanged disk admission; see
`docs/HISTORY_FENCE_RELEASE_OBSERVATION_2026-10-18.json`. No live history
retirement or reclaimed production capacity is claimed.
