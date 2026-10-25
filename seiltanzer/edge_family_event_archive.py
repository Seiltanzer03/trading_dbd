"""Observed release timing response, without surprise or invented consensus."""
from __future__ import annotations

import re

import numpy as np

from .mathematical_edge import fingerprint,number

CONTRACT='official-released-event-timing-market-response-v1'
KINDS=('CPI','NFP','ISM_MANUFACTURING','ISM_SERVICES','FOMC')
NAMES=('event.official_release_age_hours',)+tuple('event.official_release_is_'+kind for kind in KINDS)
MAX_AGE=14400.


def _kind(name):
    for prefix,kind in (('macro.cpi_','CPI'),('macro.nfp_','NFP'),
        ('macro.ism_manufacturing_','ISM_MANUFACTURING'),('macro.ism_services_','ISM_SERVICES'),('macro.fomc.','FOMC')):
        if name.startswith(prefix):
            return kind
    return None


def _features(kind,published,t0):
    return dict(zip(NAMES,((t0-published)/3600.,)+tuple(float(value==kind) for value in KINDS)))


def archive_event_state(archive,t0):
    from .edge_family_macro_archive import OfficialMacroArchive,FEATURES
    if not isinstance(archive,OfficialMacroArchive):
        raise ValueError('OFFICIAL_EVENT_ARCHIVE_NOT_VALIDATED')
    possible=[]
    for name in FEATURES:
        state=archive.state(name,t0);kind=_kind(name)
        if state is None or kind is None:
            continue
        # A conservative ALFRED next-day availability is not the event time.
        published=number(state.get('published_at'))
        if published is None or not 0<=t0-published<=MAX_AGE:
            continue
        possible.append((published,KINDS.index(kind),kind,state))
    if not possible:
        return None
    published,_,kind,state=max(possible,key=lambda row:(row[0],-row[1]))
    return dict(features=_features(kind,published,t0),source=state,
        consensus_available=False,surprise_computed=False)


def current_event_observation(macro, *, code, cutoff):
    from .edge_family_training import _feature_reason
    if not isinstance(macro,dict):
        return None
    possible=[]
    for name in macro.get('features',{}):
        kind=_kind(name);meta=macro.get('feature_provenance',{}).get(name)
        if kind is None or not isinstance(meta,dict):
            continue
        published=number(meta.get('published_at'))
        if published is None or not 0<=cutoff-published<=MAX_AGE:
            continue
        proof={**meta,'max_age_sec':MAX_AGE,'event_timing_contract':CONTRACT,
            'consensus_available':False,'surprise_computed':False}
        if _feature_reason(proof,code,'event',cutoff,allow_global=True) is not None:
            continue
        possible.append((published,KINDS.index(kind),kind,proof))
    if not possible:
        return None
    published,_,kind,proof=max(possible,key=lambda row:(row[0],-row[1]))
    values=_features(kind,published,cutoff)
    return dict(features=values,feature_provenance={name:dict(proof) for name in values})


def archive_event_dataset(code,bars,captured,horizon,archive):
    from .edge_family_market import market_dataset
    base=market_dataset(code,'session',bars,captured,horizon)
    indices=[];values=[];groups=[]
    for i,t0 in enumerate(base['times']):
        state=archive_event_state(archive,float(t0))
        if state is None:
            continue
        indices.append(i);values.append([state['features'][name] for name in NAMES])
        # Every transform of the same release is purged together.
        groups.append((state['source']['dependency_group'],))
    return dict(x=np.asarray(values,dtype=float).reshape((-1,len(NAMES))),y=base['y'][indices],
        times=base['times'][indices],ends=base['ends'][indices],groups=groups,feature_names=list(NAMES))


def fit_archive_events(code,bars,captured,horizons,archive,source,*,target_source=None):
    from .edge_family_market import _fit_family
    event_proof=None;event_error=None
    if isinstance(target_source,dict) and 'event_window_archive' in target_source:
        try:
            from .binance_event_windows import event_target_bars
            bars=event_target_bars(target_source,bars,archive,captured)
            source={**source,'bars_sha256':fingerprint(bars)}
            event_proof=target_source['event_window_archive']
        except (ValueError,TypeError,KeyError) as exc:
            event_error=str(exc)[:180]
    datasets={h:archive_event_dataset(code,bars,captured,h,archive) for h in horizons}
    result=_fit_family(code,'event',bars,captured,horizons,None,None,source,
        datasets_override=datasets,feature_variation_required=True)
    result.update(official_event_archive=archive.summary(),event_timing_contract=CONTRACT,
        features_are_captured_receipts=False,consensus_available=False,surprise_computed=False,
        historical_feature_availability='VERIFIED_OFFICIAL_RELEASE_TIMES_ACTUAL_RETRIEVAL_RETAINED')
    if event_proof is not None:
        result.update(event_window_target_archive=event_proof,event_target_bars_sha256=source['bars_sha256'])
    if event_error is not None:result['event_history_error']=event_error
    if result['status']=='TRAINED':
        result.pop('model_sha256',None);result['model_sha256']=fingerprint(result)
    return result


def archive_provenance_valid(model,trained,cutoff=None):
    from .edge_family_macro_archive import archive_model_provenance_valid
    if (model.get('event_timing_contract')!=CONTRACT or model.get('consensus_available') is not False
            or model.get('surprise_computed') is not False
            or any(head.get('feature_names',list(NAMES))!=list(NAMES) for head in model['heads'].values())):
        return False
    if not archive_model_provenance_valid({**model,'official_macro_archive':model.get('official_event_archive')},trained):
        return False
    if 'event_window_target_archive' in model or 'event_target_bars_sha256' in model:
        from .binance_event_windows import event_archive_valid
        proof=model.get('event_window_target_archive');summary=model['official_event_archive']
        digest=model.get('event_target_bars_sha256')
        if (cutoff is None or not event_archive_valid(proof,model['instrument'],summary['bundle_sha256'],cutoff)
                or proof.get('calendar_cutoff_ts')!=trained
                or not isinstance(digest,str) or re.fullmatch('[0-9a-f]{64}',digest) is None
                or model.get('source',{}).get('bars_sha256')!=digest):
            return False
    return True
