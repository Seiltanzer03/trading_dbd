// Seiltanzer Crypto Trading & Derivatives Terminal

const ASSETS = {
  BTC: { symbol: 'BTC', pair: 'btcusdt', name: 'Bitcoin', scale: 2 },
  ETH: { symbol: 'ETH', pair: 'ethusdt', name: 'Ethereum', scale: 2 },
  SOL: { symbol: 'SOL', pair: 'solusdt', name: 'Solana', scale: 3 },
};

let currentAsset = 'BTC';
let selectedExpiry = null;
let matrixData = null;
let summaryData = null;
let lastPrices = { BTC: null, ETH: null, SOL: null };
let binanceWs = null;

function byId(id) {
  return document.getElementById(id);
}

function fmtNum(val, dec = 2) {
  if (val == null || !Number.isFinite(Number(val))) return '—';
  return Number(val).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtMoney(val, dec = 2) {
  if (val == null || !Number.isFinite(Number(val))) return '—';
  return '$' + fmtNum(val, dec);
}

function fmtGex(val) {
  if (val == null || !Number.isFinite(Number(val))) return '—';
  const v = Number(val);
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return v.toFixed(0);
}

// ------------------------------------------------------------- Binance WS
function initBinanceWs() {
  const wsStatus = byId('ws-status-badge');
  const streams = 'btcusdt@trade/ethusdt@trade/solusdt@trade';
  const url = `wss://stream.binance.com:9443/ws/${streams}`;

  try {
    binanceWs = new WebSocket(url);
    binanceWs.onopen = () => {
      if (wsStatus) {
        wsStatus.textContent = '● BINANCE WS LIVE';
        wsStatus.style.color = '#50d99a';
      }
    };
    binanceWs.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const symbol = msg.s; // e.g. BTCUSDT
        const price = parseFloat(msg.p);
        if (!symbol || !price) return;

        let assetKey = null;
        if (symbol === 'BTCUSDT') assetKey = 'BTC';
        else if (symbol === 'ETHUSDT') assetKey = 'ETH';
        else if (symbol === 'SOLUSDT') assetKey = 'SOL';

        if (assetKey) {
          const prev = lastPrices[assetKey];
          lastPrices[assetKey] = price;
          if (assetKey === currentAsset) {
            updateLiveSpotPrice(price, prev);
          }
        }
      } catch (e) {
        // ignore parse error
      }
    };
    binanceWs.onerror = () => {
      if (wsStatus) {
        wsStatus.textContent = '○ WS RECONNECTING';
        wsStatus.style.color = '#f2c050';
      }
    };
    binanceWs.onclose = () => {
      if (wsStatus) {
        wsStatus.textContent = '○ WS DISCONNECTED';
        wsStatus.style.color = '#ff6371';
      }
      setTimeout(initBinanceWs, 5000);
    };
  } catch (err) {
    if (wsStatus) {
      wsStatus.textContent = '○ WS UNAVAILABLE';
      wsStatus.style.color = '#7994a5';
    }
  }
}

function updateLiveSpotPrice(price, prevPrice) {
  const el = byId('kpi-spot');
  if (!el) return;
  const dec = ASSETS[currentAsset]?.scale || 2;
  el.textContent = fmtMoney(price, dec);

  if (prevPrice != null && prevPrice !== price) {
    el.classList.remove('flash-up', 'flash-down');
    void el.offsetWidth; // trigger reflow
    el.classList.add(price > prevPrice ? 'flash-up' : 'flash-down');
  }
}

// ------------------------------------------------------------- REST Data
async function fetchMatrix(currency) {
  try {
    const res = await fetch(`/api/crypto/options-matrix?currency=${currency}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch crypto options matrix:', err);
    return null;
  }
}

async function fetchSummary() {
  try {
    const res = await fetch('/api/crypto/market-summary');
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch crypto market summary:', err);
    return null;
  }
}

// ------------------------------------------------------------- Rendering
function renderKpis() {
  if (!matrixData) return;
  const dec = ASSETS[currentAsset]?.scale || 2;
  const spotPrice = lastPrices[currentAsset] || matrixData.spot;
  const spotEl = byId('kpi-spot');
  if (spotEl) spotEl.textContent = fmtMoney(spotPrice, dec);

  // DVOL
  const dvolEl = byId('kpi-dvol');
  if (dvolEl) {
    dvolEl.textContent = matrixData.dvol != null ? fmtNum(matrixData.dvol, 2) + '%' : '—';
  }

  // 25Δ Skew
  const skewEl = byId('kpi-skew');
  const skewSub = byId('kpi-skew-sub');
  if (skewEl) {
    const sk = matrixData.skew_summary;
    if (sk && sk.rr != null) {
      const rrPct = sk.rr * 100;
      skewEl.textContent = (rrPct > 0 ? '+' : '') + fmtNum(rrPct, 2) + '%';
      skewEl.className = 'kpi-val ' + (rrPct > 1 ? 'pos' : rrPct < -1 ? 'neg' : '');
      if (skewSub) skewSub.textContent = sk.tilt ? `Наклон: ${sk.tilt}` : '25Δ Risk Reversal';
    } else {
      skewEl.textContent = '—';
      if (skewSub) skewSub.textContent = '25Δ Risk Reversal';
    }
  }

  // GEX Net
  const gexEl = byId('kpi-gex');
  const gexSub = byId('kpi-gex-sub');
  if (gexEl) {
    const gx = matrixData.gex_summary;
    if (gx && gx.total_gex != null) {
      gexEl.textContent = fmtGex(gx.total_gex);
      gexEl.className = 'kpi-val ' + (gx.total_gex > 0 ? 'pos' : 'neg');
      if (gexSub) {
        gexSub.textContent = gx.zero_flip != null ? `Flip: ${fmtMoney(gx.zero_flip, 0)}` : 'Net Dealer Gamma';
      }
    } else {
      gexEl.textContent = '—';
      if (gexSub) gexSub.textContent = 'Net Dealer Gamma';
    }
  }

  // Next Expiry
  const expEl = byId('kpi-next-exp');
  if (expEl && matrixData.expiries_list?.length > 0) {
    const firstExp = matrixData.expiries_list[0];
    const info = matrixData.matrix[firstExp];
    expEl.textContent = firstExp;
    const expSub = byId('kpi-next-exp-sub');
    if (expSub) expSub.textContent = `${info?.days || 1}d (08:00 UTC)`;
  }
}

function renderExpiryChips() {
  const container = byId('expiry-chips');
  if (!container || !matrixData || !matrixData.expiries_list) return;

  container.innerHTML = '';
  const expiries = matrixData.expiries_list;
  if (!selectedExpiry || !matrixData.matrix[selectedExpiry]) {
    selectedExpiry = expiries[0];
  }

  expiries.forEach((exp) => {
    const expInfo = matrixData.matrix[exp];
    const btn = document.createElement('button');
    btn.className = 'chip-btn' + (exp === selectedExpiry ? ' active' : '');
    btn.innerHTML = `${exp} <span class="chip-dte">${expInfo?.days || 0}d</span>`;
    btn.onclick = () => {
      selectedExpiry = exp;
      renderExpiryChips();
      renderOptionsTable();
      renderIvSmile();
    };
    container.appendChild(btn);
  });
}

function renderOptionsTable() {
  const tbody = byId('options-table-body');
  const statsEl = byId('chain-stats');
  if (!tbody || !matrixData || !selectedExpiry) return;

  const expData = matrixData.matrix[selectedExpiry];
  if (!expData || !expData.rows) {
    tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:24px;color:#70899a;">НЕТ ОПЦИОНОВ НА ВЫБРАННУЮ ЭКСПИРАЦИЮ</td></tr>';
    return;
  }

  if (statsEl) {
    statsEl.textContent = `Экспирация: ${selectedExpiry} · Дней до расчёта: ${expData.days} · Страйков: ${expData.strikes_count}`;
  }

  const spot = lastPrices[currentAsset] || matrixData.spot || 0;
  const rows = expData.rows;

  // Найти ближайший ATM страйк
  let closestStrike = null;
  let minDiff = Infinity;
  rows.forEach((r) => {
    const diff = Math.abs(r.strike - spot);
    if (diff < minDiff) {
      minDiff = diff;
      closestStrike = r.strike;
    }
  });

  let html = '';
  rows.forEach((r) => {
    const isAtm = r.strike === closestStrike;
    const c = r.call;
    const p = r.put;

    html += `
      <tr class="${isAtm ? 'atm-row' : ''}">
        <td class="call-val">${c?.delta != null ? c.delta.toFixed(2) : '—'}</td>
        <td class="call-val">${c?.iv ? c.iv.toFixed(1) + '%' : '—'}</td>
        <td class="oi-val">${c?.oi ? fmtNum(c.oi, 1) : '—'}</td>
        <td class="call-val">${c?.bid_usd ? fmtMoney(c.bid_usd) : '—'}</td>
        <td class="call-val">${c?.ask_usd ? fmtMoney(c.ask_usd) : '—'}</td>
        <td class="strike-cell">${fmtNum(r.strike, 0)}</td>
        <td class="put-val">${p?.bid_usd ? fmtMoney(p.bid_usd) : '—'}</td>
        <td class="put-val">${p?.ask_usd ? fmtMoney(p.ask_usd) : '—'}</td>
        <td class="oi-val">${p?.oi ? fmtNum(p.oi, 1) : '—'}</td>
        <td class="put-val">${p?.iv ? p.iv.toFixed(1) + '%' : '—'}</td>
        <td class="put-val">${p?.delta != null ? p.delta.toFixed(2) : '—'}</td>
      </tr>
    `;
  });

  tbody.innerHTML = html;
}

function renderIvSmile() {
  const chartEl = byId('iv-smile-plot');
  if (!chartEl || !matrixData || !selectedExpiry || typeof Plotly === 'undefined') return;

  const expData = matrixData.matrix[selectedExpiry];
  if (!expData || !expData.rows) return;

  const strikes = [];
  const callIvs = [];
  const putIvs = [];

  expData.rows.forEach((r) => {
    if (r.call?.iv && r.call.iv > 0 && r.call.iv < 250) {
      strikes.push(r.strike);
      callIvs.push(r.call.iv);
      putIvs.push(r.put?.iv || null);
    }
  });

  if (!strikes.length) return;
  const spot = lastPrices[currentAsset] || matrixData.spot || 0;

  const traces = [
    {
      x: strikes,
      y: callIvs,
      name: 'Call IV',
      mode: 'lines+markers',
      line: { color: '#4bd8e8', width: 2 },
      marker: { size: 4 },
    },
    {
      x: strikes,
      y: putIvs,
      name: 'Put IV',
      mode: 'lines+markers',
      line: { color: '#ff6371', width: 2, dash: 'dot' },
      marker: { size: 4 },
    },
  ];

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 36, r: 12, t: 10, b: 30 },
    font: { family: 'IBM Plex Mono, monospace', color: '#8da4b4', size: 9 },
    legend: { orientation: 'h', y: 1.15, x: 0, font: { size: 8 } },
    xaxis: {
      gridcolor: 'rgba(80,140,180,0.12)',
      tickfont: { size: 8 },
    },
    yaxis: {
      gridcolor: 'rgba(80,140,180,0.12)',
      ticksuffix: '%',
      tickfont: { size: 8 },
    },
    shapes: [
      {
        type: 'line',
        x0: spot,
        x1: spot,
        y0: 0,
        y1: 1,
        yref: 'paper',
        line: { color: '#ffd978', width: 1.5, dash: 'dash' },
      },
    ],
  };

  Plotly.react(chartEl, traces, layout, { displayModeBar: false, responsive: true });
}

function renderTermStructure() {
  const chartEl = byId('term-structure-plot');
  if (!chartEl || !matrixData || !matrixData.term_structure || typeof Plotly === 'undefined') return;

  const pts = matrixData.term_structure;
  if (!pts.length) return;

  const days = pts.map((p) => p[0]);
  const ivs = pts.map((p) => p[1] * 100);

  const trace = {
    x: days,
    y: ivs,
    mode: 'lines+markers',
    line: { color: '#50d99a', width: 2.5 },
    marker: { size: 6, color: '#50d99a' },
    hovertemplate: 'Дней: %{x}<br>ATM IV: %{y:.1f}%<extra></extra>',
  };

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 36, r: 12, t: 10, b: 30 },
    font: { family: 'IBM Plex Mono, monospace', color: '#8da4b4', size: 9 },
    xaxis: {
      title: { text: 'Дней до экспирации (DTE)', font: { size: 8 } },
      gridcolor: 'rgba(80,140,180,0.12)',
      tickfont: { size: 8 },
    },
    yaxis: {
      gridcolor: 'rgba(80,140,180,0.12)',
      ticksuffix: '%',
      tickfont: { size: 8 },
    },
  };

  Plotly.react(chartEl, [trace], layout, { displayModeBar: false, responsive: true });
}

function renderGexCard() {
  const gx = matrixData?.gex_summary;
  const listEl = byId('gex-top-list');
  if (!listEl) return;

  if (!gx || !gx.top_levels || !gx.top_levels.length) {
    listEl.innerHTML = '<div style="color:#6d8899;font-size:10px;">Нет данных гамма-экспозиции</div>';
    return;
  }

  let html = '';
  gx.top_levels.forEach((lvl) => {
    const isPos = lvl.gex >= 0;
    html += `
      <div class="gex-top-item">
        <span class="metric-key">Страйк ${fmtMoney(lvl.strike, 0)}:</span>
        <span class="metric-val ${isPos ? 'pos' : 'neg'}">${isPos ? '+' : ''}${fmtGex(lvl.gex)}</span>
      </div>
    `;
  });

  listEl.innerHTML = html;
}

// ------------------------------------------------------------- Refresh Cycle
async function refreshAsset(asset) {
  currentAsset = asset;
  selectedExpiry = null;

  // Update button active state
  document.querySelectorAll('.asset-pill-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.asset === asset);
  });

  const loadingEl = byId('crypto-status');
  if (loadingEl) {
    loadingEl.textContent = `○ ЗАГРУЗКА ${asset}…`;
    loadingEl.className = 'status-pill no-data';
  }

  matrixData = await fetchMatrix(asset);
  if (matrixData) {
    if (loadingEl) {
      loadingEl.textContent = `● DERIBIT ${asset} LIVE`;
      loadingEl.className = 'status-pill fresh';
    }
    renderKpis();
    renderExpiryChips();
    renderOptionsTable();
    renderIvSmile();
    renderTermStructure();
    renderGexCard();
  } else {
    if (loadingEl) {
      loadingEl.textContent = `○ ОШИБКА ${asset}`;
      loadingEl.className = 'status-pill stale';
    }
  }
}

function initModeSwitcher() {
  const derBtn = byId('tab-derivatives');
  const globBtn = byId('tab-global');
  const derView = byId('derivatives-mode-view');
  const globView = byId('global-mode-view');

  if (derBtn && globBtn && derView && globView) {
    derBtn.onclick = () => {
      derBtn.classList.add('active');
      globBtn.classList.remove('active');
      derView.style.display = 'block';
      globView.style.display = 'none';
      renderIvSmile();
      renderTermStructure();
    };
    globBtn.onclick = () => {
      globBtn.classList.add('active');
      derBtn.classList.remove('active');
      derView.style.display = 'none';
      globView.style.display = 'block';
      window.dispatchEvent(new Event('resize'));
    };
  }
}

function initAssetButtons() {
  document.querySelectorAll('.asset-pill-btn').forEach((btn) => {
    btn.onclick = () => {
      const asset = btn.dataset.asset;
      if (asset && asset !== currentAsset) {
        refreshAsset(asset);
      }
    };
  });
}

// ------------------------------------------------------------- Init
document.addEventListener('DOMContentLoaded', () => {
  initModeSwitcher();
  initAssetButtons();
  initBinanceWs();
  refreshAsset('BTC');

  // Background polling every 10s
  setInterval(() => {
    fetchMatrix(currentAsset).then((data) => {
      if (data) {
        matrixData = data;
        renderKpis();
        renderOptionsTable();
        renderIvSmile();
        renderTermStructure();
        renderGexCard();
      }
    });
  }, 10000);
});
