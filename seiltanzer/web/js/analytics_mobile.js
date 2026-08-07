const MOBILE_QUERY = '(max-width: 760px)';
let installed = false;
let resizeTimer = null;
let observer = null;

export function isAnalyticsMobile() {
  if (typeof window === 'undefined') return false;
  if (typeof window.matchMedia === 'function') return window.matchMedia(MOBILE_QUERY).matches;
  return Number(window.innerWidth || 9999) <= 760;
}

export function analyticsMobileDpr() {
  if (typeof window === 'undefined') return 1;
  return Math.min(Number(window.devicePixelRatio || 1), isAnalyticsMobile() ? 1.5 : 2);
}

function rootElement() {
  if (typeof document === 'undefined') return null;
  return document.documentElement || null;
}

function advancedPanels() {
  if (typeof document === 'undefined' || typeof document.querySelector !== 'function') return [];
  return ['#panel-gex-evol','#panel-macro-regime','#panel-wavelet','#panel-correlation']
    .map((sel) => document.querySelector(sel)).filter(Boolean);
}

function markViewportState() {
  const root = rootElement();
  if (!root?.classList) return;
  const mobile = isAnalyticsMobile();
  root.classList.toggle('analytics-mobile', mobile);
  if (!mobile) root.classList.remove('analytics-3d-busy');
}

function resizeAdvancedPlots() {
  if (typeof window === 'undefined' || !window.Plotly?.Plots?.resize
      || typeof document?.querySelectorAll !== 'function') return;
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
    if (typeof window?.CustomEvent === 'function' && typeof window?.dispatchEvent === 'function') {
      window.dispatchEvent(new window.CustomEvent('seiltanzer:analytics-mobile-resize'));
    }
  }, 90);
}

function installVisibilityObserver() {
  if (observer || typeof IntersectionObserver === 'undefined') return;
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) entry.target?.classList?.toggle('analytics-offscreen', !entry.isIntersecting);
  }, { rootMargin: '180px 0px', threshold: 0.01 });
  advancedPanels().forEach((panel) => observer.observe(panel));
}

function suspendOffscreenPanels(suspend) {
  if (!isAnalyticsMobile()) return;
  for (const panel of advancedPanels()) {
    if (!panel.classList?.contains?.('analytics-offscreen')) continue;
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

export function installAnalyticsMobileRuntime() {
  if (installed || typeof window === 'undefined' || typeof document === 'undefined') return;
  installed = true;
  markViewportState();
  bindViewport();
  bind3dBusyState();
  installVisibilityObserver();
  scheduleResize();
}
