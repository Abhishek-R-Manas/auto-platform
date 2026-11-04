"""Read-only NSE breadth feed, paper-watchlist delivery, and durable quote captures."""
import asyncio
import csv
from datetime import datetime, timezone
import io
import hashlib
import json
import logging
import math
from pathlib import Path
import sqlite3
import ssl
import struct
import threading
import time
from urllib.parse import urlencode
import uuid

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from .engine import IST
from .research.catalog import canonical
from .research.schemas import ImportData, Manifest


class FixedOriginConnect(connect):
    def process_redirect(self, exc):
        # Authentication is in the broker-required query. Never forward it on redirects.
        return exc


# A private, non-propagating logger prevents SDK debug logs exposing credential URLs.
STREAM_LOG=logging.Logger('artha.private.market.transport',level=logging.CRITICAL+1)
STREAM_LOG.addHandler(logging.NullHandler())


def stream_tls_context():
    # Verified 2026-11-04: ws.kite.trade accepts TLS 1.2 but closes TLS 1.3-only handshakes.
    # Prefer 1.3 when supported; permit only forward-secret AEAD suites on the 1.2 path.
    context=ssl.create_default_context()
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20')
    return context


def decode_frame(frame):
    """Kite full NSE/NFO (184B) and index (32B) packets; bounded strict framing."""
    if len(frame)==1: return []  # Broker heartbeat, not a quote.
    if len(frame)<2 or len(frame)>1_000_000: raise ValueError('Invalid market frame size')
    count=struct.unpack_from('>H',frame)[0]
    if count>5000: raise ValueError('Too many market packets')
    offset=2; result=[]
    for _ in range(count):
        if offset+2>len(frame): raise ValueError('Truncated packet header')
        length=struct.unpack_from('>H',frame,offset)[0]; offset+=2
        if length not in {8,28,32,44,184} or offset+length>len(frame): raise ValueError('Invalid market packet length')
        packet=frame[offset:offset+length];offset+=length
        if length not in {32,184}: continue  # Non-full modes lack trusted depth/time.
        integer=lambda start:struct.unpack_from('>I',packet,start)[0]
        token=integer(0)
        if token&255 not in {1,2,9}: continue  # NSE equity, NFO, NSE index only.
        stamp=integer(28 if length==32 else 60)
        if not stamp: continue
        item=dict(instrument_token=token,last=integer(4)/100,
                  exchange_time=datetime.fromtimestamp(stamp,IST).isoformat())
        if length==184:
            depth=[]
            for at in (64,124):
                levels=[(integer(i),integer(i+4)/100) for i in range(at,at+60,12)]
                depth.append(next(((quantity,price) for quantity,price in levels if quantity>0 and price>0),None))
            item.update(cumulative_volume=integer(16),oi=integer(48))
            if all(depth) and depth[0][1]<=depth[1][1]:
                item.update(bid=depth[0][1],ask=depth[1][1],bid_quantity=depth[0][0],ask_quantity=depth[1][0])
        if not math.isfinite(item['last']) or item['last']<=0: continue
        result.append(item)
    if offset!=len(frame): raise ValueError('Trailing bytes in market frame')
    return result


class Captures:
    def __init__(self,root):
        root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(root/'market.sqlite3',check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations(seq INTEGER PRIMARY KEY,capture_id TEXT NOT NULL,symbol TEXT NOT NULL,quote TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS capture_rows ON observations(capture_id,seq);''')
        for row in self.db.execute('SELECT id,document FROM captures').fetchall():
            item=json.loads(row['document'])
            if item['status']=='RECORDING':
                item.update(status='INTERRUPTED',ended_at=datetime.now(timezone.utc).isoformat())
                self._save(item)
        self.db.commit()

    def _save(self,item):
        self.db.execute('INSERT OR REPLACE INTO captures VALUES(?,?)',(item['id'],canonical(item)))

    def enabled(self):
        with self.lock:
            row=self.db.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
        return bool(row and row[0]=='true')

    def set_enabled(self,value):
        with self.lock,self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('enabled',?)",('true' if value else 'false',))

    def list(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM captures ORDER BY rowid DESC LIMIT 100')]

    def start(self,name,instruments,rights_confirmed=False):
        with self.lock,self.db:
            if any(c['status']=='RECORDING' for c in self.list()): raise ValueError('Stop the active capture first')
            item=dict(id=uuid.uuid4().hex,name=name,status='RECORDING',started_at=datetime.now(timezone.utc).isoformat(),
                ended_at=None,rows=0,gaps=0,rights_confirmed=rights_confirmed,instruments=instruments,dataset_id=None,
                scope='PAPER_WATCHLIST',source='KITE_WEBSOCKET',max_rows=100000)
            self._save(item);return item

    def stop(self,identifier,status='STOPPED'):
        with self.lock,self.db:
            item=next((x for x in self.list() if x['id']==identifier),None)
            if not item: raise ValueError('Capture not found')
            if item['status']=='RECORDING':
                item.update(status=status,ended_at=datetime.now(timezone.utc).isoformat());self._save(item)
            return item

    def gap(self):
        with self.lock,self.db:
            for item in self.list():
                if item['status']=='RECORDING': item['gaps']+=1;self._save(item)

    def append(self,quotes,instruments):
        with self.lock,self.db:
            for item in self.list():
                if item['status']!='RECORDING': continue
                if item['instruments']!=instruments:
                    self.stop(item['id'],'SOURCE_CHANGED');continue
                for symbol,q in quotes.items():
                    if item['rows']>=item['max_rows']:
                        item.update(status='LIMIT_REACHED',ended_at=datetime.now(timezone.utc).isoformat());break
                    self.db.execute('INSERT INTO observations(capture_id,symbol,quote) VALUES(?,?,?)',(item['id'],symbol,canonical(q)))
                    item['rows']+=1
                self._save(item)

    def import_into(self,identifier,catalog):
        with self.lock:
            item=next((x for x in self.list() if x['id']==identifier),None)
            if not item: raise ValueError('Capture not found')
            if item['status']=='RECORDING': raise ValueError('Stop recording before creating an immutable dataset')
            if not item['rows']: raise ValueError('Capture has no actual quotes; no dataset was created')
            if item['dataset_id']: return catalog.get(item['dataset_id'])
            observations=self.db.execute('SELECT symbol,quote FROM observations WHERE capture_id=? ORDER BY seq',(identifier,)).fetchall()
        identity=lambda key:'KITE:'+hashlib.sha256(key.encode()).hexdigest()[:24]
        instruments=[]
        for symbol,i in item['instruments'].items():
            instruments.append(dict(id=identity(i['exchange']+':'+symbol),symbol=symbol,kind=i['kind'],underlying=identity(i.get('underlying_key','NSE:'+i['underlying'])),
                exchange=i['exchange'],lot_size=i['lot_size'],tick_size=i['tick_size'],expiry=i.get('expiry'),
                strike=i.get('strike'),option_type=i.get('option_type'),settlement=i.get('settlement','CASH')))
        manifest=Manifest(name=item['name'],source='Kite Connect recorded quotes · '+identifier,source_kind='BROKER',
            frequency='QUOTE',rights_confirmed=item['rights_confirmed'],instruments=instruments,
            license_note='Owner subscription terms apply; no redistribution rights are inferred.',
            notes=f"Capture {identifier}; {item['gaps']} connection gaps; {item['status']}. Coalesced paper-watchlist snapshots, not an exhaustive tick tape. Timestamp is receipt/availability time; raw CSV preserves exchange and receipt times. Volume is zero, not cumulative provider volume. No exact portfolio margin or corporate-action history supplied. Master hashes: {','.join(sorted({i.get('master_hash','unavailable') for i in item['instruments'].values()}))}.")
        buffer=io.StringIO(); writer=csv.DictWriter(buffer,fieldnames=['instrument_id','timestamp','exchange_time','received_at','last','bid','ask','bid_quantity','ask_quantity','underlying_price','oi','volume','cumulative_volume','spot_exchange_time','spot_received_at'])
        writer.writeheader()
        for row in observations:
            q=json.loads(row['quote']);i=item['instruments'][row['symbol']]
            writer.writerow(dict(instrument_id=identity(i['exchange']+':'+row['symbol']),timestamp=datetime.fromtimestamp(q['received_at'],timezone.utc).isoformat(),
                exchange_time=q['exchange_time'],received_at=q['received_at'],last=q['last'],bid=q['bid'],ask=q['ask'],
                bid_quantity=q['bid_quantity'],ask_quantity=q['ask_quantity'],underlying_price=q['spot'],oi=q.get('oi',0),
                volume=0,cumulative_volume=q.get('cumulative_volume',0),spot_exchange_time=q.get('spot_exchange_time',q['exchange_time']),spot_received_at=q.get('spot_received_at',q['received_at'])))
        content=buffer.getvalue()
        if len(content.encode())>32_000_000: raise ValueError('Capture export exceeds import size limit; use shorter recording sessions')
        dataset=catalog.import_data(ImportData(manifest=manifest,content=content))
        item['dataset_id']=dataset['id']
        with self.lock,self.db: self._save(item)
        return dataset

    def close(self):
        for item in self.list():
            if item['status']=='RECORDING': self.stop(item['id'],'INTERRUPTED')
        self.db.close()


class FeedConfigurationError(ValueError):
    pass


class MarketHub:
    def __init__(self,kite,engine,root):
        self.kite=kite;self.engine=engine;self.store=Captures(root)
        self.lock=threading.RLock(); self.universe={};self.quotes={};self.dirty=set()
        self.phase='WAITING_AUTH' if self.store.enabled() else 'IDLE'
        self.error=None;self.recording_error=None;self.connections={};self.reconnects=0;self.messages=0
        self.last_message=None;self.loaded_date=None;self.generation=0
        self.streamed_tokens=set();self.snapshot_tokens=set();self.snapshot_batches=0;self.tls_versions={};self.forward_instruments={}

    def enable(self,enabled):
        self.store.set_enabled(enabled)
        with self.lock: self.generation+=1

    def desired(self):
        with self.engine.lock:
            source=self.engine.state['source']; instruments=self.engine.state['instruments'].copy()
        return source,instruments

    def set_forward_instruments(self,instruments):
        with self.lock:
            if instruments!=self.forward_instruments:
                self.forward_instruments=dict(instruments);self.generation+=1

    def forward_quotes(self,instruments):
        # Independent snapshot reader: never consumes the original paper desk's dirty set.
        with self.lock:
            return {identifier:dict(self.quotes[item['instrument_token']]) for identifier,item in instruments.items() if item['instrument_token'] in self.quotes}

    def signature(self):
        source,instruments=self.desired()
        with self.lock:forward=tuple(sorted((k,v['instrument_token']) for k,v in self.forward_instruments.items()))
        return (self.generation,self.kite.key,self.kite.token,source,
                tuple((k,i.get('instrument_token'),i.get('underlying_token')) for k,i in instruments.items()),forward)

    def status(self):
        now=time.time()
        with self.lock:
            fresh=sum(abs(now-datetime.fromisoformat(q['exchange_time']).timestamp())<=30 and now-q['received_at']<=30 for t,q in self.quotes.items() if t in self.universe)
            return dict(provider='KITE',transport='WEBSOCKET_PLUS_REST' if self.snapshot_tokens else 'WEBSOCKET_FULL',phase=self.phase,error=self.error,recording_error=self.recording_error,
                enabled=self.store.enabled(),authenticated=self.kite.status()['authenticated'],order_access=False,
                coverage='CURRENT_NSE_EQ_MASTER',universe_count=len(self.universe),received_symbols=sum(t in self.quotes for t in self.universe),
                fresh_symbols=fresh,stale_or_missing_symbols=len(self.universe)-fresh,subscription_limit=9000,connections=dict(self.connections),
                reconnects=self.reconnects,messages=self.messages,last_message=self.last_message,master_date=self.loaded_date,
                websocket_symbols=len(self.streamed_tokens & set(self.universe)),snapshot_symbols=len(self.snapshot_tokens),snapshot_batches=self.snapshot_batches,tls_versions=dict(self.tls_versions),
                limitations=['Current broker universe is not a historical listing archive','NSE EQ-type master includes shares, ETFs and other EQ-type securities',
                    'No ticks before connection are recovered','Paper fills and margin remain estimates'])

    def scan(self,query='',offset=0,limit=50):
        query=query.upper().strip(); now=time.time()
        with self.lock:
            selected=[(t,i) for t,i in self.universe.items() if query in (i['symbol']+' '+i['name']).upper()]
            selected.sort(key=lambda x:x[1]['symbol'])
            rows=[]
            for token,item in selected[offset:offset+limit]:
                q=self.quotes.get(token,{})
                age=now-datetime.fromisoformat(q['exchange_time']).timestamp() if q else None
                rows.append(dict(item,**{k:v for k,v in q.items() if k!='instrument_token'},age_seconds=round(age,1) if age is not None else None,
                    fresh=bool(q) and abs(age)<=30 and now-q['received_at']<=30))
            return dict(total=len(selected),offset=offset,limit=limit,rows=rows,status=self.status())

    def ingest(self,frame,tokens):
        updates=decode_frame(frame)
        self.accept(updates,tokens,'KITE_WEBSOCKET')

    def accept(self,updates,tokens,transport):
        now=time.time()
        with self.lock:
            self.messages+=1;self.last_message=datetime.fromtimestamp(now,timezone.utc).isoformat()
            for q in updates:
                token=q['instrument_token']
                if token not in tokens: continue
                exchange=datetime.fromisoformat(q['exchange_time']).timestamp()
                old=self.quotes.get(token)
                # Discard out-of-order and future packets; never refresh cached timestamps.
                if exchange>now+2 or (old and exchange<datetime.fromisoformat(old['exchange_time']).timestamp()): continue
                q.update(received_at=now,transport=transport)
                self.quotes[token]=q;self.dirty.add(token)

    def drain(self,instruments):
        now=time.time();updates={}
        with self.lock:
            dirty=self.dirty;self.dirty=set()
            for symbol,item in instruments.items():
                token=item.get('instrument_token')
                if token not in dirty: continue
                q=self.quotes.get(token,{})
                if not q.get('bid') or not q.get('ask'): continue
                if now-q['received_at']>30 or abs(now-datetime.fromisoformat(q['exchange_time']).timestamp())>30: continue
                reference=q if item['kind']=='EQ' else self.quotes.get(item.get('underlying_token'),{})
                if not reference or now-reference['received_at']>30 or abs(now-datetime.fromisoformat(reference['exchange_time']).timestamp())>30: continue
                updates[symbol]=dict(q,spot=reference['last'],spot_exchange_time=reference['exchange_time'],spot_received_at=reference['received_at'],iv=None)
        return updates

    async def _stream(self,number,tokens,signature,stop):
        context=stream_tls_context()
        uri='wss://ws.kite.trade?'+urlencode(dict(api_key=self.kite.key,access_token=self.kite.token))
        self.connections[number]='CONNECTING'
        try:
            async with FixedOriginConnect(uri,ssl=context,proxy=None,compression=None,open_timeout=12,close_timeout=2,
                    ping_interval=20,ping_timeout=20,max_size=1_000_000,max_queue=16,logger=STREAM_LOG) as socket:
                await socket.send(json.dumps(dict(a='subscribe',v=tokens)))
                await socket.send(json.dumps(dict(a='mode',v=['full',tokens])))
                transport=getattr(socket,'transport',None)
                ssl_object=transport.get_extra_info('ssl_object') if transport else None
                self.tls_versions[number]=ssl_object.version() if ssl_object else 'CONTROLLED_TEST'
                self.connections[number]='CONNECTED'
                if all(value=='CONNECTED' for value in self.connections.values()): self.phase='STREAMING'
                while not stop.is_set() and signature==self.signature():
                    try: frame=await asyncio.wait_for(socket.recv(),timeout=1)
                    except TimeoutError: continue
                    if isinstance(frame,bytes): self.ingest(frame,set(tokens))
                    else:
                        payload=json.loads(frame)
                        if payload.get('type')=='error': raise ValueError('Kite rejected the stream subscription; check session and data permissions')
                        # Order postbacks and arbitrary text never enter trading/accounting.
        except InvalidStatus as exc:
            if exc.response.status_code in {401,403}: self.kite.token=''
            raise ValueError('Kite stream handshake failed; verify session, network and subscription') from None
        finally: self.connections[number]='DISCONNECTED'

    async def _snapshots(self,tokens,signature,stop):
        """Overflow coverage is explicit REST sampling, never silently dropped symbols."""
        ordered=sorted(tokens);cursor=0
        while not stop.is_set() and signature==self.signature():
            batch=ordered[cursor:cursor+500];cursor+=len(batch)
            if cursor>=len(ordered): cursor=0
            keys=[self.universe[t]['key'] for t in batch]
            data=await asyncio.to_thread(self.kite._quotes,keys)
            updates=[]
            for token,key in zip(batch,keys):
                raw=data.get(key,{})
                if not raw.get('timestamp') or int(raw.get('instrument_token',0))!=token: continue
                stamp=datetime.fromisoformat(raw['timestamp'])
                if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=IST)
                last=float(raw.get('last_price',0))
                if not math.isfinite(last) or last<=0: continue
                q=dict(instrument_token=token,last=last,exchange_time=stamp.isoformat(),
                    cumulative_volume=max(0,int(raw.get('volume',0))),oi=max(0,int(raw.get('oi',0))))
                buys=[v for v in raw.get('depth',{}).get('buy',[]) if v.get('price',0)>0 and v.get('quantity',0)>0]
                sells=[v for v in raw.get('depth',{}).get('sell',[]) if v.get('price',0)>0 and v.get('quantity',0)>0]
                if buys and sells and float(buys[0]['price'])<=float(sells[0]['price']):
                    q.update(bid=float(buys[0]['price']),ask=float(sells[0]['price']),bid_quantity=int(buys[0]['quantity']),ask_quantity=int(sells[0]['quantity']))
                updates.append(q)
            self.accept(updates,set(batch),'KITE_REST_SNAPSHOT');self.snapshot_batches+=1
            await self._wait(stop,1.1)

    async def run(self,stop):
        delay=1
        while not stop.is_set():
            source,instruments=self.desired()
            try:
                if self.loaded_date!=str(datetime.now(IST).date()):
                    self.phase='LOADING_MASTER'
                    universe=await asyncio.to_thread(self.kite.equity_universe)
                    if not universe: raise FeedConfigurationError('No NSE equity instruments returned by the master')
                    with self.lock:
                        self.universe=universe;self.loaded_date=str(datetime.now(IST).date())
                if not self.kite.status()['authenticated']:
                    self.phase='WAITING_AUTH';self.error='Instrument metadata loaded. Authenticate a subscribed Kite data session for live prices.'
                    if source=='KITE': await asyncio.to_thread(self.engine.market_error,self.error)
                    await self._wait(stop,1);continue
                if not self.store.enabled() and source!='KITE':
                    self.phase='READY';self.error=None;await self._wait(stop,1);continue
                priority=set()
                if source=='KITE':
                    for item in instruments.values():
                        if not item.get('instrument_token'): raise FeedConfigurationError('Reconnect the paper watchlist to refresh instrument tokens')
                        priority.add(item['instrument_token'])
                        if item.get('underlying_token'): priority.add(item['underlying_token'])
                with self.lock:priority.update(item['instrument_token'] for item in self.forward_instruments.values())
                if len(priority)>9000: raise FeedConfigurationError('Paper watchlists exceed provider streaming capacity')
                ordered=sorted(priority)+sorted(set(self.universe)-priority)[:9000-len(priority)]
                self.streamed_tokens=set(ordered);self.snapshot_tokens=set(self.universe)-self.streamed_tokens
                signature=self.signature()
                self.connections={str(i+1):'CONNECTING' for i in range((len(ordered)+2999)//3000)};self.phase='CONNECTING';self.error=None
                async with asyncio.TaskGroup() as tasks:
                    for i,start in enumerate(range(0,len(ordered),3000)):
                        tasks.create_task(self._stream(str(i+1),ordered[start:start+3000],signature,stop))
                    if self.snapshot_tokens: tasks.create_task(self._snapshots(self.snapshot_tokens,signature,stop))
                    tasks.create_task(self._rotate_at_date(stop,signature))
                delay=1
            except asyncio.CancelledError: raise
            except Exception as exc:
                # No transport exception or broker text can expose the credential-bearing URI.
                self.phase='RECONNECTING';self.error=str(exc) if isinstance(exc,FeedConfigurationError) else 'Market stream interrupted; retrying with backoff. Check subscription and network if persistent.'
                self.reconnects+=1
                with self.lock: self.dirty.clear()
                if source=='KITE': await asyncio.to_thread(self.engine.market_error,self.error)
                await asyncio.to_thread(self.store.gap)
                await self._wait(stop,delay);delay=min(30,delay*2)
        self.phase='STOPPED'

    async def _rotate_at_date(self,stop,signature):
        while not stop.is_set() and signature==self.signature():
            if self.loaded_date!=str(datetime.now(IST).date()):
                self.generation+=1;return
            await self._wait(stop,1)

    @staticmethod
    async def _wait(stop,seconds):
        try: await asyncio.wait_for(stop.wait(),timeout=seconds)
        except TimeoutError: pass
