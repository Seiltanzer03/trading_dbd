from pathlib import Path


def test_passive_calibration_smoke_is_single_bounded_request():
    source = Path("scripts/production_functional_smoke.py").read_text(encoding="utf-8")
    assert "PASSIVE_CALIBRATION_TIMEOUT_SEC = 20.0" in source
    assert 'elif path == "/api/research/passive/calibration":' in source
    assert "timeout=PASSIVE_CALIBRATION_TIMEOUT_SEC" in source
    assert "attempts=1" in source
