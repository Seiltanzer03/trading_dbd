# Passive learning contract

The collector creates prospective `market observation` records without an active trade. It uses isolated low-priority feed objects and never changes the live UI instrument. Base cadence is one anchor per instrument per 15 minutes with seven horizons (15m, 30m, 60m, 2h, 4h, 8h, 24h). Demo rows are pipeline tests only (`evidence_eligible=false`). All passive components are `research_only` or `shadow_prediction`; promotion is forbidden.


## Trading-time and event scheduling

Production anchors are created only while the instrument's versioned regular
session is open. Horizon targets advance in trading minutes; outcome rows retain
both calendar and trading elapsed time plus market-open fraction. Closed-session
gaps do not invalidate an otherwise sufficiently sampled future path.

In addition to the 15-minute cadence, v1 admits a deterministic large-price
displacement trigger after a five-minute anti-clustering interval. The threshold
is max(0.75 × T0 15m sigma, 0.001 log-return), and its frozen threshold contract
is stored in every event-triggered observation.
