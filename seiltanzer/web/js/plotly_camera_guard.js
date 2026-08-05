// Persistent 3D camera state for Plotly mobile interactions.
//
// Real mobile Plotly gestures do not have a stable event order: the DOM
// pointer/touch end can arrive before the final plotly_relayout payload, and a
// responsive afterplot can run in between.  The guard therefore uses an
// explicit interaction state machine instead of treating pointerup as the end
// of camera ownership.

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

const IDLE = 'idle';
const INTERACTING = 'interacting';
const SETTLING = 'settling';
const SETTLE_MS = 420;
const POST_EVENT_QUIET_MS = 90;

export function createPlotlyCameraGuard(el, initialCamera) {
  let savedCamera = cloneValue(initialCamera) || {};
  let gestureStartCamera = cloneValue(savedCamera);
  let plotlyListenersOn = false;
  let suppressCameraEvents = 0;
  let wheelGestureUntil = 0;
  let settleUntil = 0;
  let lastUserCameraAt = 0;
  let gestureSawCamera = false;
  let gestureSawPayload = false;
  let state = IDLE;
  let destroyed = false;
  let settleGeneration = 0;
  let activeTouches = 0;
  const activePointers = new Set();
  const restoreTimers = new Set();
  const settleTimers = new Set();
  const removers = [];
  const originalInlineStyle = el?.style ? {
    touchAction: el.style.touchAction,
    overscrollBehavior: el.style.overscrollBehavior,
    webkitUserSelect: el.style.webkitUserSelect,
    webkitTouchCallout: el.style.webkitTouchCallout,
  } : null;

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

  function clearTimerSet(timerSet) {
    for (const timer of timerSet) clearTimeout(timer);
    timerSet.clear();
  }

  function clearRestoreTimers() {
    clearTimerSet(restoreTimers);
  }

  function clearSettleTimers() {
    clearTimerSet(settleTimers);
  }

  function domGestureActive() {
    return activePointers.size > 0 || activeTouches > 0 || clock() <= wheelGestureUntil;
  }

  function protectedPhase() {
    return state !== IDLE || domGestureActive() || clock() <= settleUntil;
  }

  function rememberCamera(camera, fromPayload = false) {
    if (!camera) return false;
    savedCamera = cloneValue(camera);
    lastUserCameraAt = clock();
    gestureSawCamera = true;
    if (fromPayload) gestureSawPayload = true;
    return true;
  }

  function captureCurrentBeforeGesture() {
    if (suppressCameraEvents > 0) return;
    const camera = currentCamera();
    if (camera) {
      savedCamera = camera;
      gestureStartCamera = cloneValue(camera);
    } else {
      gestureStartCamera = cloneValue(savedCamera);
    }
  }

  function captureRenderedCamera() {
    if (destroyed || suppressCameraEvents > 0) return false;
    const camera = currentCamera();
    if (!camera) return false;
    // A rendered sample is only a fallback for engines which emit no camera
    // payload at all. Once Plotly supplied relayouting/relayout data, that data
    // is safer than _fullLayout, which responsive WebGL code may reset to INIT.
    if (!protectedPhase() || gestureSawPayload) return false;
    // Do not turn an unrelated responsive INIT reset into a user camera when no
    // Plotly camera event and no visible camera movement happened in the gesture.
    if (!gestureSawCamera && equalValue(camera, gestureStartCamera)) return false;
    return rememberCamera(camera);
  }

  function captureRelayout(update, source) {
    if (destroyed || suppressCameraEvents > 0) return;
    const authoritative = source === 'relayouting' || protectedPhase();
    if (!authoritative) return;
    const camera = cameraFromRelayout(savedCamera, update);
    if (!camera) return;
    // After pointerup, reject the two stale cameras produced by responsive
    // redraws: the scene's initial camera and the camera from gesture start.
    // The real final user camera moves away from at least one of these values.
    if (source === 'relayout' && protectedPhase() && gestureSawPayload
        && !equalValue(savedCamera, gestureStartCamera)
        && (equalValue(camera, gestureStartCamera)
          || equalValue(camera, initialCamera))) return;
    rememberCamera(camera, true);
    clearRestoreTimers();
    // A final plotly_relayout commonly arrives after pointerup on mobile. Keep
    // the settling window open from the last authoritative camera event.
    if (!domGestureActive()) {
      state = SETTLING;
      settleUntil = Math.max(settleUntil, clock() + SETTLE_MS);
      scheduleSettleFinish();
    }
  }

  function restoreNow() {
    if (destroyed || protectedPhase() || !plotlyReady() || !savedCamera) return;
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

  function scheduleRestore(delays = [80, 240, 600]) {
    if (destroyed) return;
    for (const delay of delays) {
      const timer = setTimeout(() => {
        restoreTimers.delete(timer);
        ensurePlotlyListeners();
        restoreNow();
      }, delay);
      restoreTimers.add(timer);
    }
  }

  function scheduleRenderedSamples(generation) {
    for (const delay of [0, 24, 72, 160, 300]) {
      const timer = setTimeout(() => {
        settleTimers.delete(timer);
        if (destroyed || generation !== settleGeneration || state === IDLE) return;
        captureRenderedCamera();
      }, delay);
      settleTimers.add(timer);
    }
  }

  function finishSettling(generation) {
    if (destroyed || generation !== settleGeneration) return;
    const now = clock();
    const quietRemaining = Math.max(0, POST_EVENT_QUIET_MS - (now - lastUserCameraAt));
    const settleRemaining = Math.max(0, settleUntil - now);
    if (domGestureActive() || quietRemaining > 0 || settleRemaining > 0) {
      const delay = Math.max(24, Math.min(120, Math.max(quietRemaining, settleRemaining)));
      const timer = setTimeout(() => {
        settleTimers.delete(timer);
        finishSettling(generation);
      }, delay);
      settleTimers.add(timer);
      return;
    }
    captureRenderedCamera();
    state = IDLE;
    settleUntil = 0;
    scheduleRestore([120, 360, 800]);
  }

  function scheduleSettleFinish() {
    const generation = settleGeneration;
    const timer = setTimeout(() => {
      settleTimers.delete(timer);
      finishSettling(generation);
    }, 48);
    settleTimers.add(timer);
  }

  function beginGesture() {
    clearRestoreTimers();
    clearSettleTimers();
    settleGeneration += 1;
    state = INTERACTING;
    settleUntil = 0;
    gestureSawCamera = false;
    gestureSawPayload = false;
    captureCurrentBeforeGesture();
  }

  function enterSettling() {
    if (destroyed || domGestureActive()) return;
    clearRestoreTimers();
    clearSettleTimers();
    state = SETTLING;
    settleUntil = clock() + SETTLE_MS;
    const generation = settleGeneration;
    scheduleRenderedSamples(generation);
    scheduleSettleFinish();
  }

  function ensurePlotlyListeners() {
    if (plotlyListenersOn || !el?.on) return;
    plotlyListenersOn = true;
    el.on('plotly_relayouting', (update) => captureRelayout(update, 'relayouting'));
    el.on('plotly_relayout', (update) => captureRelayout(update, 'relayout'));
    el.on('plotly_afterplot', () => {
      if (suppressCameraEvents > 0) return;
      if (protectedPhase()) {
        const generation = settleGeneration;
        const timer = setTimeout(() => {
          settleTimers.delete(timer);
          if (generation === settleGeneration) captureRenderedCamera();
        }, 0);
        settleTimers.add(timer);
        return;
      }
      scheduleRestore([90, 260, 620]);
    });
  }

  function beginPointer(event) {
    const wasActive = domGestureActive();
    activePointers.add(pointerKey(event));
    wheelGestureUntil = 0;
    if (!wasActive) beginGesture();
  }

  function endPointer(event) {
    activePointers.delete(pointerKey(event));
    enterSettling();
  }

  function cancelPointers() {
    activePointers.clear();
    enterSettling();
  }

  function beginTouch(event) {
    const count = Number(event?.touches?.length);
    const wasActive = domGestureActive();
    activeTouches = Number.isFinite(count) ? count : Math.max(1, activeTouches);
    if (!wasActive) beginGesture();
  }

  function endTouch(event) {
    const count = Number(event?.touches?.length);
    activeTouches = Number.isFinite(count) ? count : 0;
    enterSettling();
  }

  function beginWheel() {
    const wasActive = domGestureActive();
    if (!wasActive) beginGesture();
    wheelGestureUntil = clock() + 280;
    const generation = settleGeneration;
    const timer = setTimeout(() => {
      settleTimers.delete(timer);
      if (generation !== settleGeneration) return;
      enterSettling();
    }, 320);
    settleTimers.add(timer);
  }

  if (el && typeof window !== 'undefined') {
    // Reserve the WebGL surface for Plotly. Without touch-action:none the
    // browser may own two-finger pan/pinch before Plotly sees the gesture.
    if (el.style) {
      el.style.touchAction = 'none';
      el.style.overscrollBehavior = 'contain';
      el.style.webkitUserSelect = 'none';
      el.style.webkitTouchCallout = 'none';
    }
    if (window.PointerEvent) {
      addDom(el, 'pointerdown', beginPointer, true);
      addDom(window, 'pointerup', endPointer, true);
      addDom(window, 'pointercancel', cancelPointers, true);
    } else {
      addDom(el, 'mousedown', beginPointer, true);
      addDom(window, 'mouseup', endPointer, true);
    }
    // Keep a direct touch fallback even when Pointer Events exist. iOS/WebKit
    // and embedded WebViews can expose PointerEvent while Plotly still drives
    // its gl3d gesture from Touch Events.
    addDom(el, 'touchstart', beginTouch, { passive: true, capture: true });
    addDom(window, 'touchend', endTouch, { passive: true, capture: true });
    addDom(window, 'touchcancel', endTouch, { passive: true, capture: true });
    addDom(el, 'wheel', beginWheel, { passive: true, capture: true });
    addDom(el, 'dblclick', beginWheel, true);

    const viewportChanged = () => {
      if (protectedPhase()) {
        enterSettling();
      } else {
        scheduleRestore([80, 240, 600, 1000]);
      }
    };
    addDom(window, 'resize', viewportChanged);
    addDom(window, 'orientationchange', viewportChanged);
    addDom(window.visualViewport, 'resize', viewportChanged);
  }

  function arm() {
    ensurePlotlyListeners();
    if (!protectedPhase()) scheduleRestore([80, 240, 600]);
  }

  function destroy() {
    destroyed = true;
    clearRestoreTimers();
    clearSettleTimers();
    activePointers.clear();
    activeTouches = 0;
    for (const remove of removers) remove();
    if (el?.style && originalInlineStyle) {
      el.style.touchAction = originalInlineStyle.touchAction;
      el.style.overscrollBehavior = originalInlineStyle.overscrollBehavior;
      el.style.webkitUserSelect = originalInlineStyle.webkitUserSelect;
      el.style.webkitTouchCallout = originalInlineStyle.webkitTouchCallout;
    }
  }

  return {
    arm,
    beforeWrite: ensurePlotlyListeners,
    afterWrite: arm,
    restore: () => scheduleRestore(),
    destroy,
    getSavedCamera: () => cloneValue(savedCamera),
    getState: () => state,
  };
}
