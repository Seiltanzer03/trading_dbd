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

function advancedPanels() {
  if (typeof document === 'undefined') return [];
  return ['#panel-gex-evol','#panel-macro-regime','#panel-wavelet','#panel-correlation']
    .map((sel) => document.querySelector(sel)).filter(Boolean);
}

function markViewportState() {
  if (typeof document === 'undefined') return;
  const mobile = isAnalyticsMobile();
  document.documentElement.classList.toggle('analytics-mobile', mobile);
  if (!mobile) document.documentElement.classList.remove('analytics-3d-busy');
}

function resizeAdvancedPlots() {
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
    if (typeof window.CustomEvent === 'function') {
      window.dispatchEvent(new CustomEvent('seiltanzer:analytics-mobile-resize'));
    }
  }, 90);
}

function installVisibilityObserver() {
  if (observer || typeof IntersectionObserver === 'undefined') return;
  observer = new IntersectionObserver((entries) => {
    for (const entry of entries) entry.target.classList.toggle('analytics-offscreen', !entry.isIntersecting);
  }, { rootMargin: '180px 0px', threshold: 0.01 });
  advancedPanels().forEach((panel) => observer.observe(panel));
}

function bind3dBusyState() {
  if (typeof window === 'undefined') return;
  window.addEventListener('seiltanzer:3d-busy', () => {
    if (!isAnalyticsMobile()) return;
    document.documentElement.classList.add('analytics-3d-busy');
  });
  window.addEventListener('seiltanzer:3d-idle', () => {
    document.documentElement.classList.remove('analytics-3d-busy');
    scheduleResize();
  });
}

function bindViewport() {
  if (typeof window === 'undefined') return;
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
