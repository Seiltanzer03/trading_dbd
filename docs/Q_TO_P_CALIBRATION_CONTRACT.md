# Q to P calibration contract

Option-implied probabilities are stored as risk-neutral Q. Outcomes come only from subsequently recorded real market paths. Reports compare identity Q with a train-frozen historical base-rate baseline and expose Brier/log-loss, quantile coverage, raw N, conservative effective N and evidence status. Physical P remains shadow and is never published or promoted automatically.


Gaussian/historical-volatility references are never labelled Q. Q rows are
eligible only when the option source supplies an explicit, normalized
`standardized_barrier_q` contract. Validation manifests are chronological
60/20/20 with future-label purge and a maximum-horizon embargo; random shuffle
is forbidden and TEST remains unconsumed until a registered evaluation.
