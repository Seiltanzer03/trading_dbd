from seiltanzer.core.gex_migration import compute_gex_migration


def _snapshot(ts, strikes, net, flip):
    return {
        "ts": float(ts),
        "gex": {
            "available": True,
            "strikes": list(strikes),
            "net": list(net),
            "zero_flip": flip,
        },
    }


def test_gex_migration_empty():
    res = compute_gex_migration([], current_price=100.0)
    assert res["available"] is False
    assert res["summary"]["take_path"] == "NO DATA"
    assert res["summary"]["authority"] == "context_only"
    assert res["summary"]["independent_vote"] is False


def test_gex_migration_tracks_real_levels_and_history():
    snapshots = [
        _snapshot(1_000, [90, 100, 110], [-100, 50, 300], 98),
        _snapshot(4_600, [90, 100, 110], [-150, 40, 350], 99),
    ]
    trade = {"entry": 100.0, "stop": 95.0, "take": 115.0, "direction": "long"}
    res = compute_gex_migration(snapshots, current_price=102.0, active_trade=trade)
    assert res["available"] is True
    assert len(res["timestamps"]) == 2
    assert len(res["price_grid"]) == 96
    assert res["summary"]["flip"]["price"] == 99.0
    assert res["summary"]["call_wall"]["price"] == 110.0
    assert res["summary"]["put_wall"]["price"] == 90.0
    assert res["summary"]["snapshot_count"] == 2
    assert res["summary"]["history_hours"] == 1.0
    assert res["summary"]["authority"] == "context_only"
    assert res["summary"]["independent_vote"] is False


def test_gex_migration_remote_outlier_does_not_destroy_plot_scale():
    snapshots = [
        _snapshot(1_000, [3_900, 4_050, 4_200, 4_350, 9_800],
                  [-30, -200, 40, 400, 1_000_000], 4_180),
        _snapshot(4_600, [3_900, 4_050, 4_200, 4_350, 9_800],
                  [-50, -220, 60, 450, 1_100_000], 4_190),
    ]
    trade = {"entry": 4_200.0, "stop": 4_050.0, "take": 4_500.0, "direction": "long"}
    res = compute_gex_migration(snapshots, current_price=4_240.0, active_trade=trade)
    assert res["available"] is True
    lo, hi = res["plot_range"]
    assert lo < 4_050 < 4_240 < 4_500 < hi
    assert hi < 6_000  # 9,800 outlier must not stretch the actionable chart.
    assert max(res["price_grid"]) == hi


def test_gex_missing_lookback_is_none_not_fake_zero():
    res = compute_gex_migration(
        [_snapshot(1_000, [90, 100, 105], [-100, 20, 300], 98)],
        current_price=100.0,
        active_trade={"entry": 100, "stop": 95, "take": 110, "direction": "long"},
    )
    assert res["available"] is True
    assert res["summary"]["call_wall"]["migration_6h"] is None
    assert res["summary"]["put_wall"]["migration_6h"] is None


def test_gex_migration_take_path_obstructed():
    snapshots = [_snapshot(1_000, [90, 105, 120], [-200, 500, 100], 95)]
    trade = {"entry": 100.0, "stop": 95.0, "take": 110.0, "direction": "long"}
    res = compute_gex_migration(snapshots, current_price=100.0, active_trade=trade)
    assert "OBSTRUCTED" in res["summary"]["take_path"]
    assert res["summary"]["path_pressure"] < 0
