# CI recovery: Ubuntu mirror network failures

Unreleased checkpoint based on main `9cb20e4dfdb63d11cda3863415b7d52617d0d213`
(PR415, tree `9f0dba4748f8f964ad9e368b8f211934e141ef26`). Production415 is
not accepted; previous confirmed production is PR414 SHA4c64e27f723e48a43523e0706e42b10f5d4f5e4a.

Main CI37655275606 first failed while GitHub created acceptance, with UI annotation
`Internal server error. Correlation ID: 2c2df17e-25d4-4514-bbbb-5af0c020e2c5`.
Python/browser jobs passed. User approved one full same-SHA retry after the
automatic approval reviewer required explicit consent; browser UI started attempt2.

Attempt2 browser112986004669 and failed-jobs-only attempt3 browser112988534950
both failed installing WebKit system dependencies. Connections to the forced
HTTP archive.ubuntu.com endpoint failed across multiple IPs; exit100 preceded
real WebKit tests. Python passed; acceptance correctly published failure.
Identical retries were stopped. No model or application bug is inferred.

The CI installer now probes a finite official mirror list (HTTPS archive,
runner Azure archive, previous HTTP archive), with three-second connection and
eight-second total probe limits. It writes APT sources only after a reachable
mirror is found. Signed APT verification stays enabled; strict update error mode
rejects failed indexes instead of using partial/stale lists. Dependency failures
remain failures. Five-minute step bound and mandatory real WebKit tests remain.
No production collection, data, gates, credentials or infrastructure resources change.

Four shell behavioral checks failed before implementation and passed after it;
bash syntax and git diff checks passed. Local checks used Python's standard
library to run the same pytest-compatible cases because the old local pytest
environment was unavailable. Network/sudo/package side effects were simulated.
Actual mirror installation and WebKit must pass mandatory full CI before merge.
Exact-SHA deploy/readiness/smoke/public HTTP/publication proofs belong in the PR
after acceptance, without a subsequent documentation-only release.

P1 alternate GitHub polling sessions remain unapproved and unimplemented.

## Follow-up: measured package-download budget

PR416 CI37679858554 succeeded:3133 passed/4 skipped, real WebKit and acceptance
green. Merged864bb6d2cd287a47cedbc0b41079538be8de57dc. Main37680279827
browser112994218088 selected Azure fallback after HTTPS SSL timeout. Signed
indexes downloaded52.1 MB in5s, then the87.1 MB package download progressed at
roughly100 KB/s (package names/sizes advanced throughout the log). It hit the
existing five-minute step limit at20:18:10Z before installation completed.

The initial recovery checked reachability, not package throughput. Its PR check
therefore did not prove the fallback could finish within five minutes on every
runner route. The follow-up changes only this installation step to a finite
20-minute bound, covering roughly15 minutes for87 MB at observed throughput.
The enclosing browser job has a finite30-minute budget so the dependency step
plus browser download and actual tests can finish. Independent review caught
the original15-minute job limit conflicting with the new20-minute step limit;
the budget invariant failed before correction and passed with30 minutes.
No omitted dependency, disabled browser test or green-status override. Other
step bounds and product code remain unchanged.
