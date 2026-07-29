export class Heatmap {
  constructor(options = {}) {
    this.resolution = options.resolution || 1.0;
    this.decay = options.decay || 0.1; // decay rate per second
    this.grid = new Map();
    this.maxVal = 1;
  }

  setResolution(res) {
    if (this.resolution !== res && res > 0) {
      this.resolution = res;
      this.grid.clear();
      this.maxVal = 1;
    }
  }

  add(price, weight = 1) {
    const k = Math.round(price / this.resolution) * this.resolution;
    const cur = this.grid.get(k) || 0;
    const next = cur + weight;
    this.grid.set(k, next);
    if (next > this.maxVal) this.maxVal = next;
  }

  update(dt) {
    let newMax = 1e-9;
    for (const [k, v] of this.grid.entries()) {
      const next = v * Math.exp(-this.decay * dt);
      if (next < 0.05) {
        this.grid.delete(k);
      } else {
        this.grid.set(k, next);
        if (next > newMax) newMax = next;
      }
    }
    this.maxVal = newMax;
  }

  render(ctx, view, xFn, height, colorRgb = '232,98,42') {
    if (this.grid.size === 0) return;
    
    // We draw vertical bands of heat for each price bin
    // xFn(price) gives the horizontal pixel coordinate
    
    // Instead of drawing individual rects that might have gaps, 
    // we draw slightly overlapping rectangles
    const stepPx = Math.abs(xFn(view.lo) - xFn(view.lo + this.resolution));
    const drawW = Math.max(1, stepPx * 1.2);
    
    for (const [p, v] of this.grid.entries()) {
      if (p < view.lo || p > view.hi) continue;
      
      const ratio = Math.min(1, v / this.maxVal);
      if (ratio < 0.02) continue;
      
      const x = xFn(p);
      const alpha = ratio * 0.45; // Max opacity 45%
      
      ctx.fillStyle = `rgba(${colorRgb}, ${alpha.toFixed(3)})`;
      ctx.fillRect(x - drawW / 2, 20, drawW, height);
    }
  }
}
