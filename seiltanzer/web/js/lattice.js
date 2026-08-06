// Probability Lattice DOM ownership wrapper.
//
// The legacy app renderer and the board renderer still write the historical
// element IDs. Those IDs are moved to hidden sinks; the user-visible clones are
// updated only here from the canonical lattice stats. This prevents alternating
// text and layout jumps without changing the probability or barrier maths.

export * from './lattice_core.js';
import { initLattice as initCoreLattice } from './lattice_core.js';

const STABLE_IDS = ['lat-balls', 'lat-green', 'lat-conv', 'lat-calib', 'lat-read'];
const LABELS = {
  'lat-balls': 'ШАРИКОВ ВПИТАНО',
  'lat-green': 'МАССА КОЛОНОК В +R',
  'lat-conv': 'ОШИБКА ФОРМЫ',
  'lat-calib': 'LIVE / ИСТОРИЯ',
};

function fmtPct(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : '—';
}

function ensureStableMirrors() {
  if (typeof document === 'undefined') return {};
  const mirrors = {};
  for (const id of STABLE_IDS) {
    const existing = document.getElementById(`${id}-stable`);
    if (existing) {
      mirrors[id] = existing;
      continue;
    }
    const sink = document.getElementById(id);
    if (!sink) continue;
    const mirror = sink.cloneNode(true);
    mirror.id = `${id}-stable`;
    mirror.dataset.latticeStable = id;
    mirror.hidden = false;
    mirror.setAttribute('aria-live', 'off');
    sink.hidden = true;
    sink.setAttribute('aria-hidden', 'true');
    sink.after(mirror);
    const label = sink.parentElement?.querySelector('.lbl');
    if (label && LABELS[id]) label.textContent = LABELS[id];
    mirrors[id] = mirror;
  }
  return mirrors;
}

function syncMetadata(id, mirror) {
  if (!mirror || typeof document === 'undefined') return;
  const sink = document.getElementById(id);
  if (!sink) return;
  if (sink.dataset.tip) mirror.dataset.tip = sink.dataset.tip;
  if (sink.title) mirror.title = sink.title;
}

function setValue(element, text, tone = '') {
  if (!element) return;
  element.textContent = text;
  element.className = `val${tone ? ` ${tone}` : ''}`;
}

function renderStableStats(api, active) {
  const mirrors = ensureStableMirrors();
  const stats = api.stats || {};
  const dropped = Number.isFinite(Number(stats.dropped)) ? Number(stats.dropped) : 0;
  const green = active && dropped > 0 ? stats.greenShare : null;
  const shape = active && dropped > 0 ? (stats.shapeError ?? stats.convergence) : null;
  const live = active ? stats.pGreenModel : null;
  const history = active ? stats.pGreenHistory : null;
  const shift = active && dropped > 0 ? stats.currentShift : null;

  setValue(mirrors['lat-balls'], String(dropped));
  setValue(
    mirrors['lat-green'],
    fmtPct(green),
    green == null ? '' : green > 0.55 ? 'green' : green < 0.45 ? 'red' : '',
  );
  setValue(
    mirrors['lat-conv'],
    fmtPct(shape),
    shape == null ? '' : shape < 0.10 ? 'green' : shape > 0.24 ? 'red' : '',
  );
  setValue(mirrors['lat-calib'], `${fmtPct(live)} / ${fmtPct(history)}`);

  const read = mirrors['lat-read'];
  if (read) {
    read.textContent = active
      ? `КОЛОНКИ ${dropped} · +R ${fmtPct(green)}\nLIVE ${fmtPct(live)} · ИСТ ${fmtPct(history)} · Δ ${fmtPct(shift)}`
      : 'КОЛОНКИ 0 · +R —\nLIVE — · ИСТ — · Δ —';
    read.className = 'lat-read';
    read.style.whiteSpace = 'pre-line';
    read.style.lineHeight = '1.5';
    read.style.minHeight = '3em';
  }

  for (const id of STABLE_IDS) syncMetadata(id, mirrors[id]);
}

export function initLattice(canvas) {
  const core = initCoreLattice(canvas);
  let active = false;

  const render = () => renderStableStats(core, active);
  if (typeof window !== 'undefined') {
    window.setInterval(render, 100);
    window.requestAnimationFrame(render);
  }

  return {
    setData(data) {
      active = !!data?.active;
      core.setData(data);
      render();
    },
    reset() {
      const result = core.reset();
      if (result !== false) active = false;
      render();
      return result;
    },
    get stats() {
      return core.stats;
    },
  };
}
