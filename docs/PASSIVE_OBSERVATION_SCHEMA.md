# Passive observation schema

`passive_market_observations` freezes T0 price, feature JSON, forecast JSON, provenance, contract/model versions and target horizon. A SQLite trigger rejects changes to prospective fields after insertion. Resolution fields are appended later from `passive_market_path`. Missing features are explicit availability states and never numeric zero. Retrospective replay is separately labelled and excluded from prospective OOS evidence.


`virtual_position_observations` is a separate immutable table linked by
`passive_observation_id`. Its origin is always
`synthetic_position_state_on_real_market_path`; it cannot be inserted into the
trade ledger or counted as a real trade.
