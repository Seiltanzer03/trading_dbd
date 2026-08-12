"""G.1E presentation refinements for two-speed research learning."""
from __future__ import annotations

from fastapi.responses import HTMLResponse

from .g1_intelligence_page import INTELLIGENCE_HTML


PAGE_REFINEMENT_VERSION = "g1e-two-speed-learning-presentation-v1"

_FAST_SECTION = r'''
 <section class="card span8 research" id="g1s-card"><div class="section-title"><div class="k">FAST MARKET LEARNING</div><span class="pill">G.1S · PHYSICAL 15–240m · RESEARCH ONLY</span></div><div id="g1s-summary" class="small">Загрузка short-horizon evidence…</div><div class="table-wrap" style="margin-top:8px"><table><thead><tr><th>HORIZON</th><th>STATE</th><th>RESOLVED</th><th>EFFECTIVE N</th><th>UP / DOWN</th><th>BASELINE BRIER</th><th>MODELS</th></tr></thead><tbody id="g1s-horizons"></tbody></table></div></section>
 <section class="card span4 research" id="q-maturity-card"><div class="section-title"><div class="k">OPTION Q · SLOW STRUCTURAL</div><span class="pill">NATIVE EXPIRY</span></div><div id="q-maturity" class="small">Аудит pending Q…</div></section>
 <section class="card span12 research" id="g1m-local-card"><div class="section-title"><div class="k">LOCAL MANAGEMENT FEEDBACK</div><span class="stamp">15 / 30 / 60 / 120m · отдельно от terminal G.1-M</span></div><div id="g1m-local" class="small">Загрузка…</div></section>
'''

_RELIABILITY_SECTION = r'''
 <section class="card span12" id="reliability-card"><div class="section-title"><div class="k">RAW Q → OBSERVED FREQUENCY</div><span class="stamp">reliability · G.1B authoritative bins</span></div><div class="grid" style="grid-template-columns:repeat(12,minmax(0,1fr));gap:10px"><div class="span8"><svg class="svg" id="reliability" viewBox="0 0 500 260" preserveAspectRatio="none"></svg></div><div class="span4"><div id="reliability-note" class="small">Загрузка…</div></div></div></section>
'''

_TWO_SPEED_SCRIPT = r'''
<script>
(function(){
 const fmt=n=>n==null?'—':Number(n).toLocaleString('ru-RU');
 const f4=n=>n==null?'—':Number(n).toFixed(4);
 const when=t=>t?new Date(Number(t)*1000).toLocaleString('ru-RU',{hour12:false}):'—';
 async function get(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(url+' '+r.status);return r.json()}
 function renderFast(s){
   const body=document.querySelector('#g1s-horizons'), summary=document.querySelector('#g1s-summary'); if(!body||!summary)return;
   const hs=s.horizons||[];
   summary.innerHTML=`Frozen observations <b>${fmt(s.observations)}</b> · resolved <b>${fmt(s.resolved)}</b> · pending ${fmt(s.pending)} · shadow models ${fmt(s.models)}. <b>Production authority OFF.</b>`;
   body.innerHTML=hs.map(h=>{const b=h.baselines?.constant_0_5||{};return `<tr><td><b>H${h.horizon_minutes}</b></td><td>${h.state||'—'}</td><td>${fmt(h.raw_resolved)}</td><td>${fmt(h.effective_n)}</td><td>${fmt(h.positive_n)} / ${fmt(h.negative_n)}</td><td>${f4(b.brier)}</td><td>${fmt(h.model_n)}</td></tr>`}).join('')||'<tr><td colspan="7">Пока нет short-horizon rows.</td></tr>';
 }
 function renderQ(a){
   const host=document.querySelector('#q-maturity'); if(!host)return; const c=a.counts||{};
   const overdue=Number(c.DUE_BUT_NOT_RESOLVED||0); host.innerHTML=`<div class="model-row"><span>Not due yet</span><b>${fmt(c.NOT_DUE_YET||0)}</b></div><div class="model-row"><span>Resolved</span><b>${fmt(c.RESOLVED||0)}</b></div><div class="model-row"><span>Overdue with coverage</span><b class="${overdue?'bad':'ok'}">${fmt(overdue)}</b></div><div class="model-row"><span>Resolution blocked</span><b>${fmt(c.RESOLUTION_BLOCKED||0)}</b></div><div class="model-row"><span>Contract rejected</span><b>${fmt(c.CONTRACT_REJECTED||0)}</b></div><div class="small" style="margin-top:8px">Earliest pending expiry: ${when(a.earliest_pending_target_ts)}.<br>${overdue?'OVERDUE_RESOLUTION — это ошибка, а не нормальное ожидание.':'Native-expiry Q остаётся медленным структурным контуром и не сокращается искусственно.'}</div>`;
 }
 function renderLocal(s,e){
   const host=document.querySelector('#g1m-local'); if(!host)return; const rows=(e.items||[]).map(x=>`H${x.horizon_minutes}: resolved ${fmt(x.raw_n)} · trades ${fmt(x.unique_trades)} · mean ΔR vs HOLD ${x.mean_mva_vs_hold_r==null?'—':Number(x.mean_mva_vs_hold_r).toFixed(3)}`).join(' &nbsp; | &nbsp; ');
   host.innerHTML=`Windows <b>${fmt(s.windows)}</b> · resolved <b>${fmt(s.resolved)}</b> · evidence-eligible resolved <b>${fmt(s.eligible_resolved)}</b>.<br>${rows||'Первые live G.1-M.1 windows ещё созревают.'}<br><span class="small">LOCAL_DECISION_QUALITY не заменяет terminal management edge.</span>`;
 }
 async function refreshTwoSpeed(){
   try{const [s,q,ls,le]=await Promise.all([get('/api/research/g1s/status'),get('/api/research/g1/q/audit?limit=2000'),get('/api/research/g1/management/local-status'),get('/api/research/g1/management/local-edge')]);renderFast(s);renderQ(q);renderLocal(ls,le)}catch(e){for(const id of ['#g1s-summary','#q-maturity','#g1m-local']){const n=document.querySelector(id);if(n)n.textContent='Research API unavailable: '+e.message}}
 }
 window.addEventListener('load',refreshTwoSpeed); const btn=document.querySelector('#refresh');if(btn)btn.addEventListener('click',refreshTwoSpeed);setInterval(refreshTwoSpeed,60000);
})();
</script>
'''

_RELIABILITY_SCRIPT = r'''
<script>
(function(){
 const svgNS='http://www.w3.org/2000/svg';
 const x=p=>28+Math.max(0,Math.min(1,Number(p)))*444;
 const y=p=>232-Math.max(0,Math.min(1,Number(p)))*204;
 function el(name,attrs,text){const n=document.createElementNS(svgNS,name);for(const [k,v] of Object.entries(attrs||{}))n.setAttribute(k,String(v));if(text!=null)n.textContent=String(text);return n}
 function draw(rel){
   const svg=document.querySelector('#reliability'), note=document.querySelector('#reliability-note'); if(!svg||!note)return; while(svg.firstChild)svg.removeChild(svg.firstChild);
   const bins=(rel&&rel.bins)||[], used=bins.filter(b=>Number(b.n)>0&&b.avg_probability!=null&&b.empirical_rate!=null);
   svg.appendChild(el('line',{x1:x(0),y1:y(0),x2:x(1),y2:y(1),stroke:'#44555d','stroke-dasharray':'6 5','stroke-width':1.5}));svg.appendChild(el('line',{x1:x(0),y1:y(0),x2:x(1),y2:y(0),stroke:'#263238'}));svg.appendChild(el('line',{x1:x(0),y1:y(0),x2:x(0),y2:y(1),stroke:'#263238'}));
   for(const t of [0,.25,.5,.75,1]){svg.appendChild(el('text',{x:x(t),y:250,'text-anchor':'middle',fill:'#77878f','font-size':10},Math.round(t*100)+'%'));svg.appendChild(el('text',{x:22,y:y(t)+3,'text-anchor':'end',fill:'#77878f','font-size':10},Math.round(t*100)+'%'))}
   if(used.length){const points=used.map(b=>`${x(b.avg_probability)},${y(b.empirical_rate)}`).join(' ');svg.appendChild(el('polyline',{points,fill:'none',stroke:'#77d39b','stroke-width':2.5,'vector-effect':'non-scaling-stroke'}));for(const b of used){const c=el('circle',{cx:x(b.avg_probability),cy:y(b.empirical_rate),r:Math.min(8,3+Math.sqrt(Number(b.n)||1)),fill:'#7db7d8',stroke:'#0c1215','stroke-width':1});c.appendChild(el('title',{},`Q ${(Number(b.avg_probability)*100).toFixed(1)}% → факт ${(Number(b.empirical_rate)*100).toFixed(1)}% · n=${b.n}`));svg.appendChild(c)}note.innerHTML=`Диагональ — идеальная калибровка. Точки — только непустые authoritative bins.<br><br><b>n=${rel.n||0}</b> · ECE ${rel.ece==null?'—':Number(rel.ece).toFixed(4)} · MCE ${rel.mce==null?'—':Number(rel.mce).toFixed(4)}.`}else note.textContent='Пока нет завершённых clean native-expiry Q-наблюдений. Это не мешает отдельному G.1S fast-learning.';
 }
 async function refreshReliability(){try{const r=await fetch('/api/research/g1/intelligence/forecast-quality',{cache:'no-store'});if(!r.ok)throw new Error(String(r.status));const q=await r.json();draw(q.status?.terminal_q_identity?.direction_event?.q_identity?.reliability||{})}catch(e){const note=document.querySelector('#reliability-note');if(note)note.textContent='Reliability unavailable: '+e.message}}
 window.addEventListener('load',refreshReliability);const refresh=document.querySelector('#refresh');if(refresh)refresh.addEventListener('click',refreshReliability);setInterval(refreshReliability,60000);
})();
</script>
'''


def intelligence_page() -> HTMLResponse:
    marker = ' <section class="card span12"><div class="section-title"><div class="k">WAITING FOR OUTCOME</div>'
    if marker not in INTELLIGENCE_HTML:
        raise RuntimeError("Intelligence page layout marker missing")
    html = INTELLIGENCE_HTML.replace(
        marker, _FAST_SECTION + _RELIABILITY_SECTION + marker, 1)
    html = html.replace("</body>", _TWO_SPEED_SCRIPT + _RELIABILITY_SCRIPT + "</body>", 1)
    return HTMLResponse(html, headers={"X-Seiltanzer-Intelligence-Page": PAGE_REFINEMENT_VERSION})
