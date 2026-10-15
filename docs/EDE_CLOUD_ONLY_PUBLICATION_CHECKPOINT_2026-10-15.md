# EDE cloud-only publication checkpoint — 2026-10-15

Accepted production remains 724ce141e8ab7f0813d48c487d896e64fc249255. Original plan/handoff are unchanged.

Scoped fix: PR #472, operator 7669de3dcd839ba268f5f102b06fcae0d5b57a0c, exact tree 3e4c2f2b06a5d1460ca5166c02cbf5f4e77d19ae. One final independent review found no Critical/Important findings. Owning tests: 65 passed, 6 subtests; includes actual emitted helper/lsof/receipt publication/next full cloud restore. Final mandatory CI is run 38697030484; release is gated on its success.

The one-time operator is serialized under the canonical ede-v13-heavy-research group. It waits for source producer 38689302428 to finish; validates exact source and reviewed/green operator identities; downloads only that producer's completed artifact; and republishes through the tested cloud-only transport if original publication failed. It never reruns scientific research or snapshot/source collection. Already successful original publication is a no-op. Eight simulated operator-contract scenarios passed, and embedded Python/shell syntax checks passed.

Any cache retirement is limited to the exact optional old gzip after fresh complete cloud restore plus unchanged receipt/hash/size/closed-file checks. The receipt, immutable Yandex objects, DB/WAL and history remain. The 1 GiB reserve is unchanged. It is not a general solution to authoritative SQLite growth.

Do not merge/deploy a new installed SHA until this producer/publication ownership is terminal. Do not replay older failed producer 38682439464. Publication and deployment are not yet claimed complete at this checkpoint. Existing accepted ActiveEdge and model deliveries keep their original timestamps and lineage; no duplicate fitting is requested merely for this storage correction.
