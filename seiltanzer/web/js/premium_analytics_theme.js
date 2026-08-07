let installed = false;

export function ensurePremiumAnalyticsTheme() {
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
    .analytics-hud-pill {
      display:inline-flex;align-items:center;gap:5px;padding:3px 7px;border-radius:999px;
      border:1px solid rgba(44,62,80,.14);background:rgba(255,255,255,.86);font-size:9px;color:#5f6872;
    }
    .analytics-metric-grid { display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:7px; }
    .analytics-metric-tile { border:1px solid #dedbd3;border-radius:6px;padding:6px 7px;background:rgba(255,255,255,.72); }
    .analytics-metric-tile small { display:block;color:#7d7971;font-size:8px;margin-bottom:2px; }
    .analytics-metric-tile b { font-size:11px;color:#263849; }
  `;
  document.head.appendChild(style);
}
