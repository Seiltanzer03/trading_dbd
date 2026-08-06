import pytest
from seiltanzer.core.gex_migration import compute_gex_migration


def test_gex_migration_empty():
    res = compute_gex_migration([], current_price=100.0)
    assert res["available"] is False
    assert res["summary"]["take_path"] == "NO DATA"
    assert res["summary"]["authority"] == "context_only"
    assert res["summary"]["independent_vote"] is False


def test_gex_migration_basic():
    snapshots = [
        {
            "ts": 1000.0,
            "ts_iso": "2026-08-06T10:00:00Z",
            "gex": {
                "strikes": [90.0, 100.0, 110.0],
                "net": [-100.0, 50.0, 300.0],
                "zero_flip": 98.0,
            },
        },
        {
            "ts": 4600.0,  # +1 hour
            "ts_iso": "2026-08-06T11:00:00Z",
            "gex": {
                "strikes": [90.0, 100.0, 110.0],
                "net": [-150.0, 40.0, 350.0],
                "zero_flip": 99.0,
            },
        },
    ]

    trade = {"entry": 100.0, "stop": 95.0, "take": 115.0, "direction": "long"}
    res = compute_gex_migration(snapshots, current_price=102.0, active_trade=trade)

    assert res["available"] is True
    assert len(res["timestamps"]) == 2
    assert 110.0 in res["price_grid"]
    assert res["summary"]["flip"]["price"] == 99.0
    assert res["summary"]["call_wall"]["price"] == 110.0
    assert res["summary"]["put_wall"]["price"] == 90.0
    assert res["summary"]["authority"] == "context_only"
    assert res["summary"]["independent_vote"] is False


def test_gex_migration_take_path_obstructed():
    snapshots = [
        {
            "ts": 1000.0,
            "gex": {
                "strikes": [90.0, 105.0, 120.0],
                "net": [-200.0, 500.0, 100.0],
                "zero_flip": 95.0,
            },
        }
    ]

    # Для лонга, если Call Wall (105.0) лежит между текущей ценой (100.0) и тейком (110.0) -> OBSTRUCTED
    trade = {"entry": 100.0, "stop": 95.0, "take": 110.0, "direction": "long"}
    res = compute_gex_migration(snapshots, current_price=100.0, active_trade=trade)

    assert "OBSTRUCTED" in res["summary"]["take_path"]
    assert res["summary"]["path_pressure"] < 0
