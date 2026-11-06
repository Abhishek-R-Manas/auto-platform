"""Bounded process workers, transactional checkpoints and immutable research reports."""
import copy
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import datetime, timezone, timedelta
import itertools
import hashlib
import fcntl
from importlib.metadata import version as package_version
import sys
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .catalog import Catalog, canonical, digest, aware
from .metrics import metrics, deflated_sharpe
from .replay import Replay
from .schemas import Run, VERSION
from .signals import capabilities

TERMINAL={'COMPLETED','FAILED','CANCELLED'}


def build_identity():
    source=Path(__file__).resolve().parents[1]
    code=hashlib.sha256()
    for path in sorted(source.rglob('*.py')):
        code.update(str(path.relative_to(source)).encode());code.update(path.read_bytes())
    return dict(version=VERSION,code_hash=code.hexdigest(),python=sys.version.split()[0],
        packages={name:package_version(name) for name in ('pyarrow','fastapi','pydantic','httpx')})


CURRENT_BUILD=build_identity()

def open_job(path):
    db=sqlite3.connect(path,timeout=15,check_same_thread=False)
    db.row_factory=sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS meta(id INTEGER PRIMARY KEY CHECK(id=1),document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY CHECK(id=1),document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),command TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY,time TEXT NOT NULL,kind TEXT NOT NULL,strategy TEXT,instrument TEXT,document TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_kind ON events(kind,sequence);
        CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report(id INTEGER PRIMARY KEY CHECK(id=1),document TEXT NOT NULL);''')
    return db


def metadata(db):
    row=db.execute('SELECT document FROM meta WHERE id=1').fetchone()
    return json.loads(row[0]) if row else {}


def save_meta(db,document):
    db.execute('INSERT OR REPLACE INTO meta VALUES(1,?)',(canonical(document),))


def worker_setup():
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','OPENAI_API_KEY','ARTHA_TELEGRAM_BOT_TOKEN','ARTHA_TELEGRAM_CHAT_ID'):
        os.environ.pop(name,None)


def execute(root,identifier):
    directory=Path(root)/'runs'/identifier
    with (directory/'worker.lock').open('a') as lease:
        try: fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return dict(id=identifier,status='LEASE_HELD')
        return _execute(root,identifier)


def _execute(root,identifier):
    # Arguments contain local artifact ids, never broker sessions or credentials.
    root=Path(root);path=root/'runs'/identifier/'run.sqlite3'
    catalog=Catalog(root)
    with closing(open_job(path)) as db:
        meta=metadata(db)
        try:
            config=Run.model_validate(meta['config']).model_dump(mode='json')
            dataset=catalog.verify(config['dataset_id'])
            check=capabilities(dataset,config)
            if not check['runnable']: raise ValueError('; '.join(check['blockers']))
            rows=catalog.rows(config['dataset_id'])
            replay_type=Replay
            if config.get('contract_protocol'):
                from .contract_replay import ContractReplay,arrival_rows
                required={config['contract_protocol']['reference_id']}|{l['instrument_id'] for l in config['strategies'][0]['legs']}
                rows=arrival_rows([r for r in rows if r['instrument_id'] in required],config['contract_protocol']);replay_type=ContractReplay
            if config['end']: rows=[r for r in rows if aware(r['timestamp'])<=aware(config['end'])]
            groups=[list(group) for _,group in itertools.groupby(rows,key=lambda r:r['timestamp'])]
            if config.get('contract_protocol'):
                grouped={aware(group[0]['timestamp']):group for group in groups}
                clock=config['contract_protocol']
                for at in (clock['entry_at'],clock['entry_deadline'],clock['exit_at'],config['end']):
                    grouped.setdefault(aware(at),dict(timer_at=at))
                groups=[value for at,value in sorted(grouped.items())]
            if not groups: raise ValueError('No observations in the requested evaluation range')
            identity=copy.deepcopy(CURRENT_BUILD)
            checkpoint=db.execute('SELECT document FROM checkpoint WHERE id=1').fetchone()
            if meta.get('engine_hash') and meta['engine_hash']!=identity['code_hash']: raise ValueError('Code changed since checkpoint; create a new experiment for reproducible evaluation')
            meta['engine_hash']=identity['code_hash']
            state=json.loads(checkpoint[0]) if checkpoint else None
            if state and meta['config_hash']!=digest(config): raise ValueError('Checkpoint configuration mismatch')
            buffer=[];changed={};last_state=state
            def flush(current,status=None):
                nonlocal buffer,changed,last_state
                meta.update(progress=current['cursor']/len(groups),processed=current['cursor'],total=len(groups),clock=current['clock'])
                if status: meta['status']=status
                with db:
                    db.executemany('INSERT INTO events VALUES(?,?,?,?,?,?)',[(r['sequence'],r['time'],r['kind'],r.get('strategy_id'),r.get('instrument_id'),canonical(r)) for r in buffer])
                    db.executemany('INSERT OR REPLACE INTO orders VALUES(?,?)',[(key,canonical(value)) for key,value in changed.items()])
                    db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?)',(canonical(current),))
                    save_meta(db,meta)
                buffer=[];changed={};last_state=current
            def emit(records,orders,current):
                buffer.extend(records);changed.update(orders)
                if current['cursor']%25==0: flush(current)
            replay=replay_type(dataset,config,emit,state)
            meta.update(status='RUNNING',worker_pid=os.getpid(),error=None)
            with db: save_meta(db,meta)
            start=time.monotonic()
            for cursor,group in enumerate(groups,1):
                if cursor<=replay.state['cursor']: continue
                command=db.execute('SELECT command FROM control WHERE id=1').fetchone()
                if command and command[0] in {'PAUSE','CANCEL'}:
                    status='PAUSED' if command[0]=='PAUSE' else 'CANCELLED'
                    flush(replay.state,status);return dict(id=identifier,status=status)
                if time.monotonic()-start>300: raise ValueError('Worker time budget reached; reduce the dataset or strategy batch')
                if isinstance(group,dict):replay.step([],cursor,timer_at=group['timer_at'])
                else:replay.step(group,cursor)
            flush(replay.state)
            curve=[dict(time=r['time'],equity=r['equity']) for r in db.execute("SELECT time,json_extract(document,'$.account.equity') AS equity FROM events WHERE kind='EQUITY' ORDER BY sequence")]
            performance=metrics(curve,config['capital'],config['seed'])
            windows=[]
            for window in config['windows']:
                observations=[r for r in curve if aware(window['start'])<=aware(r['time'])<=aware(window['end'])]
                capital=next((r['equity'] for r in reversed(curve) if aware(r['time'])<aware(window['start'])),config['capital'])
                windows.append(dict(**window,metrics=metrics(observations,capital,config['seed'])))
            fills=[json.loads(r[0]) for r in db.execute("SELECT document FROM events WHERE kind='FILL' ORDER BY sequence")]
            canonical_curve=[dict(time=r['time'],equity=r['equity']) for r in curve]
            final=replay.account_view()
            book_pnl=sum(b['realised_net']+b['unrealised'] for b in final['books'].values())
            violations=list(dict.fromkeys(replay.state['violations']))
            if abs(book_pnl-(final['equity']-config['capital']))>.011: violations.append('Strategy attribution does not reconcile with account P&L')
            blockers=[]
            manifest=dataset['manifest']
            if manifest['source_kind']=='SYNTHETIC': blockers.append('Synthetic fixture: no historical trading qualification')
            if not manifest['rights_confirmed']: blockers.append('Confirm historical data rights')
            if not manifest['point_in_time_universe']: blockers.append('Validate point-in-time universe membership')
            if not manifest['corporate_actions_complete']: blockers.append('Validate corporate-action completeness')
            if dataset['quality']['warnings']: blockers.append('Review dataset quality warnings')
            if manifest['frequency']!='QUOTE': blockers.append('Execution timing and spreads are bar-model assumptions')
            if any(i['kind'] in {'FUT','OPT'} for i in manifest['instruments']) and config['execution']['margin_model']!='HISTORICAL_GROSS': blockers.append('Derivatives use estimated historical margin')
            if not config['windows']: blockers.append('No explicit chronological evaluation windows')
            if performance.get('trading_days',0)<60: blockers.append('Insufficient forward-evaluation research sample')
            if final['stale_instruments']: blockers.append('Final portfolio valuations contain stale instruments')
            if not fills: blockers.append('No fills to evaluate')
            if violations or replay.state['obligations']: blockers.append('Resolve accounting, delivery or policy exceptions')
            if replay.state['halt']: blockers.append('Account halted: '+replay.state['halt'])
            report=dict(environment=identity,version=VERSION,mode='RESEARCH_PAPER_ONLY',dataset_id=dataset['id'],dataset_hash=dataset['parquet_hash'],config_hash=digest(config),
                config=config,performance=performance,windows=windows,account=final,positions=replay.final_positions(),
                pending_orders=list(replay.state['orders'].values()),obligations=replay.state['obligations'],violations=violations,
                capability=check,qualification=dict(status='PAPER_REVIEW_REQUIRED',blockers=list(dict.fromkeys(blockers)),live_orders_enabled=False),
                diagnostics=dict(halt_reason=replay.state['halt'],fills=len(fills),submitted_orders=replay.state['submitted_orders'],rejected_orders=replay.state['rejected_orders'],
                    fill_records=replay.state['filled_orders'],estimated_fees=replay.state['fees']/100,turnover=replay.state['turnover']/100,
                    reference_slippage_cost=replay.state['slippage_cost']/100,peak_margin=replay.state['margin_peak']/100,
                    closed_wins=replay.state['closed_wins'],closed_losses=replay.state['closed_losses'],bar_ambiguities=replay.state['ambiguities'],
                    ledger_reconciled=replay.state['cash']==replay.state['ledger_total'],attribution_reconciled=abs(book_pnl-(final['equity']-config['capital']))<=.011),
                models={key:value.get('model') for key,value in replay.state['local'].items() if value.get('model')},
                economic_hash=digest(dict(code_hash=identity['code_hash'],dataset=dataset['id'],config=config,curve=canonical_curve,fills=fills)),
                assumptions=['Only simulated executions; no broker order path',
                    'Bar fills use next eligible bar open plus declared spread/slippage, capped by observed bar volume; exact intrabar timing is unknown',
                    'Quote fills consume observed top-of-book depth; queue priority and market impact are not reconstructed',
                    'Margin is gross per-position; exchange portfolio offsets are not granted without an additional validated margin adapter',
                    'Physical settlement uses explicit intrinsic-value and underlying-transfer accounting; settlement charges are estimates',
                    'Daily marked-equity statistics and stress scenarios are diagnostics, not maximum-loss guarantees'])
            with db:
                db.execute('INSERT OR REPLACE INTO report VALUES(1,?)',(canonical(report),))
                meta.update(status='COMPLETED',progress=1.,completed=datetime.now(timezone.utc).isoformat(),economic_hash=report['economic_hash'])
                save_meta(db,meta)
            (path.parent/'report.json').write_text(canonical(report))
            return dict(id=identifier,status='COMPLETED')
        except Exception as exc:
            meta.update(status='FAILED',error=str(exc) if isinstance(exc,ValueError) else 'Research worker failed; inspect validation and dataset contracts',completed=datetime.now(timezone.utc).isoformat())
            with db: save_meta(db,meta)
            return dict(id=identifier,status='FAILED')
        finally: catalog.close()


class Research:
    def __init__(self,root,workers=2):
        self.root=Path(root);self.catalog=Catalog(root);self.workers=workers
        self.pool=None;self.futures={};self.lock=threading.RLock()
        # Interrupted jobs remain resumable from their last committed checkpoint.
        for run in self.runs():
            if run['status'] in {'RUNNING','QUEUED'}:
                with (self.path(run['id']).parent/'worker.lock').open('a') as lease:
                    try: fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError: continue
                    with closing(open_job(self.path(run['id']))) as db:
                        meta=metadata(db);meta.update(status='PAUSED',error='Process restarted; resume from the durable checkpoint')
                        with db:
                            save_meta(db,meta)
                            db.execute('INSERT OR REPLACE INTO control VALUES(1,?)',('PAUSE',))

    def path(self,identifier):
        if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier): raise ValueError('Invalid research run id')
        path=self.root/'runs'/identifier/'run.sqlite3'
        if not path.exists(): raise ValueError('Research run not found')
        return path

    def schedule(self,identifier):
        with self.lock:
            if self.pool is None: self.pool=ProcessPoolExecutor(max_workers=self.workers,mp_context=multiprocessing.get_context('spawn'),initializer=worker_setup)
            self.futures[identifier]=self.pool.submit(execute,str(self.root),identifier)

    def create(self,config,dispatch_key=None):
        with self.lock: return self._create(config,dispatch_key)

    def _create(self,config,dispatch_key=None):
        config=Run.model_validate(config).model_dump(mode='json')
        fixed_id=hashlib.sha256(dispatch_key.encode()).hexdigest()[:32] if dispatch_key else None
        if fixed_id and (self.root/'runs'/fixed_id/'run.sqlite3').exists():
            with closing(open_job(self.root/'runs'/fixed_id/'run.sqlite3')) as db:previous=metadata(db)
            if previous:
                if previous.get('dispatch_key')!=dispatch_key or previous['config_hash']!=digest(config) or previous['engine_hash']!=CURRENT_BUILD['code_hash']:raise ValueError('Dispatch identity binds different research inputs or build')
                with self.catalog.lock,self.catalog.db:self.catalog.db.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?)',(fixed_id,previous['created'],canonical(previous)))
                if previous['status']=='QUEUED' and fixed_id not in self.futures:self.schedule(fixed_id)
                return self.get(fixed_id)
        if sum(r['status'] in {'QUEUED','RUNNING'} for r in self.runs())>=64: raise ValueError('Research queue has reached its 64-job limit')
        dataset=self.catalog.verify(config['dataset_id']);check=capabilities(dataset,config)
        identifier=fixed_id or uuid.uuid4().hex;directory=self.root/'runs'/identifier;directory.mkdir(parents=True,mode=0o700,exist_ok=bool(fixed_id))
        document=dict(id=identifier,created=datetime.now(timezone.utc).isoformat(),status='QUEUED' if check['runnable'] else 'FAILED',progress=0,
            dispatch_key=dispatch_key,engine_hash=CURRENT_BUILD['code_hash'],config=config,config_hash=digest(config),dataset_id=dataset['id'],name=config['name'],capability=check,
            error=None if check['runnable'] else '; '.join(check['blockers']))
        with closing(open_job(directory/'run.sqlite3')) as db:
            with db:
                save_meta(db,document)
                db.execute('INSERT OR REPLACE INTO control VALUES(1,?)',('RUN',))
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute('INSERT INTO runs VALUES(?,?,?)',(identifier,document['created'],canonical(document)))
        (directory/'config.json').write_text(canonical(document))
        if check['runnable']: self.schedule(identifier)
        return document

    def runs(self):
        with self.catalog.lock: rows=self.catalog.db.execute('SELECT id FROM runs ORDER BY created DESC LIMIT 500').fetchall()
        return [self.get(row['id']) for row in rows]

    def get(self,identifier):
        with closing(open_job(self.path(identifier))) as db: result=metadata(db)
        future=self.futures.get(identifier)
        if future and future.done() and future.exception() and result['status'] not in TERMINAL|{'PAUSED'}:
            result.update(status='FAILED',error='Worker process interrupted; checkpoint is preserved')
            with closing(open_job(self.path(identifier))) as db:
                with db: save_meta(db,result)
        return result

    def report(self,identifier):
        with closing(open_job(self.path(identifier))) as db:
            row=db.execute('SELECT document FROM report WHERE id=1').fetchone()
        if not row: raise ValueError('Run has not completed a report')
        result=json.loads(row[0]);returns=result['performance'].get('daily_returns',[])
        comparable=[];trials=0
        run=self.get(identifier);experiment=run['config'].get('experiment_id') or run['dataset_id']
        with self.catalog.lock: historical=[json.loads(row[0]) for row in self.catalog.db.execute('SELECT document FROM runs') ]
        for candidate in historical:
            if (candidate['config'].get('experiment_id') or candidate['dataset_id'])!=experiment: continue
            trials+=1
            with closing(open_job(self.path(candidate['id']))) as db:
                row=db.execute('SELECT document FROM report WHERE id=1').fetchone()
            if row:
                r=json.loads(row[0])['performance'].get('daily_returns',[])
                if len(r)>1:
                    import statistics
                    sd=statistics.stdev(r)
                    if sd: comparable.append(statistics.mean(r)/sd)
        result['search_diagnostics']=deflated_sharpe(returns,comparable,trials)
        return result

    def events(self,identifier,after=0,limit=100,kind=None):
        with closing(open_job(self.path(identifier))) as db:
            query='SELECT document FROM events WHERE sequence>?';args=[after]
            if kind: query+=' AND kind=?';args.append(kind)
            query+=' ORDER BY sequence LIMIT ?';args.append(limit)
            return [json.loads(r[0]) for r in db.execute(query,args)]

    def orders(self,identifier):
        with closing(open_job(self.path(identifier))) as db:
            return [json.loads(r[0]) for r in db.execute('SELECT document FROM orders ORDER BY id LIMIT 2000')]

    def curve(self,identifier):
        with closing(open_job(self.path(identifier))) as db:
            rows=db.execute("SELECT time,json_extract(document,'$.account.equity') equity,json_extract(document,'$.account.drawdown_pct') drawdown,json_extract(document,'$.account.benchmark') benchmark FROM events WHERE kind='EQUITY' ORDER BY sequence").fetchall()
        stride=max(1,len(rows)//1000)
        selected=list(rows[::stride])
        if rows and (not selected or selected[-1]!=rows[-1]): selected.append(rows[-1])
        return [dict(row) for row in selected]

    def control(self,identifier,action):
        current=self.get(identifier)
        if action=='RESUME':
            if current['status']=='FAILED' and (current.get('dispatch_key') or '').startswith('mvp2:'):
                raise ValueError('Failed protocol attempts are terminal; start a new budgeted attempt to preserve failure evidence')
            if current['status'] not in {'PAUSED','FAILED'}: raise ValueError('Only paused/interrupted runs can resume')
            if current['status']=='FAILED' and not current['capability']['runnable']: raise ValueError('Capability failure requires a new experiment')
            prior=self.futures.get(identifier)
            if prior and not prior.done(): raise ValueError('Worker is finishing its checkpoint; retry shortly')
            with closing(open_job(self.path(identifier))) as db:
                with db:
                    db.execute('INSERT OR REPLACE INTO control VALUES(1,?)',('RUN',))
                    current.update(status='QUEUED',error=None);save_meta(db,current)
            self.schedule(identifier)
        else:
            if current['status'] in TERMINAL: raise ValueError('Run is already terminal')
            with closing(open_job(self.path(identifier))) as db:
                with db:
                    db.execute('INSERT OR REPLACE INTO control VALUES(1,?)',(action,))
                    if current['status']=='PAUSED' and action=='CANCEL': current['status']='CANCELLED';save_meta(db,current)
        return self.get(identifier)

    def handoff(self,identifier):
        report=self.report(identifier)
        document=dict(id=uuid.uuid4().hex,created=datetime.now(timezone.utc).isoformat(),run_id=identifier,
            status='PAPER_REVIEW_REQUIRED',mode='PAPER',live_orders_enabled=False,
            blockers=report['qualification']['blockers'],config=report['config'],economic_hash=report['economic_hash'],
            required_review=['Dataset and usage rights','Instrument/charges/margin assumptions','Portfolio limits','Forward paper observation plan'],
            note='An immutable review packet; this does not submit orders or enable a strategy')
        with self.catalog.lock,self.catalog.db:
            self.catalog.db.execute('INSERT INTO handoffs VALUES(?,?,?)',(document['id'],document['created'],canonical(document)))
        return document

    def suite(self,base,variants):
        with self.lock: return self._suite(base,variants)

    def _suite(self,base,variants):
        created=[]
        allowed=('capital','risk.','execution.','strategies.')
        experiment=base.get('experiment_id') or 'experiment-'+uuid.uuid4().hex[:16]
        prepared=[]
        for index,variant in enumerate(variants):
            config=copy.deepcopy(base);config['experiment_id']=experiment
            config['name']=base['name']+' / variant '+str(index+1)
            for path,value in variant.items():
                if not any(path==p or path.startswith(p) for p in allowed): raise ValueError('Unsupported variant path')
                keys=path.split('.');target=config
                try:
                    for key in keys[:-1]:
                        if isinstance(target,list):
                            if not key.isdigit(): raise ValueError('List indexes must be non-negative integers')
                            target=target[int(key)]
                        else: target=target[key]
                    key=keys[-1]
                    if isinstance(target,list):
                        if not key.isdigit(): raise ValueError('List indexes must be non-negative integers')
                        target[int(key)]=value
                    else: target[key]=value
                except (KeyError,IndexError,TypeError): raise ValueError('Invalid sensitivity variant path: '+path[:100]) from None
            prepared.append(Run.model_validate(config).model_dump(mode='json'))
        if len(prepared)+sum(r['status'] in {'RUNNING','QUEUED'} for r in self.runs())>64: raise ValueError('Sensitivity suite exceeds the available research queue capacity')
        for config in prepared: created.append(self.create(config))
        return created

    def close(self):
        for identifier,future in list(self.futures.items()):
            if not future.done():
                with closing(open_job(self.path(identifier))) as db:
                    with db: db.execute('INSERT OR REPLACE INTO control VALUES(1,?)',('PAUSE',))
        if self.pool: self.pool.shutdown(wait=True,cancel_futures=False)
        self.catalog.close()
