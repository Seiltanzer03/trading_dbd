# Trade position state contract

Version: `position-ledger-f2-v1`.

The immutable `position_management_events` ledger is authoritative for real-user economic exposure. `CLOSE_X` always closes X% of the current remainder: 1.00 → CLOSE_25 → 0.75 → CLOSE_50 → 0.375. One `decision_id` can create at most one event. Original STOP is never overwritten by an armed break-even barrier. Pending decisions are superseded by a newer review or geometry/state change; stale execution returns HTTP 409.
