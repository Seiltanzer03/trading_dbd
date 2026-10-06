#!/usr/bin/env python3
"""Finite offline consolidation of saved search and scheme-comparison outputs.

No new provider calls, model search, fitting, simulation or trading side effects.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.mathematical_edge import SEARCH_HORIZONS, TARGET_CONTRACT, PATH_TARGET_CONTRACT

MAX_BYTES = 8_000_000
TARGETS = tuple(key for key, value in TARGET_CONTRACT.items() if isinstance(value, str)) + tuple(PATH_TARGET_CONTRACT)


def mapping(value):
    return value if isinstance(value, dict) else {}


def generation(value, sha):
    return 'SHA_NOT_REPORTED' if not value else 'EXACT_REPORT_SHA' if value == sha else 'HISTORICAL_OTHER_SHA'


def build_report(math, comparison, *, expected_sha):
    if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
        raise ValueError('FULL_CODE_SHA_REQUIRED')
    matrix, instruments = [], []
    for code in ALL_INSTRUMENTS:
        row = mapping(mapping(math.get('instruments')).get(code))
        context = mapping(mapping(math.get('instrument_matrix')).get(code))
        role = context.get('management_role', 'NOT_REPORTED')
        blocked = role == 'DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED'
        instruments.append({'instrument': code, 'search_completed': row.get('search_completed') is True,
            'status': row.get('status', 'NOT_REPORTED'), 'reason': row.get('reason') or context.get('reason'),
            'management_role': role, 'mapping_blocks_working_use': blocked,
            'training_age_days': context.get('training_age_days'),
            'training_too_old_for_runtime': context.get('training_too_old_for_runtime'),
            'source_available': context.get('source_available'),
            'source_semantics': context.get('source_semantics')})
        for target in TARGETS:
            path = target in PATH_TARGET_CONTRACT
            head = mapping(mapping(row.get('path_heads') if path else row.get('diagnostics')).get(target))
            audit = mapping(head.get('candidate_audit') if path else row.get('candidate_audit'))
            selected = head.get('horizon_minutes') if path else row.get('horizon_minutes')
            for horizon in SEARCH_HORIZONS:
                candidate = mapping(audit.get(str(horizon)))
                matrix.append({'instrument': code, 'target': target, 'horizon_minutes': horizon,
                    'target_semantics': head.get('target_semantics') or (PATH_TARGET_CONTRACT if path else TARGET_CONTRACT)[target],
                    'search_status': candidate.get('status', 'NOT_REPORTED'),
                    'search_completed': (head if path else row).get('search_completed') is True,
                    'selected_horizon': selected == horizon,
                    'selected_supported_model': selected == horizon and head.get('working_supported') is True,
                    'head_status': head.get('status', 'NOT_REPORTED'),
                    'nonoverlapping_n': candidate.get('nonoverlapping_n'),
                    'selection_gain_mbit': candidate.get('selection_gain_mbit'),
                    'selected_model_test_n': head.get('test_n') if selected == horizon else None,
                    'selected_model_gain_mbit': head.get('gain_mbit') if selected == horizon else None,
                    'management_role': role, 'mapping_blocks_working_use': blocked,
                    'runtime_applied_weight': None, 'net_economic_proof': False})
    schemes = {}
    for name, row in mapping(comparison.get('summary')).items():
        if len(schemes) >= 32:
            raise ValueError('SCHEME_REPORT_BOUND')
        row = mapping(row)
        schemes[name] = {key: row.get(key) for key in (
            'model_scenarios', 'observed_path_replay', 'historical_economic_completeness')}
    return {'version': 'unified-edge-finite-completion-report-v1', 'expected_sha': expected_sha,
        'input_generations': {'math': generation(math.get('published_for_sha'), expected_sha),
            'comparison': generation(comparison.get('expected_sha'), expected_sha)},
        'input_code_shas': {'math': math.get('published_for_sha'), 'comparison': comparison.get('expected_sha')},
        'input_created_ts': {'math': math.get('created_ts'), 'comparison': comparison.get('created_ts')},
        'math_instruments': instruments, 'math_matrix': matrix,
        'comparison_review_n': comparison.get('review_n'), 'comparison_reason': comparison.get('reason'),
        'comparison_instrument_coverage': comparison.get('instrument_coverage', []),
        'schemes': schemes, 'review_transitions': comparison.get('review_transitions'),
        'actual_execution_observations': comparison.get('actual_execution_observations'),
        'production_authority': False, 'historical_profit_proven': False,
        'current_release_efficacy_proven': False, 'optimal_weights_proven': False,
        'limitations': [
            'Report generation is not a new search or evidence of current production activation.',
            'Missing target/horizon evidence stays NOT_REPORTED; unsupported models get no inferred runtime vote.',
            'Generic 2bp path targets are not the actual trade stop/take geometry.',
            'Statistical support does not prove positive net management value.',
            'Model scenarios, observed path counterfactual replay and actual execution observations are separate.',
            'Overlapping reviews are not independent trades or a settled portfolio equity ledger.',
            'A report expected_sha is not proof that every old frozen review was produced by that code.',
            'No live orders, paid LLM calls or provider requests were performed.']}


def render_markdown(report):
    def value(x):
        if x is None:
            return 'не сообщено'
        return str(x).replace('|', '/').replace('\n', ' ')
    lines = ['# Итоговая сверка unified edge', '',
        'Сводка сохранённых результатов. Прибыльность и оптимальность весов не доказаны.', '',
        'SHA целевой версии: `' + report['expected_sha'] + '`.', '',
        'Исходные отчёты: math=' + report['input_generations']['math'] +
        '; comparison=' + report['input_generations']['comparison'] + '.', '',
        'Их времена создания и SHA сохранены в JSON. Старые отчёты не являются проверкой нового выпуска.', '',
        '## Математический поиск', '',
        '| Инструмент | Поиск завершён | Статус | Роль по отчёту | Поддержанные выбранные головы |',
        '| --- | --- | --- | --- | --- |']
    for row in report['math_instruments']:
        supported = [c['target'] + ':' + str(c['horizon_minutes']) for c in report['math_matrix']
                     if c['instrument'] == row['instrument'] and c['selected_supported_model']]
        lines.append('| ' + ' | '.join(value(x) for x in (row['instrument'], row['search_completed'],
            row['status'], row['management_role'], ', '.join(supported) or 'нет')) + ' |')
    lines += ['', 'Полная матрица инструмент × цель × горизонт находится в JSON. '+
        'NOT_REPORTED означает отсутствие результата в исходном файле. Поддержанная голова '+
        'не означает фактический runtime-вес; mapping и возраст могут ограничивать применение.', '',
        '## Сравнение схем', '', 'Сохранённых разборов: ' + value(report['comparison_review_n']) + '.', '',
        '| Схема | Парных разборов | Разных сделок | Средняя ΔR против HOLD | CVaR10 воспроизведения |',
        '| --- | --- | --- | --- | --- |']
    for name, row in report['schemes'].items():
        observed = mapping(row.get('observed_path_replay'))
        lines.append('| ' + ' | '.join(value(x) for x in (name, observed.get('paired_review_n'),
            observed.get('paired_distinct_trade_n'), observed.get('mean_paired_delta_vs_hold_r'),
            observed.get('descriptive_cvar10_net_r_on_remaining'))) + ' |')
    lines += ['', 'Модельные Expected/CVaR, издержки, частота вмешательств, ablations, '+
        'смены решения и наблюдения исполнения сохранены отдельно в JSON, если есть во входном отчёте. '+
        'Replay на фактической траектории остаётся контрфактическим расчётом; он не становится broker fill.', '',
        '## Ограничения', ''] + ['- ' + item for item in report['limitations']]
    return '\n'.join(lines) + '\n'


def read(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('REPORT_INPUT_BYTE_BOUND')
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError('REPORT_INPUT_NOT_OBJECT')
    json.dumps(document, allow_nan=False)
    return document, hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--math', type=Path, required=True)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--output-prefix', type=Path, required=True)
    args = parser.parse_args()
    math, math_hash = read(args.math)
    comparison, comparison_hash = read(args.comparison)
    report = build_report(math, comparison, expected_sha=args.expected_sha)
    report['input_sha256'] = {'math': math_hash, 'comparison': comparison_hash}
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.output_prefix.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False,
        sort_keys=True, indent=2, allow_nan=False) + '\n')
    args.output_prefix.with_suffix('.md').write_text(render_markdown(report))
    print('FINITE_COMPLETION_REPORT', len(report['math_matrix']), 'cells; no new search or provider calls')


if __name__ == '__main__':
    main()
