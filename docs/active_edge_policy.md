# Active edge policy — manual trader high risk v1

This project intentionally uses a high-risk discovery threshold for the user's
manual trading terminal.

Active acceptance:

- FDR/q-value remains calculated and displayed, but does not block a signal (`q <= 1.0`).
- Structured and ML candidates require at least 1.0% relative OOS improvement.
- Two positive OOS folds are sufficient.
- Structured discovery uses smaller train/validation/test sample gates and keeps up to 24 inner candidates.
- ML accepts 50% feature coverage, up to 48 numeric features and smaller train/test cohorts.
- Daily Treasury candidates may be admitted as high-risk structured context after the existing UTC-day clustered OOS exam; the extra rates-dependency risk is explicitly marked.

Still mandatory:

- causal T0 feature availability;
- no future joins;
- purged/embargoed chronological folds;
- train-only instrument -> family -> global structural baseline;
- UTC-day dependency-aware OOS scoring;
- immutable prospective freeze for confirmation;
- no automatic trade execution;
- no automatic candidate promotion;
- no production authority.

The former research-grade threshold (`q <= 0.10`, >=0.5% improvement, >=3
positive folds) is retained only as `strict_reference_qualified` metadata. It is
not the active pass/fail criterion.
