#!/usr/bin/env python3
"""Filesystem-only off-host archive, causal dataset, model and artifact pipeline.

No provider/network/database access or production activation occurs here. Restore
archive.json from an explicitly authorized private storage boundary before a
subsequent run; 14-day workflow retention is not a permanent archive. GitHub
artifacts in a public repository are not private training-data storage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from seiltanzer.edge_family_adapters import (FAMILIES, FEATURE_CONTRACT, MODEL_CONTRACT,
                                            WEIGHT_POOLS, training_label_clock_valid,
                                            outcome_semantics_valid)
from seiltanzer.unified_edge_runtime_context import MAX_BYTES, VERSION

MAX_DATA_BYTES = 96_000_000
# The existing exporter caps compressed SSH transport separately at 32 MB.
MAX_EXPORT_BYTES = MAX_DATA_BYTES


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _validate_json(value, depth=0):
    if depth > 64:
        raise ValueError('JSON_DEPTH_BOUND')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('NONFINITE_JSON')
    if isinstance(value, dict):
        for item in value.values():
            _validate_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_json(item, depth + 1)


def read_json(path, *, max_bytes):
    with Path(path).open('rb') as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('INPUT_EXCEEDS_BYTE_BOUND')
    try:
        value = json.loads(raw, object_pairs_hook=_strict_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
        _validate_json(value)
    except (RecursionError, UnicodeDecodeError) as exc:
        raise ValueError('INVALID_BOUNDED_JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('JSON_OBJECT_REQUIRED')
    return value


def encode_json(value, *, max_bytes):
    _validate_json(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf8')
    if len(raw) > max_bytes:
        raise ValueError('OUTPUT_EXCEEDS_BYTE_BOUND')
    return raw


def _clock(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _ridge_actions_valid(model):
    actions = model.get('action_models')
    if not isinstance(actions, dict) or not actions or len(actions) > 12:
        return False
    for action, value in actions.items():
        if (not isinstance(action, str) or not action or action == 'HOLD'
                or not isinstance(value, dict) or value.get('validated') is not True):
            return False
        from seiltanzer.edge_family_action_binding import action_binding_valid
        if not action_binding_valid(action, value):
            return False
        intercept, coefficients = value.get('intercept_r'), value.get('coefficients')
        if (not isinstance(intercept, (int, float)) or isinstance(intercept, bool)
                or not math.isfinite(intercept) or not isinstance(coefficients, dict)
                or not 1 <= len(coefficients) <= 32):
            return False
        for feature, beta in coefficients.items():
            if (not isinstance(feature, str) or not feature
                    or not isinstance(beta, (int, float)) or isinstance(beta, bool)
                    or not math.isfinite(beta)):
                return False
    return True


def package_runtime_context(models, *, expected_sha, captured_ts, dataset):
    if not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise ValueError('IMMUTABLE_DEPLOYMENT_SHA_REQUIRED')
    if not _clock(captured_ts):
        raise ValueError('ARTIFACT_CLOCK_INVALID')
    if (dataset.get('synthetic') is True or
            any(row.get('synthetic') is True for row in dataset.get('rows', []))):
        return None
    if not models:
        return None
    if not isinstance(models, list) or len(models) > 128:
        raise ValueError('MODEL_MATRIX_BOUND')
    instruments = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get('validation'), dict):
            raise ValueError('UNVALIDATED_MODEL_NOT_PUBLISHABLE')
        from seiltanzer.edge_family_geometry import portable_artifact_reason
        if portable_artifact_reason(model) is not None:
            raise ValueError('UNVALIDATED_PORTABLE_GEOMETRY_NOT_PUBLISHABLE')
        validation = model.get('validation', {})
        clocks = [model.get(key) for key in ('train_end_ts', 'validation_start_ts',
                                            'validation_end_ts', 'trained_at')]
        horizon = model.get('horizon_minutes')
        if (model.get('contract_version') != MODEL_CONTRACT
                or model.get('feature_contract_version') != FEATURE_CONTRACT
                or model.get('family_id') not in FAMILIES
                or model.get('component_id') not in WEIGHT_POOLS
                or not model.get('model_version')
                or not isinstance(model.get('dataset_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', model['dataset_sha256'])
                or (dataset.get('dataset_sha256') is not None
                    and model['dataset_sha256'] != dataset['dataset_sha256'])
                or not _clock(model.get('score_scale_r'))
                or not _ridge_actions_valid(model)
                or not isinstance(model.get('instrument'), str) or not model['instrument']
                or model.get('synthetic') is True
                or not isinstance(model.get('geometry_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', model['geometry_sha256'])
                or not all(_clock(value) for value in clocks) or not _clock(horizon)
                or not clocks[0] + horizon * 60 <= clocks[1] < clocks[2] <= clocks[3] <= captured_ts
                or not training_label_clock_valid(model)
                or validation.get('status') != 'OOS_VALIDATED'
                or validation.get('point_in_time') is not True
                or validation.get('purged_split') is not True
                or validation.get('costs_included') is not True
                or validation.get('outcomes') != 'OBSERVED_NET_ACTION_DELTA_VS_HOLD'
                or not outcome_semantics_valid(validation, evidence_key='evidence_kind')
                or not _clock(validation.get('proper_score_gain'))
                or not _clock(validation.get('sample_count')) or validation['sample_count'] < 20
                or not isinstance(validation['sample_count'], int)
                or not _clock(validation.get('fold_count')) or validation['fold_count'] < 2
                or not isinstance(validation['fold_count'], int)):
            raise ValueError('UNVALIDATED_MODEL_NOT_PUBLISHABLE')
        families = instruments.setdefault(model['instrument'], {'edge_family_models': {}})['edge_family_models']
        families.setdefault(model['family_id'], []).append(model)
    if len(instruments) > 32:
        raise ValueError('INSTRUMENT_MATRIX_BOUND')
    document = {'version': VERSION, 'deployment_sha': expected_sha,
                'captured_ts': captured_ts, 'instruments': instruments}
    encode_json(document, max_bytes=MAX_BYTES)
    return document


def run_pipeline(reviews, *, expected_sha, previous_archive=None, trained_at=None):
    from seiltanzer.edge_family_archive import assemble_archive
    from seiltanzer.edge_family_dataset import build_family_dataset
    from seiltanzer.edge_family_training import train_family_models
    # Validate SHA before any expensive processing or output mutation.
    package_runtime_context([], expected_sha=expected_sha,
                            captured_ts=trained_at or time.time(), dataset={'rows': []})
    trained_at = time.time() if trained_at is None else trained_at
    archive = assemble_archive([reviews], previous=previous_archive)
    dataset = build_family_dataset(archive)
    if reviews.get('synthetic') is True:
        # A CI envelope must not promote models even from a restored archive.
        dataset['synthetic'] = True
    training = train_family_models(dataset, trained_at=trained_at)
    context = package_runtime_context(training['models'], expected_sha=expected_sha,
                                      captured_ts=trained_at, dataset=dataset)
    context_raw = encode_json(context, max_bytes=MAX_BYTES) if context is not None else None
    matrix = {instrument: {family: len(models) for family, models in selected['edge_family_models'].items()}
              for instrument, selected in (context or {}).get('instruments', {}).items()}
    diagnostics = {'version': 'edge-family-pipeline-v1', 'deployment_sha': expected_sha,
                   'captured_ts': trained_at, 'available': context is not None,
                   'status': 'AVAILABLE' if context else 'UNAVAILABLE',
                   'archive_state': ('RESTORED' if previous_archive is not None else
                                     'INITIAL_EMPTY' if not archive.get('episodes') else 'INITIAL'),
                   'reason': 'VALIDATED_RUNTIME_CONTEXT_PACKAGED' if context else training.get('reason', 'MODELS_UNAVAILABLE'),
                   'document_sha256': hashlib.sha256(context_raw).hexdigest() if context_raw else None,
                   'model_matrix': matrix, 'active_model_count': sum(sum(values.values()) for values in matrix.values()),
                   'archive_dataset_sha256': archive.get('dataset_sha256'),
                   'dataset_sha256': dataset.get('dataset_sha256'),
                   'archive_exclusions': archive.get('exclusions', []),
                   'archive_evictions': archive.get('evictions', []),
                   'dataset_exclusions': dataset.get('exclusions', []),
                   'training': {key: value for key, value in training.items() if key != 'models'},
                   'runtime_artifact_created': context is not None,
                   'production_activation_performed': False, 'production_or_database_writes': 0,
                   'network_calls': 0, 'historical_profit_proven': False,
                   'archive_retention': 'bounded restored archive; workflow artifacts expire after 14 days'}
    from scripts.audit_readiness_matrix import build_report, enrich_report
    readiness = build_report({'captured_ts': trained_at, 'instruments': {}}, input_sha256=None)
    readiness.update(code_sha=expected_sha, input_basis='NO_CURRENT_SOURCE_CAPTURE_PROVIDED')
    enrich_report(readiness, expected_sha=expected_sha, training=diagnostics, reviews=reviews)
    readiness['evidence_input_sha256'] = {
        'training': hashlib.sha256(encode_json(diagnostics, max_bytes=MAX_DATA_BYTES)).hexdigest(),
        'reviews': hashlib.sha256(encode_json(reviews, max_bytes=MAX_EXPORT_BYTES)).hexdigest()}
    from scripts.p3_history_readiness import build_historical_readiness
    history_readiness = build_historical_readiness(archive, dataset, diagnostics)
    return {'archive': archive, 'dataset': dataset, 'diagnostics': diagnostics,
            **history_readiness,
            'runtime_context': context, 'readiness': readiness}


def write_outputs(result, output_dir):
    # Serialize everything before touching outputs. An oversized artifact cannot
    # leave a partly updated bundle or silently dropped subset of models.
    payloads = {name: encode_json(value, max_bytes=MAX_BYTES if name == 'runtime_context' else MAX_DATA_BYTES)
                for name, value in result.items() if value is not None}
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    staged = {}
    try:
        # Finish and fsync every file before replacing any visible output. A
        # disk/write failure during staging preserves the previous bundle.
        # Each final file is atomically replaced; readers pin the runtime hash.
        for name, raw in payloads.items():
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.' + name + '.', delete=False) as stream:
                staged[name] = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        for name, temporary in staged.items():
            os.replace(temporary, directory / (name + '.json'))
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    if result.get('runtime_context') is None:
        # Reusing a directory with insufficient inputs must not retain a model.
        (directory / 'runtime_context.json').unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviews', required=True)
    parser.add_argument('--archive', help='Previous archive.json restored from authorized private storage')
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    reviews = read_json(args.reviews, max_bytes=MAX_EXPORT_BYTES)
    archive = read_json(args.archive, max_bytes=MAX_DATA_BYTES) if args.archive else None
    result = run_pipeline(reviews, expected_sha=args.expected_sha, previous_archive=archive)
    write_outputs(result, args.output_dir)
    print(json.dumps({key: result['diagnostics'][key] for key in
                      ('available', 'reason', 'active_model_count', 'document_sha256')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
