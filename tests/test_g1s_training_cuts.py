from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
from seiltanzer import g1_short_horizon_runtime as _g1s
from seiltanzer.g1_short_horizon_cut_refinement import CUT_CONTRACT_VERSION
from seiltanzer.passive_learning import PassiveLearningEngine


class _Engine:
    def __init__(self, passive):
        self.passive = passive


def _runtime(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    passive = PassiveLearningEngine(
        str(tmp_path / "trades.db"),
        Settings(demo=False, data_dir=str(tmp_path)), cache,
    )
    return ShortHorizonRuntime(_Engine(passive)), passive, cache


def _insert_resolved(rt, *, suffix: str, ts: float, direction: str):
    obs_id = f"g1s-cut-{suffix}"
    features = json.dumps({
        "volatility": {"reference_volatility_annual": 0.2},
        "market_regime": "test",
    }, sort_keys=True, separators=(",", ":"))
    forecast = json.dumps({
        "sigma_h_return": 0.01,
        "reference_volatility_annual": 0.2,
    }, sort_keys=True, separators=(",", ":"))
    t0_sha = hashlib.sha256(f"t0-{suffix}".encode()).hexdigest()
    res_sha = hashlib.sha256(f"res-{suffix}".encode()).hexdigest()
    with rt._lock, rt._conn:
        rt._conn.execute("""
            INSERT INTO g1s_observations(
                observation_id,source_observation_id,source_rowid,captured_ts,target_ts,
                instrument,horizon_minutes,origin,market_price,price_source,price_kind,
                price_quality,option_source,option_kind,option_quality,market_regime,session,
                source_feature_contract,source_forecast_contract,features_sha256,
                forecast_sha256,t0_sha256,measurement_eligible,training_eligible,oos_eligible,
                exclusion_reason,frozen_features_json,frozen_forecast_json,created_ts)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            obs_id, f"source-{suffix}", int(suffix), ts, ts + 900,
            "XAU", 15, "LIVE_PROSPECTIVE", 100.0, "direct", "direct", 1.0,
            None, "unavailable", 0.0, "test", "OPEN", "f", "m",
            hashlib.sha256(features.encode()).hexdigest(),
            hashlib.sha256(forecast.encode()).hexdigest(), t0_sha,
            1, 1, 1, None, features, forecast, ts,
        ))
        resolution = json.dumps({"direction": direction}, sort_keys=True)
        rt._conn.execute("""
            INSERT INTO g1s_resolutions(
                observation_id,source_observation_id,resolved_ts,terminal_log_return,
                direction_label,mfe_log_return,mae_log_return,path_quality_status,
                source_outcome_sha256,resolution_json,resolution_sha256,created_ts)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            obs_id, f"source-{suffix}", ts + 901,
            0.01 if direction == "UP" else -0.01, direction,
            0.02, -0.02, "clean", res_sha, resolution, res_sha, ts + 901,
        ))
    return obs_id, t0_sha, res_sha


def test_every_new_model_links_to_exact_immutable_training_manifest(tmp_path, monkeypatch):
    rt, passive, cache = _runtime(tmp_path)
    try:
        sources = [
            _insert_resolved(rt, suffix="1", ts=1_700_000_000.0, direction="UP"),
            _insert_resolved(rt, suffix="2", ts=1_700_004_000.0, direction="DOWN"),
        ]
        # Unit-test the lineage mechanism without weakening production constants.
        monkeypatch.setitem(_g1s.FIT_REQUIRED, "raw_resolved", 2)
        monkeypatch.setitem(_g1s.FIT_REQUIRED, "effective_n", 1)
        monkeypatch.setitem(_g1s.FIT_REQUIRED, "positive_n", 1)
        monkeypatch.setitem(_g1s.FIT_REQUIRED, "negative_n", 1)
        monkeypatch.setitem(_g1s.FIT_REQUIRED, "trading_days", 1)

        created = rt.fit_if_ready(force=True)
        assert created >= 1
        models = rt._conn.execute("SELECT model_id FROM g1s_models").fetchall()
        links = rt._conn.execute(
            "SELECT model_id,cut_id FROM g1s_model_cut_links ORDER BY model_id").fetchall()
        assert len(links) == len(models)

        cut = rt._conn.execute(
            "SELECT * FROM g1s_training_cuts ORDER BY created_ts LIMIT 1").fetchone()
        assert cut["contract_version"] == CUT_CONTRACT_VERSION
        payload = json.loads(cut["source_manifest_json"])
        manifest = payload["sources"]
        assert [(x["observation_id"], x["t0_sha256"], x["resolution_sha256"])
                for x in manifest] == sources
        assert hashlib.sha256(cut["source_manifest_json"].encode()).hexdigest() == \
            cut["source_manifest_sha256"]

        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with rt._conn:
                rt._conn.execute(
                    "UPDATE g1s_training_cuts SET raw_n=999 WHERE cut_id=?",
                    (cut["cut_id"],))
    finally:
        passive.close(); cache.close()
