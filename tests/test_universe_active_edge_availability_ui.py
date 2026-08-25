from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "seiltanzer" / "web"


def test_universe_initial_active_edge_metrics_are_unavailable_not_zero() -> None:
    html = (ROOT / "universe.html").read_text(encoding="utf-8")
    assert 'id="edge-weight">—<' in html
    assert 'id="edge-direction">—<' in html
    assert 'id="edge-votes">— / —<' in html
    assert 'id="edge-buckets">—<' in html
    assert 'id="edge-status">○ ACTIVE EDGE N/A<' in html


def test_precision_layer_distinguishes_missing_reports_from_measured_zero() -> None:
    source = (ROOT / "js" / "universe_precision.js").read_text(encoding="utf-8")
    assert "measurementAvailable" in source
    assert "ACTIVE EDGE N/A" in source
    assert "CURRENT-SHA REPORTS" in source
    assert "0 CURRENT-T0 MATCHES" in source
    assert "измеренный ноль" in source
    assert "setText('edge-votes', '— / —')" in source


def test_precision_layer_clears_stale_values_on_transport_failure() -> None:
    source = (ROOT / "js" / "universe_precision.js").read_text(encoding="utf-8")
    assert "patchEdgeUnavailableTransport" in source
    assert "EDGE API HTTP ${response.status}" in source
    assert "patchEdgeUnavailableTransport('EDGE API ERROR')" in source
    assert "ACTIVE EDGE N/A · ${label}" in source
    assert "window.Plotly.purge(chart)" in source


def test_precision_numeric_formatters_do_not_coerce_missing_to_zero() -> None:
    source = (ROOT / "js" / "universe_precision.js").read_text(encoding="utf-8")
    assert "const pct = (value, digits = 1) => finite(value)" in source
    assert "const signed = (value, digits = 2) => finite(value)" in source
    assert "const count = (value) => finite(value)" in source
    assert "profile.weight_fraction || 0" not in source
