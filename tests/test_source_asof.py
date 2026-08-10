from seiltanzer.source_asof import derive_as_of, rows_as_of


def test_adversarial_future_row_cannot_enter_direct_or_rolling_feature():
    rows = [
        {"ts": 99.0, "value": 1.0},
        {"ts": 100.0, "value": 3.0},
        {"ts": 101.0, "value": 999.0},
    ]
    sliced = rows_as_of(rows, 100.0)
    assert [row["ts"] for row in sliced] == [99.0, 100.0]
    rolling = derive_as_of(
        rows, 100.0, lambda safe: sum(row["value"] for row in safe) / len(safe))
    assert rolling == 2.0
