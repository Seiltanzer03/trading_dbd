// IV Surface public wrapper.
// Quantitative rendering stays in iv_surface_core.js. The shared camera guard
// owns the mobile 3D view so touch/pinch position survives ticks and resizes.

import { initIVSurface as initCoreIVSurface } from './iv_surface_core.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';

export {
  projectTotalVariance,
  smileMetrics,
  buildLocalProjection,
} from './iv_surface_core.js';

const INIT_CAM = {
  eye: { x: 1.4, y: -1.4, z: 0.82 },
  up: { x: 0, y: 0, z: 1 },
};

export function initIVSurface(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  // Install gesture tracking before the core module attaches its own handlers.
  const camera = createPlotlyCameraGuard(el, INIT_CAM);
  const core = initCoreIVSurface(el);

  function render(...args) {
    camera.beforeWrite();
    const result = core.render(...args);
    camera.afterWrite();
    return result;
  }

  function setMode(...args) {
    camera.beforeWrite();
    const result = core.setMode(...args);
    camera.afterWrite();
    return result;
  }

  function destroy(...args) {
    camera.destroy();
    return core.destroy(...args);
  }

  return {
    render,
    updateLive: (...args) => core.updateLive(...args),
    setMode,
    destroy,
  };
}
