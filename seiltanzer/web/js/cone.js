// Persistent camera guard for the Probability Cone.
// The quantitative/visual implementation stays in cone_core.js. This wrapper
// owns the user's 3D camera so Plotly.react(), responsive resize and live
// relayouts cannot return rotation or zoom to the initial view on mobile.

import { initCone as initCoreCone } from './cone_core.js';

const INIT_CAM = {
  eye: { x: 0.15, y: 2.3, z: 0.65 },
  up: { x: 0, y: 0, z: 1 },
};

function cloneCamera(camera) {
  if (!camera || typeof camera !== 'object') return null;
  try {
    return JSON.parse(JSON.stringify(camera));
  } catch {
    return null;
  }
}

function camerasEqual(a, b) {
  const ca = cloneCamera(a);
  const cb = cloneCamera(b);
  return ca != null && cb != null && JSON.stringify(ca) === JSON.stringify(cb);
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
  node[parts.at(-1)] = value;
}

function cameraFromUpdate(base, update) {
  if (!update || typeof update !== 'object') return null;
  let next = cloneCamera(base) || cloneCamera(INIT_CAM);
  let touched = false;
  if (update['scene.camera'] && typeof update['scene.camera'] === 'object') {
    next = cloneCamera(update['scene.camera']) || next;
    touched = true;
  }
  for (const [key, value] of Object.entries(update)) {
    if (!key.startsWith('scene.camera.')) continue;
    setNested(next, key.slice('scene.camera.'.length), value);
    touched = true;
  }
  return touched ? next : null;
}

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  const core = initCoreCone(el);
  let savedCamera = cloneCamera(INIT_CAM);
  let plotlyListenersOn = false;
  let pointerHeld = false;
  let userInteracting = false;
  let interactionTimer = null;
  const restoreTimers = new Set();
  let suppressCameraEvents = 0;

  const fullCamera = () => cloneCamera(el?._fullLayout?.scene?.camera);

  function captureCamera(update = null) {
    if (suppressCameraEvents > 0) return;
    const fromUpdate = cameraFromUpdate(savedCamera, update);
    if (fromUpdate) savedCamera = fromUpdate;
    else {
      const current = fullCamera();
      if (current) savedCamera = current;
    }
    requestAnimationFrame(() => {
      if (suppressCameraEvents > 0) return;
      const current = fullCamera();
      if (current) savedCamera = current;
    });
  }

  function clearRestoreTimers() {
    for (const timer of restoreTimers) clearTimeout(timer);
    restoreTimers.clear();
  }

  function restoreCamera(delay = 0) {
    const timer = setTimeout(() => {
      restoreTimers.delete(timer);
      if (pointerHeld || userInteracting || !el || !window?.Plotly || !savedCamera
          || !el?._fullLayout?.scene) return;
      const current = fullCamera();
      if (current && camerasEqual(current, savedCamera)) return;
      suppressCameraEvents += 1;
      const write = window.Plotly.relayout(el, {
        'scene.camera': cloneCamera(savedCamera),
      });
      Promise.resolve(write).finally(() => {
        setTimeout(() => {
          suppressCameraEvents = Math.max(0, suppressCameraEvents - 1);
        }, 0);
      });
    }, delay);
    restoreTimers.add(timer);
  }

  function settleInteraction(delay) {
    if (interactionTimer) clearTimeout(interactionTimer);
    if (pointerHeld) return;
    interactionTimer = setTimeout(() => {
      userInteracting = false;
      restoreCamera(0);
    }, delay);
  }

  function ensurePlotlyListeners() {
    if (plotlyListenersOn || !el?.on) return;
    plotlyListenersOn = true;
    el.on('plotly_relayouting', (update) => {
      if (suppressCameraEvents > 0) return;
      userInteracting = true;
      captureCamera(update);
      settleInteraction(320);
    });
    el.on('plotly_relayout', (update) => {
      if (suppressCameraEvents > 0) return;
      userInteracting = true;
      captureCamera(update);
      settleInteraction(220);
    });
    el.on('plotly_afterplot', () => {
      if (suppressCameraEvents > 0 || pointerHeld || userInteracting) return;
      restoreCamera(0);
    });
  }

  const begin = () => {
    pointerHeld = true;
    userInteracting = true;
    if (interactionTimer) clearTimeout(interactionTimer);
    clearRestoreTimers();
    captureCamera();
  };
  const release = () => {
    if (!pointerHeld) return;
    pointerHeld = false;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        captureCamera();
        settleInteraction(240);
      });
    });
  };

  if (el && typeof window !== 'undefined') {
    if (window.PointerEvent) {
      el.addEventListener('pointerdown', begin, true);
      window.addEventListener('pointerup', release, true);
      window.addEventListener('pointercancel', release, true);
    } else {
      el.addEventListener('mousedown', begin, true);
      el.addEventListener('touchstart', begin, { passive: true, capture: true });
      window.addEventListener('mouseup', release, true);
      window.addEventListener('touchend', release, { passive: true, capture: true });
      window.addEventListener('touchcancel', release, { passive: true, capture: true });
    }
    const restoreAfterViewportChange = () => {
      // cone_core registers its resize handler first. Restore more than once
      // because mobile Plotly/browser resizing can finish on a later frame.
      restoreCamera(0);
      requestAnimationFrame(() => restoreCamera(40));
      restoreCamera(140);
    };
    window.addEventListener('resize', restoreAfterViewportChange);
    window.addEventListener('orientationchange', restoreAfterViewportChange);
    window.visualViewport?.addEventListener('resize', restoreAfterViewportChange);
  }

  function setData(...args) {
    captureCamera();
    const result = core.setData(...args);
    ensurePlotlyListeners();
    requestAnimationFrame(() => ensurePlotlyListeners());
    return result;
  }

  function updateLive(...args) {
    return core.updateLive(...args);
  }

  return { setData, updateLive };
}
