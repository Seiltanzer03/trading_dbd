"""Shared model-benefit policy; never overrides source, risk or execution gates."""
from __future__ import annotations

import math

VERSION = 'management-benefit-v1'
TAIL_WEIGHT = .35  # Existing manager risk preference, not fitted accuracy.
MIN_TAIL_GAIN_R = .05
QUANT_SCALE_R = .12
HOLD_SCORE_MARGIN = .01


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def benefit_assessment(policy, delta_expected_r, delta_cvar_r, band_r,
                       expected_lower_r=None, tail_lower_r=None):
    """Allow profit benefit OR bounded protective benefit on matched scenarios.

    Lower bounds measure Monte Carlo/seed sensitivity, not market confidence.
    Legacy qualified base economics may lack these bounds; that is explicit.
    """
    delta, tail = _number(delta_expected_r), _number(delta_cvar_r)
    band = max(0., _number(band_r) if _number(band_r) is not None else .03)
    lower, tail_lower = _number(expected_lower_r), _number(tail_lower_r)
    expected = lower if lower is not None else delta
    protective_tail = tail_lower if tail_lower is not None else tail
    if policy == 'HOLD':
        allowed, basis = True, 'HOLD_BASELINE'
    elif delta is None or expected is None:
        allowed, basis = False, 'BENEFIT_ECONOMICS_UNAVAILABLE'
    elif expected > band:
        allowed, basis = True, 'MATERIAL_EXPECTED_BENEFIT'
    elif (policy != 'EXTEND_TAKE' and protective_tail is not None
          and protective_tail >= MIN_TAIL_GAIN_R - 1e-8
          and expected >= -band - 1e-8 and delta >= -band - 1e-8):
        allowed, basis = True, 'BOUNDED_TAIL_PROTECTION'
    else:
        allowed, basis = False, 'NO_MATERIAL_NET_OR_BOUNDED_TAIL_BENEFIT'
    return {'contract_version': VERSION, 'allowed': allowed, 'basis': basis,
            'indifference_band_r': band, 'minimum_tail_gain_r': MIN_TAIL_GAIN_R,
            'delta_expected_r': delta, 'delta_cvar_r': tail,
            'expected_test_value_r': expected, 'tail_test_value_r': protective_tail,
            'expected_lower_r': lower, 'tail_lower_r': tail_lower,
            'sensitivity_basis': ('PAIRED_MODEL_BOUND' if lower is not None else
                                  'QUALIFIED_POINT_ECONOMICS'),
            'market_forecast_confidence_proven': False}


def quantitative_utility(delta_expected_r, delta_cvar_r, band_r):
    """Bound tail credit without changing Expected/CVaR or adding fake costs."""
    delta, tail = _number(delta_expected_r), _number(delta_cvar_r)
    band = max(0., _number(band_r) if _number(band_r) is not None else .03)
    credit = min(band, TAIL_WEIGHT * tail) if tail is not None else None
    return {'expected_delta_r': delta, 'tail_delta_r': tail,
            'tail_weight': TAIL_WEIGHT, 'positive_tail_credit_cap_r': band,
            'tail_credit_r': credit,
            'utility_delta_r': delta + credit if delta is not None and credit is not None else delta,
            'tail_utility_available': tail is not None,
            'scale_r': QUANT_SCALE_R, 'semantics': 'POLICY_UTILITY_NOT_EXPECTED_RETURN'}
