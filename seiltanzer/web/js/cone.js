// Probability Cone public wrapper.
// Quantitative rendering stays in cone_core.js; camera persistence is handled
// independently so mobile Plotly resets cannot overwrite the user's view.

import { initCone as initCoreCone } from './cone_core.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';
import { applyLocalTouchClock } from './touch_clock.js';
import { createLatestPanelTask } from './frame_budget.js';

const INIT_CAM = {
  eye: { x: 0.15, y: 2.3, z: 0.65 },
  up: { x: 0, y: 0, z: 1 },
};

function publish(name, detail) {
  if (typeof window === 'undefined' || typeof window.CustomEvent !== 'function') return;
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  // Install DOM gesture tracking before cone_core installs its own listeners.
  const camera = createPlotlyCameraGuard(el, INIT_CAM);
  const core = initCoreCone(el);

  const setDataTask = createLatestPanelTask('cone:set-data', el, (...args) => {
    camera.beforeWrite();
    const result = core.setData(...args);
    camera.afterWrite();
    publish('seiltanzer:cone-data', { cone: args[0] || null, extra: args[1] || null });
    return result;
  }, { margin: 300 });

  const liveTask = createLatestPanelTask('cone:live', el, (...args) => {
    const result = core.updateLive(...args);
    publish('seiltanzer:cone-live', args[0] || {});
    return result;
  }, { margin: 220 });

  function setData(...args) {
    // Probability stays option-implied. Only the displayed calendar touch clock
    // is re-mapped to the current local variance pace (term structure + RV/IV).
    // Keep this synchronous mutation because app.js sends the same cone object to
    // Fan immediately afterwards; both panels must always share one touch clock.
    if (args[0]) applyLocalTouchClock(args[0]);
    setDataTask.schedule(...args);
  }

  function updateLive(...args) {
    // Live r is still delivered at display-frame latency when the panel is visible.
    // Offscreen updates collapse to the newest state instead of spending Plotly
    // work while another panel (for example the Galton board) owns the screen.
    liveTask.schedule(...args);
  }

  return { setData, updateLive };
}
