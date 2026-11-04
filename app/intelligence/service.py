"""Immutable source vintages, isolated workers and point-in-time feature joins."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
import copy,csv,hashlib,io,json,sqlite3,threading,uuid
from zoneinfo import ZoneInfo
from .schemas import ImportReport,FeedPolicy
from .parsers import fii_rows,macro_rows,rbi_items
from .analytics import kite_surfaces
from ..research.catalog import canonical,digest
from ..research.schemas import aware,iso,Manifest,ImportData

class DataFeeds:
    def __init__(self,root,kite,clock=None,providers=None):
        from .providers import OfficialFeeds
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.last_error=None;self.last_tick=None
        self.kite=kite;self.clock=clock or (lambda:datetime.now(timezone.utc));self.providers=providers or OfficialFeeds()
        self.lock=threading.RLock();self.policy_guard=threading.RLock();self.closing=False;self.futures={}
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-public-inputs')
        self.quote_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='artha-input-quotes')
        self.db=sqlite3.connect(self.root/'evidence.sqlite3',check_same_thread=False,timeout=15);self.db.row_factory=sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,source TEXT NOT NULL,kind TEXT NOT NULL,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY,artifact_id TEXT NOT NULL,series TEXT NOT NULL,period TEXT NOT NULL,available_at TEXT NOT NULL,valid_until TEXT NOT NULL,document TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS feature_time ON observations(series,period,available_at);
        CREATE TABLE IF NOT EXISTS input_events(sequence INTEGER PRIMARY KEY,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS input_jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,source TEXT NOT NULL,status TEXT NOT NULL,document TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS input_settings(name TEXT PRIMARY KEY,document TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS input_source_lookup ON observations(json_extract(document,'$.source'));
        CREATE INDEX IF NOT EXISTS input_job_source ON input_jobs(source);
        CREATE INDEX IF NOT EXISTS input_job_status ON input_jobs(status);
        CREATE INDEX IF NOT EXISTS input_artifact_source ON artifacts(source);""")
        with self.transaction():
            for row in self.db.execute("SELECT document FROM input_jobs WHERE status IN ('QUEUED','RUNNING')").fetchall():
                item=json.loads(row[0]);item.update(status='INTERRUPTED',error='Process restarted; request was interrupted',completed_at=self.now());self._save_job(item)
        self.audit_result=self.audit()

    def now(self):return iso(self.clock())

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('SAVEPOINT input_action')
            try:yield
            except BaseException:
                self.db.execute('ROLLBACK TO input_action');self.db.execute('RELEASE input_action');raise
            else:self.db.execute('RELEASE input_action')

    def policy(self):
        with self.lock:
            row=self.db.execute("SELECT document FROM input_settings WHERE name='policy'").fetchone()
            return json.loads(row[0]) if row else FeedPolicy().model_dump(mode='json')

    def policy_hash(self):return digest(self.policy())

    def set_policy(self,body):
        value=FeedPolicy.model_validate(body.model_dump() if hasattr(body,'model_dump') else body).model_dump(mode='json')
        with self.policy_guard,self.transaction():self.db.execute('INSERT OR REPLACE INTO input_settings VALUES(?,?)',('policy',canonical(value)))
        return dict(policy=value,policy_hash=digest(value))

    def _event(self,kind,identifier,content_hash):
        last=self.db.execute('SELECT sequence,document FROM input_events ORDER BY sequence DESC LIMIT 1').fetchone()
        item=dict(sequence=last[0]+1 if last else 1,kind=kind,artifact_id=identifier,artifact_hash=content_hash,recorded_at=self.now(),previous_hash=json.loads(last[1])['hash'] if last else '0'*64)
        item['hash']=digest(item);self.db.execute('INSERT INTO input_events VALUES(?,?)',(item['sequence'],canonical(item)))

    def _append(self,source,kind,url,content,rows,scope='INDIA',reviewer='OFFICIAL_CONNECTOR',extra=None,received_at=None,policy_hash=None):
        policy_hash=policy_hash or self.policy_hash()
        received=iso(received_at or self.clock());raw_hash=hashlib.sha256(content.encode()).hexdigest()
        identifier=digest(dict(source=source,kind=kind,url=url,scope=scope,raw_hash=raw_hash,policy_hash=policy_hash if source=='KITE' else None))
        with self.transaction():
            if not self.audit_result['ok']:raise ValueError('Input evidence audit failed; inspect or restore the source store')
            old=self.db.execute('SELECT document FROM artifacts WHERE id=?',(identifier,)).fetchone()
            if old:return self.artifact(identifier)
            if self.db.execute('SELECT count(*) FROM observations').fetchone()[0]+len(rows)>250000:raise ValueError('Input observation capacity reached; archive/retention review required')
            if self.db.execute('SELECT count(*) FROM artifacts').fetchone()[0]>=20000:raise ValueError('Input artifact capacity reached; archive/retention review required')
            normalized=[]
            for index,row in enumerate(rows):
                published=aware(row['published_at']) if row.get('published_at') else aware(received)
                if published>aware(received):raise ValueError('Input publication is in the future')
                item=dict(row,id=digest(dict(artifact_id=identifier,index=index)),artifact_id=identifier,source=source,scope=scope,received_at=received,available_at=iso(max(published,aware(received))),source_url=row.get('publication_url',url),policy_hash=policy_hash if source=='KITE' else None)
                item['observation_hash']=digest(item);normalized.append(item)
            doc=dict(id=identifier,source=source,kind=kind,scope=scope,source_url=url,received_at=received,raw_sha256=raw_hash,content=content,reviewer=reviewer,identity_assurance='LOCAL_OWNER_ASSERTION' if reviewer!='OFFICIAL_CONNECTOR' else 'FIXED_OFFICIAL_ORIGIN',observations=normalized,extra=extra or {},license_verified=False)
            doc['artifact_hash']=digest(doc)
            if len(canonical(doc))>10000000:raise ValueError('Normalized input artifact exceeds the storage limit')
            self.db.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(identifier,source,kind,canonical(doc)))
            for item in normalized:self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',(item['id'],identifier,item['series'],item['period'],item['available_at'],item['valid_until'],canonical(item)))
            self._event('INGESTED',identifier,doc['artifact_hash'])
        self.audit_result=dict(ok=True,note='New artifact and observation hashes verified; full audit available')
        return doc

    def ingest(self,body):
        body=ImportReport.model_validate(body.model_dump(mode='json') if hasattr(body,'model_dump') else body)
        if not body.rights_confirmed:raise ValueError('Confirm permitted local data rights before importing')
        received=self.clock()
        rows=fii_rows(body.content,body.scope,body.published_at,received,self.policy()['fii_max_age_hours']) if body.kind=='FII_CSV' else macro_rows(body.content,received)
        return self._append(body.source,body.kind,body.source_url,body.content,rows,body.scope,body.reviewer,received_at=received)

    def artifact(self,identifier):
        with self.lock:
            row=self.db.execute('SELECT document FROM artifacts WHERE id=?',(identifier,)).fetchone()
            if not row:raise ValueError('Input artifact not found')
            value=json.loads(row[0]);check=dict(value);expected=check.pop('artifact_hash',None)
            if digest(check)!=expected or hashlib.sha256(value['content'].encode()).hexdigest()!=value['raw_sha256']:raise ValueError('Input artifact integrity failed')
            return value

    def artifacts(self):
        with self.lock:
            rows=self.db.execute('SELECT id FROM artifacts ORDER BY rowid DESC LIMIT 100').fetchall()
            return [{key:value for key,value in self.artifact(row[0]).items() if key not in {'content','observations','extra'}} for row in rows]

    def observations(self,limit=200):
        with self.lock:return [json.loads(row[0]) for row in self.db.execute('SELECT document FROM observations ORDER BY rowid DESC LIMIT ?',(min(500,max(1,limit)),))]

    def features(self,at,names):
        if len(names)>20:raise ValueError('At most twenty evidence features may be joined')
        result={};stamp=iso(at);checked_artifacts={}
        with self.lock:
            for name in names:
                row=self.db.execute('SELECT document FROM observations WHERE series=? AND available_at<=? ORDER BY period DESC,available_at DESC,rowid DESC LIMIT 1',(name,stamp)).fetchone()
                if not row:continue
                item=json.loads(row[0]);check=dict(item);expected=check.pop('observation_hash',None)
                if digest(check)!=expected:raise ValueError('Input observation integrity failed')
                if item['artifact_id'] not in checked_artifacts:
                    artifact=self.artifact(item['artifact_id']);checked_artifacts[item['artifact_id']]={value['id']:value for value in artifact['observations']}
                if checked_artifacts[item['artifact_id']].get(item['id'])!=item:raise ValueError('Input observation/artifact integrity failed')
                if item['value'] is None or aware(item['valid_until'])<aware(at):continue
                if item['source']=='KITE' and item['policy_hash']!=self.policy_hash():continue
                if item['source']=='NSE' and aware(at)>aware(item.get('age_anchor') or item['published_at'])+timedelta(hours=self.policy()['fii_max_age_hours']):continue
                result[name]=dict(value=item['value'],available_at=item['available_at'],valid_until=item['valid_until'],observation_id=item['id'],artifact_id=item['artifact_id'],source=item['source'],source_url=item['source_url'],published_at=item.get('published_at'),received_at=item['received_at'],publication_time_known=item.get('published_at') is not None,unit=item['unit'],period=item['period'],base_year=item.get('base_year'))
        return result

    def surfaces(self):
        with self.lock:
            row=self.db.execute("SELECT id FROM artifacts WHERE source='KITE' ORDER BY rowid DESC LIMIT 1").fetchone()
            if not row:return []
            artifact=self.artifact(row[0])
            return [dict(item,fresh=aware(item['valid_until'])>=aware(self.now()) and artifact['extra'].get('policy_hash')==self.policy_hash()) for item in artifact['extra'].get('surfaces',[])]

    def _save_job(self,item):self.db.execute('INSERT OR REPLACE INTO input_jobs VALUES(?,?,?,?,?)',(item['id'],item['request_id'],item['source'],item['status'],canonical(item)))

    def jobs(self):
        with self.lock:return [json.loads(row[0]) for row in self.db.execute('SELECT document FROM input_jobs ORDER BY rowid DESC LIMIT 100')]

    def refresh(self,source,request_id):
        if source not in {'RBI','MOSPI','KITE'}:raise ValueError('Unknown fixed data source')
        with self.transaction():
            old=self.db.execute('SELECT document FROM input_jobs WHERE request_id=?',(request_id,)).fetchone()
            binding=digest(dict(source=source,policy=self.policy()))
            if old:
                item=json.loads(old[0])
                if item['binding']!=binding:raise ValueError('Data request id is already bound to different inputs')
                return item
            if self.db.execute("SELECT count(*) FROM input_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]>=4:raise ValueError('Input job queue is full')
            if self.db.execute("SELECT 1 FROM input_jobs WHERE source=? AND status IN ('QUEUED','RUNNING')",(source,)).fetchone():raise ValueError('A refresh is already pending for this source')
            item=dict(id=uuid.uuid4().hex,request_id=request_id,source=source,status='QUEUED',created_at=self.now(),binding=binding,policy=self.policy(),error=None,result=None)
            self._save_job(item);return item

    def _fetch(self,item):
        if item['source']=='KITE':
            surfaces,quotes=kite_surfaces(self.kite,item['policy'],self.clock)
            return dict(policy_hash=digest(item['policy']),source='KITE',kind='KITE_SURFACE',source_url='https://api.kite.trade/quote',content=canonical(dict(quotes=quotes,surfaces=surfaces)),surfaces=surfaces,received_at=self.now())
        return dict(self.providers.fetch(item['source']),received_at=self.now())

    def _apply(self,result):
        source=result['source'];received=result['received_at'];extra=result.get('extra',{})
        if source=='KITE':
            rows=[];extra['surfaces']=result['surfaces'];extra['policy_hash']=result['policy_hash']
            for item in result['surfaces']:
                for field,name in (('atm_iv_pct','atm_pct'),('skew25_pp','skew25_pp'),('butterfly25_pp','butterfly25_pp')):
                    if item[field] is not None:rows.append(dict(series=f"iv.{item['underlying']}.{item['tenor']}.{name}",period=iso(received),value=item[field],unit='VOL_PERCENT' if name=='atm_pct' else 'VOL_PERCENTAGE_POINTS',published_at=item['published_at'],valid_until=item['valid_until'],status='ESTIMATED',base_year=None))
        elif result['kind']=='RBI_RSS':rows=rbi_items(result['content'],received)
        else:rows=result['observations']
        return self._append(source,result['kind'],result['source_url'],result['content'],rows,extra=extra,received_at=received,policy_hash=result.get('policy_hash'))

    def tick(self):
        if self.closing:return
        finished=[]
        with self.transaction():
            for identifier,entry in list(self.futures.items()):
                item=json.loads(self.db.execute('SELECT document FROM input_jobs WHERE id=?',(identifier,)).fetchone()[0])
                if entry['future'].done():
                    try:
                        if item['status']=='RUNNING':
                            result=entry['future'].result();artifact=self._apply(result)
                            item.update(status='SUCCEEDED',completed_at=self.now(),result=dict(artifact_id=artifact['id'],observations=len(artifact['observations']),warnings=artifact['extra'].get('warnings',[])))
                    except Exception as exc:
                        if item['status']=='RUNNING':item.update(status='FAILED',completed_at=self.now(),error=str(exc)[:300] if isinstance(exc,ValueError) else 'Data source request failed; check its status and data access')
                    self._save_job(item);finished.append(identifier)
                elif item['status']=='RUNNING' and (aware(self.now())-aware(item['started_at'])).total_seconds()>90:
                    item.update(status='TIMED_OUT',completed_at=self.now(),error='Source deadline exceeded; late output will be discarded');self._save_job(item)
            policy=self.policy();at=aware(self.now());local=at.astimezone(ZoneInfo('Asia/Kolkata'))
            for source in ('RBI','MOSPI','KITE'):
                enabled=policy['kite_enabled'] if source=='KITE' else policy['macro_enabled']
                if not enabled or source=='KITE' and (local.weekday()>4 or not '09:15'<=local.strftime('%H:%M')<='15:30'):continue
                interval=policy['quote_interval_seconds'] if source=='KITE' else policy['macro_interval_seconds']
                previous=self.db.execute('SELECT document FROM input_jobs WHERE source=? ORDER BY rowid DESC LIMIT 1',(source,)).fetchone()
                if not previous or (at-aware(json.loads(previous[0])['created_at'])).total_seconds()>=interval:
                    try:self.refresh(source,'periodic-'+source+'-'+str(int(at.timestamp())//interval)+'-'+self.policy_hash()[:10])
                    except ValueError:pass
        with self.lock:
            for identifier in finished:self.futures.pop(identifier,None)
            for row in self.db.execute("SELECT document FROM input_jobs WHERE status='QUEUED' ORDER BY rowid").fetchall():
                item=json.loads(row[0]);group='KITE' if item['source']=='KITE' else 'PUBLIC'
                if any(entry['group']==group for entry in self.futures.values()):continue
                with self.transaction():item.update(status='RUNNING',started_at=self.now());self._save_job(item)
                try:
                    pool=self.quote_pool if group=='KITE' else self.pool
                    self.futures[item['id']]=dict(group=group,future=pool.submit(self._fetch,copy.deepcopy(item)))
                except RuntimeError:
                    with self.transaction():item.update(status='FAILED',completed_at=self.now(),error='Input worker is unavailable');self._save_job(item)
        self.last_tick=self.now();self.last_error=None

    def enrich(self,identifier,names,catalog):
        if not 1<=len(names)<=20 or len(set(names))!=len(names):raise ValueError('Select one to twenty distinct evidence features')
        original=catalog.verify(identifier);rows=catalog.rows(identifier)
        if len(rows)>50000:raise ValueError('Feature enrichment is limited to 50,000 rows per immutable dataset')
        cache={};content=io.StringIO();writer=csv.DictWriter(content,fieldnames=list(rows[0]));writer.writeheader();joined=0
        for original_row in rows:
            row=dict(original_row);stamp=row['timestamp']
            if stamp not in cache:cache[stamp]=self.features(stamp,names)
            features=json.loads(row.get('features_json') or '{}')
            if set(features)&set(names):raise ValueError('Evidence enrichment cannot overwrite existing feature names')
            features.update(cache[stamp]);joined+=len(cache[stamp]);row['features_json']=canonical(features);writer.writerow(row)
        manifest=copy.deepcopy(original['manifest']);manifest.update(name=manifest['name'][:80]+' + dated evidence',parent_dataset_id=identifier,notes=manifest.get('notes','')[:3500]+' Evidence features joined by server receipt/publication cutoffs; absent values remain absent. Policy '+self.policy_hash())
        result=catalog.import_data(ImportData(manifest=Manifest.model_validate(manifest),format='CSV',content=content.getvalue()))
        return dict(result,joined_feature_values=joined,evidence_policy_hash=self.policy_hash())

    def audit(self):
        with self.lock:
            previous='0'*64;count=0;observation_count=0
            try:
                for row in self.db.execute('SELECT document FROM input_events ORDER BY sequence'):
                    item=json.loads(row[0]);expected=item.pop('hash',None);count+=1
                    if item['sequence']!=count or item['previous_hash']!=previous or digest(item)!=expected:return dict(ok=False,reason='Input evidence event chain mismatch')
                    artifact=self.artifact(item['artifact_id'])
                    if artifact['artifact_hash']!=item['artifact_hash']:return dict(ok=False,reason='Input event/artifact mismatch')
                    observation_count+=len(artifact['observations'])
                    for observation in artifact['observations']:
                        stored=self.db.execute('SELECT document FROM observations WHERE id=?',(observation['id'],)).fetchone()
                        if not stored or json.loads(stored[0])!=observation:return dict(ok=False,reason='Input observation/artifact mismatch')
                    previous=expected
                if self.db.execute('SELECT count(*) FROM observations').fetchone()[0]!=observation_count:return dict(ok=False,reason='Input observation/event count mismatch')
                if self.db.execute('SELECT count(*) FROM artifacts').fetchone()[0]!=count:return dict(ok=False,reason='Input artifact/event count mismatch')
            except (ValueError,TypeError,KeyError):return dict(ok=False,reason='Input evidence integrity failed')
            return dict(ok=True,checked=count,note='Local source/evidence hashes; no external trust anchor')

    def status(self):
        with self.lock:
            sources={}
            for source in ('NSE','RBI','MOSPI','KITE'):
                row=self.db.execute('SELECT document FROM input_jobs WHERE source=? ORDER BY rowid DESC LIMIT 1',(source,)).fetchone();job=json.loads(row[0]) if row else None
                last=self.db.execute("SELECT document FROM observations WHERE json_extract(document,'$.source')=? ORDER BY rowid DESC LIMIT 1",(source,)).fetchone();value=json.loads(last[0]) if last else None
                count=self.db.execute("SELECT count(*) FROM observations WHERE json_extract(document,'$.source')=?",(source,)).fetchone()[0]
                sources[source]=dict(last_job=job['status'] if job else None,last_error=job['error'] if job else None,warnings=(job.get('result') or {}).get('warnings',[]) if job else [],last_received_at=value['received_at'] if value else None,observations=count,transport='OFFICIAL_CSV_IMPORT' if source=='NSE' else 'KITE_DERIVED_SNAPSHOT' if source=='KITE' else 'OFFICIAL_PUBLIC_SOURCE')
            series=[row[0] for row in self.db.execute("SELECT DISTINCT series FROM observations WHERE series NOT LIKE 'event.%' ORDER BY series LIMIT 201")]
            truncated=len(series)>200;series=series[:200];current={}
            for offset in range(0,len(series),20):current.update(self.features(self.now(),series[offset:offset+20]))
            return dict(worker=dict(healthy=self.last_error is None and (self.last_tick is None or (aware(self.now())-aware(self.last_tick)).total_seconds()<=30),last_tick=self.last_tick,error=self.last_error),policy=self.policy(),policy_hash=self.policy_hash(),sources=sources,series=series,series_truncated=truncated,current_features=current,audit=self.audit_result,live_orders_enabled=False,limitations=['FII/DII activity is daily/provisional and imported from official downloaded reports','IV/skew and Greeks are estimated from bounded Kite snapshots','Historical revisions are never substituted into earlier decisions','No Dhan provider or broker execution is installed'])

    def close(self):
        self.closing=True;self.pool.shutdown(wait=True,cancel_futures=True);self.quote_pool.shutdown(wait=True,cancel_futures=True)
        with self.lock:self.db.close()
