(() => {
 'use strict';
 let loading=false;
 const stamp=value=>new Date(value).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'});
 function render(result){
  const data=result.data;
  $('price-refresh').disabled=!result.authenticated;
  if(!data){$('price-source').textContent='NO SAVED DATA';$('price-close').textContent='—';$('price-status').textContent=result.message;$('price-chart').replaceChildren();$('price-detail').textContent='Sign in to fetch genuine history. No sample prices are substituted.';return;}
  $('price-source').textContent='SAVED KITE HISTORY';$('price-close').textContent=money(data.latest_completed_close);
  $('price-status').textContent=`${data.symbol} · latest completed candle ${stamp(data.close_time)} IST · fetched ${stamp(data.fetched_at)} IST${result.authenticated?'':' · session signed out'}`;
  const rows=data.candles,values=rows.map(r=>r.close),lo=Math.min(...values),hi=Math.max(...values),span=Math.max(hi-lo,lo*.001),low=lo-span*.08,high=hi+span*.08;
  const w=920,h=260,left=75,right=15,top=12,bottom=30,x=i=>left+i/Math.max(1,rows.length-1)*(w-left-right),y=v=>top+(high-v)/(high-low)*(h-top-bottom);
  let grid='';for(let i=0;i<4;i++){const v=low+(high-low)*i/3,yy=y(v);grid+=`<line x1="${left}" y1="${yy}" x2="${w-right}" y2="${yy}" stroke="#dce5df"/><text x="2" y="${yy+4}" fill="#52685a" font-size="12">${number(v)}</text>`;}
  const path=values.map((v,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  $('price-chart').innerHTML=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${escapeHTML(data.symbol)} historical close prices">${grid}<path d="${path}" fill="none" stroke="#13795b" stroke-width="2"/><circle cx="${x(rows.length-1)}" cy="${y(values.at(-1))}" r="3" fill="#13795b"/><text x="${left}" y="255" font-size="12">${escapeHTML(rows[0].time.slice(0,10))}</text><text x="${w-100}" y="255" font-size="12">${escapeHTML(rows.at(-1).time.slice(0,10))}</text></svg>`;
  $('price-detail').textContent=`${rows.length} completed ${data.interval} candles · last O ${money(rows.at(-1).open)} / H ${money(rows.at(-1).high)} / L ${money(rows.at(-1).low)} / C ${money(rows.at(-1).close)} · volume ${number(rows.at(-1).volume,0)}. Historical display prices cannot fill orders.`;
 }
 async function load(refresh=false){
  if(loading)return;loading=true;
  const symbol=$('price-symbol').value.trim().toUpperCase(),interval=$('price-interval').value;
  $('price-status').textContent=refresh?'Fetching genuine broker candles…':'Loading saved chart…';
  try{const result=refresh?await api('/api/market/charts/refresh',{symbol,interval,days:interval==='day'?365:10}):await api(`/api/market/charts?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`);render(result);}
  catch(error){$('price-status').textContent=error.message;toast(error.message,true);}
  finally{loading=false;}
 }
 $('price-form').addEventListener('submit',e=>{e.preventDefault();load(true);});$('price-load').addEventListener('click',()=>load());
 $('price-interval').addEventListener('change',()=>load());
 document.addEventListener('click',e=>{const chart=e.target.closest('[data-price-chart]');if(chart){$('price-symbol').value=chart.dataset.priceChart;load();}if(e.target.closest('[data-view="marketdata"]'))load();});
 window.addEventListener('hashchange',()=>{if(activeView==='marketdata')load();});
 setTimeout(()=>load(),200);
})();
