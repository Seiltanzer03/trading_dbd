"""Phase G.1E Intelligence Cockpit aggregation.

This is a presentation/research layer only.  It reuses G.1A/B/B.1/C authoritative
calculations and never recomputes production probabilities in the browser.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import time
from collections import Counter
from typing import Any

from . import g1_baseline_runtime as _g1b


G1E_STAGE = "G.1E"
INTELLIGENCE_CONTRACT_VERSION = "g1-intelligence-cockpit-v1"
INTELLIGENCE_SNAPSHOT_VERSION = "g1-intelligence-snapshot-v1"
INTELLIGENCE_SNAPSHOT_INTERVAL_SEC = 6 * 60 * 60


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value)) if value is not None else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


HUMAN_EXPLANATIONS = {
    "INSUFFICIENT_RAW_N": "Нужно больше завершённых чистых Q-наблюдений.",
    "INSUFFICIENT_EFFECTIVE_N": (
        "Прогнозов уже может быть много, но они слишком зависимы друг от друга. "
        "Нужно больше независимых рыночных ситуаций."
    ),
    "INSUFFICIENT_POSITIVE_EVENTS": "Пока слишком мало завершённых положительных исходов.",
    "INSUFFICIENT_NEGATIVE_EVENTS": "Пока слишком мало завершённых отрицательных исходов.",
    "INSUFFICIENT_Q_VARIATION": "Нужно больше разных уровней рыночной вероятности Q.",
    "MARKET_CLOSED": "Рынок был закрыт — Q-снимок в этот момент не создавался.",
    "TARGET_PRICE_NON_DIRECT": "Не было прямой пригодной котировки торгуемого инструмента.",
    "NO_Q_SOURCE_CONFIGURED": "Для инструмента пока не настроен пригодный опционный источник Q.",
    "UNRESOLVED": "Будущий горизонт ещё не завершился.",
    "TERMINAL_NOT_CLEAN": "Будущий результат не прошёл строгую проверку качества.",
    "WRONG_MEASUREMENT_RUNTIME": "Наблюдение относится к старому measurement-контракту.",
    "WRONG_SOURCE_SCHEMA": "Наблюдение относится к старой схеме данных.",
    "EVIDENCE_INELIGIBLE": "Исходный снимок не имел достаточного качества для evidence dataset.",
}


class IntelligenceRuntime:
    def __init__(self, engine, *, storage=None):
        self.engine = engine
        self.passive = engine.passive
        self.storage = storage
        self._ensure_tables()
        self._background_running = False

    # -------------------------------------------------------------- storage

    def _ensure_tables(self) -> None:
        with self.passive._lock, self.passive._conn:
            self.passive._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1e_intelligence_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    captured_ts REAL NOT NULL,
                    bucket_ts REAL NOT NULL UNIQUE,
                    contract_version TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    created_ts REAL NOT NULL
                )""")
            self.passive._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS g1e_snapshot_immutable_update
                BEFORE UPDATE ON g1e_intelligence_snapshots
                BEGIN SELECT RAISE(ABORT,'immutable G1E intelligence snapshot'); END""")
            self.passive._conn.execute("""
                CREATE TRIGGER IF NOT EXISTS g1e_snapshot_immutable_delete
                BEFORE DELETE ON g1e_intelligence_snapshots
                BEGIN SELECT RAISE(ABORT,'immutable G1E intelligence snapshot'); END""")

    # ----------------------------------------------------------- basic state

    def _sources(self) -> tuple[dict, dict, dict, dict, dict]:
        dataset = self.passive.g1_dataset_status()
        exclusions = self.passive.g1_dataset_exclusions()
        baseline = self.passive.g1_baseline_status()
        q = self.passive.g1_q_status()
        g1c = self.passive.g1c_status()
        return dataset, exclusions, baseline, q, g1c

    @staticmethod
    def _maturity_state(q: dict, g1c: dict) -> tuple[str, str]:
        resolved = int(q.get("resolved_q_observation_n") or 0)
        eligible = int(q.get("q_to_p_eligible_n") or 0)
        effective = int(q.get("effective_q_n") or 0)
        models = int(g1c.get("frozen_model_n") or 0)
        if resolved == 0:
            return "COLLECTING", "Система собирает опыт и ждёт завершения первых Q-прогнозов."
        if models == 0:
            return "EARLY", "Первые результаты уже есть, но данных ещё недостаточно для shadow-моделей."
        if not bool(g1c.get("ready_for_g1d")):
            return "PROVISIONAL", "Shadow-модели уже существуют, но их ещё рано считать доказанными на будущем рынке."
        if eligible > 0 and effective > 0:
            return "SUPPORTED", "Накоплено достаточно prospective evidence для следующего строгого OOS-этапа."
        return "COLLECTING", "Система продолжает накапливать чистые независимые наблюдения."

    @staticmethod
    def _readiness_item(item: dict | None) -> dict:
        item = item or {}
        required = item.get("required") or {}
        observed = item.get("observed") or {}
        deficits = {}
        for key, req in required.items():
            try:
                deficits[key] = max(0, int(req) - int(observed.get(key, 0)))
            except (TypeError, ValueError):
                continue
        blockers = list(item.get("blockers") or [])
        return {
            "status": item.get("status") or "INSUFFICIENT_EVIDENCE",
            "ready": bool(item.get("ready")),
            "required": required,
            "observed": observed,
            "deficits": deficits,
            "blockers": blockers,
            "explanations": [HUMAN_EXPLANATIONS.get(code, code) for code in blockers],
        }

    def status(self) -> dict:
        dataset, exclusions, baseline, q, g1c = self._sources()
        maturity, headline = self._maturity_state(q, g1c)
        fit = g1c.get("fit_readiness") or {}
        storage_status = self.storage.status(engine=self.engine) if self.storage else None
        return {
            "g1_stage": G1E_STAGE,
            "intelligence_contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "maturity_state": maturity,
            "headline": headline,
            "experience": {
                "forecast_eval_n": int(dataset.get("forecast_eval_eligible_n") or 0),
                "forecast_effective_n": int(dataset.get("effective_n") or 0),
                "q_attempts": int(q.get("capture_attempt_n") or 0),
                "q_captured": int(q.get("successful_q_capture_n") or 0),
                "q_resolved": int(q.get("resolved_q_observation_n") or 0),
                "q_clean_eligible": int(q.get("q_to_p_eligible_n") or 0),
                "q_effective_n": int(q.get("effective_q_n") or 0),
            },
            "models": {
                "platt": self._readiness_item(fit.get("platt")),
                "beta": self._readiness_item(fit.get("beta")),
                "isotonic": self._readiness_item(fit.get("isotonic")),
                "frozen_model_n": int(g1c.get("frozen_model_n") or 0),
                "prospective_prediction_n": int(g1c.get("prospective_shadow_prediction_n") or 0),
            },
            "evidence": {
                "dataset_status": dataset.get("evidence_status") or "INSUFFICIENT",
                "baseline_status": baseline.get("evidence_status") or "INSUFFICIENT",
                "q_status": q.get("evidence_status") or "INSUFFICIENT",
                "ready_for_g1d": bool(g1c.get("ready_for_g1d")),
                "g1d": g1c.get("g1d_readiness") or {},
            },
            "data_quality": {
                "excluded_n": int(exclusions.get("total_excluded_n") or 0),
                "primary_reasons": exclusions.get("primary_reason_counts") or {},
                "top_q_blockers": q.get("top_blockers") or {},
            },
            "storage": storage_status,
            "authority": {
                "research_only": True,
                "production_authority": False,
                "production_replacement_allowed": False,
                "promotion_allowed": False,
                "physical_probability_published": False,
                "shadow_p_used_for_trading": False,
            },
        }

    # ---------------------------------------------------------- data funnel

    def pipeline(self) -> dict:
        dataset, exclusions, _baseline, q, _g1c = self._sources()
        instruments = self.passive.g1_q_instruments()
        blockers = self.passive.g1_q_blockers()
        return {
            "contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "funnel": [
                {"name": "ATTEMPTS", "n": int(q.get("capture_attempt_n") or 0)},
                {"name": "CAPTURED", "n": int(q.get("successful_q_capture_n") or 0)},
                {"name": "RESOLVED", "n": int(q.get("resolved_q_observation_n") or 0)},
                {"name": "Q→P ELIGIBLE", "n": int(q.get("q_to_p_eligible_n") or 0)},
                {"name": "EFFECTIVE Q N", "n": int(q.get("effective_q_n") or 0)},
            ],
            "instruments": instruments,
            "q_blockers": blockers,
            "dataset_exclusions": exclusions,
            "forecast_eval_eligible_n": int(dataset.get("forecast_eval_eligible_n") or 0),
            "explanations": {
                code: HUMAN_EXPLANATIONS.get(code, code)
                for code in set((q.get("top_blockers") or {}).keys())
                | set((exclusions.get("primary_reason_counts") or {}).keys())
            },
        }

    # ------------------------------------------------------ forecast quality

    def forecast_quality(self) -> dict:
        baseline = self.passive.g1_baseline_status()
        cohorts = self.passive.g1_baseline_cohorts()
        return {
            "contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "status": baseline,
            "cohorts": cohorts,
            "presentation_note": (
                "Все числа рассчитаны authoritative G.1B backend; cockpit не пересчитывает Brier/PIT в браузере."
            ),
        }

    # ---------------------------------------------------------- calibration

    def calibration(self) -> dict:
        g1c = self.passive.g1c_status()
        return {
            "contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "status": g1c,
            "models": self.passive.g1c_models(limit=200),
            "cohorts": self.passive.g1c_cohorts(),
            "predictions": self.passive.g1c_predictions(limit=100),
            "research_only": True,
            "production_used": False,
        }

    # --------------------------------------------------------- pending/recent

    def _q_observation_rows(self, *, pending: bool, limit: int = 50) -> list[dict]:
        clause = "p.resolution_status='pending'" if pending else "p.resolution_status='resolved'"
        sql = f"""
            SELECT p.observation_id,p.captured_ts,p.target_ts,p.instrument,
                   p.resolution_status,p.forecast_json,p.outcome_json,
                   p.price_source,p.option_source,p.market_regime,p.session,
                   q.relation,q.proxy_transform,q.q_source_instrument
            FROM passive_market_observations p
            JOIN g1_q_capture_attempts q
              ON q.created_observation_id=p.observation_id
            WHERE q.observation_created=1 AND {clause}
            ORDER BY p.captured_ts DESC
            LIMIT ?
        """
        with self.passive._lock:
            rows = self.passive._conn.execute(sql, (max(1, min(int(limit), 500)),)).fetchall()
        out = []
        now = time.time()
        for row in rows:
            item = dict(row)
            forecast = _loads(item.pop("forecast_json", None), {})
            outcome = _loads(item.pop("outcome_json", None), {})
            cdf0 = _g1b._cdf_value(forecast.get("terminal_q_cdf"), 0.0)
            raw_q = None if cdf0 is None else max(0.0, min(1.0, 1.0 - cdf0))
            terminal = outcome.get("terminal") if isinstance(outcome, dict) else {}
            out.append({
                **item,
                "raw_q_up": raw_q,
                "expiry_ts": item.get("target_ts"),
                "time_remaining_sec": max(0.0, float(item["target_ts"]) - now) if pending else 0.0,
                "future_log_return": outcome.get("future_log_return") if isinstance(outcome, dict) else None,
                "terminal_pit_q": terminal.get("terminal_pit_q") if isinstance(terminal, dict) else None,
                "terminal_clean": terminal.get("clean_label") if isinstance(terminal, dict) else None,
                "forecast_contract": forecast.get("measurement_runtime_contract"),
                "probability_measure": forecast.get("probability_measure"),
            })
        return out

    def pending(self, limit: int = 50) -> dict:
        return {
            "contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "items": self._q_observation_rows(pending=True, limit=limit),
        }

    def resolved(self, limit: int = 50) -> dict:
        return {
            "contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "items": self._q_observation_rows(pending=False, limit=limit),
        }

    # ------------------------------------------------------------ history

    def snapshot_if_due(self, *, force: bool = False) -> bool:
        self._ensure_tables()
        now = time.time()
        bucket = math.floor(now / INTELLIGENCE_SNAPSHOT_INTERVAL_SEC) * INTELLIGENCE_SNAPSHOT_INTERVAL_SEC
        with self.passive._lock:
            exists = self.passive._conn.execute(
                "SELECT 1 FROM g1e_intelligence_snapshots WHERE bucket_ts=?", (float(bucket),)
            ).fetchone()
        if exists is not None and not force:
            return False
        status = self.status()
        compact = {
            "maturity_state": status["maturity_state"],
            "experience": status["experience"],
            "models": {
                "frozen_model_n": status["models"]["frozen_model_n"],
                "prospective_prediction_n": status["models"]["prospective_prediction_n"],
            },
            "evidence": status["evidence"],
            "storage_health": (status.get("storage") or {}).get("health"),
        }
        payload_sha = _sha(compact)
        snapshot_id = "g1e-" + _sha({"bucket": bucket, "payload": payload_sha})[:28]
        with self.passive._lock, self.passive._conn:
            self.passive._conn.execute(
                "INSERT OR IGNORE INTO g1e_intelligence_snapshots("
                "snapshot_id,captured_ts,bucket_ts,contract_version,snapshot_json,snapshot_sha256,created_ts)"
                " VALUES(?,?,?,?,?,?,?)",
                (snapshot_id, now, float(bucket), INTELLIGENCE_SNAPSHOT_VERSION,
                 _json(compact), payload_sha, time.time()),
            )
        return True

    def history(self, limit: int = 200) -> dict:
        with self.passive._lock:
            rows = self.passive._conn.execute(
                "SELECT * FROM g1e_intelligence_snapshots ORDER BY bucket_ts DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        items = []
        for row in reversed(rows):
            item = dict(row)
            item["snapshot"] = _loads(item.pop("snapshot_json"), {})
            items.append(item)
        return {
            "contract_version": INTELLIGENCE_CONTRACT_VERSION,
            "snapshot_contract_version": INTELLIGENCE_SNAPSHOT_VERSION,
            "items": items,
        }

    async def background_loop(self) -> None:
        self._background_running = True
        try:
            while True:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(self.snapshot_if_due)
                await asyncio.sleep(300.0)
        finally:
            self._background_running = False


def install_intelligence_runtime(app) -> IntelligenceRuntime:
    if getattr(app.state, "g1_intelligence_runtime_installed", False):
        return app.state.intelligence
    runtime = IntelligenceRuntime(
        app.state.engine,
        storage=getattr(app.state, "storage", None),
    )
    app.state.intelligence = runtime
    app.state.g1_intelligence_runtime_installed = True
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def intelligence_lifespan(inner_app):
        task = None
        async with original_lifespan(inner_app):
            runtime.snapshot_if_due()
            task = asyncio.create_task(runtime.background_loop())
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(runtime.snapshot_if_due, force=True)

    app.router.lifespan_context = intelligence_lifespan
    return runtime
