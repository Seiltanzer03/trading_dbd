"""Three predeclared probability interpretations; no fitting or source changes."""
from __future__ import annotations

import numpy as np

VARIANTS = ('DIRECT', 'INVERSE', 'BASELINE_SHRINK')


def variant_predictions(probabilities, baseline, variant='DIRECT'):
    p = np.asarray(probabilities, dtype=float)
    if (variant not in VARIANTS or isinstance(baseline, bool)
            or not np.isfinite(baseline) or not 0 < baseline < 1
            or not np.all(np.isfinite(p)) or np.any((p < 0) | (p > 1))):
        raise ValueError('invalid probability variant or input')
    if variant == 'INVERSE':
        return 1 - p
    if variant == 'BASELINE_SHRINK':
        return .5 * p + .5 * baseline
    return p
