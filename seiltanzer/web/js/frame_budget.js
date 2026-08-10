// Shared browser frame-budget scheduler for analytical visuals.
//
// Goal: never let several independent Plotly/Canvas analytics updates land in the
// same main-thread slice. The newest job for each key wins, one job is executed
// after each paint boundary, and offscreen panel jobs are held until the panel is
// near the viewport. This changes scheduling only; analytical values and renderers
// stay untouched.

const jobs = new Map();
let framePending = false;
let jobSeq = 0;

const nowMs = () => (typeof performance !== 'undefined' && typeof performance.now === 'function')
  ? performance.now() : Date.now();

function pageHidden() {
  return Boolean(typeof document !== 'undefined' && document.hidden);
}

function analyticsGestureBusy() {
  if (typeof window === 'undefined') return false;
  if (window.__seiltanzer3dBusy) return true;
  return Boolean(typeof document !== 'undefined'
    && document.documentElement?.classList?.contains?.('analytics-3d-busy'));
}

function requestVisualFrame(fn) {
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(fn);
  return setTimeout(() => fn(nowMs()), 16);
}

function requestPostPaint(fn) {
  requestVisualFrame(() => setTimeout(fn, 0));
}

function scheduleDrain() {
  if (framePending || !jobs.size || pageHidden() || analyticsGestureBusy()) return;
  framePending = true;
  requestPostPaint(() => {
    framePending = false;
    if (pageHidden() || analyticsGestureBusy()) return;
    const next = jobs.entries().next();
    if (next.done) return;
    const [key, job] = next.value;
    jobs.delete(key);
    try { job(); } catch (err) { console.warn('frame-budget job failed', key, err); }
    // Important: another paint boundary separates the next heavy job.
    if (jobs.size) scheduleDrain();
  });
}

export function scheduleFrameTask(key, fn) {
  if (typeof fn !== 'function') return;
  const stableKey = key || `anonymous:${++jobSeq}`;
  jobs.set(stableKey, fn);
  scheduleDrain();
}

export function cancelFrameTask(key) {
  jobs.delete(key);
}

export function elementNearViewport(el, margin = 260) {
  if (!el || typeof el.getBoundingClientRect !== 'function' || typeof window === 'undefined') return true;
  const rect = el.getBoundingClientRect();
  const h = Number(window.innerHeight || document?.documentElement?.clientHeight || 0);
  const w = Number(window.innerWidth || document?.documentElement?.clientWidth || 0);
  if (!(h > 0) || !(w > 0)) return true;
  return rect.bottom >= -margin && rect.top <= h + margin
    && rect.right >= -margin && rect.left <= w + margin;
}

export function createLatestPanelTask(key, el, run, { margin = 260 } = {}) {
  let latestArgs = null;
  let destroyed = false;
  let observer = null;

  const enqueueIfVisible = () => {
    if (destroyed || !latestArgs || pageHidden() || analyticsGestureBusy()) return;
    if (!elementNearViewport(el, margin)) return;
    scheduleFrameTask(key, () => {
      if (destroyed || !latestArgs || !elementNearViewport(el, margin)) return;
      const args = latestArgs;
      latestArgs = null;
      run(...args);
      if (latestArgs) enqueueIfVisible();
    });
  };

  if (el && typeof IntersectionObserver !== 'undefined') {
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting || entry.intersectionRatio > 0)) enqueueIfVisible();
    }, { root: null, rootMargin: `${margin}px 0px ${margin}px 0px`, threshold: 0 });
    observer.observe(el);
  }

  const wake = () => enqueueIfVisible();
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('focus', wake, { passive: true });
    window.addEventListener('seiltanzer:3d-idle', wake);
  }
  if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    document.addEventListener('visibilitychange', wake, { passive: true });
  }

  return {
    schedule(...args) {
      latestArgs = args;
      enqueueIfVisible();
    },
    flushNow() {
      if (!latestArgs || destroyed || !elementNearViewport(el, margin)) return;
      const args = latestArgs;
      latestArgs = null;
      run(...args);
    },
    destroy() {
      destroyed = true;
      latestArgs = null;
      cancelFrameTask(key);
      observer?.disconnect?.();
      observer = null;
      if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
        window.removeEventListener('focus', wake);
        window.removeEventListener('seiltanzer:3d-idle', wake);
      }
      if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
        document.removeEventListener('visibilitychange', wake);
      }
    },
  };
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('seiltanzer:3d-idle', scheduleDrain);
  window.addEventListener('focus', scheduleDrain, { passive: true });
}
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) scheduleDrain();
  }, { passive: true });
}
