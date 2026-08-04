// Probability Cone public wrapper.
// Quantitative rendering stays in cone_core.js; camera persistence is handled
// independently so mobile Plotly resets cannot overwrite the user's view.

import { initCone as initCoreCone } from './cone_core.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';

const INIT_CAM = {
  eye: { x: 0.15, y: 2.3, z: 0.65 },
  up: { x: 0, y: 0, z: 1 },
};

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  // Install DOM gesture tracking before cone_core installs its own listeners.
  const camera = createPlotlyCameraGuard(el, INIT_CAM);
  const core = initCoreCone(el);

  function setData(...args) {
    camera.beforeWrite();
    const result = core.setData(...args);
    camera.afterWrite();
    return result;
  }

  function updateLive(...args) {
    return core.updateLive(...args);
  }

  return { setData, updateLive };
}
