# Single representation evidence cache

Preserve complete report contents, fresh age, fail-closed missing data and the preencoded HTTP path. Keep only encoded bytes in the long-lived report cache; legacy dict consumers decode their own copy. SQLite data, evidence/math/source/risk gates, resource thresholds and report writers remain unchanged.

Measured production serialized report sizes total92.8MB, including39.4MB probability_oos and36.9MB ablation. Existing facade retains both parsed object graphs and encoded bytes. This is a concrete avoidable retention source, not a claim that all process memory has one owner.

Approaches: increasing memory thresholds violates the task; clearing reports loses ready UI state; retaining only the existing complete JSON representation removes duplicate object graphs and keeps HTTP fast. Implementer chooses the last approach under existing autonomous repair authorization and user opt-out from repeated planning questions. Cost: explicit legacy dict reads pay JSON decoding; production HTTP continues to use encoded bytes.

Acceptance: real allocation regression proves released object graphs; all report values and age match; invalid input fails closed; original fast HTTP and lock/SQLite isolation stay intact. Actual post-deploy RAM/worker cycles must be measured before claiming complete runtime recovery.
