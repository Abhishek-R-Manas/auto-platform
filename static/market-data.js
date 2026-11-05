(() => {
  'use strict';
  let offset=0,total=0,busy=false,timer,searchTimer;
  const localTime=value=>value?new Date(value).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'}):'—';
  async function refreshMarket(){
    if(busy || !['marketdata','settings'].includes(activeView))return;
    busy=true;
    try {
      const [scan,captures]=await Promise.all([api(`/api/market/universe?q=${encodeURIComponent($('breadth-search').value)}&offset=${offset}&limit=50`),api('/api/market/captures')]);
      const status=scan.status;total=scan.total;
      $('breadth-phase').textContent=status.phase;
      $('breadth-health').textContent=`${status.universe_count} NSE instruments in master · ${status.received_symbols} received · ${status.fresh_symbols} fresh · ${status.stale_or_missing_symbols} stale or missing · ${status.websocket_symbols} streamed / ${status.snapshot_symbols} snapshot coverage. ${status.error||''} ${status.recording_error||''}`;
      $('breadth-connect').disabled=!status.authenticated || status.enabled;
      $('breadth-disconnect').disabled=!status.enabled || state?.source==='KITE';
      $('capture-start').disabled=!status.authenticated || state?.source!=='KITE' || captures.some(c=>c.status==='RECORDING');
      $('breadth-quotes').innerHTML=!scan.rows.length?empty(status.authenticated?'No matching instruments':'Authenticate your data session','Configure server-side Kite credentials in Settings, authenticate, then connect all NSE equities. Synthetic prices are not inserted into this view.'):`<div class="table-scroll"><table><thead><tr><th>Instrument</th><th>Last</th><th>Bid / ask</th><th>Exchange time · IST</th><th>Status</th><th></th></tr></thead><tbody>${scan.rows.map(q=>`<tr><td><strong>${escapeHTML(q.symbol)}</strong><div class="fill-source">${escapeHTML(q.name)}</div></td><td class="numeric">${q.last?money(q.last):'—'}</td><td class="numeric">${q.bid?money(q.bid):'—'} / ${q.ask?money(q.ask):'—'}</td><td>${localTime(q.exchange_time)}<div class="fill-source">${q.transport==='KITE_REST_SNAPSHOT'?'REST snapshot · ':q.transport==='KITE_WEBSOCKET'?'Stream · ':''}${q.age_seconds!==null?`${number(Math.max(0,q.age_seconds),1)}s old`:'Not received'}</div></td><td><span class="pill ${q.fresh?'green':'neutral'}">${q.fresh?'FRESH':q.last?'STALE':'MISSING'}</span></td><td><button class="action-small" data-price-chart="${escapeHTML(q.key)}">Chart / close</button><button class="action-small" data-add-watch="${escapeHTML(q.key)}">Add to watchlist</button></td></tr>`).join('')}</tbody></table></div>`;
      $('breadth-page').textContent=total?`${offset+1}–${Math.min(offset+50,total)} of ${total}`:'No authenticated universe loaded';
      $('breadth-previous').disabled=offset===0;$('breadth-next').disabled=offset+50>=total;
      $('capture-list').innerHTML=captures.length?captures.map(c=>`<div class="connection-option"><div><strong>${escapeHTML(c.name)}</strong><p>${escapeHTML(c.status)} · ${c.rows} quotes · ${c.gaps} connection gaps · PAPER WATCHLIST</p></div>${c.status==='RECORDING'?`<button class="button secondary" data-stop-capture="${escapeHTML(c.id)}">Stop recording</button>`:`<button class="button secondary" data-import-capture="${escapeHTML(c.id)}" ${!c.rows?'disabled':''}>${c.dataset_id?'View dataset':'Create research dataset'}</button>`}</div>`).join(''):empty('No quote recordings','Start after connecting an authenticated paper watchlist.');
    } catch(error){$('breadth-health').textContent=error.message;}
    finally{busy=false;}
  }
  function addWatch(key){
    const current=$('kite-instruments').value.split(/[,\n]+/).map(v=>v.trim()).filter(Boolean);
    if(!current.includes(key))current.push(key);
    $('kite-instruments').value=current.join(', ');navigate('settings');toast('Added to the watchlist selection. Use Kite quotes to apply while the paper account is flat.');
  }
  $('breadth-search').addEventListener('input',()=>{offset=0;clearTimeout(timer);timer=setTimeout(refreshMarket,250);});
  $('breadth-previous').addEventListener('click',()=>{offset=Math.max(0,offset-50);refreshMarket();});
  $('breadth-next').addEventListener('click',()=>{offset+=50;refreshMarket();});
  $('breadth-connect').addEventListener('click',async()=>{await action('/api/market/connect',{enabled:true},'All NSE equities requested. Coverage will update as quotes arrive.');await refreshMarket();});
  $('breadth-disconnect').addEventListener('click',async()=>{await action('/api/market/connect',{enabled:false},'Market-data feed disconnected.');await refreshMarket();});
  $('capture-start').addEventListener('click',async()=>{await action('/api/market/captures',{name:$('capture-name').value,rights_confirmed:$('capture-rights').checked},'Watchlist quote capture started.');await refreshMarket();});
  document.addEventListener('click',async event=>{
    const add=event.target.closest('[data-add-watch]');if(add){addWatch(add.dataset.addWatch);return;}
    const stop=event.target.closest('[data-stop-capture]');
    if(stop){await action(`/api/market/captures/${encodeURIComponent(stop.dataset.stopCapture)}/stop`,{},'Recording stopped.');await refreshMarket();}
    const button=event.target.closest('[data-import-capture]');
    if(button){button.disabled=true;try{const result=await api(`/api/market/captures/${encodeURIComponent(button.dataset.importCapture)}/dataset`,{});navigate('research');toast(`Dataset ${result.status}. Inspect it in Data catalog.`);}catch(error){toast(error.message,true);}finally{button.disabled=false;await refreshMarket();}}
    if(event.target.closest('[data-view]'))setTimeout(refreshMarket,100);
  });
  $('contract-search').addEventListener('input',()=>{
    clearTimeout(searchTimer);searchTimer=setTimeout(async()=>{
      const q=$('contract-search').value.trim();if(q.length<2){$('contract-results').replaceChildren();return;}
      try{const result=await api(`/api/market/search?q=${encodeURIComponent(q)}`);if(q!==$('contract-search').value.trim())return;
        $('contract-results').innerHTML=result.map(i=>`<div class="connection-option"><div><strong>${escapeHTML(i.key)}</strong><p>${escapeHTML(i.kind)} · lot ${i.lot_size} ${escapeHTML(i.expiry||'')}</p></div><button class="action-small" data-add-watch="${escapeHTML(i.key)}">Add</button></div>`).join('')||'No current contracts found.';
      }catch(error){$('contract-results').textContent=error.message;}
    },350);
  });
  setInterval(refreshMarket,3000);setTimeout(refreshMarket,200);
})();
