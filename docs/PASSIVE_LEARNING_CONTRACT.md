# Passive learning contract

The collector creates prospective `market observation` records without an active trade. It uses isolated low-priority feed objects and never changes the live UI instrument. Base cadence is one anchor per instrument per 15 minutes with seven horizons (15m, 30m, 60m, 2h, 4h, 8h, 24h). Demo rows are pipeline tests only (`evidence_eligible=false`). All passive components are `research_only` or `shadow_prediction`; promotion is forbidden.
