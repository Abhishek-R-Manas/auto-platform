"""Durable forward portfolios, independent supervision, bounded jobs and review evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy
import json
import math
import os
import shutil
import sqlite3
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from .schemas import Portfolio, Monitoring
from .runtime import ForwardReplay, ACTIVE
from ..research.schemas import Manifest, Instrument, Run, aware, iso
from ..research.catalog import canonical, digest, bounds
from ..research.jobs import CURRENT_BUILD
from ..research.signals import capabilities, fit_model
from ..research.metrics import metrics
from ..finance import rupees
from ..candidates.provider import ModelError

class ForwardService:
    def __init__(self, root, kite, hub, lab, private_config, provider=None, clock=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.kite=kite;self.hub=hub;self.lab=lab;self.private_config=private_config
        self.clock=clock or (lambda:datetime.now(timezone.utc));self.lock=threading.RLock();self.closing=False
        self.db=sqlite3.connect(self.root/'forward.sqlite3',timeout=15,check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS portfolios(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,document TEXT NOT NULL,state TEXT NOT NULL,state_hash TEXT NOT NULL,revision INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS events(portfolio_id TEXT NOT NULL,sequence INTEGER NOT NULL,kind TEXT NOT NULL,document TEXT NOT NULL,PRIMARY KEY(portfolio_id,sequence));
          CREATE INDEX IF NOT EXISTS event_kind ON events(portfolio_id,kind,sequence);
          CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,portfolio_id TEXT NOT NULL,code TEXT NOT NULL,status TEXT NOT NULL,document TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS incident_scope ON incidents(portfolio_id,code,status);
          CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,incident_id TEXT UNIQUE NOT NULL,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS controls(portfolio_id TEXT NOT NULL,request_id TEXT NOT NULL,body_hash TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(portfolio_id,request_id));
          CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,portfolio_id TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS drills(id TEXT PRIMARY KEY,portfolio_id TEXT NOT NULL,document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,portfolio_id TEXT NOT NULL,request_id TEXT NOT NULL,body_hash TEXT NOT NULL,document TEXT NOT NULL,UNIQUE(portfolio_id,request_id));
          CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,document TEXT NOT NULL);""")
        from .provider import MonitorProvider
        self.provider=provider or MonitorProvider()
        from .alerts import TelegramDelivery
        self.alert_provider=TelegramDelivery(private_config.read() if hasattr(private_config,'read') else {})
        self.alert_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-alerts');self.alert_futures={}
        self.history_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-candles')
        self.warmup_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-warmup')
        self.history_last={True:0.,False:0.}
        self.ai_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-forward-ai')
        self.futures={};self.last_tick=None;self.last_error=None;self.last_audit=None;self.audit_ok=True
        with self.transaction():
            for row in self.db.execute('SELECT * FROM portfolios').fetchall():
                doc,state=self._decode(row);runtime=ForwardReplay(doc,state)
                runtime.pause('Process restarted; review state and resume explicitly')
                self._commit(doc,runtime,bump_revision=True)
            for row in self.db.execute("SELECT document FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchall():
                item=json.loads(row[0]);item.update(status='INTERRUPTED',completed_at=self.now(),error='Process restarted; uncertain model usage remains reserved; no automatic retry')
                self._save_job(item)
                if item['kind']=='AI':self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_periodic_suspended',canonical(True)))
            for row in self.db.execute('SELECT document FROM notifications').fetchall():
                item=json.loads(row[0])
                if item['status']=='ATTEMPTING':
                    item.update(status='FAILED_UNCERTAIN',error='Process restarted during delivery; no automatic retry')
                    self.db.execute('UPDATE notifications SET document=? WHERE id=?',(canonical(item),item['id']))
        self.last_audit_result=self.audit();self.audit_ok=self.last_audit_result['ok']
        self._priorities()

    def now(self):return iso(self.clock())

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('SAVEPOINT forward_action')
            try:yield
            except BaseException:
                self.db.execute('ROLLBACK TO forward_action');self.db.execute('RELEASE forward_action');raise
            else:self.db.execute('RELEASE forward_action')

    def _decode(self,row):
        if not row:raise ValueError('Forward portfolio not found')
        doc=json.loads(row['document']);state=json.loads(row['state'])
        check=copy.deepcopy(doc);expected=check.pop('document_hash',None)
        if expected!=digest(check) or row['state_hash']!=digest(state):raise ValueError('Forward configuration/checkpoint integrity failed; entries disabled')
        return doc,state

    def _load(self,identifier):return self._decode(self.db.execute('SELECT * FROM portfolios WHERE id=?',(identifier,)).fetchone())

    def _commit(self,doc,runtime,bump_revision=False):
        identifier=doc['id'];last=self.db.execute('SELECT sequence,document FROM events WHERE portfolio_id=? ORDER BY sequence DESC LIMIT 1',(identifier,)).fetchone()
        previous=json.loads(last['document'])['hash'] if last else '0'*64
        expected=last['sequence']+1 if last else 1
        for record in runtime.records:
            if record['sequence']!=expected:raise RuntimeError('Forward event sequence mismatch')
            envelope=dict(record,portfolio_id=identifier,configuration_hash=doc['document_hash'],engine_build=CURRENT_BUILD['code_hash'],previous_hash=previous)
            envelope['hash']=digest(envelope)
            self.db.execute('INSERT INTO events VALUES(?,?,?,?)',(identifier,expected,record['kind'],canonical(envelope)))
            previous=envelope['hash'];expected+=1
        if runtime.state['cash']!=runtime.state['ledger_total']:raise RuntimeError('Forward cash ledger reconciliation failed')
        payload=canonical(runtime.state)
        self.db.execute('UPDATE portfolios SET state=?,state_hash=?,revision=revision+? WHERE id=?',(payload,digest(runtime.state),int(bump_revision),identifier))
        runtime.records=[]

    def _bindings(self,mapping,at):
        master=self.kite.master();by_key={row['exchange']+':'+row['tradingsymbol']:row for row in master}
        mapping=copy.deepcopy(mapping)
        for key in list(mapping.values()):
            raw=by_key.get(key)
            if not raw:raise ValueError('Bound instrument is absent from current Kite master: '+key)
            if raw['exchange']=='NFO':
                underlying=self.kite.underlying_key(raw)
                if underlying not in mapping.values():mapping['REF_'+digest(underlying)[:12]]=underlying
        if len(mapping)>50:raise ValueError('A portfolio may bind at most 50 instruments including underlying references')
        instruments=[];bindings={}
        for identifier,key in mapping.items():
            raw=by_key.get(key)
            if not raw or raw['exchange'] not in {'NSE','NFO'}:raise ValueError('Unsupported current instrument binding: '+key)
            index=raw.get('segment')=='INDICES' or raw.get('instrument_type')=='INDEX'
            kind='INDEX' if index else 'EQ' if raw['exchange']=='NSE' and raw['instrument_type']=='EQ' else 'FUT' if raw['instrument_type']=='FUT' else 'OPT' if raw['instrument_type'] in {'CE','PE'} else None
            if not kind:raise ValueError('Only NSE stocks/index references and NFO futures/options are supported')
            underlying=identifier
            if kind in {'FUT','OPT'}:underlying=next(k for k,v in mapping.items() if v==self.kite.underlying_key(raw))
            expiry=datetime.fromisoformat(raw['expiry']).replace(hour=15,minute=30,tzinfo=ZoneInfo('Asia/Kolkata')) if raw.get('expiry') else None
            if expiry and expiry<=aware(at):raise ValueError('Expired contracts cannot enter a new forward protocol')
            item=Instrument(id=identifier,symbol=raw['tradingsymbol'],kind=kind,underlying=underlying,exchange=raw['exchange'],lot_size=1 if kind=='INDEX' else int(raw['lot_size']),tick_size=float(raw.get('tick_size') or .05),expiry=expiry,
                strike=float(raw['strike']) if kind=='OPT' else None,option_type=raw['instrument_type'] if kind=='OPT' else None,
                settlement='PHYSICAL' if kind in {'FUT','OPT'} and by_key[mapping[underlying]].get('segment')!='INDICES' else 'CASH').model_dump(mode='json')
            instruments.append(item)
            bindings[identifier]=dict(key=key,instrument_token=int(raw['instrument_token']),kind=kind,underlying=underlying,lot_size=item['lot_size'],tick_size=item['tick_size'],expiry=item['expiry'],master_hash=self.kite.master_hash,master_observed_date=str(self.kite.master_date))
        return instruments,bindings

    def _candidate_inputs(self,body):
        strategies=[];references=[];cutoff=None;models={}
        if body.origin=='REFERENCE':return [s.model_dump(mode='json') for s in body.strategies],references,self.now(),models
        if not self.lab.verify_audit()['ok']:raise ValueError('Candidate registry audit integrity failed')
        for identifier in body.candidate_ids:
            packet=self.lab.paper_packet(identifier);version=self.lab.version(identifier);study=self.lab.study(version['study_id'])
            dataset=self.lab.research.catalog.verify(packet['dataset_id']);manifest=dataset['manifest']
            expected=1440 if manifest['frequency']=='DAY' else manifest['interval_minutes']
            if manifest['frequency']=='QUOTE' or body.interval_minutes!=expected:raise ValueError('Forward completed-candle frequency must match the approved historical protocol')
            if packet['review_build']['code_hash']!=CURRENT_BUILD['code_hash'] and len(body.compatibility_review)<20:raise ValueError('A different execution build requires an explicit documented compatibility review')
            for key,value in body.risk.model_dump().items():
                approved=study['config']['risk'][key]
                if key=='slippage_bps' and value<approved or key!='slippage_bps' and key!='cash_buffer_pct' and value>approved or key=='cash_buffer_pct' and value<approved:
                    raise ValueError('Forward risk cannot weaken the approved candidate policy: '+key)
            old=study['config']['execution'];new=body.execution.model_dump()
            if new['spread_bps']<old['spread_bps'] or new['latency_seconds']<old['latency_seconds'] or new['participation_pct']>old['participation_pct'] or new['allow_uncovered_options'] and not old['allow_uncovered_options']:
                raise ValueError('Forward execution cannot improve approved assumptions or add uncovered-option authority')
            strategies.append(version['strategy'])
            references.append(dict(version_id=identifier,packet_id=packet['id'],packet_hash=packet['packet_hash'],strategy_hash=digest(version['strategy']),protocol_hash=packet['protocol_hash'],dataset_id=packet['dataset_id'],review_build=packet['review_build'],strategy_build=packet['strategy_build']))
            end=study['windows']['TRAIN']['end']
            cutoff=min(cutoff,end) if cutoff else end
            if version['strategy']['family']=='LINEAR_MODEL':
                spec=version['strategy'];ref=(spec['signal_instruments'] or [l['instrument_id'] for l in spec['legs'] if l['instrument_id']])[0]
                rows=[row for row in self.lab.research.catalog.rows(packet['dataset_id']) if row['instrument_id']==ref]
                model=fit_model(rows,end,spec['parameters']['model_lags'],spec['parameters']['ridge'],study['windows']['TRAIN']['start'])
                if not model:raise ValueError('Approved learned strategy has no reproducible frozen training model')
                models[spec['id']]=model
        return strategies,references,cutoff,models

    def create(self,body):
        body=body if isinstance(body,Portfolio) else Portfolio.model_validate(body)
        request=body.model_dump(mode='json');request_hash=digest(request)
        with self.lock:
            existing=self.db.execute('SELECT * FROM portfolios WHERE request_id=?',(body.request_id,)).fetchone()
            if existing:
                doc,_=self._decode(existing)
                if doc['request_hash']!=request_hash:raise ValueError('Request id is already bound to a different forward protocol')
                return self.portfolio(doc['id'])
            if self.db.execute('SELECT count(*) FROM portfolios').fetchone()[0]>=200:raise ValueError('Forward portfolio archive capacity reached')
        strategies,references,cutoff,models=self._candidate_inputs(body)
        at=self.now();instruments,bindings=self._bindings(body.bindings,at)
        current={i['id']:i for i in instruments}
        for reference in references:
            approved=self.lab.research.catalog.verify(reference['dataset_id'])['manifest']
            original={i['id']:i for i in approved['instruments']}
            version=self.lab.version(reference['version_id']);spec=version['strategy']
            fixed=set(spec['signal_instruments'])|{leg['instrument_id'] for leg in spec['legs'] if leg['instrument_id']}|{leg['selector']['underlying'] for leg in spec['legs'] if leg['selector']}
            for key in fixed:
                a=original.get(key);b=current.get(key)
                if not a or not b or any(a[field]!=b[field] for field in ('kind','symbol','exchange')):raise ValueError('A fixed approved instrument cannot be rebound to a different asset: '+key)
                if a['kind'] in {'FUT','OPT'} and any(a[field]!=b[field] for field in ('expiry','strike','option_type','lot_size')):raise ValueError('An approved fixed contract cannot silently roll or change terms: '+key)
        manifest=Manifest(name=body.name,source='KITE_CURRENT_MASTER_AND_FORWARD_OBSERVATIONS',source_kind='BROKER',frequency='QUOTE',rights_confirmed=body.data_rights_confirmed,
            instruments=instruments,calendar=body.calendar,actions=body.actions,notes='Current contracts; margin, fees, Greeks and fills are estimates. Historical/corporate-action completeness is not implied.').model_dump(mode='json')
        replay=Run(dataset_id=digest(manifest),capital=body.capital,strategies=strategies,risk=body.risk,execution=body.execution,training_end=cutoff,benchmark_id=body.benchmark_id).model_dump(mode='json')
        dataset=dict(manifest=manifest,status='READY',quality=dict(warnings=[]))
        check=capabilities(dataset,replay)
        if check['blockers']:raise ValueError('; '.join(check['blockers']))
        for spec in strategies:
            for leg in spec['legs']:
                if leg['selector'] and leg['selector']['underlying'] not in bindings:raise ValueError('Dynamic selector underlying needs an explicit binding')
        doc=dict(id=uuid.uuid4().hex,created_at=at,request_hash=request_hash,config=request,manifest=manifest,bindings=bindings,replay_config=replay,candidates=references,build=copy.deepcopy(CURRENT_BUILD),capability=check,
            qualification_label='UNQUALIFIED_REFERENCE' if body.origin=='REFERENCE' else 'APPROVED_CANDIDATE_FORWARD_EVALUATION',live_orders_enabled=False)
        doc['document_hash']=digest(doc);runtime=ForwardReplay(doc)
        # A new book has no historical entitlements; its supplied warm-up is not replayed.
        runtime.state['actions_done']=[index for index,action in enumerate(manifest['actions']) if aware(action['effective_at'])<aware(at)]
        for owner,model in models.items():runtime.state['local'][owner]['model']=model
        for owner,spec in runtime.specs.items():
            if spec['family']=='SCHEDULED':
                past=[index for index,item in enumerate(spec['schedule']) if aware(item['at'])<aware(at)]
                if len(past)==len(spec['schedule']):raise ValueError('Scheduled forward strategies require at least one future decision; historical instructions are not replayed')
                runtime.state['local'][owner]['scheduled']=past
        runtime.event('CREATED','Immutable forward paper portfolio; explicit resume required',provenance=references,qualification=doc['qualification_label'])
        with self.transaction():
            existing_tokens={value['instrument_token'] for entry in self._states() if not entry['state']['forward']['retired'] for value in entry['document']['bindings'].values()}
            if len(existing_tokens|{value['instrument_token'] for value in bindings.values()})>500:raise ValueError('Forward selected-instrument capacity exceeded')
            self.db.execute('INSERT INTO portfolios VALUES(?,?,?,?,?,0)',(doc['id'],body.request_id,canonical(doc),canonical(runtime.state),digest(runtime.state)))
            self._commit(doc,runtime)
        self._priorities();return self.portfolio(doc['id'])

    def _priorities(self):
        items={}
        with self.lock:
            for row in self.db.execute('SELECT * FROM portfolios').fetchall():
                doc,state=self._decode(row)
                if state['forward']['retired']:continue
                for key,value in doc['bindings'].items():items[doc['id']+':'+key]=value
        if len({v['instrument_token'] for v in items.values()})>500:raise ValueError('Forward selected-instrument capacity exceeded')
        self.hub.set_forward_instruments(items)

    def _binding_checks(self,doc):
        at=aware(self.now());last=getattr(self,'master_check_at',None)
        if last is None or (at-aware(last)).total_seconds()>=60:
            self.master_check_at=self.now()
            try:
                rows=self.kite.master();self.current_master={row['exchange']+':'+row['tradingsymbol']:row for row in rows};self.master_error=False
            except Exception:self.master_error=True
        if getattr(self,'master_error',False):return ['CURRENT_CONTRACT_MASTER_UNAVAILABLE']
        for item in doc['bindings'].values():
            raw=self.current_master.get(item['key'])
            if not raw or int(raw['instrument_token'])!=item['instrument_token']:return ['CONTRACT_BINDING_CHANGED']
            if item['kind']!='INDEX' and (int(raw['lot_size'])!=item['lot_size'] or float(raw.get('tick_size') or .05)!=item['tick_size']):return ['CONTRACT_TERMS_CHANGED']
            if item['expiry'] and (raw.get('expiry')!=item['expiry'][:10] or aware(item['expiry'])<=at):return ['CONTRACT_EXPIRED_OR_CHANGED']
        return []

    def _attach_inputs(self,runtime):
        inputs=getattr(self,'intelligence',None);names=runtime.document['config'].get('evidence_features',[])
        if inputs is not None and names:
            frozen=runtime.document['config'].get('evidence_policy_hash')
            runtime.feature_lookup=lambda stamp:inputs.features(stamp,names) if inputs.policy_hash()==frozen else {}
        return runtime

    def _bar_checks(self,doc,runtime):
        f=runtime.state['forward']
        if not f['ever_running'] or doc['config']['bar_source']!='KITE_CANDLES':return []
        at=aware(self.now());zone=ZoneInfo(doc['manifest']['calendar']['timezone'])
        day=at.astimezone(zone).date();span=bounds(doc['manifest']['calendar'],day)
        if doc['config']['interval_minutes']==1440 and (not span or at<span[1]):
            for _ in range(370):
                day-=timedelta(days=1);span=bounds(doc['manifest']['calendar'],day)
                if span:break
        if not span or at<=span[0]:return []
        step=doc['config']['interval_minutes']*60
        for identifier in doc['bindings']:
            baseline=max(span[0],aware(f['started_at'] or doc['created_at']),aware(f['bar_watermarks'].get(identifier) or doc['created_at']))
            if baseline>=span[1]:continue
            first_missing=span[1] if step==86400 else min(span[1],span[0]+timedelta(seconds=(int((baseline-span[0]).total_seconds())//step+1)*step))
            if at>first_missing+timedelta(seconds=120):return ['COMPLETED_BAR_GAP']
        return []

    def _readiness(self,doc,runtime):
        blockers=[];at=aware(self.now());market=self.hub.status()
        blockers.extend(self._binding_checks(doc))
        blockers.extend(self._bar_checks(doc,runtime))
        names=doc['config'].get('evidence_features',[])
        if names:
            inputs=getattr(self,'intelligence',None)
            if inputs is None:blockers.append('EVIDENCE_SERVICE_UNAVAILABLE')
            elif not inputs.status()['worker']['healthy'] or not inputs.audit_result['ok']:blockers.append('EVIDENCE_SUPERVISOR_OR_AUDIT_FAILED')
            elif inputs.policy_hash()!=doc['config'].get('evidence_policy_hash'):blockers.append('EVIDENCE_POLICY_CHANGED')
            elif set(inputs.features(self.now(),names))!=set(names):blockers.append('REQUIRED_INPUT_STALE_OR_MISSING')
        for code in ('CANDLE_WORKER_STALLED','CANDLE_REQUEST_FAILED'):
            row=self.db.execute("SELECT document FROM incidents WHERE portfolio_id=? AND code=? AND status!='RESOLVED' ORDER BY rowid DESC LIMIT 1",(doc['id'],code)).fetchone()
            if row and json.loads(row[0])['condition_active']:blockers.append(code)
        if not self.audit_ok:blockers.append('AUDIT_INTEGRITY_FAILED')
        if doc['build']['code_hash']!=CURRENT_BUILD['code_hash']:blockers.append('EXECUTION_BUILD_CHANGED')
        if not doc['config']['data_rights_confirmed'] or not doc['config']['assumptions_reviewed']:blockers.append('OPERATOR_REVIEW_REQUIRED')
        if at.date().isoformat()>doc['config']['calendar_valid_until']:blockers.append('CALENDAR_REVIEW_EXPIRED')
        if not self.kite.status()['authenticated']:blockers.append('BROKER_LOGIN_REQUIRED')
        elif not market['enabled'] or market['phase']!='STREAMING' or not market['connections'] or any(v!='CONNECTED' for v in market['connections'].values()):blockers.append('MARKET_TRANSPORT_NOT_READY')
        for item in doc['candidates']:
            try:
                packet=self.lab.paper_packet(item['version_id'])
                if packet['packet_hash']!=item['packet_hash']:blockers.append('CANDIDATE_PACKET_CHANGED')
            except ValueError:blockers.append('CANDIDATE_APPROVAL_REVOKED')
        if runtime.state['cash']!=runtime.state['ledger_total']:blockers.append('ACCOUNTING_RECONCILIATION_FAILED')
        if runtime.state['halt']:blockers.append('RISK_HALT')
        if runtime.state['obligations']:blockers.append('UNRESOLVED_OBLIGATIONS')
        for spec in runtime.specs.values():
            if spec['family']=='SCHEDULED':continue
            required=spec['parameters']['slow']+1 if spec['family']=='TREND' else spec['parameters']['model_lags']+1 if spec['family']=='LINEAR_MODEL' else spec['parameters']['lookback']+1
            references=spec['signal_instruments'] or [l['instrument_id'] for l in spec['legs'] if l['instrument_id']] or [spec['legs'][0]['selector']['underlying']]
            if any(len(runtime.state['histories'].get(key,[]))<required for key in references):blockers.append('SIGNAL_WARMUP_REQUIRED')
            if spec['family']=='LINEAR_MODEL' and not runtime.state['local'][spec['id']].get('model'):blockers.append('FROZEN_MODEL_REQUIRED')
        if shutil.disk_usage(self.root).free<100000000:blockers.append('LOW_DISK_SPACE')
        if runtime.state['sequence']>=doc['config']['supervision']['event_budget']:blockers.append('EVENT_STORAGE_BUDGET_REACHED')
        return list(dict.fromkeys(blockers))

    def portfolio(self,identifier):
        with self.lock:
            doc,state=self._load(identifier);runtime=ForwardReplay(doc,state)
            row=self.db.execute('SELECT revision FROM portfolios WHERE id=?',(identifier,)).fetchone()
            f=state['forward'];account=runtime.account_view()
            return dict(id=identifier,name=doc['config']['name'],origin=doc['config']['origin'],created_at=doc['created_at'],revision=row[0],document=doc,
                status='RETIRED' if f['retired'] else 'PAUSED' if f['paused'] else 'RUNNING',account=account,positions=runtime.final_positions(),orders=list(state['orders'].values()),
                clock=state['clock'],cursor=state['cursor'],sequence=state['sequence'],readiness=self._readiness(doc,runtime),entry_blockers=f['blockers'],halt=state['halt'],
                coverage_pct=sum(f.get('covered_seconds',{}).values())/sum(f.get('observed_seconds',{}).values())*100 if sum(f.get('observed_seconds',{}).values()) else None,observed_ticks=f['observed_ticks'],sessions=len(f['sessions']),last_tick=f['last_tick'],last_bar=f['last_bar'],
                history_counts={key:len(value) for key,value in state['histories'].items()},next_session_signals=f.get('daily_signals',{}),obligations=state['obligations'],fees=rupees(state['fees']),reconciliation_ok=state['cash']==state['ledger_total'],
                benchmark_value=f['benchmark_value'],qualification_label=doc['qualification_label'],live_orders_enabled=False)

    def portfolios(self):
        with self.lock:ids=[r[0] for r in self.db.execute('SELECT id FROM portfolios ORDER BY rowid DESC')]
        return [self.portfolio(identifier) for identifier in ids]

    def events(self,identifier,after=0,limit=100,kind=None):
        with self.lock:
            self._load(identifier)
            sql='SELECT document FROM events WHERE portfolio_id=? AND sequence>?';args=[identifier,after]
            if kind:sql+=' AND kind=?';args.append(kind)
            sql+=' ORDER BY sequence LIMIT ?';args.append(min(500,max(1,limit)))
            return [json.loads(r[0]) for r in self.db.execute(sql,args)]

    def audit(self,identifier=None):
        with self.lock:
            identifiers=[identifier] if identifier else [r[0] for r in self.db.execute('SELECT id FROM portfolios')]
            count=0
            try:
                for key in identifiers:
                    doc,state=self._load(key);previous='0'*64;sequence=0;review_count=0
                    for row in self.db.execute('SELECT document FROM events WHERE portfolio_id=? ORDER BY sequence',(key,)):
                        event=json.loads(row[0]);expected=event.pop('hash',None);sequence+=1;count+=1
                        if event['previous_hash']!=previous or event['sequence']!=sequence or digest(event)!=expected:return dict(ok=False,checked=count,reason='Forward event chain mismatch')
                        previous=expected
                        if event['kind']=='GUARDIAN_REVIEW':
                            review_count+=1
                            saved=self.db.execute('SELECT document FROM reviews WHERE id=? AND portfolio_id=?',(event['review']['id'],key)).fetchone()
                            if not saved or json.loads(saved[0])!=event['review']:return dict(ok=False,checked=count,reason='Guardian review/event mismatch')
                    if self.db.execute('SELECT count(*) FROM reviews WHERE portfolio_id=?',(key,)).fetchone()[0]!=review_count:return dict(ok=False,checked=count,reason='Guardian review/event count mismatch')
                    if state['sequence']!=sequence:return dict(ok=False,checked=count,reason='Forward checkpoint/event sequence mismatch')
            except (ValueError,TypeError,KeyError):return dict(ok=False,checked=count,reason='Forward checkpoint or configuration mismatch')
            return dict(ok=True,checked=count,note='Local SHA-256 evidence chain; not externally anchored or tamper-proof')

    def control(self,identifier,body):
        payload=body.model_dump(mode='json');body_hash=digest(payload)
        with self.transaction():
            old=self.db.execute('SELECT body_hash,response FROM controls WHERE portfolio_id=? AND request_id=?',(identifier,body.request_id)).fetchone()
            if old:
                if old[0]!=body_hash:raise ValueError('Control request id is already bound to different inputs')
                return json.loads(old[1])
            doc,state=self._load(identifier);revision=self.db.execute('SELECT revision FROM portfolios WHERE id=?',(identifier,)).fetchone()[0]
            if body.expected_revision!=revision:raise ValueError('Portfolio changed; refresh before applying the control')
            runtime=ForwardReplay(doc,state);runtime.state['clock']=self.now();f=runtime.state['forward']
            if body.action=='RESUME':
                if f['retired']:raise ValueError('A retired experiment cannot resume; create a new immutable protocol')
                if f['paused'] and sum(not p['state']['forward']['paused'] for p in self._states())>=8:raise ValueError('At most eight forward portfolios may run concurrently')
                active=[entry['document'] for entry in self._states() if entry['document']['id']!=identifier and not entry['state']['forward']['paused']]
                active.append(doc)
                demand=sum(len(item['bindings'])/item['config']['interval_minutes'] for item in active if item['config']['bar_source']=='KITE_CANDLES')
                if demand>30:raise ValueError('Selected portfolios exceed the declared 30 candle requests/minute operating budget; reduce bindings or use longer bars')
                blockers=self._readiness(doc,runtime)
                if blockers:raise ValueError('; '.join(blockers))
                f.update(paused=False,ever_running=True,started_at=f['started_at'] or self.now())
                runtime.event('RESUME','Operator resumed forward paper entries',review=payload)
            elif body.action=='PAUSE':runtime.pause('Operator paused new entries')
            elif body.action=='CANCEL_ENTRIES':runtime.cancel_entries('Operator cancelled pending entries')
            elif body.action=='LIQUIDATE':
                runtime.pause('Operator requested reduce-only liquidation; completion depends on fresh depth')
                for owner in runtime.specs:runtime.close_book(owner,'Operator liquidation request')
            elif body.action=='RETIRE':
                if state['positions'] or any(o['status'] in ACTIVE for o in state['orders'].values()):raise ValueError('Retirement requires flat positions and no active orders')
                f.update(retired=True,paused=True)
            runtime.event('OPERATOR_CONTROL',body.action,review=payload);self._commit(doc,runtime,bump_revision=True)
            result=dict(id=identifier,action=body.action,applied=True,live_orders_enabled=False)
            self.db.execute('INSERT INTO controls VALUES(?,?,?,?)',(identifier,body.request_id,body_hash,canonical(result)))
        self._priorities();return result

    def _states(self):
        return [dict(document=doc,state=state) for doc,state in (self._decode(row) for row in self.db.execute('SELECT * FROM portfolios').fetchall())]

    def _incident(self,identifier,code,reason,active=True,severity='WARNING'):
        row=self.db.execute("SELECT document FROM incidents WHERE portfolio_id=? AND code=? AND status!='RESOLVED' ORDER BY rowid DESC LIMIT 1",(identifier,code)).fetchone()
        if active and not row:
            item=dict(id=uuid.uuid4().hex,portfolio_id=identifier,code=code,reason=reason,severity=severity,status='OPEN',opened_at=self.now(),last_seen=self.now(),condition_active=True,review=None)
            self.db.execute('INSERT INTO incidents VALUES(?,?,?,?,?)',(item['id'],identifier,code,item['status'],canonical(item)))
            policy=self.alert_policy()
            external=policy['enabled'] and self.alert_provider.status()['configured']
            notification=dict(id=uuid.uuid4().hex,incident_id=item['id'],created_at=self.now(),status='QUEUED' if external else 'LOCAL_DASHBOARD',external_delivery='TELEGRAM' if external else 'NOT_CONFIGURED',reason=reason,
                text='Artha PAPER alert\nSeverity: '+severity+'\nPortfolio: '+identifier+'\nCondition: '+code+'\nReview the forward dashboard. All executions remain simulated.',attempts=[])
            self.db.execute('INSERT INTO notifications VALUES(?,?,?)',(notification['id'],item['id'],canonical(notification)))
        elif row:
            item=json.loads(row[0]);item.update(last_seen=self.now(),condition_active=active)
            self.db.execute('UPDATE incidents SET document=? WHERE id=?',(canonical(item),item['id']))

    def incidents(self,identifier=None):
        with self.lock:
            rows=self.db.execute('SELECT document FROM incidents'+(' WHERE portfolio_id=?' if identifier else '')+' ORDER BY rowid DESC LIMIT 500',(identifier,) if identifier else ())
            return [json.loads(row[0]) for row in rows]

    def acknowledge(self,identifier,body):
        with self.transaction():
            row=self.db.execute('SELECT document FROM incidents WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Incident not found')
            item=json.loads(row[0])
            if body.action=='RESOLVE' and item['condition_active']:raise ValueError('The underlying condition is still active; acknowledgement does not clear it')
            item.update(status='ACKNOWLEDGED' if body.action=='ACKNOWLEDGE' else 'RESOLVED',review=dict(body.model_dump(mode='json'),at=self.now()))
            self.db.execute('UPDATE incidents SET status=?,document=? WHERE id=?',(item['status'],canonical(item),identifier))
            doc,state=self._load(item['portfolio_id']);runtime=ForwardReplay(doc,state);runtime.state['clock']=self.now();runtime.event('INCIDENT_REVIEW',body.action,incident_id=identifier,review=item['review']);self._commit(doc,runtime)
            return item

    def _save_job(self,item):
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?)',(item['id'],item['request_id'],item['portfolio_id'],item['kind'],item['status'],canonical(item)))

    def jobs(self,identifier=None):
        with self.lock:
            rows=self.db.execute('SELECT document FROM jobs'+(' WHERE portfolio_id=?' if identifier else '')+' ORDER BY rowid DESC LIMIT 200',(identifier,) if identifier else ())
            return [json.loads(row[0]) for row in rows]

    def _queue(self,identifier,kind,request_id,context=None):
        with self.transaction():
            doc,state=self._load(identifier);binding=digest(dict(portfolio_id=identifier,kind=kind,context=context))
            old=self.db.execute('SELECT document FROM jobs WHERE request_id=?',(request_id,)).fetchone()
            if old:
                item=json.loads(old[0])
                if item['binding']!=binding:raise ValueError('Job request id is bound to different inputs')
                return item
            if self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]>=16:raise ValueError('Forward job queue is full')
            if kind in {'WARMUP','CANDLES'} and self.db.execute("SELECT 1 FROM jobs WHERE portfolio_id=? AND kind IN ('WARMUP','CANDLES') AND status IN ('QUEUED','RUNNING')",(identifier,)).fetchone():raise ValueError('A candle request is already pending for this portfolio')
            if kind=='WARMUP' and (state['positions'] or state['orders'] or state['cursor'] or not state['forward']['paused']):raise ValueError('Warm-up requires a paused new portfolio with no forward decisions, orders or positions')
            item=dict(id=uuid.uuid4().hex,request_id=request_id,portfolio_id=identifier,kind=kind,status='QUEUED',created_at=self.now(),binding=binding,context=context,result=None,error=None)
            if kind=='AI':
                if not self.provider.status()['configured']:raise ValueError('Configure a dedicated AI key and model before requesting monitoring')
                policy=self.monitoring()
                # Count all persistent attempts, including interrupted/failed/cancelled calls.
                total=self.db.execute("SELECT document FROM jobs WHERE kind='AI'").fetchall()
                jobs=[json.loads(row[0]) for row in total]
                reserved=sum(json.loads(row[0]).get('reserved_tokens',0) for row in total)
                today=sum(j['created_at'][:10]==self.now()[:10] for j in jobs)
                if today>=policy['daily_requests'] or reserved+policy['max_output_tokens']>policy['total_reserved_tokens']:raise ValueError('AI monitoring request/output reservation budget exhausted')
                if any(j['status'] in {'QUEUED','RUNNING'} for j in jobs):raise ValueError('Only one monitoring model request may be pending')
                item.update(reserved_tokens=policy['max_output_tokens'],policy=policy,context=context or self.monitor_context(identifier))
            self._save_job(item)
            runtime=ForwardReplay(doc,state);runtime.state['clock']=self.now();runtime.event('JOB_QUEUED',kind,job_id=item['id'],input_hash=digest(item['context']) if item['context'] else None);self._commit(doc,runtime)
            return copy.deepcopy(item)

    def warmup(self,identifier,request_id):return self._queue(identifier,'WARMUP',request_id)

    def _history(self,document,state,warmup,cutoff):
        interval=document['config']['interval_minutes'];name='day' if interval==1440 else 'minute' if interval==1 else str(interval)+'minute'
        calendar=document['manifest']['calendar'];zone=ZoneInfo(calendar['timezone']);cutoff=aware(cutoff)
        rows=[]
        for identifier,binding in document['bindings'].items():
            if self.closing:raise ValueError('Candle worker is shutting down')
            start=cutoff-timedelta(days=1800 if interval==1440 else 7)
            if not warmup:
                last=state['forward']['bar_watermarks'].get(identifier)
                start=max(start,aware(last)-timedelta(minutes=interval)) if last else cutoff-timedelta(days=2)
            # One request/second is deliberately below the historical endpoint's published ceiling.
            before=time.monotonic();previous=self.history_last[warmup]
            if before-previous<1.05:time.sleep(1.05-(before-previous))
            self.history_last[warmup]=time.monotonic()
            payload=self.kite._request('GET','/instruments/historical/'+str(binding['instrument_token'])+'/'+name,params={
                'from':start.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S'),'to':cutoff.astimezone(zone).strftime('%Y-%m-%d %H:%M:%S'),'continuous':0,'oi':int(binding['kind'] in {'FUT','OPT'})})
            selected=[]
            for candle in payload.get('candles',[]):
                if len(candle)<6:raise ValueError('Broker returned an invalid candle')
                opening=aware(candle[0]);span=bounds(calendar,opening.astimezone(zone).date())
                if not span:continue
                end=span[1] if interval==1440 else min(span[1],opening+timedelta(minutes=interval))
                if interval==1440:opening=span[0]
                if opening<span[0] or opening>=span[1] or end>cutoff:continue
                values=[float(v) for v in candle[1:5]]
                if any(not math.isfinite(v) or v<=0 for v in values) or float(candle[5])<0:raise ValueError('Broker candle contains invalid prices/volume')
                selected.append(dict(instrument_id=identifier,timestamp=iso(end),open_at=iso(opening),open=values[0],high=values[1],low=values[2],close=values[3],volume=int(candle[5]),source='KITE_COMPLETED_CANDLE',available_at=iso(cutoff)))
            rows.extend(selected[-2048:])
        return rows

    def _expire_jobs(self):
        for row in self.db.execute("SELECT document FROM jobs WHERE status='RUNNING'").fetchall():
            item=json.loads(row[0])
            entry=self.futures.get(item['id'])
            if not entry or entry['future'].done():continue
            doc,_=self._load(item['portfolio_id'])
            if (aware(self.now())-aware(item['started_at'])).total_seconds()<=doc['config']['supervision']['worker_timeout_seconds']:continue
            item.update(status='TIMED_OUT',completed_at=self.now(),error='Worker exceeded its declared deadline; late output discarded; any provider reservation retained')
            self._save_job(item)
            code='AI_WORKER_STALLED' if item['kind']=='AI' else 'CANDLE_WORKER_STALLED'
            self._incident(item['portfolio_id'],code,item['error'])
            if item['kind']=='AI':self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_periodic_suspended',canonical(True)))

    def _dispatch_jobs(self):
        # A paid side effect must never escape a transaction which can roll back
        # its durable reservation or RUNNING marker.
        with self.lock:
            if self.db.in_transaction:raise RuntimeError('Job reservations must commit before dispatch')
            for row in self.db.execute("SELECT document FROM jobs WHERE status='QUEUED' ORDER BY rowid").fetchall():
                item=json.loads(row[0])
                kind=item['kind'];pool=self.ai_pool if kind=='AI' else self.warmup_pool if kind=='WARMUP' else self.history_pool
                if any(entry['kind']==kind for entry in self.futures.values()):continue
                with self.transaction():
                    doc,state=self._load(item['portfolio_id'])
                    item.update(status='RUNNING',started_at=self.now());self._save_job(item)
                try:
                    if kind=='AI':future=pool.submit(self.provider.request,item['context'],item['reserved_tokens'])
                    else:future=pool.submit(self._history,copy.deepcopy(doc),copy.deepcopy(state),kind=='WARMUP',self.now())
                    self.futures[item['id']]=dict(future=future,kind=kind)
                except RuntimeError:
                    with self.transaction():
                        item.update(status='FAILED',completed_at=self.now(),error='Worker could not confirm dispatch; explicit review required')
                        self._save_job(item)
                        self._incident(item['portfolio_id'],'AI_REQUEST_FAILED' if kind=='AI' else 'CANDLE_REQUEST_FAILED',item['error'])
                        if kind=='AI':self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_periodic_suspended',canonical(True)))

    def _finish_jobs(self):
        completed={}
        for identifier,entry in list(self.futures.items()):
            if not entry['future'].done():continue
            item=json.loads(self.db.execute('SELECT document FROM jobs WHERE id=?',(identifier,)).fetchone()[0])
            try:
                if item['status']!='RUNNING':continue
                result=entry['future'].result()
                if item['kind']=='AI':
                    proposal,evidence=result
                    allowed={fact['id'] for fact in item['context']['facts']}
                    if any(not set(f['evidence_ids'])<=allowed for f in proposal['findings']):raise ModelError('Model cited evidence outside the supplied snapshot',evidence)
                    item.update(result=dict(note=proposal,evidence=evidence,input_hash=digest(item['context']),authority='ADVISORY_ONLY'))
                elif item['kind']=='WARMUP':
                    with self.transaction():
                        doc,state=self._load(item['portfolio_id']);runtime=ForwardReplay(doc,state);runtime.state['clock']=self.now()
                        if state['cursor'] or state['positions'] or state['orders'] or not state['forward']['paused']:raise ValueError('Portfolio advanced while warm-up was running; result not applied')
                        self._attach_inputs(runtime)
                        runtime.apply_bars(result,self.now(),warmup=True)
                        for owner,spec in runtime.specs.items():
                            if spec['family']=='LINEAR_MODEL' and 'model' not in runtime.state['local'][owner]:
                                ref=(spec['signal_instruments'] or [l['instrument_id'] for l in spec['legs'] if l['instrument_id']])[0]
                                model=fit_model(runtime.state['histories'].get(ref,[]),runtime.config['training_end'],spec['parameters']['model_lags'],spec['parameters']['ridge'])
                                if not model:raise ValueError('Not enough pre-cutoff warm-up data for a frozen model')
                                runtime.state['local'][owner]['model']=model
                        self._commit(doc,runtime)
                    item.update(result=dict(bars=len(result),execution_quotes_created=False,orders_created=False))
                else:
                    completed[item['portfolio_id']]=result;item.update(result=dict(bars=len(result)))
                item.update(status='SUCCEEDED',completed_at=self.now())
                for code in (('AI_REQUEST_FAILED','AI_WORKER_STALLED') if item['kind']=='AI' else ('CANDLE_REQUEST_FAILED','CANDLE_WORKER_STALLED')):
                    self._incident(item['portfolio_id'],code,'Successful explicit or scheduled recovery observed',active=False)
            except Exception as exc:
                message=str(exc) if isinstance(exc,ModelError) else 'Candle/monitor job failed; verify data permissions, contract coverage and network, then request a new attempt'
                item.update(status='FAILED',completed_at=self.now(),error=message)
                if isinstance(exc,ModelError) and exc.evidence:item['provider_evidence']=exc.evidence
                if item['kind']=='AI':
                    self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_periodic_suspended',canonical(True)))
                self._incident(item['portfolio_id'],'AI_REQUEST_FAILED' if item['kind']=='AI' else 'CANDLE_REQUEST_FAILED',message)
            finally:
                self._save_job(item);self.completed_futures.append(identifier)
        return completed

    def cancel_job(self,identifier):
        with self.transaction():
            row=self.db.execute('SELECT document FROM jobs WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Forward job not found')
            item=json.loads(row[0])
            if item['status'] not in {'QUEUED','RUNNING'}:return item
            item.update(status='CANCELLED',completed_at=self.now(),error='Cancelled; an already-running provider call may still incur usage; reservation retained')
            self._save_job(item);return item

    def monitoring(self):
        row=self.db.execute("SELECT document FROM settings WHERE name='monitoring'").fetchone()
        return json.loads(row[0]) if row else Monitoring().model_dump(mode='json')

    def set_monitoring(self,body):
        with self.transaction():
            if body.enabled and not self.provider.status()['configured']:raise ValueError('Configure the dedicated model before enabling periodic monitoring')
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('monitoring',canonical(body.model_dump(mode='json'))))
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_periodic_suspended',canonical(False)))
        return self.monitoring()

    def configure_ai(self,body):
        key=body.api_key.get_secret_value();model=body.model
        with self.lock:
            if any(j['kind']=='AI' and j['status'] in {'QUEUED','RUNNING'} for j in self.jobs()) or any(j['status'] in {'QUEUED','RUNNING','CANCEL_REQUESTED'} for j in self.lab.generations()):raise ValueError('Finish pending model requests before replacing credentials')
            self.private_config.save_ai(key,model)
            self.provider.key=key;self.provider.model=model
            self.lab.provider.key=key;self.lab.provider.model=model
        return dict(saved=True,provider=self.provider.status(),credential_values_returned=False)

    def monitor_context(self,identifier):
        asof=self.now()
        portfolio=self.portfolio(identifier)
        facts=[dict(id='portfolio:'+identifier,type='FORWARD_PORTFOLIO',value={key:portfolio[key] for key in ('name','origin','status','account','positions','entry_blockers','halt','coverage_pct','sessions','fees','qualification_label','reconciliation_ok')})]
        for item in self.incidents(identifier)[:20]:facts.append(dict(id='incident:'+item['id'],type='INCIDENT',value=item))
        rows=self.db.execute('SELECT document FROM events WHERE portfolio_id=? ORDER BY sequence DESC LIMIT 20',(identifier,)).fetchall()
        for row in rows:
            item=json.loads(row[0]);facts.append(dict(id='event:'+str(item['sequence']),type='DECISION',value={key:item[key] for key in ('sequence','time','kind','reason','strategy_id','instrument_id')}))
        inputs=getattr(self,'intelligence',None)
        if inputs is not None:
            names=portfolio['document']['config'].get('evidence_features',[]) or inputs.status()['series'][:20]
            for name,value in inputs.features(asof,names).items():facts.append(dict(id='input:'+value['observation_id'],type='DATED_RESEARCH_INPUT',value=dict(series=name,**value)))
            for item in [row for row in inputs.observations(100) if row['series']=='event.RBI' and aware(row['available_at'])<=aware(asof)][:5]:
                facts.append(dict(id='input:'+item['id'],type='OFFICIAL_MACRO_RELEASE',value={key:item.get(key) for key in ('title','published_at','publication_precision','publication_timezone_assumed','available_at','source_url')}))
        context=dict(observed_at=asof,mode='PAPER',facts=facts,limitations=['Only a bounded forward snapshot is supplied','No holdout observations or broker credentials are supplied','All fills, margin and sensitivities are simulations/estimates','Model output is advisory and cannot execute controls'])
        if len(canonical(context))>80000:raise ValueError('Monitoring context exceeds its bounded input budget')
        return context

    def request_monitor(self,identifier,request_id):return self._queue(identifier,'AI',request_id)

    def tick(self):
        if self.closing:return
        self.completed_futures=[]
        try:
            with self.transaction():
                self._expire_jobs()
                completed=self._finish_jobs()
                at=self.now();in_session_any=False
                for entry in self._states():
                    doc,state=entry['document'],entry['state'];identifier=doc['id'];runtime=ForwardReplay(doc,state)
                    if state['forward']['retired']:continue
                    blockers=self._readiness(doc,runtime);quotes=self.hub.forward_quotes(doc['bindings'])
                    day=aware(at).astimezone(ZoneInfo(doc['manifest']['calendar']['timezone'])).date();span=bounds(doc['manifest']['calendar'],day)
                    in_session=bool(span and span[0]<=aware(at)<=span[1]);in_session_any|=in_session
                    if in_session and state['forward']['ever_running'] and blockers and not state['forward']['paused']:
                        runtime.pause('Independent supervisor paused entries: '+', '.join(blockers))
                    prior_records=runtime.records.copy()
                    self._attach_inputs(runtime)
                    runtime.process(quotes,at,blockers,completed.get(identifier,[]))
                    if prior_records:
                        # Preserve the pause record while keeping monotonically ordered execution records.
                        runtime.records=prior_records+runtime.records
                    if in_session and state['forward']['ever_running'] and 'STALE_OR_MISSING_QUOTES' in runtime.state['forward']['blockers'] and not runtime.state['forward']['paused']:
                        runtime.pause('Independent supervisor paused entries because executable quotes are missing or stale')
                    observed_codes=set(blockers)
                    if in_session and not all(runtime.fresh(key) for key in doc['bindings']):observed_codes.add('STALE_OR_MISSING_QUOTES')
                    if runtime.state['halt']:observed_codes.add('RISK_HALT')
                    if runtime.state['obligations']:observed_codes.add('UNRESOLVED_OBLIGATIONS')
                    for code in observed_codes:self._incident(identifier,code,'Independent supervisor: '+code,severity='CRITICAL' if code in {'RISK_HALT','UNRESOLVED_OBLIGATIONS','ACCOUNTING_RECONCILIATION_FAILED'} else 'WARNING')
                    for incident in self.incidents(identifier):
                        if incident['status']!='RESOLVED' and incident['code'] not in observed_codes and incident['code'] not in {'AI_REQUEST_FAILED','CANDLE_REQUEST_FAILED','AI_WORKER_STALLED','CANDLE_WORKER_STALLED'}:self._incident(identifier,incident['code'],incident['reason'],active=False)
                    self._commit(doc,runtime,bump_revision=state['forward']['paused']!=runtime.state['forward']['paused'])
                    if doc['config']['bar_source']=='KITE_CANDLES' and runtime.state['forward']['ever_running'] and span and span[0]<=aware(at)<=span[1]+timedelta(hours=1) and self.kite.status()['authenticated']:
                        interval=doc['config']['interval_minutes']*60
                        closed=span[1] if interval==86400 and aware(at)>=span[1] else span[0] if interval==86400 else min(span[1],span[0]+timedelta(seconds=int((aware(at)-span[0]).total_seconds())//interval*interval))
                        watermarks=runtime.state['forward']['bar_watermarks']
                        if closed>span[0] and (not watermarks or min(aware(v) for v in watermarks.values())<closed):
                            active=any(j['portfolio_id']==identifier and j['kind'] in {'WARMUP','CANDLES'} and j['status'] in {'QUEUED','RUNNING'} for j in self.jobs(identifier))
                            recent=next((j for j in self.jobs(identifier) if j['kind'] in {'WARMUP','CANDLES'}),None)
                            if not active and (not recent or (aware(at)-aware(recent['created_at'])).total_seconds()>=15):self._queue(identifier,'CANDLES','candles-'+identifier+'-'+str(int(aware(at).timestamp())//15))
                policy=self.monitoring();suspended=self.db.execute("SELECT document FROM settings WHERE name='ai_periodic_suspended'").fetchone()
                if policy['enabled'] and not (suspended and json.loads(suspended[0])):
                    for entry in self._states():
                        if entry['state']['forward']['retired']:continue
                        identifier=entry['document']['id'];previous=next((j for j in self.jobs(identifier) if j['kind']=='AI'),None)
                        if not previous or (aware(at)-aware(previous['created_at'])).total_seconds()>=policy['interval_seconds']:
                            try:self.request_monitor(identifier,'periodic-'+identifier+'-'+str(int(aware(at).timestamp())//policy['interval_seconds']))
                            except ValueError as exc:
                                self._incident(identifier,'AI_BUDGET_OR_CONFIGURATION',str(exc))
                                self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('ai_periodic_suspended',canonical(True)))
                self.last_tick=at;self.last_error=None
            for identifier in self.completed_futures:self.futures.pop(identifier,None)
            self._dispatch_jobs()
            self._notifications_tick()
        except Exception:
            self.last_error='Forward worker failed; entries require operator review'
            # No quote, credential or provider exception text is logged.
            with self.transaction():
                for entry in self._states():
                    runtime=ForwardReplay(entry['document'],entry['state']);runtime.state['clock']=self.now();runtime.pause(self.last_error);self._commit(entry['document'],runtime)
                    self._incident(entry['document']['id'],'WORKER_FAILED',self.last_error,severity='CRITICAL')
        if self.last_audit is None or (aware(self.now())-aware(self.last_audit)).total_seconds()>=60:
            self.last_audit_result=self.audit();self.audit_ok=self.last_audit_result['ok'];self.last_audit=self.now()

    def report(self,identifier):
        with self.lock:
            doc,state=self._load(identifier);runtime=ForwardReplay(doc,state);portfolio=self.portfolio(identifier)
            rows=self.db.execute("SELECT document FROM events WHERE portfolio_id=? AND kind='EQUITY' ORDER BY sequence",(identifier,)).fetchall()
            curve=[dict(time=(item:=json.loads(row[0]))['time'],equity=item['account']['equity']) for row in rows]
            # Include the current quote mark even when no new candle has closed.
            curve.append(dict(time=state['clock'],equity=portfolio['account']['equity']))
            performance=metrics(curve,doc['config']['capital'])
            performance['max_drawdown_pct']=max(performance['max_drawdown_pct'],state['forward'].get('max_drawdown_pct',0))
            fill_rows=self.db.execute("SELECT document FROM events WHERE portfolio_id=? AND kind='FILL' ORDER BY sequence",(identifier,)).fetchall()
            fills=[json.loads(row[0]) for row in fill_rows]
            incidents=self.incidents(identifier);drills=self.drills(identifier)
            policy=doc['config']['supervision'];gates=[]
            def gate(name,passed,observed,required):gates.append(dict(name=name,passed=bool(passed),observed=observed,required=required))
            gate('approved_candidate_origin',doc['config']['origin']=='REGISTRY',doc['config']['origin'],'REGISTRY')
            gate('prospective_sessions',portfolio['sessions']>=policy['minimum_sessions'],portfolio['sessions'],policy['minimum_sessions'])
            gate('completed_orders',state['filled_orders']>=policy['minimum_fills'],state['filled_orders'],policy['minimum_fills'])
            gate('benchmark_observations',state['forward']['benchmark_value'] is not None,state['forward']['benchmark_value'],'Actual prospective benchmark observations')
            scenarios=runtime.stress_grid();stress_limit=max(0,portfolio['account']['equity'])*doc['config']['risk']['overnight_stress_pct']/100
            worst=max((max(0,-row['pnl']) for row in scenarios),default=0)
            gate('stress_scenarios',not any(row['unknown'] for row in scenarios) and worst<=stress_limit,dict(worst_loss=worst,unknown=sorted({key for row in scenarios for key in row['unknown']})),dict(max_loss=stress_limit))
            gate('predeclared_benchmark',doc['config']['benchmark_id'] is not None,doc['config']['benchmark_id'],'Declared in immutable protocol')
            gate('after_cost_return',performance['total_return_pct']>=policy['minimum_return_pct'],performance['total_return_pct'],policy['minimum_return_pct'])
            gate('drawdown',performance['max_drawdown_pct']<=doc['config']['risk']['drawdown_pct'],performance['max_drawdown_pct'],doc['config']['risk']['drawdown_pct'])
            gate('quote_coverage',portfolio['coverage_pct'] is not None and portfolio['coverage_pct']>=policy['minimum_quote_coverage_pct'],portfolio['coverage_pct'],policy['minimum_quote_coverage_pct'])
            gate('accounting_and_audit',portfolio['reconciliation_ok'] and self.audit(identifier)['ok'],portfolio['reconciliation_ok'],True)
            unresolved=self.db.execute("SELECT count(*) FROM incidents WHERE portfolio_id=? AND status!='RESOLVED'",(identifier,)).fetchone()[0]
            gate('no_unresolved_incidents',unresolved==0,unresolved,0)
            gate('no_unresolved_obligations',not state['obligations'],len(state['obligations']),0)
            gate('recovery_drills',bool(drills) and drills[0]['passed'] and drills[0]['build']['code_hash']==CURRENT_BUILD['code_hash'],dict(passed=drills[0]['passed'],build=drills[0]['build']['code_hash']) if drills else None,dict(passed=True,build=CURRENT_BUILD['code_hash']))
            model_notes=[j for j in self.jobs(identifier) if j['kind']=='AI' and j['status']=='SUCCEEDED']
            gate('genuine_ai_monitor_validation',any(j['result']['evidence'].get('live_provider_response') for j in model_notes),bool(model_notes),'Successful genuine provider monitoring')
            gate('current_readiness',not self._readiness(doc,runtime),self._readiness(doc,runtime),[])
            result=dict(portfolio_id=identifier,observed_at=self.now(),protocol_hash=doc['document_hash'],build=doc['build'],account=portfolio['account'],performance=performance,stress=scenarios,
                execution=dict(fill_records=len(fills),mean_delay_seconds=sum(f.get('delay_seconds',0) for f in fills)/len(fills) if fills else None,slippage_cost=rupees(state['slippage_cost']),fees=rupees(state['fees']),submitted_orders=state['submitted_orders'],rejected_orders=state['rejected_orders']),
                gates=gates,ready_for_phase5_review=all(g['passed'] for g in gates),decision='REVIEW_ELIGIBLE' if all(g['passed'] for g in gates) else 'CONTINUE_PAPER_OR_REVISE',
                live_orders_enabled=False,limitations=['Prospective evidence is not a profitability guarantee','Conservative gross margin, estimated charges and model Greeks','No exchange queue, official settlement or automated physical delivery','Single-host local operations; no independent industry certification'])
            benchmark=state['forward']['benchmark_value']
            result['benchmark']=dict(instrument_id=doc['config']['benchmark_id'],virtual_value=benchmark,return_pct=(benchmark/doc['config']['capital']-1)*100 if benchmark is not None else None,
                excess_return_pct=performance['total_return_pct']-(benchmark/doc['config']['capital']-1)*100 if benchmark is not None else None,
                method='Prospective price return from first forward mark; no distribution reinvestment, financing or risk-adjusted alpha claim')
            # Stable evidence excludes request time and guardian events. A review
            # cannot approve itself or silently follow changed financial evidence.
            head=self.db.execute("SELECT sequence,document FROM events WHERE portfolio_id=? AND kind!='GUARDIAN_REVIEW' ORDER BY sequence DESC LIMIT 1",(identifier,)).fetchone()
            result['evidence_head']=dict(sequence=head[0],hash=json.loads(head[1])['hash']) if head else None
            result['positions']=portfolio['positions'];result['pending_orders']=portfolio['orders']
            inputs=getattr(self,'intelligence',None);names=doc['config'].get('evidence_features',[])
            result['research_inputs']=inputs.features(self.now(),names) if inputs is not None and names else {}
            result['evidence_policy_hash']=doc['config'].get('evidence_policy_hash')
            result['technical_ready_for_review']=all(g['passed'] for g in gates)
            result['evidence_hash']=digest({key:value for key,value in result.items() if key!='observed_at'})
            reviews=self.reviews(identifier);latest=reviews[0] if reviews else None
            current=bool(latest and latest['decision'] not in {'REVOKE','REJECT'} and latest['evidence_hash']==result['evidence_hash'] and aware(latest['expires_at'])>aware(self.now()))
            result['guardian']=dict(latest=latest,current=current,identity_assurance='LOCAL_OWNER_ASSERTION',license_verified=False,execution_authority=False)
            gate('guardian_review',current and latest['decision']=='APPROVE_REVIEW',latest['decision'] if latest else None,'Current human approval of this exact evidence for later review only')
            result['ready_for_phase5_review']=all(g['passed'] for g in gates)
            result['decision']='REVIEW_ELIGIBLE' if result['ready_for_phase5_review'] else 'CONTINUE_PAPER_OR_REVISE'
            result['report_hash']=digest(result);return result

    def reviews(self,identifier):
        with self.lock:
            self._load(identifier)
            items=[json.loads(row[0]) for row in self.db.execute('SELECT document FROM reviews WHERE portfolio_id=? ORDER BY rowid DESC LIMIT 100',(identifier,))]
            for item in items:
                check=dict(item);expected=check.pop('review_hash',None)
                if digest(check)!=expected:raise ValueError('Guardian review integrity failed')
            return items

    def review(self,identifier,body):
        payload=body.model_dump(mode='json');body_hash=digest(payload)
        with self.transaction():
            previous=self.db.execute('SELECT body_hash,document FROM reviews WHERE portfolio_id=? AND request_id=?',(identifier,body.request_id)).fetchone()
            if previous:
                if previous[0]!=body_hash:raise ValueError('Review request id is already bound to different inputs')
                return json.loads(previous[1])
            report=self.report(identifier)
            if body.evidence_hash!=report['evidence_hash']:raise ValueError('Review evidence changed; refresh and inspect the current report')
            if not aware(self.now())<aware(body.expires_at)<=aware(self.now())+timedelta(days=30):raise ValueError('Review expiry must be in the next 30 days')
            if body.decision=='APPROVE_REVIEW' and not report['technical_ready_for_review']:raise ValueError('Technical qualification gates must pass before approval for later review')
            item=dict(payload,id=uuid.uuid4().hex,portfolio_id=identifier,recorded_at=self.now(),identity_assurance='LOCAL_OWNER_ASSERTION',license_verified=False,execution_authority=False)
            item['review_hash']=digest(item)
            self.db.execute('INSERT INTO reviews VALUES(?,?,?,?,?)',(item['id'],identifier,body.request_id,body_hash,canonical(item)))
            doc,state=self._load(identifier);runtime=ForwardReplay(doc,state)
            runtime.event('GUARDIAN_REVIEW',body.decision,review=item)['time']=item['recorded_at']
            self._commit(doc,runtime)
            return item

    def export(self,identifier):
        with self.lock:
            doc,state=self._load(identifier)
            result=dict(document=doc,report=self.report(identifier),positions=ForwardReplay(doc,state).final_positions(),incidents=self.incidents(identifier),jobs=self.jobs(identifier),reviews=self.reviews(identifier),drills=self.drills(identifier),audit=self.audit(identifier),
                events=self.events(identifier,limit=500),event_count=state['sequence'],events_complete=state['sequence']<=500,
                complete_event_access='/api/forward/portfolios/'+identifier+'/events?after=0&limit=500',live_orders_enabled=False)
            result['export_hash']=digest(result);return result

    def drills(self,identifier):
        with self.lock:return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM drills WHERE portfolio_id=? ORDER BY rowid DESC LIMIT 20',(identifier,))]

    def run_drills(self,identifier,request_id):
        with self.lock:
            self._load(identifier)
            previous=[d for d in self.drills(identifier) if d['request_id']==request_id]
            if previous:return previous[0]
            from .drills import run_drills
            result=run_drills(self,identifier)
            result.update(id=uuid.uuid4().hex,portfolio_id=identifier,request_id=request_id,completed_at=self.now(),build=CURRENT_BUILD)
            with self.transaction():self.db.execute('INSERT INTO drills VALUES(?,?,?)',(result['id'],identifier,canonical(result)))
            return result

    def _research_input_status(self):
        inputs=getattr(self,'intelligence',None)
        result=dict(fii_dii='NOT_CONNECTED',macro='NOT_CONNECTED',iv_skew_feed='NOT_CONNECTED',option_greeks='ESTIMATED_FROM_BOUND_QUOTES')
        if inputs is not None:
            status=inputs.status();current=status['current_features']
            if any(name.startswith(('fii.','dii.')) for name in current):result['fii_dii']='DATED_OFFICIAL_REPORTS'
            if any(name.startswith('macro.') for name in current):result['macro']='DATED_OFFICIAL_STATISTICS'
            if any(name.startswith('iv.') for name in current):result['iv_skew_feed']='ESTIMATED_KITE_SURFACE'
        return result

    def status(self):
        with self.lock:
            at=aware(self.now());healthy=self.last_error is None and (self.last_tick is None or (at-aware(self.last_tick)).total_seconds()<=30)
            jobs=self.jobs();reserved=sum(json.loads(r[0]).get('reserved_tokens',0) for r in self.db.execute("SELECT document FROM jobs WHERE kind='AI'"))
            return dict(mode='FORWARD_PAPER_ONLY',live_orders_enabled=False,forge=dict(name='Centaur Strategy Forge',positioning='Compliance-first generative wealth infrastructure',current_stage='SANDBOX',guardian='LOCAL_OWNER_ASSERTION',regulatory_status='NOT_VERIFIED',customer_execution_enabled=False,research_inputs=self._research_input_status(),commercial_targets=dict(community_users=1000,scale_users=10000),automation_share_pct=None),build=CURRENT_BUILD,worker=dict(healthy=healthy,last_tick=self.last_tick,error=self.last_error),audit=self.last_audit_result,
                portfolio_count=self.db.execute('SELECT count(*) FROM portfolios').fetchone()[0],candle_request_budget_per_minute=30,provider=self.provider.status(),monitoring=self.monitoring(),reserved_output_tokens=reserved,
                monitoring_suspended=bool((row:=self.db.execute("SELECT document FROM settings WHERE name='ai_periodic_suspended'").fetchone()) and json.loads(row[0])),pending_jobs=sum(j['status'] in {'QUEUED','RUNNING'} for j in jobs),open_incidents=self.db.execute("SELECT count(*) FROM incidents WHERE status!='RESOLVED'").fetchone()[0],
                notifications=[json.loads(r[0]) for r in self.db.execute('SELECT document FROM notifications ORDER BY rowid DESC LIMIT 30')],notification_policy=self.alert_policy(),notification_provider=self.alert_provider.status(),external_notifications='TELEGRAM_ENABLED' if self.alert_policy()['enabled'] else 'DISABLED')

    def close(self):
        self.closing=True
        self.history_pool.shutdown(wait=True,cancel_futures=True);self.warmup_pool.shutdown(wait=True,cancel_futures=True);self.ai_pool.shutdown(wait=True,cancel_futures=True);self.alert_pool.shutdown(wait=True,cancel_futures=True)
        with self.lock:self.db.close()

    def alert_policy(self):
        row=self.db.execute("SELECT document FROM settings WHERE name='alert_policy'").fetchone()
        return json.loads(row[0]) if row else dict(enabled=False,consent=False,daily_limit=50)

    def configure_alerts(self,body):
        with self.transaction():
            if self.alert_futures:raise ValueError('Wait for pending delivery before replacing notification credentials')
            self.private_config.save_alerts(body.bot_token.get_secret_value(),body.chat_id.get_secret_value())
            self.alert_provider.token=body.bot_token.get_secret_value();self.alert_provider.chat=body.chat_id.get_secret_value()
            policy=dict(enabled=body.enabled,consent=body.consent,daily_limit=body.daily_limit,updated_at=self.now())
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('alert_policy',canonical(policy)))
            return dict(saved=True,policy=policy,provider=self.alert_provider.status(),credential_values_returned=False)

    def retry_notification(self,identifier,body):
        with self.transaction():
            row=self.db.execute('SELECT document FROM notifications WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Notification not found')
            item=json.loads(row[0]);policy=self.alert_policy()
            if not policy['enabled'] or not policy['consent'] or not self.alert_provider.status()['configured']:raise ValueError('Configure and authorize external delivery first')
            if item['status']=='SENT':raise ValueError('Notification already delivered; an idempotent retry cannot resend it')
            if item['status']=='QUEUED':return item
            if item['status']=='ATTEMPTING':raise ValueError('Delivery is already in progress')
            if item['status']=='FAILED_UNCERTAIN' and not body.acknowledge_possible_duplicate:raise ValueError('Explicitly acknowledge that an uncertain delivery retry may duplicate a message')
            item.update(status='QUEUED',external_delivery='TELEGRAM',error=None)
            self.db.execute('UPDATE notifications SET document=? WHERE id=?',(canonical(item),identifier));return item

    def _notifications_tick(self):
        # An outbox attempt is committed before any network side effect. Completion is
        # committed before removing its future, so a portfolio rollback cannot resend it.
        finished=[]
        with self.transaction():
            for identifier,future in list(self.alert_futures.items()):
                if not future.done():continue
                item=json.loads(self.db.execute('SELECT document FROM notifications WHERE id=?',(identifier,)).fetchone()[0])
                try:item.update(status='SENT',delivery=future.result(),completed_at=self.now(),error=None)
                except Exception:item.update(status='FAILED_UNCERTAIN',completed_at=self.now(),error='External delivery failed or is uncertain; no automatic retry')
                self.db.execute('UPDATE notifications SET document=? WHERE id=?',(canonical(item),identifier));finished.append(identifier)
        for identifier in finished:self.alert_futures.pop(identifier,None)
        pending=None
        with self.transaction():
            policy=self.alert_policy()
            if self.alert_futures or not policy['enabled'] or not policy['consent'] or not self.alert_provider.status()['configured']:return
            rows=self.db.execute('SELECT document FROM notifications ORDER BY rowid').fetchall();items=[json.loads(r[0]) for r in rows]
            attempted=sum(attempt[:10]==self.now()[:10] for item in items for attempt in item.get('attempts',[]))
            for item in items:
                if item['status'] not in {'QUEUED','BUDGET_BLOCKED'}:continue
                if attempted>=policy['daily_limit']:
                    item['status']='BUDGET_BLOCKED';self.db.execute('UPDATE notifications SET document=? WHERE id=?',(canonical(item),item['id']));continue
                item.setdefault('attempts',[]).append(self.now());item.update(status='ATTEMPTING',destination_hash=digest(self.alert_provider.chat),policy_hash=digest(policy))
                self.db.execute('UPDATE notifications SET document=? WHERE id=?',(canonical(item),item['id']));pending=item;break
        if pending:
            try:self.alert_futures[pending['id']]=self.alert_pool.submit(self.alert_provider.send,pending['text'])
            except RuntimeError:
                with self.transaction():
                    pending.update(status='FAILED_UNCERTAIN',error='Delivery worker did not confirm completion; explicit review required')
                    self.db.execute('UPDATE notifications SET document=? WHERE id=?',(canonical(pending),pending['id']))

    def has_exposure(self):
        with self.lock:
            return any(entry['state']['positions'] or any(o['status'] in ACTIVE for o in entry['state']['orders'].values()) for entry in self._states())

    def pause_for_credentials(self):
        with self.transaction():
            for entry in self._states():
                if entry['state']['forward']['retired']:continue
                runtime=ForwardReplay(entry['document'],entry['state']);runtime.state['clock']=self.now()
                runtime.pause('Market credentials changed; verify the new connection and resume explicitly')
                self._commit(entry['document'],runtime,bump_revision=True)
