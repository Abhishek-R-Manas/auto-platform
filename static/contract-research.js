/* Frozen paper experiments; every action remains separate from broker authority. */
(() => {
'use strict';
const root=$('contract-research-root'),esc=escapeHTML,base='/api/contract-research';
let loaded=false,metadata=null,definitions=[],datasets=[],protocols=[],selected=null,details=null,busy=false,lastFreeze=null,lastCapture=null,preferred=null,lastStart=null,loadSequence=0;
const labels={BASELINE:'Baseline',REPRODUCE:'Repeat identical inputs',COST_STRESS:'Cost & latency stress'};
const risks={trade_pct:'Per-trade risk · %',open_pct:'Aggregate risk · %',daily_pct:'Daily loss · %',weekly_pct:'Weekly loss · %',drawdown_pct:'Drawdown · %',margin_pct:'Gross margin · %',slippage_bps:'Slippage · bps',overnight_stress_pct:'Stress budget · %',gross_exposure_pct:'Gross exposure · %',underlying_exposure_pct:'Underlying exposure · %',cash_buffer_pct:'Cash buffer · %'};
function input(label,id,value,extra=''){return '<label>'+esc(label)+'<input id="cr-'+id+'" value="'+esc(value)+'" '+extra+'></label>';}
function pick(values,selected){return values.map(v=>'<option value="'+esc(v[0])+'" '+(v[0]===selected?'selected':'')+'>'+esc(v[1])+'</option>').join('');}
const local=v=>new Date(v).toLocaleString('sv-SE',{timeZone:'Asia/Kolkata'}).replace(' ','T').slice(0,19);
function showResearch(){
 $('contract-validation-root').classList.add('hidden');$('ct-tab-validation').classList.remove('active');
 $('contracts-root').classList.add('hidden');root.classList.remove('hidden');
 $('ct-tab-research').classList.add('active');$('ct-tab-definition').classList.remove('active');
 load();
}
function showDefinition(){
 $('contract-validation-root').classList.add('hidden');$('ct-tab-validation').classList.remove('active');
 root.classList.add('hidden');$('contracts-root').classList.remove('hidden');
 $('ct-tab-research').classList.remove('active');$('ct-tab-definition').classList.add('active');
}
$('ct-tab-research').addEventListener('click',showResearch);
$('ct-tab-definition').addEventListener('click',showDefinition);
$('ct-tab-validation').addEventListener('click',()=>{
 root.classList.add('hidden');$('contracts-root').classList.add('hidden');$('contract-validation-root').classList.remove('hidden');
 for(const id of ['definition','research','validation'])$('ct-tab-'+id).classList.toggle('active',id==='validation');
 window.ArthaValidation.mount($('contract-validation-root'),()=>api(base+'/validation'),()=>rawRequest(base+'/validation/export'));
});
window.addEventListener('artha:contract-saved',event=>{preferred=event.detail.id;if(loaded)refreshChoices();});
window.addEventListener('artha:navigate',event=>{if(event.detail.view==='contracts'&&!root.classList.contains('hidden'))load();});
async function load(){
 if(loaded){await refreshChoices();return;}
 root.textContent='Loading research workspace…';
 try{
  metadata=await api(base+'/catalog');
  render();loaded=true;await refreshChoices();
 }catch(error){root.textContent=error.message;}
}
function render(){
 root.innerHTML='<div class="workspace-title"><div><span class="overline">PIN THE RULES. KEEP THE EVIDENCE.</span><h1>Research & evidence</h1><p>Evaluate an exact saved revision and retain every attempt.</p></div><span class="ct-status">Simulated execution · private research</span></div>'+
 '<p class="ct-notice">A completed simulation is research evidence. Data rights, source authenticity, independent review and investment edge remain separate questions.</p>'+
 '<div class="cr-layout"><div><article class="surface ct-card"><h2>1. Freeze an evaluation protocol</h2><form id="cr-freeze">'+
 '<div class="ct-grid"><label>Saved strategy revision<select id="cr-contract" required><option value="">Select a saved definition</option></select></label><label>Immutable quote dataset<select id="cr-dataset" required><option value="">Select a dataset</option></select></label></div><p id="cr-definition-note" class="ct-note">Save a definition first, or load an explicitly fictional example.</p>'+
 '<div class="ct-grid">'+input('Virtual research capital · INR','capital',metadata.capital_paise/100,'type="number" min="100" step=".01" required')+input('Observation cutoff · IST','cutoff','','type="datetime-local" step="1" required')+input('Entry window · seconds','window',10,'type="number" min="1" max="300" required')+input('Execution latency · seconds','latency',0,'type="number" min="0" max="60" required')+input('Maximum attempts · failures count','budget',6,'type="number" min="2" max="20" required')+input('Organization label · local context','organization','LOCAL_WORKSPACE','maxlength="80" required')+'</div>'+
 '<details class="cr-policy"><summary>Research risk policy and external references</summary><div class="ct-grid">'+Object.keys(risks).map(key=>input(risks[key],'risk-'+key,metadata.risk_defaults[key],'type="number" min="0" max="5000" step=".01" required')).join('')+'</div><label>External references · one per line<textarea id="cr-external" rows="2" maxlength="5000"></textarea></label><p class="ct-note">External results remain unverified references. These virtual limits apply only to this isolated experiment.</p></details>'+
 '<label class="cr-check"><input id="cr-ack" type="checkbox"> I acknowledge unqualified research with incomplete customer, source or data evidence.</label>'+
 '<div class="ct-actions"><button class="button primary" type="submit" id="cr-freeze-button">Freeze protocol</button><button class="button secondary" type="button" id="cr-example">Load fictional example</button><button class="text-button" type="button" id="cr-open-definition">Open definition</button></div><p id="cr-message" class="ct-note" role="status"></p></form></article>'+
 '<article class="surface ct-card cr-spaced"><h2>2. Run the frozen experiment</h2><label>Evaluation protocol<select id="cr-protocol"><option value="">Select a frozen protocol</option></select></label><p id="cr-protocol-note" class="ct-note">Strategy version, dataset, build, policy and cutoff are fixed before execution.</p><div id="cr-binding"></div>'+
 '<div class="ct-actions cr-spaced"><button class="button primary" id="cr-baseline" type="button" disabled>Run baseline</button><button class="button secondary" id="cr-repeat" type="button" disabled>Repeat identical inputs</button><button class="button secondary" id="cr-stress" type="button" disabled>Run cost stress</button></div>'+
 '<p class="ct-note">Cost stress doubles declared fee rates, increases slippage and adds one second of execution latency. Failed and cancelled attempts use the same frozen budget.</p><div id="cr-trials" class="ct-table-scroll"></div></article></div>'+
 '<aside><article class="surface ct-card"><h2>3. Capture the evidence</h2><p class="ct-note">Finish or cancel active attempts, then capture an immutable packet. Earlier packets stay historical when new attempts are added.</p><button class="button primary cr-spaced" id="cr-capture" type="button" disabled>Capture evidence packet</button><div id="cr-packets"></div><div id="cr-findings"></div>'+
 '<label class="cr-check"><input id="cr-trace" type="checkbox"> Include quote-bearing economic traces; my source agreement permits this private export.</label><p class="ct-note">Summary exports omit these traces. Source authenticity is not certified by a hash check.</p></article>'+
 '<article class="surface ct-card cr-spaced"><h2>Verify an exported packet</h2><p class="ct-note">Checks hashes, pinned identities and included economic traces. With a protocol selected, its identities must match.</p><button class="button secondary" type="button" id="cr-verify">Choose packet JSON</button><input id="cr-file" class="hidden" type="file" accept=".json,application/json"><div id="cr-verification" role="status"></div></article></aside></div>';
 $('cr-freeze').addEventListener('submit',event=>{event.preventDefault();freeze();});
 $('cr-example').addEventListener('click',loadExample);
 $('cr-contract').addEventListener('change',chooseContract);
 $('cr-protocol').addEventListener('change',async()=>{selected=$('cr-protocol').value||null;await refreshProtocol();});
 for(const [id,kind] of [['baseline','BASELINE'],['repeat','REPRODUCE'],['stress','COST_STRESS']])$('cr-'+id).addEventListener('click',()=>start(kind));
 $('cr-capture').addEventListener('click',capture);
 $('cr-verify').addEventListener('click',()=>$('cr-file').click());
 $('cr-file').addEventListener('change',verify);
 $('cr-open-definition').addEventListener('click',()=>{if($('cr-contract').value){window.dispatchEvent(new CustomEvent('artha:open-contract',{detail:{id:$('cr-contract').value}}));showDefinition();}});
}
async function refreshChoices(){
 if(!loaded)return;
 try{
  const [ds,data,ps]=await Promise.all([api('/api/contracts'),api('/api/research/datasets'),api(base+'/protocols')]);
  definitions=ds;datasets=data;protocols=ps;
  const old=$('cr-contract').value,wanted=preferred||old;preferred=null;
  $('cr-contract').innerHTML='<option value="">Select a saved definition</option>'+pick(ds.map(v=>[v.id,v.name+' · '+v.id.slice(0,10)]),wanted);
  const dataset=$('cr-dataset').value;
  $('cr-dataset').innerHTML='<option value="">Select a quote dataset</option>'+pick(data.map(v=>[v.id,v.manifest.name+' · '+v.manifest.source_kind]),dataset);
  $('cr-protocol').innerHTML='<option value="">Select a frozen protocol</option>'+pick(ps.map(v=>[v.id,v.core.definition.name+' · '+v.id.slice(0,10)]),selected);
  if(wanted&&wanted!==old)await chooseContract();
  if(selected)await refreshProtocol();
 }catch(error){toast(error.message,true);}
}
async function chooseContract(){
 const id=$('cr-contract').value;if(!id)return;
 try{
  const record=await api('/api/contracts/'+id),d=record.core.definition;
  $('cr-definition-note').textContent=d.name+' · '+d.strategy.structure.replaceAll('_',' ')+' · immutable revision '+id.slice(0,12);
  if(record.core.dataset_id)$('cr-dataset').value=record.core.dataset_id;
  $('cr-cutoff').value=local(new Date(new Date(d.rules.exit_at).getTime()+10000).toISOString());
 }catch(error){toast(error.message,true);}
}
function requestBody(){
 const risk={};for(const key of Object.keys(risks))risk[key]=Number($('cr-risk-'+key).value);
 return {contract_id:$('cr-contract').value,dataset_id:$('cr-dataset').value,capital_paise:Math.round(Number($('cr-capital').value)*100),
  organization_ref:$('cr-organization').value,risk_policy:risk,observation_cutoff:$('cr-cutoff').value+'+05:30',
  entry_window_seconds:Number($('cr-window').value),latency_seconds:Number($('cr-latency').value),max_trials:Number($('cr-budget').value),
  allow_unverified_data:$('cr-ack').checked,external_references:$('cr-external').value.split('\n').map(s=>s.trim()).filter(Boolean)};
}
async function freeze(){
 if(busy)return;busy=true;$('cr-freeze-button').disabled=true;
 try{
  const body=requestBody(),signature=JSON.stringify(body);
  if(!lastFreeze||lastFreeze.signature!==signature)lastFreeze={signature,id:crypto.randomUUID()};
  const protocol=await api(base+'/protocols',{...body,request_id:lastFreeze.id});
  selected=protocol.id;$('cr-message').textContent='Protocol frozen: '+protocol.id.slice(0,12);await refreshChoices();toast('Evaluation protocol frozen. No trial has been started.');
 }catch(error){$('cr-message').textContent=error.message;toast(error.message,true);}
 finally{busy=false;$('cr-freeze-button').disabled=false;setButtons();}
}
async function loadExample(){
 if(busy)return;busy=true;$('cr-example').disabled=true;
 try{
  const value=await api(base+'/example',{request_id:crypto.randomUUID()});preferred=value.contract.id;
  await refreshChoices();$('cr-dataset').value=value.dataset_id;
  $('cr-message').textContent='Fictional example loaded. Review its virtual capital, risk policy and timing before freezing.';
 }catch(error){toast(error.message,true);}
 finally{busy=false;$('cr-example').disabled=false;setButtons();}
}
async function start(kind){
 if(busy||!selected)return;busy=true;setButtons(true);
 try{
  const signature=JSON.stringify([selected,kind,details?.trials.map(t=>t.id)]);
  if(!lastStart||lastStart.signature!==signature)lastStart={signature,id:crypto.randomUUID()};
  await api(base+'/trials',{request_id:lastStart.id,protocol_id:selected,kind});await refreshProtocol();
 }
 catch(error){toast(error.message,true);}
 finally{busy=false;setButtons(false);}
}
function setButtons(force=false){
 if(!loaded)return;
 const active=details?.trials.some(t=>['QUEUED','RUNNING','PAUSED','RESERVED'].includes(t.status)),budget=details&&details.trials.length>=details.protocol.core.max_trials;
 const blocked=force||busy||!details||!details.current_build||active||budget;
 $('cr-baseline').disabled=!!blocked;
 const baseline=details?.trials.some(t=>t.core.kind==='BASELINE'&&t.status==='COMPLETED');
 $('cr-repeat').disabled=!!(blocked||!baseline);$('cr-stress').disabled=!!(blocked||!baseline);
 $('cr-capture').disabled=!!(force||busy||!details||active||!details.trials.length||!details.current_build);
}
async function refreshProtocol(){
 const sequence=++loadSequence,requested=selected;
 if(!selected){details=null;$('cr-trials').replaceChildren();$('cr-binding').replaceChildren();$('cr-packets').replaceChildren();$('cr-findings').replaceChildren();setButtons();return;}
 try{
  const result=await api(base+'/protocols/'+requested);if(sequence!==loadSequence||requested!==selected)return;details=result;
  const p=result.protocol.core;
  $('cr-protocol-note').textContent=(result.current_build?'Pinned to the current build. ':'Preserved older build; freeze a new protocol to continue. ')+result.trials.length+' / '+p.max_trials+' attempts used.';
  $('cr-binding').innerHTML='<div class="cr-binding"><span>Clock <b>'+esc(p.clock_mode.replaceAll('_',' '))+'</b></span><span>Virtual capital <b>'+esc(money(p.run_config.capital))+'</b></span><span>Dataset <b>'+esc(p.dataset_snapshot.id.slice(0,12))+'</b></span><span>Cutoff <b>'+esc(new Date(p.observation_cutoff).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}))+' IST</b></span></div><details><summary>Inspect frozen interpretation</summary><pre>'+esc(JSON.stringify({strategy:p.run_config.strategies[0],execution:p.run_config.execution,policy:p.run_config.contract_protocol},null,2))+'</pre></details>';
  $('cr-trials').innerHTML=result.trials.length?'<table class="cr-trials"><thead><tr><th>Attempt</th><th>Status</th><th>Net P&L</th><th>Costs</th><th>Fills</th><th></th></tr></thead><tbody>'+result.trials.map(t=>'<tr><td>'+esc(labels[t.core.kind])+'<small>'+esc(t.id.slice(0,10))+'</small></td><td>'+esc(t.status)+(t.error?'<small>'+esc(t.error)+'</small>':'')+'</td><td>'+esc(t.summary?money(t.summary.net_pnl):'—')+'</td><td>'+esc(t.summary?money(t.summary.fees):'—')+'</td><td>'+esc(t.summary?.fills??'—')+'</td><td>'+(['QUEUED','RUNNING','PAUSED'].includes(t.status)?'<button type="button" class="text-button" data-cr-control="'+esc(t.id)+'" data-control="'+(t.status==='PAUSED'?'RESUME':'CANCEL')+'">'+(t.status==='PAUSED'?'Resume':'Cancel')+'</button>':'')+(t.status==='RESERVED'?'<button type="button" class="text-button" data-cr-recover="'+esc(t.id)+'">Recover same attempt</button>':'')+'</td></tr>').join('')+'</tbody></table>':'<p class="ct-note">Run a baseline to begin. The virtual financial account is isolated from your trading accounts.</p>';
  root.querySelectorAll('[data-cr-control]').forEach(b=>b.addEventListener('click',async()=>{try{await api(base+'/trials/'+b.dataset.crControl+'/control',{action:b.dataset.control});await refreshProtocol();}catch(error){toast(error.message,true);}}));
  root.querySelectorAll('[data-cr-recover]').forEach(b=>b.addEventListener('click',async()=>{try{await api(base+'/trials/'+b.dataset.crRecover+'/recover',{});await refreshProtocol();}catch(error){toast(error.message,true);}}));
  $('cr-packets').innerHTML=result.packets.map(packet=>'<div class="cr-packet"><strong>'+esc(packet.historical?'Historical packet':'Captured packet')+'</strong><small>'+esc(packet.id.slice(0,12))+'</small><div class="ct-actions"><button class="button secondary" type="button" data-cr-inspect="'+esc(packet.id)+'">Inspect</button><button class="button secondary" type="button" data-cr-export="'+esc(packet.id)+'" data-historical="'+packet.historical+'">Export JSON</button></div></div>').join('');
  root.querySelectorAll('[data-cr-inspect]').forEach(b=>b.addEventListener('click',()=>inspect(b.dataset.crInspect)));
  root.querySelectorAll('[data-cr-export]').forEach(b=>b.addEventListener('click',()=>exportPacket(b.dataset.crExport,b.dataset.historical==='true')));
  setButtons(busy);
 }catch(error){toast(error.message,true);}
}
async function capture(){
 if(busy||!selected)return;busy=true;setButtons(true);
 try{
  const signature=JSON.stringify([selected,details.trials.map(t=>t.id)]);
  if(!lastCapture||lastCapture.signature!==signature)lastCapture={signature,id:crypto.randomUUID()};
  const packet=await api(base+'/packets',{request_id:lastCapture.id,protocol_id:selected});
  await refreshProtocol();await inspect(packet.id);toast('Evidence captured. New attempts will create a later evidence history.');
 }catch(error){toast(error.message,true);}
 finally{busy=false;setButtons();}
}
async function inspect(id){
 try{const packet=await api(base+'/packets/'+id);$('cr-findings').innerHTML='<h3>Evidence findings</h3>'+Object.entries(packet.core.findings).map(([key,value])=>'<div class="cr-finding"><strong>'+esc(key.replaceAll('_',' '))+'</strong><span class="ct-status '+(value.status==='PASS'?'ct-pass':value.status==='FAIL'?'ct-blocked':'')+'">'+esc(value.status)+'</span><p class="ct-note">'+esc(value.note)+'</p></div>').join('');}
 catch(error){toast(error.message,true);}
}
async function rawRequest(path,body){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
 try{
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-Artha-Client':'paper-workspace'},body,signal:controller.signal});
  if(!response.ok){const data=await response.json();throw new Error(typeof data.detail==='string'?data.detail:'Packet request failed');}
  return await response.blob();
 }finally{clearTimeout(timer);}
}
async function exportPacket(id,historical){
 try{
  const trace=$('cr-trace').checked;
  const packet=await rawRequest(base+'/packets/'+id+'/export?include_trace='+trace+'&rights_acknowledged='+trace+'&allow_historical='+historical);
  const url=URL.createObjectURL(packet),a=document.createElement('a');a.href=url;a.download='contract-evidence-'+id.slice(0,12)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(error){toast(error.message,true);}
}
async function verify(event){
 const file=event.target.files[0];if(!file)return;
 try{
  if(file.size>metadata.max_packet_bytes)throw new Error('Packet exceeds the 16 MB verification limit.');
  const original=await file.text();JSON.parse(original);
  const identities=details?{expected_protocol:details.protocol.id,expected_build:details.protocol.core.build.code_hash,expected_dataset:details.protocol.core.dataset_snapshot.id}:{};
  // Preserve JSON numeric types in the downloaded evidence; browser Number reserialization loses 2.0 versus 2.
  const suffix=JSON.stringify(identities).slice(1,-1);
  const response=await rawRequest(base+'/verify','{"packet":'+original+(suffix?','+suffix:'')+'}');
  const result=JSON.parse(await response.text());
  $('cr-verification').textContent=(result.integrity_valid?'Integrity valid. ':'Integrity check failed. ')+(result.trace_valid?'Included economic traces match. ':'Economic traces not verified. ')+(result.repeat_results_match?'Baseline and repeat match. ':'')+result.reason;
 }catch(error){$('cr-verification').textContent=error.message;}
 finally{event.target.value='';}
}
setInterval(()=>{if(loaded&&!root.classList.contains('hidden')&&activeView==='contracts'&&!document.hidden&&selected&&!busy&&details?.trials.some(t=>['QUEUED','RUNNING'].includes(t.status)))refreshProtocol();},1200);
})();
