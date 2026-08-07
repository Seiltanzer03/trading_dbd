import { $ } from './util.js';

let chart=null, emptyEl=null, statusEl=null, payload=null, graphData=null;
let currentMode='NETWORK', resizeObserver=null, refreshTimer=null, rafId=null;
const positions=new Map(); let draggedNodeId=null;

export function initCorrelation(){
  emptyEl=$('#corr-empty');statusEl=$('#corr-status');
  $('#btn-corr-network')?.addEventListener('click',()=>setMode('NETWORK'));
  $('#btn-corr-matrix')?.addEventListener('click',()=>setMode('MATRIX'));
  const holder=$('#corr-chart');if(holder&&typeof ResizeObserver!=='undefined'){resizeObserver=new ResizeObserver(()=>renderCorrelation());resizeObserver.observe(holder);}
  fetchGraphData();refreshTimer=setInterval(fetchGraphData,300000);
}
function setMode(mode){currentMode=mode;$('#btn-corr-network')?.classList.toggle('active',mode==='NETWORK');$('#btn-corr-matrix')?.classList.toggle('active',mode==='MATRIX');renderCorrelation();}
export async function fetchGraphData(){try{const res=await fetch('/api/analytics/correlation-graph',{cache:'no-store'});if(!res.ok)throw new Error(`HTTP ${res.status}`);graphData=await res.json();renderCorrelation();}catch(err){console.warn('Correlation graph fetch error:',err);if(statusEl)statusEl.textContent='○ NETWORK OFFLINE';}}
export function updateCorrelation(p){const matrix=p?.matrix_short||p?.matrix;if(!p||!matrix?.length){payload=null;if(currentMode==='MATRIX'&&emptyEl)emptyEl.style.display='flex';return;}payload=p;if(currentMode==='MATRIX')renderMatrixChart();}
function renderCorrelation(){if(currentMode==='NETWORK')renderForceGraph();else renderMatrixChart();}

function groupColor(group){return{equity:'#2f5d86',volatility:'#914b60',metals:'#a37a25',energy:'#7b623f',fx:'#4f7b68',crypto:'#6e5e9d',other:'#59616a'}[group]||'#59616a';}
function lineColor(rho,alpha){return rho>=0?`rgba(37,130,86,${alpha})`:`rgba(196,66,72,${alpha})`;}

function settleLayout(nodes,links,width,height){
  const pad=62,byId=new Map(nodes.map(n=>[n.id,n]));
  nodes.forEach(n=>{const p=positions.get(n.id);if(p){n.x=p.x;n.y=p.y;}else{n.x=pad+Number(n.x_norm??.5)*(width-pad*2);n.y=pad+Number(n.y_norm??.5)*(height-pad*2);}});
  for(let iter=0;iter<85;iter++){
    const force=new Map(nodes.map(n=>[n.id,{x:0,y:0}]));
    for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){const a=nodes[i],b=nodes[j];let dx=b.x-a.x,dy=b.y-a.y,dist=Math.max(25,Math.hypot(dx,dy));const rep=1350/(dist*dist);dx/=dist;dy/=dist;force.get(a.id).x-=dx*rep;force.get(a.id).y-=dy*rep;force.get(b.id).x+=dx*rep;force.get(b.id).y+=dy*rep;}
    links.forEach(l=>{const a=byId.get(l.source),b=byId.get(l.target);if(!a||!b)return;let dx=b.x-a.x,dy=b.y-a.y,dist=Math.max(1,Math.hypot(dx,dy));dx/=dist;dy/=dist;const target=215-Math.abs(Number(l.correlation||0))*125;const spring=(dist-target)*.013*(.35+Math.abs(Number(l.correlation||0)));force.get(a.id).x+=dx*spring;force.get(a.id).y+=dy*spring;force.get(b.id).x-=dx*spring;force.get(b.id).y-=dy*spring;});
    nodes.forEach(n=>{if(n.id===draggedNodeId)return;const f=force.get(n.id);n.x=Math.max(pad,Math.min(width-pad,n.x+f.x));n.y=Math.max(pad,Math.min(height-pad,n.y+f.y));});
  }
  nodes.forEach(n=>positions.set(n.id,{x:n.x,y:n.y}));
}

function renderForceGraph(){
  const holder=$('#corr-chart');if(!holder)return;if(rafId){cancelAnimationFrame(rafId);rafId=null;}if(chart){chart.dispose();chart=null;}
  if(!graphData?.available||!graphData.nodes?.length){if(emptyEl){emptyEl.style.display='flex';emptyEl.textContent=`○ ${graphData?.reason||'НЕТ РЕАЛЬНОЙ CROSS-ASSET МАТРИЦЫ'}`;}if(statusEl)statusEl.textContent='○ NO REAL NETWORK DATA';holder.querySelector('canvas')?.remove();return;}
  if(emptyEl)emptyEl.style.display='none';let cv=holder.querySelector('canvas');if(!cv){cv=document.createElement('canvas');cv.style.cssText='width:100%;height:100%;display:block;cursor:grab;touch-action:none';holder.appendChild(cv);}
  const rect=holder.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2),width=Math.max(520,Math.floor(rect.width||900)),height=Math.max(330,Math.floor(rect.height||380));cv.width=width*dpr;cv.height=height*dpr;cv.style.width=`${width}px`;cv.style.height=`${height}px`;const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);
  const nodes=graphData.nodes.map(n=>({...n})),links=graphData.links||[];settleLayout(nodes,links,width,height);const byId=new Map(nodes.map(n=>[n.id,n]));
  const dynamics=links.filter(l=>Number(l.velocity_magnitude||0)>.025||l.status==='BREAK_ALERT');

  function draw(now=0){
    ctx.clearRect(0,0,width,height);
    // Quiet topology first.
    links.forEach((l,li)=>{const a=byId.get(l.source),b=byId.get(l.target);if(!a||!b)return;const rho=Number(l.correlation||0),alert=l.status==='BREAK_ALERT',tension=Number(l.tension||0);const alpha=alert?.82:.10+Math.abs(rho)*.43;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.strokeStyle=alert?'rgba(198,55,60,.78)':lineColor(rho,alpha);ctx.lineWidth=(alert?2.5:.7+Math.abs(rho)*2.7)+Math.min(1.2,tension);ctx.setLineDash(alert?[7,5]:[]);ctx.stroke();ctx.setLineDash([]);
      if(Math.abs(rho)>=.58||alert){const mx=(a.x+b.x)/2,my=(a.y+b.y)/2,txt=`${rho>=0?'+':''}${rho.toFixed(2)}`;ctx.font='9px IBM Plex Mono,monospace';const tw=ctx.measureText(txt).width+8;ctx.fillStyle='rgba(255,255,255,.87)';ctx.fillRect(mx-tw/2,my-8,tw,15);ctx.fillStyle=alert?'#b52c31':'#5d5a53';ctx.textAlign='center';ctx.fillText(txt,mx,my+3);}
    });
    // Data-driven activity packets. Two mirrored packets deliberately avoid implying causal direction.
    dynamics.forEach((l,li)=>{const a=byId.get(l.source),b=byId.get(l.target);if(!a||!b)return;const vel=Math.max(.02,Number(l.velocity_magnitude||0)),phase=((now/1000)*(0.08+vel*.9)+li*.173)%1;const tension=Math.min(1,Number(l.tension||0));for(const t of [phase,1-phase]){const x=a.x+(b.x-a.x)*t,y=a.y+(b.y-a.y)*t,r=2.2+5*tension;const g=ctx.createRadialGradient(x,y,0,x,y,r*2.5);g.addColorStop(0,l.status==='BREAK_ALERT'?'rgba(225,66,58,.95)':'rgba(33,156,164,.9)');g.addColorStop(1,'rgba(255,255,255,0)');ctx.fillStyle=g;ctx.beginPath();ctx.arc(x,y,r*2.5,0,Math.PI*2);ctx.fill();}}
    );
    nodes.forEach(n=>{const stress=Math.min(1,Number(n.stress_normalized||0)),coupling=Math.min(1,Number(n.coupling||0)),r=15+9*coupling;
      if(stress>.03){const halo=ctx.createRadialGradient(n.x,n.y,r*.7,n.x,n.y,r+18+stress*16);halo.addColorStop(0,`rgba(198,55,60,${.08+.18*stress})`);halo.addColorStop(1,'rgba(198,55,60,0)');ctx.fillStyle=halo;ctx.beginPath();ctx.arc(n.x,n.y,r+18+stress*16,0,Math.PI*2);ctx.fill();}
      ctx.beginPath();ctx.arc(n.x,n.y,r,0,Math.PI*2);ctx.fillStyle=groupColor(n.group);ctx.fill();ctx.strokeStyle=n.break_count?'#c6373c':'#fff';ctx.lineWidth=n.break_count?3:2;ctx.stroke();
      // Stress ring is a quantitative gauge, not decoration.
      ctx.beginPath();ctx.arc(n.x,n.y,r+5,-Math.PI/2,-Math.PI/2+Math.PI*2*stress);ctx.strokeStyle=stress>.65?'#c6373c':stress>.35?'#d79031':'#33a8a5';ctx.lineWidth=3;ctx.stroke();
      ctx.fillStyle='#fff';ctx.font='bold 9px IBM Plex Mono,monospace';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(n.id,n.x,n.y);if(stress>.35){ctx.fillStyle='#6c6860';ctx.font='8px IBM Plex Mono,monospace';ctx.fillText(`σ ${Number(n.stress_pressure||0).toFixed(2)}`,n.x,n.y+r+16);}
    });
    // Bottom topology HUD.
    const s=graphData.summary||{};ctx.fillStyle='rgba(248,247,243,.9)';ctx.fillRect(8,height-30,width-16,23);ctx.font='9px IBM Plex Mono,monospace';ctx.fillStyle='#55524c';ctx.textAlign='left';ctx.fillText(`SYSTEM COUPLING ${Number(s.systemic_coupling||0).toFixed(2)}   TENSION ${Number(s.network_tension||0).toFixed(2)}   FRAGMENT ${Number(s.fragmentation||0).toFixed(2)}   STRESS NODE ${s.dominant_stress_node||'—'}`,18,height-15);
    if(dynamics.length)rafId=requestAnimationFrame(draw);else rafId=null;
  }
  draw(performance.now());

  function pointerPos(e){const r=cv.getBoundingClientRect();return{x:(e.clientX-r.left)*width/r.width,y:(e.clientY-r.top)*height/r.height};}
  cv.onpointerdown=e=>{const p=pointerPos(e),hit=nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<=30);if(!hit)return;draggedNodeId=hit.id;cv.setPointerCapture?.(e.pointerId);cv.style.cursor='grabbing';};
  cv.onpointermove=e=>{if(!draggedNodeId)return;const p=pointerPos(e),n=byId.get(draggedNodeId);if(!n)return;n.x=Math.max(30,Math.min(width-30,p.x));n.y=Math.max(30,Math.min(height-30,p.y));positions.set(n.id,{x:n.x,y:n.y});if(!dynamics.length)draw(performance.now());};
  const release=e=>{if(!draggedNodeId)return;draggedNodeId=null;cv.releasePointerCapture?.(e.pointerId);cv.style.cursor='grab';};cv.onpointerup=release;cv.onpointercancel=release;

  const summary=graphData.summary||{};if(statusEl)statusEl.textContent=summary.active_breaks_count?`⚠ ${summary.active_breaks_count} BREAK · TENSION ${Number(summary.network_tension||0).toFixed(2)}`:`● ${summary.observed_pairs||links.length} PAIRS · COUPLING ${Number(summary.systemic_coupling||0).toFixed(2)}${summary.velocity_ready?'':' · ΔV BUILDING'}`;
  const interpret=$('#corr-interpretation');if(interpret){const top=(graphData.break_alerts||[])[0];interpret.innerHTML=top?`<b>NETWORK TENSION:</b> ${top.source}↔${top.target} · ρ ${Number(top.correlation).toFixed(2)} · Δbaseline ${top.delta_baseline==null?'—':Number(top.delta_baseline).toFixed(2)} · Δ15m ${top.delta_15m==null?'—':Number(top.delta_15m).toFixed(2)}. Светящиеся пакеты показывают скорость изменения связи в обе стороны и <b>не означают причинность</b>.`:`<b>TOPOLOGY:</b> размер узла = средняя сила связей, внешнее кольцо = incident stress, толщина ребра = |ρ|. Динамика включается только когда реально меняется correlation relationship.`;interpret.style.display='block';}
}

function renderMatrixChart(){
  if(rafId){cancelAnimationFrame(rafId);rafId=null;}const holder=$('#corr-chart');if(!holder||!payload||!window.echarts)return;if(emptyEl)emptyEl.style.display='none';holder.querySelector('canvas')?.remove();if(chart)chart.dispose();chart=window.echarts.init(holder);
  const matrix=payload.matrix_short||payload.matrix,assets=payload.assets||payload.pairs||[],delta=payload.matrix_delta||[],points=[];for(let i=0;i<matrix.length;i++)for(let j=0;j<matrix[i].length;j++){const v=Number(matrix[i][j]);if(Number.isFinite(v))points.push([j,i,v,Number(delta?.[i]?.[j])]);}
  chart.setOption({animation:false,tooltip:{formatter:p=>{const[j,i,rho,d]=p.data;return`<b>${assets[i]} ↔ ${assets[j]}</b><br>rolling ρ: ${rho.toFixed(2)}<br>Δ vs baseline: ${Number.isFinite(d)?d.toFixed(2):'—'}`;}},grid:{left:70,right:30,top:20,bottom:45},xAxis:{type:'category',data:assets,axisLabel:{rotate:-25,fontSize:10}},yAxis:{type:'category',data:assets,inverse:true,axisLabel:{fontSize:10}},visualMap:{min:-1,max:1,show:false,inRange:{color:['#bd4549','#f4f2ec','#2b7a52']}},series:[{type:'heatmap',data:points,label:{show:true,formatter:p=>Number(p.data[2]).toFixed(2),fontSize:9},itemStyle:{borderColor:'#fff',borderWidth:1}}]});
}
