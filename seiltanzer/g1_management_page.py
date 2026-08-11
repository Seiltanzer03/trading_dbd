"""Small research-only Management Edge cockpit."""
from __future__ import annotations

from fastapi.responses import HTMLResponse


MANAGEMENT_EDGE_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Seiltanzer · Management Edge</title>
<style>
:root{color-scheme:dark;background:#090d0f;color:#d8e1e5;font-family:Inter,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;padding:22px}.wrap{max-width:1320px;margin:auto}.top{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.sub{color:#829098;font-size:13px}.badge{border:1px solid #31505c;border-radius:999px;padding:7px 11px;font-size:12px}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px;margin-top:16px}.card{grid-column:span 3;background:#101619;border:1px solid #202d33;border-radius:13px;padding:15px}.wide{grid-column:span 12}.half{grid-column:span 6}.k{font-size:11px;letter-spacing:.1em;color:#80919a}.v{font-size:27px;margin-top:7px}.note{font-size:12px;color:#829098;margin-top:7px;line-height:1.45}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:right;border-bottom:1px solid #1d292f;padding:9px}th:first-child,td:first-child{text-align:left}.good{color:#82d8a2}.bad{color:#e38c8c}.muted{color:#7c8990}button,a{color:#b9d4df;background:#111a1e;border:1px solid #2a3e47;border-radius:8px;padding:8px 10px;text-decoration:none}button{cursor:pointer}@media(max-width:800px){body{padding:12px}.card,.half{grid-column:span 12}.v{font-size:22px}table{font-size:11px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="k">SEILTANZER · RESEARCH ONLY</div><h1 style="margin:5px 0">MANAGEMENT EDGE</h1><div class="sub">Доказывает, добавляет ли управление открытой позицией value относительно HOLD / ORIGINAL PLAN / EXIT.</div></div><div><a href="/intelligence">Intelligence Lab</a> <button id="refresh">Обновить</button></div></div>
<div class="grid">
<div class="card"><div class="k">STATUS</div><div class="v" id="status">—</div><div class="note" id="authority">production authority OFF</div></div>
<div class="card"><div class="k">OBSERVATIONS</div><div class="v" id="obs">—</div><div class="note" id="resolved">—</div></div>
<div class="card"><div class="k">UNIQUE TRADES</div><div class="v" id="trades">—</div><div class="note" id="effective">—</div></div>
<div class="card"><div class="k">READY FOR OOS</div><div class="v" id="ready">—</div><div class="note" id="blockers">—</div></div>
<div class="card half"><div class="k">PRODUCTION POLICY vs HOLD</div><div class="v" id="mva">—</div><div class="note" id="mva-note">—</div></div>
<div class="card half"><div class="k">PROTECTION TRADE-OFF</div><div class="v" id="protection">—</div><div class="note" id="upside">—</div></div>
<div class="card wide"><div class="k">ACTION MATRIX</div><div style="overflow:auto"><table><thead><tr><th>Policy</th><th>N</th><th>Trades</th><th>Mean ΔR vs HOLD</th><th>Median</th><th>Win vs HOLD</th><th>CVaR10 ΔR</th></tr></thead><tbody id="policies"></tbody></table></div></div>
<div class="card wide"><div class="k">RECENT MANAGEMENT OBSERVATIONS</div><div style="overflow:auto"><table><thead><tr><th>Observation</th><th>Trade</th><th>Production</th><th>Origin</th><th>Resolved</th><th>Best realized</th><th>Regret R</th><th>Compliance</th></tr></thead><tbody id="recent"></tbody></table></div></div>
</div></div>
<script>
const fmt=(v,d=3)=>v==null?'—':Number(v).toFixed(d);const pct=v=>v==null?'—':(Number(v)*100).toFixed(1)+'%';
async function json(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(url+' '+r.status);return r.json()}
async function load(){try{
 const [s,e,p,o]=await Promise.all([json('/api/research/g1/management/status'),json('/api/research/g1/management/edge'),json('/api/research/g1/management/policies'),json('/api/research/g1/management/observations?limit=25')]);
 status.textContent=s.evidence_status;obs.textContent=s.observations;resolved.textContent=`resolved ${s.resolved} · pending ${s.pending}`;trades.textContent=s.unique_trades;effective.textContent=`effective N ${fmt(s.effective_n,1)}`;ready.textContent=s.ready_for_oos?'YES':'NO';ready.className='v '+(s.ready_for_oos?'good':'muted');blockers.textContent=(s.readiness_blockers||[]).join(', ')||'Нет blockers';authority.textContent='Research only · production authority OFF · edge claim OFF';
 mva.textContent=fmt(e.dependency_adjusted_mean_mva_r)+'R';mva.className='v '+(Number(e.dependency_adjusted_mean_mva_r||0)>0?'good':Number(e.dependency_adjusted_mean_mva_r||0)<0?'bad':'muted');mva_note.textContent=`raw N ${e.raw?.n||0} · win vs HOLD ${pct(e.win_vs_hold_rate)} · median ${fmt(e.raw?.median)}R`;protection.textContent=fmt(e.downside_saved_r)+'R saved';upside.textContent=`upside sacrificed ${fmt(e.upside_sacrificed_r)}R`;
 policies.innerHTML=(p.items||[]).map(x=>`<tr><td>${x.policy}</td><td>${x.raw_n}</td><td>${x.unique_trades}</td><td>${fmt(x.mva_vs_hold?.mean)}</td><td>${fmt(x.mva_vs_hold?.median)}</td><td>${pct(x.win_vs_hold_rate)}</td><td>${fmt(x.mva_vs_hold?.cvar10)}</td></tr>`).join('');
 recent.innerHTML=(o.items||[]).map(x=>`<tr><td><a href="/api/research/g1/management/decision/${encodeURIComponent(x.observation_id)}">${x.observation_id.slice(0,14)}…</a></td><td>${x.trade_id}</td><td>${x.production_policy}</td><td>${x.origin}</td><td>${x.resolved_ts?'YES':'PENDING'}</td><td>${x.realized_best_action||'—'}</td><td>${fmt(x.production_regret_r)}</td><td>${x.compliance_state||'—'}</td></tr>`).join('');
 }catch(err){status.textContent='UNAVAILABLE';blockers.textContent=err.message}}
refresh.addEventListener('click',load);load();setInterval(load,60000);
</script></body></html>'''


def management_edge_page() -> HTMLResponse:
    return HTMLResponse(MANAGEMENT_EDGE_HTML, headers={
        "X-Seiltanzer-Management-Edge": "g1m-management-cockpit-v1"
    })
