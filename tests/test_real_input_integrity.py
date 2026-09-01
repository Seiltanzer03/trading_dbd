from __future__ import annotations

import time
from types import SimpleNamespace

from seiltanzer.ai_policy_base import _volume_profile_delta
from seiltanzer.config import Settings
from seiltanzer.data.adaptive_chain import _cache_fallback
from seiltanzer.data.cache import production_chain_snapshot
from seiltanzer.data.feeds import _status_dict
from seiltanzer.engine import Engine


class _SnapshotCache:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def chain_snapshots(self, _proxy, limit=60):
        return self.snapshots[-limit:]


def test_live_chain_cache_requires_explicit_real_provenance():
    assert production_chain_snapshot({"demo": False}) is True
    assert production_chain_snapshot({"demo": True}) is False
    assert production_chain_snapshot({}) is False
    assert production_chain_snapshot({
        "demo": False,
        "provenance": {"synthetic_data_used": True},
    }) is False
    # BS reconstruction from actually reported IV is derived lower-evidence
    # input, not a demo/synthetic chain.
    assert production_chain_snapshot({
        "demo": False,
        "density_input": {"mode": "reported_iv_smile_bs_reconstruction"},
    }) is True


def test_live_cache_fallback_rejects_demo_snapshot():
    owner = SimpleNamespace(
        cache=_SnapshotCache([{"ts": time.time(), "demo": True}]),
        chain=None,
    )
    _cache_fallback(owner, "QQQ", RuntimeError("source unavailable"), _status_dict)
    assert owner.chain["metrics"] is None
    assert owner.chain["status"] == "no_data"
    assert owner.chain["cache_fallback"] == {
        "used": False,
        "reason": "no_explicitly_real_snapshot",
        "demo_or_unverified_rejected": True,
    }


def test_live_cache_fallback_uses_only_explicit_real_snapshot():
    real = {"ts": time.time() - 10, "demo": False, "proxy": "QQQ"}
    demo = {"ts": time.time(), "demo": True, "proxy": "QQQ"}
    owner = SimpleNamespace(cache=_SnapshotCache([real, demo]), chain=None)
    _cache_fallback(owner, "QQQ", RuntimeError("source unavailable"), _status_dict)
    assert owner.chain["metrics"] == real
    assert owner.chain["status"] == "delayed"
    assert owner.chain["cache_fallback"]["snapshot_provenance"] == \
        "explicit_real_demo_false"


def test_tpo_profile_never_becomes_directional_flow_for_ai(tmp_path):
    engine = Engine(Settings(demo=False, data_dir=str(tmp_path)))
    try:
        engine.market.daily = {
            "bars": {
                "highs": [101.0] * 30,
                "lows": [99.0] * 30,
                "closes": [100.0] * 30,
            },
        }
        engine.market.intraday = [
            (1.0, 100.0, 0.0),
            (2.0, 101.0, 0.0),
            (3.0, 100.5, 0.0),
        ]
        profile = engine._volume_profile_payload()
        assert profile["is_tpo"] is True
        assert profile["flow_available"] is False
        assert profile["flow_provenance"] == "unavailable_no_observed_volume"
        assert all(row["delta"] is None for row in profile["bins"])

        evidence = _volume_profile_delta(profile, {"direction": "long"})
        assert evidence["directional_delta_ratio"] is None
        assert evidence["available"] is False
        assert evidence["authority"] == "none"
    finally:
        engine.close()


def test_live_ridge_rejects_demo_cache_rows(tmp_path):
    engine = Engine(Settings(demo=False, data_dir=str(tmp_path)))
    try:
        engine.cache.add_chain_snapshot(
            "QQQ", {"demo": True, "proxy": "QQQ"}, ts=time.time())
        ridge = engine.ridge_payload()
        assert ridge["available"] is False
        assert ridge["snapshots"] == []
        assert "нет ни одного" in ridge["reason"]
    finally:
        engine.close()
