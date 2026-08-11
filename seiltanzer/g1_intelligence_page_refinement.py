"""Presentation refinement: reliability curve for the Intelligence Lab.

The browser only maps authoritative G.1B bin coordinates to SVG pixels; it does
not recompute Brier, empirical rates, eligibility or Q/P math.
"""
from __future__ import annotations

from fastapi.responses import HTMLResponse

from .g1_intelligence_page import INTELLIGENCE_HTML


PAGE_REFINEMENT_VERSION = "g1e-reliability-presentation-v1"

_RELIABILITY_SECTION = r'''
 <section class="card span12" id="reliability-card"><div class="section-title"><div class="k">RAW Q → OBSERVED FREQUENCY</div><span class="stamp">reliability · G.1B authoritative bins</span></div><div class="grid" style="grid-template-columns:repeat(12,minmax(0,1fr));gap:10px"><div class="span8"><svg class="svg" id="reliability" viewBox="0 0 500 260" preserveAspectRatio="none"></svg></div><div class="span4"><div id="reliability-note" class="small">Загрузка…</div></div></div></section>
'''

_RELIABILITY_SCRIPT = r'''
<script>
(function(){
 const svgNS='http://www.w3.org/2000/svg';
 const x=p=>28+Math.max(0,Math.min(1,Number(p)))*444;
 const y=p=>232-Math.max(0,Math.min(1,Number(p)))*204;
 function el(name,attrs,text){const n=document.createElementNS(svgNS,name);for(const [k,v] of Object.entries(attrs||{}))n.setAttribute(k,String(v));if(text!=null)n.textContent=String(text);return n}
 function draw(rel){
   const svg=document.querySelector('#reliability'), note=document.querySelector('#reliability-note');
   if(!svg||!note)return;
   while(svg.firstChild)svg.removeChild(svg.firstChild);
   const bins=(rel&&rel.bins)||[], used=bins.filter(b=>Number(b.n)>0&&b.avg_probability!=null&&b.empirical_rate!=null);
   svg.appendChild(el('line',{x1:x(0),y1:y(0),x2:x(1),y2:y(1),stroke:'#44555d','stroke-dasharray':'6 5','stroke-width':1.5}));
   svg.appendChild(el('line',{x1:x(0),y1:y(0),x2:x(1),y2:y(0),stroke:'#263238'}));
   svg.appendChild(el('line',{x1:x(0),y1:y(0),x2:x(0),y2:y(1),stroke:'#263238'}));
   for(const t of [0,.25,.5,.75,1]){
     svg.appendChild(el('text',{x:x(t),y:250,'text-anchor':'middle',fill:'#77878f','font-size':10},Math.round(t*100)+'%'));
     svg.appendChild(el('text',{x:22,y:y(t)+3,'text-anchor':'end',fill:'#77878f','font-size':10},Math.round(t*100)+'%'));
   }
   if(used.length){
     const points=used.map(b=>`${x(b.avg_probability)},${y(b.empirical_rate)}`).join(' ');
     svg.appendChild(el('polyline',{points,fill:'none',stroke:'#77d39b','stroke-width':2.5,'vector-effect':'non-scaling-stroke'}));
     for(const b of used){
       const c=el('circle',{cx:x(b.avg_probability),cy:y(b.empirical_rate),r:Math.min(8,3+Math.sqrt(Number(b.n)||1)),fill:'#7db7d8',stroke:'#0c1215','stroke-width':1});
       c.appendChild(el('title',{},`Q ${(Number(b.avg_probability)*100).toFixed(1)}% → факт ${(Number(b.empirical_rate)*100).toFixed(1)}% · n=${b.n}`));
       svg.appendChild(c);
     }
     note.innerHTML=`Диагональ — идеальная калибровка. Точки — только непустые authoritative bins.<br><br><b>n=${rel.n||0}</b> · ECE ${rel.ece==null?'—':Number(rel.ece).toFixed(4)} · MCE ${rel.mce==null?'—':Number(rel.mce).toFixed(4)}.<br><br>Малые bins не считаются доказательством edge.`;
   } else {
     note.textContent='Пока нет завершённых clean Q-наблюдений. Кривая появится автоматически после resolution; пустые bins не подменяются синтетикой.';
   }
 }
 async function refreshReliability(){
   try{const r=await fetch('/api/research/g1/intelligence/forecast-quality',{cache:'no-store'});if(!r.ok)throw new Error(String(r.status));const q=await r.json();draw(q.status?.terminal_q_identity?.direction_event?.q_identity?.reliability||{});}catch(e){const note=document.querySelector('#reliability-note');if(note)note.textContent='Reliability API unavailable: '+e.message;}
 }
 window.addEventListener('load',refreshReliability);
 const refresh=document.querySelector('#refresh');if(refresh)refresh.addEventListener('click',refreshReliability);
 setInterval(refreshReliability,60000);
})();
</script>
'''


def intelligence_page() -> HTMLResponse:
    marker = ' <section class="card span12"><div class="section-title"><div class="k">WAITING FOR OUTCOME</div>'
    if marker not in INTELLIGENCE_HTML:
        raise RuntimeError("Intelligence page layout marker missing")
    html = INTELLIGENCE_HTML.replace(marker, _RELIABILITY_SECTION + marker, 1)
    html = html.replace("</body>", _RELIABILITY_SCRIPT + "</body>", 1)
    return HTMLResponse(html, headers={"X-Seiltanzer-Intelligence-Page": PAGE_REFINEMENT_VERSION})
