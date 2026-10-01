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
FEATURES = ('ret5', 'ret15', 'ret60', 'rv15', 'rv60', 'range_position', 'tod_sin', 'tod_cos')
MAX_WEIGHT = .15
SHARED_CAP = .40
H2_INTERVAL = (-.00067862, -.00004046)
H2_THETA = 1.38343
H2_MOVEMENT_THRESHOLD = .38
MOVE_RETURN_THRESHOLD = .0002
MAX_ARTIFACT_BYTES = 500_000
MAX_ARTIFACT_AGE = 7 * 86400
MAX_PRICE_AGE = 900


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
    return [float(returns[-1]), float(np.log(closes[-1] / closes[-4])),
            float(np.log(closes[-1] / closes[-13])), float(np.std(returns[-3:])),
            float(np.std(returns)), (closes[-1] - lo) / (hi - lo) if hi > lo else .5,
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


def train_instrument(code, bars, captured_ts):
    bars = sorted([b for b in bars if b['bar_end_ts'] <= captured_ts], key=lambda b: b['bar_end_ts'])
    candidates = []
    # Fixed small candidate set. Select on earlier validation, final 20% untouched.
    for horizon in (15, 30, 60):
        x, y, times, ends = dataset([b for b in bars if b['bar_end_ts'] <= captured_ts], horizon)
        n = len(x)
        if n < 180:
            continue
        split = int(n * .8)
        rows = []
        for frac in (.4, .55, .7):
            a, b = int(n * frac), min(split, int(n * (frac + .15)))
            train = np.flatnonzero(ends[:a] < times[a] - 300)
            if len(train) < 60 or b - a < 10:
                continue
            values = []
            for col in (0, 1):
                head = fit_head(x[train], y[train, col])
                values.append(gain(y[a:b, col], predict(head, x[a:b]), head['baseline']))
            rows.append(values)
        if len(rows) < 3:
            continue
        score = max(float(np.mean(np.array(rows)[:, k])) for k in (0, 1))
        candidates.append((score, horizon, x, y, times, ends, split, rows))
    if not candidates:
        return {'instrument': code, 'status': 'UNRESOLVED', 'reason': 'INSUFFICIENT_COMPLETED_NONOVERLAPPING_BARS', 'weight_fraction': 0.}
    _, horizon, x, y, times, ends, split, validation = max(candidates, key=lambda c: c[0])
    train = np.flatnonzero(ends[:split] < times[split] - 300)
    diagnostics, heads = {}, {}
    for col, name in enumerate(('direction', 'movement')):
        head = fit_head(x[train], y[train, col])
        chunks = np.array_split(np.arange(split, len(x)), 3)
        fold_gain = [gain(y[ix, col], predict(head, x[ix]), head['baseline']) for ix in chunks if len(ix)]
        test_gain = gain(y[split:, col], predict(head, x[split:]), head['baseline'])
        valid_positive = sum(row[col] > 0 for row in validation)
        eligible = test_gain > 0 and valid_positive >= 2 and sum(g > 0 for g in fold_gain) >= 2
        diagnostics[name] = {'gain_mbit': round(test_gain, 5), 'block_gains_mbit': fold_gain,
                             'positive_blocks': sum(g > 0 for g in fold_gain), 'blocks': len(fold_gain),
                             'validation_gains_mbit': [r[col] for r in validation], 'working_supported': eligible,
                             'test_n': len(x) - split, 'test_brier': float(np.mean((predict(head, x[split:]) - y[split:, col]) ** 2))}
        heads[name] = fit_head(x, y[:, col])
    supported = any(d['working_supported'] for d in diagnostics.values())
    body = {'instrument': code, 'horizon_minutes': horizon, 'status': 'WORKING_SUPPORTED' if supported else 'NO_SUPPORTED_ADVANTAGE_YET',
            'feature_contract': FEATURE_CONTRACT, 'features': list(FEATURES), 'heads': heads,
            'diagnostics': diagnostics, 'training_cutoff': float(ends[-1]),
            'training_n': len(x), 'training_days': len(set((times // 86400).astype(int))),
            'test_cutoff': float(times[split]), 'source_sha256': fingerprint(bars),
            'working_eligible': supported, 'formal_eligible': False, 'net_economic_proof': False,
            'evidence_label': 'HISTORICAL_CHRONOLOGICAL_WORKING', 'candidate_count': len(candidates),
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
        if report.get('published_for_sha') != runtime_git_sha():
            return None
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
    if (model.get('feature_contract') != FEATURE_CONTRACT or model.get('features') != list(FEATURES)
            or model.get('model_sha256') != digest
            or number(model.get('training_cutoff')) is None or model['training_cutoff'] > captured):
        return {**base, 'reason': 'MODEL_CONTRACT_HASH_OR_CAUSAL_CUTOFF_INVALID'}
    features = price_features(completed_live_bars(getattr(feed, 'intraday_ohlcv', []), captured), captured)
    if features is None:
        return {**base, 'reason': 'COMPLETED_CAUSAL_5M_PRICE_FEATURES_UNAVAILABLE'}
    try:
        ps = {name: float(predict(model['heads'][name], features)) for name in ('direction', 'movement')}
        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in ps.values()):
            raise ValueError('nonfinite probability')
        ds = model['diagnostics']
        direction = str(trade.get('direction') or '').lower()
        if direction not in {'long', 'buy', 'short', 'sell'}:
            return {**base, 'reason': 'POSITION_DIRECTION_UNAVAILABLE'}
        sign = 1 if direction in {'long', 'buy'} else -1
        if ds['direction']['working_supported']:
            d = sign * (2 * ps['direction'] - 1)
            diagnostic = ds['direction']; role = 'PRICE_DIRECTION'
        elif ds['movement']['working_supported']:
            baseline = model['heads']['movement']['baseline']
            d = -max(0., (baseline - ps['movement']) / max(baseline, .01))
            diagnostic = ds['movement']; role = 'LOW_MOVEMENT_CAUTION'
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
                'probabilities': ps, 'role': role, 'latest_bar_end_ts': bars[-1]['bar_end_ts'], 'captured_ts': captured,
                'feature_values': dict(zip(FEATURES, features)), 'working_eligible': True,
                'training_age_days': round(age_days, 2), 'quality_multiplier': round(quality, 6),
                'h2': h2_controller(code, ps['movement'], None), 'ranking_only': True,
                'net_economic_proof': False}
    except (KeyError, TypeError, ValueError, IndexError, ZeroDivisionError):
        return {**base, 'reason': 'WORKING_MODEL_INVALID'}


def combine_math_profile(profile, mathematical):
    """One shared cap, no extra independent evidence family for price transforms."""
    mw = min(MAX_WEIGHT, max(0., number(mathematical.get('weight_fraction')) or 0.)) if mathematical.get('available') else 0.
    if mw == 0:
        return {**profile, 'mathematical_component_weight': 0.}
    old = max(0., number(profile.get('weight_fraction')) or 0.)
    total = old + mw
    d = (old * (number(profile.get('direction_score')) or 0.) + mw * mathematical['direction_score']) / total
    scale = min(SHARED_CAP, total) / total
    return {**profile, 'available': True, 'weight_fraction': round(total * scale, 6),
            'direction_score': round(d, 6), 'preferred_close_fraction': round((1-d)/2, 6),
            'mathematical_component_weight': round(mw * scale, 6), 'pre_math_weight_fraction': old,
            'pre_math_direction_score': profile.get('direction_score'),
            'pre_math_profile': {k: profile.get(k) for k in ('available', 'weight_fraction', 'direction_score', 'preferred_close_fraction')},
            'active_component_weight': round((number(profile.get('active_component_weight')) or 0.) * scale, 6),
            'exploratory_component_weight': round((number(profile.get('exploratory_component_weight')) or 0.) * scale, 6),
            'component_scale': scale, 'shared_cap': SHARED_CAP,
            'basis': 'shared_bounded_historical_edge_and_causal_working_price_model'}


def render_math_edge(profile, combined=None, ranking_audit=None):
    combined = combined or {}
    if not profile.get('available'):
        return '\n\n**МАТЕМАТИЧЕСКИЙ EDGE** —\nВес 0; причина: ' + str(profile.get('reason') or 'UNAVAILABLE') + '. Расчёт продолжается без этого веса.'
    ps = profile.get('probabilities') or {}
    audit = ranking_audit or {}
    transition = ''
    if audit.get('raw_policy_without_mathematical_edge') and audit.get('raw_policy_with_edge'):
        transition = f"Выбор до gate без mathematical edge: {audit['raw_policy_without_mathematical_edge']}; с ним: {audit['raw_policy_with_edge']}. Финальный план определяется gate и арбитром.\n"
    return ('\n\n**МАТЕМАТИЧЕСКИЙ EDGE** —\n'
            f"{profile['instrument']}, горизонт {profile.get('horizon_minutes')} мин.; роль {profile.get('role')}. "
            f"P(up) {ps.get('direction', 0):.1%}; P(move) {ps.get('movement', 0):.1%}. "
            f"Мягкий вес {profile['weight_fraction']:.1%}; внутри общего лимита 40%: "
            f"{combined.get('mathematical_component_weight', profile['weight_fraction']):.1%}.\n"
            f"Проверка вне обучения: {(profile.get('diagnostics') or {}).get('direction' if profile.get('role') == 'PRICE_DIRECTION' else 'movement', {}).get('gain_mbit', 0):+.3f} mbit/event; "
            f"модель {str(profile.get('model_sha256') or '')[:12]}.\n"
            + transition +
            'Рабочая историческая модель; proper-score не является доказательством прибыли. '
            'Price-признаки не создают новую независимую семью. Hard CVaR и допуски действий обязательны. '
            f"GEX H2: {(profile.get('h2') or {}).get('reason') or 'диагностика'}.")


def extended_ranking_bonus(policy, profile, effective_weight=None):
    """At most .0045R; applied AFTER every extended-action hard gate."""
    if not profile.get('available'):
        return 0.
    w = min(MAX_WEIGHT, max(0., number(effective_weight if effective_weight is not None else profile.get('weight_fraction')) or 0.))
    d = max(-1., min(1., number(profile.get('direction_score')) or 0.))
    affinity = max(0., d) if policy == 'EXTEND_TAKE' else max(0., -d)
    return round(.03 * w * affinity, 8)
