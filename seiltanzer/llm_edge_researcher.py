"""Research-only LLM hypothesis proposer for the causal Edge Discovery Engine.

The LLM suggests which already-registered causal features to test together. It
never fits thresholds, sees future outcomes, writes Active Edge candidates, or
changes Position Manager/CVaR/stops/size. Numeric thresholds remain train-only
EDE work via ``train_relative`` conditions.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import Any, Callable

import httpx

from .edge_discovery import ProspectiveFeatureAdapter
from .edge_discovery.prospective import HORIZONS, PROSPECTIVE_ADAPTER_VERSION
from .edge_discovery.registry import EDE_CONTRACT_VERSION
from .edge_discovery.research_policy import feature_research_policy, interaction_feature_pairs
from .edge_discovery.universal_templates import universal_feature_definitions
from .g1_short_horizon_p2e_segmented_persistence import ASSET_FAMILY_BY_INSTRUMENT, session_utc

CONTRACT_VERSION = "llm-edge-researcher-v1"
PROMPT_VERSION = "llm-edge-hypothesis-proposal-v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_TIMEOUT_SEC = 10.0
MAX_OUTPUT_TOKENS = 2048
MAX_HYPOTHESES = 8
MAX_CONDITIONS = 2

TARGET_FAMILY_BY_ID = {
    "DIRECTION": "DIRECTION",
    "RETURN_SIGMA": "RETURN",
    "MFE_SIGMA": "MFE",
    "MAE_SIGMA": "MAE",
    "FORWARD_VOL_RATIO": "FORWARD_VOLATILITY",
    "FIRST_TOUCH:up_0p5s_down_0p5s": "FIRST_TOUCH",
    "FIRST_TOUCH:up_1s_down_0p5s": "FIRST_TOUCH",
    "FIRST_TOUCH:up_0p5s_down_1s": "FIRST_TOUCH",
    "FIRST_TOUCH:up_1s_down_1s": "FIRST_TOUCH",
}
ALLOWED_TARGET_IDS = tuple(TARGET_FAMILY_BY_ID)

SYSTEM_PROMPT = """Ты research-only генератор проверяемых гипотез для Edge Discovery Engine.
Тебе передан только причинный T0 snapshot без будущих исходов. Предлагай 1-2 условия,
используя ТОЛЬКО allowed feature IDs, condition kinds/states и allowed feature pairs.
Числовые пороги не придумывай: numeric condition всегда train_relative +
ABOVE_MEDIAN/BELOW_MEDIAN, а порог позже фитится детерминированно только на train-cut.
Категориальное значение бери только из allowed_states переданного feature.
Не давай BUY/SELL/HOLD/CLOSE, не меняй Position Manager, CVaR, stop, size или risk.
Не утверждай, что edge доказан. Не добавляй production authority/eligibility/status.
Верни только JSON-объект {\"hypotheses\":[...]}.
Каждая hypothesis содержит ровно: name, target_id, conditions, rationale.
Каждое condition содержит ровно: feature_id, kind, state.
Не более requested_max_hypotheses гипотез."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    raw = value if isinstance(value, str) else _canonical(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _model() -> str:
    return (
        os.environ.get("EDGE_RESEARCHER_MODEL", "").strip()
        or os.environ.get("DATA_FACTORY_MODEL", "").strip()
        or os.environ.get("OPENROUTER_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def _provider(summary: dict[str, Any], model: str, max_hypotheses: int) -> dict[str, Any]:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY_NOT_CONFIGURED")
    body = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "CAUSAL T0 RESEARCH INPUT:\n" + _canonical({
                **summary, "requested_max_hypotheses": int(max_hypotheses)
            })},
        ],
    }
    proxy = os.environ.get("OPENROUTER_PROXY", "").strip() or None
    try:
        timeout = float(os.environ.get("EDGE_RESEARCHER_TIMEOUT_SEC", "") or DEFAULT_TIMEOUT_SEC)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SEC
    timeout = max(3.0, min(20.0, timeout))
    try:
        with httpx.Client(proxy=proxy, timeout=timeout, trust_env=False) as client:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions", json=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "Seiltanzer-Edge-Researcher/1.0",
                    "HTTP-Referer": "https://seiltanzer-terminal.local",
                    "X-Title": "Seiltanzer LLM Edge Researcher",
                },
            )
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"PROVIDER_HTTP_{exc.response.status_code}") from exc
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise RuntimeError(f"PROVIDER_ERROR_{type(exc).__name__}") from exc
    content = result.get("choices", [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("PROVIDER_EMPTY_RESPONSE")
    return _extract_provider_json(content)


def _resilient_extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    if start >= 0:
        sub = raw[start:]
        last_brace = sub.rfind("}")
        while last_brace > 0:
            candidate = sub[:last_brace + 1].strip()
            if candidate.endswith(","):
                candidate = candidate[:-1].strip()
            for suffix in ("", "]}", "}"):
                try:
                    data = json.loads(candidate + suffix)
                    if isinstance(data, dict) and "hypotheses" in data:
                        return data
                except json.JSONDecodeError:
                    continue
            last_brace = sub.rfind("}", 0, last_brace)

    raise RuntimeError("PROVIDER_INVALID_JSON")


def _extract_provider_json(content: str) -> dict[str, Any]:
    payload = _resilient_extract_json(content)
    if not isinstance(payload, dict):
        raise RuntimeError("PROVIDER_INVALID_JSON_OBJECT")
    return payload


def _ensure_tables(runtime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_edge_research_runs(
                run_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                cache_key TEXT NOT NULL UNIQUE,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                max_hypotheses INTEGER NOT NULL,
                provider_response_json TEXT NOT NULL,
                hypothesis_ids_json TEXT NOT NULL,
                rejections_json TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_edge_hypotheses(
                hypothesis_id TEXT PRIMARY KEY,
                first_run_id TEXT NOT NULL,
                first_observation_id TEXT NOT NULL,
                first_snapshot_sha256 TEXT NOT NULL,
                name TEXT NOT NULL,
                target_id TEXT NOT NULL,
                target_family TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                conditions_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                evaluation_state TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_llm_edge_runs_observation "
            "ON llm_edge_research_runs(observation_id,created_ts)")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_llm_edge_hypotheses_target "
            "ON llm_edge_hypotheses(target_id,horizon_minutes,created_ts)")
        for table in ("llm_edge_research_runs", "llm_edge_hypotheses"):
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable llm edge research row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable llm edge research row'); END""")


def _load_observation(runtime, observation_id: str | None) -> dict[str, Any] | None:
    with runtime._lock:
        if observation_id:
            row = runtime._conn.execute(
                "SELECT * FROM g1s_observations WHERE observation_id=? LIMIT 1",
                (str(observation_id),)).fetchone()
        else:
            row = runtime._conn.execute(
                "SELECT * FROM g1s_observations "
                "WHERE horizon_minutes IN (15,30,60,120,240) "
                "ORDER BY captured_ts DESC,observation_id DESC LIMIT 1").fetchone()
    return None if row is None else dict(row)


def _snapshot(runtime, observation_id: str | None) -> dict[str, Any]:
    row = _load_observation(runtime, observation_id)
    if row is None:
        raise ValueError("OBSERVATION_NOT_FOUND")
    t0 = _finite(row.get("captured_ts"))
    horizon = int(row.get("horizon_minutes") or 0)
    if t0 is None or horizon not in HORIZONS:
        raise ValueError("INVALID_T0_OR_HORIZON")

    adapter = ProspectiveFeatureAdapter(runtime, available_asof=t0)
    values, rejected, provenance = adapter._feature_values(row, strict=False)
    definitions = {item.feature_id: item for item in universal_feature_definitions()}
    features: dict[str, dict[str, Any]] = {}
    for feature_id, record in values.items():
        definition = definitions.get(feature_id)
        if (definition is None or definition.research_scope != "G1S"
                or not definition.training_eligibility
                or record.availability != "AVAILABLE" or record.stale
                or not record.training_eligible or record.value is None
                or record.asof is None or float(record.asof) > t0 + 1e-6):
            continue
        features[feature_id] = {
            "feature_id": feature_id,
            "family": definition.family,
            "datatype": definition.datatype,
            "value": record.value,
            "asof": float(record.asof),
            "quality": record.quality,
            "provenance": (provenance.get(feature_id) or {}).get("provenance", "FROZEN_T0"),
        }

    rv15, rv60 = features.get("vol.rv_15m"), features.get("vol.rv_60m")
    if rv15 and rv60:
        left, right = _finite(rv15.get("value")), _finite(rv60.get("value"))
        if left is not None and right is not None and right > 0.0:
            definition = definitions["vol.rv15_over_rv60"]
            qualities = [q for q in (_finite(rv15.get("quality")),
                                     _finite(rv60.get("quality"))) if q is not None]
            features["vol.rv15_over_rv60"] = {
                "feature_id": "vol.rv15_over_rv60",
                "family": definition.family,
                "datatype": definition.datatype,
                "value": left / right,
                "asof": max(float(rv15["asof"]), float(rv60["asof"])),
                "quality": min(qualities) if qualities else None,
                "provenance": "CAUSAL_DERIVED_FROM_T0_FEATURES",
            }

    instrument = str(row.get("instrument") or "")
    for feature_id, value in {
        "regime.asset": instrument,
        "regime.asset_family": ASSET_FAMILY_BY_INSTRUMENT.get(instrument, "UNKNOWN"),
        "regime.session_utc": session_utc(t0),
    }.items():
        definition = definitions.get(feature_id)
        if definition is not None and value not in {None, ""}:
            features[feature_id] = {
                "feature_id": feature_id,
                "family": definition.family,
                "datatype": definition.datatype,
                "value": value,
                "asof": t0,
                "quality": 1.0,
                "provenance": "CAUSAL_T0_METADATA",
            }
    if not features:
        raise ValueError("NO_ELIGIBLE_T0_FEATURES")

    eligible_ids = tuple(sorted(features))
    allowed_pairs: set[tuple[str, str]] = set()
    for activation in ("CURRENT_SELECTIVE", "UNIVERSAL_OUTCOMES"):
        for left, right, _policy in interaction_feature_pairs(
                definitions.values(), eligible_feature_ids=eligible_ids,
                activation=activation):
            allowed_pairs.add(tuple(sorted((left, right))))

    feature_contract = []
    for feature_id in eligible_ids:
        item = dict(features[feature_id])
        if item["datatype"] in {"float", "int", "number"}:
            item["allowed_kind"] = "train_relative"
            item["allowed_states"] = ["ABOVE_MEDIAN", "BELOW_MEDIAN"]
        else:
            item["allowed_kind"] = "categorical"
            item["allowed_states"] = [str(item["value"])]
        feature_contract.append(item)

    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "ede_contract_version": EDE_CONTRACT_VERSION,
        "prospective_adapter_version": PROSPECTIVE_ADAPTER_VERSION,
        "observation_id": str(row["observation_id"]),
        "instrument": instrument,
        "captured_ts": t0,
        "horizon_minutes": horizon,
        "features": feature_contract,
        "allowed_feature_pairs": [list(pair) for pair in sorted(allowed_pairs)],
        "allowed_target_ids": list(ALLOWED_TARGET_IDS),
        "rejected_feature_ids": sorted(set(rejected)),
        "contains_future_outcomes": False,
        "contains_position_manager_state": False,
        "research_only": True,
        "production_authority": False,
    }
    snapshot["snapshot_sha256"] = _sha(snapshot)
    return snapshot


def _validate_hypothesis(raw: Any, snapshot: dict[str, Any], *, index: int
                         ) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"{index}:HYPOTHESIS_NOT_OBJECT"
    allowed_fields = {"name", "target_id", "conditions", "rationale"}
    if set(raw) - allowed_fields:
        return None, f"{index}:UNKNOWN_HYPOTHESIS_FIELDS"
    if set(raw) != allowed_fields:
        return None, f"{index}:MISSING_HYPOTHESIS_FIELDS"
    name = str(raw.get("name") or "").strip()
    rationale = str(raw.get("rationale") or "").strip()
    target_id = str(raw.get("target_id") or "").strip()
    if not name or len(name) > 120:
        return None, f"{index}:INVALID_NAME"
    if not rationale or len(rationale) > 600:
        return None, f"{index}:INVALID_RATIONALE"
    target_family = TARGET_FAMILY_BY_ID.get(target_id)
    if target_family is None:
        return None, f"{index}:UNKNOWN_TARGET"
    raw_conditions = raw.get("conditions")
    if not isinstance(raw_conditions, list) or not (1 <= len(raw_conditions) <= MAX_CONDITIONS):
        return None, f"{index}:INVALID_CONDITION_COUNT"

    by_feature = {item["feature_id"]: item for item in snapshot["features"]}
    definitions = {item.feature_id: item for item in universal_feature_definitions()}
    normalized, seen = [], set()
    horizon = int(snapshot["horizon_minutes"])
    for condition_index, raw_condition in enumerate(raw_conditions):
        if not isinstance(raw_condition, dict):
            return None, f"{index}.{condition_index}:CONDITION_NOT_OBJECT"
        if set(raw_condition) != {"feature_id", "kind", "state"}:
            return None, f"{index}.{condition_index}:INVALID_CONDITION_FIELDS"
        feature_id = str(raw_condition.get("feature_id") or "")
        kind = str(raw_condition.get("kind") or "")
        state = str(raw_condition.get("state") or "")
        feature, definition = by_feature.get(feature_id), definitions.get(feature_id)
        if feature is None or definition is None:
            return None, f"{index}.{condition_index}:FEATURE_NOT_AVAILABLE_AT_T0"
        if feature_id in seen:
            return None, f"{index}.{condition_index}:DUPLICATE_FEATURE"
        seen.add(feature_id)
        policy = feature_research_policy(definition)
        if target_family not in policy.allowed_targets or horizon not in policy.allowed_horizons:
            return None, f"{index}.{condition_index}:FEATURE_POLICY_REJECTED"
        if feature["datatype"] in {"float", "int", "number"}:
            if kind != "train_relative" or state not in {"ABOVE_MEDIAN", "BELOW_MEDIAN"}:
                return None, f"{index}.{condition_index}:INVALID_NUMERIC_CONDITION"
        elif kind != "categorical" or state != str(feature["value"]):
            return None, f"{index}.{condition_index}:INVALID_CATEGORICAL_CONDITION"
        normalized.append({"feature_id": feature_id, "kind": kind, "state": state})

    normalized.sort(key=lambda item: (item["feature_id"], item["kind"], item["state"]))
    if len(normalized) == 2:
        pair = tuple(sorted(item["feature_id"] for item in normalized))
        if pair not in {tuple(item) for item in snapshot["allowed_feature_pairs"]}:
            return None, f"{index}:INTERACTION_POLICY_REJECTED"
    identity = {"target_id": target_id, "horizon_minutes": horizon,
                "conditions": normalized}
    hypothesis_id = "llm-edge-hypothesis-" + _sha(identity)[:24]
    return {
        "hypothesis_id": hypothesis_id,
        "name": name,
        "target_id": target_id,
        "target_family": target_family,
        "horizon_minutes": horizon,
        "conditions": normalized,
        "rationale": rationale,
        "source": "LLM_EDGE_RESEARCHER",
        "status": "PROPOSED_SHADOW",
        "evaluation_state": "PENDING_DETERMINISTIC_EVALUATION",
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
        "may_change_position_manager": False,
        "may_change_cvar_stop_or_size": False,
    }, None


def _load_hypothesis_rows(runtime, hypothesis_ids: list[str]) -> list[dict[str, Any]]:
    output = []
    with runtime._lock:
        for hypothesis_id in hypothesis_ids:
            row = runtime._conn.execute(
                "SELECT * FROM llm_edge_hypotheses WHERE hypothesis_id=? LIMIT 1",
                (str(hypothesis_id),)).fetchone()
            if row is None:
                continue
            value = dict(row)
            output.append({
                "hypothesis_id": str(value["hypothesis_id"]),
                "name": str(value["name"]),
                "target_id": str(value["target_id"]),
                "target_family": str(value["target_family"]),
                "horizon_minutes": int(value["horizon_minutes"]),
                "conditions": json.loads(str(value["conditions_json"])),
                "rationale": str(value["rationale"]),
                "source": str(value["source"]),
                "status": str(value["status"]),
                "evaluation_state": str(value["evaluation_state"]),
                "research_only": True,
                "production_authority": False,
                "eligible_for_policy": False,
                "auto_promotion": False,
                "may_change_position_manager": False,
                "may_change_cvar_stop_or_size": False,
                "first_observation_id": str(value["first_observation_id"]),
                "first_snapshot_sha256": str(value["first_snapshot_sha256"]),
            })
    return output


def _cached_run(runtime, cache_key: str) -> dict[str, Any] | None:
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT * FROM llm_edge_research_runs WHERE cache_key=? LIMIT 1",
            (cache_key,)).fetchone()
    if row is None:
        return None
    value = dict(row)
    ids = json.loads(str(value["hypothesis_ids_json"]))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "OK" if ids else "NO_VALID_HYPOTHESES",
        "run_id": str(value["run_id"]),
        "observation_id": str(value["observation_id"]),
        "snapshot_sha256": str(value["snapshot_sha256"]),
        "model": str(value["model"]),
        "prompt_version": str(value["prompt_version"]),
        "hypotheses": _load_hypothesis_rows(runtime, ids),
        "rejections": json.loads(str(value["rejections_json"])),
        "cache_hit": True,
        "provider_called": False,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
    }


def propose_edge_hypotheses(runtime, observation_id: str | None = None, *,
                            max_hypotheses: int = 5,
                            provider: Callable[[dict[str, Any], str, int], dict[str, Any]] | None = None
                            ) -> dict[str, Any]:
    _ensure_tables(runtime)
    limit = max(1, min(int(max_hypotheses), MAX_HYPOTHESES))
    try:
        snapshot = _snapshot(runtime, observation_id)
    except (ValueError, RuntimeError) as exc:
        return {"contract_version": CONTRACT_VERSION, "status": "UNAVAILABLE",
                "reason": str(exc)[:180], "research_only": True,
                "production_authority": False, "eligible_for_policy": False}
    model = _model()
    cache_key = _sha({"snapshot_sha256": snapshot["snapshot_sha256"],
                      "prompt_version": PROMPT_VERSION, "model": model,
                      "max_hypotheses": limit})
    cached = _cached_run(runtime, cache_key)
    if cached is not None:
        return cached
    provider_input = {key: snapshot[key] for key in (
        "contract_version", "ede_contract_version", "prospective_adapter_version",
        "observation_id", "instrument", "captured_ts", "horizon_minutes",
        "features", "allowed_feature_pairs", "allowed_target_ids",
        "contains_future_outcomes", "contains_position_manager_state",
        "research_only", "production_authority")}
    try:
        response = (provider or _provider)(provider_input, model, limit)
    except (RuntimeError, ValueError) as exc:
        return {"contract_version": CONTRACT_VERSION, "status": "UNAVAILABLE",
                "reason": str(exc)[:180], "observation_id": snapshot["observation_id"],
                "snapshot_sha256": snapshot["snapshot_sha256"], "cache_hit": False,
                "provider_called": True, "research_only": True,
                "production_authority": False, "eligible_for_policy": False}
    if not isinstance(response, dict) or set(response) != {"hypotheses"}:
        return {"contract_version": CONTRACT_VERSION, "status": "UNAVAILABLE",
                "reason": "INVALID_PROVIDER_CONTRACT",
                "observation_id": snapshot["observation_id"],
                "snapshot_sha256": snapshot["snapshot_sha256"], "cache_hit": False,
                "provider_called": True, "research_only": True,
                "production_authority": False, "eligible_for_policy": False}
    raw_hypotheses = response.get("hypotheses")
    raw_hypotheses = raw_hypotheses[:limit] if isinstance(raw_hypotheses, list) else []
    valid, rejections, seen = [], [], set()
    for index, raw in enumerate(raw_hypotheses):
        hypothesis, rejection = _validate_hypothesis(raw, snapshot, index=index)
        if rejection:
            rejections.append(rejection)
            continue
        assert hypothesis is not None
        if hypothesis["hypothesis_id"] in seen:
            rejections.append(f"{index}:DUPLICATE_HYPOTHESIS")
            continue
        seen.add(hypothesis["hypothesis_id"])
        valid.append(hypothesis)

    created_ts = time.time()
    run_id = "llm-edge-run-" + cache_key[:24]
    with runtime._lock, runtime._conn:
        for hypothesis in valid:
            runtime._conn.execute("""
                INSERT OR IGNORE INTO llm_edge_hypotheses(
                    hypothesis_id,first_run_id,first_observation_id,first_snapshot_sha256,
                    name,target_id,target_family,horizon_minutes,conditions_json,rationale,
                    source,status,evaluation_state,created_ts
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    hypothesis["hypothesis_id"], run_id, snapshot["observation_id"],
                    snapshot["snapshot_sha256"], hypothesis["name"],
                    hypothesis["target_id"], hypothesis["target_family"],
                    int(hypothesis["horizon_minutes"]), _canonical(hypothesis["conditions"]),
                    hypothesis["rationale"], hypothesis["source"], hypothesis["status"],
                    hypothesis["evaluation_state"], created_ts))
        runtime._conn.execute("""
            INSERT INTO llm_edge_research_runs(
                run_id,observation_id,snapshot_sha256,cache_key,model,prompt_version,
                max_hypotheses,provider_response_json,hypothesis_ids_json,rejections_json,
                created_ts
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                run_id, snapshot["observation_id"], snapshot["snapshot_sha256"], cache_key,
                model, PROMPT_VERSION, limit, _canonical(response),
                _canonical([item["hypothesis_id"] for item in valid]),
                _canonical(rejections), created_ts))
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "OK" if valid else "NO_VALID_HYPOTHESES",
        "run_id": run_id,
        "observation_id": snapshot["observation_id"],
        "snapshot_sha256": snapshot["snapshot_sha256"],
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "hypotheses": _load_hypothesis_rows(runtime, [item["hypothesis_id"] for item in valid]),
        "rejections": rejections,
        "cache_hit": False,
        "provider_called": True,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
        "next_step": "DETERMINISTIC_EDE_EVALUATION_REQUIRED",
    }


def edge_researcher_status(runtime) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        run_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_research_runs").fetchone()[0])
        hypothesis_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM llm_edge_hypotheses").fetchone()[0])
        latest = runtime._conn.execute(
            "SELECT run_id,observation_id,snapshot_sha256,model,prompt_version,created_ts "
            "FROM llm_edge_research_runs ORDER BY created_ts DESC LIMIT 1").fetchone()
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "OK",
        "run_n": run_n,
        "hypothesis_n": hypothesis_n,
        "latest_run": None if latest is None else dict(latest),
        "prompt_version": PROMPT_VERSION,
        "max_conditions": MAX_CONDITIONS,
        "max_hypotheses_per_run": MAX_HYPOTHESES,
        "numeric_thresholds_fit_by_llm": False,
        "future_outcomes_visible_to_llm": False,
        "writes_active_edge_registry": False,
        "research_only": True,
        "production_authority": False,
        "eligible_for_policy": False,
        "auto_promotion": False,
    }
