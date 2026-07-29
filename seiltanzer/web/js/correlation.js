// CROSS-ASSET REGIME MATRIX.
//
// Upper triangle: rolling 5-minute return correlation (last 96 observations).
// Lower triangle: change versus the 3-month daily baseline. This makes regime
// breaks visible instead of animating a static monthly matrix.

import { $ } from './util.js';

let chart, emptyEl, statusEl;
let payload = null;
const WATCHED = [
  [0, 1, 'NAS↔VXN', 'spot-vol'],
  [2, 3, 'SP500↔VIX', 'spot-vol'],
  [4, 5, 'GOLD↔GVZ', 'spot-vol'],
  [6, 7, 'OIL↔OVX', 'spot-vol'],
  [0, 2, 'NAS↔SP500', 'cross-index'],
];

export function initCorrelation() {
  emptyEl = $('#corr-empty');
  statusEl = $('#corr-status');
}

function finite(v) {
  return Number.isFinite(Number(v));
}

function regimeShift(p) {
  const short = p.matrix_short || p.matrix;
  const base = p.matrix_baseline;
  const delta = p.matrix_delta;
  if (!short || !base || !delta) return null;
  const candidates = WATCHED.map(([i, j, label, kind]) => {
    const s = short?.[i]?.[j], b = base?.[i]?.[j], d = delta?.[i]?.[j];
    return finite(s) && finite(b) && finite(d)
      ? { i, j, label, kind, short: Number(s), base: Number(b), delta: Number(d) }
      : null;
  }).filter(Boolean).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const top = candidates[0];
  if (!top || Math.abs(top.delta) < 0.18) return null;
  let meaning = 'связь сменила режим — подтверждение движения слабее';
  if (top.kind === 'spot-vol') {
    if (top.base < -0.2 && top.short > top.base + 0.25)
      meaning = 'обычная обратная spot-vol защита ослабла';
    else if (top.short < top.base - 0.25)
      meaning = 'обратная spot-vol связь усилилась';
  } else if (top.short < top.base - 0.25) {
    meaning = 'индексы расходятся — меньше cross-market подтверждения';
  } else if (top.short > top.base + 0.25) {
    meaning = 'индексы синхронизировались — больше общего beta-риска';
  }
  return { ...top, meaning };
}

export function updateCorrelation(p) {
  const matrix = p?.matrix_short || p?.matrix;
  if (!p || !matrix || matrix.length === 0) {
    payload = null;
    if (emptyEl) emptyEl.style.display = 'flex';
    if (chart) { chart.dispose(); chart = null; }
    if (statusEl) statusEl.textContent = '○ НЕТ ДАННЫХ';
    return;
  }
  payload = p;
  if (emptyEl) emptyEl.style.display = 'none';
  
  const shift = regimeShift(p);
  if (statusEl) {
    if (shift) {
      statusEl.textContent = `⚠ Δρ ${shift.label} ${shift.delta >= 0 ? '+' : ''}${shift.delta.toFixed(2)}`;
      statusEl.className = 'badge warn';
      statusEl.title = `${shift.label}: baseline ${shift.base.toFixed(2)} → rolling ${shift.short.toFixed(2)}. ${shift.meaning}`;
    } else {
      statusEl.textContent = `● ROLLING 5M · ${p.dynamic_pairs || '—'} ПАР`;
      statusEl.className = 'badge live';
      statusEl.title = 'Rolling 5m correlations versus a 3-month daily baseline. Refresh: 5 minutes.';
    }
  }
  
  renderCorrelation();
}

function renderCorrelation() {
    const el = $('#corr-canvas');
    if (!el || !payload) return;
    
    if (!chart && window.echarts) {
        chart = window.echarts.init(el);
        new ResizeObserver(() => { if (chart) chart.resize(); }).observe(el);
    }
    if (!chart) return;
    
    const targetShort = payload.matrix_short || payload.matrix;
    const targetDelta = payload.matrix_delta || [];
    const effective = payload.matrix || targetShort;
    const assets = payload.assets;
    const n = targetShort.length;
    
    const data = [];
    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            const upper = i < j;
            let val = 0;
            let type = '';
            
            if (i === j) {
                val = 1;
                type = 'diagonal';
            } else if (upper) {
                val = finite(targetShort?.[i]?.[j]) ? Number(targetShort[i][j]) : Number(effective?.[i]?.[j] || 0);
                type = 'rolling';
            } else {
                val = finite(targetDelta?.[i]?.[j]) ? Number(targetDelta[i][j]) : 0;
                type = 'delta';
            }
            // Heatmap requires [x, y, value]
            // We use j for X axis (columns), and i for Y axis (rows). 
            // We reverse Y axis later by setting inverse: true
            data.push([j, i, val, type]);
        }
    }
    
    const option = {
        animation: false,
        tooltip: {
            position: 'top',
            backgroundColor: 'rgba(255,255,255,0.95)',
            textStyle: { color: '#333' },
            borderColor: '#ddd',
            formatter: function (params) {
                const pt = params.data;
                const assetY = assets[pt[1]];
                const assetX = assets[pt[0]];
                const val = pt[2].toFixed(2);
                if (pt[3] === 'diagonal') return `<b>${assetX}</b>`;
                if (pt[3] === 'rolling') {
                    return `<div style="font-size:11px;color:#888">${assetY} ↔ ${assetX}</div><br/>
                            <div style="font-size:14px;color:#333">Rolling ρ5m: <b style="color:${val >= 0 ? '#e67e22' : '#3498db'}">${val > 0 ? '+' : ''}${val}</b></div>`;
                } else {
                    return `<div style="font-size:11px;color:#888">${assetX} ↔ ${assetY}</div><br/>
                            <div style="font-size:14px;color:#333">Shift (Δρ): <b style="color:${Math.abs(val) > 0.25 ? '#e74c3c' : (val >= 0 ? '#e67e22' : '#3498db')}">${val > 0 ? '+' : ''}${val}</b></div>`;
                }
            }
        },
        grid: { left: '8%', right: '8%', top: '5%', bottom: '5%', containLabel: true },
        xAxis: {
            type: 'category',
            data: assets,
            splitArea: { show: true },
            axisLabel: { color: '#666', fontSize: 10, rotate: -30 }
        },
        yAxis: {
            type: 'category',
            data: assets,
            splitArea: { show: true },
            inverse: true,
            axisLabel: { color: '#666', fontSize: 10 }
        },
        visualMap: {
            min: -1,
            max: 1,
            calculable: true,
            orient: 'horizontal',
            left: 'center',
            bottom: '0%',
            show: false,
            inRange: {
                color: ['#3498db', '#f9f9f9', '#e67e22']
            }
        },
        series: [{
            name: 'Correlation',
            type: 'heatmap',
            data: data,
            label: {
                show: true,
                formatter: function (p) {
                    if (p.data[3] === 'diagonal') {
                        return `{diag|${assets[p.data[0]]}}`;
                    }
                    const v = p.data[2];
                    const vFmt = v > 0 ? '+' + v.toFixed(2) : v.toFixed(2);
                    return `{valDark|${vFmt}}`;
                },
                rich: {
                    diag: { color: '#888', fontSize: 10, fontWeight: 'bold' },
                    valDark: { color: '#222', fontSize: 11, fontWeight: 'bold' }
                }
            },
            itemStyle: {
                borderColor: '#fff',
                borderWidth: 2
            },
            emphasis: {
                itemStyle: {
                    shadowBlur: 10,
                    shadowColor: 'rgba(0, 0, 0, 0.5)'
                }
            }
        }]
    };
    
    chart.setOption(option);
}
