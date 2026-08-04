import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = () => 0;

const {
  projectTotalVariance,
  buildLocalProjection,
  smileMetrics,
} = await import('../../seiltanzer/web/js/iv_surface.js');

const flat = [
  { days: 2, ivPct: 20 },
  { days: 7, ivPct: 20 },
];
for (const d of [1 / 24, 0.25, 1, 4]) {
  assert.ok(Math.abs(projectTotalVariance(flat, d) - 20) < 1e-8);
}

const rows = [
  { days: 2, xs: [-10, 0, 10], ys: [28, 20, 18] },
  { days: 7, xs: [-10, 0, 10], ys: [26, 21, 19] },
];
const grid = [-10, -5, 0, 5, 10];
const local = buildLocalProjection(rows, grid);
assert.equal(local.length, 7);
assert.equal(local[0].length, grid.length);
assert.ok(local.every((row) => row.every(Number.isFinite)));

const zRows = [[28, 24, 20, 19, 18]];
const atSnapshot = smileMetrics(grid, zRows, 0);
const afterRise = smileMetrics(grid, zRows, 5);
assert.equal(atSnapshot.atm, 20);
assert.equal(afterRise.atm, 19);
assert.ok(atSnapshot.skew > 0);
assert.ok(Number.isFinite(afterRise.curvature));

console.log(JSON.stringify({
  localRows: local.length,
  firstHourAtm: local[0][2],
  snapshotAtm: atSnapshot.atm,
  liveAtm: afterRise.atm,
}));
