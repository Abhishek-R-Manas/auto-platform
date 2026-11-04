'use strict';
(()=>{
  const base='/api/research';
  let datasets=[],runs=[],references=[],chosenDataset='',chosenRun='',reportKey='',events=[],eventAfter=0;
  let fetching=false,templateSequence=0;
  const compared=new Set();
  const pretty=value=>JSON.stringify(value,null,2);
  const localDate=value=>value?new Date(value).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}):'—';
  const warning=text=>`<div class="research-warning">${escapeHTML(text)}</div>`;
  const jsonDetails=(title,value)=>`<details><summary>${escapeHTML(title)}</summary><pre class="research-json">${escapeHTML(pretty(value))}</pre></details>`;
  const error=message=>{$('research-error').textContent=message;$('research-error').classList.remove('hidden');};
  function clearError(){$('research-error').classList.add('hidden');}
  async function perform(task){clearError();try{return await task();}catch(e){error(e.message);return null;}}
  function tab(name){
    document.querySelectorAll('.research-panel').forEach(el=>el.classList.toggle('hidden',el.id!=='research-panel-'+name));
    document.querySelectorAll('[data-research-tab]').forEach(el=>el.classList.toggle('active',el.dataset.researchTab===name));
    if(name==='report'&&chosenRun)loadReport();
  }
  function currentDataset(){return datasets.find(d=>d.id===chosenDataset);}
  function config(){const value=JSON.parse($('research-config').value);if(!value.dataset_id)throw new Error('Choose a dataset and strategy template first.');return value;}
  function setConfig(value){
    $('research-config').value=pretty(value);$('research-name').value=value.name;$('research-capital').value=value.capital;
    $('research-trade-risk').value=value.risk.trade_pct;$('research-stress-risk').value=value.risk.overnight_stress_pct;
    $('research-slippage').value=value.risk.slippage_bps;
    $('research-preflight-result').innerHTML='';
  }
  function reflectControls(){
    try{const value=config();value.name=$('research-name').value;value.capital=Number($('research-capital').value);
      value.risk.trade_pct=Number($('research-trade-risk').value);value.risk.overnight_stress_pct=Number($('research-stress-risk').value);value.risk.slippage_bps=Number($('research-slippage').value);$('research-config').value=pretty(value);
    }catch(e){error(e.message);}
  }
  function chart(series,normalise=false){
    const colors=['#267f59','#5088a8','#bd7b4b','#8c70a4','#b24e60','#6b8b3d'];
    const points=series.flatMap(s=>s.curve.filter(p=>Number.isFinite(p.equity)).map(p=>[Date.parse(p.time),normalise?p.equity/s.capital*100:p.equity]));
    if(!points.length)return '<p class="card-note">No equity observations in this evaluation range.</p>';
    const x0=Math.min(...points.map(p=>p[0])),x1=Math.max(...points.map(p=>p[0]));let y0=Math.min(...points.map(p=>p[1])),y1=Math.max(...points.map(p=>p[1]));
    const pad=Math.max(1,(y1-y0)*.1);y0-=pad;y1+=pad;
    const x=t=>40+(t-x0)/Math.max(1,x1-x0)*870,y=v=>200-(v-y0)/(y1-y0)*175;
    const lines=series.map((s,index)=>{const line=s.curve.filter(p=>Number.isFinite(p.equity)).map((p,i)=>`${i?'L':'M'}${x(Date.parse(p.time)).toFixed(1)},${y(normalise?p.equity/s.capital*100:p.equity).toFixed(1)}`).join(' ');return `<path d="${line}" fill="none" stroke="${colors[index%colors.length]}" stroke-width="2"/>`;}).join('');
    return `<svg class="research-chart" viewBox="0 0 960 235" role="img" aria-label="Research equity curves"><line x1="40" y1="200" x2="910" y2="200" stroke="#dce6dc"/><text x="40" y="15" font-size="11" fill="#6c7e6a">${escapeHTML(normalise?'Starting capital = 100':money(y1))}</text>${lines}<text x="40" y="225" font-size="11" fill="#6c7e6a">${escapeHTML(new Date(x0).toLocaleDateString('en-IN'))}</text><text x="810" y="225" font-size="11" fill="#6c7e6a">${escapeHTML(new Date(x1).toLocaleDateString('en-IN'))}</text></svg><p class="card-note">${series.map((s,i)=>`<span style="color:${colors[i%colors.length]}">${escapeHTML(s.name)}</span>`).join(' · ')}</p>`;
  }
  async function loadTemplates(reset=true){
    if(!chosenDataset)return;
    const seq=++templateSequence;
    const result=await api(base+'/templates?dataset_id='+encodeURIComponent(chosenDataset));
    if(seq!==templateSequence)return;
    references=result;
    $('research-template-select').innerHTML=result.map(r=>`<option value="${escapeHTML(r.id)}">${escapeHTML(r.name)}</option>`).join('')||'<option>No reference template fits this universe</option>';
    if(reset&&result.length)setConfig(result[0].config);
  }
  async function selectDataset(identifier,build=false){
    chosenDataset=identifier;$('research-dataset-select').value=identifier;
    await loadTemplates(true);await datasetDetail();if(build)tab('build');
  }
  async function datasetDetail(){
    const d=currentDataset();if(!d)return;
    const rows=await api(base+'/datasets/'+d.id+'/rows?limit=12');
    const q=d.quality;
    $('research-dataset-detail').innerHTML=`<h3>${escapeHTML(d.manifest.name)}</h3><p class="card-note">${escapeHTML(d.id)} · ${d.rows} accepted observations · ${q.invalid_rows} invalid observations</p>${q.warnings.map(warning).join('')}${q.errors.length?jsonDetails('Quarantined source errors',q.errors):''}<div class="table-scroll"><table><thead><tr><th>Instrument</th><th>Rows</th><th>Missing sessions</th><th>Intraday gaps</th></tr></thead><tbody>${q.coverage.map(c=>`<tr><td>${escapeHTML(c.instrument_id)}</td><td>${c.rows}</td><td>${c.missing_sessions}</td><td>${c.intraday_gap_intervals}</td></tr>`).join('')}</tbody></table></div>${jsonDetails('Manifest, license and quality evidence',d)}${jsonDetails('Normalized observation preview',rows)}<a class="button secondary" href="${base}/datasets/${encodeURIComponent(d.id)}/manifest">Export dataset manifest</a>`;
  }
  function renderDatasets(){
    $('research-datasets').innerHTML=datasets.map(d=>`<div class="research-data-card"><div><strong>${escapeHTML(d.manifest.name)}</strong> <span class="research-tag">${escapeHTML(d.status)}</span><span class="research-tag">${escapeHTML(d.manifest.source_kind)}</span><p>${number(d.rows,0)} observations · ${escapeHTML(d.manifest.frequency)} · ${escapeHTML(d.start?.slice(0,10)||'—')} to ${escapeHTML(d.end?.slice(0,10)||'—')}</p><p>${escapeHTML(d.manifest.source)} · version ${escapeHTML(d.id.slice(0,12))}</p></div><button class="button secondary" data-research-dataset="${escapeHTML(d.id)}">Inspect & use</button></div>`).join('')||empty('No historical datasets','Import source data with a manifest, or explicitly create a synthetic fixture to verify the workflow.');
    const previous=$('research-dataset-select').value;
    $('research-dataset-select').innerHTML=datasets.map(d=>`<option value="${escapeHTML(d.id)}">${escapeHTML(d.manifest.name)} · ${escapeHTML(d.status)}</option>`).join('');
    if(datasets.some(d=>d.id===chosenDataset))$('research-dataset-select').value=chosenDataset;
    else if(datasets.some(d=>d.id===previous))$('research-dataset-select').value=previous;
  }
  function renderRuns(){
    $('research-runs').innerHTML=runs.length?`<div class="table-scroll"><table><thead><tr><th>Compare</th><th>Experiment</th><th>Status / progress</th><th>Account size</th><th>Controls</th></tr></thead><tbody>${runs.map(r=>`<tr><td><input type="checkbox" data-research-compare="${r.id}" aria-label="Compare ${escapeHTML(r.name)}" ${compared.has(r.id)?'checked':''} ${r.status!=='COMPLETED'?'disabled':''}></td><td><strong>${escapeHTML(r.name)}</strong><div class="fill-source">${escapeHTML(r.id.slice(0,10))} · ${escapeHTML(localDate(r.created))}</div>${r.error?`<p class="negative">${escapeHTML(r.error)}</p>`:''}</td><td>${escapeHTML(r.status)}<div class="research-progress"><i style="width:${Math.max(0,Math.min(100,r.progress*100))}%"></i></div>${number(r.progress*100,0)}%</td><td>${money(r.config.capital)}</td><td><div class="button-row"><button class="action-small" data-research-run="${r.id}">Inspect</button>${['RUNNING','QUEUED'].includes(r.status)?`<button class="action-small" data-research-control="PAUSE" data-run="${r.id}">Pause</button>`:''}${r.status==='PAUSED'?`<button class="action-small" data-research-control="RESUME" data-run="${r.id}">Resume</button>`:''}${['RUNNING','QUEUED','PAUSED'].includes(r.status)?`<button class="action-small" data-research-control="CANCEL" data-run="${r.id}">Cancel</button>`:''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('No experiments yet','Choose a dataset and reference strategy, inspect its assumptions, then queue a run.');
  }
  async function refresh(){
    if(fetching)return;fetching=true;
    try{
      [datasets,runs]=await Promise.all([api(base+'/datasets'),api(base+'/runs')]);renderDatasets();renderRuns();
      if(!chosenDataset&&datasets.length)await selectDataset(datasets[0].id);
      if(chosenRun&&!$('research-panel-report').classList.contains('hidden'))await loadReport();
    }finally{fetching=false;}
  }
  async function loadReport(force=false){
    if(!chosenRun)return;
    const run=await api(base+'/runs/'+chosenRun);
    $('research-report-name').textContent=run.name;$('research-report-status').textContent=run.status+' · '+number(run.progress*100,0)+'%';
    $('research-export').disabled=$('research-handoff').disabled=run.status!=='COMPLETED';
    if(run.status!=='COMPLETED'){$('research-report').innerHTML=warning(run.error||'The worker is processing this experiment. Progress and checkpoint state refresh automatically.');$('research-events').innerHTML='';return;}
    const key=chosenRun+run.economic_hash;if(key===reportKey&&!force)return;reportKey=key;
    const [r,curve,orders]=await Promise.all([api(base+'/runs/'+chosenRun+'/report'),api(base+'/runs/'+chosenRun+'/curve'),api(base+'/runs/'+chosenRun+'/orders')]);
    const p=r.performance,a=r.account,d=r.diagnostics;
    const cards=[['Final virtual equity',money(a.equity)],['Net total return',percent(p.total_return_pct)],['Maximum drawdown',number(p.max_drawdown_pct)+'%'],['Estimated charges',money(d.estimated_fees)],['Daily Sharpe',p.sharpe==null?'Insufficient sample':number(p.sharpe,3)],['Peak margin',money(d.peak_margin)]];
    $('research-report').innerHTML=`<div class="research-grid">${cards.map(([label,value])=>`<div class="research-metric"><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`).join('')}</div>${r.qualification.blockers.map(warning).join('')}${chart([{name:run.name,curve,capital:run.config.capital}])}<p class="card-note">${d.fills} fill records · ${d.rejected_orders} rejected entries · ${d.bar_ambiguities} ambiguous stop/target bars · Cash ledger ${d.ledger_reconciled?'reconciled':'MISMATCH'} · Attribution ${d.attribution_reconciled?'reconciled':'MISMATCH'}</p><h3>Strategy books</h3><div class="table-scroll"><table><thead><tr><th>Book</th><th>Realised net</th><th>Unrealised</th><th>Charges</th><th>Committed capital</th></tr></thead><tbody>${Object.entries(a.books).map(([key,b])=>`<tr><td>${escapeHTML(key)}</td><td>${money(b.realised_net)}</td><td>${money(b.unrealised)}</td><td>${money(b.fees)}</td><td>${money(b.committed)}</td></tr>`).join('')}</tbody></table></div><h3>Final positions</h3><div class="table-scroll"><table><thead><tr><th>Book / instrument</th><th>Units</th><th>Mark</th><th>Fresh observation</th></tr></thead><tbody>${r.positions.map(pos=>`<tr><td>${escapeHTML(pos.strategy_id)} · ${escapeHTML(pos.instrument_id)}</td><td>${pos.quantity}</td><td>${money(pos.mark)}</td><td>${pos.fresh?'Yes':'No'}</td></tr>`).join('')||'<tr><td colspan="4">Flat account</td></tr>'}</tbody></table></div>${jsonDetails('Combined underlying exposure and Greeks',a.underlying)}${jsonDetails('Chronological evaluation windows',r.windows)}${jsonDetails('Robustness and search diagnostics',{bootstrap:p.bootstrap,search:r.search_diagnostics,notes:p.notes})}${jsonDetails('Order lifecycle records',orders)}${jsonDetails('Pending orders and delivery obligations',{orders:r.pending_orders,obligations:r.obligations,violations:r.violations})}${jsonDetails('Dataset/configuration identities, model versions and assumptions',{dataset:r.dataset_id,parquet_hash:r.dataset_hash,config_hash:r.config_hash,economic_hash:r.economic_hash,models:r.models,assumptions:r.assumptions,config:r.config})}`;
    await loadEvents(true);
  }
  async function loadEvents(first=false){
    if(!chosenRun)return;if(first)eventAfter=0;
    const kind=$('research-event-kind').value;
    const result=await api(base+'/runs/'+chosenRun+'/events?limit=100&after='+eventAfter+(kind?'&kind='+encodeURIComponent(kind):''));
    events=result;
    $('research-events').innerHTML=result.length?`<div class="table-scroll"><table><thead><tr><th>Event</th><th>Replay time</th><th>Book / instrument</th><th>Reason</th><th></th></tr></thead><tbody>${result.map(e=>`<tr><td>${e.sequence} · ${escapeHTML(e.kind)}</td><td>${escapeHTML(localDate(e.time))}</td><td>${escapeHTML(e.strategy_id||'Account')}<div class="fill-source">${escapeHTML(e.instrument_id||'')}</div></td><td>${escapeHTML(e.reason)}</td><td><button class="action-small" data-research-event="${e.sequence}">Evidence</button></td></tr>`).join('')}</tbody></table></div>`:empty('No more matching events','Use First events or change the event filter.');
    if(result.length)eventAfter=result.at(-1).sequence;
  }
  async function queued(result){if(!result)return;toast('Research experiment recorded. Review its status and assumptions.');await refresh();chosenRun=result.id;reportKey='';tab('runs');}

  $('research-tabs').addEventListener('click',e=>{const button=e.target.closest('[data-research-tab]');if(button)tab(button.dataset.researchTab);});
  document.addEventListener('click',e=>{
    const nav=e.target.closest('[data-view="research"]');if(nav)perform(refresh);
    const dataset=e.target.closest('[data-research-dataset]');if(dataset)perform(()=>selectDataset(dataset.dataset.researchDataset,true));
    const run=e.target.closest('[data-research-run]');if(run){chosenRun=run.dataset.researchRun;reportKey='';tab('report');}
    const control=e.target.closest('[data-research-control]');if(control)perform(async()=>{await api(base+'/runs/'+control.dataset.run+'/control',{action:control.dataset.researchControl});await refresh();});
    const event=e.target.closest('[data-research-event]');if(event){const record=events.find(x=>x.sequence===Number(event.dataset.researchEvent));$('research-event-detail').textContent=pretty(record);$('research-event-detail').focus();}
  });
  document.addEventListener('change',e=>{const field=e.target.closest('[data-research-compare]');if(field){if(field.checked)compared.add(field.dataset.researchCompare);else compared.delete(field.dataset.researchCompare);}});
  $('research-synthetic').addEventListener('click',()=>perform(async()=>{const b=$('research-synthetic');b.disabled=true;try{const d=await api(base+'/datasets/synthetic',{});await refresh();await selectDataset(d.id);toast('Synthetic software-test dataset created. It is not historical market evidence.');}finally{b.disabled=false;}}));
  $('research-manifest-example').addEventListener('click',()=>{$('research-manifest').value=pretty({name:'Historical equities',source:'Your licensed data provider',source_kind:'VENDOR',frequency:'DAY',timestamp_semantics:'END',rights_confirmed:false,point_in_time_universe:false,corporate_actions_complete:false,instruments:[{id:'NSE:ASSET',symbol:'ASSET',kind:'EQ',underlying:'NSE:ASSET',lot_size:1,tick_size:.05}],calendar:{timezone:'Asia/Kolkata',holidays:[],session:{open:'09:15:00',close:'15:30:00'}}});});
  $('research-import-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const file=$('research-file').files[0];if(!file)throw new Error('Choose a data file.');if(file.size>24000000)throw new Error('Import files must be at most 24 MB; split larger datasets into versioned batches.');const manifest=JSON.parse($('research-manifest').value);let content,format;if(file.name.toLowerCase().endsWith('.parquet')){format='PARQUET';const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));content=btoa(binary);}else{format='CSV';content=await file.text();}const result=await api(base+'/datasets/import',{manifest,format,content});await refresh();chosenDataset=result.id;await datasetDetail();toast(result.status==='READY'?'Dataset imported and versioned.':'Dataset quarantined; inspect its validation errors.',result.status!=='READY');});});
  $('research-kite-import').addEventListener('click',()=>perform(async()=>{const result=await api(base+'/datasets/kite',JSON.parse($('research-kite-request').value));await refresh();await selectDataset(result.id);toast('Broker candles imported. Inspect historical coverage and contract assumptions.');}));
  $('research-dataset-select').addEventListener('change',()=>perform(()=>selectDataset($('research-dataset-select').value)));
  $('research-template-select').addEventListener('change',()=>{const item=references.find(r=>r.id===$('research-template-select').value);if(item)setConfig(item.config);});
  for(const id of ['research-name','research-capital','research-trade-risk','research-stress-risk','research-slippage'])$(id).addEventListener('change',reflectControls);
  $('research-config').addEventListener('change',()=>{try{const value=config();$('research-name').value=value.name;$('research-capital').value=value.capital;$('research-trade-risk').value=value.risk.trade_pct;$('research-stress-risk').value=value.risk.overnight_stress_pct;$('research-slippage').value=value.risk.slippage_bps;}catch(e){error(e.message);}});
  $('research-add-windows').addEventListener('click',()=>perform(async()=>{const d=currentDataset(),value=config();if(!d)throw new Error('Select a dataset.');const start=Date.parse(d.start),end=Date.parse(d.end),first=start+(end-start)*.6,second=start+(end-start)*.8;value.windows=[{label:'TRAIN',start:new Date(start).toISOString(),end:new Date(first).toISOString()},{label:'VALIDATION',start:new Date(first+1).toISOString(),end:new Date(second).toISOString()},{label:'HOLDOUT',start:new Date(second+1).toISOString(),end:new Date(end).toISOString()}];if(value.strategies.some(s=>s.family==='LINEAR_MODEL')){value.training_end=value.windows[0].end;value.start=value.windows[1].start;}setConfig(value);toast('Chronological windows added. Keep holdout results out of strategy selection.');}));
  $('research-preflight').addEventListener('click',()=>perform(async()=>{const c=await api(base+'/preflight',config());$('research-preflight-result').innerHTML=`<p><strong>${c.runnable?'Runnable with stated assumptions':'Capability check blocked this run'}</strong> · ${escapeHTML(c.fidelity)}</p>${c.blockers.map(warning).join('')}${c.warnings.map(warning).join('')}`;}));
  $('research-run-form').addEventListener('submit',e=>{e.preventDefault();perform(async()=>{const button=$('research-run');button.disabled=true;try{await queued(await api(base+'/runs',config()));}finally{button.disabled=false;}});});
  $('research-sensitivity').addEventListener('click',()=>perform(async()=>{const current=config(),slip=current.risk.slippage_bps;const result=await api(base+'/suites',{base:current,variants:[{'risk.slippage_bps':slip},{'risk.slippage_bps':Math.min(1000,Math.max(1,slip*2))},{'risk.slippage_bps':Math.min(1000,Math.max(2,slip*3)),'execution.spread_bps':Math.min(1000,current.execution.spread_bps*2)}]});result.forEach(r=>compared.add(r.id));await refresh();tab('runs');toast('Three sensitivity runs queued; all variants remain in the experiment history.');}));
  $('research-walk-forward').addEventListener('click',()=>perform(async()=>{const result=await api(base+'/walk-forward',{base:config(),train_observations:Number($('research-wf-train').value),test_observations:Number($('research-wf-test').value),step_observations:Number($('research-wf-test').value),embargo_observations:Number($('research-wf-embargo').value),max_folds:8});await refresh();tab('runs');toast(`${result.length} chronological folds queued.`);}));
  $('research-refresh').addEventListener('click',()=>perform(refresh));
  $('research-event-kind').addEventListener('change',()=>perform(()=>loadEvents(true)));
  $('research-events-first').addEventListener('click',()=>perform(()=>loadEvents(true)));
  $('research-events-next').addEventListener('click',()=>perform(()=>loadEvents(false)));
  $('research-export').addEventListener('click',()=>{if(chosenRun)location.href=base+'/runs/'+chosenRun+'/export';});
  $('research-handoff').addEventListener('click',()=>perform(async()=>{const packet=await api(base+'/runs/'+chosenRun+'/handoff',{});$('research-handoff-result').innerHTML=`<strong>Paper review packet saved · ${escapeHTML(packet.id)}</strong>${packet.blockers.map(warning).join('')}${jsonDetails('Complete review packet',packet)}<p class="card-note">No strategy has been activated and no broker order was submitted.</p>`;toast('Paper review packet saved with its evidence and outstanding requirements.');}));
  $('research-compare').addEventListener('click',()=>perform(async()=>{if(!compared.size||compared.size>10)throw new Error('Select between one and ten completed runs in the queue.');const result=await api(base+'/compare?ids='+[...compared].join(','));$('research-comparison').innerHTML=`<div class="table-scroll"><table><thead><tr><th>Experiment</th><th>Return</th><th>Drawdown</th><th>Sharpe</th><th>Charges</th><th>Review blockers</th></tr></thead><tbody>${result.map(r=>`<tr><td>${escapeHTML(r.run.name)}</td><td>${percent(r.report.performance.total_return_pct)}</td><td>${number(r.report.performance.max_drawdown_pct)}%</td><td>${r.report.performance.sharpe==null?'—':number(r.report.performance.sharpe,3)}</td><td>${money(r.report.diagnostics.estimated_fees)}</td><td>${r.report.qualification.blockers.length}</td></tr>`).join('')}</tbody></table></div>${chart(result.map(r=>({name:r.run.name,curve:r.curve,capital:r.run.config.capital})),true)}${result.map(r=>jsonDetails(r.run.name+' · assumptions and research diagnostics',{capability:r.report.capability,search:r.report.search_diagnostics,qualification:r.report.qualification})).join('')}`;tab('compare');}));
  async function loadCapabilities(){const c=await api(base+'/capabilities');$('research-capabilities').innerHTML=Object.entries(c.families).map(([name,description])=>`<p><strong>${escapeHTML(name.replaceAll('_',' '))}</strong> · ${escapeHTML(description)}</p>`).join('')+c.limitations.map(warning).join('');}
  perform(loadCapabilities);
  if(location.hash==='#research')perform(refresh);
  setInterval(()=>{if(!$('view-research').classList.contains('hidden'))perform(refresh);},4000);
})();
