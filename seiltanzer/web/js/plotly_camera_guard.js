// Single-owner camera controller for Plotly gl3d.
//
// iOS Chrome uses WebKit and its native Plotly touch event order differs from
// desktop emulation.  The terminal used to have three competing owners of the
// camera: Plotly's native touch handler, the chart core's live restyles/reacts,
// and a delayed restore guard.  Under real iPhone load they raced and the last
// writer frequently restored the initial camera.
//
// This module now owns iOS touch rotation/zoom itself, pauses every external
// Plotly write while a gesture is active, injects the saved camera into every
// structural react/resize, and disables Plotly's automatic responsive listener
// for registered 3D charts.  Container resizing is handled by ResizeObserver.

const REGISTRY = new WeakMap();
const ALL_GUARDS = new Set();
let PATCHED_PLOTLY = null;
let ORIGINAL = null;
let GLOBAL_INTERACTIONS = 0;

function cloneValue(value) {
  if (value == null) return value;
  try { return JSON.parse(JSON.stringify(value)); } catch { return null; }
}

function setNested(target, path, value) {
  const parts = path.split('.').filter(Boolean);
  if (!parts.length) return;
  let node = target;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    if (!node[key] || typeof node[key] !== 'object') node[key] = {};
    node = node[key];
  }
  node[parts.at(-1)] = cloneValue(value);
}

export function cameraFromRelayout(baseCamera, update) {
  if (!update || typeof update !== 'object') return null;
  let next = cloneValue(baseCamera) || {};
  let touched = false;
  if (update['scene.camera'] && typeof update['scene.camera'] === 'object') {
    next = cloneValue(update['scene.camera']) || next;
    touched = true;
  }
  for (const [key, value] of Object.entries(update)) {
    if (!key.startsWith('scene.camera.')) continue;
    setNested(next, key.slice('scene.camera.'.length), value);
    touched = true;
  }
  return touched ? next : null;
}

function equalValue(a, b, eps = 1e-7) {
  if (typeof a === 'number' || typeof b === 'number') {
    return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= eps;
  }
  if (a == null || b == null) return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length
      && a.every((value, index) => equalValue(value, b[index], eps));
  }
  if (typeof a === 'object' || typeof b === 'object') {
    if (typeof a !== 'object' || typeof b !== 'object') return false;
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    return [...keys].every((key) => equalValue(a[key], b[key], eps));
  }
  return a === b;
}

function isIOSWebKit() {
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  return /iPad|iPhone|iPod/i.test(ua)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}

function nowMs() {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

function resolved(value) {
  return Promise.resolve(value);
}

function publishGlobalBusy() {
  if (typeof window === 'undefined') return;
  window.__seiltanzer3dBusy = GLOBAL_INTERACTIONS > 0;
  window.dispatchEvent(new CustomEvent(
    GLOBAL_INTERACTIONS > 0 ? 'seiltanzer:3d-busy' : 'seiltanzer:3d-idle',
    { detail: { active: GLOBAL_INTERACTIONS } },
  ));
  if (GLOBAL_INTERACTIONS === 0) {
    for (const guard of ALL_GUARDS) guard.flushDeferred();
  }
}

function prepareConfig(config) {
  // responsive:true installs Plotly's own window resize handler. On iOS the
  // browser chrome changes visualViewport height while touching the page, so
  // that handler can rebuild gl3d and restore INIT_CAM mid-gesture.
  return { ...(config || {}), responsive: false };
}

function installPlotlyPatch() {
  if (typeof window === 'undefined' || !window.Plotly) return false;
  const P = window.Plotly;
  if (PATCHED_PLOTLY === P) return true;

  ORIGINAL = {
    newPlot: P.newPlot.bind(P),
    react: P.react.bind(P),
    restyle: P.restyle.bind(P),
    relayout: P.relayout.bind(P),
    resize: P.Plots?.resize?.bind(P.Plots),
    purge: P.purge?.bind(P),
  };

  P.newPlot = (el, traces, layout, config) => {
    const guard = REGISTRY.get(el);
    const nextLayout = guard ? guard.prepareLayout(layout) : layout;
    const nextConfig = guard ? prepareConfig(config) : config;
    return resolved(ORIGINAL.newPlot(el, traces, nextLayout, nextConfig))
      .then((value) => { guard?.afterPlotWrite(); return value; });
  };

  P.react = (el, traces, layout, config) => {
    const guard = REGISTRY.get(el);
    const run = () => resolved(ORIGINAL.react(
      el,
      traces,
      guard ? guard.prepareLayout(layout) : layout,
      guard ? prepareConfig(config) : config,
    )).then((value) => { guard?.afterPlotWrite(); return value; });
    if (guard && guard.isProtected() && !guard.isInternalWrite()) {
      return guard.deferStructuralWrite(run);
    }
    return run();
  };

  P.restyle = (el, update, traces) => {
    const guard = REGISTRY.get(el);
    if (guard && !guard.isInternalWrite()) {
      if (guard.isProtected()) return resolved(el);
      if (!guard.allowRestyle(traces)) return resolved(el);
    }
    return ORIGINAL.restyle(el, update, traces);
  };

  P.relayout = (el, update) => {
    const guard = REGISTRY.get(el);
    if (guard && !guard.isInternalWrite()) {
      const camera = cameraFromRelayout(guard.getSavedCamera(), update);
      if (camera) guard.rememberExternalCamera(camera);
      else if (guard.isProtected()) return resolved(el);
    }
    return resolved(ORIGINAL.relayout(el, update))
      .then((value) => { guard?.afterPlotWrite(); return value; });
  };

  if (ORIGINAL.resize && P.Plots) {
    P.Plots.resize = (el) => {
      const guard = REGISTRY.get(el);
      const run = () => {
        guard?.pinLayoutCamera();
        return resolved(ORIGINAL.resize(el))
          .then((value) => { guard?.afterPlotWrite(); return value; });
      };
      if (guard && guard.isProtected() && !guard.isInternalWrite()) {
        return guard.deferResize(run);
      }
      return run();
    };
  }

  if (ORIGINAL.purge) {
    P.purge = (el) => {
      REGISTRY.get(el)?.destroy();
      return ORIGINAL.purge(el);
    };
  }

  PATCHED_PLOTLY = P;
  return true;
}

function cameraVector(camera) {
  const center = camera?.center || { x: 0, y: 0, z: 0 };
  const eye = camera?.eye || { x: 1.25, y: 1.25, z: 1.25 };
  const x = Number(eye.x || 0) - Number(center.x || 0);
  const y = Number(eye.y || 0) - Number(center.y || 0);
  const z = Number(eye.z || 0) - Number(center.z || 0);
  return { center, eye, x, y, z, radius: Math.max(Math.hypot(x, y, z), 0.05) };
}

function rotateCamera(camera, dx, dy, width, height) {
  const base = cloneValue(camera) || {};
  const v = cameraVector(base);
  let azimuth = Math.atan2(v.y, v.x);
  let elevation = Math.asin(Math.max(-1, Math.min(1, v.z / v.radius)));
  azimuth -= (dx / Math.max(width, 180)) * Math.PI * 2.15;
  elevation += (dy / Math.max(height, 180)) * Math.PI * 1.25;
  elevation = Math.max(-1.42, Math.min(1.42, elevation));
  const planar = v.radius * Math.cos(elevation);
  base.center = cloneValue(v.center);
  base.eye = {
    x: Number(v.center.x || 0) + planar * Math.cos(azimuth),
    y: Number(v.center.y || 0) + planar * Math.sin(azimuth),
    z: Number(v.center.z || 0) + v.radius * Math.sin(elevation),
  };
  if (!base.up) base.up = { x: 0, y: 0, z: 1 };
  return base;
}

function zoomCamera(camera, ratio) {
  const base = cloneValue(camera) || {};
  const v = cameraVector(base);
  const radius = Math.max(0.28, Math.min(7.5, v.radius * ratio));
  const scale = radius / v.radius;
  base.center = cloneValue(v.center);
  base.eye = {
    x: Number(v.center.x || 0) + v.x * scale,
    y: Number(v.center.y || 0) + v.y * scale,
    z: Number(v.center.z || 0) + v.z * scale,
  };
  if (!base.up) base.up = { x: 0, y: 0, z: 1 };
  return base;
}

function touchPoint(touch) {
  return { x: Number(touch?.clientX || 0), y: Number(touch?.clientY || 0) };
}

function touchDistance(touches) {
  if (!touches || touches.length < 2) return 0;
  const a = touchPoint(touches[0]);
  const b = touchPoint(touches[1]);
  return Math.max(Math.hypot(a.x - b.x, a.y - b.y), 1);
}

export function createPlotlyCameraGuard(el, initialCamera) {
  const homeCamera = cloneValue(initialCamera) || {};
  let savedCamera = cloneValue(homeCamera);
  let state = 'idle';
  let internalDepth = 0;
  let destroyed = false;
  let listenersOn = false;
  let localBusyPublished = false;
  let settleTimer = null;
  let verifyTimer = null;
  let resizeTimer = null;
  let resizeObserver = null;
  let lastSize = null;
  let deferredStructural = null;
  let deferredResize = null;
  let touchSession = null;
  let cameraWriteQueued = false;
  let pendingCamera = null;
  let lastCameraWriteAt = 0;
  const restyleTimes = new Map();
  const idleCallbacks = new Set();
  const removers = [];
  const customIOS = isIOSWebKit();

  const guard = {
    arm,
    beforeWrite: () => { arm(); return !isProtected(); },
    afterWrite: arm,
    prepareLayout,
    pinLayoutCamera,
    afterPlotWrite,
    isProtected,
    isInternalWrite: () => internalDepth > 0,
    getSavedCamera: () => cloneValue(savedCamera),
    getState: () => state,
    rememberExternalCamera,
    allowRestyle,
    deferStructuralWrite,
    deferResize,
    flushDeferred,
    onIdle,
    destroy,
    usesCustomIOSTouch: () => customIOS,
  };

  if (el) {
    REGISTRY.set(el, guard);
    ALL_GUARDS.add(guard);
  }
  installPlotlyPatch();
  installDomListeners();
  installResizeObserver();

  function addDom(target, name, handler, options) {
    if (!target?.addEventListener) return;
    target.addEventListener(name, handler, options);
    removers.push(() => target.removeEventListener?.(name, handler, options));
  }

  function beginInteraction() {
    if (destroyed) return;
    clearTimeout(settleTimer);
    settleTimer = null;
    if (state === 'idle') {
      state = 'interacting';
      if (!localBusyPublished) {
        localBusyPublished = true;
        GLOBAL_INTERACTIONS += 1;
        publishGlobalBusy();
      }
    } else {
      state = 'interacting';
    }
  }

  function enterSettling(delay = 240) {
    if (destroyed) return;
    state = 'settling';
    clearTimeout(settleTimer);
    settleTimer = setTimeout(finishInteraction, delay);
  }

  function finishInteraction() {
    if (destroyed) return;
    state = 'idle';
    touchSession = null;
    if (localBusyPublished) {
      localBusyPublished = false;
      GLOBAL_INTERACTIONS = Math.max(0, GLOBAL_INTERACTIONS - 1);
      publishGlobalBusy();
    } else {
      flushDeferred();
    }
    verifyCamera(0);
    for (const callback of [...idleCallbacks]) callback();
  }

  function isProtected() {
    return state !== 'idle' || GLOBAL_INTERACTIONS > 0;
  }

  function currentCamera() {
    return cloneValue(el?._fullLayout?.scene?.camera);
  }

  function rememberExternalCamera(camera) {
    if (!camera) return;
    savedCamera = cloneValue(camera);
  }

  function rememberGestureCamera(camera) {
    if (!camera) return;
    // Plotly/WebKit can publish the chart's initial camera after the user's
    // final relayout but before the gesture settles.  Treat that exact HOME
    // value as a stale responsive rebuild when a newer user camera is owned.
    // An intentional toolbar HOME remains valid because it first calls
    // rememberExternalCamera(home), making savedCamera equal to homeCamera.
    if (isProtected() && equalValue(camera, homeCamera)
        && !equalValue(savedCamera, homeCamera)) return;
    savedCamera = cloneValue(camera);
  }

  function prepareLayout(layout) {
    const next = layout || {};
    if (!next.scene) next.scene = {};
    next.scene.camera = cloneValue(savedCamera);
    return next;
  }

  function pinLayoutCamera() {
    if (!el) return;
    if (!el.layout) el.layout = {};
    if (!el.layout.scene) el.layout.scene = {};
    el.layout.scene.camera = cloneValue(savedCamera);
  }

  function afterPlotWrite() {
    arm();
    verifyCamera(customIOS ? 80 : 20);
  }

  function verifyCamera(delay = 40) {
    clearTimeout(verifyTimer);
    verifyTimer = setTimeout(() => {
      if (destroyed || isProtected() || !el?._fullLayout?.scene) return;
      const current = currentCamera();
      if (!current || equalValue(current, savedCamera)) return;
      writeCamera(savedCamera);
    }, delay);
  }

  function writeCamera(camera) {
    if (destroyed || !window.Plotly || !el?._fullLayout?.scene) return resolved(el);
    savedCamera = cloneValue(camera);
    internalDepth += 1;
    pinLayoutCamera();
    let result;
    try {
      result = window.Plotly.relayout(el, { 'scene.camera': cloneValue(savedCamera) });
    } catch (error) {
      internalDepth = Math.max(0, internalDepth - 1);
      return Promise.reject(error);
    }
    return resolved(result).finally(() => {
      setTimeout(() => { internalDepth = Math.max(0, internalDepth - 1); }, 0);
    });
  }

  function queueCamera(camera) {
    pendingCamera = cloneValue(camera);
    savedCamera = cloneValue(camera);
    if (cameraWriteQueued) return;
    cameraWriteQueued = true;
    const run = () => {
      const elapsed = nowMs() - lastCameraWriteAt;
      if (customIOS && elapsed < 34) {
        setTimeout(run, 34 - elapsed);
        return;
      }
      cameraWriteQueued = false;
      const next = pendingCamera;
      pendingCamera = null;
      if (!next) return;
      lastCameraWriteAt = nowMs();
      writeCamera(next).catch(() => {});
    };
    requestAnimationFrame(run);
  }

  function restyleKey(traces) {
    if (Array.isArray(traces)) return traces.join(',');
    return String(traces ?? 'all');
  }

  function allowRestyle(traces) {
    if (!customIOS) return true;
    const key = restyleKey(traces);
    const now = nowMs();
    const previous = restyleTimes.get(key) || -Infinity;
    // Real iPhones were spending most of the main thread in gl3d restyle. Cap
    // every independent trace group to 20Hz; desktop remains unchanged.
    if (now - previous < 50) return false;
    restyleTimes.set(key, now);
    return true;
  }

  function deferStructuralWrite(task) {
    return new Promise((resolve, reject) => {
      if (!deferredStructural) deferredStructural = { task, waiters: [] };
      deferredStructural.task = task;
      deferredStructural.waiters.push({ resolve, reject });
    });
  }

  function deferResize(task) {
    return new Promise((resolve, reject) => {
      if (!deferredResize) deferredResize = { task, waiters: [] };
      deferredResize.task = task;
      deferredResize.waiters.push({ resolve, reject });
    });
  }

  function runDeferred(slotName) {
    const slot = slotName === 'structural' ? deferredStructural : deferredResize;
    if (!slot) return;
    if (slotName === 'structural') deferredStructural = null;
    else deferredResize = null;
    resolved(slot.task()).then(
      (value) => slot.waiters.forEach((w) => w.resolve(value)),
      (error) => slot.waiters.forEach((w) => w.reject(error)),
    );
  }

  function flushDeferred() {
    if (destroyed || isProtected()) return;
    runDeferred('structural');
    runDeferred('resize');
    verifyCamera(70);
  }

  function onIdle(callback) {
    idleCallbacks.add(callback);
    return () => idleCallbacks.delete(callback);
  }

  function updateTouchSession(touches) {
    const camera = currentCamera() || savedCamera;
    if (touches.length >= 2) {
      touchSession = {
        mode: 'pinch',
        camera: cloneValue(camera),
        distance: touchDistance(touches),
      };
    } else if (touches.length === 1) {
      touchSession = {
        mode: 'rotate',
        camera: cloneValue(camera),
        point: touchPoint(touches[0]),
      };
    } else {
      touchSession = null;
    }
  }

  function ownTouchEvent(event) {
    event.preventDefault?.();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
  }

  function iosTouchStart(event) {
    ownTouchEvent(event);
    beginInteraction();
    updateTouchSession(event.touches || []);
  }

  function iosTouchMove(event) {
    ownTouchEvent(event);
    const touches = event.touches || [];
    if (!touchSession) updateTouchSession(touches);
    if (!touchSession) return;
    const rect = el.getBoundingClientRect?.() || { width: el.clientWidth, height: el.clientHeight };
    if (touchSession.mode === 'pinch' && touches.length >= 2) {
      const distance = touchDistance(touches);
      queueCamera(zoomCamera(touchSession.camera, touchSession.distance / distance));
    } else if (touchSession.mode === 'rotate' && touches.length === 1) {
      const point = touchPoint(touches[0]);
      queueCamera(rotateCamera(
        touchSession.camera,
        point.x - touchSession.point.x,
        point.y - touchSession.point.y,
        rect.width || 320,
        rect.height || 430,
      ));
    }
  }

  function iosTouchEnd(event) {
    ownTouchEvent(event);
    const touches = event.touches || [];
    if (touches.length) {
      updateTouchSession(touches);
      return;
    }
    enterSettling(220);
  }

  function blockNativeTouchPointer(event) {
    if (event.pointerType && event.pointerType !== 'touch') return;
    ownTouchEvent(event);
  }

  function nativeBegin() { beginInteraction(); }
  function nativeEnd() { enterSettling(260); }
  function nativeTouchEnd(event) {
    if ((event?.touches || []).length) return;
    enterSettling(260);
  }

  function installDomListeners() {
    if (!el || typeof window === 'undefined') return;
    el.style.touchAction = 'none';
    el.style.overscrollBehavior = 'contain';
    el.style.webkitUserSelect = 'none';
    el.style.webkitTouchCallout = 'none';

    if (customIOS) {
      // Installed before Plotly.newPlot, capture phase, non-passive: iOS touch
      // never reaches Plotly's native gl3d handler. We therefore avoid the
      // browser-specific pointerup/relayout race completely.
      addDom(el, 'touchstart', iosTouchStart, { passive: false, capture: true });
      addDom(el, 'touchmove', iosTouchMove, { passive: false, capture: true });
      addDom(el, 'touchend', iosTouchEnd, { passive: false, capture: true });
      addDom(el, 'touchcancel', iosTouchEnd, { passive: false, capture: true });
      addDom(el, 'pointerdown', blockNativeTouchPointer, true);
      addDom(el, 'pointermove', blockNativeTouchPointer, true);
      addDom(el, 'pointerup', blockNativeTouchPointer, true);
      addDom(el, 'pointercancel', blockNativeTouchPointer, true);
    } else {
      addDom(el, 'pointerdown', nativeBegin, true);
      addDom(window, 'pointerup', nativeEnd, true);
      addDom(window, 'pointercancel', nativeEnd, true);
      addDom(el, 'touchstart', nativeBegin, { passive: true, capture: true });
      addDom(window, 'touchend', nativeTouchEnd, { passive: true, capture: true });
      addDom(window, 'touchcancel', nativeTouchEnd, { passive: true, capture: true });
      addDom(el, 'wheel', nativeBegin, { passive: true, capture: true });
      addDom(el, 'wheel', () => enterSettling(300), { passive: true });
    }
  }

  function stylePlotlyChildren() {
    if (!el?.querySelectorAll) return;
    for (const node of el.querySelectorAll('canvas,.gl-container,.svg-container,.plot-container')) {
      if (!node.style) continue;
      node.style.touchAction = 'none';
      node.style.overscrollBehavior = 'contain';
      node.style.webkitUserSelect = 'none';
    }
  }

  function arm() {
    if (destroyed) return;
    installPlotlyPatch();
    stylePlotlyChildren();
    if (listenersOn || !el?.on) return;
    listenersOn = true;
    el.on('plotly_relayouting', (update) => {
      if (internalDepth > 0 || customIOS) return;
      beginInteraction();
      const camera = cameraFromRelayout(savedCamera, update);
      rememberGestureCamera(camera);
    });
    el.on('plotly_relayout', (update) => {
      if (internalDepth > 0 || customIOS) return;
      const camera = cameraFromRelayout(savedCamera, update);
      rememberGestureCamera(camera);
      enterSettling(220);
    });
    el.on('plotly_afterplot', () => {
      stylePlotlyChildren();
      if (!isProtected()) verifyCamera(50);
    });
  }

  function installResizeObserver() {
    if (!el || typeof ResizeObserver === 'undefined') return;
    resizeObserver = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      const next = { width: Math.round(box.width), height: Math.round(box.height) };
      if (!lastSize) { lastSize = next; return; }
      if (Math.abs(next.width - lastSize.width) < 3
          && Math.abs(next.height - lastSize.height) < 3) return;
      lastSize = next;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (!window.Plotly?.Plots?.resize || !el?._fullLayout) return;
        window.Plotly.Plots.resize(el);
      }, 140);
    });
    resizeObserver.observe(el);
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    clearTimeout(settleTimer);
    clearTimeout(verifyTimer);
    clearTimeout(resizeTimer);
    resizeObserver?.disconnect();
    for (const remove of removers) remove();
    if (localBusyPublished) {
      localBusyPublished = false;
      GLOBAL_INTERACTIONS = Math.max(0, GLOBAL_INTERACTIONS - 1);
      publishGlobalBusy();
    }
    REGISTRY.delete(el);
    ALL_GUARDS.delete(guard);
  }

  return guard;
}
