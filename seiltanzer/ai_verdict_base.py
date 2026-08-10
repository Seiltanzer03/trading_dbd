"""Quantitative, stateful AI review of the active trade.

The position action is selected by deterministic policy simulations. The language
model may explain the result, but it may not change the selected policy or numbers.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .ai_policy import analyze_policies, metric_coverage
from .source_asof import rows_as_of
from .strategy_playbooks import PLAYBOOKS as SETUP_PLAYBOOKS


SYSTEM_PROMPT = """Ты — аналитический интерфейс количественного менеджера уже открытой сделки.
Действие и все числа уже рассчитаны детерминированным policy_manager. Нельзя менять
policy_manager.recommendation.policy, объём закрытия, значения политик, границу отмены
или придумывать дополнительные действия. Не пересказывай входовой сетап.

Все группы metric_coverage уже проверены движком: option first-touch/stop/no-touch,
barrier EV, полная RND и центральная траектория конуса, реальные экспирации IV и
локальная проекция 1–24h, live tape 5/15/60m, ATR/режим/VRP, VWAP/дневные/implied
уровни/POC/value area/delta, полная корреляционная матрица, OI walls/GEX, фильтры,
качество цены/цепочки и история. OI/GEX — только контекст, если знак дилерской позиции
не наблюдается. Не называй локальную IV-проекцию новой опционной котировкой.

Запрещены размытые объяснения: «выше потенциальная прибыль», «слишком раннее действие», «лучший risk-adjusted», «ситуация ухудшается» без чисел. Каждое сравнение
альтернатив должно содержать конкретные Expected R и CVaR10. Не используй английские
термины без числовой расшифровки.

Ответ 260–430 слов, строго с разделами:
ДЕЙСТВИЕ — дословно action_ru.
РАСЧЁТ ПОЛИТИК — все HOLD/CLOSE_10/CLOSE_25/CLOSE_50/EXIT: Expected R, медиана,
CVaR10 и вероятность ближайшего рубежа раньше стопа.
ПОЧЕМУ ВЫБРАНО — числовое сравнение выбранной политики с HOLD и EXIT, ограничение
CVaR и устойчивость пересчётов. Если optimizer был понижен gate, перечисли причины.
ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ — конкретные метрики и значения; ничего не скрывай.
РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ — контрфактические вклады цены/sigma/drift/skew/term, если есть.
ПОСЛЕ ИСПОЛНЕНИЯ — точная доля остатка, стоп, БУ/trailing и следующий рубеж.
ГРАНИЦА ОТМЕНЫ — только рассчитанная граница; если её нет, так и скажи.
СЛЕДУЮЩИЙ ПЕРЕСЧЁТ — новая цепочка, движение ±0.15R, рубеж/стоп или граница отмены.
КАЧЕСТВО ДАННЫХ — возраст цепочки, proxy и доля доступных групп метрик.
"""


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _rnd(value: Any, digits: int = 4) -> float | None:
    value = _num(value)
    return round(value, digits) if value is not None else None


def _at(value: Any, *path: str, default=None):
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _fmt_r(value: Any) -> str:
    value = _num(value)
    return "—" if value is None else f"{value:+.3f}R"


def _fmt_pct(value: Any) -> str:
    value = _num(value)
    return "—" if value is None else f"{value * 100:.1f}%"


def _price_from_r(trade: dict, r_value: float | None) -> float | None:
    if r_value is None:
        return None
    entry = _num(trade.get("entry"))
    stop = _num(trade.get("stop"))
    if entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    sign = 1.0 if trade.get("direction") == "long" else -1.0
    return entry + sign * float(r_value) * risk


def _previous_full_snapshot(engine, trade_id: int) -> dict | None:
    """Load the latest stored machine snapshot for counterfactual attribution."""
    journal = engine.journal
    try:
        with journal._lock:  # internal read, protected by the journal's own lock
            row = journal._conn.execute(
                "SELECT snapshot_json FROM ai_verdicts WHERE trade_id=? "
                "ORDER BY ts DESC LIMIT 1", (trade_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _forecast_history(engine, trade_id: int, as_of_ts: float | None = None) -> dict:
    rows = engine.journal.option_forecast_history(trade_id, limit=180)
    if as_of_ts is not None:
        rows = rows_as_of(rows, as_of_ts)
    if not rows:
        return {"samples": 0, "available": False}
    first, latest = rows[0], rows[-1]
    keys = ("r", "p_take", "p_stop", "p_unresolved", "option_ev")

    def delta(key: str, left: dict, right: dict):
        a, b = _num(left.get(key)), _num(right.get(key))
        return round(a - b, 4) if a is not None and b is not None else None

    def point(row: dict) -> dict:
        return {k: _rnd(row.get(k)) for k in keys}

    def window(minutes: int) -> dict:
        target = (_num(latest.get("ts")) or 0.0) - minutes * 60.0
        base = min(rows, key=lambda row: abs((_num(row.get("ts")) or 0.0) - target))
        return {k: delta(k, latest, base) for k in keys}

    return {
        "available": True, "samples": len(rows),
        "minutes": round((float(latest["ts"]) - float(first["ts"])) / 60.0, 1),
        "open": point(first), "current": point(latest),
        "delta_from_open": {k: delta(k, latest, first) for k in keys},
        "delta_windows": {"5m": window(5), "15m": window(15), "60m": window(60)},
        "chain_age_sec": _rnd(latest.get("chain_age_sec"), 1),
        "source": latest.get("source"),
    }


def _time_context(tick: dict, trade: dict, previous_reviews: list[dict]) -> dict:
    now_ts = _num(tick.get("ts")) or time.time()
    local_tz_name = os.environ.get("APP_TIMEZONE", "Europe/Athens")
    local_tz = ZoneInfo(local_tz_name)
    now_utc = datetime.fromtimestamp(now_ts, timezone.utc)
    now_local = now_utc.astimezone(local_tz)
    opened = datetime.fromtimestamp(float(trade["opened_at"]), timezone.utc)
    ny_tz = ZoneInfo("America/New_York")
    ny = now_utc.astimezone(ny_tz)
    regular_open = datetime.combine(ny.date(), dt_time(9, 30), ny_tz)
    regular_close = datetime.combine(ny.date(), dt_time(16, 0), ny_tz)
    if ny.weekday() < 5 and regular_open <= ny <= regular_close:
        session, event = "US regular open", "to_close"
        session_minutes = round((regular_close - ny).total_seconds() / 60.0)
    elif ny.weekday() < 5 and ny < regular_open:
        session, event = "US premarket", "to_open"
        session_minutes = round((regular_open - ny).total_seconds() / 60.0)
    else:
        session, event = "US post/closed", "to_next_open"
        next_open = regular_open + timedelta(days=1)
        while next_open.weekday() >= 5:
            next_open += timedelta(days=1)
        session_minutes = round((next_open - ny).total_seconds() / 60.0)
    last_ts = _num(previous_reviews[-1].get("ts")) if previous_reviews else None
    return {
        "captured_local": now_local.isoformat(timespec="seconds"),
        "timezone": local_tz_name, "session": session,
        "session_event": event, "session_minutes": session_minutes,
        "minutes_open": round((now_ts - float(trade["opened_at"])) / 60.0, 1),
        "minutes_since_review": round((now_ts - last_ts) / 60.0, 1) if last_ts else None,
    }


def _strategy(engine, trade: dict) -> dict:
    from .config import SETUPS
    cfg = SETUPS.get(int(trade.get("setup") or 0))
    if not cfg:
        return {}
    stats = engine.journal.setup_stats(cfg.num, engine.settings.journal_min_trades)
    return {
        "setup": cfg.num, "name": cfg.name, "instrument": cfg.instrument,
        "direction": trade.get("direction"), "playbook": SETUP_PLAYBOOKS.get(cfg.num, {}),
        "stats": stats.__dict__,
        "management": {
            "rungs_r": [1.0, 1.25, 1.5, 1.75, 2.0, 2.2],
            "take_fraction_each_rung": 0.10,
            "breakeven_after_r": 1.5,
            "trailing": "5m/15m only after 1.5R",
            "forbidden": ["widen_stop", "average_loss"],
        },
    }


def _observation(tick: dict, policy: dict, trade: dict) -> dict:
    prob = tick.get("prob") or {}
    price = _num(_at(tick, "feeds", "price", "value"))
    # The complete option/IV/levels/correlation/feed blocks already live under
    # policy_manager.evidence.  Repeating them here added 12-14 kB to every LLM
    # request and occasionally breached the snapshot budget without adding any
    # information.  Keep only unique position geometry plus explicit references
    # so model and deterministic renderers share one canonical metric copy.
    return {
        "position": {
            "price": _rnd(price), "r": _rnd(prob.get("r"), 4),
            "max_r": _rnd(_at(tick, "ladder", "max_r"), 4),
            "to_take_r": _rnd((prob.get("T") or 0) - (prob.get("r") or 0), 4),
            "to_stop_r": _rnd((prob.get("r") or 0) + 1.0, 4),
        },
        "exact_levels": {"entry": trade.get("entry"), "stop": trade.get("stop"),
                         "take": trade.get("take"), "current": price},
        "canonical_metric_paths": {
            "market_evidence": "policy_manager.evidence",
            "option_path_inputs": "policy_manager.inputs",
            "policy_outcomes": "policy_manager.policies",
            "execution_and_ladder": "policy_manager.recommendation",
        },
    }


def build_snapshot(engine) -> dict:
    tick = engine.tick_payload()
    captured_ts = time.time()
    trade = tick.get("trade")
    if not trade:
        return {"captured_ts": captured_ts, "trade_id": None,
                "message": "нет активной сделки"}
    trade_id = int(trade["id"])
    ridge = engine.ridge_payload()
    previous_full = _previous_full_snapshot(engine, trade_id)
    previous_inputs = _at(previous_full or {}, "policy_manager", "inputs")
    previous_evidence = _at(previous_full or {}, "policy_manager", "evidence")
    policy = analyze_policies(engine, tick, ridge, trade,
                              previous_policy_inputs=previous_inputs,
                              previous_evidence=previous_evidence)
    # Finalize T after every deterministic calculation/write used by the review.
    # All admitted market observations must be at or before this boundary.
    captured_ts = time.time()
    history = _forecast_history(engine, trade_id, captured_ts)
    policy["metric_coverage"] = metric_coverage(policy.get("evidence") or {}, history)
    previous_reviews = engine.journal.recent_ai_contexts(trade_id, limit=3)
    cancellation = policy.get("cancellation_boundary") or {}
    switch = cancellation.get("hold_switch") if cancellation.get("available") else None
    if switch:
        switch["price"] = _rnd(_price_from_r(trade, _num(switch.get("r"))), 4)
    return {
        "captured_ts": captured_ts, "trade_id": trade_id,
        "strategy": _strategy(engine, trade),
        "time_context": _time_context(tick, trade, previous_reviews),
        "observation": _observation(tick, policy, trade),
        "metric_history": history,
        "policy_manager": policy,
        "metric_coverage": policy["metric_coverage"],
        "previous_reviews": [{"ts": x.get("ts"), "metrics": x.get("metrics")}
                             for x in previous_reviews],
        "validation": engine.journal.validation_report(),
    }


def _evidence_line(item: dict) -> str:
    metric = item.get("metric", "metric")
    fields = []
    for key, value in item.items():
        if key in ("metric", "context_only"):
            continue
        if isinstance(value, float):
            fields.append(f"{key}={value:.4f}")
        else:
            fields.append(f"{key}={value}")
    suffix = "; контекст без самостоятельного веса" if item.get("context_only") else ""
    return f"{metric}: " + ", ".join(fields) + suffix


def render_policy_report(snapshot: dict) -> str:
    manager = snapshot.get("policy_manager") or {}
    recommendation = manager.get("recommendation") or {}
    policies = manager.get("policies") or {}
    selected = recommendation.get("policy", "HOLD")
    action = recommendation.get("action_ru", "НЕ СОКРАЩАТЬ ПОЗИЦИЮ")
    selected_metrics = policies.get(selected) or {}
    hold = policies.get("HOLD") or {}
    exit_ = policies.get("EXIT") or {}
    rule = manager.get("selection_rule") or {}
    stability = manager.get("stability") or {}
    evidence = manager.get("evidence") or {}
    coverage = manager.get("metric_coverage", {}).get("summary", {})
    inputs = manager.get("inputs") or {}

    lines = [f"**ДЕЙСТВИЕ** — {action}.", "", "**РАСЧЁТ ПОЛИТИК** —"]
    for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        m = policies.get(name) or {}
        lines.append(
            f"{name}: Expected {_fmt_r(m.get('expected_final_r'))}; "
            f"медиана {_fmt_r(m.get('median_final_r'))}; "
            f"CVaR10 {_fmt_r(m.get('cvar10_r'))}; "
            f"P(рубеж раньше стопа) {_fmt_pct(m.get('p_next_rung_before_stop'))}; "
            f"P(стоп раньше рубежа) {_fmt_pct(m.get('p_stop_before_next_rung'))}; "
            f"P(без события 60м) {_fmt_pct(_at(m, 'no_event_probability', '60m'))}."
        )

    adv_mean = (_num(selected_metrics.get("expected_final_r")) or 0.0) - (
        _num(hold.get("expected_final_r")) or 0.0)
    adv_cvar = (_num(selected_metrics.get("cvar10_r")) or 0.0) - (
        _num(hold.get("cvar10_r")) or 0.0)
    vs_exit = (_num(selected_metrics.get("expected_final_r")) or 0.0) - (
        _num(exit_.get("expected_final_r")) or 0.0)
    lines.extend([
        "", "**ПОЧЕМУ ВЫБРАНО** —",
        f"Относительно HOLD: Expected {adv_mean:+.3f}R; CVaR10 {adv_cvar:+.3f}R. "
        f"Относительно EXIT: Expected {vs_exit:+.3f}R. "
        f"Ограничение CVaR10: не ниже {_fmt_r(rule.get('cvar_floor_r'))}; "
        f"устойчивость {stability.get('selected_count', 0)}/{stability.get('checks', 0)} "
        f"({ _fmt_pct(stability.get('selected_share')) })."
    ])
    raw = recommendation.get("raw_optimizer_policy")
    reasons = recommendation.get("gate_downgrade_reasons") or []
    if raw and raw != selected:
        lines.append(f"Сырой оптимизатор выбрал {raw}, но контроль подтверждений понизил действие до {selected}: "
                     + "; ".join(reasons) + ".")

    lines.extend(["", "**ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ** —"])
    adverse = evidence.get("adverse_confirmations") or []
    supportive = evidence.get("supportive_contradictions") or []
    lines.append("Против удержания: " + ("; ".join(_evidence_line(x) for x in adverse[:6])
                                          if adverse else "нет независимых подтверждений" ) + ".")
    lines.append("В пользу удержания: " + ("; ".join(_evidence_line(x) for x in supportive[:6])
                                           if supportive else "нет независимых подтверждений" ) + ".")
    flags = evidence.get("uncertainty_flags") or []
    if flags:
        lines.append("Неопределённость: " + "; ".join(_evidence_line(x) for x in flags) + ".")

    attribution = manager.get("counterfactual_attribution") or {}
    lines.extend(["", "**РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ** —"])
    if attribution.get("available"):
        parts = [f"{x['component']} {x['delta_expected_r']:+.3f}R"
                 for x in attribution.get("sequential_contributions") or []]
        lines.append(f"Expected HOLD изменился на {attribution.get('total_change_r', 0):+.3f}R: "
                     + "; ".join(parts) + ".")
    else:
        lines.append("Предыдущего снимка policy inputs ещё нет; разложение появится со следующего разбора.")
    dynamic = manager.get("metric_changes") or {}
    if dynamic.get("available"):
        top = dynamic.get("changes") or []
        lines.append("Изменения остальных наблюдаемых метрик: " + "; ".join(
            f"{x['metric']} {x['previous']:+.4f}→{x['current']:+.4f} ({x['delta']:+.4f})"
            for x in top[:10]) + ".")

    remaining = recommendation.get("remaining_fraction")
    lines.extend([
        "", "**ПОСЛЕ ИСПОЛНЕНИЯ** —",
        f"Оставить {float(remaining or 0) * 100:.0f}% текущего остатка. "
        f"{recommendation.get('remaining_management')}. "
        f"Следующий рубеж: {_fmt_r(recommendation.get('next_rung_r'))}."
    ])

    cancel = manager.get("cancellation_boundary") or {}
    lines.extend(["", "**ГРАНИЦА ОТМЕНЫ** —"])
    if cancel.get("available") and cancel.get("hold_switch"):
        sw = cancel["hold_switch"]
        price = sw.get("price")
        price_text = f", цена {price:.4f}" if _num(price) is not None else ""
        lines.append(f"До исполнения действие отменяется при r={sw.get('r'):+.3f}R{price_text}: "
                     f"на этом пересчёте выбран HOLD; barrier EV={sw.get('barrier_ev_r'):+.3f}R.")
    else:
        lines.append(cancel.get("reason") or "На проверенной сетке r переход к HOLD не найден.")

    r0 = _num(inputs.get("r0")) or 0.0
    trade = snapshot.get("observation", {}).get("exact_levels") or {}
    trade_dir = snapshot.get("strategy", {}).get("direction")
    pseudo_trade = {**trade, "direction": trade_dir}
    lower_price = _price_from_r(pseudo_trade, r0 - 0.15)
    upper_price = _price_from_r(pseudo_trade, r0 + 0.15)
    lines.extend([
        "", "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —",
        f"Новая опционная цепочка; движение цены на ±0.15R "
        f"({lower_price:.4f} / {upper_price:.4f})" if lower_price is not None and upper_price is not None
        else "Новая опционная цепочка; движение цены на ±0.15R",
    ])
    lines[-1] += "; касание следующего рубежа, стопа или рассчитанной границы отмены."

    age = _num(inputs.get("chain_age_sec"))
    lines.extend([
        "", "**КАЧЕСТВО ДАННЫХ** —",
        f"Цепочка: {inputs.get('chain_status')}; возраст "
        f"{age / 60:.1f} мин" if age is not None else f"Цепочка: {inputs.get('chain_status')}; возраст неизвестен",
    ])
    lines[-1] += (f"; proxy={inputs.get('proxy_quality')}; доступно "
                  f"{coverage.get('available_groups', 0)}/{coverage.get('total_groups', 0)} групп метрик "
                  f"({_fmt_pct(coverage.get('coverage_ratio'))}).")
    return "\n".join(lines)


def _validate_model_report(content: str, snapshot: dict) -> list[str]:
    manager = snapshot.get("policy_manager") or {}
    action = _at(manager, "recommendation", "action_ru")
    required = ("ДЕЙСТВИЕ", "РАСЧЁТ ПОЛИТИК", "ПОЧЕМУ ВЫБРАНО",
                "ПОДТВЕРЖДЕНИЯ И ПРОТИВОРЕЧИЯ", "РАЗЛОЖЕНИЕ ИЗМЕНЕНИЯ",
                "ПОСЛЕ ИСПОЛНЕНИЯ", "ГРАНИЦА ОТМЕНЫ", "СЛЕДУЮЩИЙ ПЕРЕСЧЁТ",
                "КАЧЕСТВО ДАННЫХ")
    violations = [f"нет раздела {x}" for x in required if x not in content.upper()]
    if action and action not in content.upper():
        violations.append("изменено рассчитанное действие")
    policies = manager.get("policies") or {}
    for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"):
        if name not in content:
            violations.append(f"нет политики {name}")
        metrics = policies.get(name) or {}
        for field in ("expected_final_r", "cvar10_r"):
            value = _num(metrics.get(field))
            if value is not None and f"{value:+.3f}" not in content:
                violations.append(f"изменено или пропущено {name}.{field}")
    vague = ("выше потенциальная прибыль", "слишком раннее действие",
             "лучший risk-adjusted", "держать без добавления")
    violations.extend(f"размытая формулировка: {x}" for x in vague if x in content.lower())
    return violations


def request_verdict(snapshot: dict) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY не настроен на сервере")
    model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    deterministic = render_policy_report(snapshot) if snapshot.get("policy_manager") else None
    body = {
        "model": model, "temperature": 0.0, "max_tokens": 1100,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Количественный снимок сделки:\n"
             + json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
             + ("\n\nАвторитетный черновик, числа и действие не менять:\n" + deterministic
                if deterministic else "")},
        ],
    }
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    try:
        with httpx.Client(proxy=proxy, timeout=45, trust_env=False) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions", json=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "Accept": "application/json", "User-Agent": "Seiltanzer-Terminal/1.0",
                         "HTTP-Referer": "https://seiltanzer-terminal.local",
                         "X-Title": "Seiltanzer Terminal"})
            resp.raise_for_status()
            try:
                result = resp.json()
            except (TypeError, ValueError) as exc:
                raise RuntimeError("provider_bad_response: malformed JSON") from exc
    except httpx.HTTPStatusError as exc:
        # Status is sufficient for normalization. Provider response bodies may
        # echo request metadata and must not enter user-facing/logged errors.
        raise RuntimeError(f"OpenRouter HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter connection failed: {type(exc).__name__}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        if deterministic:
            return {"verdict": deterministic, "model": "deterministic-policy-fallback",
                    "captured_ts": snapshot.get("captured_ts")}
        raise RuntimeError("OpenRouter вернул пустой ответ")
    if deterministic and _validate_model_report(content, snapshot):
        content = deterministic
        used_model = "deterministic-policy-fallback"
    else:
        used_model = result.get("model", model)
    return {"verdict": content.strip(), "model": used_model,
            "captured_ts": snapshot.get("captured_ts")}
