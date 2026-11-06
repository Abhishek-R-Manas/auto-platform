/* Separate MVP-1 definition workspace. It never submits research jobs or orders. */
(() => {
'use strict';
const root=$('contracts-root'),esc=escapeHTML,base='/api/contracts';
let examples={},datasets=[],current=null,parentId=null,saved=null,editVersion=0,busy=false,pending=null,loaded=false;
const displayNames={CALL_VERTICAL:'Call vertical',PUT_VERTICAL:'Put vertical',IRON_CONDOR:'Iron condor'};
const local=v=>new Date(v).toLocaleString('sv-SE',{timeZone:'Asia/Kolkata'}).replace(' ','T').slice(0,16);
const instant=v=>v+(v.length===16?':00':'')+'+05:30';
const amount=v=>Math.round(Number(v)*100);
function field(label,id,value,type='text',extra=''){
 return '<label>'+esc(label)+'<input id="ct-'+id+'" type="'+type+'" value="'+esc(value??'')+'" '+extra+'></label>';
}
function options(values,value){return values.map(v=>'<option value="'+esc(v[0])+'" '+(v[0]===value?'selected':'')+'>'+esc(v[1])+'</option>').join('');}
function select(label,id,values,value){return '<label>'+esc(label)+'<select id="ct-'+id+'">'+options(values,value)+'</select></label>';}
function section(title,body,note=''){return '<article class="surface ct-card"><h2>'+esc(title)+'</h2>'+body+(note?'<p class="ct-note">'+esc(note)+'</p>':'')+'</article>';}
function dirty(){
 editVersion++;pending=null;saved=null;
 $('ct-export-saved').disabled=true;
 $('ct-result').replaceChildren();
 $('ct-result-state').textContent='Changed — check this definition';
 $('ct-result-state').className='ct-status';
}
function render(definition,datasetId=null,record=null){
 current=structuredClone(definition);parentId=record?.id||null;saved=record;pending=null;editVersion++;
 const d=current,s=d.strategy,r=d.rules,c=d.customer,data=d.data;
 root.innerHTML='<div class="workspace-title"><div><span class="overline">RESEARCH STARTS WITH A CLEAR DEFINITION</span><h1>Strategy Contracts</h1><p>Specify the rules, inspect missing evidence and keep each revision.</p></div><span class="ct-status">Definitions only · no execution</span></div>'+
 '<div class="ct-toolbar"><label>Illustrative examples<select id="ct-example"><option value="">Choose an example</option>'+options(Object.keys(examples).map(k=>[k,displayNames[k]]),'')+'</select></label><button class="button secondary" id="ct-new" type="button">New definition</button><button class="button secondary" id="ct-import" type="button">Import JSON</button><input type="file" id="ct-file" accept=".json,application/json" hidden><button class="button secondary" id="ct-export" type="button">Export definition</button><button class="button secondary" id="ct-export-saved" type="button" '+(saved?'':'disabled')+'>Export saved revision</button></div>'+
 '<p class="ct-notice">Examples use fictional prices, dated illustrative contract terms and no market connection. Review every field against your intended study.</p>'+
 '<div class="ct-layout"><form id="ct-form" class="ct-form">'+
 section('Research context','<div class="ct-grid">'+field('Definition name','name',d.name,'text','required maxlength="200"')+select('Team workflow','role',[['OPTIONS_RESEARCH','Options research'],['RA_ALGO_PROVIDER','RA / algo provider'],['OPTIONS_DESK','Options desk']],c.team_role)+'</div><label>Research question<textarea id="ct-question" rows="2" minlength="10" maxlength="2000" required>'+esc(c.research_question)+'</textarea></label><div class="ct-grid">'+field('Budget owner · optional','owner',c.budget_owner,'text','maxlength="200"')+field('Current review time · minutes','baseline',c.baseline_review_minutes,'number','min="1" max="100000"')+select('Customer evidence','customer-state',[['PROVISIONAL','Provisional assumption'],['REVIEWED','Reviewed · owner record']],c.validation_state)+field('Interview / workflow reference','customer-ref',c.evidence_reference,'text','maxlength="1000"')+'</div>','Customer review requires a budget owner and an actual evidence reference. These fields do not verify professional licensing.')+
 section('Trading rules','<div class="ct-grid">'+select('Supported structure','structure',Object.keys(displayNames).map(k=>[k,displayNames[k]]),s.structure)+field('Underlying reference ID','reference',s.reference_id,'text','required maxlength="100"')+field('Entry · IST','entry',local(r.entry_at),'datetime-local','required')+field('Exit · IST','exit',local(r.exit_at),'datetime-local','required')+field('Lots per leg','lots',s.lots,'number','required min="1" max="1000" step="1"')+field('Dated units per lot','lot-size',s.lot_size,'number','required min="1" max="100000" step="1"')+field('Premium tick · INR','tick',s.tick_size_paise/100,'number','required min=".01" step=".01"')+field('Terms effective · IST','terms',local(s.terms_effective_at),'datetime-local','required')+'</div><p class="ct-rule-summary">NIFTY · fixed contracts · one scheduled entry · same-session exit · no rolls, adjustments or reentry.</p>','Hedges-first and unwind-on-failure behavior is a requirement for the next-stage execution adapter. Saving a definition does not implement or run it.')+
 section('Exact option legs','<div class="ct-table-scroll"><table class="ct-legs"><thead><tr><th>Stable instrument ID</th><th>Side</th><th>Type</th><th>Strike · INR</th><th>Illustrative premium · INR</th><th>Expiry · IST</th></tr></thead><tbody>'+s.legs.map((l,i)=>'<tr data-contract-leg="'+i+'"><td><input aria-label="Leg '+(i+1)+' instrument" data-leg-field="instrument_id" value="'+esc(l.instrument_id)+'" maxlength="100" required></td><td><select aria-label="Leg '+(i+1)+' side" data-leg-field="side">'+options([['BUY','Buy'],['SELL','Sell']],l.side)+'</select></td><td><select aria-label="Leg '+(i+1)+' type" data-leg-field="kind">'+options([['CE','Call'],['PE','Put']],l.kind)+'</select></td><td><input aria-label="Leg '+(i+1)+' strike" data-leg-field="strike_paise" type="number" min=".01" step=".01" value="'+l.strike_paise/100+'" required></td><td><input aria-label="Leg '+(i+1)+' premium" data-leg-field="premium_paise" type="number" min=".01" step=".01" value="'+l.premium_paise/100+'" required></td><td><input aria-label="Leg '+(i+1)+' expiry" data-leg-field="expiry" type="datetime-local" value="'+esc(local(l.expiry))+'" required></td></tr>').join('')+'</tbody></table></div>','Each leg uses the same number of units. Contract identities must match the selected historical dataset exactly.')+
 section('Research loss budget','<div class="ct-grid">'+field('Maximum contractual expiry loss · INR','loss',d.risk.max_expiry_loss_paise/100,'number','required min=".01" step=".01"')+field('Estimated total costs · INR','costs',d.risk.estimated_costs_paise/100,'number','required min="0" step=".01"')+'</div>','The expiry bound assumes all legs execute at the declared premiums. Partial fills, gaps, exits and margin require separate execution validation.')+
 section('Data requirements','<label>Immutable research dataset<select id="ct-dataset"><option value="">Not selected — define requirements first</option>'+datasets.map(v=>'<option value="'+esc(v.id)+'" '+(v.id===datasetId?'selected':'')+'>'+esc(v.manifest.name+' · '+v.manifest.frequency+' · '+v.manifest.source_kind)+'</option>').join('')+'</select></label><div class="ct-grid">'+select('Required fidelity','fidelity',[['QUOTE','Bid / ask and depth'],['MINUTE','Minute bars · assumptions required']],data.required_fidelity)+select('Proposed provider','provider',[['UNSELECTED','Not selected'],['KITE_PERSONAL','Personal Kite connection'],['LICENSED_VENDOR','Vendor · rights need review']],data.provider)+select('Intended use','use',[['LOCAL_RESEARCH','Local research'],['CLIENT_RESEARCH','Client research'],['REDISTRIBUTION','Redistribution']],data.intended_use)+field('Maximum observation age · seconds','age',data.max_quote_age_seconds,'number','required min="1" max="900"')+field('Minimum displayed depth · lots','depth',data.min_depth_lots,'number','required min="1" max="1000"')+field('Maximum quoted spread · bps','spread',data.max_spread_bps,'number','required min="1" max="10000"')+'</div>'+field('Source and coverage note','source-note',data.source_note,'text','maxlength="1000"')+'<div class="ct-grid">'+select('Rights declaration','rights',[['UNKNOWN','Unknown'],['OWNER_ASSERTED','Owner assertion']],data.rights_status)+field('Agreement / permitted-use reference','rights-ref',data.rights_reference,'text','maxlength="1000"')+field('Calendar review reference','calendar-ref',data.calendar_reference,'text','maxlength="1000"')+field('Dated fee review reference','fees-ref',data.fees_reference,'text','maxlength="1000"')+'</div><div class="ct-checks"><label><input type="checkbox" id="ct-calendar" '+(data.calendar_reviewed?'checked':'')+'> Calendar reviewed against the cited source</label><label><input type="checkbox" id="ct-fees" '+(data.fees_reviewed?'checked':'')+'> Fee schedule reviewed against the cited source</label></div>','Source selection and owner declarations do not independently verify data rights or receipt-time coverage. No market or provider request is made here.')+
 '<div class="ct-actions"><button class="button secondary" type="submit" id="ct-check">Check definition</button><button class="button primary" type="button" id="ct-save">Save revision</button><span id="ct-save-state" role="status">'+(record?'Saved revision '+esc(record.id.slice(0,12)):'Unsaved definition')+'</span></div></form>'+
 '<aside class="ct-review"><article class="surface ct-card"><h2>Contract check</h2><p id="ct-result-state" class="ct-status">Check this definition</p><div id="ct-result" aria-live="polite"></div></article><article class="surface ct-card"><h2>Saved revisions</h2><div id="ct-saved-list">Loading revisions…</div></article></aside></div>';
 $('ct-form').addEventListener('input',dirty);
 $('ct-form').addEventListener('change',event=>{if(event.target.id==='ct-structure'){const next=read();next.strategy.legs=structuredClone(examples[next.strategy.structure].strategy.legs);const previousParent=parentId;render(next,$('ct-dataset').value);parentId=previousParent;}});
 $('ct-form').addEventListener('submit',event=>{event.preventDefault();perform(false);});
 $('ct-save').addEventListener('click',()=>{if($('ct-form').reportValidity())perform(true);});
 $('ct-example').addEventListener('change',event=>{if(event.target.value)render(examples[event.target.value]);});
 $('ct-new').addEventListener('click',()=>render(examples.CALL_VERTICAL));
 $('ct-import').addEventListener('click',()=>$('ct-file').click());
 $('ct-file').addEventListener('change',importFile);
 $('ct-export').addEventListener('click',()=>{if($('ct-form').reportValidity())download({definition:read(),dataset_id:$('ct-dataset').value||null},'strategy-definition.json');});
 $('ct-export-saved').addEventListener('click',()=>{if(saved)window.location.href=base+'/'+encodeURIComponent(saved.id)+'/export';});
 refreshSaved();
 if(record)show(record.core.assessment);
}
function read(){
 const d=structuredClone(current),s=d.strategy,r=d.rules,c=d.customer,data=d.data;
 d.name=$('ct-name').value;c.team_role=$('ct-role').value;c.research_question=$('ct-question').value;c.budget_owner=$('ct-owner').value;c.baseline_review_minutes=$('ct-baseline').value?Number($('ct-baseline').value):null;c.validation_state=$('ct-customer-state').value;c.evidence_reference=$('ct-customer-ref').value;
 s.structure=$('ct-structure').value;s.reference_id=$('ct-reference').value;s.lots=Number($('ct-lots').value);s.lot_size=Number($('ct-lot-size').value);s.tick_size_paise=amount($('ct-tick').value);s.terms_effective_at=instant($('ct-terms').value);
 r.entry_at=instant($('ct-entry').value);r.exit_at=instant($('ct-exit').value);
 s.legs=[...root.querySelectorAll('[data-contract-leg]')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-leg-field]')].map(el=>[el.dataset.legField,el.dataset.legField==='expiry'?instant(el.value):el.dataset.legField.endsWith('_paise')?amount(el.value):el.value])));
 d.risk.max_expiry_loss_paise=amount($('ct-loss').value);d.risk.estimated_costs_paise=amount($('ct-costs').value);
 Object.assign(data,{required_fidelity:$('ct-fidelity').value,provider:$('ct-provider').value,intended_use:$('ct-use').value,max_quote_age_seconds:Number($('ct-age').value),min_depth_lots:Number($('ct-depth').value),max_spread_bps:Number($('ct-spread').value),source_note:$('ct-source-note').value,rights_status:$('ct-rights').value,rights_reference:$('ct-rights-ref').value,calendar_reviewed:$('ct-calendar').checked,calendar_reference:$('ct-calendar-ref').value,fees_reviewed:$('ct-fees').checked,fees_reference:$('ct-fees-ref').value});
 return d;
}
function show(report){
 const node=$('ct-result-state');
 node.textContent=report.definition_ready?'Definition complete':'Definition needs changes';node.className='ct-status '+(report.definition_ready?'ct-pass':'ct-blocked');
 const p=report.payoff;
 $('ct-result').innerHTML=(p?'<div class="ct-payoff"><div><small>Illustrative expiry loss</small><strong>'+esc(money(p.max_loss_paise/100))+'</strong></div><div><small>Illustrative expiry profit</small><strong>'+esc(money(p.max_profit_paise/100))+'</strong></div></div><p class="ct-note">'+esc(p.basis)+'</p>':'')+
 '<div class="ct-dimensions"><span>Definition <b>'+ (report.definition_ready?'Defined':'Blocked')+'</b></span><span>Data evidence <b>'+(report.data_ready?'Compatible':'Incomplete')+'</b></span><span>Customer evidence <b>'+(report.customer_validated?'Owner record':'Pending')+'</b></span></div>'+
 '<details open><summary>Findings and missing evidence</summary><ul class="ct-findings">'+report.findings.filter(f=>f.status!=='PASS').map(f=>'<li class="'+(f.status==='BLOCKED'?'ct-blocked':'')+'"><strong>'+esc(f.code.replaceAll('_',' '))+'</strong><small>'+esc(f.category+' · '+f.status)+'</small><p>'+esc(f.message)+'</p></li>').join('')+'</ul></details>'+
 '<details><summary>Exact interpretation</summary><pre>'+esc(JSON.stringify(report.intent,null,2))+'</pre></details><p class="ct-note">Definition check only. Execution adapter, backtest dispatch, professional approval and commercial data acceptance belong to subsequent work.</p>';
}
async function perform(save){
 if(busy)return;busy=true;const version=editVersion;
 $('ct-check').disabled=true;$('ct-save').disabled=true;
 try{
  const body={definition:read(),dataset_id:$('ct-dataset').value||null};
  if(save){
   body.parent_id=parentId;const signature=JSON.stringify(body);
   if(!pending||pending.signature!==signature)pending={signature,id:crypto.randomUUID()};
   const record=await api(base,{...body,request_id:pending.id});
   await refreshSaved();
   if(version===editVersion){saved=record;parentId=record.id;pending=null;show(record.core.assessment);$('ct-save-state').textContent='Saved revision '+record.id.slice(0,12);$('ct-export-saved').disabled=false;}
   window.dispatchEvent(new CustomEvent('artha:contract-saved',{detail:{id:record.id}}));
   toast(version===editVersion?'Contract revision saved.':'Earlier inputs saved; check your current edits.');
  }else{const result=await api(base+'/check',body);if(version===editVersion)show(result);}
 }catch(error){$('ct-result-state').textContent=error.message;$('ct-result-state').className='ct-status ct-blocked';}
 finally{busy=false;$('ct-check').disabled=false;$('ct-save').disabled=false;}
}
async function refreshSaved(){
 try{
  const rows=await api(base);
  $('ct-saved-list').innerHTML=rows.length?rows.map(v=>'<button class="ct-saved-item" type="button" data-contract-open="'+esc(v.id)+'"><strong>'+esc(v.name)+'</strong><small>'+esc((v.parent_id?'Revision':'Original')+' · '+(v.definition_ready?'Defined':'Needs changes')+' · '+v.id.slice(0,10))+'</small></button>').join(''):'<p class="ct-note">Save a definition to keep immutable revisions here.</p>';
  $('ct-saved-list').querySelectorAll('[data-contract-open]').forEach(button=>button.addEventListener('click',async()=>{try{const record=await api(base+'/'+button.dataset.contractOpen);render(record.core.definition,record.core.dataset_id,record);}catch(error){toast(error.message,true);}}));
 }catch(error){$('ct-saved-list').textContent=error.message;}
}
function download(value,name){
 const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function importFile(event){
 const file=event.target.files[0];if(!file)return;
 try{
  if(file.size>65536)throw new Error('Definition JSON must be at most 64 KB.');
  const value=JSON.parse(await file.text()),definition=value.core?.definition||value.definition||value;
  const dataset=value.core?.dataset_id||value.dataset_id||null;
  await api(base+'/check',{definition,dataset_id:dataset});
  render(definition,dataset);toast('Definition imported for review. Nothing has been saved or executed.');
 }catch(error){toast(error.message,true);}
 finally{event.target.value='';}
}
async function load(){
 if(loaded){
  try{datasets=await api('/api/research/datasets');const select=$('ct-dataset'),selected=select.value;select.innerHTML='<option value="">Not selected — define requirements first</option>'+datasets.map(v=>'<option value="'+esc(v.id)+'">'+esc(v.manifest.name+' · '+v.manifest.frequency+' · '+v.manifest.source_kind)+'</option>').join('');select.value=selected;}catch(error){toast(error.message,true);}
  refreshSaved();return;
 }
 root.textContent='Loading strategy definitions…';
 try{const result=await Promise.all([api(base+'/catalog'),api('/api/research/datasets')]);examples=result[0].examples;datasets=result[1];render(examples.CALL_VERTICAL);loaded=true;}
 catch(error){root.textContent=error.message;}
}
window.addEventListener('artha:open-contract',async event=>{try{const record=await api(base+'/'+event.detail.id);render(record.core.definition,record.core.dataset_id,record);}catch(error){toast(error.message,true);}});
window.addEventListener('artha:navigate',event=>{if(event.detail.view==='contracts')load();});
if(activeView==='contracts')load();
})();
