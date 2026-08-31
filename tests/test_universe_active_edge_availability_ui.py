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
    scene_source = (ROOT / "js" / "universe_scenes.js").read_text(encoding="utf-8")
    assert "patchEdgeUnavailableTransport" in source
    assert "EDGE API HTTP ${response.status}" in scene_source
    assert "markEdgeUniverseTransportUnavailable" in scene_source
    assert "ACTIVE EDGE N/A · ${label}" in source
    assert "chart?.classList.add('transport-stale')" in source
    assert "window.Plotly.purge(chart)" not in source


def test_precision_layer_does_not_duplicate_heavy_edge_requests() -> None:
    source = (ROOT / "js" / "universe_precision.js").read_text(encoding="utf-8")
    assert "fetch('/api/visual/edge-universe'" not in source
    assert "MutationObserver" not in source
    assert "setInterval(refreshPrecision" not in source
    assert "window.applyEdgeUniversePrecision" in source


def test_universe_graph_identifies_missing_features_and_uses_real_t0_motion() -> None:
    source = (ROOT / "js" / "universe_scenes.js").read_text(encoding="utf-8")
    html = (ROOT / "universe.html").read_text(encoding="utf-8")
    assert "N/A AGGREGATE" not in source
    assert "featureGeo.unavailableN" in source
    assert "N/A = NOT OBSERVED, NOT ZERO" in html
    assert "realT0Changed" in source
    assert "motionSegments" in source
    assert "window.Plotly.animate" in source
    assert "setInterval" not in source


def test_precision_numeric_formatters_do_not_coerce_missing_to_zero() -> None:
    source = (ROOT / "js" / "universe_precision.js").read_text(encoding="utf-8")
    assert "const pct = (value, digits = 1) => finite(value)" in source
    assert "const signed = (value, digits = 2) => finite(value)" in source
    assert "const count = (value) => finite(value)" in source
    assert "profile.weight_fraction || 0" not in source
