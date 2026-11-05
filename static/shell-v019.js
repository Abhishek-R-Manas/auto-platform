'use strict';

const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = (value, digits=2) => new Intl.NumberFormat('en-IN', {style:'currency',currency:'INR',minimumFractionDigits:digits,maximumFractionDigits:digits}).format(Number(value)||0);
const number = (value,digits=2) => new Intl.NumberFormat('en-IN',{maximumFractionDigits:digits}).format(Number(value)||0);
const signClass = value => Number(value)<0 ? 'negative':'positive';
const percent = value => `${Number(value)>=0?'+':''}${Number(value||0).toFixed(2)}%`;
const timeOf = value => new Date(value).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Kolkata'});
const compact = value => Math.abs(value)>=10000000?`${(value/10000000).toFixed(2)}Cr`:Math.abs(value)>=100000?`${(value/100000).toFixed(2)}L`:number(value,0);
const paths = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  layers:'<path d="m12 3 10 5-10 5L2 8Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
  arrows:'<path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/>',
  activity:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  book:'<path d="M4 4h7a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4ZM20 4h-3a3 3 0 0 0-3 3v14a4 4 0 0 1 4-2h2Z"/>',
  settings:'<path d="m9 3-1 3-3 1-2 4 2 3v4l4 2 3-1 3 1 4-2v-4l2-3-2-4-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  wallet:'<path d="M20 7H5a2 2 0 0 1 0-4h13v4M4 7v13h17V7"/><path d="M21 12h-6v4h6"/><path d="M17 14h.01"/>',
  coins:'<ellipse cx="8" cy="6" rx="6" ry="3"/><path d="M2 6v5c0 4 12 4 12 0V6M2 11v5c0 4 12 4 12 0v-5M15 9c8-2 9 4 3 5m3-3v6c0 2-3 3-6 3"/>',
  trend:'<path d="m3 17 6-6 4 4 8-11M15 4h6v6"/>',
  search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  step:'<path d="m5 5 10 7L5 19ZM19 5v14"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]||paths.activity}</svg>`;
function paintIcons(root=document) { root.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon)); }
paintIcons();

let state=null, activeView='home', marketKind='ALL', chartWindow='all', filterText='';
let refreshing=false, formsInitialised=false, symbolSignature='', ticketSide='BUY', previewTimer, previewSequence=0;
let toastTimer, lastFocus=null, requestSignature='', requestId='', submitting=false;
const riskLabels={trade_pct:'Per-trade planned loss · %',open_pct:'Aggregate open risk · %',daily_pct:'Daily loss threshold · %',weekly_pct:'Weekly loss threshold · %',drawdown_pct:'Drawdown threshold · %',margin_pct:'Futures margin utilisation · %',slippage_bps:'Adverse slippage · basis points',overnight_stress_pct:'Overnight stress budget · %'};
const headings={home:["Your trading workspace.","Connect, trade and build strategies in one place."],trade:["Trading desk.","Watchlist, charts, depth and order entry."],builder:["Strategy Builder.","Build and analyze options baskets and trading rules."],saved:["My Strategies.","Your drafts, historical tests and forward portfolios."],pilot:["Set up real-money trading.","Verify your account, set limits and activate this session."],forward:['Supervise every forward decision.','Run controlled paper portfolios, inspect risk and preserve prospective evidence.'],setup:['Connect your market.','Set up your Kite data subscription and verify access with a guided connection flow.'],candidates:['Build hypotheses. Demand evidence.','Create AI-assisted strategies, evaluate them reproducibly and preserve every research decision.'],marketdata:['The market, with evidence.','Browse the NSE universe, inspect quote freshness and record forward research data.'],research:['Research every assumption.','Replay historical data, compare strategies and inspect the evidence behind simulated results.'],overview:['A clear view of every decision.','Explore the market. Test your process. Keep your capital virtual.'],positions:['Know what you hold.','Track positions, protective exits and exposure across your account.'],orders:['Orders.','Follow order status, fills and pending requests in the selected account.'],strategies:['Make the process repeatable.','Test transparent rules before considering real capital.'],risk:['Give every decision a boundary.','Define virtual capital, risk budgets and operating controls.'],journal:['Keep the reasoning.','Build a durable record of decisions and observations.'],settings:['Your workspace, connected.','Choose your data source and understand the simulation boundaries.']};
const navNames={home:"Home",trade:"Trade",builder:"Strategy Builder",saved:"My Strategies",pilot:"Trading setup",forward:'Forward paper',setup:'Connection setup',candidates:'Strategy lab',marketdata:'NSE market data',research:'Research lab',overview:'Overview',positions:'Portfolio',orders:'Orders',strategies:'Strategies',risk:'Risk & capital',journal:'Trading journal',settings:'Settings'};

function toast(message,error=false){ clearTimeout(toastTimer); $('toast').textContent=message; $('toast').classList.remove('hidden'); $('toast').classList.toggle('error',error); toastTimer=setTimeout(()=>$('toast').classList.add('hidden'),6000); }
async function api(path,body){
  const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),30000);
  try{
    const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Artha-Client':'paper-workspace'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
    const data=await response.json();
    if(!response.ok){const detail=typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(e=>`${e.loc?.slice(1).join('.')}: ${e.msg}`).join('; '):'Request could not be completed';throw new Error(detail);}
    return data;
  } catch(error){if(error.name==='AbortError')throw new Error('Request timed out. Refresh the order blotter before retrying.');throw error;}
  finally{clearTimeout(timer);}
}
async function action(path,body={},message){try{const data=await api(path,body);if(message)toast(message);await refresh();return data;}catch(error){toast(error.message,true);return null;}}

function navigate(view){
  if(!headings[view])return;
  if(activeView!==view)window.scrollTo({top:0,left:0,behavior:'instant'});
  activeView=view;document.body.dataset.view=view;
  $("more-tools").open=false;
  $('workspace-mode').textContent=view==='pilot'?'REAL MONEY · GATED PILOT':view==='marketdata'?'MARKET DATA':'Paper trading';
  $('workspace-mode').classList.toggle('amber',view==='pilot');
  $('new-trade').classList.toggle('hidden',['home','trade','builder','saved','pilot','marketdata'].includes(view));
  document.querySelector('.mode-strip').classList.toggle('hidden',['home','trade','builder','saved','pilot','marketdata'].includes(view));
  document.querySelectorAll('.view').forEach(el=>el.classList.toggle('hidden',el.id!==`view-${view}`));
  document.querySelectorAll('button[data-view],a[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('session-management').classList.toggle('hidden',view!=='settings');
  $('breadcrumb').textContent=navNames[view];
  const title=headings[view][0];$('page-title').innerHTML=`${escapeHTML(title.slice(0,-1))}<span>.</span>`;
  $('page-description').textContent=headings[view][1];
  history.replaceState(null,'',`#${view}`);
  if(state)render();
  window.dispatchEvent(new CustomEvent("artha:navigate",{detail:{view}}));
}

function empty(title,caption,trade=false){return `<div class="empty-state"><span>${icon('layers')}</span><strong>${escapeHTML(title)}</strong><p>${escapeHTML(caption)}</p>${trade?'<button class="text-button" data-action="new-trade">Place your first paper trade ↗</button>':''}</div>`;}
function positionsTable(positions,short=false){
  if(!positions.length)return empty('No open positions','Your next decision starts with a paper trade.',true);
  return `<div class="table-scroll"><table class="positions-table"><thead><tr><th>Instrument</th><th>Quantity</th><th>Entry / mark</th><th>Unrealised P&L</th>${short?'':'<th>Stop / target</th><th>Planned risk</th>'}<th></th></tr></thead><tbody>${positions.map(p=>`<tr><td><strong>${escapeHTML(p.symbol)}</strong><div class="fill-source">${escapeHTML(p.kind)} · ${escapeHTML(p.source)}</div></td><td class="numeric">${number(p.quantity,0)}</td><td class="numeric">${money(p.average_price)}<div class="fill-source">${money(p.mark)}</div></td><td class="numeric ${signClass(p.unrealised)}">${money(p.unrealised)}</td>${short?'':`<td class="numeric">${money(p.stop_loss)}<div class="fill-source">${p.take_profit?money(p.take_profit):'No target'}</div></td><td class="numeric">${money(p.planned_risk)}</td>`}<td><button class="action-small" data-close-position="${escapeHTML(p.symbol)}">Close</button></td></tr>`).join('')}</tbody></table></div>`;
}

function drawEquity(){
  let observations=state.history;if(chartWindow!=='all')observations=observations.slice(-Number(chartWindow));
  if(!observations.length)return;
  const width=Math.max(260,$('equity-chart').clientWidth-26),height=205,left=3,right=72,top=14,bottom=27;
  const values=observations.map(p=>Number(p.equity));let low=Math.min(...values),high=Math.max(...values);
  const minimum=Math.max(100,state.account.capital*.001);const spread=Math.max(high-low,minimum);
  low-=(spread-(high-low))/2+spread*.15;high=low+spread*1.3;
  const x=i=>left+i/Math.max(1,values.length-1)*(width-left-right);
  const y=value=>top+(high-value)/(high-low)*(height-top-bottom);
  let points=values.map((value,i)=>[x(i),y(value)]);if(points.length===1)points=[[left,y(values[0])],[width-right,y(values[0])]];
  const line=points.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ');
  const area=`${line} L${points.at(-1)[0]},${height-bottom} L${left},${height-bottom} Z`;
  let grid='';for(let i=0;i<4;i++){const value=low+(high-low)*i/3,yy=y(value);const label=Math.abs(value)>=100000?`${(value/100000).toFixed(3)}L`:number(value,0);grid+=`<line x1="${left}" y1="${yy}" x2="${width-right+5}" y2="${yy}" stroke="#edf2ed" stroke-dasharray="3 5"/><text x="${width-right+12}" y="${yy+3}" fill="#7c8d80" font-size="9" font-family="monospace">₹${label}</text>`;}
  const last=points.at(-1);const firstTime=timeOf(observations[0].time),lastTime=timeOf(observations.at(-1).time);
  $('equity-chart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><defs><linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#308562" stop-opacity=".12"/><stop offset="1" stop-color="#308562" stop-opacity="0"/></linearGradient></defs>${grid}<path d="${area}" fill="url(#equity-fill)"/><path d="${line}" fill="none" stroke="#388666" stroke-width="2" vector-effect="non-scaling-stroke"/><circle cx="${last[0]}" cy="${last[1]}" r="3" fill="#388666" stroke="#fff" stroke-width="2"/><text x="3" y="${height-5}" fill="#a4b09f" font-size="9" font-family="monospace">${firstTime}</text><text x="${width-right-33}" y="${height-5}" fill="#a4b09f" font-size="9" font-family="monospace">${lastTime}</text></svg>`;
}
function sparkline(values){if(values.length<2)return '<span class="fill-source">Collecting</span>';const low=Math.min(...values),span=Math.max(.01,Math.max(...values)-low);const line=values.map((v,i)=>`${i?'L':'M'}${(i/(values.length-1)*72+1).toFixed(1)},${(20-(v-low)/span*17).toFixed(1)}`).join(' ');return `<svg viewBox="0 0 75 23" aria-hidden="true"><path d="${line}" fill="none" stroke="${values.at(-1)>=values[0]?'#83ad92':'#c99a8e'}" stroke-width="1.2"/></svg>`;}

function renderMarket(){
  const items=state.market.filter(i=>(marketKind==='ALL'||i.kind===marketKind)&&(`${i.symbol} ${i.name}`.toLowerCase().includes(filterText)));
  $('market-body').innerHTML=items.map(i=>`<tr><td><div class="instrument-cell"><span class="instrument-logo ${i.kind==='FUT'?'future':i.kind==='OPT'?'option':''}">${escapeHTML(i.kind==='EQ'?i.symbol.slice(0,2):i.kind==='FUT'?'FU':i.option_type)}</span><div><strong>${escapeHTML(i.kind==='EQ'?i.symbol:i.name)}</strong><small>${escapeHTML(i.exchange)} · ${i.kind==='EQ'?'Equity':`Lot ${number(i.lot_size,0)}`} ${i.fresh?'':'· stale'}</small></div></div></td><td class="numeric">${money(i.last)}</td><td class="numeric ${signClass(i.change_pct)}">${percent(i.change_pct)}</td><td class="sparkline">${sparkline(i.sparkline)}</td><td class="numeric">${money(i.ask-i.bid)}</td><td><button class="trade-action" data-trade="${escapeHTML(i.symbol)}" aria-label="Paper trade ${escapeHTML(i.symbol)}">+</button></td></tr>`).join('')||'<tr><td colspan="6">No matching instruments.</td></tr>';
  $('market-count').textContent=`${items.length} instruments`;
}

function renderOrders(){
  const filter=$('order-filter').value;
  const orders=state.orders.filter(o=>filter==='ALL'||(filter==='ACTIVE'?['OPEN','PARTIAL'].includes(o.status):o.status===filter));
  $('orders-table').innerHTML=!orders.length?empty('No orders in this view','Your paper requests and execution status will appear here.'):`<div class="table-scroll"><table><thead><tr><th>Time</th><th>Instrument</th><th>Side / type</th><th>Filled / quantity</th><th>Avg. fill</th><th>Status</th><th></th></tr></thead><tbody>${orders.map(o=>`<tr><td class="numeric">${timeOf(o.time)}</td><td><strong>${escapeHTML(o.symbol)}</strong><div class="fill-source">${escapeHTML(o.source)}${o.reduce_only?' · reduce only':''}</div></td><td class="${o.side==='BUY'?'positive':'negative'}">${escapeHTML(o.side)}<div class="fill-source">${escapeHTML(o.order_type)}</div></td><td class="numeric">${number(o.filled_quantity,0)} / ${number(o.quantity,0)}</td><td class="numeric">${o.average_price?money(o.average_price):'—'}</td><td><span class="order-status ${escapeHTML(o.status)}">${escapeHTML(o.status)}</span>${o.reason?`<span class="order-reason" title="${escapeHTML(o.reason)}">${escapeHTML(o.reason)}</span>`:''}</td><td>${['OPEN','PARTIAL'].includes(o.status)?`<button class="action-small" data-cancel="${escapeHTML(o.id)}">Cancel remainder</button>`:''}</td></tr>`).join('')}</tbody></table></div>`;
  $('fills-table').innerHTML=!state.fills.length?empty('No simulated fills yet','Execution records are committed together with the cash ledger.'):`<div class="table-scroll"><table><thead><tr><th>Time</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Price</th><th>Estimated fees</th><th>Gross realised</th></tr></thead><tbody>${state.fills.map(f=>`<tr><td class="numeric">${timeOf(f.time)}</td><td>${escapeHTML(f.symbol)}</td><td class="${f.side==='BUY'?'positive':'negative'}">${escapeHTML(f.side)}</td><td class="numeric">${number(f.quantity,0)}</td><td class="numeric">${money(f.price)}</td><td class="numeric">${money(f.fees)}</td><td class="numeric ${signClass(f.realised)}">${money(f.realised)}</td></tr>`).join('')}</tbody></table></div>`;
}

function render(){
  const a=state.account;
  $('date-label').textContent=new Date(['pilot','marketdata'].includes(activeView)?Date.now():state.clock).toLocaleDateString('en-IN',{weekday:'long',day:'2-digit',month:'long',year:'numeric',timeZone:'Asia/Kolkata'}).toUpperCase()+` · ${['pilot','marketdata'].includes(activeView)?'IST':state.source==='DEMO'?'VIRTUAL SESSION':'IST'}`;
  $('metric-equity').textContent=money(a.equity);$('metric-pnl').textContent=money(a.pnl);$('metric-pnl').className=signClass(a.pnl);
  $('metric-return').textContent=percent(a.pnl/a.capital*100);$('metric-return').className=signClass(a.pnl);
  $('metric-cash').textContent=money(a.available_cash);$('metric-margin').textContent=`${money(a.margin_used,0)} est. margin · ${money(a.reserved_cash,0)} reserved`;
  $('metric-risk').textContent=money(a.planned_risk);$('metric-risk-budget').textContent=`${number(a.planned_risk/Math.max(1,a.equity)*100)}% used · ${state.risk.open_pct}% limit`;
  $('chart-value').textContent=money(a.equity);$('chart-return').textContent=percent(a.pnl/a.capital*100);$('chart-return').className=signClass(a.pnl);
  $('chart-drawdown').textContent=`${a.drawdown_pct.toFixed(2)}%`;$('chart-observations').textContent=`${state.history.length} observations`;
  drawEquity();renderMarket();
  if(window.arthaRenderPortfolioBadge)window.arthaRenderPortfolioBadge();else $('position-count').textContent='—';$('open-count').textContent=state.positions.length;
  $('overview-positions').innerHTML=positionsTable(state.positions.slice(0,4),true);
  $('positions-table').innerHTML=positionsTable(state.positions);
  const stale=state.market.some(i=>!i.fresh);
  $('risk-status').textContent=state.halt_reason?'HALTED':state.paused?'PAUSED':stale?'DATA CHECK':'WITHIN LIMITS';
  $('risk-status').className=`status-pill ${state.halt_reason?'danger':state.paused||stale?'warn':''}`;
  $('risk-heading').textContent=state.halt_reason?'Risk threshold reached':state.paused?'New entries paused':stale?'Fresh quotes required':'Within your boundaries';
  $('risk-caption').textContent=state.halt_reason||'Protective exits remain active.';
  const rows=[['Planned open risk',a.planned_risk/Math.max(1,a.equity)*100,state.risk.open_pct],['Daily loss',Math.max(0,-a.day_pnl)/Math.max(1,a.equity-a.day_pnl)*100,state.risk.daily_pct],['Joint stress estimate',a.stress_loss/Math.max(1,a.equity)*100,state.risk.overnight_stress_pct]];
  $('risk-bars').innerHTML=rows.map(([label,value,limit])=>`<div class="risk-bar"><div><span>${label}</span><strong>${value.toFixed(2)} / ${limit}%</strong></div><div class="bar-track"><i class="${value>=limit?'danger':value>limit*.7?'warning':''}" style="width:${Math.min(100,Math.max(0,value/limit*100))}%"></i></div></div>`).join('');
  $('pause-entries').textContent=state.paused?'Resume entries':'Pause entries';
  $('pause-entries').disabled=Boolean(state.halt_reason);
  $('reconcile-status').textContent=state.reconcile.ok?'RECONCILED':'MISMATCH';$('reconcile-status').className=`pill ${state.reconcile.ok?'green':'neutral'}`;
  $('source-label').textContent=state.source==='DEMO'?'Synthetic market':'Zerodha market data';
  $('source-description').textContent=state.source==='DEMO'?'Illustrative prices and contract sizes. All executions are simulated.':`Stream ${state.market_data?.phase||'waiting'} · Virtual balances and simulated executions.`;
  $('market-clock').textContent=`${timeOf(state.clock)} IST`;
  $('playback').textContent=state.auto_feed?'Pause feed':'Play feed';$('playback').disabled=state.source!=='DEMO';$('step-market').disabled=state.source!=='DEMO';
  $('market-footnote').textContent=state.source==='DEMO'?'Synthetic prices · illustrative derivative lot sizes':'Kite quotes · estimated execution and funding';
  $('footer-status').textContent=activeView==='pilot'?'Separate broker pilot · explicit real-money confirmation':activeView==='marketdata'?'Kite market observations · timestamped historical cache':`${state.source==='DEMO'?'Synthetic market':'Kite market data'} · Paper execution · ${state.reconcile.ok?'Ledger reconciled':'Reconciliation failed'}`;
  const strategy=state.strategies[0];$('strategy-state').textContent=strategy.status;$('strategy-status-full').textContent=strategy.status;$('strategy-signal').textContent=strategy.last_signal;
  $('strategy-dot').style.background=strategy.enabled?'#5a987a':'#b9c5b5';
  $('greek-totals').innerHTML=[['Gamma',state.greeks.gamma,4],['Theta · ₹/day',state.greeks.theta,2],['Vega · ₹/IV point',state.greeks.vega,2]].map(([label,value,digits])=>`<div><span>${label}</span><strong>${number(value,digits)}</strong></div>`).join('');
  $('underlying-exposure').innerHTML=Object.entries(state.underlying_delta).map(([key,value])=>`<div class="exposure-row"><span>${escapeHTML(key)}</span><strong>${number(value,2)} units</strong></div>`).join('')||empty('No directional exposure','Exposure appears when positions are open.');
  $('pnl-breakdown').innerHTML=[['Realised net · includes costs',a.realised_net],['Unrealised',a.unrealised],['Estimated charges',a.total_fees]].map(([label,value])=>`<div><span>${escapeHTML(label)}</span><strong>${money(value)}</strong></div>`).join('');
  $('strategy-version').textContent=strategy.version;
  $('strategy-specification').textContent=state.strategy_specification;
  $('execution-assumptions').innerHTML=state.execution_model.assumptions.map(text=>`<p class="card-note">${escapeHTML(text)}</p>`).join('');
  renderOptions();syncDecisions();
  renderOrders();
  $('activity-list').innerHTML=state.journal.slice(0,4).map(e=>`<div class="activity-row"><span class="activity-icon">${icon(e.kind==='FILL'?'arrows':e.kind==='ORDER'?'plus':'shield')}</span><div class="activity-text"><strong title="${escapeHTML(e.message)}">${escapeHTML(e.message)}</strong><small>${timeOf(e.time)} IST · ${escapeHTML(e.kind.toLowerCase())}</small></div></div>`).join('');
  $('journal-list').innerHTML=state.journal.map(e=>`<div class="journal-event ${escapeHTML(e.level.toLowerCase())}"><time>${timeOf(e.time)} IST</time><div><span class="event-type">${escapeHTML(e.kind)}</span><p>${escapeHTML(e.message)}</p></div></div>`).join('');
  $('connection-status').textContent=state.source;
  $('kite-status').textContent=state.kite.authenticated?'Authenticated. Market data can be connected.':state.kite.login_available?'Server credentials configured. Authenticate for today’s session.':state.kite.configured?'API key configured. Add a session token or API secret on the server.':'Server-side credentials are not configured.';
  $('kite-login').classList.toggle('hidden',!state.kite.login_available);$('use-kite').disabled=!state.kite.authenticated;$('use-demo').disabled=state.source==='DEMO';
  $('limitations-list').innerHTML=state.limitations.map(text=>`<div class="capability-row">${escapeHTML(text)}</div>`).join('');
  const signature=state.market.map(i=>i.symbol).join('|');
  if(signature!==symbolSignature){symbolSignature=signature;const options=state.market.map(i=>`<option value="${escapeHTML(i.symbol)}">${escapeHTML(i.name)} · ${escapeHTML(i.kind)}</option>`).join('');$('ticket-symbol').innerHTML=options;$('strategy-symbol').innerHTML=options;}
  if(!formsInitialised){$('capital-amount').value=a.capital;$('risk-fields').innerHTML=Object.entries(riskLabels).map(([key,label])=>`<label>${label}<input type="number" data-risk="${key}" value="${state.risk[key]}" step="0.01" min="0.01" max="${key==='trade_pct'?8:50}" required></label>`).join('');$('strategy-symbol').value=strategy.symbol;$('strategy-fast').value=strategy.fast;$('strategy-slow').value=strategy.slow;$('strategy-stop').value=strategy.stop_pct;$('strategy-enabled').checked=strategy.enabled;formsInitialised=true;}
  const error=state.halt_reason||state.feed_error;
  $('system-error').classList.toggle('hidden',!error);$('system-error').textContent=error?`${error}. Review the account and journal; actual exits require usable quotes.`:'';
  if(!$('trade-drawer').classList.contains('hidden')){renderTicketQuote();schedulePreview();}
}

let decisionRecords=[], decisionSession='', decisionHead='', decisionLoading=false, decisionBrowsing=false;
function renderOptions(){
  $('options-monitor').innerHTML=state.option_groups.map(g=>`<h3>${escapeHTML(g.underlying)} · net held exposure</h3><p class="card-note">Delta ${number(g.delta,2)} units · Gamma ${number(g.gamma,4)} · Theta ${money(g.theta)}/day · Vega ${money(g.vega)}/IV point</p><div class="table-scroll"><table><thead><tr><th>Leg / expiry</th><th>Held units</th><th>Delta / Gamma per unit</th><th>Theta / Vega per unit</th><th>Spread</th><th>Bid / ask units</th><th>Liquidity</th></tr></thead><tbody>${g.legs.map(l=>`<tr><td>${escapeHTML(l.symbol)}<div class="fill-source">${escapeHTML(l.expiry.slice(0,10))} · ${number(l.days_to_expiry,2)} days</div></td><td>${number(l.quantity,0)}</td><td>${l.greeks?number(l.greeks.delta,4)+' / '+number(l.greeks.gamma,6):'Model unavailable'}</td><td>${l.greeks?money(l.greeks.theta)+' / '+money(l.greeks.vega):'—'}</td><td>${money(l.spread)} · ${number(l.spread_pct)}%</td><td>${number(l.bid_quantity,0)} / ${number(l.ask_quantity,0)}<div class="fill-source">${l.lot_size} units/lot</div></td><td>${escapeHTML(l.liquidity)}</td></tr>`).join('')}</tbody></table></div>`).join('')||empty('No option instruments','Connect a supported market source to inspect options.');
}
function renderDecisions(){
  const opened=new Set([...$('decision-list').querySelectorAll('details[open][data-decision]')].map(el=>el.dataset.decision));
  $('decision-list').innerHTML=decisionRecords.map(d=>`<details class="decision-record" data-decision="${escapeHTML(d.id)}" ${opened.has(d.id)?'open':''}><summary><strong>${escapeHTML(d.action)} · ${escapeHTML(d.symbol)}</strong> · ${escapeHTML(new Date(d.time).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}))}<p>${escapeHTML(d.reason)}</p></summary><div class="decision-body"><p>${d.strategy?escapeHTML(d.strategy.id+' · '+d.strategy.version):'Manual or account control'} · ${escapeHTML(d.source)} · ${escapeHTML(d.execution_model.version)}</p>${d.quote?`<p>Known quote: bid ${money(d.quote.bid)}, ask ${money(d.quote.ask)} · exchange ${escapeHTML(d.quote.exchange_time)}</p>`:''}${d.sizing?`<p>Requested ${number(d.sizing.selected_quantity,0)} units; eligible maximum ${number(d.sizing.quantity,0)}. Risk budget ${money(d.sizing.risk_budget)}.</p><p>${escapeHTML(d.sizing.explanation)}</p>`:''}${d.execution?`<p>Simulated fill: ${number(d.execution.quantity,0)} units at ${money(d.execution.price)}; reference ${money(d.execution.reference)}, fees ${money(d.execution.fees)}, paper delay ${number(d.execution.delay_ms,0)} ms.</p>`:''}${d.checks.length?`<div class="table-scroll"><table><thead><tr><th>Risk check</th><th>Result</th><th>Observed / limit</th><th>Explanation</th></tr></thead><tbody>${d.checks.map(c=>`<tr><td>${escapeHTML(c.name.replaceAll('_',' '))}</td><td>${escapeHTML(c.status)}</td><td>${escapeHTML(c.observed??'—')} / ${escapeHTML(c.limit??'—')}</td><td>${escapeHTML(c.reason)}</td></tr>`).join('')}</tbody></table></div>`:''}${d.inputs?`<details><summary>Observed strategy inputs</summary><pre>${escapeHTML(JSON.stringify(d.inputs,null,2))}</pre></details>`:''}<details><summary>Complete decision evidence</summary><pre>${escapeHTML(JSON.stringify(d,null,2))}</pre></details></div></details>`).join('')||empty('No decision records yet','New orders and enabled strategy evaluations create durable evidence. Earlier activity remains in the journal.');
  $('decisions-more').disabled=!decisionRecords.length||decisionLoading;
}
function syncDecisions(){
  if(decisionSession!==state.session_id){decisionRecords=[];decisionSession=state.session_id;decisionHead='';decisionBrowsing=false;}
  if(decisionBrowsing)return;
  const head=state.decisions[0]?.id||'';
  if(head!==decisionHead){if(decisionHead&&!state.decisions.some(d=>d.id===decisionHead))decisionRecords=[];decisionHead=head;const byId=new Map([...state.decisions,...decisionRecords].map(d=>[d.id,d]));decisionRecords=[...byId.values()].sort((a,b)=>b.sequence-a.sequence).slice(0,100);renderDecisions();}
  else if(!decisionRecords.length)renderDecisions();
}
async function loadDecisions(reset=false){
  if(decisionLoading)return;
  decisionLoading=true;
  try{
    const before=reset?null:decisionRecords.at(-1)?.sequence;
    const rows=await api('/api/decisions?limit=50&all_sessions='+$('decisions-all').checked+(before?'&before='+before:''));
    if(reset||rows.length)decisionRecords=rows;
    decisionBrowsing=!reset;
    if(reset)decisionHead=state.decisions[0]?.id||'';
    decisionRecords=[...new Map(decisionRecords.map(d=>[d.id,d])).values()].sort((a,b)=>b.sequence-a.sequence);
    if(!rows.length)toast('All available decision records are loaded.');
  }catch(error){toast(error.message,true);}finally{decisionLoading=false;renderDecisions();}
}
$('decisions-latest').addEventListener('click',()=>loadDecisions(true));
$('decisions-more').addEventListener('click',()=>loadDecisions());
$('decisions-all').addEventListener('change',()=>loadDecisions(true));

async function refresh(){if(refreshing)return;refreshing=true;try{state=await api('/api/state');render();window.dispatchEvent(new CustomEvent('artha:state'));}catch(error){$('system-error').classList.remove('hidden');$('system-error').textContent=`Workspace connection unavailable: ${error.message}. Displayed information may be stale.`;}finally{refreshing=false;}}

function ticketItem(){return state?.market.find(i=>i.symbol===$('ticket-symbol').value);}
function renderTicketQuote(){const item=ticketItem();if(!item)return;$('ticket-quote').innerHTML=`<div><strong>${money(item.last)}</strong><br><small>Bid ${money(item.bid)} · Ask ${money(item.ask)}</small></div><div><span class="pill ${item.fresh?'green':'neutral'}">${item.fresh?'QUOTE READY':'STALE'}</span><br><small>${item.kind==='EQ'?'Shares':`${item.lot_size} units per lot`}</small></div>`;}
function setTicketDefaults(){const item=ticketItem();if(!item)return;const distance=item.kind==='EQ'?.02:item.kind==='FUT'?.002:.12;const ref=ticketSide==='BUY'?item.ask:item.bid;const direction=ticketSide==='BUY'?1:-1;$('ticket-stop').value=(ref*(1-direction*distance)).toFixed(2);$('ticket-target').value=(ref*(1+direction*distance*2)).toFixed(2);$('ticket-limit').value=(Math.round(ref/item.tick_size)*item.tick_size).toFixed(2);$('ticket-limit').step=item.tick_size;const label=$('quantity-label');label.firstChild.textContent=item.kind==='EQ'?'Shares':'Lots';$('ticket-quantity').value=item.kind==='EQ'?10:1;renderTicketQuote();schedulePreview();}
function openTicket(symbol){$("ticket-heading").textContent="New paper order";$("trade-drawer").querySelector(".eyebrow").textContent="PAPER ACCOUNT · SIMULATED EXECUTION";$("trade-form").classList.remove("hidden");$("live-ticket-panel").classList.add("hidden");if(!state)return;$("ticket-rationale").value="";lastFocus=document.activeElement;if(symbol)$('ticket-symbol').value=symbol;ticketSide='BUY';document.querySelectorAll('[data-side]').forEach(b=>b.classList.toggle('active',b.dataset.side===ticketSide));$('trade-overlay').classList.remove('hidden');$('trade-drawer').classList.remove('hidden');document.querySelector('.app-shell').inert=true;document.querySelector('.sidebar').inert=true;requestSignature='';requestId='';setTicketDefaults();$('ticket-symbol').focus();}
function closeTicket(){$('trade-overlay').classList.add('hidden');$('trade-drawer').classList.add('hidden');document.querySelector('.app-shell').inert=false;document.querySelector('.sidebar').inert=false;clearTimeout(previewTimer);previewSequence++;lastFocus?.focus();}
function ticketBody(){const item=ticketItem();const lots=Number($('ticket-quantity').value);const stop=Number($('ticket-stop').value);if(!item||!Number.isInteger(lots)||lots<=0||!Number.isFinite(stop)||stop<=0)throw new Error('Enter a positive whole quantity and protective stop.');return {rationale:$("ticket-rationale").value||null,symbol:item.symbol,side:ticketSide,quantity:lots*(item.kind==='EQ'?1:item.lot_size),order_type:$('ticket-type').value,limit_price:$('ticket-type').value==='LIMIT'?Number($('ticket-limit').value):null,stop_loss:stop,take_profit:$('ticket-target').value?Number($('ticket-target').value):null,reduce_only:false};}
function schedulePreview(){clearTimeout(previewTimer);previewTimer=setTimeout(previewTicket,250);}
async function previewTicket(){const seq=++previewSequence;try{const result=await api('/api/orders/preview',ticketBody());if(seq!==previewSequence)return;$('order-preview').innerHTML=`<div class="preview-row"><span>Estimated execution</span><strong>${money(result.estimated_price)}</strong></div><div class="preview-row"><span>Estimated funding</span><strong>${money(result.estimated_funding)}</strong></div><div class="preview-row"><span>Planned stop distance loss*</span><strong>${money(result.estimated_stop_loss)}</strong></div><div class="preview-row"><span>Entry costs · estimate</span><strong>${money(result.estimated_fees.total)}</strong></div><div class="preview-status ${result.allowed?'':'blocked'}">${icon(result.allowed?'check':'shield')}<span>${escapeHTML(result.allowed?'Order passes current entry checks':result.reason)}</span></div>`;$('submit-trade').disabled=!result.allowed||submitting;}catch(error){if(seq!==previewSequence)return;$('order-preview').innerHTML=`<div class="preview-status blocked">${icon('shield')}<span>${escapeHTML(error.message)}</span></div>`;$('submit-trade').disabled=true;}}

document.addEventListener('click',async event=>{
  const view=event.target.closest('button[data-view],a[data-view]');if(view)navigate(view.dataset.view);
  const go=event.target.closest('[data-go]');if(go)navigate(go.dataset.go);
  const trade=event.target.closest('[data-trade]');if(trade)openTicket(trade.dataset.trade);
  if(event.target.closest('[data-action="new-trade"]'))openTicket();
  const cap=event.target.closest('[data-capital]');if(cap)$('capital-amount').value=cap.dataset.capital;
  const cancel=event.target.closest('[data-cancel]');if(cancel)await action(`/api/orders/${encodeURIComponent(cancel.dataset.cancel)}/cancel`,{},'Unfilled remainder cancelled.');
  const close=event.target.closest('[data-close-position]');if(close){const result=await action(`/api/positions/${encodeURIComponent(close.dataset.closePosition)}/close`);if(result)toast(result.status==='REJECTED'?result.reason:`Paper exit ${result.status.toLowerCase()}. Check remaining positions.`,result.status==='REJECTED');}
});
$('new-trade').addEventListener('click',()=>openTicket());$('close-ticket').addEventListener('click',closeTicket);$('trade-overlay').addEventListener('click',closeTicket);
$('ticket-symbol').addEventListener('change',setTicketDefaults);
$('ticket-type').addEventListener('change',()=>{$('limit-field').classList.toggle('hidden',$('ticket-type').value!=='LIMIT');schedulePreview();});
document.querySelectorAll('[data-side]').forEach(button=>button.addEventListener('click',()=>{ticketSide=button.dataset.side;document.querySelectorAll('[data-side]').forEach(b=>b.classList.toggle('active',b===button));setTicketDefaults();}));
['ticket-quantity','ticket-stop','ticket-target','ticket-limit','ticket-rationale'].forEach(id=>$(id).addEventListener('input',schedulePreview));
$('trade-form').addEventListener('submit',async event=>{event.preventDefault();if(submitting)return;submitting=true;$('submit-trade').disabled=true;try{const body=ticketBody();const signature=JSON.stringify(body);if(signature!==requestSignature){requestSignature=signature;requestId=crypto.randomUUID();}const order=await api('/api/orders',{...body,client_id:requestId});if(order.status==='REJECTED'){toast(order.reason,true);}else{closeTicket();navigate('orders');toast(`Paper order ${order.status.toLowerCase()}.`);}await refresh();}catch(error){toast(error.message,true);}finally{submitting=false;schedulePreview();}});
$('size-trade').addEventListener('click',async()=>{try{const result=await api('/api/orders/size',ticketBody());const item=ticketItem();if(!result.quantity){toast(result.reason||'No quantity fits the current risk policy.',true);return;}$('ticket-quantity').value=result.quantity/(item.kind==='EQ'?1:item.lot_size);schedulePreview();toast(`Size calculated within a ${money(result.risk_budget)} planned risk budget.`);}catch(error){toast(error.message,true);}});
$('market-search').addEventListener('input',event=>{filterText=event.target.value.toLowerCase();if(state)renderMarket();});
$('market-tabs').addEventListener('click',event=>{const button=event.target.closest('[data-kind]');if(!button)return;marketKind=button.dataset.kind;document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',b===button));if(state)renderMarket();});
$('chart-window').addEventListener('click',event=>{const button=event.target.closest('[data-window]');if(!button)return;chartWindow=button.dataset.window;document.querySelectorAll('[data-window]').forEach(b=>b.classList.toggle('active',b===button));if(state)drawEquity();});
$('order-filter').addEventListener('change',()=>state&&renderOrders());
$('playback').addEventListener('click',()=>action('/api/controls/autoplay',{enabled:!state.auto_feed}));
$('step-market').addEventListener('click',()=>action('/api/controls/step',{count:1}));
$('pause-entries').addEventListener('click',()=>action('/api/controls/pause',{paused:!state.paused},state.paused?'New entries resumed.':'New entries paused. Protective exits remain active.'));
$('flatten').addEventListener('click',async()=>{if(confirm('Pause new entries, cancel pending entries, and request simulated exits for every open position? Exits may remain unfilled.'))await action('/api/controls/flatten',{},'Paper exits requested. Review fills and remaining positions.');});
$('capital-form').addEventListener('submit',async event=>{event.preventDefault();await action('/api/account/capital',{amount:Number($('capital-amount').value)},'Virtual allocation updated. Existing P&L is preserved.');});
$('risk-form').addEventListener('submit',async event=>{event.preventDefault();const body={};document.querySelectorAll('[data-risk]').forEach(el=>body[el.dataset.risk]=Number(el.value));await action('/api/risk',body,'Paper risk policy saved.');});
$('strategy-form').addEventListener('submit',async event=>{event.preventDefault();await action('/api/strategy',{enabled:$('strategy-enabled').checked,symbol:$('strategy-symbol').value,fast:Number($('strategy-fast').value),slow:Number($('strategy-slow').value),stop_pct:Number($('strategy-stop').value)},'Paper strategy configuration saved.');});
$('note-form').addEventListener('submit',async event=>{event.preventDefault();const result=await action('/api/journal/notes',{text:$('journal-note').value},'Observation added to your journal.');if(result)$('journal-note').value='';});
for(const id of ['export-top','export-journal'])$(id).addEventListener('click',()=>{window.location.href='/api/journal/export';});
$('use-demo').addEventListener('click',()=>action('/api/market/source',{source:'DEMO'},'Synthetic market connected. Execution remains paper-only.'));
$('use-kite').addEventListener('click',async()=>{const button=$('use-kite');button.disabled=true;button.textContent='Loading market data…';try{await action('/api/market/source',{source:'KITE',...($('kite-instruments').value.trim()?{instruments:$('kite-instruments').value.split(/[,\n]+/).map(v=>v.trim()).filter(Boolean)}:{})},'Zerodha quotes connected to paper execution.');}finally{button.textContent='Use Kite quotes';button.disabled=!state?.kite.authenticated;}});
$('new-session').addEventListener('click',async()=>{const capital=Number($('capital-amount').value);if(!confirm(`Archive this paper session and start a fresh experiment with ${money(capital)}? Existing records will be retained.`))return;const result=await action('/api/account/new-session',{amount:capital},'New paper session started. Previous results are archived.');if(result)await loadArchives();});
async function loadArchives(){try{const archives=await api('/api/account/archives');$('session-archives').innerHTML=archives.length?`<div class="table-scroll"><table><thead><tr><th>Session</th><th>Ended</th><th>Virtual capital</th><th>Net P&L</th><th>Orders</th></tr></thead><tbody>${archives.map(a=>`<tr><td>#${a.id} · ${escapeHTML(a.source)}</td><td>${escapeHTML(new Date(a.ended).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}))}</td><td class="numeric">${money(a.capital)}</td><td class="numeric ${signClass(a.pnl)}">${money(a.pnl)}</td><td>${a.orders}</td></tr>`).join('')}</tbody></table></div>`:empty('No archived sessions','Your completed paper experiments will appear here.');}catch(error){toast(error.message,true);}}
document.addEventListener('keydown',event=>{if($('trade-drawer').classList.contains('hidden'))return;if(event.key==='Escape')closeTicket();if(event.key==='Tab'){const fields=[...$('trade-drawer').querySelectorAll('button:not(:disabled),select,input')].filter(el=>el.offsetParent!==null);const first=fields[0],last=fields.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
if(location.search.includes('kite=connected')){toast('Zerodha authenticated. Select “Use Kite quotes” in Settings to switch data.');history.replaceState(null,'','#settings');}
navigate(location.hash.slice(1)||'home');
window.addEventListener('resize',()=>{if(state)drawEquity();});
refresh();loadArchives();setInterval(refresh,2000);
