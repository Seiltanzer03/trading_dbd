"""Instrument-specific working mathematical edge for manual management.

Pure causal Price heads, chronological validation and an untouched final block.
A proper-score gain is not a net-profit proof. Missing archived GEX units/models
never become a synthetic H2 input. Runtime only reads small frozen artifacts.
"""
from __future__ import annotations
import hashlib
import json
import math
import time
from pathlib import Path
import numpy as np
from .g1_short_horizon_runtime import _fit_logistic, _sigmoid

CONTRACT = 'mathematical-edge-working-v1'
FEATURE_CONTRACT = 'math-completed-5m-price-v1'
FEATURES = ('ret5', 'ret15', 'ret30', 'ret60', 'absret5', 'absret15', 'absret30',
            'rv15', 'rv30', 'rv60', 'range_position', 'tod_sin', 'tod_cos')
MAX_WEIGHT = .15
SHARED_CAP = .40
H2_INTERVAL = (-.00067862, -.00004046)
H2_THETA = 1.38343
H2_MOVEMENT_THRESHOLD = .38
MOVE_RETURN_THRESHOLD = .0002
TARGET_CONTRACT = {'direction': 'UP_GIVEN_ABS_RETURN_GT_2BP',
                   'movement': 'ABS_RETURN_GT_2BP', 'threshold_log_return': MOVE_RETURN_THRESHOLD}
MAX_ARTIFACT_BYTES = 500_000
MAX_ARTIFACT_AGE = 7 * 86400
MAX_PRICE_AGE = 900
SEARCH_HORIZONS = (15, 30, 60, 120)
PATH_TARGET_CONTRACT = {
    'downside_excursion': 'FUTURE_LOW_REACHES_T0_CLOSE_EXP_MINUS_2BP',
    'upside_excursion': 'FUTURE_HIGH_REACHES_T0_CLOSE_EXP_PLUS_2BP',
    'upper_before_lower': 'UPPER_2BP_FIRST_GIVEN_RESOLVED_FIRST_TOUCH_NO_SAME_BAR_TIES',
    'early_first_touch': 'FIRST_2BP_TOUCH_COMPLETED_BAR_END_WITHIN_HALF_HORIZON',
}


def number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, OverflowError):
        return None


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def price_features(bars, captured_ts):
    """Identical historic/live features; only consecutive completed 5m bars."""
    rows = [b for b in bars if number(b.get('bar_end_ts')) is not None
            and b['bar_end_ts'] <= captured_ts and number(b.get('close')) is not None
            and b['close'] > 0]
    rows = sorted(rows, key=lambda b: b['bar_end_ts'])[-13:]
    if len(rows) < 13:
        return None
    ends = np.array([b['bar_end_ts'] for b in rows])
    if np.any(np.abs(np.diff(ends) - 300) > 1) or captured_ts - ends[-1] > MAX_PRICE_AGE:
        return None
    closes = np.array([b['close'] for b in rows])
    returns = np.diff(np.log(closes))
    highs = [number(b.get('high')) for b in rows[-12:]]
    lows = [number(b.get('low')) for b in rows[-12:]]
    if any(v is None or v <= 0 for v in highs + lows):
        return None
    hi, lo = max(highs), min(lows)
    phase = 2 * math.pi * (ends[-1] % 86400) / 86400
    r5, r15, r30, r60 = (float(np.log(closes[-1] / closes[-k])) for k in (2, 4, 7, 13))
    return [r5, r15, r30, r60, abs(r5), abs(r15), abs(r30),
            float(np.sqrt(np.mean(returns[-3:] ** 2))), float(np.sqrt(np.mean(returns[-6:] ** 2))),
            float(np.sqrt(np.mean(returns ** 2))), (closes[-1] - lo) / (hi - lo) if hi > lo else .5,
            math.sin(phase), math.cos(phase)]


def completed_live_bars(raw, captured_ts):
    """Aggregate minute OHLCV only when all five source minutes are present."""
    groups = {}
    for row in raw or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        ts = number(row[0])
        values = [number(v) for v in row[1:5]]
        if ts is None or ts + 60 > captured_ts or any(v is None or v <= 0 for v in values):
            continue
        start = math.floor(ts / 300) * 300
        groups.setdefault(start, {})[int(ts)] = values
    bars = []
    for start, group in sorted(groups.items()):
        if set(group) != {int(start + k * 60) for k in range(5)}:
            continue
        values = [group[int(start + k * 60)] for k in range(5)]
        bars.append({'bar_end_ts': start + 300, 'open': values[0][0],
                     'high': max(v[1] for v in values), 'low': min(v[2] for v in values),
                     'close': values[-1][3]})
    return bars


def dataset(bars, horizon):
    """Nonoverlapping targets, with gaps/session boundaries rejected."""
    bars = sorted(bars, key=lambda b: b['bar_end_ts'])
    xs, targets, ends, times = [], [], [], []
    steps = horizon // 5
    last_target = -1
    for i in range(12, len(bars) - steps):
        t0, target = bars[i]['bar_end_ts'], bars[i + steps]['bar_end_ts']
        if t0 < last_target or abs(target - t0 - horizon * 60) > 1:
            continue
        path = bars[i:i + steps + 1]
        if any(abs(path[j + 1]['bar_end_ts'] - path[j]['bar_end_ts'] - 300) > 1
               for j in range(len(path) - 1)):
            continue
        x = price_features(bars[max(0, i - 12):i + 1], t0)
        if x is None:
            continue
        ret = math.log(bars[i + steps]['close'] / bars[i]['close'])
        xs.append(x); targets.append((int(ret > 0), int(abs(ret) > MOVE_RETURN_THRESHOLD), ret))
        times.append(t0); ends.append(target); last_target = target
    return np.asarray(xs), np.asarray(targets), np.asarray(times), np.asarray(ends)


def fit_head(x, y):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.maximum(scale, 1e-8)
    beta = _fit_logistic(np.clip((x - mean) / scale, -8, 8), y, l2=10.)
    return {'mean': mean.tolist(), 'scale': scale.tolist(), 'beta': beta.tolist(),
            'baseline': float((y.sum() + 1) / (len(y) + 2))}


def predict(head, x):
    mean, scale, beta = (np.asarray(head[k]) for k in ('mean', 'scale', 'beta'))
    z = np.clip((np.asarray(x) - mean) / scale, -8, 8)
    return _sigmoid(beta[0] + z @ beta[1:])


def gain(y, p, baseline):
    p = np.clip(p, .001, .999); b = min(.999, max(.001, baseline))
    losses = -(y * np.log2(p) + (1 - y) * np.log2(1 - p))
    base = -(y * math.log2(b) + (1 - y) * math.log2(1 - b))
    return float(np.mean(base - losses) * 1000)


def head_indices(indices, y, col):
    """Direction is UP/DOWN conditional on MOVE; flat is never DOWN."""
    return indices[y[indices, 1] == 1] if col == 0 else indices


def path_dataset(bars, horizon):
    """Generic 2bp path events; never infer stop/TP ordering inside one OHLC bar."""
    bars = sorted(bars, key=lambda row: row['bar_end_ts'])
    x, _, times, ends = dataset(bars, horizon)
    lookup = {row['bar_end_ts']: index for index, row in enumerate(bars)}
    targets = []
    for t0 in times:
        index = lookup[t0]
        path = bars[index + 1:index + horizon // 5 + 1]
        lower = bars[index]['close'] * math.exp(-MOVE_RETURN_THRESHOLD)
        upper = bars[index]['close'] * math.exp(MOVE_RETURN_THRESHOLD)
        down = [row for row in path if row['low'] <= lower]
        up = [row for row in path if row['high'] >= upper]
        first_down = down[0]['bar_end_ts'] if down else None
        first_up = up[0]['bar_end_ts'] if up else None
        first = min(value for value in (first_down, first_up) if value is not None) if down or up else None
        # With no touch or a same-bar tie, the upper-first outcome is unknown.
        ordered = (float(first_up == first) if first is not None and first_down != first_up else float('nan'))
        early = float(first is not None and first - t0 <= horizon * 30)
        targets.append((float(bool(down)), float(bool(up)), ordered, early))
    return x, np.asarray(targets), times, ends


def train_path_heads(bars, boundaries, horizons):
    """Finite independent heads; one untouched common final time block."""
    candidates = {name: [] for name in PATH_TARGET_CONTRACT}
    audits = {name: {} for name in PATH_TARGET_CONTRACT}
    final_cutoff = boundaries[-1]
    for horizon in horizons:
        x, targets, times, ends = path_dataset(bars, horizon)
        for col, name in enumerate(PATH_TARGET_CONTRACT):
            audit = audits[name][str(horizon)] = {'status': 'INSUFFICIENT_SAMPLE', 'nonoverlapping_n': len(x)}
            if len(x) < 180:
                continue
            observed = np.isfinite(targets[:, col])
            y = targets[:, col]
            validation = []
            for start, stop in zip(boundaries[:-1], boundaries[1:]):
                train = np.flatnonzero(observed & (ends < start - 300))
                valid = np.flatnonzero(observed & (times >= start) & (times < stop) & (ends < final_cutoff - 300))
                if len(train) < 20 or len(valid) < 3 or len(np.unique(y[train])) < 2:
                    break
                head = fit_head(x[train], y[train])
                validation.append(gain(y[valid], predict(head, x[valid]), head['baseline']))
            if len(validation) != 3:
                audit['status'] = 'INSUFFICIENT_VARIATION_OR_VALIDATION_SAMPLE'
                continue
            audit.update(status='VALIDATED_FOR_SELECTION', validation_gains_mbit=validation,
                         selection_gain_mbit=float(np.mean(validation)), final_test_touched_for_selection=False)
            candidates[name].append((float(np.mean(validation)), horizon, x, y, observed, times, ends, validation))
    results = {}
    for name in PATH_TARGET_CONTRACT:
        base = {'target_semantics': PATH_TARGET_CONTRACT[name], 'threshold_log_return': MOVE_RETURN_THRESHOLD,
                'candidate_audit': audits[name], 'search_completed': True, 'net_economic_proof': False,
                'intrabar_order_inferred': False, 'generic_barriers_are_trade_stop_take': False,
                'working_supported': False, 'head': None}
        if not candidates[name]:
            results[name] = {**base, 'status': 'UNRESOLVED', 'reason': 'INSUFFICIENT_VARIATION_OR_SAMPLE'}
            continue
        mean_gain, horizon, x, y, observed, times, ends, validation = max(candidates[name], key=lambda candidate: candidate[0])
        train = np.flatnonzero(observed & (ends < final_cutoff - 300))
        test = np.flatnonzero(observed & (times >= final_cutoff))
        if len(train) < 20 or len(test) < 9 or len(np.unique(y[train])) < 2:
            results[name] = {**base, 'horizon_minutes': horizon, 'status': 'UNRESOLVED',
                             'reason': 'INSUFFICIENT_FINAL_SAMPLE', 'test_n': len(test)}
            continue
        head = fit_head(x[train], y[train])
        chronological_chunks = np.array_split(np.flatnonzero(times >= final_cutoff), 3)
        chunks = [chunk[observed[chunk]] for chunk in chronological_chunks]
        block_gains = [gain(y[ix], predict(head, x[ix]), head['baseline']) if len(ix) >= 3 else None for ix in chunks]
        test_gain = gain(y[test], predict(head, x[test]), head['baseline'])
        positive = sum(value is not None and value > 0 for value in block_gains)
        supported = (mean_gain > 0 and sum(value > 0 for value in validation) >= 2
                     and test_gain > 0 and positive >= 2 and all(value is not None for value in block_gains))
        all_observed = np.flatnonzero(observed)
        test_brier = float(np.mean((predict(head, x[test]) - y[test]) ** 2))
        baseline_brier = float(np.mean((head['baseline'] - y[test]) ** 2))
        results[name] = {**base, 'horizon_minutes': horizon,
                         'status': 'WORKING_SUPPORTED' if supported else 'NO_SUPPORTED_ADVANTAGE_YET',
                         'working_supported': supported, 'gain_mbit': round(test_gain, 5),
                         'block_gains_mbit': block_gains, 'positive_blocks': positive, 'blocks': 3,
                         'validation_gains_mbit': validation, 'validation_mean_gain_mbit': mean_gain,
                         'test_n': len(test), 'test_brier': test_brier, 'baseline_brier': baseline_brier,
                         'brier_gain': baseline_brier-test_brier, 'training_n': len(all_observed),
                         'training_cutoff': float(ends[all_observed[-1]]), 'test_cutoff': final_cutoff,
                         'head': fit_head(x[all_observed], y[all_observed])}
    return results


def train_instrument(code, bars, captured_ts, horizons=SEARCH_HORIZONS):
    horizons = tuple(dict.fromkeys(horizons))
    if not horizons or any(h not in SEARCH_HORIZONS for h in horizons):
        raise ValueError('unsupported mathematical edge search horizons')
    bars = sorted([b for b in bars if b['bar_end_ts'] <= captured_ts], key=lambda b: b['bar_end_ts'])
    candidates = []
    candidate_audit = {}
    anchor_x, _, anchor_times, _ = dataset(bars, 15)
    if len(anchor_x) < 180:
        return {'instrument': code, 'status': 'UNRESOLVED', 'reason': 'INSUFFICIENT_COMPLETED_NONOVERLAPPING_BARS', 'weight_fraction': 0.,
                'requested_horizons_minutes': list(horizons), 'search_completed': True,
                'candidate_audit': {str(h): {'status': 'INSUFFICIENT_ANCHOR_SAMPLE'} for h in horizons}}
    # Freeze one time grid BEFORE trying horizons or conditional head masks.
    boundaries = [float(anchor_times[int(len(anchor_times)*f)]) for f in (.4,.55,.7,.8)]
    final_cutoff = boundaries[-1]
    # Fixed small candidate set. Select on earlier validation, final 20% untouched.
    for horizon in horizons:
        x, y, times, ends = dataset([b for b in bars if b['bar_end_ts'] <= captured_ts], horizon)
        n = len(x)
        candidate_audit[str(horizon)] = {'nonoverlapping_n': n, 'status': 'INSUFFICIENT_SAMPLE'}
        if n < 180:
            continue
        split = int(np.searchsorted(times, final_cutoff))
        if split >= n or n-split < 9:
            candidate_audit[str(horizon)]['status'] = 'INSUFFICIENT_FINAL_SAMPLE'
            continue
        rows = []
        for a_time, b_time in zip(boundaries[:-1], boundaries[1:]):
            train = np.flatnonzero(ends < a_time - 300)
            valid = np.flatnonzero((times >= a_time) & (times < b_time) & (ends < final_cutoff - 300))
            if len(train) < 60 or len(valid) < 10:
                continue
            values = []
            for col in (0, 1):
                train_ix = head_indices(train, y, col)
                test_ix = head_indices(valid, y, col)
                if len(train_ix) < 20 or len(test_ix) < 3:
                    values.append(None)
                    continue
                head = fit_head(x[train_ix], y[train_ix, col])
                values.append(gain(y[test_ix, col], predict(head, x[test_ix]), head['baseline']))
            rows.append(values)
        if len(rows) < 3:
            candidate_audit[str(horizon)]['status'] = 'INSUFFICIENT_VALIDATION_BLOCKS'
            continue
        scores = [float(np.mean([r[k] for r in rows])) for k in (0, 1) if all(r[k] is not None for r in rows)]
        if not scores:
            candidate_audit[str(horizon)]['status'] = 'CONDITIONAL_HEAD_SAMPLE_UNAVAILABLE'
            continue
        score = max(scores)
        candidate_audit[str(horizon)].update(status='VALIDATED_FOR_SELECTION',
                                            selection_gain_mbit=score,
                                            validation_gains_mbit=rows,
                                            final_test_touched_for_selection=False)
        candidates.append((score, horizon, x, y, times, ends, split, rows))
    if not candidates:
        return {'instrument': code, 'status': 'UNRESOLVED', 'reason': 'INSUFFICIENT_COMPLETED_NONOVERLAPPING_BARS', 'weight_fraction': 0.,
                'requested_horizons_minutes': list(horizons), 'search_completed': True,
                'candidate_audit': candidate_audit}
    _, horizon, x, y, times, ends, split, validation = max(candidates, key=lambda c: c[0])
    train = np.flatnonzero(ends < final_cutoff - 300)
    diagnostics, heads = {}, {}
    for col, name in enumerate(('direction', 'movement')):
        train_ix = head_indices(train, y, col)
        test_ix = head_indices(np.arange(split, len(x)), y, col)
        if len(train_ix) < 20 or len(test_ix) < 9:
            diagnostics[name] = {'gain_mbit': None, 'working_supported': False, 'test_n': len(test_ix),
                                 'status': 'CONDITIONAL_HEAD_SAMPLE_UNAVAILABLE'}
            heads[name] = None
            continue
        head = fit_head(x[train_ix], y[train_ix, col])
        chunks = [head_indices(ix, y, col) for ix in np.array_split(np.arange(split, len(x)), 3)]
        fold_gain = [gain(y[ix, col], predict(head, x[ix]), head['baseline']) if len(ix) >= 3 else None for ix in chunks]
        test_gain = gain(y[test_ix, col], predict(head, x[test_ix]), head['baseline'])
        valid_positive = sum(row[col] is not None and row[col] > 0 for row in validation)
        validation_mean = float(np.mean([row[col] for row in validation])) if all(row[col] is not None for row in validation) else None
        positive = sum(g is not None and g > 0 for g in fold_gain)
        eligible = (test_gain > 0 and validation_mean is not None and validation_mean > 0
                    and valid_positive >= 2 and positive >= 2 and all(g is not None for g in fold_gain))
        diagnostics[name] = {'gain_mbit': round(test_gain, 5), 'block_gains_mbit': fold_gain,
                             'positive_blocks': positive, 'blocks': len(fold_gain),
                             'validation_gains_mbit': [r[col] for r in validation], 'validation_mean_gain_mbit': validation_mean,
                             'working_supported': eligible,
                             'test_n': len(test_ix), 'test_brier': float(np.mean((predict(head, x[test_ix]) - y[test_ix, col]) ** 2)),
                             'baseline_brier': float(np.mean((head['baseline'] - y[test_ix, col]) ** 2)),
                             'target_semantics': 'UP_GIVEN_ABS_RETURN_GT_2BP' if col == 0 else 'ABS_RETURN_GT_2BP'}
        diagnostics[name]['brier_gain'] = diagnostics[name]['baseline_brier'] - diagnostics[name]['test_brier']
        all_ix = head_indices(np.arange(len(x)), y, col)
        heads[name] = fit_head(x[all_ix], y[all_ix, col])
    path_heads = train_path_heads(bars, boundaries, horizons)
    price_supported = any(d['working_supported'] for d in diagnostics.values())
    path_supported = any(d['working_supported'] for d in path_heads.values())
    supported = price_supported or path_supported
    body = {'instrument': code, 'horizon_minutes': horizon, 'status': 'WORKING_SUPPORTED' if supported else 'NO_SUPPORTED_ADVANTAGE_YET',
            'feature_contract': FEATURE_CONTRACT, 'features': list(FEATURES), 'heads': heads,
            'target_contract': dict(TARGET_CONTRACT),
            'diagnostics': diagnostics, 'training_cutoff': float(ends[-1]),
            'path_target_contract': dict(PATH_TARGET_CONTRACT), 'path_heads': path_heads,
            'training_n': len(x), 'training_days': len(set((times // 86400).astype(int))),
            'test_cutoff': final_cutoff, 'validation_time_boundaries': boundaries,
            'candidate_test_cutoffs': {str(c[1]): final_cutoff for c in candidates}, 'source_sha256': fingerprint(bars),
            'working_eligible': supported, 'formal_eligible': False, 'net_economic_proof': False,
            'price_heads_supported': price_supported, 'path_heads_supported': path_supported,
            'evidence_label': 'HISTORICAL_CHRONOLOGICAL_WORKING', 'candidate_count': len(candidates),
            'requested_horizons_minutes': list(horizons), 'candidate_audit': candidate_audit, 'search_completed': True,
            'selection': 'earlier_three_validation_blocks_then_untouched_final_20pct',
            'GEX_status': 'NOT_SUPPORTED_OR_ARCHIVED_FEATURE_CONTRACT_MISSING'}
    body['model_sha256'] = fingerprint(body)
    return body


def h2_controller(instrument, movement, dot_gex, feature_contract=None):
    """Archive-compatible units must be explicit; no generic GEX slope mapping."""
    if instrument not in {'EURUSD', 'USDCAD'}:
        return {'available': False, 'reason': 'FX_H2_NOT_TRANSFERRED_TO_THIS_INSTRUMENT', 'weight_fraction': 0.}
    if feature_contract != 'archived-dot-gex-v1' or number(dot_gex) is None:
        return {'available': False, 'reason': 'ARCHIVED_GEX_DERIVATIVE_UNITS_UNAVAILABLE', 'weight_fraction': 0.}
    p = number(movement)
    if p is None or not 0 <= p <= 1:
        return {'available': False, 'reason': 'MOVEMENT_PROBABILITY_UNAVAILABLE', 'weight_fraction': 0.}
    h2 = H2_INTERVAL[0] <= dot_gex <= H2_INTERVAL[1]
    adjusted = p / (p + H2_THETA * (1 - p)) if h2 else p
    return {'available': True, 'h2': h2, 'm_price': p, 'm_price_gex': adjusted,
            'action': 'VETO' if p >= H2_MOVEMENT_THRESHOLD > adjusted else 'KEEP',
            'formal_eligible': False, 'weight_fraction': 0., 'role': 'ARCHIVE_RULE_DIAGNOSTIC_ONLY'}


def load_artifact(path, captured_ts):
    try:
        path = Path(path)
        if not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
            return None
        report = json.loads(path.read_text())
        if not isinstance(report, dict) or not isinstance(report.get('instruments'), dict):
            return None
        age = captured_ts - float(report['created_ts'])
        if report.get('contract_version') != CONTRACT or not 0 <= age <= MAX_ARTIFACT_AGE:
            return None
        if report.get('automatic_execution') is not False or report.get('production_authority') is not False:
            return None
        from .runtime_git_identity import runtime_git_sha
        expected = runtime_git_sha()
        if not expected or len(expected) != 40 or report.get('published_for_sha') != expected:
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return None


def runtime_gex_diagnostics(engine, code, captured, movement):
    """Read an existing causal frozen observation; never recompute its history."""
    from .mathematical_edge_archive import phi_g
    from .edge_discovery.ai_context import _latest_frozen_context
    context = _latest_frozen_context(engine, {'captured_ts': captured, 'strategy': {'instrument': code}})
    observed = number(context.get('observation_t0'))
    gex = context.get('gex') or {}
    source_ts = number(gex.get('ts'))
    if observed is None or source_ts is None or not 0 <= captured-observed <= 900 or not 0 <= captured-source_ts <= 1800:
        return h2_controller(code, movement, None)
    dynamics = gex.get('dynamics') or {}
    force = dynamics.get('force_score') or {}
    h2 = h2_controller(code, movement, force.get('slope') if force.get('available') else None, 'archived-dot-gex-v1')
    h2['source_ts'] = source_ts
    h2['gex_derivative_path'] = 'g1s_evidence_v3.gex.dynamics.force_score.slope'
    h2['phi_g'] = phi_g((context.get('v2_option_context') or {}).get('gex_net_balance'),
                         (dynamics.get('field_score') or {}).get('slope'), force.get('slope'),
                         (dynamics.get('stiffness_score') or {}).get('slope'))
    h2['price_decoder_is_archived'] = False
    h2['probability_semantics'] = 'NEW_WORKING_HEAD_ARCHIVE_THETA_COUNTERFACTUAL_ONLY'
    h2['entry_veto_is_not_existing_position_exit'] = True
    return h2


def runtime_path_predictions(model, features, captured):
    """Separate generic path probabilities, with no implied direction or order."""
    if model.get('path_target_contract') != PATH_TARGET_CONTRACT:
        return {}
    output = {}
    for name, semantics in PATH_TARGET_CONTRACT.items():
        row = (model.get('path_heads') or {}).get(name) or {}
        if not row.get('working_supported') or row.get('target_semantics') != semantics or not row.get('head'):
            continue
        cutoff = number(row.get('training_cutoff'))
        if cutoff is None or cutoff > captured:
            continue
        probability = float(predict(row['head'], features))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            continue
        age_days = (captured-cutoff)/86400
        quality = (min(1., max(0., row['gain_mbit']/20))
                   * min(1., row['positive_blocks']/max(1, row['blocks']))
                   * min(1., row['test_n']/100) * max(0., 1-age_days/90))
        output[name] = {'probability': probability, 'baseline_probability': row['head']['baseline'],
                        'horizon_minutes': row['horizon_minutes'], 'target_semantics': semantics,
                        'test_n': row['test_n'], 'gain_mbit': row['gain_mbit'],
                        'quality_multiplier': round(quality, 6),
                        'max_effective_weight_fraction': round(MAX_WEIGHT*quality, 6),
                        'training_age_days': round(age_days, 2), 'ranking_only': True,
                        'net_economic_proof': False, 'independent_evidence_vote': False,
                        'intrabar_order_inferred': False, 'generic_barriers_are_trade_stop_take': False}
    return output


def crypto_training_source_admission(report, model, code):
    """Bind crypto training to the actual configured quote/venue, not its alias.

    USD/USDT and cross-venue return transfer require measured validation. There
    is no such transfer contract implemented here: a `validated: true` flag or
    USD≈USDT assertion never admits a proxy. Offline searches remain permitted.
    """
    from .config import CRYPTO_INSTRUMENTS
    instrument = CRYPTO_INSTRUMENTS.get(code)
    if instrument is None:
        return {'available': True, 'reason': 'LEGACY_TRADFI_WORKING_PROXY_POLICY'}
    base = {'available': False, 'configured_symbol': instrument.binance_symbol,
            'configured_quote_currency': 'USDT', 'measured_transfer_mapping_available': False}
    raw = report.get('sources')
    sources = [row for row in raw if isinstance(row, dict) and row.get('instrument') == code] if isinstance(raw, list) else []
    if len(sources) != 1:
        return {**base, 'reason': 'CRYPTO_TRAINING_SOURCE_PROVENANCE_UNAVAILABLE'}
    source = sources[0]
    semantics = source.get('source_semantics') or {}
    if not isinstance(semantics, dict):
        return {**base, 'reason': 'CRYPTO_TRAINING_SOURCE_PROVENANCE_INVALID'}
    base.update(provider=source.get('provider'), ticker=source.get('ticker'),
                provider_quote_currency=semantics.get('provider_quote_currency'),
                currency_basis_mismatch=semantics.get('currency_basis_mismatch'))
    if (semantics.get('currency_basis_mismatch') is not False
            or semantics.get('provider_quote_currency') != 'USDT'
            or semantics.get('configured_quote_currency') != 'USDT'):
        return {**base, 'reason': 'CRYPTO_USD_USDT_PRICE_MAPPING_UNVALIDATED'}
    if (source.get('provider') != 'Binance' or source.get('ticker') != instrument.binance_symbol
            or source.get('interval') != '5m'
            or source.get('source_kind') not in {'SINGLE_PROVIDER_COMPLETED_5M', 'SINGLE_RETAINED_PROVIDER_COMPLETED_5M'}
            or semantics.get('synthetic_price_history') is not False):
        return {**base, 'reason': 'CRYPTO_PRICE_VENUE_OR_SERIES_MAPPING_UNVALIDATED'}
    digest = model.get('source_sha256')
    if (not isinstance(digest, str) or len(digest) != 64
            or source.get('validated_bars_sha256') != digest):
        return {**base, 'reason': 'CRYPTO_MODEL_TRAINING_SOURCE_HASH_UNBOUND'}
    return {**base, 'available': True, 'reason': 'EXACT_CONFIGURED_BINANCE_USDT_TRAINING_SERIES'}


def runtime_profile(engine, tick, trade):
    captured = number(tick.get('ts'))
    base = {'contract_version': CONTRACT, 'available': False, 'weight_fraction': 0.,
            'max_weight_fraction': MAX_WEIGHT, 'formal_eligible': False, 'automatic_execution_source': False,
            'hard_risk_modified': False, 'independent_evidence_vote': False}
    code = str(trade.get('instrument') or tick.get('instrument') or '')
    base['instrument'] = code
    if captured is None or time.time() - captured > MAX_PRICE_AGE or captured > time.time() + 1:
        return {**base, 'reason': 'T0_TIMESTAMP_UNAVAILABLE_OR_STALE'}
    feed = getattr(engine, 'market', None)
    if getattr(feed, 'instrument_code', None) != code:
        return {**base, 'reason': 'PRICE_FEED_INSTRUMENT_MISMATCH'}
    data_dir = getattr(getattr(engine, 'settings', None), 'data_dir', None)
    report = load_artifact(Path(data_dir) / 'research' / 'mathematical_edge_latest.json', captured) if data_dir else None
    if report is None:
        return {**base, 'reason': 'CURRENT_WORKING_MODEL_UNAVAILABLE'}
    model = (report.get('instruments') or {}).get(code) or {}
    if not isinstance(model, dict):
        return {**base, 'reason': 'WORKING_MODEL_INVALID'}
    base.update({k: model[k] for k in ('status', 'model_sha256', 'horizon_minutes', 'training_cutoff', 'training_n', 'diagnostics') if k in model})
    body = {k: v for k, v in model.items() if k != 'model_sha256'}
    try:
        digest = fingerprint(body)
    except (TypeError, ValueError):
        return {**base, 'reason': 'WORKING_MODEL_INVALID'}
    if (model.get('instrument') != code or model.get('feature_contract') != FEATURE_CONTRACT or model.get('features') != list(FEATURES)
            or model.get('target_contract') != TARGET_CONTRACT
            or model.get('model_sha256') != digest
            or number(model.get('training_cutoff')) is None or model['training_cutoff'] > captured):
        return {**base, 'reason': 'MODEL_CONTRACT_HASH_OR_CAUSAL_CUTOFF_INVALID'}
    from .config import CRYPTO_INSTRUMENTS
    if code in CRYPTO_INSTRUMENTS:
        admission = crypto_training_source_admission(report, model, code)
        base['training_price_source_admission'] = admission
        if not admission['available']:
            return {**base, 'reason': admission['reason']}
        # Training provenance cannot lend its authority to another live series.
        authority = getattr(feed, 'intraday_source_authority', None)
        authority = authority if isinstance(authority, dict) else {}
        observed, received = number(authority.get('observed_ts')), number(authority.get('available_at'))
        if (authority.get('provider') != 'Binance'
                or authority.get('source_symbol') != CRYPTO_INSTRUMENTS[code].binance_symbol
                or authority.get('target_instrument') != code
                or authority.get('source_verified') is not True or authority.get('derived') is not False
                or observed is None or received is None
                or not 0 < observed <= received <= captured or captured-observed > MAX_PRICE_AGE):
            return {**base, 'reason': 'CRYPTO_LIVE_CONFIGURED_SERIES_AUTHORITY_UNAVAILABLE'}
    features = price_features(completed_live_bars(getattr(feed, 'intraday_ohlcv', []), captured), captured)
    if features is None:
        return {**base, 'reason': 'COMPLETED_CAUSAL_5M_PRICE_FEATURES_UNAVAILABLE'}
    completed = completed_live_bars(getattr(feed, 'intraday_ohlcv', []), captured)
    base.update(captured_ts=captured, latest_bar_end_ts=completed[-1]['bar_end_ts'])
    try:
        base['path_predictions'] = runtime_path_predictions(model, features, captured)
        ps = {name: float(predict(model['heads'][name], features)) if model['heads'].get(name) else None for name in ('direction', 'movement')}
        if any(p is not None and (not math.isfinite(p) or not 0 <= p <= 1) for p in ps.values()):
            raise ValueError('nonfinite probability')
        ds = model['diagnostics']
        direction = str(trade.get('direction') or '').lower()
        if direction not in {'long', 'buy', 'short', 'sell'}:
            return {**base, 'reason': 'POSITION_DIRECTION_UNAVAILABLE'}
        sign = 1 if direction in {'long', 'buy'} else -1
        if ds['direction']['working_supported']:
            d = sign * (2 * ps['direction'] - 1) * ps['movement']
            diagnostic = ds['direction']; role = 'PRICE_DIRECTION'
        elif ds['movement']['working_supported']:
            baseline = model['heads']['movement']['baseline']
            d = -max(0., (baseline - ps['movement']) / max(baseline, .01))
            diagnostic = ds['movement']; role = 'LOW_MOVEMENT_TIME_MANAGEMENT'
        else:
            return {**base, 'reason': 'NO_SUPPORTED_ADVANTAGE_YET', 'probabilities': ps}
        quality = max(0., min(1., diagnostic['gain_mbit'] / 20)) * min(1., diagnostic['positive_blocks'] / max(1, diagnostic['blocks']))
        # A small holdout and old observations earn a smaller working weight.
        # Historical data remain usable; age/sample uncertainty is visible.
        age_days = max(0., (captured - model['training_cutoff']) / 86400)
        quality *= min(1., diagnostic['test_n'] / 100) * max(0., 1 - age_days / 90)
        w = MAX_WEIGHT * quality * abs(d)
        bars = completed_live_bars(getattr(feed, 'intraday_ohlcv', []), captured)
        return {**base, 'available': w > 0, 'reason': 'WORKING_PRICE_EDGE' if w > 0 else 'NO_DIRECTIONAL_MANAGEMENT_PREFERENCE',
                'weight_fraction': round(w, 6), 'direction_score': round(d, 6), 'preferred_close_fraction': round((1-d)/2, 6),
                'base_policy_eligible': role == 'PRICE_DIRECTION',
                'eligible_extended_roles': list(('TIME_STOP', 'REDUCE_TAKE')) if role != 'PRICE_DIRECTION' else None,
                'probabilities': ps, 'role': role, 'latest_bar_end_ts': bars[-1]['bar_end_ts'], 'captured_ts': captured,
                'feature_values': dict(zip(FEATURES, features)), 'working_eligible': True,
                'runtime_price_feature_source': ('Exact configured Binance USDT completed OHLCV; not broker execution bars'
                    if code in CRYPTO_INSTRUMENTS else 'Yahoo intraday OHLCV; may be mapped to current broker basis; price proxy, not broker execution bars'),
                'training_age_days': round(age_days, 2), 'quality_multiplier': round(quality, 6),
                'h2': runtime_gex_diagnostics(engine, code, captured, ps['movement']), 'ranking_only': True,
                'net_economic_proof': False}
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        return {**base, 'reason': 'WORKING_MODEL_INVALID'}


def combine_math_profile(profile, mathematical):
    """One shared cap, no extra independent evidence family for price transforms."""
    mw = min(MAX_WEIGHT, max(0., number(mathematical.get('weight_fraction')) or 0.)) if mathematical.get('available') else 0.
    if mw == 0:
        return {**profile, 'mathematical_component_weight': 0., 'mathematical_extended_component_weight': 0.}
    old = max(0., number(profile.get('weight_fraction')) or 0.)
    if mathematical.get('base_policy_eligible') is False:
        return {**profile, 'mathematical_component_weight': 0.,
                'mathematical_extended_component_weight': round(min(mw, max(0., SHARED_CAP-old)), 6),
                'mathematical_base_role': 'MOVEMENT_IS_NOT_A_CLOSE_OR_DIRECTION_SIGNAL'}
    total = old + mw
    d = (old * (number(profile.get('direction_score')) or 0.) + mw * mathematical['direction_score']) / total
    scale = min(SHARED_CAP, total) / total
    return {**profile, 'available': True, 'weight_fraction': round(total * scale, 6),
            'max_weight_fraction': SHARED_CAP,
            'direction_score': round(d, 6), 'preferred_close_fraction': round((1-d)/2, 6),
            'mathematical_component_weight': round(mw * scale, 6), 'pre_math_weight_fraction': old,
            'mathematical_extended_component_weight': round(mw * scale, 6),
            'pre_math_direction_score': profile.get('direction_score'),
            'pre_math_profile': {k: profile.get(k) for k in ('available', 'weight_fraction', 'direction_score', 'preferred_close_fraction')},
            'active_component_weight': round((number(profile.get('active_component_weight')) or 0.) * scale, 6),
            'exploratory_component_weight': round((number(profile.get('exploratory_component_weight')) or 0.) * scale, 6),
            'component_scale': scale, 'shared_cap': SHARED_CAP,
            'basis': 'shared_bounded_historical_edge_and_causal_working_price_model'}


def render_math_edge(profile, combined=None, ranking_audit=None):
    combined = combined or {}
    if not profile.get('probabilities'):
        return '\n\n**МАТЕМАТИЧЕСКИЙ EDGE** —\nВес 0; причина: ' + str(profile.get('reason') or 'UNAVAILABLE') + '. Расчёт продолжается без этого веса.'
    ps = profile.get('probabilities') or {}
    def probability(value):
        return f'{value:.1%}' if number(value) is not None else 'UNAVAILABLE'
    audit = ranking_audit or {}
    chosen_diagnostic = (profile.get('diagnostics') or {}).get('direction' if profile.get('role') == 'PRICE_DIRECTION' else 'movement', {})
    score = number(chosen_diagnostic.get('gain_mbit'))
    gain_text = f'{score:+.3f}' if score is not None else 'UNAVAILABLE'
    transition = ''
    if audit.get('raw_policy_without_mathematical_edge') and audit.get('raw_policy_with_edge'):
        transition = f"Выбор до gate без mathematical edge: {audit['raw_policy_without_mathematical_edge']}; с ним: {audit['raw_policy_with_edge']}. Финальный план определяется gate и арбитром.\n"
    return ('\n\n**МАТЕМАТИЧЕСКИЙ EDGE** —\n'
            f"{profile['instrument']}, горизонт {profile.get('horizon_minutes')} мин.; роль {profile.get('role')}. "
            f"P(up | move) {probability(ps.get('direction'))}; P(move >2bp) {probability(ps.get('movement'))}. "
            'Это оценки модели, а не гарантия исхода. '
            f"Мягкий вес базовых политик {combined.get('mathematical_component_weight', profile['weight_fraction']):.1%}; "
            f"расширенных действий {combined.get('mathematical_extended_component_weight', profile['weight_fraction']):.1%}; общий лимит 40%.\n"
            f"Проверка вне обучения: {gain_text} mbit/event; "
            f"модель {str(profile.get('model_sha256') or '')[:12]}.\n"
            + transition +
            'Источник признаков: intraday Yahoo proxy; возможна привязка к текущей шкале брокера. Это не история фактических исполнений.\n'
            'Рабочая историческая модель; proper-score не является доказательством прибыли. '
            'Price-признаки не создают новую независимую семью. Hard CVaR и допуски действий обязательны. '
            'Влияние Price на базовый выбор ограничено потерей не более 0.03R исходного net Expected относительно лучшей допустимой политики. '
            f"GEX H2: {(profile.get('h2') or {}).get('reason') or (profile.get('h2') or {}).get('action') or 'диагностика'}; вес 0. "
            'Самостоятельный сигнал пониженной вероятности движения влияет только на TIME_STOP/REDUCE_TAKE; это не направление цены и не команда закрыть позицию.')


def extended_ranking_bonus(policy, profile, effective_weight=None):
    """At most .0045R; applied AFTER every extended-action hard gate."""
    if not profile.get('available'):
        return 0.
    roles = profile.get('eligible_extended_roles')
    if roles is not None and policy not in roles:
        return 0.
    w = min(MAX_WEIGHT, max(0., number(effective_weight if effective_weight is not None else profile.get('weight_fraction')) or 0.))
    d = max(-1., min(1., number(profile.get('direction_score')) or 0.))
    affinity = max(0., d) if policy == 'EXTEND_TAKE' else max(0., -d)
    return round(.03 * w * affinity, 8)
