"""Observed positive option-chain OI of the configured measurement proxy.

Provider-normalized zero values are ambiguous: do not impute missing OI or
claim a complete chain, dealer inventory, CFTC exposure or broker positions.
"""
from __future__ import annotations

import math

from .edge_family_native_history import _iv_observation
from .mathematical_edge import number
from .data.cache import production_chain_snapshot
from .real_input_integrity import simulated_evidence


def interest_observation(*, code, metrics, source_id, observed_ts, received_ts, quality, source_sha256):
    if not production_chain_snapshot(metrics) or simulated_evidence(metrics):
        return None
    # Reuse the exact configured proxy, receipt, quality and identity checks.
    base=_iv_observation(code=code,proxy=metrics.get('proxy'),iv=1.,source_id=source_id,
        observed_ts=observed_ts,received_ts=received_ts,quality=quality,source_sha256=source_sha256)
    profile=metrics.get('oi_profile')
    if base is None or not isinstance(profile,dict):
        return None
    strike,call,put=(profile.get(key) for key in ('strikes','call_oi','put_oi'))
    if (any(not isinstance(values,list) for values in (strike,call,put))
            or not 1<=len(strike)==len(call)==len(put)<=2048):
        return None
    values=[[number(value) for value in series] for series in (strike,call,put)]
    if (any(value is None for series in values for value in series) or min(values[0])<=0
            or min(values[1]+values[2])<0 or len(set(values[0]))!=len(strike)):
        return None
    calls=sum(v for v in values[1] if v>0);puts=sum(v for v in values[2] if v>0)
    if min(calls,puts)<=0 or not math.isfinite(calls+puts):
        return None
    features={'positioning.option_reported_put_share':puts/(calls+puts),
        'positioning.option_log_positive_reported_oi':math.log1p(calls+puts)}
    proof={**base['feature_provenance']['option.iv'],
        'factor_contract':'configured-proxy-positive-reported-option-interest-v1',
        'feature_units':'reported_contract_count_transforms', 'partial_reported_chain':True,
        'observed_broker_positions':False,'dealer_position_inferred':False,
        'zero_open_interest_imputed':False,'raw_option_probability_is_physical':False}
    return dict(captured_ts=received_ts,features=features,
        feature_provenance={name:dict(proof) for name in features})
