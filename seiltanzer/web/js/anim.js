// Анимационное ядро: плавная интерполяция состояния между тиками сервера.
// Сервер шлёт данные раз в ~1–2 с; панели рисуются 60fps, сглаживая переход
// цель←текущее — поэтому всё «дышит» и движется в реальном времени.

// частотно-независимое экспоненциальное сглаживание: cur -> target
export function approach(cur, target, dt, rate = 6) {
  if (cur == null || !isFinite(cur)) return target;
  if (target == null || !isFinite(target)) return cur;
  const k = 1 - Math.exp(-rate * dt);
  return cur + (target - cur) * k;
}

export function approachArr(cur, target, dt, rate = 6) {
  if (!target) return cur;
  if (!cur || cur.length !== target.length) return target.slice();
  const out = new Array(target.length);
  for (let i = 0; i < target.length; i++) out[i] = approach(cur[i], target[i], dt, rate);
  return out;
}

export const lerp = (a, b, t) => a + (b - a) * t;

// Плавный счётчик числа в DOM-элементе (count-up между значениями).
const tweens = new WeakMap();
export function tweenNumber(el, value, fmt, rate = 8) {
  if (el == null) return;
  let st = tweens.get(el);
  if (!st) { st = { cur: value, target: value }; tweens.set(el, st); }
  st.target = value;
  st.fmt = fmt;
  st.rate = rate;
  numEls.add(el);
}

let last = performance.now();
function tick(now) {
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;
  // обновляем все зарегистрированные числовые твины
  for (const el of numEls) {
    const st = tweens.get(el);
    if (!st) continue;
    if (st.target == null || !isFinite(st.target)) { el.textContent = st.fmt ? st.fmt(st.target) : '—'; continue; }
    st.cur = approach(st.cur, st.target, dt, st.rate);
    if (Math.abs(st.cur - st.target) < Math.abs(st.target) * 1e-4 + 1e-6) st.cur = st.target;
    el.textContent = st.fmt ? st.fmt(st.cur) : String(st.cur);
  }
  requestAnimationFrame(tick);
}
const numEls = new Set();
export function registerNumber(el) { if (el) numEls.add(el); }
requestAnimationFrame(tick);

// Пульс: 0..1 синус для «живых» акцентов (мигание точки LIVE, свечение тейла).
export function pulse(now = performance.now(), periodMs = 1600) {
  return 0.5 + 0.5 * Math.sin((now / periodMs) * Math.PI * 2);
}
