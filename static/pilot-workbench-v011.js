(() => {
'use strict';
let preview=null,busy=false,actionBusy=false,sending=false,latest=null;
const esc=escapeHTML,terminal=['COMPLETE','CANCELLED','REJECTED'];
const knownPending=['UNCERTAIN','CANCEL_PENDING','CANCEL_SENDING','SENDING'];
function orderTable(s){return s.orders.length?`<div class="table-scroll"><table><thead><tr><th>Instrument</th><th>Side / quantity</th><th>Type / limit</th><th>Status</th><th>Filled / average</th><th></th></tr></thead><tbody>${s.orders.map(o=>`<tr><td>${esc(o.order.symbol)}<small>${esc(o.broker_order_id||o.tag)}</small></td><td>${esc(o.order.side)} ${o.order.quantity}${o.origin==='PROTECTIVE'?'<small>Protective exit</small>':''}</td><td>${esc(o.order.order_type||'LIMIT')} · ${money(o.order.limit_price)}${o.order.order_type==='SL'?'<small>Trigger '+money(o.order.stop_loss)+'</small>':''}</td><td class="live-order-status">${esc(o.status)}</td><td>${o.filled_quantity} / ${o.order.quantity}<small>${o.filled_quantity?money(o.average_price):'Awaiting broker fills'}</small></td><td>${o.broker_order_id&&!terminal.includes(o.status)&&!knownPending.includes(o.status)?`<button class="text-button" data-pilot-cancel="${esc(o.id)}" data-protective="${o.origin==='PROTECTIVE'}">${o.origin==='PROTECTIVE'?'Cancel stop & pause':'Request cancellation'}</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:empty('No real broker orders','Orders placed here appear with broker acknowledgements, actual fills and uncertain outcomes.');}
window.arthaRenderBrokerOrders=s=>{if(!s)return;$('broker-orders').innerHTML=`<div class="surface-heading"><div><h2>Real broker orders</h2><p class="muted">Artha orders only · ${s.reconciliation.observed_at?'Last reconciled '+esc(new Date(s.reconciliation.observed_at).toLocaleString('en-IN')):'Sync your account to read current broker status'}</p></div><button class="button secondary" data-live-sync ${!s.authenticated?'disabled':''}>Sync status</button></div>${orderTable(s)}<p class="card-note">Acknowledgement is not a fill. Pending cancellations still reserve quantity. Use Kite to inspect orders placed outside Artha.</p>`;};
function render(s){
 latest=s;const r=s.readiness,m=s.mandate;
 $('pilot-mode').textContent=s.armed?'ACTIVE · REAL MONEY':s.transport_enabled?'READY FOR SETUP':'ORDER CONNECTION OFF';
 $('pilot-status').textContent=`${s.authenticated?'Signed in to Zerodha':'Sign in to Zerodha'} · ${m?'Account '+m.account_id:'No trading limits saved'}${s.reconciliation.error?' · '+s.reconciliation.error:''}`;
 $('pilot-protection').textContent=`${s.protection.unprotected_units} units without acknowledged native stop coverage${s.protection.error?' · '+s.protection.error:''}${s.protection.suspended.length?' · Protection suspended: '+s.protection.suspended.join(', '):''}`;
 $('pilot-gates').innerHTML=r.gates.map((g,i)=>`<li class="${g.ok?'live-ready':''}"><span class="live-step-mark">${g.ok?'✓':i+1}</span><span>${esc(g.label)}</span></li>`).join('');
 $('pilot-calendar').textContent=`${r.scope}. Calendar reviewed ${r.calendar.reviewed_at}, valid through ${r.calendar.valid_until}. ${r.calendar.note}${r.authority_error?' '+r.authority_error:''}`;
 $('pilot-limits-summary').innerHTML=m?[['Account',esc(m.account_id)],['Authority',m.mode==='MANUAL_NSE_CNC'?'Manual delivery':'Qualified strategy'],['Cash available',r.available_cash_paise===null?'Sync required':money(r.available_cash_paise/100)],['Allocated capital',money(m.capital)],['Order limit',money(m.max_order)],['Daily loss halt',money(m.daily_loss)],['Per-trade risk',money(m.max_trade_loss)],['Expires',esc(new Date(m.expires_at).toLocaleString('en-IN'))]].map(([k,v])=>`<div><small>${k}</small><strong>${v}</strong></div>`).join(''):'';
 $('pilot-reconcile').disabled=!s.authenticated||actionBusy;$('pilot-preview-button').disabled=!s.authenticated||!m||(!s.armed&&$('pilot-side').value!=='SELL')||!s.transport_enabled||actionBusy;
 $('pilot-arm').querySelector('button').disabled=!s.authenticated||!m||!s.transport_enabled||s.armed||actionBusy;
 $('pilot-save-limits').disabled=!s.authenticated||!!s.orders.length||actionBusy;
 if(s.reconciliation.account_id&&!$('pilot-account').value)$('pilot-account').value=s.reconciliation.account_id;
 $('pilot-orders').innerHTML=orderTable(s);
 $('live-ticket-status').textContent=s.armed?`REAL MONEY · ${m?.account_id} · ${r.scope}. Review the price and quantity before sending.`:`Trading is inactive. ${s.transport_enabled?'':'Broker order connection is disabled. '}${r.gates.filter(g=>!g.ok).map(g=>g.label).join(' · ')}. Open Trading setup below.`;
 window.dispatchEvent(new CustomEvent('artha:pilot-status',{detail:s}));
}
async function refreshPilot(force=false){
 if(busy||!force&&activeView!=='pilot'&&activeView!=='orders'&&$('live-ticket-panel').classList.contains('hidden'))return;
 busy=true;try{render(await api('/api/pilot/status'));}catch(e){$('pilot-status').textContent=e.message;}finally{busy=false;}
}
async function run(task){if(actionBusy)return;actionBusy=true;try{return await task();}catch(e){toast(e.message,true);$('pilot-setup-message').textContent=e.message;return null;}finally{actionBusy=false;await refreshPilot(true);}}
function clearPreview(){preview=null;$('pilot-confirm').classList.add('hidden');$('pilot-confirm-text').value='';}
function authorityMode(){const manual=$('pilot-authority').value==='MANUAL';$('pilot-forward-field').classList.toggle('hidden',manual);$('pilot-portfolio').required=!manual;$('pilot-manual-confirm-field').classList.toggle('hidden',!manual);$('pilot-manual-confirm').required=manual;}
$('pilot-authority').addEventListener('change',authorityMode);authorityMode();
$('pilot-reconcile').addEventListener('click',()=>run(async()=>{await api('/api/pilot/reconcile',{});$('pilot-setup-message').textContent='Broker account synchronized. Verify the account ID and set your limits.';}));
$('pilot-restore-protection').addEventListener('click',()=>run(async()=>{await api('/api/pilot/reconcile',{});await api('/api/pilot/protection/restore',{});}));
$('pilot-pause').addEventListener('click',()=>run(async()=>{clearPreview();await api('/api/pilot/pause',{});$('pilot-setup-message').textContent='Entries paused. Inspect pending cancellations and existing positions.';}));
$('pilot-mandate').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
 const body={account_id:$('pilot-account').value.trim().toUpperCase(),symbols:$('pilot-symbols').value.split(',').map(v=>v.trim().toUpperCase()).filter(Boolean),capital:Number($('pilot-capital').value),daily_loss:Number($('pilot-loss').value),max_trade_loss:Number($('pilot-trade-loss').value),max_order:Number($('pilot-order-cap').value),max_orders_per_day:Number($('pilot-order-count').value),expires_at:new Date($('pilot-expiry').value).toISOString(),reviewer:$('pilot-reviewer').value.trim(),static_ip_confirmed:$('pilot-static-ip').checked,delivery_authorized:$('pilot-delivery').checked,protective_orders_authorized:$('pilot-native-stops').checked};
 const manual=$('pilot-authority').value==='MANUAL';if(manual)body.confirmation=$('pilot-manual-confirm').value;else body.portfolio_id=$('pilot-portfolio').value.trim();
 await api('/api/pilot/reconcile',{});await api('/api/pilot/'+(manual?'manual/mandate':'mandate'),body);clearPreview();$('pilot-setup-message').textContent='Trading limits saved. Verify the checklist, then activate this session with the exact account ID.';
 });});
$('pilot-arm').addEventListener('submit',e=>{e.preventDefault();run(async()=>{await api('/api/pilot/reconcile',{});await api('/api/pilot/arm',{confirmation:$('pilot-arm-text').value});$('pilot-arm-text').value='';$('pilot-setup-message').textContent='Real-money session active. Open Trade and choose Live broker account to review an order.';});});
$('pilot-trade').addEventListener('input',()=>{clearPreview();if(latest)render(latest);});
$('pilot-trade').addEventListener('submit',e=>{e.preventDefault();if(sending)return;run(async()=>{
 clearPreview();await api('/api/pilot/reconcile',{});
 const result=await api('/api/pilot/preview',{request_id:crypto.randomUUID(),symbol:$('pilot-symbol').value.trim().toUpperCase(),side:$('pilot-side').value,quantity:Number($('pilot-quantity').value),limit_price:Number($('pilot-price').value),stop_loss:$('pilot-stop').value?Number($('pilot-stop').value):null,rationale:$('pilot-rationale').value.trim()});
 preview=result;const o=result.order,v=result.evidence;
 $('pilot-preview').innerHTML=`<h3>Review real order</h3><dl>${[['Account',esc(latest?.mandate?.account_id||'')],['Instrument','NSE · '+esc(o.symbol)],['Side / quantity',esc(o.side)+' '+o.quantity],['Limit price',money(o.limit_price)],['Order value',money(v.notional_paise/100)],['Estimated charges',money(v.fee_reserve_paise/100)],['Planned risk',money(v.planned_risk_paise/100)],['Protective trigger',o.stop_loss?money(o.stop_loss):'Reduction only']].map(([k,x])=>`<div><dt>${k}</dt><dd>${x}</dd></div>`).join('')}</dl><p>Preview expires at ${esc(new Date(result.expires_at).toLocaleTimeString('en-IN'))}. Submission rechecks price, funds and limits. Broker acceptance does not guarantee a fill.</p>`;
 $('pilot-confirm').classList.remove('hidden');
 });});
$('pilot-confirm').addEventListener('submit',async e=>{
 e.preventDefault();if(!preview||sending||actionBusy)return;const current=preview;sending=true;const button=e.currentTarget.querySelector('button');button.disabled=true;
 try{
 const result=await api('/api/pilot/submit',{preview_id:current.id,confirmation:$('pilot-confirm-text').value});clearPreview();
 $('pilot-preview').innerHTML=`<div class="live-result"><strong>${esc(result.status)}</strong><p>${esc(result.order.side)} ${result.order.quantity} ${esc(result.order.symbol)} at limit ${money(result.order.limit_price)}.</p><p>${result.status==='UNCERTAIN'?'The broker response is uncertain. Do not place a replacement order. Sync status and verify in Kite.':'Request recorded. Open Orders to follow actual broker fills and protective exits.'}</p>${result.broker_order_id?'<p>Broker order '+esc(result.broker_order_id)+'</p>':''}</div><button class="button secondary" id="live-show-orders">View real orders →</button>`;
 }catch(err){clearPreview();$('pilot-preview').textContent=err.message+' If submission may have reached the broker, sync Orders and inspect Kite before placing another request.';toast(err.message,true);}
 finally{sending=false;button.disabled=false;await refreshPilot(true);}
});
document.addEventListener('click',e=>{
 const cancel=e.target.closest('[data-pilot-cancel]');if(cancel){if(cancel.dataset.protective==='true'&&!window.confirm('Cancel this protective stop and pause entries? Keep supervising this position in Kite until a new exit is confirmed.'))return;run(async()=>{await api('/api/pilot/reconcile',{});await api('/api/pilot/orders/'+encodeURIComponent(cancel.dataset.pilotCancel)+'/cancel',{});});}
 if(e.target.closest('[data-live-sync]'))run(()=>api('/api/pilot/reconcile',{}));
 if(e.target.closest('#live-show-orders')){closeTicket();$('ws-account').value='LIVE';navigate('orders');refreshPilot(true);}
 if(e.target.closest('[data-view="pilot"]'))setTimeout(()=>refreshPilot(true),30);
});
window.addEventListener('artha:live-ticket-open',()=>{clearPreview();setTimeout(()=>refreshPilot(true),0);});
setInterval(()=>refreshPilot(),3000);setTimeout(()=>refreshPilot(true),250);
})();
