from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_market_information_animation_has_no_decorative_base_clock():
    gex = _read("seiltanzer/web/js/gex.js")
    corr = _read("seiltanzer/web/js/correlation.js")
    wavelet = _read("seiltanzer/web/js/wavelet.js")
    ridge = _read("seiltanzer/web/js/ridge.js")
    assert "Math.sin(now /" not in gex
    assert "const speed = .035 +" not in corr
    assert "now / 14" not in corr
    assert "(now/900)%1" not in wavelet
    assert "const breath" not in ridge
    assert "observedMotionDecay" in corr
    assert "observedMotionDecay" in wavelet


def test_cross_asset_full_mode_and_honest_counts_are_present():
    corr = _read("seiltanzer/web/js/correlation.js")
    assert "return links.slice()" in corr
    assert "SHOWN LINKS ${activeLinks.length} / OBSERVED ${links.length}" in corr
    assert "const packets = [.5 + .5 * phase, .5 - .5 * phase]" in corr
    assert "no causal direction" in corr


def test_unified_3d_toolbar_is_guard_owned_and_never_auto_rotates():
    toolbar = _read("seiltanzer/web/js/plotly_terminal_toolbar.js")
    guard = _read("seiltanzer/web/js/plotly_camera_guard.js")
    for mode in ("orbit", "turntable", "pan", "zoom"):
        assert f"setMode('{mode}')" in toolbar
        assert f"'{mode}'" in guard
    assert "rememberExternalCamera" in toolbar
    assert "getDragMode" in toolbar
    assert "setDragMode" in toolbar
    assert "onDragMode" in toolbar
    assert "existing?.__terminal3dPlot === plot" in toolbar
    assert "requestAnimationFrame" not in toolbar
    assert "function panCamera" in guard
    assert "function turntableCamera" in guard
    assert "next.scene.dragmode = dragMode" in guard
    for module in ("regime_phase.js", "wavelet.js", "gex.js"):
        source = _read(f"seiltanzer/web/js/{module}")
        assert "attachTerminal3DToolbar" in source
        assert "createPlotlyCameraGuard" in source


def test_wavelet_and_gex_keep_3d_plot_instances_across_refresh_and_resize():
    wavelet = _read("seiltanzer/web/js/wavelet.js")
    gex = _read("seiltanzer/web/js/gex.js")

    assert "new ResizeObserver(handleResize)" in wavelet
    assert "window.Plotly.Plots.resize(plot)" in wavelet
    assert "if(firstRender) window.Plotly.newPlot" in wavelet
    assert "else window.Plotly.react" in wavelet
    assert "let plot=containerEl.querySelector('[data-renderer=\"wavelet-surface\"]')" in wavelet

    assert "new ResizeObserver(handleResize)" in gex
    assert "window.Plotly.Plots.resize(plot)" in gex
    assert "if (firstRender) window.Plotly.newPlot" in gex
    assert "else window.Plotly.react" in gex
    assert "let plot = containerEl.querySelector('[data-renderer=\"pressure\"]')" in gex
