from __future__ import annotations

from types import SimpleNamespace

from seiltanzer.edge_discovery.shadow_cache import (
    load_shadow_summary_cache,
    shadow_summary_cache_path,
    write_shadow_summary_cache,
)


def _engine(tmp_path):
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))


def test_shadow_summary_cache_is_atomic_bounded_and_causal(tmp_path):
    engine = _engine(tmp_path)
    summary = {
        "prediction_count": 3,
        "resolved_count": 2,
        "pending_count": 1,
        "candidate_count": 1,
        "candidates": {"c1": {"candidate_id": "c1", "status": "SHADOW_ACTIVE"}},
        "production_authority": False,
    }
    meta = write_shadow_summary_cache(engine, summary=summary, cutoff_ts=100.0)
    path = shadow_summary_cache_path(engine)

    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert meta["bytes"] == path.stat().st_size
    assert meta["bytes"] < 20_000

    loaded = load_shadow_summary_cache(engine, cutoff_ts=100.0)
    assert loaded is not None
    assert loaded["summary"] == summary
    assert loaded["production_authority"] is False
    assert loaded["auto_promotion"] is False

    # A historical snapshot may never consume a summary materialized later.
    assert load_shadow_summary_cache(engine, cutoff_ts=99.0) is None


def test_shadow_summary_cache_missing_is_nonfatal(tmp_path):
    engine = _engine(tmp_path)
    assert load_shadow_summary_cache(engine, cutoff_ts=100.0) is None
