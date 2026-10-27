"""Market-response inputs derived from actually received official rate curves.

The parent remains research context. A trained/validated child forecast has its
own admission; this module grants neither quote nor broker-carry authority.
"""
from __future__ import annotations

from datetime import datetime,timedelta,timezone
import re
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
    source_url = block.get('source_url')
    if not isinstance(source_url, str):
        return None
    try:
        url = urlparse(source_url)
        if url.scheme != 'https' or url.hostname != 'home.treasury.gov' or url.username or url.password or url.port not in (None,443):
            return None
    except (ValueError,TypeError):
        return None
    boundary, fetched, available, captured, expires = (number(block.get(key)) for key in
        ('published_at','fetched_at','asof','captured_ts','stale_after'))
    if (any(value is None for value in (boundary,fetched,available,captured,expires))
            or not 0 < boundary <= available == max(boundary,fetched) <= captured <= cutoff < expires
            or not 0 < fetched <= available or cutoff-boundary > MAX_AGE):
        return None
    conservative=block.get('publication_timestamp_kind')=='CONSERVATIVE_NEXT_UTC_DAY'
    if conservative:
        try:
            source_day=datetime.strptime(block.get('observation_date',''),'%m/%d/%Y').replace(tzinfo=timezone.utc)
            if boundary!=(source_day+timedelta(days=1)).timestamp() or cutoff-fetched>MAX_AGE:return None
        except (ValueError,TypeError):return None
        observed=fetched  # Actual observation; the later bound is not a publication timestamp.
    else:
        if fetched<boundary:return None
        observed=boundary
    rows = block.get('features')
    if not isinstance(rows,dict):
        return None
    features = {}
    for source, name in FIELDS.items():
        row = rows.get(source)
        if (not isinstance(row,dict) or row.get('available') is not True
                or row.get('stale') is not False or row.get('future_points_used') is not False
                or any(number(row.get(key)) != value for key,value in
                    (('published_at',boundary),('fetched_at',fetched),('asof',available),('stale_after',expires)))):
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
        observed_ts=observed, received_ts=fetched, available_at=available,
        max_age_sec=MAX_AGE, quality=1., dependency_group='official_us_treasury_daily_curve',
        clock_basis='ORIGINAL_OFFICIAL_PUBLICATION_AND_ACTUAL_TREASURY_FETCH',
        factor_contract='recorded-treasury-market-response-factors-v1',
        parent_research_only=block.get('research_only') is True, parent_production_authority=False,
        historical_backfill=False, broker_carry_available=False, target_quote_authority=False)
    if conservative:
        proof.update(factor_contract='recorded-treasury-market-response-factors-v2',
            max_age_sec=min(MAX_AGE,boundary+MAX_AGE-observed,expires-observed),
            publication_timestamp_kind='CONSERVATIVE_NEXT_UTC_DAY',
            conservative_publication_bound_ts=boundary,source_observation_date=block['observation_date'],
            purge_dependency_id='official-us-treasury-daily-curve:'+str(int(boundary)),
            clock_basis='ACTUAL_HTTP_RECEIPT_WITH_CONSERVATIVE_NEXT_UTC_DAY_AVAILABILITY')
    else:
        proof['published_at']=boundary
    return dict(captured_ts=captured, features=features,
        feature_provenance={name:{**proof,'feature_units':('percentage_points' if name.endswith('_pp') else 'percent_per_annum')}
            for name in features})


def treasury_purge_id(meta,cutoff):
    """Stable curve identity supplements receipt groups, including legacy rows."""
    try:
        digest=meta.get('source_sha256')
        if (not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None
                or meta.get('source_id')!='official-treasury-curve:'+digest
                or meta.get('dependency_group')!='official_us_treasury_daily_curve'):return None
        observed,received,available,age=(number(meta.get(key)) for key in
            ('observed_ts','received_ts','available_at','max_age_sec'))
        if any(value is None for value in (observed,received,available,age)):return None
        modern=(meta.get('factor_contract')=='recorded-treasury-market-response-factors-v2'
            or 'conservative_publication_bound_ts' in meta or meta.get('published_at') is None)
        if modern:
            boundary=number(meta.get('conservative_publication_bound_ts'))
            if (meta.get('factor_contract')!='recorded-treasury-market-response-factors-v2'
                    or meta.get('publication_timestamp_kind')!='CONSERVATIVE_NEXT_UTC_DAY'
                    or meta.get('published_at') is not None or boundary is None
                    or observed!=received):return None
            source_day=datetime.strptime(meta.get('source_observation_date',''),'%m/%d/%Y').replace(tzinfo=timezone.utc)
            if (boundary!=(source_day+timedelta(days=1)).timestamp()
                    or not 0<age<=min(MAX_AGE,boundary+MAX_AGE-observed)):return None
        else:
            boundary=number(meta.get('published_at'))
            if (meta.get('factor_contract') not in (None,'recorded-treasury-market-response-factors-v1')
                    or boundary is None or observed!=boundary):return None
        if not 0<boundary<=available==max(boundary,received)<=cutoff or cutoff-boundary>MAX_AGE:return None
        identity='official-us-treasury-daily-curve:'+format(boundary,'.17g')
        if meta.get('purge_dependency_id',identity)!=identity:return None
        return identity
    except (ValueError,TypeError,KeyError,OverflowError):return None
