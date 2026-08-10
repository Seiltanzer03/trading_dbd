import { installAnalyticsMobileRuntime } from './analytics_mobile.js';

let installed = false;

export function ensurePremiumAnalyticsTheme() {
  installAnalyticsMobileRuntime();
  if (installed || typeof document === 'undefined') return;
  installed = true;
  const style = document.createElement('style');
  style.id = 'premium-analytics-theme';
  style.textContent = `
    #panel-gex-evol .canvas-holder,
    #panel-macro-regime .canvas-holder,
    #panel-wavelet .canvas-holder,
    #panel-correlation .corr-holder {
      border: 1px solid rgba(27,43,61,.18) !important;
      border-radius: 10px !important;
      overflow: hidden !important;
      background:
        radial-gradient(circle at 18% 12%, rgba(42,111,140,.13), transparent 34%),
        radial-gradient(circle at 85% 85%, rgba(176,116,42,.08), transparent 38%),
        linear-gradient(145deg, #07111d 0%, #0a1725 48%, #0d1a28 100%) !important;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.045), 0 8px 26px rgba(17,29,43,.08);
      contain: layout paint;
    }
    #panel-gex-evol .gex-side-card,
    #panel-macro-regime .regime-side-card,
    #panel-wavelet .wavelet-side-card {
      border-radius: 9px !important;
      border-color: rgba(28,44,61,.13) !important;
      background: linear-gradient(180deg, rgba(255,255,255,.92), rgba(246,247,246,.98)) !important;
      box-shadow: 0 8px 24px rgba(33,42,52,.045);
    }
    #panel-gex-evol .btn-toggle.active,
    #panel-wavelet .btn-toggle.active,
    #panel-correlation .btn-toggle.active,
    #panel-macro-regime .btn-toggle.active {
      background: #10283f !important;
      border-color: #10283f !important;
      color: #fff !important;
      box-shadow: 0 2px 8px rgba(16,40,63,.18);
    }
    .analytics-human-line{padding:8px 13px 7px;border-top:1px solid rgba(75,104,120,.12);border-bottom:1px solid rgba(75,104,120,.10);background:linear-gradient(90deg,rgba(10,31,44,.055),rgba(72,201,190,.035),transparent);color:#314d5b;font:700 10px/1.35 'IBM Plex Mono',monospace;letter-spacing:.055em}
    .analytics-hud-pill {
      display:inline-flex;align-items:center;gap:5px;padding:3px 7px;border-radius:999px;
      border:1px solid rgba(44,62,80,.14);background:rgba(255,255,255,.86);font-size:9px;color:#5f6872;
    }
    .analytics-metric-grid { display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:7px; }
    .analytics-metric-tile { border:1px solid #dedbd3;border-radius:6px;padding:6px 7px;background:rgba(255,255,255,.72);min-width:0; }
    .analytics-metric-tile small { display:block;color:#7d7971;font-size:8px;margin-bottom:2px; }
    .analytics-metric-tile b { font-size:11px;color:#263849;overflow-wrap:anywhere; }

    @media (max-width: 760px) {
      #panel-gex-evol,
      #panel-macro-regime,
      #panel-wavelet,
      #panel-correlation {
        max-width:100% !important;
        overflow:hidden !important;
        contain:layout paint style;
      }
      #panel-gex-evol .panel-head,
      #panel-macro-regime .panel-head,
      #panel-wavelet .panel-head,
      #panel-correlation .panel-head {
        align-items:flex-start !important;
        flex-wrap:wrap !important;
        gap:8px !important;
        padding:9px 10px !important;
      }
      #panel-gex-evol .panel-head h2,
      #panel-macro-regime .panel-head h2,
      #panel-wavelet .panel-head h2,
      #panel-correlation .panel-head h2 {
        flex:1 1 100% !important;
        min-width:0 !important;
        font-size:9.5px !important;
        line-height:1.35 !important;
        letter-spacing:.09em !important;
        overflow-wrap:anywhere !important;
      }
      #panel-gex-evol .panel-head-right,
      #panel-macro-regime .panel-head-right,
      #panel-wavelet .panel-head-right,
      #panel-correlation .panel-head-right {
        width:100% !important;
        min-width:0 !important;
        display:flex !important;
        flex-wrap:wrap !important;
        justify-content:space-between !important;
        gap:6px !important;
      }
      #panel-gex-evol .btn-group,
      #panel-macro-regime .btn-group,
      #panel-wavelet .btn-group,
      #panel-correlation .btn-group {
        display:grid !important;
        grid-auto-flow:column !important;
        grid-auto-columns:minmax(0,1fr) !important;
        width:100% !important;
        max-width:100% !important;
        gap:4px !important;
      }
      #corr-link-mode-group{order:3;width:100%;justify-content:flex-end}
      .analytics-human-line{font-size:8px;padding:7px 10px;letter-spacing:.035em}
      #panel-gex-evol .btn-toggle,
      #panel-macro-regime .btn-toggle,
      #panel-wavelet .btn-toggle,
      #panel-correlation .btn-toggle {
        min-width:0 !important;
        min-height:34px !important;
        padding:6px 4px !important;
        font-size:8px !important;
        letter-spacing:.035em !important;
        white-space:nowrap !important;
        overflow:hidden !important;
        text-overflow:ellipsis !important;
        touch-action:manipulation !important;
        -webkit-tap-highlight-color:transparent !important;
      }
      #panel-gex-evol .badge,
      #panel-macro-regime .badge,
      #panel-wavelet .badge,
      #panel-correlation .badge {
        max-width:100% !important;
        white-space:normal !important;
        overflow-wrap:anywhere !important;
        font-size:8px !important;
        line-height:1.35 !important;
      }
      #panel-gex-evol .panel-body,
      #panel-macro-regime .panel-body,
      #panel-wavelet .panel-body {
        display:grid !important;
        grid-template-columns:minmax(0,1fr) !important;
        align-items:start !important;
        gap:9px !important;
        padding:9px !important;
      }
      #panel-correlation .panel-body { padding:9px !important; }
      #panel-gex-evol .canvas-holder,
      #panel-macro-regime .canvas-holder,
      #panel-wavelet .canvas-holder,
      #panel-correlation .corr-holder {
        min-width:0 !important;
        width:100% !important;
        max-width:100% !important;
        flex:0 0 auto !important;
        margin:0 !important;
        position:relative !important;
      }
      #panel-gex-evol .canvas-holder,
      #panel-wavelet .canvas-holder { height:clamp(300px,84vw,380px) !important; }
      #panel-macro-regime .canvas-holder { height:clamp(320px,90vw,395px) !important; }
      #panel-correlation .corr-holder { height:clamp(320px,88vw,385px) !important; }
      #gex-evol-canvas,
      #regime-phase-plot,
      #wavelet-canvas-holder,
      #corr-chart,
      #gex-evol-canvas > *,
      #wavelet-canvas-holder > *,
      #corr-chart > * {
        width:100% !important;
        max-width:100% !important;
        min-width:0 !important;
        overflow:hidden !important;
      }
      #gex-evol-canvas canvas,
      #wavelet-canvas-holder canvas,
      #corr-chart canvas {
        width:100% !important;
        max-width:100% !important;
        height:100% !important;
        display:block !important;
      }
      #gex-summary-card,
      #regime-summary-card,
      #wavelet-summary-card {
        min-width:0 !important;
        width:100% !important;
        max-width:100% !important;
        flex:0 0 auto !important;
        padding:9px !important;
        font-size:9.5px !important;
        gap:6px !important;
        overflow:hidden !important;
      }
      .analytics-metric-grid { gap:4px !important; }
      .analytics-metric-tile { padding:5px 6px !important; min-width:0 !important; }
      .analytics-metric-tile small { font-size:7px !important; }
      .analytics-metric-tile b { font-size:9px !important; line-height:1.3 !important; }
      #panel-gex-evol .js-plotly-plot,
      #panel-macro-regime .js-plotly-plot,
      #panel-wavelet .js-plotly-plot,
      #panel-gex-evol .plot-container,
      #panel-macro-regime .plot-container,
      #panel-wavelet .plot-container,
      #panel-gex-evol .svg-container,
      #panel-macro-regime .svg-container,
      #panel-wavelet .svg-container,
      #panel-gex-evol .gl-container,
      #panel-macro-regime .gl-container,
      #panel-wavelet .gl-container {
        width:100% !important;
        max-width:100% !important;
        height:100% !important;
        touch-action:none !important;
        overscroll-behavior:contain !important;
        user-select:none !important;
        -webkit-user-select:none !important;
        -webkit-tap-highlight-color:transparent !important;
      }
      #panel-gex-evol .modebar,
      #panel-macro-regime .modebar,
      #panel-wavelet .modebar { display:none !important; }
      #corr-chart canvas {
        touch-action:none !important;
        overscroll-behavior:contain !important;
        -webkit-tap-highlight-color:transparent !important;
      }
      #corr-interpretation {
        padding:8px 4px !important;
        font-size:9px !important;
        line-height:1.55 !important;
        overflow-wrap:anywhere !important;
      }
      .canvas-empty { font-size:9px !important; padding:14px !important; text-align:center !important; }
      .analytics-offscreen { contain-intrinsic-size:360px !important; }
      html.analytics-3d-busy #panel-gex-evol,
      html.analytics-3d-busy #panel-macro-regime,
      html.analytics-3d-busy #panel-wavelet,
      html.analytics-3d-busy #panel-correlation {
        box-shadow:none !important;
      }
      html.analytics-3d-busy .analytics-offscreen { visibility:hidden !important; }
    }

    @media (max-width: 430px) {
      #panel-gex-evol .canvas-holder,
      #panel-wavelet .canvas-holder { height:clamp(292px,86vw,340px) !important; }
      #panel-macro-regime .canvas-holder { height:clamp(310px,92vw,360px) !important; }
      #panel-correlation .corr-holder { height:clamp(310px,90vw,355px) !important; }
      #panel-gex-evol .panel-foot,
      #panel-macro-regime .panel-foot,
      #panel-wavelet .panel-foot,
      #panel-correlation .panel-foot {
        padding:7px 9px !important;
        font-size:8.5px !important;
        line-height:1.45 !important;
      }
    }

    @media (max-width: 900px) and (orientation: landscape) and (max-height: 520px) {
      #panel-gex-evol .canvas-holder,
      #panel-wavelet .canvas-holder,
      #panel-macro-regime .canvas-holder,
      #panel-correlation .corr-holder { height:290px !important; }
      #gex-summary-card,
      #regime-summary-card,
      #wavelet-summary-card { font-size:8.5px !important; }
    }
  `;
  document.head.appendChild(style);
}
