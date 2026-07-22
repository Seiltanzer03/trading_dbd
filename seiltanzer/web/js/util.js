// Утилиты: форматирование, tooltip, DOM-хелперы.

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => [...document.querySelectorAll(sel)];

export function fmtPct(x, digits = 1) {
  if (x == null || !isFinite(x)) return '—';
  return (x * 100).toFixed(digits) + '%';
}

export function fmtNum(x, digits = 2) {
  if (x == null || !isFinite(x)) return '—';
  return Number(x).toLocaleString('ru-RU', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

export function fmtPrice(x) {
  if (x == null || !isFinite(x)) return '—';
  const d = Math.abs(x) >= 1000 ? 1 : Math.abs(x) >= 10 ? 2 : 4;
  return Number(x).toLocaleString('ru-RU', {
    minimumFractionDigits: d, maximumFractionDigits: d,
  });
}

export function fmtR(x, digits = 2) {
  if (x == null || !isFinite(x)) return '—';
  const s = x > 0 ? '+' : '';
  return s + x.toFixed(digits) + 'R';
}

export function fmtTs(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toISOString().slice(0, 16).replace('T', ' ');
}

export const STATUS_ICON = {
  live: '●', demo: '◆', delayed: '◐', no_data: '○', manual: '◑',
};

export function statusLabel(status) {
  return { live: 'LIVE', demo: 'DEMO', delayed: 'DELAYED', no_data: 'НЕТ ДАННЫХ', manual: 'ВРУЧНУЮ' }[status] || status;
}

// ------------------------------------------------------------- tooltip

export function initTooltips() {
  const tip = $('#tooltip');
  let current = null;
  document.addEventListener('mouseover', (e) => {
    const el = e.target.closest('[data-tip]');
    if (el && el.dataset.tip) {
      current = el;
      tip.textContent = el.dataset.tip;
      tip.hidden = false;
    } else if (current && !current.contains(e.target)) {
      current = null;
      tip.hidden = true;
    }
  });
  document.addEventListener('mousemove', (e) => {
    if (tip.hidden) return;
    const pad = 14;
    let x = e.clientX + pad, y = e.clientY + pad;
    const r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = e.clientX - r.width - pad;
    if (y + r.height > innerHeight - 8) y = e.clientY - r.height - pad;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  });
  document.addEventListener('mouseout', (e) => {
    if (current && !e.relatedTarget?.closest?.('[data-tip]')) {
      current = null;
      tip.hidden = true;
    }
  });
}

// холст с учётом devicePixelRatio; возвращает ctx с логическими координатами
export function setupCanvas(canvas, cssHeight) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  const h = cssHeight;
  canvas.style.height = h + 'px';
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

export const COLORS = {
  paper: '#F4F2EC', card: '#FFFFFF', ink: '#14140F', rule: '#D8D5CC',
  red: '#C6373C', green: '#2E7D4F', dim: '#8A877D', accent: '#EEECE4',
  greenSoft: '#C9DCCF', redSoft: '#E7C4C5',
};
