# History runtime reader closure — inline implementation

Spec: `docs/superpowers/specs/2026-10-18-history-runtime-projection-design.md`.
One integrator, one final read-only reviewer; user-authorized autonomous work.

1. [x] Inventory real SQL owners and measure current reference coverage using
   bounded covering indexes. Preserve all parents/recent/trigger/health rows in
   the current off-host builder; strict historical physical proof stays distinct.
   Initial conservative retention intentionally refuses a zero-candidate image.
2. [x] Replace blanket rejected-member retention with a verified immutable
   metadata projection for already-evaluated, hash-matching IFD rows only.
   Keep every unprocessed/mutated/eligible row and all other reference guards.
3. [x] Extract the streaming measurement-counter owner and persist exact retired
   diagnostic rollups. Bind index/rollups in backward-compatible versioned state;
   verify/consume them through backup, restore and the full current union.
4. [x] Wire actual G1 and passive status/exclusion/mutation readers. Prove causal
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

Task3 storage binding: v2 hot state now pins both the immutable rejected index
and exact bounded rollup payload; v1 remains strictly compatible. Backup/start
validation checks exact retired coverage, retained-source non-overlap, matching
ineligible membership, rollup hashes/counts and resurrection guards. Full union
removes only these two storage guards in its verified unpublished worker copy;
the existing trigger-origin denial remains for every other trigger. A real
current-union test restores full actual engine rows and consumes the projection,
preventing double counting. Focused combined profile:124passed in4.02s. The
current builder still uses conservative reference retention; projection creation
and actual runtime readers are not yet wired, so startup capability stays absent.

Tasks2–4 integrated checkpoint: the current builder now verifies rejected
membership/source hashes and writes actual v2 projections. Legacy/light passive,
G1/refinement/exclusion/mutation and measurement readers consume exact retired
metadata/counters. Real full-versus-hot engines preserve eligible IDs and a
nonempty frozen cut manifest. Full union restores source rows and consumes only
the owned derived objects. The external stdlib guard now checks actual v2 roots
and literal calculation-owner versions before allowing the declared capability.
Unprocessed, mutated, eligible, recent and dependency parents remain full hot.
The strict historical v1 manifest compatibility remains unchanged.

After an execution-environment interruption, local HEAD and the owned diff were
recovered intact. The targeted recovery profile passed153tests in4.73s. Earlier
integrated profiles recorded222 history/runtime passes and50 acceptance passes;
these are not final CI. The observed disk-pressure acceptance failure now has a
tested immediate explicit refusal instead of twelve minutes of futile polling;
no resource or completion gate is weakened. See the recovery checkpoint and
release observation documents. Task5 remains pending final independent review,
mandatory full CI and exact-SHA release. No production compaction occurred.
