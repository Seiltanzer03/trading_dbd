// Persistent 3D camera state for Plotly mobile interactions.
//
// Camera changes are accepted only while a real pointer/touch/wheel gesture is
// active. Programmatic react/resize relayouts must never overwrite the user's
// last camera. That is the mobile failure mode which returns a scene to INIT.

function cloneValue(value) {
  if (value == null) return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return null;
  }
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
  node[parts[parts.length - 1]] = cloneValue(value);
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

function equalValue(a, b, eps = 1e-9) {
  if (typeof a === 'number' || typeof b === 'number') {
    return Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= eps;
  }
  if (a == null || b == null) return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((value, index) => equalValue(value, b[index], eps));
  }
  if (typeof a === 'object' || typeof b === 'object') {
    if (typeof a !== 'object' || typeof b !== 'object') return false;
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const key of keys) {
      if (!equalValue(a[key], b[key], eps)) return false;
    }
    return true;
  }
  return a === b;
}

export function createPlotlyCameraGuard(el, initialCamera) {
  let savedCamera = cloneValue(initialCamera) || {};
  let plotlyListenersOn = false;
  let suppressCameraEvents = 0;
  let wheelGestureUntil = 0;
  let destroyed = false;
  const activePointers = new Set();
  const timers = new Set();
  const removers = [];

  const clock = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
  const pointerKey = (event) => event?.pointerId ?? 'primary';
  const currentCamera = () => cloneValue(el?._fullLayout?.scene?.camera);
  const plotlyReady = () => !!(el && typeof window !== 'undefined'
    && window.Plotly && el?._fullLayout?.scene);

  function addDom(target, name, handler, options) {
    if (!target?.addEventListener) return;
    target.addEventListener(name, handler, options);
    if (target.removeEventListener) {
      removers.push(() => target.removeEventListener(name, handler, options));
    }
  }

  function clearTimers() {
    for (const timer of timers) clearTimeout(timer);
    timers.clear();
  }

  function userGestureActive() {
    return activePointers.size > 0 || clock() <= wheelGestureUntil;
  }

  function captureCurrentBeforeGesture() {
    if (suppressCameraEvents > 0) return;
    const camera = currentCamera();
    if (camera) savedCamera = camera;
  }

  function captureRelayout(update) {
    if (destroyed || suppressCameraEvents > 0 || !userGestureActive()) return;
    const camera = cameraFromRelayout(savedCamera, update);
    if (camera) savedCamera = camera;
  }

  function restoreNow() {
    if (destroyed || activePointers.size > 0 || !plotlyReady() || !savedCamera) return;
    const current = currentCamera();
    if (current && equalValue(current, savedCamera)) return;

    suppressCameraEvents += 1;
    let write;
    try {
      write = window.Plotly.relayout(el, { 'scene.camera': cloneValue(savedCamera) });
    } catch {
      suppressCameraEvents = Math.max(0, suppressCameraEvents - 1);
      return;
    }
    Promise.resolve(write).finally(() => {
      setTimeout(() => {
        suppressCameraEvents = Math.max(0, suppressCameraEvents - 1);
      }, 0);
    });
  }

  function scheduleRestore(delays = [0, 80, 220, 500]) {
    if (destroyed) return;
    for (const delay of delays) {
      const timer = setTimeout(() => {
        timers.delete(timer);
        ensurePlotlyListeners();
        restoreNow();
      }, delay);
      timers.add(timer);
    }
  }

  function ensurePlotlyListeners() {
    if (plotlyListenersOn || !el?.on) return;
    plotlyListenersOn = true;
    el.on('plotly_relayouting', captureRelayout);
    el.on('plotly_relayout', captureRelayout);
    el.on('plotly_afterplot', () => {
      if (suppressCameraEvents > 0 || activePointers.size > 0) return;
      scheduleRestore([0, 90, 260]);
    });
  }

  function beginPointer(event) {
    clearTimers();
    activePointers.add(pointerKey(event));
    wheelGestureUntil = 0;
    captureCurrentBeforeGesture();
  }

  function endPointer(event) {
    activePointers.delete(pointerKey(event));
    if (activePointers.size > 0) return;
    // Never read _fullLayout here. On mobile it may already contain INIT_CAM.
    // The last Plotly relayouting payload remains the authoritative user view.
    scheduleRestore([20, 120, 280, 560, 1000]);
  }

  function cancelPointers() {
    activePointers.clear();
    scheduleRestore([20, 120, 280, 560, 1000]);
  }

  function beginWheel() {
    clearTimers();
    wheelGestureUntil = clock() + 260;
    captureCurrentBeforeGesture();
    scheduleRestore([320, 560, 900]);
  }

  if (el && typeof window !== 'undefined') {
    if (window.PointerEvent) {
      addDom(el, 'pointerdown', beginPointer, true);
      addDom(window, 'pointerup', endPointer, true);
      addDom(window, 'pointercancel', cancelPointers, true);
    } else {
      addDom(el, 'mousedown', beginPointer, true);
      addDom(el, 'touchstart', beginPointer, { passive: true, capture: true });
      addDom(window, 'mouseup', endPointer, true);
      addDom(window, 'touchend', cancelPointers, { passive: true, capture: true });
      addDom(window, 'touchcancel', cancelPointers, { passive: true, capture: true });
    }
    addDom(el, 'wheel', beginWheel, { passive: true, capture: true });
    addDom(el, 'dblclick', beginWheel, true);

    const viewportChanged = () => {
      // Never capture during resize: Plotly/browser can already expose INIT_CAM.
      scheduleRestore([0, 80, 220, 500, 900]);
    };
    addDom(window, 'resize', viewportChanged);
    addDom(window, 'orientationchange', viewportChanged);
    addDom(window.visualViewport, 'resize', viewportChanged);
  }

  function arm() {
    ensurePlotlyListeners();
    scheduleRestore([0, 50, 160, 360, 720]);
  }

  function destroy() {
    destroyed = true;
    clearTimers();
    activePointers.clear();
    for (const remove of removers) remove();
  }

  return {
    arm,
    beforeWrite: ensurePlotlyListeners,
    afterWrite: arm,
    restore: () => scheduleRestore(),
    destroy,
    getSavedCamera: () => cloneValue(savedCamera),
  };
}
