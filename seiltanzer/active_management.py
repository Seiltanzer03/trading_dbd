"""Independent deterministic extended-action candidates, manual execution only."""
from .llm_shadow_working_action import build_working_action
from .extended_policy_evaluation import evaluate_extended_action

POLICIES = ("MOVE_TO_BE", "TIGHTEN_STOP", "TRAIL_GAMMA_FLIP", "REDUCE_TAKE",
            "EXTEND_TAKE", "SCALE_OUT_ON_SPIKE", "TIME_STOP")


def select_active_management(snapshot):
    """Select only quantified improvements; no candidate is also a valid result."""
    rows, candidates = [], []
    armed = {x.get("policy") for x in (snapshot.get("position_state") or {}).get(
        "armed_conditional_actions", [])}
    for policy in POLICIES:
        if policy in armed:
            rows.append({"policy": policy, "status": "already_armed"})
            continue
        proposal = {"policy": policy, "status": "ok", "confidence": .65,
            "confidence_semantics": "legacy_manual_registration_threshold_not_probability",
            "source": "deterministic_active_management", "automatic_execution_allowed": False}
        proposal["working_action"] = build_working_action(snapshot, proposal)
        assessment = evaluate_extended_action(snapshot, proposal["working_action"])
        proposal["quant_evaluation"] = assessment
        rows.append({"policy": policy, "parameters": proposal["working_action"].get("parameters") or {},
            "instruction_ru": proposal["working_action"].get("instruction_ru"), **assessment})
        if assessment["status"] == "eligible":
            candidates.append(proposal)
    snapshot["active_management_candidates"] = rows
    if not candidates:
        return None
    selected = max(candidates, key=lambda p: (
        p["quant_evaluation"]["paired_delta_ci95_lower_r"],
        p["quant_evaluation"]["worst_seed_cvar10_gross_r"]))
    selected["reason_ru"] = "Максимальный нижний предел расчётного прироста Expected среди допустимых расширенных действий; HOLD остаётся базовым сравнением."
    selected["production_authority"] = False
    selected["statistically_validated_advantage"] = False
    return selected


_REASON_RU = {
    "BREAK_EVEN_IS_NOT_A_VALID_TIGHTER_STOP": "БУ уже установлен либо перенос к входу не подтягивает стоп",
    "NO_MATERIAL_ROBUST_EXPECTED_GAIN": "нижняя граница модельной пользы не превышает порог",
    "GEX_CONTEXT_NOT_A_VERIFIED_EXECUTION_ANCHOR": "gamma-уровень требует live/ok цепочки не старше 120 секунд",
    "OPTION_WALL_NOT_A_VERIFIED_EXECUTION_ANCHOR": "опционный уровень тейка требует live/ok цепочки не старше 120 секунд",
    "FARTHER_AUTHORITATIVE_OPTION_WALL_UNAVAILABLE": "нет пригодного опционного уровня дальше тейка",
    "SPIKE_TRIGGER_ALREADY_CROSSED_OR_OUTSIDE_TAKE": "импульсный уровень уже пройден либо находится за тейком",
    "NO_DISTINCT_SPIKE_TRIGGER_BEFORE_TAKE": "между новым максимумом и тейком нет отдельного импульсного уровня",
    "LOW_DATA_RELIABILITY_FOR_EXTENDED_OVERRIDE": "низкая надёжность: нет двух независимых adverse-семейств с живым подтверждением",
    "DATA_RELIABILITY_UNAVAILABLE": "надёжность отсутствует; активный допуск запрещён",
    "EXECUTION_COST_MODEL_UNAVAILABLE": "нет модели будущих издержек",
    "AUTHORITATIVE_INSTRUMENT_PRICE_UNAVAILABLE": "нет авторитетной текущей цены инструмента",
    "ROBUST_EXPECTED_GAIN_AND_CVAR_PASS": "модельная польза и hard CVaR подтверждены",
    "VARIANT_CVAR_BELOW_HARD_FLOOR": "вариант нарушает hard CVaR",
}


def render_active_management(rows: list[dict], remaining: float | None = None) -> str:
    """Publish all actual assessments, including rejected numeric candidates."""
    from datetime import datetime, timezone
    import math
    def number(value):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return "UNAVAILABLE"
        return f"{value:+.5f}R"
    lines = ["**ПРОВЕРКА АКТИВНОГО МЕНЕДЖМЕНТА** —",
        "Сравнение каждого кандидата с HOLD на одинаковых путях. Числа на единицу текущего остатка; MC-интервал описывает ошибку симуляции, не уверенность в рынке."]
    for row in rows:
        reason = row.get("reason") or "already_armed"
        status = {"blocked": "не допущено", "eligible": "допущено", "already_armed": "уже установлено"}.get(row.get("status"), "UNAVAILABLE")
        lines.append(f"{row['policy']}: {status} — {_REASON_RU.get(reason, reason)} [{reason}].")
        params = row.get("parameters") or {}
        labels = {"stop_price": "стоп", "take_price": "тейк", "trigger_price": "импульсный уровень", "timeout_minutes": "минут до выхода"}
        levels = [f"{label} {params[key]:g}" for key, label in labels.items()
                  if isinstance(params.get(key), (int, float)) and math.isfinite(params[key])]
        if isinstance(params.get("close_fraction"), (int, float)):
            levels.append(f"закрыть {100 * params['close_fraction']:g}% остатка, объём фиксируется при подтверждении")
        if isinstance(params.get("deadline_ts"), (int, float)):
            levels.append("срок " + datetime.fromtimestamp(params['deadline_ts'], timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC"))
        if levels:
            lines.append("Параметры: " + "; ".join(levels) + ".")
        if "expected_delta_vs_hold_r" in row:
            lines.append(f"Expected net: HOLD {number(row.get('expected_hold_net_r'))} → вариант {number(row.get('expected_variant_net_r'))}; Δ {number(row.get('expected_delta_vs_hold_r'))}. "
                f"Нижняя MC-граница Δ {number(row.get('paired_delta_ci95_lower_r'))}; порог {number(row.get('materiality_band_r'))}. "
                f"CVaR10 net worst seed: HOLD {number(row.get('worst_seed_hold_cvar10_net_r'))} → вариант {number(row.get('worst_seed_cvar10_net_r'))}; floor {number(row.get('hard_net_floor_r'))}. Пути: {row.get('paths')}.")
            if remaining is not None:
                lines.append(f"ΔExpected для исходного объёма сделки: {number(remaining * row['expected_delta_vs_hold_r'])}.")
            lines.append(f"Будущие издержки {number(row.get('execution_cost_r'))}; "
                + ("резервная оценка" if row.get('cost_assumed') else "явно заданная модель")
                + f"; источник {row.get('cost_source', 'UNAVAILABLE')}.")
        elif row.get("status") != "already_armed":
            lines.append("Expected/CVaR кандидата: UNAVAILABLE — остановлено до симуляции; нули не подставлены.")
    return "\n".join(lines)
