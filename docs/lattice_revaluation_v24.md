# Probability Lattice revaluation v24

## What the board now measures

The new block is not based on the decorative Galton balls. The server records
snapshots of the terminal R distribution for the active trade and exposes:

- the first valid distribution after entry;
- the arithmetic average over the active trade;
- the current distribution;
- movement of probability mass between `R<=-1`, `-1<R<0`, `0<=R<T` and `R>=T`;
- changes in first-touch probability, barrier EV, median and P10-P90 width;
- short-term slopes, noise and directional consistency.

Repeated API/AI calls inside one second do not increase the average. State is
periodically persisted in the existing SQLite cache and is reset when the trade
geometry changes.

## AI authority

Derived values are useful but are not independent market observations. They all
come from the same option-distribution model. Therefore the policy manager adds
one aggregate row named `distribution_revaluation_weighted` to the existing
`option_distribution` family.

The aggregate score is weighted by:

1. source quality: live mapping > indicative mapping > snapshot mapping;
2. number of time samples collected since entry;
3. recent noise in P(take).

Indicative mapping is discounted, not disabled. A material weighted change can
still alter the direction of the option-distribution family, while immature,
very noisy or scenario-only history remains context-only. Existing source and
EXIT safety gates remain authoritative.
