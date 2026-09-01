import { fetchStructured } from './safe_fetch.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';
import { attachTerminal3DToolbar } from './plotly_terminal_toolbar.js';

const HOME_FIELD = { eye:{x:1.45,y:1.55,z:1.18},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1} };
const HOME_ROTATION = { eye:{x:1.38,y:1.52,z:1.12},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1} };
const plots = { field:{ready:false,signature:null,guard:null}, rotation:{ready:false,signature:null,guard:null}, correlation:{ready:false,signature:null} };
let inFlight = null;

const byId = (id) => document.getElementById(id);
const finite = (value) => value != null && Number.isFinite(Number(value));
const n = (value) => finite(value) ? Number(value) : null;
const text = (id, value) => { const node=byId(id); if(node) node.textContent=value; };
const visible = (id, show) => { const node=byId(id); if(node) node.style.display=show?'flex':'none'; };
const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function fmtMoney(value) {
  if(!finite(value)) return 'N/A';
  const x=Number(value), abs=Math.abs(x);
  if(abs>=1e12) return `$${(x/1e12).toFixed(2)}T`;
  if(abs>=1e9) return `$${(x/1e9).toFixed(2)}B`;
  if(abs>=1e6) return `$${(x/1e6).toFixed(2)}M`;
  if(abs>=1e3) return `$${(x/1e3).toFixed(2)}K`;
  if(abs>=1) return `$${x.toLocaleString('en-US',{maximumFractionDigits:2})}`;
  return `$${x.toLocaleString('en-US',{maximumSignificantDigits:6})}`;
}
function fmtPct(value, digits=2) { return finite(value)?`${Number(value)>=0?'+':''}${Number(value).toFixed(digits)}%`:'N/A'; }
function fmtAge(value) {
  if(!finite(value)) return 'N/A';
  const sec=Math.max(0,Number(value));
  if(sec<90) return `${Math.round(sec)}s`;
  if(sec<7200) return `${Math.round(sec/60)}m`;
  return `${(sec/3600).toFixed(1)}h`;
}
function signClass(value) { return !finite(value)?'na':Number(value)>0?'positive':Number(value)<0?'negative':''; }
function setSigned(id,value,formatter=fmtPct) { const node=byId(id); if(!node)return; node.textContent=formatter(value); node.className=signClass(value); }
function sourceLabel(source) {
  if(!source) return 'N/A';
  const status=String(source.status||'no_data').toUpperCase();
  const age=source.observed_at?Math.max(0,Date.now()/1000-Number(source.observed_at)):null;
  return `${status} · ${age==null?'AGE N/A':fmtAge(age)}${source.error?` · ${source.error}`:''}`;
}

const baseLayout = {
  paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
  margin:{l:12,r:12,t:18,b:12},font:{family:'IBM Plex Mono, monospace',color:'#8ea1ad',size:9},
  showlegend:false,uirevision:'crypto-global-observed-v1',
};
const axis3d = (title) => ({title:{text:title,font:{size:9}},gridcolor:'rgba(165,195,210,.12)',zerolinecolor:'rgba(165,195,210,.2)',showbackground:true,backgroundcolor:'rgba(7,12,17,.48)',tickfont:{size:8}});

async function renderField(payload) {
  const chart=byId('crypto-field');
  const assets=(payload.assets||[]).filter((asset)=>finite(asset.btc_correlation_7d)&&finite(asset.change_7d_pct)&&finite(asset.realized_vol_24h_annual_pct));
  visible('field-empty',!assets.length);
  if(!assets.length) return;
  const caps=assets.map((asset)=>Math.max(1,n(asset.market_cap_usd)||1));
  const logCaps=caps.map((cap)=>Math.log10(cap)); const min=Math.min(...logCaps), max=Math.max(...logCaps);
  const sizes=logCaps.map((value)=>14+24*(value-min)/Math.max(.1,max-min));
  const traces=[{
    type:'scatter3d',mode:'markers+text',x:assets.map((a)=>a.btc_correlation_7d),y:assets.map((a)=>a.change_7d_pct),z:assets.map((a)=>a.realized_vol_24h_annual_pct),
    text:assets.map((a)=>a.symbol),textposition:'top center',textfont:{size:10,color:'#d7e7ee'},
    marker:{size:sizes,color:assets.map((a)=>a.change_24h_pct),colorscale:[[0,'#ff6371'],[.5,'#758694'],[1,'#50d99a']],cmin:-8,cmax:8,opacity:.88,line:{width:1,color:'rgba(220,240,248,.46)'},colorbar:{title:'24H %',thickness:8,len:.55,tickfont:{size:8}}},
    customdata:assets.map((a)=>[a.name,a.price_usd,a.market_cap_usd,a.change_24h_pct,a.history_observations,a.correlation_observations]),
    hovertemplate:'<b>%{text} · %{customdata[0]}</b><br>Price %{customdata[1]:$,.6g}<br>Cap %{customdata[2]:$,.3s}<br>24h %{customdata[3]:+.2f}%<br>Corr BTC %{x:.3f}<br>7d %{y:+.2f}%<br>RV %{z:.1f}%<br>Hourly obs %{customdata[4]} · pair %{customdata[5]}<extra></extra>',
  }];
  const layout={...baseLayout,scene:{xaxis:{...axis3d('CORR WITH BTC · 7D'),range:[-1,1]},yaxis:axis3d('RETURN · 7D %'),zaxis:axis3d('RV 24H ANN. %'),aspectmode:'cube',camera:HOME_FIELD,dragmode:'orbit'}};
  const signature=JSON.stringify(assets.map((a)=>[a.symbol,a.asof,a.price_usd,a.change_7d_pct,a.realized_vol_24h_annual_pct,a.btc_correlation_7d]));
  if(signature===plots.field.signature) return;
  if(!plots.field.guard) plots.field.guard=createPlotlyCameraGuard(chart,HOME_FIELD);
  if(!plots.field.ready){ await Plotly.newPlot(chart,traces,layout,{displayModeBar:false,scrollZoom:true,responsive:false}); plots.field.ready=true; }
  else await Plotly.react(chart,traces,layout,{displayModeBar:false,scrollZoom:true,responsive:false});
  plots.field.signature=signature;
  attachTerminal3DToolbar({plot:chart,container:chart.parentElement,guard:plots.field.guard,homeCamera:HOME_FIELD,key:'crypto-field'});
}

async function renderRotation(payload) {
  const chart=byId('rotation-path'); const path=(payload.leadership_path||[]).filter((p)=>finite(p.btc_24h_pct)&&finite(p.alt_median_24h_pct)&&finite(p.breadth_positive));
  visible('rotation-empty',path.length<2); if(path.length<2)return;
  const trace={type:'scatter3d',mode:'lines+markers',x:path.map((p)=>p.btc_24h_pct),y:path.map((p)=>p.alt_median_24h_pct),z:path.map((p)=>100*p.breadth_positive),
    line:{width:7,color:path.map((_,index)=>index),colorscale:[[0,'#667a88'],[1,'#64d8e5']]},marker:{size:path.map((_,index)=>index===path.length-1?7:3),color:path.map((p)=>100*p.breadth_positive),colorscale:[[0,'#ff6975'],[.5,'#f1b75d'],[1,'#58d795']],cmin:0,cmax:100},
    customdata:path.map((p)=>[new Date(p.ts*1000).toISOString().slice(5,16).replace('T',' '),p.observed_alts]),hovertemplate:'<b>%{customdata[0]} UTC</b><br>BTC 24h %{x:+.2f}%<br>Alt median %{y:+.2f}%<br>Positive breadth %{z:.0f}%<br>Observed alts %{customdata[1]}<extra></extra>'};
  const layout={...baseLayout,scene:{xaxis:axis3d('BTC 24H %'),yaxis:axis3d('ALT MEDIAN 24H %'),zaxis:{...axis3d('POSITIVE BREADTH %'),range:[0,100]},aspectmode:'cube',camera:HOME_ROTATION,dragmode:'orbit'}};
  const signature=JSON.stringify(path.map((p)=>[p.ts,p.btc_24h_pct,p.alt_median_24h_pct,p.breadth_positive]));
  if(signature===plots.rotation.signature)return;
  if(!plots.rotation.guard) plots.rotation.guard=createPlotlyCameraGuard(chart,HOME_ROTATION);
  if(!plots.rotation.ready){await Plotly.newPlot(chart,[trace],layout,{displayModeBar:false,scrollZoom:true,responsive:false});plots.rotation.ready=true;}
  else await Plotly.react(chart,[trace],layout,{displayModeBar:false,scrollZoom:true,responsive:false});
  plots.rotation.signature=signature;
  attachTerminal3DToolbar({plot:chart,container:chart.parentElement,guard:plots.rotation.guard,homeCamera:HOME_ROTATION,key:'crypto-rotation'});
}

async function renderCorrelation(payload) {
  const chart=byId('crypto-correlation'), corr=payload.correlation||{}, symbols=corr.symbols||[], matrix=corr.matrix||[];
  let observed=0,possible=symbols.length*(symbols.length-1)/2;
  for(let i=0;i<symbols.length;i++)for(let j=i+1;j<symbols.length;j++)if(finite(matrix?.[i]?.[j]))observed++;
  text('corr-coverage',`${observed} / ${possible} PAIRS`); visible('correlation-empty',!observed); if(!observed)return;
  const trace={type:'heatmap',x:symbols,y:symbols,z:matrix,zmin:-1,zmax:1,zmid:0,colorscale:[[0,'#ff6573'],[.5,'#111a22'],[1,'#58d795']],xgap:1,ygap:1,colorbar:{title:'ρ',thickness:8,len:.66,tickfont:{size:8}},hovertemplate:'%{y} × %{x}<br>ρ %{z:.3f}<extra></extra>'};
  const layout={...baseLayout,margin:{l:55,r:12,t:18,b:42},xaxis:{side:'bottom',tickfont:{size:8},gridcolor:'rgba(255,255,255,.05)'},yaxis:{autorange:'reversed',tickfont:{size:8},gridcolor:'rgba(255,255,255,.05)'}};
  const signature=JSON.stringify(matrix);
  if(signature===plots.correlation.signature)return;
  if(!plots.correlation.ready){await Plotly.newPlot(chart,[trace],layout,{displayModeBar:false,responsive:true});plots.correlation.ready=true;}
  else await Plotly.react(chart,[trace],layout,{displayModeBar:false,responsive:true});
  plots.correlation.signature=signature;
}

function renderSnapshot(payload) {
  const global=payload.global||{}, summary=payload.summary||{}, sources=payload.sources||{}, transport=payload.transport||{};
  const state=String(payload.status||'no_data'); const status=byId('crypto-status');
  status.className=`status-pill ${state==='observed'?'live':state==='partial'||state==='stale'?'partial':'no-data'}`;
  status.textContent=state==='observed'?'● OBSERVED':state==='partial'?'◐ PARTIAL DATA':state==='stale'?'◐ STALE SNAPSHOT':state==='warming'?'◌ WARMING':'○ NO DATA';
  text('global-cap',fmtMoney(global.total_market_cap_usd)); text('global-volume',fmtMoney(global.total_volume_24h_usd));
  text('btc-dominance',finite(global.btc_dominance_pct)?`${Number(global.btc_dominance_pct).toFixed(2)}%`:'N/A');
  text('eth-dominance',finite(global.eth_dominance_pct)?`${Number(global.eth_dominance_pct).toFixed(2)}%`:'N/A');
  setSigned('global-change',global.market_cap_change_24h_pct); text('market-breadth',finite(summary.breadth_positive_24h)?`${Math.round(100*summary.breadth_positive_24h)}% · ${summary.observed_change_assets} OBS`:'N/A');
  text('field-stamp',payload.built_at?`${new Date(payload.built_at*1000).toISOString().slice(0,16).replace('T',' ')} UTC`:'N/A');
  const observations=summary.observations_ru||[]; byId('crypto-observations').innerHTML=observations.map((item)=>`<p>${escapeHtml(item)}</p>`).join('')||'<p>N/A</p>';
  const assets=payload.assets||[]; const available=assets.filter((asset)=>asset.available).length; text('asset-coverage',`${available} / ${assets.length}`);
  byId('asset-table').innerHTML=assets.map((asset)=>`<tr><td>${escapeHtml(asset.symbol)} <span class="na">${escapeHtml(asset.name)}</span></td><td>${fmtMoney(asset.price_usd)}</td><td>${fmtMoney(asset.market_cap_usd)}</td><td class="${signClass(asset.change_1h_pct)}">${fmtPct(asset.change_1h_pct)}</td><td class="${signClass(asset.change_24h_pct)}">${fmtPct(asset.change_24h_pct)}</td><td class="${signClass(asset.change_7d_pct)}">${fmtPct(asset.change_7d_pct)}</td><td>${finite(asset.realized_vol_24h_annual_pct)?`${Number(asset.realized_vol_24h_annual_pct).toFixed(1)}%`:'N/A'}</td><td>${finite(asset.btc_correlation_7d)?Number(asset.btc_correlation_7d).toFixed(3):'N/A'}</td><td>${fmtAge(asset.age_sec)}</td></tr>`).join('');
  text('source-coingecko',`CoinGecko: ${sourceLabel(sources.coingecko)}`); text('source-yahoo',`Yahoo: ${sourceLabel(sources.yahoo)}`);
  text('transport-state',`${transport.cache_state||'CACHE N/A'} · PAYLOAD ${fmtAge(transport.payload_age_sec)}${transport.last_refresh_error?` · ${transport.last_refresh_error}`:''}`);
}

async function refresh() {
  if(inFlight)return inFlight;
  inFlight=(async()=>{try{const payload=await fetchStructured('/api/crypto/global');renderSnapshot(payload);await Promise.all([renderField(payload),renderRotation(payload),renderCorrelation(payload)]);if(payload.status==='warming')setTimeout(refresh,2500);}catch(error){const status=byId('crypto-status');status.className='status-pill no-data';status.textContent=`○ API ${error.status||'ERROR'}`;text('transport-state',String(error.message||error));}})();
  try{await inFlight;}finally{inFlight=null;}
}

refresh();
setInterval(refresh,60000);
