# Publish received native minute inputs

Continue the approved current-input restoration inline. Baseline local950e464/treeff240834 matches installedmainf3298. Live acceptance still oscillates3/26/5 of92 despite fresh quotes/options and normal RAM; no stability claim. Source inspection found that the actual installed `MarketData.refresh_intraday` runs on an isolated private view, while `instrument_refresh.CHANNEL_FIELDS` omits both new minute-reference fields. The native fetch succeeds on that view and is discarded on publication. The earlier direct acquisition test bypassed this boundary.

Scope is exactly the existing isolated intraday publication contract: include `decision_minute_reference` and `decision_minute_reference_error`. Preserve independent minute/5m/execution contracts, source hashes/receipt clocks, last-good behavior and instrument/revision/channel-generation checks. No new provider, model, metric, training, timeout, gate or order/ACK.

1. RED through real installed `MarketData.refresh_intraday`, real freeze/metric producer and external-transport-only doubles. Assert native returns and provenance for all10noncrypto instruments; cover last-good/error publication, instrument/ABA rejection and reversed completions.
2. Add only the two owned fields; GREEN owning publication/native/metric/frozen/provider tests. One fresh final review of this narrow diff, one correction pass if needed, then mandatory final-tree CI and authorized PR/merge.
3. Normal exact-SHA delivery/readiness/smoke/publications, then repeated actual runtime freshness and API latency. ExistingCLOSE_25 trade142 requires factual broker reconciliation and must never be ACKed by this work.

Ruling: this is the missing consumer boundary in the already approved source-restoration design, with one obvious correction; no new design decision or user approval is needed. Repeating standalone provider probes or changing freshness thresholds would not repair the discarded publication.
