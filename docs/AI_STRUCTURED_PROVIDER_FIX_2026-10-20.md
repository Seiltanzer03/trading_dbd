# Structured AI provider contract — 2026-10-20

## Actual reproduced defect

Production bb672aad636cf4e9aba9d3e1cd453aa35402c650 returned repeated six-second
provider fallbacks. Request ai-5210d2c0b82c474982d7 subsequently received its HTTP
response in6684.787ms, then raised RuntimeError in provider_validate.

An isolated replay of all12 valid preferences against the actual saved19:06UTC
snapshot passed the real report-integrity checks. A separate36-case hosted
fixture also passed without mocking the validator. Therefore disagreement with
the quant winner was not treated as a demonstrated validator defect.

Exactly one existing-provider diagnostic39482427471/job118834125573, on the same
saved input with no decision publication, reproduced **shadow_invalid_policy**.
The model returned in6486.38ms, prompt11724/output440tokens. No raw model response,
prompt, key or proxy credentials were logged. The original response remains
unrecoverable; the specific invalid string is not assumed.

## Correction

The request now supplies a strict JSON schema: one canonical action from the
existing12 and a required complete score vector. Providers must support the
requested parameters; latency sorting prefers a faster endpoint of the same
configured model. The prompt uses an unambiguous schema contract and shorter
rationale. Model/key/API endpoint, six-second outer guard, eight-second transport
setting, fallback, costs, hard risk and scientific admission are unchanged.

The strict wire format carries supported family opinions as rows with family_id,
exact feature_names and1–3 policy_preferences. A bounded adapter restores the
existing dictionary contract. Empty arrays mean no opinions; malformed structural
rows and duplicates are rejected. Legacy dictionary responses retain their
existing per-family isolation. This supplies no fabricated source or forecast.

JSON schema checks do not replace the existing local numeric/source/risk guards.
Portable schema types/enums avoid relying on provider-specific numeric constraints.
jsonschema is a development/test dependency only.

Provider documentation:
- https://openrouter.ai/docs/guides/features/structured-outputs
- https://openrouter.ai/docs/guides/routing/provider-selection

## Evidence and limits

- RED39483152177/job118836646368:19failed/17passed on the uncorrected product.
- GREEN39483635637/job118838324556:36passed in1.86s, including request-path
  schema validation, all12 missing-score cases, invalid action labels, family
  conversion/empty/duplicate cases, and unchanged local numeric rejection.
- Previous baseline report checks:36hosted cases in0.58s and12actual saved-snapshot
  preferences passed, no provider/production writes in those replays.

Final independent review, mandatory final-tree CI and exact-SHA production
acceptance remain required. Latency preference is not a service-level guarantee.
No claim of a complete live LLM fix is made until a structured reply is accepted
inside the actual caller budget. No storage cutover is included in this package.
