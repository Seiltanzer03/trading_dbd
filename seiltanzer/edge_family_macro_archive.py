"""Offline public-vintage knowledge, separate from recorded live receipts."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import json
import re
from types import MappingProxyType

import numpy as np

from .edge_family_adapters import MAX_AGE_SEC
from .mathematical_edge import fingerprint, number
from .macro_historical_offhost_bundle import validate_bundle
from .macro_numeric_data import _candidate_vector
from .macro_bls_alfred_vintage import SOURCE_KIND as ALFRED_SOURCE_KIND
from .macro_fomc_deterministic_bootstrap import parse_release_timestamp,parse_fomc_index

CONTRACT = 'official-macro-family-archive-v1'
# Fixed minimal states, with the same units/names as current official facts.
FEATURES = ('macro.cpi_headline_mom_pct', 'macro.cpi_core_mom_pct',
    'macro.cpi_core_yoy_pct', 'macro.nfp_payroll_change_k',
    'macro.nfp_unemployment_rate_pct', 'macro.nfp_wage_mom_pct',
    'macro.ism_manufacturing_pmi', 'macro.ism_services_pmi',
    'macro.fomc.target_mid_pct', 'macro.fomc.target_change_bp')
FOMC_FEATURES = frozenset(FEATURES[-2:])
FOMC_VINTAGE = 'OFFICIAL_DATED_PAGE_NOT_VERSIONED'
FOMC_AVAILABILITY = 'DATED_FOMC_PUBLICATION_NOT_VERSIONED_ACTUAL_RETRIEVAL_RETAINED'
_VALIDATED = object()


@dataclass(frozen=True)
class _Point:
    available_at: float
    fetched_at: float
    value: float
    source_sha256: str
    source_id: str
    published_at: float | None
    availability_rule: str
    source_kind: str | None = None
    source_vintage_guarantee: str | None = None
    supporting_provenance: tuple = ()


@dataclass(frozen=True, init=False)
class OfficialMacroArchive:
    """Constructed only after the complete official bundle passes validation."""
    _points: object
    _times: object
    captured_ts: float
    bundle_sha256: str
    source_window_end: float
    source_code_sha: str
    source_created_at: float
    def __init__(self, *, token, points, captured_ts, bundle_sha256, source_window_end,
                 source_code_sha, source_created_at):
        if token is not _VALIDATED:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_NOT_VALIDATED')
        object.__setattr__(self,'_points',MappingProxyType({name:tuple(rows) for name,rows in points.items()}))
        object.__setattr__(self,'_times',MappingProxyType({name:tuple(row.available_at for row in rows)
                                       for name,rows in self._points.items()}))
        object.__setattr__(self,'captured_ts',captured_ts)
        object.__setattr__(self,'bundle_sha256',bundle_sha256)
        object.__setattr__(self,'source_window_end',source_window_end)
        object.__setattr__(self,'source_code_sha',source_code_sha)
        object.__setattr__(self,'source_created_at',source_created_at)

    def state(self, name, t0):
        stamp = number(t0)
        if stamp is None or not 0 < stamp <= min(self.captured_ts,self.source_window_end) or name not in self._points:
            return None
        index = bisect_right(self._times[name],stamp)-1
        if index < 0:
            return None
        point = self._points[name][index]
        if stamp-point.available_at > MAX_AGE_SEC['macro']:
            return None
        result = {'value':point.value, 'available_at':point.available_at,
            'published_at':point.published_at, 'fetched_at':point.fetched_at,
            'availability_rule':point.availability_rule,
            'source_sha256':point.source_sha256, 'source_id':point.source_id,
            'dependency_group':'release:'+point.source_id,
            'evidence_kind':'OFFICIAL_PUBLICATION_ARCHIVE',
            'prospective_observation':False}
        if point.source_kind:
            result.update(source_kind=point.source_kind,
                source_vintage_guarantee=point.source_vintage_guarantee,
                publication_vintage_guaranteed=False,
                supporting_provenance=[dict(source_id=sid,source_sha256=digest,
                    published_at=published,fetched_at=fetched)
                    for sid,digest,published,fetched in point.supporting_provenance],
                dependency_groups=[result['dependency_group'],
                    *('release:'+row[0] for row in point.supporting_provenance)])
        return result

    def published_release_times(self):
        """Known dated publications only; an ALFRED availability is not an event."""
        return sorted({point.published_at for rows in self._points.values() for point in rows
            if point.published_at is not None
            and self.captured_ts-365*86400<=point.published_at<=self.captured_ts})

    def summary(self):
        return {'contract_version':CONTRACT, 'bundle_sha256':self.bundle_sha256,
            'validated_at':self.captured_ts,
            'source_code_sha':self.source_code_sha,'source_window_end':self.source_window_end,
            'source_created_at':self.source_created_at,'offline_copy_revalidation':True,
            'feature_release_counts':{name:len(rows) for name,rows in self._points.items()},
            'feature_vintage_guarantees':{name:sorted({row.source_vintage_guarantee
                for row in rows if row.source_vintage_guarantee})
                for name,rows in self._points.items() if any(row.source_vintage_guarantee for row in rows)},
            'retrieval_clock_backdated':False, 'prospective_observation':False,
            'production_authority':False}


def verified_macro_archive(bundle, *, expected_sha, captured_ts, acceptance_run_id=None):
    stamp=number(captured_ts)
    if stamp is None or stamp <= 0:
        raise ValueError('OFFICIAL_MACRO_ARCHIVE_CAPTURE_INVALID')
    if not isinstance(expected_sha,str) or re.fullmatch('[0-9a-f]{40}',expected_sha) is None:
        raise ValueError('OFFICIAL_MACRO_ARCHIVE_SOURCE_SHA_INVALID')
    if len(json.dumps(bundle,allow_nan=False).encode()) > 96_000_000:
        raise ValueError('OFFICIAL_MACRO_ARCHIVE_INPUT_OVER_BOUND')
    try:
        verified=validate_bundle(bundle,expected_sha=expected_sha,
            acceptance_run_id=acceptance_run_id,now=stamp,require_fresh_transport=False)
    except (AttributeError,KeyError,IndexError,TypeError) as exc:
        # Untrusted structural errors reject the optional archive, not the run.
        raise ValueError('OFFICIAL_MACRO_ARCHIVE_STRUCTURE_INVALID') from exc
    if float(verified['created_at'])>stamp:
        raise ValueError('OFFICIAL_MACRO_ARCHIVE_CLOCK_INVALID')
    points={name:[] for name in FEATURES}
    for record in (*verified['bls_records'],*verified['ism_records']):
        spec=record.get('spec') or {}
        family=spec.get('family') or record['family']
        payload=record['payload']
        published=float(spec.get('published_at') or payload['published_at'])
        fetched=float(record['fetched_at'])
        if not 0 < published <= fetched <= stamp:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_CLOCK_INVALID')
        values=_candidate_vector({'releases':{family:{'status':'VALID','payload':payload}}})
        if family.startswith('ISM_'):
            values['macro.'+family.lower()+'_pmi']=payload.get('pmi')
        identity='official-macro-archive:'+fingerprint({'family':family,
            'available_at':published,'source_sha256':record['source_sha256']})
        alfred=record.get('source_kind')==ALFRED_SOURCE_KIND
        for name in FEATURES:
            value=number(values.get(name))
            if value is not None:
                points[name].append(_Point(published,fetched,value,record['source_sha256'],identity,
                    None if alfred else published,
                    'CONSERVATIVE_ALFRED_NEXT_DAY' if alfred else 'OFFICIAL_PUBLICATION_TIME'))
    # These are already canonically validated dated statements, not recorded
    # contemporaneous receipts or guaranteed original document vintages.
    scheduled={spec.source_url:spec for manifest in verified['fomc_schedules'].values()
        for spec in parse_fomc_index(manifest['content'])}
    ordered=sorted(scheduled.values(),key=lambda spec:spec.date_code)
    predecessors={spec.source_url:ordered[i-1].source_url if i else None
        for i,spec in enumerate(ordered)}
    publications = {}
    for record in verified['fomc_records']:
        url=record['spec']['source_url']
        if url in publications:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_AMBIGUOUS_RELEASE')
        published=parse_release_timestamp(record['html'],date_code=record['spec']['date_code'])
        fetched=float(record['fetched_at'])
        if not 0 < published <= fetched <= stamp:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_CLOCK_INVALID')
        identity='official-macro-archive:'+fingerprint({'family':'FOMC_STATEMENT',
            'available_at':published,'source_sha256':record['source_sha256']})
        publications[url] = (identity,record['source_sha256'],published,fetched)
    for record in verified['fomc_records']:
        identity,digest,published,fetched=publications[record['spec']['source_url']]
        payload=record['payload']
        for key in ('target_mid_pct','target_change_bp'):
            value=number(payload.get(key))
            if value is None:
                continue  # Absent rate/previous release is not a zero change.
            support=()
            if key=='target_change_bp':
                previous_url=record.get('previous_source_url')
                prior=publications.get(previous_url)
                if (previous_url!=predecessors[record['spec']['source_url']]
                        or prior is None or prior[2]>=published):
                    raise ValueError('OFFICIAL_MACRO_ARCHIVE_FOMC_PREDECESSOR_INVALID')
                support=(prior,)
            points['macro.fomc.'+key].append(_Point(published,fetched,value,digest,identity,
                published,'DATED_FOMC_STATEMENT_PUBLICATION_NOT_VERSIONED',
                'OFFICIAL_FED_DATED_STATEMENT_PAGE',payload['source_vintage_guarantee'],support))
    for name,rows in points.items():
        unique={}
        for row in rows:
            existing=unique.get(row.available_at)
            if existing is not None and existing!=row:
                raise ValueError('OFFICIAL_MACRO_ARCHIVE_AMBIGUOUS_RELEASE')
            unique[row.available_at]=row
        points[name]=sorted(unique.values(),key=lambda row:row.available_at)
    return OfficialMacroArchive(token=_VALIDATED,points=points,captured_ts=stamp,
        bundle_sha256=verified['bundle_sha256'],source_window_end=float(verified['window']['end_ts']),
        source_code_sha=expected_sha,source_created_at=float(verified['created_at']))


def archive_market_dataset(code, name, bars, captured, horizon, archive):
    """Join public-vintage state to genuine nonoverlapping future bar labels."""
    from .edge_family_market import market_dataset
    if not isinstance(archive,OfficialMacroArchive) or archive.captured_ts != captured:
        raise ValueError('OFFICIAL_MACRO_ARCHIVE_NOT_VALIDATED_FOR_CUTOFF')
    base=market_dataset(code,'session',bars,captured,horizon)
    indices=[];values=[];groups=[]
    for i,t0 in enumerate(base['times']):
        state=archive.state(name,float(t0))
        if state is None:
            continue
        indices.append(i);values.append([state['value']])
        groups.append(tuple(state.get('dependency_groups',(state['dependency_group'],))))
    return {'x':np.asarray(values,dtype=float).reshape((-1,1)),
        'y':base['y'][indices], 'times':base['times'][indices],
        'ends':base['ends'][indices], 'groups':groups, 'feature_names':[name]}


def fit_archive_macro(code,bars,captured,horizons,archive,source):
    """Select a fixed minimal state on past folds; never on the final test."""
    from .edge_family_market import market_dataset,_fit_family
    grid=market_dataset(code,'session',bars,captured,min(horizons))
    times=grid['times']
    if len(times)<180:
        return {'status':'INSUFFICIENT_HISTORY','reason':'INSUFFICIENT_NONOVERLAPPING_FAMILY_OBSERVATIONS',
                'forecast':None,'training_n':len(times)}
    boundaries=[float(times[int(len(times)*f)]) for f in (.4,.55,.7,.8)]
    candidates=[];audit={};candidate_data={}
    for name in FEATURES:
        datasets={h:archive_market_dataset(code,name,bars,captured,h,archive) for h in horizons}
        result=_fit_family(code,'macro',bars,captured,horizons,None,None,source,
            datasets_override=datasets,boundaries_override=boundaries,feature_variation_required=True,
            pretest_selection_only=True)
        audit[name]={'status':result['status'],'reason':result.get('reason'),
            'training_n':result['training_n'],
            'dataset_sha256':fingerprint({str(h):{'x':d['x'].tolist(),'y':d['y'].tolist(),
                'times':d['times'].tolist(),'ends':d['ends'].tolist(),'groups':d['groups']}
                for h,d in datasets.items()})}
        if result['status']=='PRETEST_SELECTED':
            candidates.append((name,result));candidate_data[name]=datasets
    if not candidates:
        return {'status':'HEAD_TRAINING_UNAVAILABLE','reason':'NO_HEAD_WITH_CAUSAL_VALIDATION',
            'forecast':None,'training_n':max((v['training_n'] for v in audit.values()),default=0),
            'official_macro_archive':archive.summary(),'macro_feature_search':audit}
    selected={}
    for head_name in ('direction','movement'):
        possible=[(name,model) for name,model in candidates if 'validation_gains_mbit' in model['heads'][head_name]]
        if possible:
            # Tie order is the fixed FEATURES order; no final-test metric enters selection.
            name,model=max(possible,key=lambda item:float(np.mean(
                item[1]['heads'][head_name]['validation_gains_mbit'])))
            # Freeze the winner before evaluating its held-out rows. An
            # unavailable final test must never select a different feature.
            fitted=_fit_family(code,'macro',bars,captured,horizons,None,None,source,
                datasets_override=candidate_data[name],boundaries_override=boundaries,
                feature_variation_required=True)
            selected[head_name]=fitted['heads'][head_name]
            audit[name]['status']=fitted['status'];audit[name]['reason']=fitted.get('reason')
        else:
            selected[head_name]={'working_supported':False,
                'reason':'HEAD_CLASSES_OR_VALIDATION_UNAVAILABLE','forecast':None}
    result={k:v for k,v in candidates[0][1].items() if k not in ('heads','model_sha256','dataset_sha256')}
    result.update(status='TRAINED' if any('head' in row for row in selected.values()) else 'HEAD_TRAINING_UNAVAILABLE',
        heads=selected,features_are_captured_receipts=False,
        official_macro_archive=archive.summary(),macro_feature_search=audit,
        training_n=max(m['training_n'] for _,m in candidates),
        training_cutoff=max(m['training_cutoff'] for _,m in candidates),
        dataset_sha256=fingerprint({name:v['dataset_sha256'] for name,v in audit.items()}),
        historical_feature_availability=(FOMC_AVAILABILITY if any(
            name in FOMC_FEATURES for head in selected.values() for name in head.get('feature_names',[]))
            else 'VERIFIED_PUBLIC_VINTAGE_BEFORE_T0_ACTUAL_RETRIEVAL_RETAINED'))
    result['model_sha256']=fingerprint(result)
    return result


def archive_model_provenance_valid(model,trained):
    proof=model.get('official_macro_archive')
    if not isinstance(proof,dict) or model.get('features_are_captured_receipts') is not False:
        return False
    heads=model.get('heads')
    if not isinstance(heads,dict):
        return False
    selected=set()
    for head in heads.values():
        if not isinstance(head,dict):
            return False
        names=head.get('feature_names',[])
        if not isinstance(names,list) or any(not isinstance(name,str) for name in names):
            return False
        selected.update(set(names) & FOMC_FEATURES)
    guarantees=proof.get('feature_vintage_guarantees')
    if selected and (model.get('historical_feature_availability')!=FOMC_AVAILABILITY
            or not isinstance(guarantees,dict)
            or any(guarantees.get(name)!=[FOMC_VINTAGE] for name in selected)):
        return False
    clocks=[number(proof.get(key)) for key in ('validated_at','source_created_at','source_window_end')]
    return (proof.get('contract_version')==CONTRACT and clocks[0]==trained
        and all(value is not None and 0<value<=trained for value in clocks)
        and proof.get('retrieval_clock_backdated') is False
        and proof.get('prospective_observation') is False and proof.get('production_authority') is False
        and proof.get('offline_copy_revalidation') is True
        and isinstance(proof.get('source_code_sha'),str)
        and re.fullmatch('[0-9a-f]{40}',proof['source_code_sha']) is not None
        and isinstance(proof.get('bundle_sha256'),str)
        and re.fullmatch('[0-9a-f]{64}',proof['bundle_sha256']) is not None)
