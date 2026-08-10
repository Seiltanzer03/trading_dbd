# Phase F.2 audit

Starting SHA: `f4d16840160b778bde0357202c5240f6371238e4`.

## Scope

Phase F.2 restores the deterministic management decision workflow, adds an immutable real-position event ledger, promotes original STOP/active risk barrier/TAKE to first-class report geometry, and adds a continuously collected passive prospective dataset. Passive, virtual and real-trade evidence remain separate.

## Integrity

- LLM is explanation-only.
- CLOSE_X is relative to current remainder.
- Decision execution is idempotent and stale-protected.
- T0 forecast fields are SQLite-immutable.
- Resolutions use only subsequently recorded market paths.
- Demo/synthetic rows are excluded from evidence.
- Raw N and conservative non-overlapping effective N are both reported.
- Q remains risk-neutral; calibrated P is shadow.
- Every promotion flag remains false.

## Authority matrix

| Component | Authority |
|---|---|
| Production policy | production |
| Passive forecast | research_only |
| Q→P calibrator | shadow |
| Virtual policy cohort | research_only |
| Human execution | manual |
| LLM | explanation_only |
