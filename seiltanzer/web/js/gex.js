// GEX КЛЮЧЕВЫЕ УРОВНИ — контекст концентрации OI × модельной gamma.

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
        if (chart) {
            chart.dispose();
            chart = null;
        }
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
        if (chart) {
            chart.dispose();
            chart = null;
        }
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
        latest: latest.gex
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
    
    const strikes = data.latest.strikes.map(s => s * data.scale * liveMap);
    const net = data.latest.net;
    const priceStrike = liveData.price || data.price || 0;
    
    // Filter out extreme far OTM to keep chart zoomed in on action
    const displayRange = priceStrike * 0.15; // show +/- 15% from price
    
    const filteredStrikes = [];
    const filteredNet = [];
    
    for (let i = 0; i < strikes.length; i++) {
        if (Math.abs(strikes[i] - priceStrike) < displayRange) {
            filteredStrikes.push(strikes[i]);
            filteredNet.push(net[i]);
        }
    }
    
    const maxAbsNet = Math.max(...filteredNet.map(Math.abs));

    // Colors
    const colorCall = '#2ecc71';
    const colorPut = '#e74c3c';
    
    const seriesData = filteredNet.map((v, i) => {
        return {
            value: [v, filteredStrikes[i]],
            itemStyle: {
                color: v > 0 ? colorCall : colorPut,
                opacity: 0.8
            }
        };
    });

    const markLines = [];
    
    // Current Price Line
    if (priceStrike > 0) {
        markLines.push({
            yAxis: priceStrike,
            lineStyle: { color: '#e67e22', width: 2, type: 'dashed' },
            label: { formatter: 'PRICE\n{c}', position: 'start', color: '#e67e22' }
        });
    }
    
    // Trade lines
    if (liveData.trade) {
        if (liveData.trade.entry) {
            markLines.push({
                yAxis: liveData.trade.entry,
                lineStyle: { color: '#bdc3c7', width: 1, type: 'dashed' },
                label: { formatter: 'ENTRY', position: 'end', color: '#bdc3c7' }
            });
        }
        if (liveData.trade.stop) {
            markLines.push({
                yAxis: liveData.trade.stop,
                lineStyle: { color: '#c0392b', width: 2, type: 'solid' },
                label: { formatter: 'STOP', position: 'end', color: '#c0392b', fontWeight: 'bold' }
            });
        }
        if (liveData.trade.take) {
            markLines.push({
                yAxis: liveData.trade.take,
                lineStyle: { color: '#27ae60', width: 2, type: 'solid' },
                label: { formatter: 'TAKE', position: 'end', color: '#27ae60', fontWeight: 'bold' }
            });
        }
    }

    const option = {
        grid: { left: '8%', right: '8%', bottom: '5%', top: '5%', containLabel: true },
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            formatter: (params) => {
                const pt = params[0];
                const v = pt.value[0];
                const s = pt.value[1];
                const type = v > 0 ? 'CALL (Resistance)' : 'PUT (Support)';
                return `Strike: ${s.toFixed(2)}<br/>Net GEX: ${fmtVal(v)}<br/>${type}`;
            },
            backgroundColor: 'rgba(20,20,15,0.9)',
            textStyle: { color: '#fff', fontSize: 12 },
            borderColor: '#333'
        },
        xAxis: {
            type: 'value',
            splitLine: { show: false },
            axisLabel: { formatter: (v) => fmtVal(v), color: '#888' },
            max: maxAbsNet * 1.1,
            min: -maxAbsNet * 1.1
        },
        yAxis: {
            type: 'value',
            scale: true,
            splitLine: { show: true, lineStyle: { color: '#333', type: 'dashed' } },
            axisLabel: { color: '#888' }
        },
        series: [
            {
                type: 'bar',
                encode: { x: 0, y: 1 },
                data: seriesData,
                barCategoryGap: '20%',
                markLine: {
                    symbol: 'none',
                    data: markLines,
                    label: {
                        show: true,
                        backgroundColor: 'rgba(20,20,15,0.8)',
                        padding: 4,
                        borderRadius: 4
                    }
                }
            }
        ]
    };

    chart.setOption(option);
}
