// GEX КЛЮЧЕВЫЕ УРОВНИ — контекст концентрации OI × модельной gamma.
// Полная переработка: убираем нулевые страйки, обрезаем выбросы,
// добавляем текстовую интерпретацию для трейдера.

import { $ } from './util.js';

let chart = null;
let emptyEl, statusEl;
let data = null;
let liveData = { price: 0, proxyPrice: 0, trade: null };

export function initGex() {
    emptyEl = $('#gex-evol-empty');
    statusEl = $('#gex-evol-status');
}

export function updateGex(ridgePayload) {
    if (!ridgePayload || !ridgePayload.available || !ridgePayload.snapshots || ridgePayload.snapshots.length < 1) {
        data = null;
        if (emptyEl) emptyEl.style.display = 'flex';
        if (chart) { chart.dispose(); chart = null; }
        if (statusEl) statusEl.textContent = '○ GEX НЕДОСТУПЕН';
        return;
    }
    
    const latest = ridgePayload.snapshots[ridgePayload.snapshots.length - 1];
    if (!latest?.gex?.available || !latest.gex.strikes?.length) {
        data = null;
        if (emptyEl) {
            emptyEl.style.display = 'flex';
            emptyEl.textContent = '○ GEX КОНТЕКСТ ОТКЛЮЧЁН ДЛЯ ЭТОГО PROXY';
        }
        if (chart) { chart.dispose(); chart = null; }
        if (statusEl) statusEl.textContent = '○ GEX CONTEXT ONLY';
        return;
    }
    
    data = {
        snaps: ridgePayload.snapshots,
        scale: ridgePayload.scale || 1.0,
        price: ridgePayload.price,
        proxyPrice: ridgePayload.proxy_spot_current,
        transform: ridgePayload.proxy_transform || 'direct',
        instrument: ridgePayload.instrument || null,
        latest: latest.gex,
        oiWalls: ridgePayload.oi_walls || null,
        zeroFlip: latest.gex.zero_flip,
        top: latest.gex.top
    };
    
    if (emptyEl) emptyEl.style.display = 'none';
    if (statusEl) statusEl.textContent = '● OI-GEX HEURISTIC (ECHARTS)';
    renderGex();
}

export function updateLiveGex(live) {
    if (live.price !== undefined) liveData.price = live.price;
    if (live.proxyPrice !== undefined) liveData.proxyPrice = live.proxyPrice;
    if (live.trade !== undefined) liveData.trade = live.trade;
    if (data) renderGex();
}

function fmtVal(v) {
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(0) + 'K';
    return v.toFixed(0);
}

function renderGex() {
    const el = $('#gex-evol-canvas');
    if (!el || !data) return;
    
    if (!chart && window.echarts) {
        chart = window.echarts.init(el);
        new ResizeObserver(() => { if (chart) chart.resize(); }).observe(el);
    }
    if (!chart) return;

    const instrumentFactor = (liveData.price && data.price) ? liveData.price / data.price : 1.0;
    const proxyFactor = (liveData.proxyPrice && data.proxyPrice) ? liveData.proxyPrice / data.proxyPrice : 1.0;
    const liveMap = data.transform === 'inverse' ? instrumentFactor * proxyFactor : instrumentFactor / proxyFactor;
    
    const rawStrikes = data.latest.strikes.map(s => s * data.scale * liveMap);
    const rawNet = data.latest.net;
    const priceStrike = liveData.price || data.price || 0;
    
    // ========== STEP 1: Filter out zero/near-zero strikes ==========
    const pairs = [];
    for (let i = 0; i < rawStrikes.length; i++) {
        if (rawNet[i] !== 0) {
            pairs.push({ strike: rawStrikes[i], net: rawNet[i] });
        }
    }
    
    if (pairs.length === 0) {
        // All zeros — show empty
        chart.setOption({
            title: { text: 'GEX: все значения = 0', left: 'center', top: 'center', textStyle: { color: '#999', fontSize: 14 } },
            series: []
        });
        return;
    }
    
    // ========== STEP 2: Clamp outliers using IQR method ==========
    const absValues = pairs.map(p => Math.abs(p.net)).sort((a, b) => a - b);
    const q75Idx = Math.floor(absValues.length * 0.75);
    const q25Idx = Math.floor(absValues.length * 0.25);
    const q75 = absValues[q75Idx] || 1;
    const q25 = absValues[q25Idx] || 0;
    const iqr = q75 - q25;
    const clampThreshold = q75 + iqr * 3; // Very generous — only extreme outliers
    
    const clampedPairs = pairs.map(p => ({
        strike: p.strike,
        net: p.net,
        clamped: Math.abs(p.net) > clampThreshold 
            ? Math.sign(p.net) * clampThreshold 
            : p.net,
        isOutlier: Math.abs(p.net) > clampThreshold
    }));
    
    // ========== STEP 3: Sort by strike price ==========
    clampedPairs.sort((a, b) => a.strike - b.strike);
    
    // ========== STEP 4: Take region around current price ==========
    let closestIdx = 0;
    let minDiff = Infinity;
    for (let i = 0; i < clampedPairs.length; i++) {
        const diff = Math.abs(clampedPairs[i].strike - priceStrike);
        if (diff < minDiff) { minDiff = diff; closestIdx = i; }
    }
    const span = Math.min(20, Math.floor(clampedPairs.length / 2));
    const startIdx = Math.max(0, closestIdx - span);
    const endIdx = Math.min(clampedPairs.length - 1, closestIdx + span);
    const visible = clampedPairs.slice(startIdx, endIdx + 1);
    
    if (visible.length === 0) return;
    
    // ========== STEP 5: Identify key levels ==========
    const sorted = [...visible].sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
    const topN = sorted.slice(0, 5);
    const biggestCall = topN.filter(p => p.net > 0).sort((a, b) => b.net - a.net)[0];
    const biggestPut = topN.filter(p => p.net < 0).sort((a, b) => a.net - b.net)[0];
    
    // ========== STEP 6: Build chart ==========
    const maxAbs = Math.max(...visible.map(p => Math.abs(p.clamped)));
    
    const yCategories = visible.map(p => p.strike.toFixed(1));
    const seriesData = visible.map((p, i) => ({
        value: p.clamped,
        itemStyle: {
            color: p.net > 0 ? '#27ae60' : '#c0392b',
            opacity: p.isOutlier ? 1.0 : 0.75,
            borderColor: p.isOutlier ? (p.net > 0 ? '#1a7a42' : '#8e2a20') : 'transparent',
            borderWidth: p.isOutlier ? 2 : 0
        },
        label: {
            show: Math.abs(p.net) >= (sorted[2]?.net ? Math.abs(sorted[2].net) : 0),
            position: p.net > 0 ? 'right' : 'left',
            formatter: () => fmtVal(p.net) + (p.isOutlier ? ' ⚠' : ''),
            color: '#444',
            fontSize: 11,
            fontWeight: 'bold'
        }
    }));
    
    // Mark lines
    const markLines = [];
    if (priceStrike > 0) {
        markLines.push({
            yAxis: priceStrike.toFixed(1),
            lineStyle: { color: '#e67e22', width: 2, type: 'solid' },
            label: { formatter: `PRICE ${priceStrike.toFixed(1)}`, position: 'insideStartTop', color: '#e67e22', fontWeight: 'bold', fontSize: 12 }
        });
    }
    if (data.zeroFlip) {
        const flipScaled = data.zeroFlip * data.scale * liveMap;
        markLines.push({
            yAxis: flipScaled.toFixed(1),
            lineStyle: { color: '#9b59b6', width: 2, type: 'dashed' },
            label: { formatter: `FLIP ${flipScaled.toFixed(1)}`, position: 'insideEndTop', color: '#9b59b6', fontWeight: 'bold' }
        });
    }
    if (liveData.trade) {
        if (liveData.trade.entry) markLines.push({ yAxis: liveData.trade.entry.toFixed(1), lineStyle: { color: '#95a5a6', width: 1, type: 'dashed' }, label: { formatter: 'ENTRY', position: 'insideEndTop', color: '#95a5a6' } });
        if (liveData.trade.stop) markLines.push({ yAxis: liveData.trade.stop.toFixed(1), lineStyle: { color: '#e74c3c', width: 2 }, label: { formatter: 'STOP', position: 'insideEndTop', color: '#e74c3c', fontWeight: 'bold' } });
        if (liveData.trade.take) markLines.push({ yAxis: liveData.trade.take.toFixed(1), lineStyle: { color: '#2ecc71', width: 2 }, label: { formatter: 'TAKE', position: 'insideEndTop', color: '#2ecc71', fontWeight: 'bold' } });
    }
    
    // ========== STEP 7: Build interpretation text ==========
    const interpretEl = document.getElementById('gex-interpretation');
    if (interpretEl) {
        const parts = [];
        if (data.zeroFlip) {
            const flipScaled = data.zeroFlip * data.scale * liveMap;
            const side = priceStrike > flipScaled ? 'ВЫШЕ FLIP (positive gamma territory — mean-reversion zone)' : 'НИЖЕ FLIP (negative gamma — momentum/trend zone)';
            parts.push(`<b>Цена ${side}</b>`);
        }
        if (biggestCall) parts.push(`🟢 Макс. CALL GEX: ${biggestCall.strike.toFixed(1)} (${fmtVal(biggestCall.net)}) — условное сопротивление`);
        if (biggestPut) parts.push(`🔴 Макс. PUT GEX: ${biggestPut.strike.toFixed(1)} (${fmtVal(biggestPut.net)}) — условная поддержка`);
        if (data.top) {
            parts.push(`📌 Top OI: ${(data.top * data.scale * liveMap).toFixed(1)}`);
        }
        interpretEl.innerHTML = parts.join('<br>');
        interpretEl.style.display = parts.length ? 'block' : 'none';
    }

    const option = {
        animation: false,
        grid: { left: 80, right: 80, bottom: 30, top: 10, containLabel: false },
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            formatter: (params) => {
                if (!params[0]) return '';
                const strike = params[0].name;
                const p = visible[params[0].dataIndex];
                if (!p) return '';
                const type = p.net > 0 ? '🟢 CALL (условное сопротивление)' : '🔴 PUT (условная поддержка)';
                const dist = priceStrike ? ((p.strike - priceStrike) / priceStrike * 100).toFixed(2) + '%' : '—';
                return `<b>Strike: ${strike}</b><br/>Net GEX: ${fmtVal(p.net)}${p.isOutlier ? ' ⚠ ВЫБРОС' : ''}<br/>${type}<br/>До цены: ${dist}`;
            },
            backgroundColor: 'rgba(255,255,255,0.97)',
            textStyle: { color: '#333', fontSize: 12 },
            borderColor: '#ddd'
        },
        xAxis: {
            type: 'value',
            splitLine: { show: true, lineStyle: { color: '#f0f0f0' } },
            axisLabel: { formatter: (v) => fmtVal(v), color: '#888' },
            axisLine: { lineStyle: { color: '#ccc' } }
        },
        yAxis: {
            type: 'category',
            data: yCategories,
            axisLabel: { color: '#666', fontSize: 10 },
            axisLine: { lineStyle: { color: '#ccc' } },
            splitLine: { show: false }
        },
        series: [
            {
                type: 'bar',
                data: seriesData,
                barWidth: '60%',
                markLine: {
                    symbol: 'none',
                    data: markLines,
                    label: { show: true, padding: [2, 6], borderRadius: 3 }
                }
            }
        ]
    };

    chart.setOption(option, true); // true = replace old option completely
}
