# PHI_G REPRODUCIBILITY v1

Status: DISCOVERY / REPRODUCIBILITY QA
This document does not upgrade Level 2.

## Purpose

Recover the frozen first-training-day GEX coordinate directly from the causal T0 export without using validation outcomes and without refitting the prospective Gate-A decoder.

Previously frozen rounded coordinate:

Phi_G =
-0.223 z(Balance)
-0.636 z(FieldSlope)
-0.311 z(ForceSlope)
+0.670 z(StiffnessSlope)

Reported explained variance:
~51.1%.

## Raw T0 feature paths recovered from events_min.csv.gz

Balance:
g1s_evidence_v2.option_context.gex_net_balance

FieldSlope:
g1s_evidence_v3.gex.dynamics.field_score.slope

ForceSlope:
g1s_evidence_v3.gex.dynamics.force_score.slope

StiffnessSlope:
g1s_evidence_v3.gex.dynamics.stiffness_score.slope

The same values are also present under the frozen G1S feature JSON for applicable events.

## First training date

UTC date:
2026-08-27

Resolved 15m UP/DOWN events:
66

Rows complete on all four GEX variables:
61

The original source-level experiment evidently used a 60-row complete-case training matrix.

A leave-one-row reconstruction identifies one 60-row subset whose PC1 matches the previously frozen coordinate essentially exactly.

Excluded row in this reconstruction:

event_id:
market-fede5685cf5cbbf7111a63b1-15m

instrument:
XAU

captured time:
2026-08-27T22:04:42.166207Z

This row is missing cross-asset fields that are present in the other 60 GEX-complete rows, strongly suggesting that the original experiment used a broader source-ladder complete-case requirement.

This explains the 60-row matrix plausibly, but the exact historical exclusion code is not available; therefore label this as an extremely strong reconstruction, not a cryptographic proof of the old training selector.


## Reconstructed training-row fingerprint

For future reproducibility, sort the 60 reconstructed event IDs lexicographically, join them with a newline after every ID (including the final ID), and hash with SHA-256.

Result:

f376f9d6091e0ea229f340027bfb36fa13a665f6c7ce50d454cbc8e1cf53717c

This fingerprints the reconstructed 60-event training selector without depending on row order.

## Recovered training normalization

Using the 60-row reconstructed matrix:

Mean(Balance)
=
0.06679664187294204

SD_sample(Balance)
=
0.6723481628411081

Mean(FieldSlope)
=
0.0002687633333333334

SD_sample(FieldSlope)
=
0.0008169935748490516

Mean(ForceSlope)
=
-0.0002097

SD_sample(ForceSlope)
=
0.00066984614297183

Mean(StiffnessSlope)
=
-0.0002436866666666667

SD_sample(StiffnessSlope)
=
0.0007341548583850654

## Recovered PCA

PC1 explained variance ratio:

0.5106460000887746

or:

51.0646000089%.

PC1 loading, sign aligned to the frozen coordinate:

Balance:
-0.22297579

FieldSlope:
-0.63552668

ForceSlope:
-0.31133441

StiffnessSlope:
+0.67041668

Therefore:

Phi_G_reconstructed =
-0.22297579 z(Balance)
-0.63552668 z(FieldSlope)
-0.31133441 z(ForceSlope)
+0.67041668 z(StiffnessSlope).

Cosine similarity with the rounded frozen vector
(-.223,-.636,-.311,+.670):

0.9999997479.

Thus the archived rounded coordinate is reproduced to essentially machine-level directional agreement.

## Consequence for Gate A

The GEX side of the frozen representation is now reproducible from the current causal T0 export:

- raw variable paths are identified;
- first-day training normalization is reconstructed;
- first principal component is reconstructed;
- explained variance reproduces the recorded ~51.1%.

Therefore the prior Gate-A reproducibility blocker is narrowed.

The remaining missing object is the PRICE / DECODER side:

- exact Phi_P construction and normalization;
- ridge C=.1 intercept/coefficient bundle;
- ridge C=1 intercept/coefficient bundle;
- or, equivalently, the frozen q_P and q_{P+GEX} T0 probabilities for both decoders.

The passive forecast JSON in events.parquet belongs to a passive/shadow forecast contract and is not established as the offline Gate-A ridge predictor.

No validation-outcome refit is allowed.

## Current scientific status

Level 2 remains UNRESOLVED because there are still zero post-Gate-A observations.

This reconstruction only removes a technical reproducibility ambiguity; it does not turn discovery evidence into prospective evidence.
