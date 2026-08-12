from __future__ import annotations

import json
import sqlite3
import threading
import time

from seiltanzer.g1_short_horizon_champion_runtime import (
    CHAMPION_CONTRACT_VERSION,
    DIRECTION_TARGET,
    RETURN_TARGET,
    _bootstrap_champions,
    _champion_status,
    _ensure_tables,
    _refresh_progress,
    _write_champion_predictions,
)


class _Runtime:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.errors = []
        self._conn.executescript(
            """
            CREATE TABLE g1s_models(
                model_id TEXT PRIMARY KEY, model_family TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL, feature_set TEXT NOT NULL,
                training_cutoff_ts REAL NOT NULL, parameters_json TEXT NOT NULL,
                authority TEXT NOT NULL, created_ts REAL NOT NULL
            );
            CREATE TABLE g1s_return_models(
                model_id TEXT PRIMARY KEY, model_family TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL, feature_set TEXT NOT NULL,
                training_cutoff_ts REAL NOT NULL, parameters_json TEXT NOT NULL,
                authority TEXT NOT NULL, created_ts REAL NOT NULL
            );
            CREATE TABLE g1s_shadow_predictions(
                prediction_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL,
                model_id TEXT NOT NULL, created_ts REAL NOT NULL, p_up REAL NOT NULL,
                prediction_json TEXT NOT NULL, prediction_sha256 TEXT NOT NULL,
                production_used INTEGER NOT NULL DEFAULT 0,
                UNIQUE(observation_id,model_id)
            );
            CREATE TABLE g1s_return_predictions(
                prediction_id TEXT PRIMARY KEY, observation_id TEXT NOT NULL,
                model_id TEXT NOT NULL, predicted_log_return REAL NOT NULL,
                prediction_json TEXT NOT NULL, prediction_sha256 TEXT NOT NULL,
                production_used INTEGER NOT NULL DEFAULT 0, created_ts REAL NOT NULL,
                UNIQUE(observation_id,model_id)
            );
            CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY, captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL, instrument TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL, market_regime TEXT,
                oos_eligible INTEGER NOT NULL, frozen_features_json TEXT NOT NULL
            );
            CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY, direction_label TEXT,
                terminal_log_return REAL, resolved_ts REAL NOT NULL
            );
            """
        )
        _ensure_tables(self)

    def _feature_vector(self, row, feature_set):
        features = json.loads(row["frozen_features_json"])
        return [float(features["x"])], {"x": float(features["x"])}

    @staticmethod
    def _dependency_key(row):
        bucket = int(float(row["captured_ts"]) // (int(row["horizon_minutes"])*60.0))
        return f"{row['instrument']}|{row['horizon_minutes']}|{bucket}"

    def _error(self, code, detail, *, observation_id=None, critical=False):
        self.errors.append((code, detail, observation_id, critical))


def _linear_params(beta=1.0):
    return json.dumps({
        "feature_mean": [0.0], "feature_std": [1.0],
        "intercept_and_coefficients": [0.0, float(beta)],
    })


def _add_direction_model(rt, model_id, created, cutoff):
    rt._conn.execute(
        "INSERT INTO g1s_models VALUES(?,?,?,?,?,?,?,?)",
        (model_id, "REGULARIZED_LOGISTIC", 15, "TEST", cutoff,
         _linear_params(), "research_only", created),
    )
    rt._conn.commit()


def _add_return_model(rt, model_id, created, cutoff):
    rt._conn.execute(
        "INSERT INTO g1s_return_models VALUES(?,?,?,?,?,?,?,?)",
        (model_id, "DEPENDENCY_WEIGHTED_RIDGE", 15, "TEST", cutoff,
         _linear_params(0.01), "research_only", created),
    )
    rt._conn.commit()


def _add_obs(rt, observation_id, captured, target, x=0.5):
    rt._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?,?,?)",
        (observation_id, captured, target, "XAU", 15, "NORMAL", 1,
         json.dumps({"x": x})),
    )
    rt._conn.commit()


def test_frozen_champion_continues_after_newer_challenger_exists() -> None:
    rt = _Runtime()
    now = time.time()
    _add_direction_model(rt, "m1", now-300, now-400)
    assert _bootstrap_champions(rt, frozen_at=now-200) == 1

    cohort = dict(rt._conn.execute(
        "SELECT * FROM g1s_validation_cohorts WHERE target=?", (DIRECTION_TARGET,)
    ).fetchone())
    assert cohort["champion_model_id"] == "m1"
    assert cohort["training_cutoff_ts"] < cohort["oos_start_ts"]

    # Refit creates a newer challenger for the same key. Bootstrap must not
    # replace the frozen champion or restart its validation cohort.
    _add_direction_model(rt, "m2", now-100, now-150)
    assert _bootstrap_champions(rt, frozen_at=now-50) == 0
    same = dict(rt._conn.execute(
        "SELECT * FROM g1s_validation_cohorts WHERE target=?", (DIRECTION_TARGET,)
    ).fetchone())
    assert same["validation_cohort_id"] == cohort["validation_cohort_id"]
    assert same["champion_model_id"] == "m1"

    _add_obs(rt, "o1", now-20, now+600, 0.5)
    assert _write_champion_predictions(rt, "o1", now-20, 15) == 1
    p1 = rt._conn.execute(
        "SELECT * FROM g1s_shadow_predictions WHERE observation_id='o1' AND model_id='m1'"
    ).fetchone()
    assert p1 is not None

    # Same T0 may also carry a challenger prediction; it does not interrupt the
    # champion stream and the unique key is observation+model, not observation.
    rt._conn.execute(
        "INSERT INTO g1s_shadow_predictions VALUES(?,?,?,?,?,?,?,0)",
        ("challenger-o1", "o1", "m2", time.time(), 0.55, "{}", "sha-m2"),
    )
    rt._conn.commit()
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_shadow_predictions WHERE observation_id='o1'"
    ).fetchone()[0] == 2

    _add_obs(rt, "o2", now-10, now+700, -0.25)
    assert _write_champion_predictions(rt, "o2", now-10, 15) == 1
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_champion_prediction_links WHERE model_id='m1'"
    ).fetchone()[0] == 2
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_champion_prediction_links WHERE model_id='m2'"
    ).fetchone()[0] == 0

    status = _champion_status(rt)
    item = next(x for x in status["items"] if x["target"] == DIRECTION_TARGET)
    assert item["champion_model_id"] == "m1"
    assert item["latest_challenger_model_id"] == "m2"
    assert item["challenger_does_not_replace_champion"] is True
    assert item["champion_training_excludes_live_oos"] is True
    assert status["auto_promotion"] is False
    assert status["production_authority"] is False


def test_champion_prediction_timestamp_must_precede_target() -> None:
    rt = _Runtime()
    now = time.time()
    _add_direction_model(rt, "m1", now-300, now-400)
    _bootstrap_champions(rt, frozen_at=now-200)

    # Simulate a delayed materialization: this row was genuinely T0-eligible at
    # capture, but the target is already in the past now. No "prospective" row
    # may be created retrospectively.
    _add_obs(rt, "late", now-100, now-10, 0.2)
    assert _write_champion_predictions(rt, "late", now-100, 15) == 0
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_shadow_predictions WHERE observation_id='late'"
    ).fetchone()[0] == 0
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_champion_prediction_links WHERE observation_id='late'"
    ).fetchone()[0] == 0


def test_direction_and_continuous_targets_get_separate_frozen_cohorts() -> None:
    rt = _Runtime()
    now = time.time()
    _add_direction_model(rt, "m-dir", now-300, now-400)
    _add_return_model(rt, "m-ret", now-280, now-390)
    assert _bootstrap_champions(rt, frozen_at=now-200) == 2

    _add_obs(rt, "o1", now-20, now+600, 0.4)
    assert _write_champion_predictions(rt, "o1", now-20, 15) == 2
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_shadow_predictions WHERE model_id='m-dir'"
    ).fetchone()[0] == 1
    assert rt._conn.execute(
        "SELECT COUNT(*) FROM g1s_return_predictions WHERE model_id='m-ret'"
    ).fetchone()[0] == 1
    targets = {row[0] for row in rt._conn.execute(
        "SELECT target FROM g1s_champion_prediction_links WHERE observation_id='o1'"
    )}
    assert targets == {DIRECTION_TARGET, RETURN_TARGET}


def test_progress_counts_only_resolved_linked_oos_and_never_promotes() -> None:
    rt = _Runtime()
    now = time.time()
    _add_direction_model(rt, "m1", now-300, now-400)
    _bootstrap_champions(rt, frozen_at=now-200)
    for index, label in enumerate(("UP", "DOWN")):
        captured = now-20+index
        obs_id = f"o{index}"
        _add_obs(rt, obs_id, captured, now+600+index, 0.2+index)
        _write_champion_predictions(rt, obs_id, captured, 15)
        rt._conn.execute(
            "INSERT INTO g1s_resolutions VALUES(?,?,?,?)",
            (obs_id, label, 0.001 if label == "UP" else -0.001, now+700+index),
        )
    rt._conn.commit()
    _refresh_progress(rt)
    status = _champion_status(rt)
    item = next(x for x in status["items"] if x["target"] == DIRECTION_TARGET)
    assert item["oos_raw_n"] == 2
    assert item["positive_n"] == 1
    assert item["negative_n"] == 1
    assert item["status"] == "LIVE_VALIDATING"
    assert item["evidence_maturity"] == "INSUFFICIENT"
    assert item["auto_promotion"] == 0
    assert status["contract_version"] == CHAMPION_CONTRACT_VERSION
