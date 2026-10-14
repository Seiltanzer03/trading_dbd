# Installed-version acceptance continuation — 2026-10-14

Target main/runtime: `93c97d5c446e2dfb8f9efdbe787d675bc3de667a`, tree `0754fbef1e8f335d9fc3016c056a012ddb998d79`.

The original deploy38562255159 failed twice: official BLS transport503, then unchanged150s AI snapshot readiness budget. Its failure cleanup released the original cooperative research gate. Separate installed smoke38569260138 subsequently passed, with actual AI HTTP200/8367ms, but did not repair the failed deployment statuses or continuous downstream ownership.

A bounded one-off continuation is now running: [38573621412](https://github.com/Seiltanzer03/trading_dbd/actions/runs/38573621412), branch `ops/installed-acceptance-93c97d5-20261014`, commit `efd962dfd2861cf33a09c3cea1979f2c1c96e258`.

Only branch-local `.github/workflows/deploy.yml` differs from runtime/main. The one-off push replaces physical delivery/restart with actual installed HEAD, tracked-runtime cleanliness and active-service checks, rejects a still-active acceptance owner, and requires original exact-SHA green CI. It creates a fresh acceptance owner from the current run ID. The existing gate acquisition, worker pause, readiness, official macro bundle, functional smoke, public probe, three publications, post-research handoff, failure cleanup and outcome-dependent status publication are byte-identical to the original acceptance tail, except truthful production status wording describing installed-SHA revalidation. No outcome is prefilled; every condition must actually succeed. No runtime/main code, thresholds, timers, source admission or trading authority changed.

Verification:18 existing orchestration/readiness tests passed in0.58s; YAML and15 shell blocks parsed; branch restriction, immutable target, production concurrency, unchanged permissions, read-only delivery and unchanged complete acceptance tail checked. The already-green full runtime CI is not rerun. No new training beyond the original required per-version publication is requested.

At this checkpoint the installed-SHA/CI verification passed and cooperative pause is running. Formal acceptance is still pending. The eight-family input blockers and absence of proven empirical profit remain unchanged. Existing Yandex Object Storage and GitHub configuration remain the only storage/release infrastructure.
