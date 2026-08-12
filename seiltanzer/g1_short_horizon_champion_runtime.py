"""Frozen champion/challenger validation for G.1S.

The existing G.1S fitters intentionally keep producing research challengers as
new resolved evidence arrives.  Serious live OOS evidence, however, must not
restart every time a challenger is fitted.  This module freezes one champion per
(target, horizon, feature-set, model-family), keeps that exact artifact scoring
future eligible T0 rows, and records an immutable validation-cohort link for each
prospective prediction.

Nothing here promotes a model or changes production decision authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from typing import Any

import numpy as np

from . import g1_short_horizon_integration as _integration
from . import storage_runtime as _storage
from .g1_short_horizon_evidence_completion import SERIOUS_OOS_REQUIRED
from .g1_short_horizon_gbt_refinement import MODEL_FAMILY as GBT_MODEL_FAMILY, _predict_gbt
from .g1_short_horizon_runtime import ShortHorizonRuntime, _loads, _sigmoid


CHAMPION_CONTRACT_VERSION = "g1s-frozen-champion-v1"
CHAMPION_LINK_VERSION = "g1s-champion-prediction-link-v1"
CHAMPION_PROGRESS_VERSION = "g1s-champion-progress-v1"
DIRECTION_TARGET = "direction_up"
RETURN_TARGET = "terminal_log_return"
CHAMPION_TABLES = (
    "g1s_validation_cohorts",
    "g1s_champion_prediction_links",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_validation_cohorts(
                validation_cohort_id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                feature_set TEXT NOT NULL,
                model_family TEXT NOT NULL,
                champion_model_id TEXT NOT NULL,
                frozen_at REAL NOT NULL,
                training_cutoff_ts REAL NOT NULL,
                oos_start_ts REAL NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                auto_promotion INTEGER NOT NULL DEFAULT 0,
                production_authority INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                UNIQUE(target,horizon_minutes,feature_set,model_family)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_validation_cohort_key "
            "ON g1s_validation_cohorts(target,horizon_minutes,feature_set,model_family)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_champion_prediction_links(
                link_id TEXT PRIMARY KEY,
                validation_cohort_id TEXT NOT NULL,
                target TEXT NOT NULL,
                prediction_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL,
                prediction_created_ts REAL NOT NULL,
                contract_version TEXT NOT NULL,
                created_ts REAL NOT NULL,
                UNIQUE(validation_cohort_id,observation_id)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_champion_link_cohort_capture "
            "ON g1s_champion_prediction_links(validation_cohort_id,captured_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_champion_progress(
                validation_cohort_id TEXT PRIMARY KEY,
                contract_version TEXT NOT NULL,
                oos_raw_n INTEGER NOT NULL DEFAULT 0,
                oos_effective_n INTEGER NOT NULL DEFAULT 0,
                positive_n INTEGER NOT NULL DEFAULT 0,
                negative_n INTEGER NOT NULL DEFAULT 0,
                temporal_blocks INTEGER NOT NULL DEFAULT 0,
                regimes INTEGER NOT NULL DEFAULT 0,
                linked_prediction_n INTEGER NOT NULL DEFAULT 0,
                latest_prediction_ts REAL,
                latest_resolved_ts REAL,
                evidence_maturity TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_ts REAL NOT NULL
            )""")
        for table in CHAMPION_TABLES:
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S champion evidence row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S champion evidence row'); END""")


def _model_rows(runtime: ShortHorizonRuntime, target: str) -> list[dict[str, Any]]:
    table = "g1s_models" if target == DIRECTION_TARGET else "g1s_return_models"
    try:
        with runtime._lock:
            rows = runtime._conn.execute(
                f"SELECT model_id,model_family,horizon_minutes,feature_set,"
                f"training_cutoff_ts,created_ts FROM {table} "
                "WHERE authority='research_only' ORDER BY created_ts,model_id"
            ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _bootstrap_champions(runtime: ShortHorizonRuntime, *, frozen_at: float | None = None) -> int:
    """Freeze only missing keys; existing champions are never auto-replaced."""
    _ensure_tables(runtime)
    freeze = float(frozen_at if frozen_at is not None else time.time())
    created = 0
    for target in (DIRECTION_TARGET, RETURN_TARGET):
        grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
        for model in _model_rows(runtime, target):
            created_ts = _finite(model.get("created_ts"))
            cutoff = _finite(model.get("training_cutoff_ts"))
            if created_ts is None or cutoff is None:
                continue
            if created_ts > freeze + 1e-6 or cutoff >= freeze - 1e-9:
                continue
            key = (int(model["horizon_minutes"]), str(model["feature_set"]),
                   str(model["model_family"]))
            grouped[key].append(model)
        for (horizon, feature_set, family), models in grouped.items():
            # Latest artifact known at freeze is a deterministic bootstrap.  Its
            # future OOS starts *now*; no historical rows are relabelled as live OOS.
            champion = max(models, key=lambda item: (float(item["created_ts"]), str(item["model_id"])))
            model_id = str(champion["model_id"])
            cutoff = float(champion["training_cutoff_ts"])
            oos_start = max(freeze, cutoff + 1e-6)
            key_raw = f"{target}|{horizon}|{feature_set}|{family}|{model_id}|{freeze:.6f}"
            cohort_id = "g1s-cohort-" + _sha(key_raw)[:28]
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_validation_cohorts("
                    "validation_cohort_id,target,horizon_minutes,feature_set,model_family,"
                    "champion_model_id,frozen_at,training_cutoff_ts,oos_start_ts,source,status,"
                    "auto_promotion,production_authority,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'LIVE_PROSPECTIVE_BOOTSTRAP','LIVE_VALIDATING',0,0,?)",
                    (cohort_id, target, horizon, feature_set, family, model_id,
                     freeze, cutoff, oos_start, time.time()),
                )
                if cur.rowcount > 0:
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_champion_progress("
                        "validation_cohort_id,contract_version,evidence_maturity,status,updated_ts) "
                        "VALUES(?,?,'INSUFFICIENT','LIVE_VALIDATING',?)",
                        (cohort_id, CHAMPION_PROGRESS_VERSION, time.time()),
                    )
                    created += 1
    return created


def _cohorts_for_observation(runtime: ShortHorizonRuntime, horizon: int,
                             captured_ts: float) -> list[dict[str, Any]]:
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT * FROM g1s_validation_cohorts
            WHERE horizon_minutes=? AND oos_start_ts<=?
            ORDER BY target,feature_set,model_family
        """, (int(horizon), float(captured_ts))).fetchall()
    return [dict(row) for row in rows]


def _linear_vector(runtime: ShortHorizonRuntime, obs: dict[str, Any],
                   model: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    vector, _ = runtime._feature_vector(obs, str(model["feature_set"]))
    params = _loads(model["parameters_json"], {})
    x = np.asarray(vector, dtype=float)
    mean = np.asarray(params.get("feature_mean") or [], dtype=float)
    std = np.asarray(params.get("feature_std") or [], dtype=float)
    beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
    if len(mean) != len(x) or len(std) != len(x) or len(beta) != len(x)+1:
        raise ValueError("model artifact shape mismatch")
    z = (x-mean)/np.where(std < 1e-12, 1.0, std)
    return z, params


def _score_direction(runtime: ShortHorizonRuntime, obs: dict[str, Any],
                     model: dict[str, Any]) -> float:
    family = str(model["model_family"])
    params = _loads(model["parameters_json"], {})
    if family == GBT_MODEL_FAMILY:
        vector, _ = runtime._feature_vector(obs, str(model["feature_set"]))
        return float(_predict_gbt(np.asarray([vector], dtype=float), params)[0])
    z, params = _linear_vector(runtime, obs, model)
    beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
    return float(_sigmoid(np.asarray([beta[0] + z@beta[1:]]))[0])


def _score_return(runtime: ShortHorizonRuntime, obs: dict[str, Any],
                  model: dict[str, Any]) -> float:
    z, params = _linear_vector(runtime, obs, model)
    beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
    return float(beta[0] + z@beta[1:])


def _prediction_table(target: str) -> tuple[str, str]:
    if target == DIRECTION_TARGET:
        return "g1s_shadow_predictions", "g1s_models"
    return "g1s_return_predictions", "g1s_return_models"


def _existing_prediction(runtime: ShortHorizonRuntime, target: str,
                         observation_id: str, model_id: str):
    prediction_table, _ = _prediction_table(target)
    with runtime._lock:
        return runtime._conn.execute(
            f"SELECT prediction_id,created_ts FROM {prediction_table} "
            "WHERE observation_id=? AND model_id=? LIMIT 1",
            (str(observation_id), str(model_id)),
        ).fetchone()


def _write_missing_prediction(runtime: ShortHorizonRuntime, cohort: dict[str, Any],
                              obs: dict[str, Any]) -> tuple[str, float] | None:
    target = str(cohort["target"])
    _prediction_table_name, model_table = _prediction_table(target)
    with runtime._lock:
        model_row = runtime._conn.execute(
            f"SELECT * FROM {model_table} WHERE model_id=?",
            (str(cohort["champion_model_id"]),),
        ).fetchone()
    if model_row is None:
        return None
    model = dict(model_row)
    captured = float(obs["captured_ts"])
    target_ts = float(obs["target_ts"])
    if float(model["created_ts"]) > captured + 1e-6:
        return None
    if float(model["training_cutoff_ts"]) >= captured - 1e-9:
        return None
    prediction_ts = time.time()
    if prediction_ts >= target_ts - 1e-9:
        # Late backlog materialization is not prospective evidence.
        return None
    try:
        if target == DIRECTION_TARGET:
            p_up = _score_direction(runtime, obs, model)
            payload = {
                "contract_version": CHAMPION_CONTRACT_VERSION,
                "validation_cohort_id": str(cohort["validation_cohort_id"]),
                "champion_prediction": True,
                "observation_id": str(obs["observation_id"]),
                "model_id": str(model["model_id"]),
                "model_family": str(model["model_family"]),
                "feature_set": str(model["feature_set"]),
                "model_created_ts": float(model["created_ts"]),
                "training_cutoff_ts": float(model["training_cutoff_ts"]),
                "captured_ts": captured,
                "target_ts": target_ts,
                "p_up": p_up,
                "research_only": True,
                "production_used": False,
                "auto_promotion": False,
            }
            raw = _json(payload)
            pred_id = "g1s-champ-pred-" + _sha(raw)[:27]
            with runtime._lock, runtime._conn:
                runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_shadow_predictions("
                    "prediction_id,observation_id,model_id,created_ts,p_up,prediction_json,"
                    "prediction_sha256,production_used) VALUES(?,?,?,?,?,?,?,0)",
                    (pred_id, str(obs["observation_id"]), str(model["model_id"]),
                     prediction_ts, p_up, raw, _sha(raw)),
                )
        else:
            predicted = _score_return(runtime, obs, model)
            payload = {
                "contract_version": CHAMPION_CONTRACT_VERSION,
                "validation_cohort_id": str(cohort["validation_cohort_id"]),
                "champion_prediction": True,
                "observation_id": str(obs["observation_id"]),
                "model_id": str(model["model_id"]),
                "model_family": str(model["model_family"]),
                "feature_set": str(model["feature_set"]),
                "model_created_ts": float(model["created_ts"]),
                "training_cutoff_ts": float(model["training_cutoff_ts"]),
                "captured_ts": captured,
                "target_ts": target_ts,
                "predicted_log_return": predicted,
                "target": RETURN_TARGET,
                "research_only": True,
                "production_used": False,
                "auto_promotion": False,
            }
            raw = _json(payload)
            pred_id = "g1s-champ-ret-" + _sha(raw)[:28]
            with runtime._lock, runtime._conn:
                runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_return_predictions("
                    "prediction_id,observation_id,model_id,predicted_log_return,prediction_json,"
                    "prediction_sha256,production_used,created_ts) VALUES(?,?,?,?,?,?,0,?)",
                    (pred_id, str(obs["observation_id"]), str(model["model_id"]),
                     predicted, raw, _sha(raw), prediction_ts),
                )
    except Exception as exc:
        try:
            runtime._error("CHAMPION_PREDICTION_UNAVAILABLE", f"{model['model_id']}: {exc}",
                           observation_id=str(obs["observation_id"]), critical=False)
        except Exception:
            pass
        return None
    existing = _existing_prediction(runtime, target, str(obs["observation_id"]),
                                    str(model["model_id"]))
    if existing is None:
        return None
    return str(existing["prediction_id"]), float(existing["created_ts"])


def _link_prediction(runtime: ShortHorizonRuntime, cohort: dict[str, Any],
                     obs: dict[str, Any], prediction_id: str,
                     prediction_created_ts: float) -> int:
    captured = float(obs["captured_ts"]); target_ts = float(obs["target_ts"])
    if captured < float(cohort["oos_start_ts"])-1e-9:
        return 0
    if prediction_created_ts >= target_ts-1e-9:
        return 0
    payload = {
        "contract_version": CHAMPION_LINK_VERSION,
        "validation_cohort_id": str(cohort["validation_cohort_id"]),
        "target": str(cohort["target"]),
        "prediction_id": str(prediction_id),
        "observation_id": str(obs["observation_id"]),
        "model_id": str(cohort["champion_model_id"]),
        "captured_ts": captured,
        "target_ts": target_ts,
        "prediction_created_ts": float(prediction_created_ts),
        "prediction_precedes_target": True,
        "champion_training_cutoff_precedes_oos_start": (
            float(cohort["training_cutoff_ts"]) < float(cohort["oos_start_ts"])
        ),
    }
    raw = _json(payload)
    link_id = "g1s-champ-link-" + _sha(raw)[:27]
    with runtime._lock, runtime._conn:
        cur = runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_champion_prediction_links("
            "link_id,validation_cohort_id,target,prediction_id,observation_id,model_id,"
            "captured_ts,target_ts,prediction_created_ts,contract_version,created_ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (link_id, str(cohort["validation_cohort_id"]), str(cohort["target"]),
             str(prediction_id), str(obs["observation_id"]),
             str(cohort["champion_model_id"]), captured, target_ts,
             float(prediction_created_ts), CHAMPION_LINK_VERSION, time.time()),
        )
    return int(cur.rowcount > 0)


def _write_champion_predictions(runtime: ShortHorizonRuntime, observation_id: str,
                                captured_ts: float, horizon: int) -> int:
    _ensure_tables(runtime)
    with runtime._lock:
        obs_row = runtime._conn.execute(
            "SELECT * FROM g1s_observations WHERE observation_id=?",
            (str(observation_id),),
        ).fetchone()
    if obs_row is None:
        return 0
    obs = dict(obs_row)
    if int(obs.get("oos_eligible") or 0) != 1:
        return 0
    if abs(float(obs["captured_ts"])-float(captured_ts)) > 1e-6:
        return 0
    written = 0
    for cohort in _cohorts_for_observation(runtime, int(horizon), float(captured_ts)):
        model_id = str(cohort["champion_model_id"])
        existing = _existing_prediction(runtime, str(cohort["target"]),
                                        str(observation_id), model_id)
        if existing is None:
            result = _write_missing_prediction(runtime, cohort, obs)
            if result is None:
                continue
            prediction_id, prediction_created_ts = result
        else:
            prediction_id = str(existing["prediction_id"])
            prediction_created_ts = float(existing["created_ts"])
        written += _link_prediction(runtime, cohort, obs, prediction_id,
                                    prediction_created_ts)
    return written


def _refresh_progress(runtime: ShortHorizonRuntime) -> None:
    _ensure_tables(runtime)
    with runtime._lock:
        cohorts = [dict(row) for row in runtime._conn.execute(
            "SELECT * FROM g1s_validation_cohorts ORDER BY created_ts,validation_cohort_id"
        ).fetchall()]
    for cohort in cohorts:
        target = str(cohort["target"])
        if target == DIRECTION_TARGET:
            sql = """
                SELECT l.observation_id,l.captured_ts,l.prediction_created_ts,
                       g.horizon_minutes,g.instrument,g.market_regime,
                       r.direction_label,r.resolved_ts
                FROM g1s_champion_prediction_links l
                JOIN g1s_observations g USING(observation_id)
                JOIN g1s_resolutions r USING(observation_id)
                WHERE l.validation_cohort_id=? AND l.target=?
                  AND r.direction_label!='FLAT' AND g.oos_eligible=1
                  AND l.prediction_created_ts<g.target_ts
                ORDER BY l.captured_ts,l.observation_id
            """
        else:
            sql = """
                SELECT l.observation_id,l.captured_ts,l.prediction_created_ts,
                       g.horizon_minutes,g.instrument,g.market_regime,
                       r.terminal_log_return,r.resolved_ts
                FROM g1s_champion_prediction_links l
                JOIN g1s_observations g USING(observation_id)
                JOIN g1s_resolutions r USING(observation_id)
                WHERE l.validation_cohort_id=? AND l.target=?
                  AND r.terminal_log_return IS NOT NULL AND g.oos_eligible=1
                  AND l.prediction_created_ts<g.target_ts
                ORDER BY l.captured_ts,l.observation_id
            """
        with runtime._lock:
            rows = [dict(row) for row in runtime._conn.execute(
                sql, (str(cohort["validation_cohort_id"]), target)
            ).fetchall()]
            link_meta = runtime._conn.execute(
                "SELECT COUNT(*) n,MAX(prediction_created_ts) latest FROM "
                "g1s_champion_prediction_links WHERE validation_cohort_id=?",
                (str(cohort["validation_cohort_id"]),),
            ).fetchone()
        groups = {runtime._dependency_key(row) for row in rows}
        if target == DIRECTION_TARGET:
            positive = sum(str(row.get("direction_label")) == "UP" for row in rows)
            negative = sum(str(row.get("direction_label")) == "DOWN" for row in rows)
        else:
            positive = sum(float(row.get("terminal_log_return") or 0.0) > 0 for row in rows)
            negative = sum(float(row.get("terminal_log_return") or 0.0) < 0 for row in rows)
        blocks = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"])))
                      for row in rows})
        regimes = len({str(row.get("market_regime") or "UNKNOWN") for row in rows})
        observed = {
            "raw_resolved": len(rows),
            "effective_n": len(groups),
            "positive_n": int(positive),
            "negative_n": int(negative),
            "temporal_blocks": int(blocks),
        }
        blockers = [key for key, required in SERIOUS_OOS_REQUIRED.items()
                    if int(observed.get(key, 0)) < int(required)]
        if regimes < 2:
            blockers.append("volatility_regime_count")
        maturity = "SERIOUS_SAMPLE_GATE_MET" if not blockers else "INSUFFICIENT"
        latest_resolved = max((_finite(row.get("resolved_ts")) or 0.0 for row in rows), default=0.0)
        with runtime._lock, runtime._conn:
            runtime._conn.execute("""
                INSERT INTO g1s_champion_progress(
                    validation_cohort_id,contract_version,oos_raw_n,oos_effective_n,positive_n,
                    negative_n,temporal_blocks,regimes,linked_prediction_n,latest_prediction_ts,
                    latest_resolved_ts,evidence_maturity,status,updated_ts)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(validation_cohort_id) DO UPDATE SET
                    contract_version=excluded.contract_version,
                    oos_raw_n=excluded.oos_raw_n,oos_effective_n=excluded.oos_effective_n,
                    positive_n=excluded.positive_n,negative_n=excluded.negative_n,
                    temporal_blocks=excluded.temporal_blocks,regimes=excluded.regimes,
                    linked_prediction_n=excluded.linked_prediction_n,
                    latest_prediction_ts=excluded.latest_prediction_ts,
                    latest_resolved_ts=excluded.latest_resolved_ts,
                    evidence_maturity=excluded.evidence_maturity,status=excluded.status,
                    updated_ts=excluded.updated_ts
            """, (
                str(cohort["validation_cohort_id"]), CHAMPION_PROGRESS_VERSION,
                len(rows), len(groups), int(positive), int(negative), int(blocks), int(regimes),
                int(link_meta["n"] or 0), link_meta["latest"],
                (latest_resolved or None), maturity, "LIVE_VALIDATING", time.time(),
            ))


def _latest_challenger(runtime: ShortHorizonRuntime, cohort: dict[str, Any]) -> dict[str, Any] | None:
    target = str(cohort["target"])
    _prediction_table_name, model_table = _prediction_table(target)
    with runtime._lock:
        row = runtime._conn.execute(
            f"SELECT model_id,created_ts,training_cutoff_ts FROM {model_table} "
            "WHERE horizon_minutes=? AND feature_set=? AND model_family=? AND model_id!=? "
            "ORDER BY created_ts DESC,model_id DESC LIMIT 1",
            (int(cohort["horizon_minutes"]), str(cohort["feature_set"]),
             str(cohort["model_family"]), str(cohort["champion_model_id"])),
        ).fetchone()
    return dict(row) if row is not None else None


def _champion_status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT c.*,p.oos_raw_n,p.oos_effective_n,p.positive_n,p.negative_n,
                   p.temporal_blocks,p.regimes,p.linked_prediction_n,p.latest_prediction_ts,
                   p.latest_resolved_ts,p.evidence_maturity,p.updated_ts
            FROM g1s_validation_cohorts c
            LEFT JOIN g1s_champion_progress p USING(validation_cohort_id)
            ORDER BY c.target,c.horizon_minutes,c.feature_set,c.model_family
        """).fetchall()
    items = []
    for source in rows:
        item = dict(source)
        challenger = _latest_challenger(runtime, item)
        item["latest_challenger_model_id"] = challenger.get("model_id") if challenger else None
        item["latest_challenger_created_ts"] = challenger.get("created_ts") if challenger else None
        item["champion_is_frozen"] = True
        item["challenger_does_not_replace_champion"] = True
        item["champion_training_excludes_live_oos"] = (
            float(item["training_cutoff_ts"]) < float(item["oos_start_ts"])
        )
        item["auto_promotion"] = False
        item["production_authority"] = False
        items.append(item)
    return {
        "contract_version": CHAMPION_CONTRACT_VERSION,
        "validation_label": "LIVE_PROSPECTIVE_OOS",
        "serious_oos_required": {**SERIOUS_OOS_REQUIRED, "volatility_regime_count": 2},
        "items": items,
        "champion_count": len(items),
        "champion_frozen": True,
        "challenger_training_allowed": True,
        "challenger_can_stop_champion_stream": False,
        "champion_can_train_on_own_oos": False,
        "prediction_must_precede_target": True,
        "auto_promotion": False,
        "production_authority": False,
    }


def install_g1_short_horizon_champion_runtime() -> None:
    if getattr(ShortHorizonRuntime, "_champion_runtime_version", None) == CHAMPION_CONTRACT_VERSION:
        return
    previous_init = ShortHorizonRuntime.__init__
    previous_fit = ShortHorizonRuntime.fit_if_ready
    previous_predict = ShortHorizonRuntime._create_prospective_predictions
    previous_resolve = ShortHorizonRuntime.resolve_new
    previous_status = ShortHorizonRuntime.status

    def runtime_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _ensure_tables(self)
        _bootstrap_champions(self)
        _refresh_progress(self)

    def fit_if_ready(self, *, force: bool = False):
        created = int(previous_fit(self, force=force) or 0)
        # New model keys get a champion once; existing keys remain frozen even
        # when this call creates a newer challenger.
        _bootstrap_champions(self)
        return created

    def create_predictions(self, observation_id: str, captured_ts: float, horizon: int):
        created = int(previous_predict(self, observation_id, captured_ts, horizon) or 0)
        created += _write_champion_predictions(self, observation_id, captured_ts, horizon)
        return created

    def resolve_new(self, limit: int = 2000):
        resolved = int(previous_resolve(self, limit=limit) or 0)
        if resolved:
            _refresh_progress(self)
        return resolved

    def status(self):
        report = previous_status(self)
        report["champion_validation"] = _champion_status(self)
        return report

    ShortHorizonRuntime.__init__ = runtime_init
    ShortHorizonRuntime.fit_if_ready = fit_if_ready
    ShortHorizonRuntime._create_prospective_predictions = create_predictions
    ShortHorizonRuntime.resolve_new = resolve_new
    ShortHorizonRuntime.champion_status = _champion_status
    ShortHorizonRuntime.refresh_champion_progress = _refresh_progress
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._champion_runtime_version = CHAMPION_CONTRACT_VERSION

    _storage.CRITICAL_TABLES = tuple(dict.fromkeys((*_storage.CRITICAL_TABLES, *CHAMPION_TABLES)))
    _integration.G1S_CRITICAL_TABLES = tuple(dict.fromkeys((*_integration.G1S_CRITICAL_TABLES, *CHAMPION_TABLES)))
