"""Original received option evidence available at a completed forecast origin."""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .config import ALL_INSTRUMENTS
from .data.cache import production_chain_snapshot
from .edge_family_native_history import option_observation
from .edge_family_option_interest import interest_observation
from .mathematical_edge import fingerprint,number
from .passive_learning import PassiveLearningEngine


def _received(code,chain,cutoff):
    if not isinstance(chain,dict):return None
    metrics=chain.get('metrics');stamp=number(chain.get('ts'))
    configured=ALL_INSTRUMENTS.get(code)
    if (configured is None or not configured.options_proxy or stamp is None or not 0<stamp<=cutoff
            or not production_chain_snapshot(metrics)
            or metrics.get('proxy')!=configured.options_proxy
            or not isinstance(chain.get('source'),str) or not chain['source']):return None
    return metrics,stamp,(code,metrics['proxy'],chain['source'])


def _observation(family,code,chain,received):
    metrics,stamp,_=received
    try:
        build=option_observation if family=='option' else interest_observation
        return build(code=code,metrics=metrics,source_id=chain['source'],
            observed_ts=stamp,received_ts=stamp,quality=PassiveLearningEngine._quality(chain),
            source_sha256=fingerprint(metrics))
    except (ValueError,TypeError,OverflowError):return None


def _eligible(row,identity,origin,cutoff):
    if not isinstance(row,dict):return False
    stamp=number(row.get('captured_ts'));proof=row.get('feature_provenance');features=row.get('features')
    if (stamp is None or not 0<stamp<=origin or not 0<=cutoff-stamp<=1800
            or not isinstance(proof,dict) or not isinstance(features,dict)
            or not 1<=len(features)<=16 or set(proof)!=set(features)):return False
    code,proxy,source=identity
    for item in proof.values():
        if not isinstance(item,dict):return False
        observed=number(item.get('observed_ts'));received=number(item.get('received_ts'))
        available=number(item.get('available_at'));age=number(item.get('max_age_sec'))
        quality=number(item.get('quality'));digest=item.get('source_sha256')
        if (item.get('source_instrument')!=code or item.get('measurement_instrument')!=proxy
                or item.get('source_id')!=source or observed is None or received is None
                or not 0<observed<=received<=origin or received!=stamp
                or available is None or not received<=available<=origin
                or age is None or not 0<=cutoff-observed<=min(age,1800)
                or item.get('source_verified') is not True or quality is None or not 0<quality<=1
                or not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None):return False
    if any(number(value) is None for value in features.values()):return False
    return True


def freeze_option_family_inputs(cache,code,chain,cutoff):
    """Background only; keep at most two original observations per family."""
    received=_received(code,chain,cutoff)
    if received is None:
        cache.clear();return {}
    identity=received[2]
    if cache.get('identity')!=identity or cache.get('captured_ts',cutoff)>cutoff:cache.clear()
    observations={family:_observation(family,code,chain,received) for family in ('option','positioning')}
    if not any(observations.values()):
        cache.clear();return {}
    origin=math.floor(cutoff/300)*300
    retained={};selected={}
    for family in ('option','positioning'):
        current=observations[family]
        rows=[row for row in (cache.get('families') or {}).get(family,())
              if isinstance(row,dict) and number(row.get('captured_ts')) is not None
              and 0<=cutoff-row['captured_ts']<=1800]
        if current is not None:rows.append(current)
        prior=[row for row in rows if _eligible(row,identity,origin,cutoff)]
        chosen=max(prior,key=lambda row:row['captured_ts']) if prior else None
        latest=max(rows,key=lambda row:row['captured_ts']) if rows else None
        retained[family]=[row for index,row in enumerate((chosen,latest))
                          if row is not None and (index==0 or row!=chosen)]
        if chosen is not None:selected[family]=chosen
    cache.update(identity=identity,captured_ts=cutoff,families=deepcopy(retained))
    return deepcopy(selected)


def option_family_input(engine,snapshot,code,family,origin,cutoff):
    chain=getattr(engine.market,'chain',None);received=_received(code,chain,cutoff)
    if received is None:return None
    current=_observation(family,code,chain,received)
    if current is None:return None
    if _eligible(current,received[2],origin,cutoff):return current
    # Later information is not moved back to the bar clock. Only the pinned
    # background generation may supply the genuine earlier receipt.
    if received[1]<=origin:return None
    from .decision_metric_inputs import live_decision_context
    context=live_decision_context(engine,snapshot) or {}
    prior=(context.get('option_family_inputs') or {}).get(family)
    return prior if _eligible(prior,received[2],origin,cutoff) else None
