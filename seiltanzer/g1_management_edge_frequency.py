"""Prospective frequency ledger for bounded edge influence on management.

Only future frozen G.1-M reviews are recorded.  The ledger measures when active
or rolling exploratory evidence was eligible, matched the current T0, changed
the raw soft-ranking choice and survived into the gated recommendation.  It is
read-only research evidence and never changes policy or execution authority.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import Counter
from statistics import median
from typing import Any

from .g1_management_runtime import ManagementEdgeRuntime, _json, _sha_text


EDGE_FREQUENCY_VERSION = "g1m-edge-decision-frequency-v1"
WINDOWS = {"24h": 86_400.0, "7d": 604_800.0, "30d": 2_592_000.0, "all": None}
_INSTALLED = False
_EXPLORATORY_CONTEXT_BLOCKERS = {
    "G1S_RUNTIME_UNAVAILABLE",
    "MATERIALIZED_LIFECYCLE_UNAVAILABLE",
    "MATERIALIZED_LIFECYCLE_NOT_READY",
    "CURRENT_T0_FEATURES_UNAVAILABLE",
    "POSITION_DIRECTION_UNAVAILABLE",
    "CURRENT_T0_FRESHNESS_BOUNDARY_UNAVAILABLE",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _at(value: Any, *path: str, default=None):
    current = value
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _asset_family(instrument: str) -> str:
    value = instrument.upper()
    if value in {"XAU", "XAG", "XAUUSD", "XAGUSD"}:
        return "METALS"
    if value in {"BTC", "ETH", "SOL", "BTCUSD", "ETHUSD", "SOLUSD"}:
        return "CRYPTO"
    if value.endswith("USD") and value not in {"NAS100", "SP500", "US30"}:
        return "FX"
    if value:
        return "INDICES"
    return "UNKNOWN"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = min(1.0, max(0.0, q)) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _ensure_tables(runtime: ManagementEdgeRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1m_edge_frequency_activation (
                id INTEGER PRIMARY KEY CHECK(id=1),
                activation_ts REAL NOT NULL,
                contract_version TEXT NOT NULL
            )""")
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1m_edge_frequency_activation"
            "(id,activation_ts,contract_version) VALUES(1,?,?)",
            (time.time(), EDGE_FREQUENCY_VERSION),
        )
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1m_edge_decision_t0 (
                observation_id TEXT PRIMARY KEY,
                review_id TEXT NOT NULL UNIQUE,
                trade_id INTEGER NOT NULL,
                captured_ts REAL NOT NULL,
                origin TEXT NOT NULL,
                instrument TEXT NOT NULL,
                asset_family TEXT NOT NULL,
                direction TEXT NOT NULL,
                market_regime TEXT,
                eligible INTEGER NOT NULL,
                exploratory_eligible INTEGER NOT NULL,
                signal_state TEXT NOT NULL,
                matched_limited_n INTEGER NOT NULL,
                matched_advantage_n INTEGER NOT NULL,
                matched_disadvantage_n INTEGER NOT NULL,
                matched_mixed_n INTEGER NOT NULL,
                advantage_supporting_n INTEGER NOT NULL,
                advantage_opposing_n INTEGER NOT NULL,
                active_matched_n INTEGER NOT NULL,
                exploratory_weight REAL NOT NULL,
                active_weight REAL NOT NULL,
                combined_weight REAL NOT NULL,
                preferred_close_fraction REAL,
                raw_policy_without_edge TEXT,
                raw_policy_with_edge TEXT,
                recommendation_policy TEXT NOT NULL,
                management_policy TEXT NOT NULL,
                raw_choice_changed INTEGER NOT NULL,
                weighted_raw_reached_recommendation INTEGER NOT NULL,
                blocked_reason TEXT,
                context_json TEXT NOT NULL,
                context_sha256 TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1m_edge_decision_ts "
            "ON g1m_edge_decision_t0(captured_ts,eligible)")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1m_edge_decision_trade "
            "ON g1m_edge_decision_t0(trade_id,captured_ts)")
        for table in ("g1m_edge_frequency_activation", "g1m_edge_decision_t0"):
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1M edge-frequency row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1M edge-frequency row'); END""")


def _signal_state(
    *, eligible: bool, supporting: int, opposing: int,
    disadvantage: int, matched: int, direction_score: float | None,
) -> str:
    if not eligible:
        return "UNAVAILABLE"
    if supporting > 0 and opposing > 0:
        return "CONFLICTED"
    if direction_score is not None and direction_score > 1e-12:
        return "SUPPORTS_POSITION"
    if direction_score is not None and direction_score < -1e-12:
        return "OPPOSES_POSITION"
    if disadvantage > 0:
        return "EARLY_DISADVANTAGE_ONLY"
    if matched > 0:
        return "MIXED_OR_NON_DIRECTIONAL"
    return "NO_MATCH"


def current_edge_management_payload(snapshot: Any) -> dict[str, Any]:
    """Compact manual-trader explanation from the already-frozen decision."""
    root = _mapping(snapshot)
    manager = _mapping(root.get("policy_manager"))
    exploratory = _mapping(manager.get("llm_edge_exploratory_weight"))
    active = _mapping(manager.get("active_edge_provisional_weight"))
    combined = _mapping(manager.get("combined_edge_soft_weight"))
    audit = _mapping(_at(manager, "selection_rule", "combined_edge_soft_weight"))
    decision = _mapping(manager.get("management_decision"))
    recommendation = _mapping(manager.get("recommendation"))
    score = _finite(combined.get("direction_score"))
    if score is None or abs(score) <= 1e-12:
        direction = "NEUTRAL"
        direction_ru = "нет чистого перевеса к удержанию или фиксации"
    elif score > 0.0:
        direction = "SUPPORTS_HOLD"
        direction_ru = "поддерживает удержание большей части позиции"
    else:
        direction = "SUPPORTS_EARLIER_CLOSE"
        direction_ru = "поддерживает более раннюю частичную фиксацию"
    return {
        "contract_version": EDGE_FREQUENCY_VERSION,
        "available": bool(exploratory or active or combined),
        "action_now": decision.get("policy") or recommendation.get("policy") or "HOLD",
        "instruction_ru": decision.get("instruction_ru"),
        "direction": direction,
        "direction_ru": direction_ru,
        "weights": {
            "exploratory": _finite(exploratory.get("weight_fraction")) or 0.0,
            "active": _finite(active.get("weight_fraction")) or 0.0,
            "combined": _finite(combined.get("weight_fraction")) or 0.0,
            "preferred_close_fraction": _finite(combined.get("preferred_close_fraction")),
        },
        "matched_status_counts": _mapping(exploratory.get("matched_status_counts")),
        "top_signals": list(exploratory.get("matched_signals") or [])[:3],
        "counterfactual": {
            "raw_policy_without_edge": audit.get("raw_policy_without_edge"),
            "raw_policy_with_edge": audit.get("raw_policy_with_edge"),
            "raw_policy_changed": bool(audit.get("raw_policy_changed")),
            "recommendation_policy": recommendation.get("policy"),
            "scope": audit.get("counterfactual_scope"),
        },
        "blocked_reason": (
            audit.get("reason") if combined.get("available") and not audit.get("applied")
            else exploratory.get("reason") if not combined.get("available") else None
        ),
        "hard_risk_cvar_preserved": True,
        "may_widen_stop": False,
        "may_increase_position": False,
        "automatic_execution_allowed": False,
        "measurement_note_ru": (
            "Улучшение прогноза — не доходность сделки. Вес меняет только мягкое "
            "ранжирование действий, не отменяет hard-risk/CVaR."
        ),
    }


def _store_edge_decision_t0(runtime: ManagementEdgeRuntime, source: sqlite3.Row) -> bool:
    review_id = str(source["review_id"])
    captured_ts = float(source["captured_ts"])
    with runtime._lock:
        activation = runtime._conn.execute(
            "SELECT activation_ts FROM g1m_edge_frequency_activation WHERE id=1"
        ).fetchone()
        observation = runtime._conn.execute(
            "SELECT observation_id,origin,current_r,remaining_before "
            "FROM g1m_management_observations WHERE review_id=?", (review_id,),
        ).fetchone()
    if observation is None or activation is None:
        return False
    if captured_ts < float(activation["activation_ts"]) - 1e-9:
        return False

    snapshot = _loads(source["snapshot_json"])
    manager = _mapping(snapshot.get("policy_manager"))
    exploratory = _mapping(manager.get("llm_edge_exploratory_weight"))
    active = _mapping(manager.get("active_edge_provisional_weight"))
    combined = _mapping(manager.get("combined_edge_soft_weight"))
    selection = _mapping(manager.get("selection_rule"))
    audit = _mapping(selection.get("combined_edge_soft_weight"))
    status_counts = _mapping(exploratory.get("matched_status_counts"))
    active_context = _mapping(_at(snapshot, "ede_causal_context", "active_high_risk"))

    matched_limited = _integer(exploratory.get("matched_limited_hypothesis_n"))
    advantage = _integer(status_counts.get("EARLY_ADVANTAGE"))
    disadvantage = _integer(status_counts.get("EARLY_DISADVANTAGE"))
    mixed = _integer(status_counts.get("EARLY_MIXED")) + _integer(
        status_counts.get("EARLY_UNDECIDED"))
    advantage_supporting = _integer(
        exploratory.get("matched_advantage_supporting_n"))
    advantage_opposing = _integer(
        exploratory.get("matched_advantage_opposing_n"))
    active_supporting = _integer(active_context.get("supporting_position_n"))
    active_opposing = _integer(active_context.get("opposing_position_n"))
    supporting = advantage_supporting + active_supporting
    opposing = advantage_opposing + active_opposing
    active_matched = _integer(active.get("matched_directional_signal_n"))

    origin = str(observation["origin"] or "UNKNOWN")
    exploratory_reason = str(exploratory.get("reason") or "")
    exploratory_eligible = bool(
        exploratory and exploratory_reason not in _EXPLORATORY_CONTEXT_BLOCKERS)
    active_context_present = bool(active or active_context)
    eligible = bool(
        origin == "LIVE_PROSPECTIVE"
        and (exploratory_eligible or active_context_present)
    )
    direction_score = _finite(combined.get("direction_score"))
    state = _signal_state(
        eligible=eligible, supporting=supporting, opposing=opposing,
        disadvantage=disadvantage, matched=matched_limited + active_matched,
        direction_score=direction_score,
    )

    raw_without = audit.get("raw_policy_without_edge")
    raw_with = audit.get("raw_policy_with_edge")
    recommendation = str(_at(manager, "recommendation", "policy") or "HOLD")
    management = str(_at(manager, "management_decision", "policy") or recommendation)
    raw_changed = bool(audit.get("raw_policy_changed"))
    reached_recommendation = bool(raw_changed and raw_with == recommendation)
    blocked_reason = None
    if combined.get("available") and not audit.get("applied"):
        blocked_reason = str(audit.get("reason") or "SOFT_WEIGHT_NOT_APPLIED")
    elif not combined.get("available"):
        blocked_reason = exploratory_reason or str(active.get("reason") or "NO_EDGE_WEIGHT")

    instrument = str(
        _at(snapshot, "strategy", "instrument")
        or _at(snapshot, "observation", "instrument") or "UNKNOWN")
    direction = str(_at(snapshot, "strategy", "direction") or "UNKNOWN").upper()
    regime = (
        _at(snapshot, "observation", "regime", "phase")
        or _at(snapshot, "regime", "phase")
        or _at(snapshot, "market", "regime")
    )
    context = {
        "contract_version": EDGE_FREQUENCY_VERSION,
        "origin": origin,
        "eligible": eligible,
        "exploratory_eligible": exploratory_eligible,
        "signal_state": state,
        "current_r": _finite(observation["current_r"]),
        "remaining_fraction": _finite(observation["remaining_before"]),
        "matched_status_counts": status_counts,
        "matched_horizons": exploratory.get("matched_horizons") or [],
        "matched_target_families": exploratory.get("matched_target_families") or [],
        "top_matched_signals": exploratory.get("matched_signals") or [],
        "active_matched_groups": (active_context.get("matched_groups") or [])[:64],
        "weights": {
            "exploratory": _finite(exploratory.get("weight_fraction")) or 0.0,
            "active": _finite(active.get("weight_fraction")) or 0.0,
            "combined": _finite(combined.get("weight_fraction")) or 0.0,
            "preferred_close_fraction": _finite(combined.get("preferred_close_fraction")),
        },
        "counterfactual": {
            "raw_policy_without_edge": raw_without,
            "raw_policy_with_edge": raw_with,
            "raw_policy_changed": raw_changed,
            "raw_policy_transition": audit.get("raw_policy_transition"),
            "counterfactual_scope": audit.get("counterfactual_scope"),
            "recommendation_policy": recommendation,
            "management_policy": management,
            "weighted_raw_reached_recommendation": reached_recommendation,
        },
        "blocked_reason": blocked_reason,
        "hard_risk_modified": False,
        "automatic_execution_source": False,
        "production_authority": False,
        "historical_backfill": False,
    }
    raw_context = _json(context)
    with runtime._lock, runtime._conn:
        cursor = runtime._conn.execute("""
            INSERT OR IGNORE INTO g1m_edge_decision_t0(
                observation_id,review_id,trade_id,captured_ts,origin,instrument,
                asset_family,direction,market_regime,eligible,exploratory_eligible,
                signal_state,matched_limited_n,matched_advantage_n,
                matched_disadvantage_n,matched_mixed_n,advantage_supporting_n,
                advantage_opposing_n,active_matched_n,exploratory_weight,
                active_weight,combined_weight,preferred_close_fraction,
                raw_policy_without_edge,raw_policy_with_edge,recommendation_policy,
                management_policy,raw_choice_changed,
                weighted_raw_reached_recommendation,blocked_reason,context_json,
                context_sha256,created_ts)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(observation["observation_id"]), review_id, int(source["trade_id"]),
            captured_ts, origin, instrument, _asset_family(instrument), direction,
            None if regime is None else str(regime), int(eligible),
            int(exploratory_eligible), state, matched_limited, advantage,
            disadvantage, mixed, advantage_supporting, advantage_opposing,
            active_matched, _finite(exploratory.get("weight_fraction")) or 0.0,
            _finite(active.get("weight_fraction")) or 0.0,
            _finite(combined.get("weight_fraction")) or 0.0,
            _finite(combined.get("preferred_close_fraction")), raw_without, raw_with,
            recommendation, management, int(raw_changed), int(reached_recommendation),
            blocked_reason, raw_context, _sha_text(raw_context), time.time(),
        ))
    return cursor.rowcount > 0


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _dimension(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(field) or "UNKNOWN"), []).append(row)
    output = []
    for value, items in grouped.items():
        eligible = [row for row in items if bool(row["eligible"])]
        early = [row for row in eligible if int(row["matched_limited_n"]) > 0]
        changed = [row for row in eligible if bool(row["raw_choice_changed"])]
        output.append({
            field: value,
            "eligible_reviews": len(eligible),
            "unique_trades": len({int(row["trade_id"]) for row in eligible}),
            "early_match_reviews": len(early),
            "early_match_rate": _rate(len(early), len(eligible)),
            "raw_choice_changed_reviews": len(changed),
        })
    return sorted(output, key=lambda row: (-int(row["eligible_reviews"]), str(row[field])))


def _aggregate(rows: list[dict[str, Any]], *, label: str, cutoff_ts: float | None) -> dict:
    selected = [row for row in rows if cutoff_ts is None or float(row["captured_ts"]) >= cutoff_ts]
    eligible = [row for row in selected if bool(row["eligible"])]
    exploratory_eligible = [row for row in selected if bool(row["exploratory_eligible"])]
    denominator = len(exploratory_eligible)

    def early_count(predicate) -> int:
        return sum(bool(predicate(row)) for row in exploratory_eligible)

    early_any = early_count(lambda row: int(row["matched_limited_n"]) > 0)
    advantage = early_count(lambda row: int(row["matched_advantage_n"]) > 0)
    disadvantage = early_count(lambda row: int(row["matched_disadvantage_n"]) > 0)
    mixed = early_count(lambda row: int(row["matched_mixed_n"]) > 0)
    active_any = sum(int(row["active_matched_n"]) > 0 for row in eligible)
    any_edge = sum(
        int(row["matched_limited_n"]) > 0 or int(row["active_matched_n"]) > 0
        for row in eligible
    )
    supports = sum(row["signal_state"] == "SUPPORTS_POSITION" for row in eligible)
    opposes = sum(row["signal_state"] == "OPPOSES_POSITION" for row in eligible)
    conflicted = sum(row["signal_state"] == "CONFLICTED" for row in eligible)
    changed = sum(bool(row["raw_choice_changed"]) for row in eligible)
    reached = sum(bool(row["weighted_raw_reached_recommendation"]) for row in eligible)
    weights = [float(row["combined_weight"]) for row in eligible]
    nonzero_weights = [value for value in weights if value > 0.0]
    transitions = Counter(
        f"{row['raw_policy_without_edge']}->{row['raw_policy_with_edge']}"
        for row in eligible if bool(row["raw_choice_changed"])
    )
    blocked = Counter(
        str(row["blocked_reason"]) for row in selected if row.get("blocked_reason"))

    horizon_reviews: Counter[int] = Counter()
    family_reviews: Counter[str] = Counter()
    for row in exploratory_eligible:
        context = _loads(row.get("context_json"))
        for horizon in set(context.get("matched_horizons") or []):
            try:
                horizon_reviews[int(horizon)] += 1
            except (TypeError, ValueError, OverflowError):
                continue
        for family in set(context.get("matched_target_families") or []):
            family_reviews[str(family)] += 1

    unique_trades = len({int(row["trade_id"]) for row in eligible})
    eligible_n = len(eligible)
    return {
        "window": label,
        "cutoff_ts": cutoff_ts,
        "captured_reviews": len(selected),
        "eligible_reviews": len(eligible),
        "exploratory_eligible_reviews": denominator,
        "unique_trades": unique_trades,
        "dependency_weighted_effective_n": unique_trades,
        "early_any_match_reviews": early_any,
        "early_any_match_rate": _rate(early_any, denominator),
        "early_advantage_reviews": advantage,
        "early_advantage_rate": _rate(advantage, denominator),
        "early_disadvantage_reviews": disadvantage,
        "early_disadvantage_rate": _rate(disadvantage, denominator),
        "early_mixed_or_undecided_reviews": mixed,
        "early_mixed_or_undecided_rate": _rate(mixed, denominator),
        "active_any_match_reviews": active_any,
        "active_any_match_rate": _rate(active_any, eligible_n),
        "any_edge_match_reviews": any_edge,
        "any_edge_match_rate": _rate(any_edge, eligible_n),
        "supports_position_reviews": supports,
        "supports_position_rate": _rate(supports, eligible_n),
        "opposes_position_reviews": opposes,
        "opposes_position_rate": _rate(opposes, eligible_n),
        "conflicted_reviews": conflicted,
        "conflicted_rate": _rate(conflicted, eligible_n),
        "raw_choice_changed_reviews": changed,
        "raw_choice_changed_rate": _rate(changed, eligible_n),
        "weighted_raw_reached_recommendation_reviews": reached,
        "combined_weight": {
            "nonzero_reviews": len(nonzero_weights),
            "median": median(nonzero_weights) if nonzero_weights else None,
            "p90": _quantile(nonzero_weights, 0.90),
        },
        "raw_policy_transitions": dict(sorted(transitions.items())),
        "blocked_reasons": dict(blocked.most_common()),
        "by_instrument": _dimension(eligible, "instrument"),
        "by_asset_family": _dimension(eligible, "asset_family"),
        "by_direction": _dimension(eligible, "direction"),
        "by_market_regime": _dimension(eligible, "market_regime"),
        "by_signal_horizon": [
            {"horizon_minutes": horizon, "matched_reviews": n}
            for horizon, n in sorted(horizon_reviews.items())
        ],
        "by_target_family": [
            {"target_family": family, "matched_reviews": n}
            for family, n in sorted(family_reviews.items())
        ],
    }


def edge_frequency(runtime: ManagementEdgeRuntime) -> dict[str, Any]:
    with runtime._lock:
        activation = runtime._conn.execute(
            "SELECT activation_ts FROM g1m_edge_frequency_activation WHERE id=1"
        ).fetchone()
        rows = [dict(row) for row in runtime._conn.execute(
            "SELECT * FROM g1m_edge_decision_t0 ORDER BY captured_ts"
        ).fetchall()]
    now = time.time()
    return {
        "contract_version": EDGE_FREQUENCY_VERSION,
        "activation_ts": float(activation["activation_ts"]) if activation else None,
        "generated_ts": now,
        "unit_of_observation": "unique_frozen_management_review",
        "deduplication_key": "review_id",
        "dependency_contract": "one_trade_total_weight_one",
        "future_captures_only": True,
        "historical_backfill_included": False,
        "windows": {
            label: _aggregate(
                rows, label=label,
                cutoff_ts=None if seconds is None else now - seconds,
            )
            for label, seconds in WINDOWS.items()
        },
        "interpretation": {
            "EARLY_DISADVANTAGE": (
                "forecasting conditions underperformed their baseline; this is not "
                "an automatic opposite trade"
            ),
            "raw_choice_changed": (
                "the bounded edge blend changed the deterministic raw optimizer "
                "choice before confirmation/continuity gates"
            ),
            "weighted_raw_reached_recommendation": (
                "the changed weighted raw choice also became the gated recommendation"
            ),
        },
        "authority": {
            "research_only": True,
            "production_authority": False,
            "automatic_execution_allowed": False,
            "automatic_weight_change_allowed": False,
            "hard_risk_modified": False,
        },
    }


def install_g1_management_edge_frequency() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(
        ManagementEdgeRuntime, "_edge_frequency_version", None
    ) == EDGE_FREQUENCY_VERSION:
        return
    _INSTALLED = True

    original_ensure = ManagementEdgeRuntime._ensure_tables
    original_capture = ManagementEdgeRuntime._capture_observation
    original_status = ManagementEdgeRuntime.status
    original_decision = ManagementEdgeRuntime.decision

    def ensure_tables(self) -> None:
        original_ensure(self)
        _ensure_tables(self)

    def capture_observation(self, source: sqlite3.Row) -> bool:
        inserted = original_capture(self, source)
        if not inserted:
            return False
        try:
            _store_edge_decision_t0(self, source)
        except Exception as exc:
            try:
                self._error(
                    code="EDGE_FREQUENCY_T0_SIDECAR_EXCEPTION", detail=str(exc),
                    critical=False, review_id=str(source["review_id"]),
                    trade_id=int(source["trade_id"]),
                )
            except Exception:
                pass
        return True

    def status(self) -> dict:
        body = original_status(self)
        with self._lock:
            row = self._conn.execute("""
                SELECT COUNT(*) AS captured_n,
                       SUM(CASE WHEN exploratory_eligible=1 THEN 1 ELSE 0 END) AS eligible_n,
                       SUM(CASE WHEN matched_limited_n>0 THEN 1 ELSE 0 END) AS matched_n,
                       SUM(raw_choice_changed) AS changed_n
                FROM g1m_edge_decision_t0
            """).fetchone()
        body["edge_frequency_ledger"] = {
            "contract_version": EDGE_FREQUENCY_VERSION,
            "captured_n": int(row["captured_n"] or 0),
            "exploratory_eligible_n": int(row["eligible_n"] or 0),
            "early_matched_n": int(row["matched_n"] or 0),
            "raw_choice_changed_n": int(row["changed_n"] or 0),
            "future_captures_only": True,
            "production_authority": False,
        }
        return body

    def decision(self, observation_id: str) -> dict | None:
        body = original_decision(self, observation_id)
        if body is None:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM g1m_edge_decision_t0 WHERE observation_id=?",
                (observation_id,),
            ).fetchone()
        body["edge_decision_t0"] = None if row is None else {
            **dict(row), "context": _loads(row["context_json"]),
        }
        return body

    ManagementEdgeRuntime._ensure_tables = ensure_tables
    ManagementEdgeRuntime._capture_observation = capture_observation
    ManagementEdgeRuntime.status = status
    ManagementEdgeRuntime.decision = decision
    ManagementEdgeRuntime.edge_frequency = edge_frequency
    ManagementEdgeRuntime._edge_frequency_version = EDGE_FREQUENCY_VERSION
