const MOBILE_QUERY = '(max-width: 760px)';
const MOBILE_SURFACE_ROWS = 30;
const MOBILE_SURFACE_COLS = 44;
let installed = false;
let resizeTimer = null;
let observer = null;
let mutationObserver = null;
let optimizeTimer = null;

export function isAnalyticsMobile() {
  if (typeof window === 'undefined') return false;
  if (typeof window.matchMedia === 'function') return window.matchMedia(MOBILE_QUERY).matches;
  return Number(window.innerWidth || 9999) <= 760;
}

export function analyticsMobileDpr() {
  if (typeof window === 'undefined') return 1;
  return Math.min(Number(window.devicePixelRatio || 1), isAnalyticsMobile() ? 1.5 : 2);
}

function sampleIndices(length, maxCount) {
  const n = Math.max(0, Number(length) || 0);
  const m = Math.max(2, Number(maxCount) || 2);
  if (n <= m) return Array.from({ length: n }, (_, i) => i);
  const out = [];
  for (let i = 0; i < m; i++) {
    const idx = Math.round(i * (n - 1) / (m - 1));
    if (out.at(-1) !== idx) out.push(idx);
  }
  if (out.at(-1) !== n - 1) out.push(n - 1);
  return out;
}

function pick1d(values, indices) {
  if (!Array.isArray(values)) return values;
  return indices.map((i) => values[Math.max(0, Math.min(values.length - 1, i))]);
}

function pick2d(values, rowIdx, colIdx) {
  if (!Array.isArray(values) || !Array.isArray(values[0])) return values;
  return rowIdx.map((r) => {
    const row = values[Math.max(0, Math.min(values.length - 1, r))] || [];
    return colIdx.map((c) => row[Math.max(0, Math.min(row.length - 1, c))]);
  });
}

export function decimateSurfaceTrace(trace, maxRows = MOBILE_SURFACE_ROWS, maxCols = MOBILE_SURFACE_COLS) {
  if (!trace || trace.type !== 'surface' || !Array.isArray(trace.z) || !Array.isArray(trace.z[0])) return trace;
  const rows = trace.z.length;
  const cols = trace.z[0]?.length || 0;
  if (!rows || !cols || (rows <= maxRows && cols <= maxCols)) return trace;
  const rowIdx = sampleIndices(rows, maxRows);
  const colIdx = sampleIndices(cols, maxCols);
  const next = {
    ...trace,
    z: pick2d(trace.z, rowIdx, colIdx),
  };
  if (Array.isArray(trace.surfacecolor)) next.surfacecolor = pick2d(trace.surfacecolor, rowIdx, colIdx);
  if (Array.isArray(trace.x)) next.x = Array.isArray(trace.x[0])
    ? pick2d(trace.x, rowIdx, colIdx)
    : pick1d(trace.x, colIdx);
  if (Array.isArray(trace.y)) next.y = Array.isArray(trace.y[0])
    ? pick2d(trace.y, rowIdx, colIdx)
    : pick1d(trace.y, rowIdx);
  return next;
}

function rootElement() {
  if (typeof document === 'undefined') return null;
  return document.documentElement || null;
}

function advancedPanels() {
  if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return [];
  return ['#panel-gex-evol', '#panel-macro-regime', '#panel-wavelet', '#panel-correlation']
    .map((sel) => document.querySelector(sel)).filter(Boolean);
}

function advancedPlotNodes() {
  if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') return [];
  return [...document.querySelectorAll(
    '#gex-evol-canvas .js-plotly-plot, #regime-phase-plot.js-plotly-plot, #wavelet-canvas-holder .js-plotly-plot',
  )];
}

function markViewportState() {
  const root = rootElement();
  if (!root?.classList) return;
  const mobile = isAnalyticsMobile();
  root.classList.toggle('analytics-mobile', mobile);
  root.classList.toggle('analytics-landscape', mobile && Number(window.innerWidth || 0) > Number(window.innerHeight || 0));
  if (!mobile) root.classList.remove('analytics-3d-busy');
}

function tunePlotTouch(plot) {
  if (!plot?.style || !isAnalyticsMobile()) return;
  plot.style.touchAction = 'none';
  plot.style.overscrollBehavior = 'contain';
  plot.style.userSelect = 'none';
  plot.style.webkitUserSelect = 'none';
  plot.setAttribute?.('data-mobile-touch-ready', '1');
}

async function optimizeSurfacePlot(plot) {
  if (!isAnalyticsMobile() || !plot || plot.dataset?.mobileMeshOptimized === '1') return;
  if (!window.Plotly || !Array.isArray(plot.data) || !plot.data.some((t) => t?.type === 'surface')) return;
  const traces = plot.data.map((trace) => decimateSurfaceTrace(trace));
  const changed = traces.some((trace, i) => trace !== plot.data[i]);
  tunePlotTouch(plot);
  if (!changed) {
    if (plot.dataset) plot.dataset.mobileMeshOptimized = '1';
    return;
  }
  const camera = plot?._fullLayout?.scene?.camera;
  const layout = { ...(plot.layout || {}) };
  layout.scene = { ...(layout.scene || {}), dragmode: 'orbit' };
  if (camera) layout.scene.camera = JSON.parse(JSON.stringify(camera));
  if (plot.dataset) plot.dataset.mobileMeshOptimized = 'working';
  try {
    await window.Plotly.react(plot, traces, layout, {
      responsive: false,
      displayModeBar: false,
      scrollZoom: true,
    });
    if (plot.dataset) plot.dataset.mobileMeshOptimized = '1';
  } catch {
    if (plot.dataset) delete plot.dataset.mobileMeshOptimized;
  }
}

function scheduleOptimize() {
  if (!isAnalyticsMobile()) return;
  if (optimizeTimer) clearTimeout(optimizeTimer);
  optimizeTimer = setTimeout(() => {
    optimizeTimer = null;
    advancedPlotNodes().forEach((plot) => {
      tunePlotTouch(plot);
      optimizeSurfacePlot(plot);
    });
  }, 70);
}

function resizeAdvancedPlots() {
  if (typeof window === 'undefined' || !window.Plotly?.Plots?.resize) return;
  advancedPlotNodes().forEach((plot) => {
    tunePlotTouch(plot);
    try { window.Plotly.Plots.resize(plot); } catch {}
  });
  scheduleOptimize();
}

function scheduleResize() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    markViewportState();
    resizeAdvancedPlots();
    if (typeof window !== 'undefined' && typeof window.CustomEvent === 'function' && typeof window.dispatchEvent === 'function') {
      window.dispatchEvent(new window.CustomEvent('seiltanzer:analytics-mobile-resize'));
    }
  }, 100);
}

function installVisibilityObserver() {
  if (observer || typeof IntersectionObserver === 'undefined') return;
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      entry.target?.classList?.toggle('analytics-offscreen', !entry.isIntersecting);
      entry.target?.classList?.toggle('analytics-onscreen', entry.isIntersecting);
    }
  }, { rootMargin: '140px 0px', threshold: 0.01 });
  advancedPanels().forEach((panel) => observer.observe(panel));
}

function setPanelSuspended(panel, suspend) {
  if (!panel?.style) return;
  if (suspend) {
    if (panel.dataset.mobileSuspended === '1') return;
    panel.dataset.mobileSuspended = '1';
    panel.dataset.mobileContentVisibility = panel.style.contentVisibility || '';
    panel.style.contentVisibility = 'hidden';
    panel.style.pointerEvents = 'none';
    panel.querySelectorAll?.('canvas').forEach((canvas) => {
      canvas.dataset.mobileVisibility = canvas.style.visibility || '';
      canvas.style.visibility = 'hidden';
    });
  } else {
    if (panel.dataset.mobileSuspended !== '1') return;
    delete panel.dataset.mobileSuspended;
    panel.style.contentVisibility = panel.dataset.mobileContentVisibility || '';
    panel.style.pointerEvents = '';
    delete panel.dataset.mobileContentVisibility;
    panel.querySelectorAll?.('canvas').forEach((canvas) => {
      canvas.style.visibility = canvas.dataset.mobileVisibility || '';
      delete canvas.dataset.mobileVisibility;
    });
  }
}

function suspendOffscreenPanels(suspend) {
  if (!isAnalyticsMobile()) return;
  for (const panel of advancedPanels()) {
    if (!panel.classList?.contains?.('analytics-offscreen')) continue;
    setPanelSuspended(panel, suspend);
  }
}

function bind3dBusyState() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  window.addEventListener('seiltanzer:3d-busy', () => {
    if (!isAnalyticsMobile()) return;
    rootElement()?.classList?.add('analytics-3d-busy');
    suspendOffscreenPanels(true);
  });
  window.addEventListener('seiltanzer:3d-idle', () => {
    rootElement()?.classList?.remove('analytics-3d-busy');
    suspendOffscreenPanels(false);
    scheduleResize();
  });
}

function bindViewport() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  window.addEventListener('resize', scheduleResize, { passive: true });
  window.addEventListener('orientationchange', scheduleResize, { passive: true });
  window.visualViewport?.addEventListener?.('resize', scheduleResize, { passive: true });
}

function installPlotObserver() {
  if (mutationObserver || typeof MutationObserver === 'undefined' || typeof document === 'undefined' || !document.body) return;
  mutationObserver = new MutationObserver((records) => {
    if (!isAnalyticsMobile()) return;
    const relevant = records.some((record) => [...(record.addedNodes || [])].some((node) =>
      node?.nodeType === 1 && (
        node.matches?.('.js-plotly-plot, [data-renderer="pressure"], [data-wavelet-renderer="surface"]')
        || node.querySelector?.('.js-plotly-plot')
      )));
    if (relevant) scheduleOptimize();
  });
  mutationObserver.observe(document.body, { childList: true, subtree: true });
}

export function installAnalyticsMobileRuntime() {
  if (installed || typeof window === 'undefined' || typeof document === 'undefined') return;
  installed = true;
  markViewportState();
  bindViewport();
  bind3dBusyState();
  installVisibilityObserver();
  installPlotObserver();
  scheduleResize();
}
