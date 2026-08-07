const MOBILE_QUERY = '(max-width: 760px)';
let installed = false;
let resizeTimer = null;
let observer = null;
let mutationObserver = null;
let busy = false;
const parkedCanvases = new WeakMap();

export function isAnalyticsMobile() {
  if (typeof window === 'undefined') return false;
  if (typeof window.matchMedia === 'function') return window.matchMedia(MOBILE_QUERY).matches;
  return Number(window.innerWidth || 9999) <= 760;
}

export function analyticsMobileDpr() {
  if (typeof window === 'undefined') return 1;
  return Math.min(Number(window.devicePixelRatio || 1), isAnalyticsMobile() ? 1.5 : 2);
}

function rootEl() {
  if (typeof document === 'undefined') return null;
  return document.documentElement || document.querySelector?.('html') || null;
}

function advancedPanels() {
  if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return [];
  return ['#panel-gex-evol','#panel-macro-regime','#panel-wavelet','#panel-correlation']
    .map((sel) => document.querySelector(sel)).filter(Boolean);
}

function markViewportState() {
  const root = rootEl();
  if (!root?.classList) return;
  const mobile = isAnalyticsMobile();
  root.classList.toggle('analytics-mobile', mobile);
  if (!mobile) root.classList.remove('analytics-3d-busy');
}

function resizeAdvancedPlots() {
  if (typeof document === 'undefined' || typeof document.querySelectorAll !== 'function') return;
  if (typeof window === 'undefined' || !window.Plotly?.Plots?.resize) return;
  const plots = document.querySelectorAll(
    '#gex-evol-canvas .js-plotly-plot, #regime-phase-plot.js-plotly-plot, #wavelet-canvas-holder .js-plotly-plot',
  );
  plots.forEach((plot) => { try { window.Plotly.Plots.resize(plot); } catch {} });
}

function scheduleResize() {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    resizeTimer = null;
    markViewportState();
    resizeAdvancedPlots();
    if (typeof window !== 'undefined' && typeof window.CustomEvent === 'function' && typeof window.dispatchEvent === 'function') {
      window.dispatchEvent(new CustomEvent('seiltanzer:analytics-mobile-resize'));
    }
  }, 100);
}

function isCustomAnalyticsCanvas(canvas) {
  if (!canvas || typeof canvas.closest !== 'function') return false;
  if (!canvas.closest('#panel-gex-evol,#panel-wavelet,#panel-correlation,#panel-macro-regime')) return false;
  if (canvas.closest('.js-plotly-plot,.plot-container,.gl-container')) return false;
  return true;
}

function parkCanvas(canvas) {
  if (!isAnalyticsMobile() || !isCustomAnalyticsCanvas(canvas) || parkedCanvases.has(canvas)) return;
  const width = Number(canvas.width || 0);
  const height = Number(canvas.height || 0);
  if (width <= 2 && height <= 2) return;
  parkedCanvases.set(canvas, { width, height, visibility: canvas.style?.visibility || '' });
  try {
    canvas.width = 2;
    canvas.height = 2;
    if (canvas.style) canvas.style.visibility = 'hidden';
  } catch {}
}

function unparkCanvas(canvas) {
  const saved = parkedCanvases.get(canvas);
  if (!saved) return;
  parkedCanvases.delete(canvas);
  try {
    canvas.width = Math.max(1, saved.width || 1);
    canvas.height = Math.max(1, saved.height || 1);
    if (canvas.style) canvas.style.visibility = saved.visibility;
  } catch {}
}

function canvasesIn(panel) {
  if (!panel?.querySelectorAll) return [];
  return [...panel.querySelectorAll('canvas')].filter(isCustomAnalyticsCanvas);
}

function syncPanelCanvasBudget(panel) {
  if (!panel || !isAnalyticsMobile()) return;
  const offscreen = panel.classList?.contains('analytics-offscreen');
  const shouldPark = offscreen || busy;
  for (const canvas of canvasesIn(panel)) {
    if (shouldPark) parkCanvas(canvas);
    else unparkCanvas(canvas);
  }
}

function syncAllCanvasBudgets() {
  for (const panel of advancedPanels()) syncPanelCanvasBudget(panel);
}

function installVisibilityObserver() {
  if (observer || typeof IntersectionObserver === 'undefined') return;
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      entry.target.classList?.toggle('analytics-offscreen', !entry.isIntersecting);
      syncPanelCanvasBudget(entry.target);
    }
  }, { rootMargin: '160px 0px', threshold: 0.01 });
  advancedPanels().forEach((panel) => observer.observe(panel));
}

function installMutationObserver() {
  if (mutationObserver || typeof MutationObserver === 'undefined' || typeof document === 'undefined') return;
  const root = document.body || rootEl();
  if (!root) return;
  mutationObserver = new MutationObserver((records) => {
    if (!isAnalyticsMobile()) return;
    for (const record of records) {
      for (const node of record.addedNodes || []) {
        if (node?.tagName === 'CANVAS') {
          const panel = node.closest?.('#panel-gex-evol,#panel-macro-regime,#panel-wavelet,#panel-correlation');
          if (panel) syncPanelCanvasBudget(panel);
        } else if (node?.querySelectorAll) {
          for (const canvas of node.querySelectorAll('canvas')) {
            const panel = canvas.closest?.('#panel-gex-evol,#panel-macro-regime,#panel-wavelet,#panel-correlation');
            if (panel) syncPanelCanvasBudget(panel);
          }
        }
      }
    }
  });
  mutationObserver.observe(root, { childList: true, subtree: true });
}

function suspendOffscreenPanels(suspend) {
  if (!isAnalyticsMobile()) return;
  for (const panel of advancedPanels()) {
    if (!panel.classList?.contains('analytics-offscreen')) continue;
    if (suspend) {
      panel.dataset.mobileContentVisibility = panel.style.contentVisibility || '';
      panel.style.contentVisibility = 'hidden';
      panel.style.pointerEvents = 'none';
    } else {
      panel.style.contentVisibility = panel.dataset.mobileContentVisibility || '';
      panel.style.pointerEvents = '';
      delete panel.dataset.mobileContentVisibility;
    }
  }
}

function bind3dBusyState() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  window.addEventListener('seiltanzer:3d-busy', () => {
    if (!isAnalyticsMobile()) return;
    busy = true;
    rootEl()?.classList?.add('analytics-3d-busy');
    syncAllCanvasBudgets();
    suspendOffscreenPanels(true);
  });
  window.addEventListener('seiltanzer:3d-idle', () => {
    busy = false;
    rootEl()?.classList?.remove('analytics-3d-busy');
    suspendOffscreenPanels(false);
    syncAllCanvasBudgets();
    scheduleResize();
  });
}

function bindViewport() {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  window.addEventListener('resize', scheduleResize, { passive: true });
  window.addEventListener('orientationchange', scheduleResize, { passive: true });
  window.visualViewport?.addEventListener?.('resize', scheduleResize, { passive: true });
  document?.addEventListener?.('visibilitychange', () => {
    if (document.hidden) {
      busy = true;
      syncAllCanvasBudgets();
    } else {
      busy = false;
      syncAllCanvasBudgets();
      scheduleResize();
    }
  }, { passive: true });
}

export function installAnalyticsMobileRuntime() {
  if (installed || typeof window === 'undefined' || typeof document === 'undefined') return;
  installed = true;
  markViewportState();
  bindViewport();
  bind3dBusyState();
  installVisibilityObserver();
  installMutationObserver();
  scheduleResize();
}
