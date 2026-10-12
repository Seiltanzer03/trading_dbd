# Active Edge: reuse completed research after delivery failure

Baseline: accepted main `2d2193008bb5ab94f62a7e5eeeef3a647f877ba7`,
tree `164bff6fbbdbd5efb5330bf300814d3b8452529c`, PR #456.
Scope: the existing global plan's exact-SHA publication boundary; no additional
sources, model searches, weight tuning, broker costs or live orders.

## Production evidence

- Deployment `38244531517` passed actual readiness, public HTTP and functional
  smoke. `/api/ai/verdict` returned HTTP 200 in 6718 ms, within the unchanged
  12000 ms success gate. This real provider request is not a zero-paid-call fixture.
- Inventory `38246680021`, job `114802187131`, completed successfully at
  12:30 UTC: 82501 observations, 23498 resolved outcomes, 96 canonical features;
  65 DATA_READY, 27 insufficient independent evidence, 2 management-only,
  2 quality-only. The job took approximately 26 minutes; no measured memory
  or WAL-growth improvement is claimed.
- EDE audit `38249449724` and publication succeeded. The baseline-admissible
  historical cohort has 8500 resolved rows across ten instruments. The audit
  found no material selective edge under its existing gates; this result does
  not remove the separately published working mathematical models.
- Mathematical publication `38246599523`: 24 fitted instrument/family groups,
  14 with working historical admission, including four unchanged retained fits.
- Active Edge run `38258069964` computed all five structured horizons and the
  ML challenger. The 15-minute calculation completed, but its publication
  failed at the pre-transfer `/api/state` probe: three attempts each timed out
  at the existing three-second limit. Its report was preserved as an artifact.
  The other five publications succeeded. No cause of sustained server latency
  is inferred from this single failure.

## Finite change and acceptance boundary

Research jobs `structured` and `ml` now only calculate and preserve their
reports. Separate bounded `publish-structured` and `publish-ml` jobs download
the named artifact from the same workflow run and invoke the existing publisher.
A failed publication job can therefore be retried without rerunning research
or reacquiring historical inputs. A missing artifact is an error; partial
matrix research failure does not suppress delivery of other saved horizons.

Expected SHA still comes from `resolve`, checkout remains pinned, and the
publisher keeps its JSON, non-authority, size and production-SHA checks.
Original model/source clocks are not refreshed by delivery. Existing source
freshness/admission and three-second API probes remain unchanged. A retry after
a different production revision remains a SHA failure, not a rebinding.

Five workflow regressions observed RED before the change. The owning profile
contains those graph/actual-shell-command checks plus exact-SHA publisher and
bounded probe regressions. These isolated tests make no provider or order calls.
The local workspace was restored as a targeted exact-baseline connector export
after the previous checkout disappeared; a complete repository CI is therefore
mandatory before merge. Independent final review found no issues. Initial full CI found one existing
artifact-preservation regression (3847 passed, one failed); the prior upload
condition and optional-file preservation behavior were restored. Missing named
artifacts still fail the publication download. All 36 owning checks then passed.
Corrected exact-revision full CI remains mandatory; the new workflow is not yet
released or demonstrated on production.

Stop after the final review, mandatory green CI and exact-SHA deployment. Record
the first actual split-job publication receipt separately; configuration and
offline tests alone do not prove delivery or all eight predictive families.
