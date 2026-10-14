# FOMC numeric macro history — execution ledger

Plan: original unified-edge §7 / effective unified-edge package B. Baseline local2ae2611/tree61853ab equals merged main468dc8c. Work remains isolated on feat/fomc-macro-history-20261014; no changes to the release currently being accepted.

Observed gap: canonical historical bundle validates and retains FOMC statements, but verified_macro_archive only creates BLS/ISM feature points. Actual accepted4dd off-host build38447965142 contains9FOMC records,0BLS and4ISM; FOMC numerical history never enters the existing finite macro trainer.

Bounded design: add exactly the existing target-mid percentage and target-change basis-point features to the fixed archive search. Reuse canonical bundle validation and the existing statement timestamp/parser. Keep actual fetch clocks; dated pages are historical publication reconstruction, NOT versioned-vintage or first-receipt proof. Missing predecessor means missing change, never zero. Preserve predecessor source identity and purge shared publications. No new provider, acquisition, scheduler, paid call, quote switch, position/cost mutation or gate relaxation.

Verification: real canonical fixture RED→GREEN for source clock, rate units, missing predecessor, declared vintage limit, dataset/group behavior and source tampering. Existing macro/archive/market/retention/FOMC profile; one fresh final whole-package review, one tested Important fix pass if needed; final-tree CI once. Existing trusted-PR math workflow supplies the actual finite source/model result; do not declare a supported forecast before its result.

Ruling: use the already validated official dated FOMC archive in its existing offline-publication role — the source already declares OFFICIAL_DATED_PAGE_NOT_VERSIONED and retains actual retrieval clocks. Cost if wrong: a later page revision may affect reconstructed history; this cannot establish strict original-vintage knowledge, a contemporaneous receipt, or observed profit. Preserve that limitation explicitly in state, report and model provenance.

Ruling: two numerical fields only — existing units/names and a fixed search, no sentiment/novelty/consensus invention. Cost if wrong: useful other fields are not searched in this package; a negative finite result remains valid.

Pre-flight: existing archive state feeds archive_market_dataset→the fixed purged pretest selector→existing runtime macro facts. Publication time comes from the canonical statement header, not the approximate schedule. A change depends on current and predecessor publications; neither may cross training/scoring as independent evidence.

Ruling: use macro.fomc.target_mid_pct / macro.fomc.target_change_bp — the existing runtime family adapter uses dotted names; FOMC_DETERMINISTIC_FEATURES names the separate EDE vector with underscores. Cost if wrong: a trained model would fail runtime matching; verify against the actual adapter, not another mapping constant. No runtime rename is needed.

Skill fallback: the TDD skill's linked writing-good-tests resource was unavailable. Apply the complete main skill's stated rules: real behavior, explicit fail-causing change, no test-only production hooks, dependency side effects checked. No verification or approval gate is silently weakened.

Task 1: seven new behavioral regressions observed RED on the baseline, then GREEN after the bounded reader/model-provenance implementation. The first rate-state assertion initially forgot the already-known June rate before July publication; corrected the test to require no state before the first release and the June rate until July. No production clock or admission rule was relaxed. Focused final run: 7 passed; owning eight-file archive/market/retention/historical/FOMC/native/transport profile: 162 passed,14.30s.

Task 2: added regression coverage for real shared-predecessor purging across distinct June/July changes using three canonically validated test documents, and positive/negative provenance admission (honest dated archive accepted; original-vintage or captured-receipt claims rejected). This exercises existing implementation, not a new source or a claim of actual historical performance. Focused run:8 passed,1.21s. Final integration profile and one fresh whole-package review pending.

Task 1–2 local integration:163 passed,12.33s in the same eight-file owning profile; git diff --check clean. This is not full CI or production/model-support evidence. Submit the completed bounded package for one fresh review before remote publication.

Production checkpoint: PR466 is accepted as main468dc8c665d590894776452bdd095e3bd8dc9a52/tree61853abf567fe57eee88046cfde01074338aa444. Deploy38520319593 and seven production contexts passed, as did mathematical-edge38522070758 and sources38522072369. Earlier scheduled EDE38520481244 installed its archive but failed because port8790 was unavailable during deployment at06:14:43UTC; this is not a successful EDE result. Accepted-release inventory38522139650 remains a separate live chain; do not repeat successful CI, weaken readiness, or report the earlier audit as complete.

Review tooling fallback: the linked code-reviewer template could not be read. The complete requesting-code-review/executing-plans instructions remain available; provide the reviewer explicit scope, baseline, spec, ledger, threat classes, severity/line/reproduction requirements and declined-to-judge list. No review or verification gate is skipped.

Final fresh review: three Important, no Critical/Minor. Independently reproduced six failures in seven cases: duplicate URL substituted the no-rate document hash; a skipped immediate predecessor changed -25bp into -50bp; truthy non-object heads and vintage metadata escaped optional model validation. The feature-integer case was already safely rejected and remains supplementary coverage, not a new fix claim.

Final: fixed malformed optional model metadata — test_fomc_malformed_model_metadata_is_rejected_without_breaking_retention:list/string/integer/vintage_list RED→GREEN through actual model/retention consumers. Validate head/feature/guarantee structure before iteration; no generic exception masking.
Final: fixed duplicate FOMC source identity — test_fomc_duplicate_url_cannot_substitute_another_document_hash RED→GREEN; reject repeated URLs before building the lookup.
Final: fixed skipped scheduled predecessor — test_fomc_change_cannot_skip_the_immediate_scheduled_predecessor RED→GREEN; canonical chronological schedule determines the predecessor, while genuinely missing parsed change remains absent.
Final fix-pass integration:170 passed,14.67s in the eight-file owning profile; git diff --check clean. No second reviewer or minor-polish pass. Mandatory final-tree CI remains required, not inferred from local tests.

Final: Ruling: preserve fail-closed archive identity and one-meeting change semantics — duplicate URLs or non-immediate linked rate changes reject this optional macro archive rather than guessing a version or repairing source linkage. Cost if wrong: conservative loss of macro history from an ambiguous packet; unaffected families continue through the existing optional-archive rejection path.
Final: Ruling: empirical/release matters declined by reviewer require their own real evidence — accepted468 mathematical publication38522070758/artifact11874745436 has25 valid model contracts and15 supported models, not17; no new FOMC supported forecast, actual live contribution or profit is established. Original dated pages remain non-versioned and cannot prove first receipt. New mandatory CI/deployment/exact-SHA acceptance remain pending. Cost if wrong: overstated readiness or authority; keep unavailable forecasts/contributions null and do not promote models from tests.

Latest accepted-release research: inventory38522139650 completed successfully and handed off to EDE38524714659, in progress. Hold the next merge until its exact468 publication finishes. The earlier failed scheduled audit38520481244 is not rerun or concealed.
