# Bounded FOMC latest HTTP admission

Accepted baseline tree8dbc01df, production b96cca445056297f43fd8978d2f4751d6cbff44c.
Read-only diagnostic38404485915 measured macro/status0.079s but FOMC latest
timed out15s. Service NRestarts0; runner/local/public-self state allHTTP200.
This is an observed route delay, not proof of a terminal outage or an OOM cause.

The FOMC latest route entered the shared SQLite RLock before table discovery,
release selection and read-only retained-rate projection. HTTP had no admission
bound when another research operation held that lock. A real HTTP/SQLite
regression reproduced waiting while a separate thread held the shared lock.

Only the FOMC latest HTTP route now opts into immediate lock admission. Busy
returns UNAVAILABLE/FOMC_RELEASE_STORE_BUSY; it does not substitute old/default
values or report an empty history. Successful admission holds the same lock
through the original read/projection and releases it in finally. Ordinary
historical readers keep their original blocking contract. Original source/hash,
publication/fetch clocks, causal cutoff and research-only authority remain.
This bounds lock admission, not arbitrary SQLite execution or source acquisition.

Validation: observed RED waiting for the writer, then GREEN. Busy real HTTP and
three unlocked causal cutoffs pass;221owning macro/event/prospective tests pass,
one existing Starlette TestClient deprecation warning. Independent final review
and mandatory exact-tree CI remain required. This diff is isolated from ongoing
EDE38404251655; no current production code/model/position changes are claimed.
