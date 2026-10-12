"""Bounded reuse of existing fits; fitting and current-input authority stay separate."""
from copy import deepcopy
import json

from .config import ALL_INSTRUMENTS
from .edge_family_adapters import FAMILIES
from .edge_family_market import _model_reason
from .mathematical_edge import CONTRACT, number
from .real_input_integrity import simulated_evidence

MAX_BYTES = 8_000_000
RESTORABLE = {'INSUFFICIENT_HISTORY', 'HEAD_TRAINING_UNAVAILABLE', 'HISTORICAL_FEATURES_UNAVAILABLE'}


def retain_fitted_families(current_models, previous_reports, captured_ts):
    """Retain only on input insufficiency; never replace an actual current fit."""
    cutoff = number(captured_ts)
    if cutoff is None or cutoff <= 0 or not isinstance(current_models, dict):
        raise ValueError('FAMILY_RETENTION_CAPTURE_OR_MATRIX_INVALID')
    if (len(current_models) > len(ALL_INSTRUMENTS)
            or any(code not in ALL_INSTRUMENTS or not isinstance(rows, dict)
                or not set(rows).issubset(FAMILIES) for code, rows in current_models.items())):
        raise ValueError('FAMILY_RETENTION_MATRIX_INVALID')
    result = deepcopy(current_models)
    audit = {'contract_version': 'fitted-family-continuity-v1', 'new_observations': False,
        'current_run_fitted': sum(isinstance(m, dict) and m.get('status') == 'TRAINED'
            for rows in current_models.values() for m in rows.values()),
        'retained_fitted': 0, 'retained_models': [], 'rejected_prior_models': [],
        'rejected_prior_reports': [], 'selection_uses_heldout_score': False}
    candidates = {}
    reports = previous_reports if isinstance(previous_reports, (list, tuple)) else []
    if len(reports) > 2:
        audit['rejected_prior_reports'].append('PREVIOUS_REPORT_COUNT_OVER_BOUND')
        reports = []
    for index, report in enumerate(reports):
        try:
            if (not isinstance(report, dict) or report.get('contract_version') != CONTRACT
                    or report.get('production_authority') is not False
                    or report.get('automatic_execution') is not False
                    or simulated_evidence(report, recursive=False)
                    or number(report.get('created_ts')) is None
                    or not 0 < report['created_ts'] <= cutoff
                    or len(json.dumps(report, allow_nan=False).encode()) > MAX_BYTES):
                raise ValueError('PREVIOUS_REPORT_INVALID')
            matrix = report.get('family_market_models')
            if (not isinstance(matrix, dict) or not set(matrix).issubset(ALL_INSTRUMENTS)
                    or any(not isinstance(rows, dict) or not set(rows).issubset(FAMILIES)
                           for rows in matrix.values())):
                raise ValueError('PREVIOUS_MODEL_MATRIX_INVALID')
            for code, rows in matrix.items():
                for family, model in rows.items():
                    if not isinstance(model, dict) or model.get('status') != 'TRAINED':
                        continue
                    reason = _model_reason(model, code, family, cutoff)
                    if reason is None and model['trained_at'] > report['created_ts']:
                        reason = 'MARKET_MODEL_AFTER_ORIGINAL_REPORT'
                    if reason:
                        audit['rejected_prior_models'].append({'instrument': code,
                            'family': family, 'reason': reason, 'report_index': index})
                        continue
                    candidates.setdefault((code, family), {})[model['model_sha256']] = model
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            audit['rejected_prior_reports'].append({'report_index': index, 'reason': 'PREVIOUS_REPORT_INVALID'})
    for code, rows in result.items():
        for family, current in rows.items():
            if (not isinstance(current, dict) or current.get('status') not in RESTORABLE
                    or any(isinstance(h, dict) and 'head' in h for h in (current.get('heads') or {}).values())):
                continue
            possible = list(candidates.get((code, family), {}).values())
            if not possible:
                continue
            newest = max(m['trained_at'] for m in possible)
            winners = [m for m in possible if m['trained_at'] == newest]
            if len(winners) != 1:
                audit['rejected_prior_models'].append({'instrument': code, 'family': family,
                    'reason': 'AMBIGUOUS_ORIGINAL_FIT_TIME'})
                continue
            model = winners[0]
            rows[family] = deepcopy(model)
            audit['retained_models'].append({'instrument': code, 'family': family,
                'model_sha256': model['model_sha256'], 'trained_at': model['trained_at'],
                'training_cutoff': model['training_cutoff'], 'current_status': current['status'],
                'current_reason': current.get('reason'), 'original_model_unchanged': True})
    audit['retained_fitted'] = len(audit['retained_models'])
    audit['total_fitted'] = audit['current_run_fitted'] + audit['retained_fitted']
    return result, audit


def read_fitted_cache(path):
    """Optional local cache; no unavailable input can block current training."""
    from pathlib import Path
    try:
        file=Path(path)
        if file.stat().st_size>MAX_BYTES:return None
        value=json.loads(file.read_text())
        return value if isinstance(value,dict) else None
    except (OSError,ValueError,TypeError,RecursionError):
        return None


def write_fitted_cache(report,path):
    """Store model contracts only, without raw market or private observations."""
    from pathlib import Path
    if (report.get('contract_version')!=CONTRACT or report.get('production_authority') is not False
            or report.get('automatic_execution') is not False or simulated_evidence(report,recursive=False)):
        raise ValueError('FITTED_CACHE_REPORT_INVALID')
    keys=('contract_version','created_ts','production_authority','automatic_execution',
          'family_market_models','published_for_sha','publication_contract_version','publication_run_id')
    value={key:report[key] for key in keys if key in report}
    raw=json.dumps(value,allow_nan=False)
    if len(raw.encode())>MAX_BYTES:raise ValueError('FITTED_CACHE_OVER_BOUND')
    file=Path(path);file.parent.mkdir(parents=True,exist_ok=True)
    temporary=file.with_suffix(file.suffix+'.tmp');temporary.write_text(raw);temporary.replace(file)
