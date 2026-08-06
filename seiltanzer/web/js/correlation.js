import { $ } from './util.js';

let chart, emptyEl, statusEl;
let payload = null;
let graphData = null;
let currentMode = 'NETWORK'; // 'NETWORK' | 'MATRIX'
let draggedNode = null;

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

  const btnNet = $('#btn-corr-network');
  const btnMat = $('#btn-corr-matrix');

  if (btnNet) btnNet.addEventListener('click', () => setMode('NETWORK'));
  if (btnMat) btnMat.addEventListener('click', () => setMode('MATRIX'));

  fetchGraphData();
}

function setMode(mode) {
  currentMode = mode;
  const btnNet = $('#btn-corr-network');
  const btnMat = $('#btn-corr-matrix');
  if (btnNet) btnNet.classList.toggle('active', mode === 'NETWORK');
  if (btnMat) btnMat.classList.toggle('active', mode === 'MATRIX');

  if (payload || graphData) renderCorrelation();
}

export async function fetchGraphData() {
  try {
    const res = await fetch('/api/analytics/correlation-graph');
    if (res.ok) {
      graphData = await res.json();
      if (currentMode === 'NETWORK') renderCorrelation();
    }
  } catch (err) {
    console.warn('Graph data fetch error:', err);
  }
}

export function updateCorrelation(p) {
  const matrix = p?.matrix_short || p?.matrix;
  if (!p || !matrix || matrix.length === 0) {
    payload = null;
    if (emptyEl) emptyEl.style.display = 'flex';
    if (chart) { chart.dispose(); chart = null; }
    return;
  }
  payload = p;
  if (emptyEl) emptyEl.style.display = 'none';

  renderCorrelation();
}

function renderCorrelation() {
  if (currentMode === 'NETWORK') {
    renderForceGraph();
  } else {
    renderMatrixChart();
  }
}

function renderForceGraph() {
  const holder = $('#corr-chart');
  if (!holder) return;

  if (chart) {
    chart.dispose();
    chart = null;
  }

  let cv = holder.querySelector('canvas');
  if (!cv) {
    holder.innerHTML = '<canvas style="width:100%;height:100%;display:block;cursor:grab;"></canvas>';
    cv = holder.querySelector('canvas');
  }

  const rect = holder.getBoundingClientRect();
  const width = Math.max(400, Math.floor(rect.width || 800));
  const height = Math.max(300, Math.floor(rect.height || 420));
  cv.width = width;
  cv.height = height;

  const ctx = cv.getContext('2d');

  const nodes = graphData?.nodes || [
    { id: 'NAS100', name: 'Nasdaq 100', x: width * 0.3, y: height * 0.3 },
    { id: 'SPX500', name: 'S&P 500', x: width * 0.4, y: height * 0.4 },
    { id: 'US30', name: 'Dow Jones', x: width * 0.35, y: height * 0.55 },
    { id: 'GER40', name: 'DAX 40', x: width * 0.6, y: height * 0.25 },
    { id: 'UK100', name: 'FTSE 100', x: width * 0.7, y: height * 0.4 },
    { id: 'GOLD', name: 'Gold', x: width * 0.25, y: height * 0.75 },
    { id: 'SILVER', name: 'Silver', x: width * 0.35, y: height * 0.8 },
    { id: 'BTCUSD', name: 'Bitcoin', x: width * 0.75, y: height * 0.7 },
  ];

  const links = graphData?.links || [];

  // Drag interaction
  function getMousePos(e) {
    const r = cv.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  cv.onpointerdown = (e) => {
    const pos = getMousePos(e);
    for (const node of nodes) {
      const dist = Math.hypot(node.x - pos.x, node.y - pos.y);
      if (dist <= 20) {
        draggedNode = node;
        cv.style.cursor = 'grabbing';
        break;
      }
    }
  };

  window.onpointermove = (e) => {
    if (draggedNode) {
      const pos = getMousePos(e);
      draggedNode.x = pos.x;
      draggedNode.y = pos.y;
      drawGraph();
    }
  };

  window.onpointerup = () => {
    if (draggedNode) {
      draggedNode = null;
      cv.style.cursor = 'grab';
    }
  };

  function drawGraph() {
    ctx.clearRect(0, 0, width, height);

    // Links
    for (const link of links) {
      const sNode = nodes.find((n) => n.id === link.source);
      const tNode = nodes.find((n) => n.id === link.target);
      if (sNode && tNode) {
        ctx.beginPath();
        ctx.moveTo(sNode.x, sNode.y);
        ctx.lineTo(tNode.x, tNode.y);

        const rho = link.correlation || 0;
        const isAlert = link.status === 'BREAK_ALERT';

        ctx.lineWidth = isAlert ? 3 : Math.max(1, Math.abs(rho) * 3.5);
        if (isAlert) {
          ctx.strokeStyle = '#e74c3c'; // Flashing warning link
          ctx.setLineDash([4, 4]);
        } else if (rho > 0) {
          ctx.strokeStyle = `rgba(39, 174, 96, ${Math.max(0.3, rho)})`;
          ctx.setLineDash([]);
        } else {
          ctx.strokeStyle = `rgba(192, 57, 43, ${Math.max(0.3, Math.abs(rho))})`;
          ctx.setLineDash([]);
        }
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Nodes
    for (const node of nodes) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, 16, 0, 2 * Math.PI);
      ctx.fillStyle = '#2c3e50';
      ctx.fill();
      ctx.strokeStyle = '#ecf0f1';
      ctx.lineWidth = 2;
      ctx.stroke();

      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px IBM Plex Mono, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(node.id, node.x, node.y);
    }
  }

  drawGraph();

  if (statusEl) {
    const alertCount = graphData?.summary?.active_breaks_count || 0;
    statusEl.textContent = alertCount > 0 ? `● ${alertCount} CORRELATION BREAK ALERTS` : '● NETWORK STABLE';
  }
}

function renderMatrixChart() {
  const holder = $('#corr-chart');
  if (!holder || !payload || !window.echarts) return;

  const matrix = payload.matrix_short || payload.matrix;
  const pairs = payload.pairs || ['NAS100', 'VXN', 'SP500', 'VIX', 'GOLD', 'GVZ', 'OIL', 'OVX'];

  chart = window.echarts.init(holder);

  const dataPoints = [];
  for (let i = 0; i < matrix.length; i++) {
    for (let j = 0; j < matrix[i].length; j++) {
      dataPoints.push([i, j, Number(matrix[i][j]).toFixed(2)]);
    }
  }

  const option = {
    tooltip: { position: 'top' },
    grid: { height: '80%', top: '10%' },
    xAxis: { type: 'category', data: pairs, splitArea: { show: true } },
    yAxis: { type: 'category', data: pairs, splitArea: { show: true } },
    visualMap: {
      min: -1,
      max: 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: '15%',
      inRange: { color: ['#c0392b', '#f39c12', '#27ae60'] },
    },
    series: [
      {
        name: 'Correlation',
        type: 'heatmap',
        data: dataPoints,
        label: { show: true },
        emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0, 0, 0, 0.5)' } },
      },
    ],
  };

  chart.setOption(option);
}
