const DEFAULT_CAMERA = {
  eye: { x: 1.25, y: 1.25, z: 1.25 },
  center: { x: 0, y: 0, z: 0 },
  up: { x: 0, y: 0, z: 1 },
};

function clone(value) {
  try { return JSON.parse(JSON.stringify(value)); } catch { return value; }
}

function ensureToolbarStyles() {
  if (typeof document === 'undefined' || document.getElementById('terminal-3d-toolbar-style')) return;
  const style = document.createElement('style');
  style.id = 'terminal-3d-toolbar-style';
  style.textContent = `
    .terminal-3d-toolbar{position:absolute;z-index:8;left:50%;bottom:8px;transform:translateX(-50%);display:flex;gap:3px;align-items:center;padding:4px;background:rgba(5,14,23,.86);border:1px solid rgba(190,213,227,.2);border-radius:7px;box-shadow:0 6px 22px rgba(0,0,0,.24);backdrop-filter:blur(7px)}
    .terminal-3d-toolbar button{appearance:none;border:1px solid rgba(185,210,225,.17);background:rgba(24,43,57,.76);color:#bcd0dc;border-radius:4px;min-width:34px;height:28px;padding:0 7px;font:600 8px/1 'IBM Plex Mono',monospace;letter-spacing:.03em;cursor:pointer;touch-action:manipulation}
    .terminal-3d-toolbar button:hover,.terminal-3d-toolbar button:focus-visible{color:#fff;border-color:rgba(88,201,218,.62);outline:none}
    .terminal-3d-toolbar button.active{color:#07131d;background:#62d6db;border-color:#8ee9e8}
    .terminal-3d-toolbar .toolbar-separator{width:1px;height:20px;background:rgba(190,213,227,.18);margin:0 1px}
    @media(max-width:680px){.terminal-3d-toolbar{bottom:6px;max-width:calc(100% - 12px);overflow-x:auto;justify-content:flex-start}.terminal-3d-toolbar button{min-width:42px;height:36px;padding:0 8px;font-size:7px}.terminal-3d-toolbar::-webkit-scrollbar{display:none}}
  `;
  document.head?.appendChild(style);
}

export function attachTerminal3DToolbar({ plot, container, guard, homeCamera, key = 'plot' } = {}) {
  if (!plot || !container || typeof document === 'undefined') return null;
  ensureToolbarStyles();
  container.style.position = container.style.position || 'relative';
  container.querySelector(`[data-terminal-3d-toolbar="${key}"]`)?.remove();
  const toolbar = document.createElement('div');
  toolbar.className = 'terminal-3d-toolbar';
  toolbar.dataset.terminal3dToolbar = key;
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', '3D view controls');
  let dragMode = 'orbit';

  const relayout = (update) => {
    if (!window.Plotly || !plot?._fullLayout) return;
    window.Plotly.relayout(plot, update);
  };
  const button = (label, title, action, mode = null) => {
    const el = document.createElement('button');
    el.type = 'button'; el.textContent = label; el.title = title;
    el.setAttribute('aria-label', title);
    if (mode === dragMode) el.classList.add('active');
    el.addEventListener('click', (event) => {
      event.preventDefault(); event.stopPropagation();
      action();
      if (mode) {
        dragMode = mode;
        toolbar.querySelectorAll('button[data-drag-mode]').forEach((node) => node.classList.toggle('active', node.dataset.dragMode === mode));
      }
    });
    if (mode) el.dataset.dragMode = mode;
    toolbar.appendChild(el);
    return el;
  };

  button('ORBIT', 'Orbit drag mode', () => relayout({ 'scene.dragmode': 'orbit' }), 'orbit');
  button('TURNTABLE', 'Turntable drag mode', () => relayout({ 'scene.dragmode': 'turntable' }), 'turntable');
  button('PAN', 'Pan drag mode', () => relayout({ 'scene.dragmode': 'pan' }), 'pan');
  button('ZOOM', 'Zoom drag mode', () => relayout({ 'scene.dragmode': 'zoom' }), 'zoom');
  const separator = document.createElement('span'); separator.className = 'toolbar-separator'; separator.setAttribute('aria-hidden', 'true'); toolbar.appendChild(separator);
  button('RESET', 'Reset to Plotly default view', () => {
    const camera = clone(DEFAULT_CAMERA); guard?.rememberExternalCamera?.(camera); relayout({ 'scene.camera': camera });
  });
  button('HOME', 'Return to terminal home view', () => {
    const camera = clone(homeCamera || DEFAULT_CAMERA); guard?.rememberExternalCamera?.(camera); relayout({ 'scene.camera': camera });
  });
  container.appendChild(toolbar);
  return toolbar;
}
