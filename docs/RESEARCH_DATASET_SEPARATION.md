# Research dataset separation

Three layers are non-interchangeable: passive market forecast evidence, virtual-position management evidence, and real-user-trade management evidence. Reports must show all three separately. Passive/virtual results cannot support claims about realised user management improvement. Real trades preserve human selection bias rather than pretending to be a random market sample.


Virtual management now uses a dedicated immutable
`virtual_position_observations` cohort. Deterministic states r0 ∈
{-0.5, 0, +0.5, +1.0} are evaluated in both directions at 60m and 4h on the
same subsequently recorded real market path. HOLD/CLOSE_10/CLOSE_25/CLOSE_50/
EXIT share that path and relative-remainder semantics. Reports publish policy
regret only as virtual research and explicitly forbid claims about real-user
improvement.
