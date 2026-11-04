"""Durable, bounded research orchestration. Approval only creates a paper review packet."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timezone
import copy
import json
from pathlib import Path
import sqlite3
import statistics
import threading
import uuid

from .provider import OpenAIProvider,ModelError,PROMPT_VERSION
from .schemas import Study,Candidate,AIProposal
from ..research.catalog import canonical,digest,aware
from ..research.schemas import Run,Strategy
from ..research.signals import capabilities
from ..research.metrics import deflated_sharpe
from ..research.jobs import CURRENT_BUILD
from ..research.fixtures import templates

ACTIVE={'QUEUED','RUNNING','PAUSED'}

def now(): return datetime.now(timezone.utc).isoformat()

class StrategyLab:
    def __init__(self,root,research,provider=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.research=research;self.provider=provider or OpenAIProvider();self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/'registry.sqlite3',timeout=15,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,study_id TEXT NOT NULL,status TEXT NOT NULL,document TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS versions_study ON versions(study_id);
            CREATE TABLE IF NOT EXISTS generations(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exposures(lineage TEXT PRIMARY KEY,study_id TEXT NOT NULL,version_id TEXT NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS packets(id TEXT PRIMARY KEY,version_id TEXT NOT NULL,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(sequence INTEGER PRIMARY KEY,document TEXT NOT NULL);''')
        self.audit_ok=self.verify_audit()['ok'];self.closing=False;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-model');self.futures={}
        if self.audit_ok:
            with self.transaction():
                for item in self._list('generations'):
                    if item['status'] in {'QUEUED','RUNNING','CANCEL_REQUESTED'}:
                        item.update(status='INTERRUPTED',error='Process restarted; provider usage may have occurred; explicit new request required',completed=now())
                        self._save('generations',item);self._event('GENERATION_INTERRUPTED',item['id'],{})
                for item in self._list('evaluations'):
                    if item['status'] in {'QUEUED','RUNNING'}:
                        item.update(status='PAUSED',error='Process restarted; resume explicitly against the same code build')
                        self._save('evaluations',item);self._event('EVALUATION_PAUSED',item['id'],{})

    @contextmanager
    def transaction(self):
        with self.lock:
            name='lab_'+uuid.uuid4().hex
            self.db.execute('SAVEPOINT '+name)
            try:
                yield
            except BaseException:
                self.db.execute('ROLLBACK TO '+name);self.db.execute('RELEASE '+name)
                raise
            else: self.db.execute('RELEASE '+name)

    def _writeable(self):
        if not self.verify_audit()["ok"]: self.audit_ok=False
        if self.closing or not self.audit_ok: raise ValueError('Registry is closed or its audit integrity check failed; writes are disabled')

    def _list(self,table):
        # table is always an internal literal, never API input.
        return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM '+table+' ORDER BY rowid DESC')]

    def _get(self,table,identifier):
        row=self.db.execute('SELECT document FROM '+table+' WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError(table.rstrip('s').capitalize()+' not found')
        return json.loads(row[0])

    def _save(self,table,item):
        if table in {'studies','packets'}:
            if table=='packets': self.db.execute('INSERT INTO packets VALUES(?,?,?)',(item['id'],item['version_id'],canonical(item)))
            else: self.db.execute('INSERT OR REPLACE INTO studies VALUES(?,?)',(item['id'],canonical(item)))
        else:
            self.db.execute('INSERT OR REPLACE INTO '+table+' VALUES(?,?,?)',(item['id'],item['request_id'],canonical(item)))

    def _event(self,kind,identifier,details):
        last=self.db.execute('SELECT sequence,document FROM audit ORDER BY sequence DESC LIMIT 1').fetchone()
        seq=last['sequence']+1 if last else 1;previous=json.loads(last['document'])['hash'] if last else '0'*64
        item=dict(sequence=seq,time=now(),kind=kind,object_id=identifier,details=details,previous_hash=previous)
        item['hash']=digest(item)
        self.db.execute('INSERT INTO audit VALUES(?,?)',(seq,canonical(item)))

    def verify_audit(self):
        with self.lock:
            previous='0'*64;count=0
            try:
                for row in self.db.execute('SELECT sequence,document FROM audit ORDER BY sequence'):
                    item=json.loads(row['document']);expected=item.pop('hash',None);count+=1
                    if item['previous_hash']!=previous or item['sequence']!=count or row['sequence']!=count or digest(item)!=expected:
                        return dict(ok=False,checked=count,reason='Audit chain mismatch')
                    previous=expected
            except (ValueError,TypeError,KeyError,AttributeError):
                return dict(ok=False,checked=count,reason='Malformed audit evidence')
            return dict(ok=True,checked=count,head=previous,note='Local hash chain detects modification; it is not externally anchored or tamper-proof')

    def status(self):
        with self.lock:
            return dict(mode='AI_RESEARCH_PAPER_ONLY',provider=self.provider.status(),audit=self.verify_audit(),
                studies=self.db.execute('SELECT count(*) FROM studies').fetchone()[0],versions=self.db.execute('SELECT count(*) FROM versions').fetchone()[0],
                live_orders_enabled=False,forward_trading_enabled=False,
                limitations=['Real datasets and economic assumptions need operator review','Historical LLM knowledge can contain future events',
                    'Paper approval creates evidence only; Phase 4 activation is separate','Unsupported strategy families remain blocked'])

    def studies(self):
        with self.lock: return self._list('studies')[:200]

    def study(self,identifier):
        with self.lock: return self._get('studies',identifier)

    def _open_study(self,identifier):
        item=self._get('studies',identifier)
        if item['finalist_version_id']: raise ValueError('Study is frozen after finalist selection; no new candidates or model feedback are allowed')
        if self.db.execute('SELECT 1 FROM exposures WHERE lineage=?',(item['lineage'],)).fetchone():
            raise ValueError('This dataset lineage has an exposed holdout; use genuinely untouched data for another study')
        return item

    def create_study(self,body):
        config=Study.model_validate(body).model_dump(mode='json')
        if config['train_pct']+config['validation_pct']>90: raise ValueError('Reserve at least ten percent for the holdout')
        dataset=self.research.catalog.verify(config['dataset_id'])
        if dataset['status']!='READY': raise ValueError('A study requires a READY immutable dataset')
        rows=self.research.catalog.rows(dataset['id']);times=sorted({r['timestamp'] for r in rows})
        n=len(times);train=int(n*config['train_pct']/100);validation=int(n*config['validation_pct']/100);gap=config['embargo_bars']
        if train<40 or validation-gap<max(10,2*gap+10) or n-train-validation-gap<10: raise ValueError('Need at least 40 training, 10 validation and 10 holdout observations after embargo')
        windows=dict(TRAIN=dict(start=times[0],end=times[train-1]),VALIDATION=dict(start=times[train+gap],end=times[train+validation-1]),
                     HOLDOUT=dict(start=times[train+validation+gap],end=times[-1]))
        available={r['instrument_id'] for r in rows if r['timestamp']<=windows['TRAIN']['end']}
        selected=config['instrument_ids'] or sorted(available)
        if not set(selected)<=available or len(selected)>50: raise ValueError('Select at most fifty instruments observed during training')
        lineage=dataset['id'];seen=set()
        while self.research.catalog.get(lineage)['manifest']['parent_dataset_id']:
            if lineage in seen: raise ValueError('Dataset lineage cycle')
            seen.add(lineage);lineage=self.research.catalog.get(lineage)['manifest']['parent_dataset_id']
        root_dataset_id=lineage;lineage=self.research.catalog.get(root_dataset_id)['parquet_hash']
        item=dict(id=uuid.uuid4().hex,created=now(),config=config,windows=windows,lineage=lineage,root_dataset_id=root_dataset_id,
            training_instruments=selected,finalist_version_id=None,data_review=None,version_attempts=0,generation_attempts=0,
            reserved_output_tokens=0,reserved_runs=0,protocol_hash=digest(dict(config=config,windows=windows,selected=selected)),build=CURRENT_BUILD)
        # No holdout prices, sample returns or future corporate actions are included in this immutable context.
        manifest=dataset['manifest'];cutoff=windows['TRAIN']['end'];context_items=[]
        for original in manifest['instruments']:
            if original['id'] not in selected: continue
            i={k:original[k] for k in ('id','symbol','kind','underlying','exchange','lot_size','tick_size','expiry','strike','option_type','settlement')}
            history=[r for r in rows if r['instrument_id']==i['id'] and r['timestamp']<=cutoff]
            known_terms=[t for t in original['terms'] if aware(t['effective_at'])<=aware(cutoff)]
            if known_terms: i.update({k:known_terms[-1][k] for k in ('lot_size','tick_size')})
            i['training_observations']=[dict(**{k:r[k] for k in ('timestamp','close','volume')},features={name:value for name,value in json.loads(r.get('features_json') or '{}').items() if aware(value['available_at'])<=aware(r['timestamp']) and (not value.get('valid_until') or aware(value['valid_until'])>=aware(r['timestamp']))}) for r in history[-12:]]
            context_items.append(i)
        item['training_context']=dict(protocol_hash=item['protocol_hash'],training_end=cutoff,frequency=manifest['frequency'],
            source_kind=manifest['source_kind'],instruments=context_items,
            restrictions=['Only training data is supplied','No tools or generated-code execution','No claimed prospective performance'])
        if len(canonical(item['training_context']))>120000: raise ValueError('Training context is too large; reduce selected instruments')
        with self.transaction():
            self._writeable()
            if self.db.execute('SELECT 1 FROM exposures WHERE lineage=?',(lineage,)).fetchone(): raise ValueError('This dataset lineage already has an exposed holdout')
            if any(existing['lineage']==lineage for existing in self._list('studies')): raise ValueError('A dataset lineage already has a study protocol; add versions within that study')
            self._save('studies',item);self._event('STUDY_CREATED',item['id'],dict(protocol_hash=item['protocol_hash'],dataset_id=dataset['id']))
        return item

    def references(self,study_id):
        study=self.study(study_id);dataset=self.research.catalog.get(study['config']['dataset_id'])
        result=[]
        for template in templates(dataset):
            for spec in template['config']['strategies']:
                required=set(spec['signal_instruments'])|{l['instrument_id'] for l in spec['legs'] if l['instrument_id']}
                if required<=set(study['training_instruments']): result.append(dict(id=template['id'],name=template['name'],strategy=spec))
        return result

    def versions(self,study_id=None):
        with self.lock:
            rows=self.db.execute('SELECT id FROM versions'+(' WHERE study_id=?' if study_id else '')+' ORDER BY rowid DESC LIMIT 200',(study_id,) if study_id else ()).fetchall()
            return [self.version(r['id']) for r in rows]

    def version(self,identifier):
        with self.lock:
            row=self.db.execute('SELECT document,status FROM versions WHERE id=?',(identifier,)).fetchone()
            if not row: raise ValueError('Candidate version not found')
            item=json.loads(row['document']);core={k:v for k,v in item.items() if k not in {'id','created'}}
            if digest(core)!=identifier: raise ValueError('Immutable candidate hash mismatch')
            item['status']=row['status'];return item

    def _set_state(self,identifier,status):
        self.db.execute('UPDATE versions SET status=? WHERE id=?',(status,identifier))

    def add_version(self,body,origin='MANUAL',provenance=None):
        body=Candidate.model_validate(body).model_dump(mode='json')
        with self.transaction():
            self._writeable();study=self._open_study(body['study_id'])
            parent=body['parent_version_id']
            if parent and self.version(parent)['study_id']!=study['id']: raise ValueError('A revision must belong to its parent study')
            core=dict(**body,implementation_build=CURRENT_BUILD,origin=origin,provenance=provenance or dict(authoring='MANUAL',live_provider_response=False))
            identifier=digest(core)
            if self.db.execute('SELECT 1 FROM versions WHERE id=?',(identifier,)).fetchone(): return self.version(identifier)
            pending=sum(g['config']['study_id']==study['id'] and g['status'] in {'QUEUED','RUNNING','CANCEL_REQUESTED'} for g in self._list('generations')) if origin=='MANUAL' else 0
            if study['version_attempts']+pending>=study['config']['max_versions']: raise ValueError('Study candidate-version budget exhausted')
            study['version_attempts']+=1;self._save('studies',study)
            required=set(body['strategy']['signal_instruments'])|{l['instrument_id'] for l in body['strategy']['legs'] if l['instrument_id']}
            if not required<=set(study['training_instruments']): raise ValueError('Candidate references instruments outside the declared training universe')
            config=self._run_config(study,body['strategy'],'TRAIN',study['windows']['TRAIN'])
            check=capabilities(self.research.catalog.get(study['config']['dataset_id']),config)
            item=dict(core,id=identifier,created=now())
            self.db.execute('INSERT INTO versions VALUES(?,?,?,?)',(identifier,study['id'],'VALIDATED' if check['runnable'] else 'BLOCKED',canonical(item)))
            self._event('CANDIDATE_CREATED',identifier,dict(origin=origin,parent=parent,spec_hash=digest(body['strategy']),capability=check))
            return self.version(identifier)

    def generations(self):
        with self.lock: return self._list('generations')[:200]

    def generate(self,body):
        config=body.model_dump(mode='json') if hasattr(body,'model_dump') else dict(body)
        with self.transaction():
            self._writeable()
            existing=self.db.execute('SELECT document FROM generations WHERE request_id=?',(config['request_id'],)).fetchone()
            if existing:
                item=json.loads(existing[0])
                if item['request_hash']!=digest(config): raise ValueError('Request id already binds different generation inputs')
                return item
            study=self._open_study(config['study_id'])
            if config.get('parent_version_id') and self.version(config['parent_version_id'])['study_id']!=study['id']: raise ValueError('Parent version belongs to another study')
            pending=sum(g['config']['study_id']==study['id'] and g['status'] in {'QUEUED','RUNNING','CANCEL_REQUESTED'} for g in self._list('generations'))
            if study['version_attempts']+pending>=study['config']['max_versions']: raise ValueError('Candidate-version reservations exhausted')
            if not self.provider.status()['configured']: raise ValueError('Configure the AI provider first; no request was sent')
            if study['generation_attempts']>=study['config']['max_generations']: raise ValueError('Study generation-request budget exhausted')
            if study['reserved_output_tokens']+study['config']['max_output_tokens']>study['config']['max_ai_output_tokens']: raise ValueError('Study reserved AI output-token budget exhausted')
            if sum(g['status'] in {'QUEUED','RUNNING','CANCEL_REQUESTED'} for g in self._list('generations'))>=8: raise ValueError('AI queue is full')
            study['generation_attempts']+=1;study['reserved_output_tokens']+=study['config']['max_output_tokens'];self._save('studies',study)
            context=copy.deepcopy(study['training_context'])
            if config.get('parent_version_id'):
                parent=self.version(config['parent_version_id']);context['parent_candidate']={k:parent[k] for k in ('strategy','hypothesis','risks','assumptions')}
            item=dict(id=uuid.uuid4().hex,request_id=config['request_id'],request_hash=digest(config),config=config,status='QUEUED',created=now(),
                version_id=None,error=None,evidence=None,context=context,context_hash=digest(context),provider=self.provider.status(),build=CURRENT_BUILD)
            self._save('generations',item);self._event('GENERATION_QUEUED',item['id'],dict(study_id=study['id'],context_hash=item['context_hash']))
        self.futures[item['id']]=self.pool.submit(self._generate,item['id'])
        return item

    def _generate(self,identifier):
        with self.transaction():
            item=self._get('generations',identifier)
            if item['status']=='CANCELLED': return
            item['status']='RUNNING';self._save('generations',item)
            study=self._get('studies',item['config']['study_id'])
        evidence=None
        try:
            proposal,evidence=self.provider.request(item['config']['idea'],item['context'],study['config']['max_output_tokens'])
            proposal=AIProposal.model_validate(proposal).model_dump(mode='json')
            spec=Strategy.model_validate_json(proposal['strategy_json']).model_dump(mode='json')
            with self.transaction():
                current=self._get('generations',identifier)
                if current['status']=='CANCEL_REQUESTED' or self.closing:
                    current.update(status='CANCELLED',evidence=evidence,completed=now());self._save('generations',current);return
                version=self.add_version(dict(study_id=study['id'],strategy=spec,hypothesis=proposal['hypothesis'],risks=proposal['risks'],
                    assumptions=proposal['assumptions'],parent_version_id=item['config']['parent_version_id']),origin='AI',
                    provenance=dict(generation_id=identifier,**evidence))
                current.update(status='SUCCEEDED',version_id=version['id'],evidence=evidence,completed=now());self._save('generations',current)
                self._event('GENERATION_SUCCEEDED',identifier,dict(version_id=version['id']))
        except Exception as exc:
            if evidence is None and isinstance(exc,ModelError): evidence=exc.evidence
            with self.transaction():
                current=self._get('generations',identifier)
                current.update(status='CANCELLED' if current['status']=='CANCEL_REQUESTED' else 'FAILED',completed=now(),evidence=evidence,
                    error=str(exc) if isinstance(exc,ModelError) else 'Generated candidate failed schema, capability, study-freeze or budget validation; original proposal retained in evidence')
                self._save('generations',current);self._event('GENERATION_FAILED',identifier,dict(error=current['error']))

    def cancel_generation(self,identifier):
        with self.transaction():
            self._writeable();item=self._get('generations',identifier)
            if item['status']=='QUEUED': item['status']='CANCELLED'
            elif item['status']=='RUNNING': item['status']='CANCEL_REQUESTED'
            self._save('generations',item);self._event('GENERATION_CANCELLED',identifier,dict(status=item['status']))
            return item

    def _run_config(self,study,strategy,label,window,training_end=None):
        train=study['windows']['TRAIN'];end=training_end or train['end']
        model_training_end=train['start']
        if label=='TRAIN' and strategy['family']=='LINEAR_MODEL':
            dates=sorted({r['timestamp'] for r in self.research.catalog.rows(study['config']['dataset_id']) if train['start']<=r['timestamp']<=train['end']})
            middle=len(dates)//2;model_training_end=dates[middle-1]
            window=dict(start=dates[middle+study['config']['embargo_bars']],end=train['end'])
        windows=[dict(label='TRAIN',**train)] if label=='TRAIN' else [dict(label='TRAIN',start=train['start'],end=end),dict(label='HOLDOUT' if label=='HOLDOUT' else 'VALIDATION',**window)]
        return Run.model_validate(dict(dataset_id=study['config']['dataset_id'],name='Phase 3 '+label,capital=study['config']['capital'],strategies=[strategy],
            risk=study['config']['risk'],execution=study['config']['execution'],seed=42,start=window['start'],end=window['end'],training_end=end if label!='TRAIN' else model_training_end,
            windows=windows if label!='TRAIN' else [],embargo_bars=study['config']['embargo_bars'] if label!='TRAIN' else 0,
            benchmark_id=next((i for i in study['training_instruments'] if i in strategy['signal_instruments']),None),experiment_id=study['id'])).model_dump(mode='json')

    def evaluations(self,version_id=None):
        with self.lock:
            result=self._list('evaluations')
            return [r for r in result if not version_id or r['version_id']==version_id][:200]

    def _evaluation(self,version,request_id,holdout=False):
        study=self._get('studies',version['study_id']);spec=version['strategy'];mode='HOLDOUT' if holdout else 'SCREEN'
        request_hash=digest(dict(version_id=version['id'],mode=mode))
        row=self.db.execute('SELECT document FROM evaluations WHERE request_id=?',(request_id,)).fetchone()
        if row:
            item=json.loads(row[0])
            if item['request_hash']!=request_hash: raise ValueError('Request id already binds another evaluation')
            return item
        if any(e['version_id']==version['id'] and e['mode']==mode for e in self._list('evaluations')): raise ValueError('This immutable candidate already has this evaluation; resume or inspect it')
        if version['status'] in {'BLOCKED','REJECTED','RETIRED'}: raise ValueError('Candidate is blocked, rejected or retired')
        if sum(e['status'] in {'QUEUED','RUNNING','PAUSED'} for e in self._list('evaluations'))>=8: raise ValueError('Evaluation queue is full')
        jobs=[]
        def add(label,config): jobs.append(dict(label=label,config=config,config_hash=digest(config),run_id=None,status='PENDING'))
        if holdout: add('HOLDOUT',self._run_config(study,spec,'HOLDOUT',study['windows']['HOLDOUT']))
        else:
            train=self._run_config(study,spec,'TRAIN',study['windows']['TRAIN']);add('TRAIN',train);add('REPRODUCE',copy.deepcopy(train))
            base=self._run_config(study,spec,'VALIDATION',study['windows']['VALIDATION']);add('VALIDATION',base)
            stress=copy.deepcopy(base);stress['name']='Phase 3 COST_STRESS'
            stress['execution']['spread_bps']=min(1000,stress['execution']['spread_bps']*2+1)
            stress['risk']['slippage_bps']=min(1000,stress['risk']['slippage_bps']*2+1)
            stress['execution']['latency_seconds']=min(86400,stress['execution']['latency_seconds']+60)
            stress['execution']['participation_pct']=max(.01,stress['execution']['participation_pct']/2)
            add('COST_STRESS',Run.model_validate(stress).model_dump(mode='json'))
            dates=sorted({r['timestamp'] for r in self.research.catalog.rows(study['config']['dataset_id']) if study['windows']['VALIDATION']['start']<=r['timestamp']<=study['windows']['VALIDATION']['end']})
            middle=len(dates)//2
            for i,(start,end) in enumerate(((dates[0],dates[middle-1]),(dates[min(len(dates)-2,middle+study['config']['embargo_bars'])],dates[-1]))):
                preceding=study['windows']['TRAIN']['end'] if i==0 else dates[middle-1]
                add('WALK_FORWARD_'+str(i+1),self._run_config(study,spec,'WALK_FORWARD',dict(start=start,end=end),preceding))
            for scale in (.8,1.2):
                sensitivity=copy.deepcopy(base);sensitivity['name']='Phase 3 SENSITIVITY '+str(scale)
                sensitivity['strategies'][0]['stop_pct']=max(.01,min(100,spec['stop_pct']*scale))
                add('SENSITIVITY_'+str(scale),Run.model_validate(sensitivity).model_dump(mode='json'))
        if study['reserved_runs']+len(jobs)+(0 if holdout else 1)>study['config']['max_runs']: raise ValueError('Study replay-run budget exhausted')
        study['reserved_runs']+=len(jobs);self._save('studies',study)
        item=dict(id=uuid.uuid4().hex,request_id=request_id,request_hash=request_hash,version_id=version['id'],study_id=study['id'],mode=mode,
            status='QUEUED',created=now(),jobs=jobs,summary=None,build=CURRENT_BUILD,error=None)
        self._save('evaluations',item);self._set_state(version['id'],'HOLDOUT_RUNNING' if holdout else 'EVALUATING')
        self._event('EVALUATION_QUEUED',item['id'],dict(mode=mode,version_id=version['id'],run_budget=len(jobs)))
        return item

    def evaluate(self,version_id,request_id):
        with self.transaction():
            self._writeable();version=self.version(version_id);self._open_study(version['study_id'])
            return self._evaluation(version,request_id)

    def seal(self,version_id,request_id):
        with self.transaction():
            self._writeable();version=self.version(version_id);study=self._get('studies',version['study_id'])
            if study['finalist_version_id']:
                if study['finalist_version_id']!=version_id: raise ValueError('A different finalist already consumed this study holdout')
                return next(e for e in self._list('evaluations') if e['version_id']==version_id and e['mode']=='HOLDOUT')
            screen=next((e for e in self._list('evaluations') if e['version_id']==version_id and e['mode']=='SCREEN' and e['status']=='COMPLETED'),None)
            if not screen: raise ValueError('Complete candidate screening before selecting the finalist')
            if any(g['config']['study_id']==study['id'] and g['status'] in {'QUEUED','RUNNING','CANCEL_REQUESTED'} for g in self._list('generations')):
                raise ValueError('Finish or cancel model requests before freezing the study')
            if self.db.execute('SELECT 1 FROM exposures WHERE lineage=?',(study['lineage'],)).fetchone(): raise ValueError('This dataset lineage holdout is already consumed')
            item=self._evaluation(version,request_id,True)
            study=self._get('studies',study['id']);study['finalist_version_id']=version_id;self._save('studies',study)
            exposure=dict(study_id=study['id'],version_id=version_id,reserved_at=now(),window=study['windows']['HOLDOUT'],evaluation_id=item['id'])
            self.db.execute('INSERT INTO exposures VALUES(?,?,?,?)',(study['lineage'],study['id'],version_id,canonical(exposure)))
            self._event('HOLDOUT_CONSUMED',study['id'],exposure)
            return item

    def tick(self):
        with self.transaction():
            if self.closing or not self.audit_ok: return
            if not self.verify_audit()['ok']:
                self.audit_ok=False;return
            for item in self._list('evaluations'):
                if item['status'] not in {'QUEUED','RUNNING'}: continue
                try:
                    if item['build']['code_hash']!=CURRENT_BUILD['code_hash']: raise ValueError('Code changed; this evaluation cannot be resumed on another build')
                    item['status']='RUNNING'
                    for job in item['jobs']:
                        if job['run_id']: job['status']=self.research.get(job['run_id'])['status']
                    if all(j['status'] in {'COMPLETED','FAILED','CANCELLED'} for j in item['jobs']):
                        self._finish(item);continue
                    if not any(j['status'] in {'QUEUED','RUNNING','PAUSED'} for j in item['jobs']):
                        job=next(j for j in item['jobs'] if j['status']=='PENDING')
                        # The config hash is a durable dispatch key; recover a submitted run after a crash.
                        with self.research.catalog.lock:
                            rows=self.research.catalog.db.execute('SELECT id,document FROM runs').fetchall()
                        match=next((r['id'] for r in rows if json.loads(r['document'])['config_hash']==job['config_hash'] and
                            json.loads(r['document'])['config'].get('experiment_id')==item['study_id'] and
                            json.loads(r['document']).get('engine_hash')==item['build']['code_hash'] and
                            r['id'] not in {x['run_id'] for x in item['jobs'] if x is not job}),None)
                        run=self.research.get(match) if match else self.research.create(job['config'])
                        job.update(run_id=run['id'],status=run['status'])
                    self._save('evaluations',item)
                except Exception as exc:
                    item.update(status='FAILED',error=str(exc) if isinstance(exc,ValueError) else 'Evaluation orchestration failed; inspect retained worker evidence',completed=now())
                    self._save('evaluations',item);self._event('EVALUATION_FAILED',item['id'],dict(error=item['error']))

    def _finish(self,item):
        study=self._get('studies',item['study_id']);reports={};checks=[]
        for job in item['jobs']:
            if job['status']=='COMPLETED': reports[job['label']]=self.research.report(job['run_id'])
            else: checks.append(dict(name=job['label']+' completion',passed=False,reason=job['status']))
        for label,report in reports.items():
            if label.startswith(('COST_STRESS','WALK_FORWARD','SENSITIVITY')):
                checks.append(dict(name=label+' robustness',passed=report['diagnostics']['fills']>=study['config']['policy']['min_fills'] and report['performance']['max_drawdown_pct']<=study['config']['policy']['max_drawdown_pct'] and report['performance']['total_return_pct']>=study['config']['policy']['min_return_pct'],reason='Predeclared fill, drawdown and after-cost return filters'))
            checks.append(dict(name=label+' accounting',passed=bool(report['diagnostics']['ledger_reconciled'] and report['diagnostics']['attribution_reconciled'] and not report['diagnostics'].get('halt_reason') and not report['violations'] and not report['obligations']),reason='Ledger, attribution and obligations'))
        if item['mode']=='SCREEN':
            same='TRAIN' in reports and 'REPRODUCE' in reports and reports['TRAIN']['economic_hash']==reports['REPRODUCE']['economic_hash']
            checks.append(dict(name='Reproducible training replay',passed=same,reason='Identical code, configuration, seed, data and economic hash'))
        main=reports.get('HOLDOUT' if item['mode']=='HOLDOUT' else 'VALIDATION');policy=study['config']['policy']
        if main:
            p=main['performance'];d=main['diagnostics']
            for name,passed,reason in (
                ('Minimum evaluation sample',p.get('trading_days',0)>=policy['min_validation_days'],str(p.get('trading_days',0))+' trading days'),
                ('Minimum fill evidence',d['fills']>=policy['min_fills'],str(d['fills'])+' fill records'),
                ('Return threshold',p['total_return_pct']>=policy['min_return_pct'],format(p['total_return_pct'],'.4f')+'% after estimated costs'),
                ('Drawdown threshold',p['max_drawdown_pct']<=policy['max_drawdown_pct'],format(p['max_drawdown_pct'],'.4f')+'%'),
                ('Fresh final valuations',not main['account']['stale_instruments'],'Inspect final valuation timestamps')):
                checks.append(dict(name=name,passed=bool(passed),reason=reason))
        summaries={label:dict(performance=report['performance'],diagnostics=report['diagnostics'],economic_hash=report['economic_hash'],
                    assumptions=report['assumptions'],qualification=report['qualification']) for label,report in reports.items()}
        item.update(status='COMPLETED',completed=now(),summary=dict(checks=checks,technical_and_research_thresholds_passed=bool(main) and all(c['passed'] for c in checks),
            reports=summaries,live_orders_enabled=False,limitations=['Thresholds are declared research filters, not statistical proof of an edge','Independent data/model review and prospective evidence remain required']))
        self._save('evaluations',item)
        version=self.version(item['version_id'])
        if version['status'] not in {'RETIRED','REJECTED','PAPER_APPROVED'}: self._set_state(version['id'],'HOLDOUT_REVIEW' if item['mode']=='HOLDOUT' else 'SCREENED')
        self._event('EVALUATION_COMPLETED',item['id'],dict(passed=item['summary']['technical_and_research_thresholds_passed']))

    def control(self,identifier,action):
        with self.transaction():
            self._writeable();item=self._get('evaluations',identifier)
            if action=='RESUME' and item['build']['code_hash']!=CURRENT_BUILD['code_hash']: raise ValueError('Evaluation build mismatch; preserve this attempt and create a study on untouched data')
            if item['status'] in {'COMPLETED','CANCELLED'}: return item
            for job in item['jobs']:
                if not job['run_id']: continue
                status=self.research.get(job['run_id'])['status']
                if (action in {'PAUSE','CANCEL'} and status in {'QUEUED','RUNNING','PAUSED'}) or (action=='RESUME' and status=='PAUSED'):
                    self.research.control(job['run_id'],action)
            item['status']={'PAUSE':'PAUSED','RESUME':'QUEUED','CANCEL':'CANCELLED'}[action]
            self._save('evaluations',item);self._event('EVALUATION_CONTROL',identifier,dict(action=action));return item

    def data_review(self,study_id,body):
        with self.transaction():
            self._writeable();study=self._get('studies',study_id);review=body.model_dump(mode='json')
            study['data_review']=dict(review,reviewed_at=now());self._save('studies',study)
            self._event('DATA_REVIEW',study_id,study['data_review']);return study

    def evidence(self,version_id):
        version=self.version(version_id);study=self.study(version['study_id']);dataset=self.research.catalog.verify(study['config']['dataset_id'])
        evaluations=self.evaluations(version_id);blockers=[]
        if dataset['manifest']['source_kind']=='SYNTHETIC': blockers.append('Synthetic data cannot qualify a paper candidate')
        if not dataset['manifest']['rights_confirmed']: blockers.append('Historical data rights are unconfirmed')
        if not dataset['manifest']['point_in_time_universe']: blockers.append('Point-in-time universe evidence is incomplete')
        if not dataset['manifest']['corporate_actions_complete']: blockers.append('Corporate-action completeness is unconfirmed')
        if study['config']['execution']['bar_ambiguity']=='FAVORABLE_FIRST': blockers.append('Optimistic favorable-first fills are sensitivity evidence only and cannot qualify a paper candidate')
        review=study['data_review']
        if not review or not all(review[k] for k in ('source_and_rights','calendar_and_actions','charges_margin_and_settlement','execution_assumptions')):
            blockers.append('Operator data, calendar, charges, margin and execution review is incomplete')
        screen=next((e for e in evaluations if e['mode']=='SCREEN' and e['status']=='COMPLETED'),None)
        holdout=next((e for e in evaluations if e['mode']=='HOLDOUT' and e['status']=='COMPLETED'),None)
        if not screen or not screen['summary']['technical_and_research_thresholds_passed']: blockers.append('Screening thresholds or accounting checks did not pass')
        if not holdout or not holdout['summary']['technical_and_research_thresholds_passed']: blockers.append('Single-use holdout thresholds or accounting checks did not pass')
        if study['finalist_version_id']!=version_id: blockers.append('Candidate is not the frozen study finalist')
        # Search correction uses only comparable validation returns and retains all version attempts.
        sharpes=[];returns=[]
        for trial in self.evaluations():
            if trial['study_id']!=study['id'] or trial['mode']!='SCREEN' or not trial['summary']: continue
            for label,report in trial['summary']['reports'].items():
                if label!='VALIDATION' and not label.startswith('SENSITIVITY'): continue
                values=report['performance'].get('daily_returns',[])
                if trial['version_id']==version_id and label=='VALIDATION': returns=values
                if len(values)>1 and statistics.stdev(values): sharpes.append(statistics.mean(values)/statistics.stdev(values))
        capability=capabilities(dataset,self._run_config(study,version['strategy'],'TRAIN',study['windows']['TRAIN']))
        blockers.extend(capability['blockers'])
        return dict(version=version,study=study,dataset=dataset,evaluations=evaluations,capability=capability,approval_blockers=blockers,
            search_diagnostics=deflated_sharpe(returns,sharpes,max(study['version_attempts'],study['generation_attempts'])+2*sum(e['mode']=='SCREEN' for e in self.evaluations() if e['study_id']==study['id'])),
            explanations=[dict(statement='Candidate is a research hypothesis',source='version.hypothesis'),
                dict(statement='Reported performance is simulated after declared cost assumptions',source='evaluations[].summary.reports'),
                dict(statement='Historical LLM knowledge may include future events; prospective evidence remains necessary',source='phase-3 research protocol')],
            live_orders_enabled=False,forward_trading_enabled=False)

    def review(self,version_id,body):
        with self.transaction():
            self._writeable();version=self.version(version_id)
            if body.expected_status!=version['status']: raise ValueError('Candidate status changed; refresh before reviewing')
            if body.action=='APPROVE_PAPER':
                evidence=self.evidence(version_id)
                if evidence['approval_blockers']: raise ValueError('; '.join(evidence['approval_blockers']))
                if version['status'] not in {'HOLDOUT_REVIEW','PAPER_APPROVED'}: raise ValueError('Only a holdout-reviewed finalist can receive a new paper review packet')
                packet=dict(id=uuid.uuid4().hex,created=now(),version_id=version_id,study_id=version['study_id'],strategy=version['strategy'],
                    review=body.model_dump(mode='json'),data_review=evidence['study']['data_review'],model_provenance=version['provenance'],
                    evaluation_hashes={e['id']:digest(e) for e in evidence['evaluations']},dataset_provenance=dict(source=evidence['dataset']['manifest']['source'],raw_hash=evidence['dataset']['raw_hash'],parquet_hash=evidence['dataset']['parquet_hash']),protocol_hash=evidence['study']['protocol_hash'],dataset_id=evidence['dataset']['id'],
                    evaluation_ids=[e['id'] for e in evidence['evaluations']],mode='PAPER_REVIEW_PACKET',live_orders_enabled=False,
                    forward_trading_enabled=False,strategy_build=version['implementation_build'],review_build=CURRENT_BUILD,build=CURRENT_BUILD)
                packet['packet_hash']=digest(packet);self._save('packets',packet);state='PAPER_APPROVED'
            else:
                state='REJECTED' if body.action=='REJECT' else 'RETIRED'
                for evaluation in self.evaluations(version_id):
                    if evaluation['status'] in ACTIVE: self.control(evaluation['id'],'CANCEL')
            self._set_state(version_id,state);self._event('CANDIDATE_REVIEW',version_id,dict(review=body.model_dump(mode='json'),status=state))
            return self.version(version_id)

    def paper_packet(self,version_id):
        with self.lock:
            if not self.verify_audit()['ok']:raise ValueError('Candidate registry audit failed')
            version=self.version(version_id)
            if version['status']!='PAPER_APPROVED':raise ValueError('Candidate is not currently paper-approved')
            packets=[p for p in self._list('packets') if p['version_id']==version_id]
            if not packets:raise ValueError('Candidate has no immutable paper review packet')
            packet=packets[0];check=copy.deepcopy(packet);expected=check.pop('packet_hash',None)
            if digest(check)!=expected or digest(packet['strategy'])!=digest(version['strategy']):raise ValueError('Paper packet integrity failed')
            study=self.study(version['study_id'])
            if packet.get('study_id')!=study['id'] or packet.get('protocol_hash')!=study['protocol_hash'] or packet.get('data_review')!=study['data_review']:
                raise ValueError('Paper approval review changed; issue a fresh reviewed packet before activation')
            if packet.get('mode')!='PAPER_REVIEW_PACKET' or packet.get('live_orders_enabled') is not False:raise ValueError('Packet has no valid paper-only activation scope')
            return packet

    def audit_events(self):
        with self.lock: return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM audit ORDER BY sequence DESC LIMIT 200')]

    def export(self,version_id):
        with self.lock:
            result=self.evidence(version_id)
            result['generations']=[g for g in self._list('generations') if g['config']['study_id']==result['study']['id']]
            result['packets']=[p for p in self._list('packets') if p['version_id']==version_id]
            result['lineage_versions']=self.versions(result['study']['id'])
            scope={result['study']['id']}|{v['id'] for v in result['lineage_versions']}|{g['id'] for g in result['generations']}|{e['id'] for e in self.evaluations() if e['study_id']==result['study']['id']}
            result['audit_events']=[json.loads(r[0]) for r in self.db.execute('SELECT document FROM audit ORDER BY sequence') if json.loads(r[0])['object_id'] in scope]
            result['audit']=self.verify_audit();result['export_hash']=digest(result);return result

    def close(self):
        self.closing=True
        self.pool.shutdown(wait=True,cancel_futures=True)
        with self.lock: self.db.close()
