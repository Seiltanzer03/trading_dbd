"""Actual native capture history and the shared configured option-IV input.

Q-implied volatility is an observed feature, not a physical forecast or a
target quote. Only the existing historical target trainer gives it a forecast.
"""
from __future__ import annotations

import re

from .config import ALL_INSTRUMENTS
from .data.cache import production_chain_snapshot
from .edge_family_adapters import FAMILIES, MAX_AGE_SEC, build_edge_family_evidence
from .edge_family_training import FEATURE_PREFIXES, _feature_reason
from .mathematical_edge import fingerprint, number
from .real_input_integrity import FLAGS, simulated_evidence
from .native_family_capture_projection import ROOTS, verified_capture

MAX_ROWS = 20000


def option_observation(*, code, metrics, source_id, observed_ts, received_ts,
                       quality, source_sha256):
    """Keep the original snapshot receipt; never assert an exchange quote time."""
    if not production_chain_snapshot(metrics):
        return None
    if simulated_evidence({**{key:metrics.get(key) for key in FLAGS},
            'provenance':metrics.get('provenance'), 'density_input':metrics.get('density_input')}):
        return None
    implied = metrics.get('implied_move')
    iv = number(implied.get('sigma_annual')) if isinstance(implied, dict) else None
    return _iv_observation(code=code, proxy=metrics.get('proxy'), iv=iv, source_id=source_id,
        observed_ts=observed_ts, received_ts=received_ts, quality=quality, source_sha256=source_sha256)


def _iv_observation(*, code, proxy, iv, source_id, observed_ts, received_ts, quality, source_sha256):
    configured = ALL_INSTRUMENTS.get(code)
    if (configured is None or not configured.options_proxy or proxy != configured.options_proxy
            or not isinstance(source_id,str) or not source_id
            or not isinstance(source_sha256,str) or re.fullmatch('[0-9a-f]{64}',source_sha256) is None):
        return None
    observed, received, score = map(number,(observed_ts,received_ts,quality))
    iv = number(iv)
    if (iv is None or iv <= 0 or observed is None or received is None
            or not 0 < observed <= received or received-observed > MAX_AGE_SEC['option']
            or score is None or not 0 < score <= 1):
        return None
    proof = {'source_verified':True, 'source_id':source_id,
        'source_sha256':source_sha256, 'source_instrument':code,
        'measurement_instrument':configured.options_proxy,
        'observed_ts':observed, 'received_ts':received, 'available_at':received,
        'max_age_sec':MAX_AGE_SEC['option'], 'quality':score,
        'dependency_group':code+':option_distribution',
        'clock_basis':'ACTUAL_OPTION_SNAPSHOT_AND_ORIGINAL_CAPTURE_RECORD',
        'feature_units':'annual_volatility_fraction',
        'target_quote_authority':False, 'raw_option_probability_is_physical':False}
    return {'captured_ts':received, 'features':{'option.iv':iv},
            'feature_provenance':{'option.iv':proof}}


def native_history(rows, *, captured_ts):
    """Use immutable source bytes and original capture, not later G1 import time."""
    result = {}
    curves={};conflicting_curves=set()
    cutoff = number(captured_ts)
    if cutoff is None or not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return result
    for row in rows:
        if not isinstance(row, dict) or row.get('source_verified') is not True:
            continue
        code = row.get('instrument')
        captured, recorded = number(row.get('captured_ts')), number(row.get('original_created_ts'))
        if (code not in ALL_INSTRUMENTS or captured is None or recorded is None
                or not 0 < captured <= recorded <= cutoff):
            continue
        frozen = verified_capture(row)
        if not isinstance(frozen, dict) or simulated_evidence(frozen, recursive=False):
            continue
        from .edge_family_treasury_factors import treasury_observation
        curve_block=frozen.get('treasury_context_v1')
        curve=treasury_observation(curve_block,code=code,cutoff=recorded)
        if curve:
            proof=curve['feature_provenance']['carry.us02y_yield_pct']
            key=(proof['observed_ts'],proof['received_ts'])
            values=fingerprint(curve['features'])
            candidate=(recorded,code,row['features_sha256'],values,curve_block)
            previous=curves.get(key)
            if previous and previous[3]!=values:
                conflicting_curves.add(key)
            if previous is None or candidate[:3]<previous[:3]:
                curves[key]=candidate
        # The raw chain and the cached V3 IV block may differ. The original
        # passive capture age belongs to this exact raw OI profile.
        from .edge_family_option_interest import interest_observation
        age=number(row.get('option_age_sec'))
        if age is not None and age>=0:
            interest=interest_observation(code=code,metrics=frozen.get('option_distribution'),
                source_id=row.get('option_source'),observed_ts=captured-age,received_ts=recorded,
                quality=row.get('option_quality'),source_sha256=row.get('features_sha256'))
            if interest:
                for proof in interest['feature_provenance'].values():
                    proof['clock_basis']='ORIGINAL_RAW_CHAIN_CAPTURE_AGE_AND_PASSIVE_RECEIPT'
                result.setdefault(code,{}).setdefault('positioning',[]).append(interest)
        # Generic existing family sources retain their own PIT provenance. Do
        # not relabel the price-derived G1 macro/regime block as official macro.
        # Rates are global measurements, accumulated once with original capture
        # lineage below. Do not duplicate them as target-specific observations.
        snapshot = {key:frozen[key] for key in ROOTS if key in frozen and key!='treasury_context_v1'}
        if snapshot:
            snapshot.update(instrument=code, captured_ts=recorded)
            for family, facts in build_edge_family_evidence(snapshot)['families'].items():
                if family in {'session','intermarket','option'}:
                    continue
                names = sorted(n for n in facts['features'] if n.startswith(FEATURE_PREFIXES[family])
                    and _feature_reason(facts['feature_provenance'].get(n),code,family,recorded,
                        allow_global=family in {'macro','event'}) is None)[:16]
                if names:
                    result.setdefault(code,{}).setdefault(family,[]).append({
                        'captured_ts':recorded, 'features':{n:facts['features'][n] for n in names},
                        'feature_provenance':{n:facts['feature_provenance'][n] for n in names}})
        v3 = frozen.get('g1s_evidence_v3') or {}
        v2 = frozen.get('g1s_evidence_v2') or {}
        block = v3.get('option_static') if isinstance(v3,dict) else None
        field = 'iv'
        if not isinstance(block,dict):
            block = v2.get('option_context') if isinstance(v2,dict) else None
            field = 'implied_vol_annual'
        if not isinstance(block,dict) or block.get('available') is not True or simulated_evidence(block):
            continue
        quality = block.get('quality') if isinstance(block,dict) else None
        observed = quality.get('source_ts') if isinstance(quality,dict) else block.get('source_ts')
        metrics = frozen.get('option_distribution')
        # The current refreshed chain and the recorded pre-T0 cached chain can
        # differ. Take BOTH value and clock from the same immutable V3/V2 block.
        if not production_chain_snapshot(metrics) or simulated_evidence({
                **{key:metrics.get(key) for key in FLAGS}, 'provenance':metrics.get('provenance')}):
            continue
        option = _iv_observation(code=code, proxy=block.get('proxy'), iv=block.get(field),
            source_id='immutable-option-capture:'+row['features_sha256'], observed_ts=observed,
            received_ts=recorded, quality=row.get('option_quality'),
            source_sha256=row.get('features_sha256'))
        if option:
            option['feature_provenance']['option.iv']['value_origin'] = 'IMMUTABLE_G1S_OPTION_BLOCK'
            result.setdefault(code,{}).setdefault('option',[]).append(option)
    for key,candidate in sorted(curves.items()):
        if key in conflicting_curves:
            continue
        recorded,origin,digest,_,block=candidate
        for target in ALL_INSTRUMENTS:
            point=treasury_observation(block,code=target,cutoff=recorded)
            if point is None:
                continue
            point['captured_ts']=recorded
            for proof in point['feature_provenance'].values():
                proof.update(global_factor=True,origin_capture_instrument=origin,
                    origin_capture_sha256=digest,origin_recorded_ts=recorded,
                    target_attribution=target)
            result.setdefault(target,{}).setdefault('value_carry',[]).append(point)
    return result


def merge_histories(*matrices):
    """Identical duplicates collapse; conflicting observations remain rejectable."""
    result, seen = {}, set()
    for matrix in matrices:
        if not isinstance(matrix,dict):
            continue
        for code, families in matrix.items():
            if code not in ALL_INSTRUMENTS or not isinstance(families,dict):
                continue
            for family, rows in families.items():
                if family not in FAMILIES or not isinstance(rows,list):
                    continue
                target = result.setdefault(code,{}).setdefault(family,[])
                for row in rows:
                    try:
                        identity = (code,family,fingerprint(row))
                    except (ValueError, TypeError):
                        continue
                    if identity not in seen and len(target) < MAX_ROWS:
                        seen.add(identity); target.append(row)
    return result
