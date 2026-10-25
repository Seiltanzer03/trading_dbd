"""Market-response inputs derived from actually received official rate curves.

The parent remains research context. A trained/validated child forecast has its
own admission; this module grants neither quote nor broker-carry authority.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .config import ALL_INSTRUMENTS
from .mathematical_edge import fingerprint, number
from .real_input_integrity import simulated_evidence

FIELDS = {'rates.us02y_yield':'carry.us02y_yield_pct',
          'rates.us10y_yield':'carry.us10y_yield_pct', 'rates.curve_2s10s':'carry.curve_2s10s_pp'}
MAX_AGE = 86400.


def treasury_observation(block, *, code, cutoff):
    """Use published/fetched clocks from the same immutable curve, no backfill."""
    if (code not in ALL_INSTRUMENTS or number(cutoff) is None or not isinstance(block,dict)
            or simulated_evidence(block) or block.get('simulated') is True
            or block.get('contract_version') != 'treasury-live-t0-context-v1'
            or any(block.get(key) is not True for key in ('available','official_source_verified','complete_base_curve'))
            or any(block.get(key) is not False for key in ('stale','future_points_used'))):
        return None
    try:
        url = urlparse(block.get('source_url',''))
        if url.scheme != 'https' or url.hostname != 'home.treasury.gov' or url.username or url.password or url.port not in (None,443):
            return None
    except (ValueError,TypeError):
        return None
    observed, fetched, available, captured, expires = (number(block.get(key)) for key in
        ('published_at','fetched_at','asof','captured_ts','stale_after'))
    if (any(value is None for value in (observed,fetched,available,captured,expires))
            or not 0 < observed <= available == max(observed,fetched) <= captured <= cutoff < expires
            or fetched < observed or cutoff-observed > MAX_AGE):
        return None
    rows = block.get('features')
    if not isinstance(rows,dict):
        return None
    features = {}
    for source, name in FIELDS.items():
        row = rows.get(source)
        if (not isinstance(row,dict) or row.get('available') is not True
                or row.get('stale') is not False or row.get('future_points_used') is not False
                or any(number(row.get(key)) != value for key,value in
                    (('published_at',observed),('fetched_at',fetched),('asof',available),('stale_after',expires)))):
            return None
        value = number(row.get('value'))
        if value is None or not -100 <= value <= 100:
            return None
        features[name] = value
    if abs(features['carry.us10y_yield_pct']-features['carry.us02y_yield_pct']-features['carry.curve_2s10s_pp']) > 1e-8:
        return None
    digest = fingerprint(block)
    proof = dict(source_verified=True, source_id='official-treasury-curve:'+digest,
        source_sha256=digest, source_instrument=code, measurement_instrument='US_TREASURY_PAR_YIELD_CURVE',
        observed_ts=observed, published_at=observed, received_ts=fetched, available_at=available,
        max_age_sec=MAX_AGE, quality=1., dependency_group='official_us_treasury_daily_curve',
        clock_basis='ORIGINAL_OFFICIAL_PUBLICATION_AND_ACTUAL_TREASURY_FETCH',
        factor_contract='recorded-treasury-market-response-factors-v1',
        parent_research_only=block.get('research_only') is True, parent_production_authority=False,
        historical_backfill=False, broker_carry_available=False, target_quote_authority=False)
    return dict(captured_ts=captured, features=features,
        feature_provenance={name:{**proof,'feature_units':('percentage_points' if name.endswith('_pp') else 'percent_per_annum')}
            for name in features})
