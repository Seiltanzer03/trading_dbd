// IV Surface public wrapper.
// Quantitative rendering stays in iv_surface_core.js. The shared camera guard
// owns the mobile 3D view so touch/pinch position survives ticks and resizes.

import { initIVSurface as initCoreIVSurface } from './iv_surface_core.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';
import { createLatestPanelTask } from './frame_budget.js';

export {
  projectTotalVariance,
  smileMetrics,
  buildLocalProjection,
} from './iv_surface_core.js';

const INIT_CAM = {
  eye: { x: 1.4, y: -1.4, z: 0.82 },
  up: { x: 0, y: 0, z: 1 },
};

function normalizePayload(surfacePayload) {
  return Array.isArray(surfacePayload)
    ? { value: surfacePayload, status: 'delayed' }
    : (surfacePayload || {});
}

// Snapshot identity deliberately excludes spot_current. The delayed option mesh
// is expensive and should be rebuilt only when the actual option snapshot changes;
// the core already has updateLive() for price-linked moneyness/ribbon movement.
function snapshotSignature(surfacePayload) {
  const payload = normalizePayload(surfacePayload);
  const rows = Array.isArray(payload.value) ? payload.value : [];
  if (!rows.length) return `empty|${payload.status || ''}|${payload.ts || ''}`;
  const rowSig = rows.map((row) => {
    const strikes = Array.isArray(row?.strikes) ? row.strikes : [];
    const ivs = Array.isArray(row?.ivs) ? row.ivs : [];
    const mid = ivs.length ? Math.floor(ivs.length / 2) : 0;
    return [
      row?.expiry || '',
      Number(row?.days || 0).toFixed(6),
      Number(row?.spot_at_snapshot || 0).toFixed(6),
      strikes.length,
      ivs.length,
      Number(ivs[0] || 0).toFixed(6),
      Number(ivs[mid] || 0).toFixed(6),
      Number(ivs.at?.(-1) || 0).toFixed(6),
    ].join(':');
  }).join('|');
  return `${payload.ts || payload.snapshot_ts || payload.chain_ts || ''}|${rowSig}`;
}

export function initIVSurface(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  // Install gesture tracking before the core module attaches its own handlers.
  const camera = createPlotlyCameraGuard(el, INIT_CAM);
  const core = initCoreIVSurface(el);
  let lastSnapshotSig = null;

  const renderTask = createLatestPanelTask('iv-surface:render', el, (state, surfacePayload, force = false) => {
    const payload = normalizePayload(surfacePayload);
    const sig = snapshotSignature(payload);
    const hasSurface = Array.isArray(payload.value) && payload.value.length > 0;

    // Most websocket ticks only change spot_current. Reusing the existing mesh is
    // visually identical to a full render because iv_surface_core animates the live
    // ribbon, curtain, ridge, dot and wake from updateLive().
    if (!force && hasSurface && lastSnapshotSig === sig) {
      core.updateLive(payload);
      return;
    }

    camera.beforeWrite();
    const result = core.render(state, surfacePayload, force);
    camera.afterWrite();
    lastSnapshotSig = hasSurface ? sig : null;
    return result;
  }, { margin: 300 });

  const liveTask = createLatestPanelTask('iv-surface:live', el, (...args) => core.updateLive(...args), { margin: 220 });

  function render(state, surfacePayload, force = false) {
    // Keep only the newest tick and execute the Plotly write when this panel is near
    // the viewport. No IV data or animation layer is removed; offscreen work is
    // simply postponed so it cannot interrupt a visible Canvas animation.
    renderTask.schedule(state, surfacePayload, force);
  }

  function setMode(...args) {
    // A mode button can only be used while the panel is visible; apply the latest
    // pending state first so mode switching never operates on stale geometry.
    renderTask.flushNow();
    camera.beforeWrite();
    const result = core.setMode(...args);
    camera.afterWrite();
    return result;
  }

  function destroy(...args) {
    renderTask.destroy();
    liveTask.destroy();
    lastSnapshotSig = null;
    camera.destroy();
    return core.destroy(...args);
  }

  return {
    render,
    updateLive: (...args) => liveTask.schedule(...args),
    setMode,
    destroy,
  };
}
