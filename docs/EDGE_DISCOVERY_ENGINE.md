# Edge Discovery Engine v1

EDE is an offline, research-only layer for finding interpretable conditional
market edge. It does not run in the web request path and cannot change the AI
verdict, a production model, or trading authority.

The logical feature registry routes features to existing T0 contracts instead
of reimplementing them. Missing values remain unavailable; staleness and source
quality are separate from the market value. Current 60-day P1B bars are labelled
discovery data, never pristine OOS.

The first bounded audit evaluates ret5 persistence through at most three
conditions drawn from asset/family/session, train-only volatility and trend
quintiles, aligned cross-asset confirmation, and breadth. Each outer fold runs
its own purged inner discovery. Outer tests only evaluate selected rules.
Benjamini-Hochberg correction is applied to actually tested hypotheses.

Candidate and live-shadow ledgers are append-only. A historical candidate must
be explicitly frozen before future predictions can be written; outcomes cannot
be attached before their target timestamp. Rejected candidates remain recorded.

Heavy audits run on GitHub Actions. The default workflow fetches a fresh real
5m/60d set into a temporary research database, which becomes immutable for the
run and never touches production credentials. The CLI can alternatively read a
separately supplied offline P1B copy in SQLite read-only mode. Option, GEX, IV,
skew, Greek and derivative features are inventoried,
but the first P1B audit reports them unavailable unless genuine immutable T0
history has sufficient coverage. Synthetic or current-snapshot backfill is
forbidden.
