"""Controlled protocol/accounting tests; these fixtures are never genuine market quotes."""
import asyncio
from datetime import datetime, timezone
import json
import struct
import time

from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.engine import PaperEngine, IST
from app.live import Captures, FixedOriginConnect, MarketHub, decode_frame, stream_tls_context
from app.market import KiteData
from app.research.catalog import Catalog

HEADERS={'X-Artha-Client':'paper-workspace'}


def frame(token=257,stamp=None,last=100,bid=99.95,ask=100.05,quantity=10,index=False):
    stamp=int(time.time()) if stamp is None else stamp
    packet=bytearray(32 if index else 184)
    def put(at,value): struct.pack_into('>I',packet,at,int(value))
    put(0,token);put(4,round(last*100));put(28 if index else 60,stamp)
    if not index:
        put(16,1000);put(48,50)
        put(64,quantity);put(68,round(bid*100));put(124,quantity);put(128,round(ask*100))
    return struct.pack('>HH',1,len(packet))+packet


@pytest.fixture
def hub(tmp_path):
    engine=PaperEngine(tmp_path/'paper.sqlite3');kite=KiteData();kite.key='test-key';kite.token='test-session'
    result=MarketHub(kite,engine,tmp_path/'market')
    yield result
    result.store.close();engine.close()


def instrument(symbol='RELIANCE',token=257):
    return dict(symbol=symbol,name=symbol,kind='EQ',exchange='NSE',underlying=symbol,lot_size=1,tick_size=.05,
                base=100,instrument_token=token,expiry=None,master_hash='test-master')


def quote(at=None):
    at=datetime(2026,11,4,10,0,tzinfo=IST).timestamp() if at is None else at
    return dict(last=100,bid=99.95,ask=100.05,bid_quantity=10,ask_quantity=10,spot=100,iv=None,
                received_at=at,exchange_time=datetime.fromtimestamp(at,IST).isoformat(),transport='KITE_WEBSOCKET')


def test_full_stock_and_index_protocol_and_heartbeat():
    q=decode_frame(frame())[0]
    assert (q['last'],q['bid'],q['ask'],q['bid_quantity'],q['cumulative_volume'])==(100,99.95,100.05,10,1000)
    assert decode_frame(b'\x00')==[]
    index=decode_frame(frame(token=265,index=True))[0]
    assert index['last']==100 and 'bid' not in index


@pytest.mark.parametrize('value',[b'',b'\x00\x01',b'\x00\x01\x00\xb8'+b'x'*10,frame()+b'x',b'\xff\xff'])
def test_malformed_frames_are_rejected(value):
    with pytest.raises(ValueError): decode_frame(value)


def test_illiquid_and_crossed_quotes_keep_prices_without_executable_depth():
    assert decode_frame(frame(quantity=0))[0]['last']==100
    assert 'bid' not in decode_frame(frame(bid=101,ask=100))[0]


def test_out_of_order_future_and_unsubscribed_updates_do_not_refresh(hub):
    now=int(time.time());hub.ingest(frame(stamp=now),{257});first=hub.quotes[257].copy()
    hub.ingest(frame(stamp=now-10,last=50),{257})
    hub.ingest(frame(stamp=now+60,last=200),{257})
    hub.ingest(frame(token=513,last=500),{257})
    assert hub.quotes=={257:first}


def test_partial_stream_drain_requires_fresh_underlying(hub):
    items={'RELIANCE':instrument(),'NIFTY-FUT':dict(instrument('NIFTY-FUT',514),kind='FUT',underlying='NIFTY',underlying_token=265)}
    hub.ingest(frame(),{257});hub.ingest(frame(token=514),{514})
    assert set(hub.drain(items))=={'RELIANCE'}
    hub.ingest(frame(token=265,index=True,last=24000),{265});hub.ingest(frame(token=514),{514})
    assert hub.drain(items)['NIFTY-FUT']['spot']==24000
    assert hub.drain(items)=={}  # Cached ticks cannot supply new liquidity on another pump.


def test_scanner_covers_every_master_entry_and_marks_old_quotes_stale(hub):
    hub.universe={i*256+1:dict(key=f'NSE:STOCK{i:04}',symbol=f'STOCK{i:04}',name='Example',kind='EQ') for i in range(1,3501)}
    token=257;hub.ingest(frame(token=token,stamp=int(time.time())-60),{token})
    result=hub.scan('',3450,50)
    assert result['total']==3500 and len(result['rows'])==50 and result['status']['universe_count']==3500
    assert hub.scan('STOCK0001')['rows'][0]['fresh'] is False
    assert hub.status()['fresh_symbols']==0


def test_stream_batches_all_symbols_into_multiple_connections(hub,monkeypatch):
    hub.enable(True)
    universe={i*256+1:dict(key=f'NSE:S{i}',symbol=f'S{i}',name='Example',kind='EQ') for i in range(1,3006)}
    monkeypatch.setattr(hub.kite,'equity_universe',lambda:universe)
    subscriptions=[]
    class FakeSocket:
        async def send(self,value):
            payload=json.loads(value)
            if payload['a']=='subscribe': subscriptions.append(payload['v'])
        async def recv(self): await asyncio.sleep(.01);return b'\x00'
    class FakeConnect:
        def __init__(self,uri,**kwargs):
            assert uri.startswith('wss://ws.kite.trade?') and kwargs['proxy'] is None
            assert kwargs['ssl'].verify_mode!=0 and kwargs['max_size']==1_000_000
        async def __aenter__(self): return FakeSocket()
        async def __aexit__(self,*args): return False
    monkeypatch.setattr('app.live.FixedOriginConnect',FakeConnect)
    async def run():
        stop=asyncio.Event();task=asyncio.create_task(hub.run(stop))
        for _ in range(100):
            if len(subscriptions)==2: break
            await asyncio.sleep(.01)
        stop.set();await asyncio.wait_for(task,3)
    asyncio.run(run())
    assert len(subscriptions)==2 and max(map(len,subscriptions))==3000
    assert set().union(*(set(x) for x in subscriptions))==set(universe)


def test_transport_exceptions_are_redacted_and_retried(hub,monkeypatch):
    hub.enable(True);monkeypatch.setattr(hub.kite,'equity_universe',lambda:{257:instrument()})
    class BadConnect:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): raise OSError('credential-bearing-URL-must-not-escape')
        async def __aexit__(self,*a): pass
    monkeypatch.setattr('app.live.FixedOriginConnect',BadConnect)
    async def run():
        stop=asyncio.Event();task=asyncio.create_task(hub.run(stop))
        for _ in range(100):
            if hub.reconnects: break
            await asyncio.sleep(.01)
        stop.set();await task
    asyncio.run(run())
    assert hub.reconnects==1 and 'credential-bearing' not in json.dumps(hub.status())


def test_redirects_are_never_followed_with_token():
    error=ValueError('redirect')
    assert FixedOriginConnect('wss://ws.kite.trade?api_key=test').process_redirect(error) is error


def test_capture_preserves_real_source_and_times_without_altering_main_account(hub,tmp_path):
    catalog=Catalog(tmp_path/'catalog');items={'M&M':instrument('M&M')}
    cash=hub.engine.state['cash'];capture=hub.store.start('Controlled test capture',items,False)
    hub.store.append({'M&M':quote()},items);hub.store.gap()
    with pytest.raises(ValueError,match='Stop recording'): hub.store.import_into(capture['id'],catalog)
    hub.store.stop(capture['id']);dataset=hub.store.import_into(capture['id'],catalog)
    assert dataset['status']=='READY' and dataset['manifest']['source_kind']=='BROKER'
    assert not dataset['manifest']['rights_confirmed'] and dataset['manifest']['instruments'][0]['symbol']=='M&M'
    assert '1 connection gaps' in dataset['manifest']['notes']
    assert hub.store.import_into(capture['id'],catalog)['id']==dataset['id']
    assert hub.engine.state['cash']==cash and not hub.engine.state['orders']
    catalog.db.close()


def test_recording_limit_and_restart_preserve_evidence(hub,tmp_path):
    items={'RELIANCE':instrument()};capture=hub.store.start('Limited capture',items)
    with hub.store.lock,hub.store.db:
        capture['max_rows']=1;hub.store._save(capture)
    hub.store.append({'RELIANCE':quote()},items);hub.store.append({'RELIANCE':quote()},items)
    assert hub.store.list()[0]['status']=='LIMIT_REACHED' and hub.store.list()[0]['rows']==1
    restarted=Captures(tmp_path/'restart')
    capture=restarted.start('Interrupted',items);restarted.db.close()
    reopened=Captures(tmp_path/'restart')
    assert reopened.list()[0]['status']=='INTERRUPTED';reopened.close()


def test_source_change_stops_capture_without_mixing_symbols(hub):
    capture=hub.store.start('Old source',{'RELIANCE':instrument()})
    hub.store.append({'INFY':quote()},{'INFY':instrument('INFY',513)})
    assert hub.store.list()[0]['status']=='SOURCE_CHANGED' and hub.store.list()[0]['rows']==0
    with pytest.raises(ValueError,match='no actual quotes'): hub.store.import_into(capture['id'],None)


def test_partial_updates_do_not_replenish_other_symbols_liquidity(hub):
    engine=hub.engine;engine.state['used_liquidity']={'RELIANCE:BUY':5,'INFY:BUY':7}
    q=engine.state['quotes']['RELIANCE'].copy();q.pop('received_at',None)
    engine.apply_quotes({'RELIANCE':q})
    assert engine.state['used_liquidity'].get('INFY:BUY')==7
    assert 'RELIANCE:BUY' not in engine.state['used_liquidity']


def test_actual_receipt_age_is_not_refreshed_by_engine(hub):
    engine=hub.engine;q=engine.state['quotes']['RELIANCE'].copy();q['received_at']=time.time()-60
    engine.apply_quotes({'RELIANCE':q})
    assert not engine._fresh('RELIANCE')


def test_no_credentials_api_is_honest_and_rejects_live_requests(tmp_path,monkeypatch):
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN'): monkeypatch.delenv(name,raising=False)
    app=create_app(tmp_path/'api.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.get('/api/market/universe').json()['total']==0
        assert client.get('/api/market/status').json()['authenticated'] is False
        assert client.post('/api/market/connect',json={'enabled':True},headers=HEADERS).status_code==400
        assert client.post('/api/market/captures',json={},headers=HEADERS).status_code==400
        assert client.post('/api/market/connect',json={'enabled':False}).status_code==403
        assert client.post('/api/market/source',json={'source':'KITE','instruments':['https://evil.example']},headers=HEADERS).status_code==422
        assert client.get('/api/state').json()['live_trading_available'] is False


def test_master_cache_and_complete_equity_universe(monkeypatch):
    kite=KiteData();calls=[]
    header='exchange,tradingsymbol,instrument_type,name,expiry,strike,lot_size,tick_size,instrument_token'
    content=header+'\nNSE,AAA,EQ,AAA,,0,1,0.05,257\nNSE,BBB,EQ,BBB,,0,1,0.05,513\nBSE,CCC,EQ,CCC,,0,1,0.05,772\n'
    def request(*args,**kwargs): calls.append(args);return content
    monkeypatch.setattr(kite,'_request',request)
    assert len(kite.equity_universe())==2
    assert kite.search('BB')[0]['key']=='NSE:BBB'
    assert len(calls)==1 and len(kite.master_hash)==64


def test_partial_updates_do_not_invent_another_instruments_history(hub):
    engine=hub.engine
    before=len(engine.state['prices'].get('INFY',[]))
    engine.apply_quotes({'RELIANCE':engine.state['quotes']['RELIANCE'].copy()})
    assert len(engine.state['prices'].get('INFY',[]))==before


def test_public_metadata_loads_without_a_price_session(hub,monkeypatch):
    hub.kite.token='';hub.kite.key=''
    monkeypatch.setattr(hub.kite,'equity_universe',lambda:{257:dict(instrument(),key='NSE:RELIANCE')})
    async def run():
        stop=asyncio.Event();task=asyncio.create_task(hub.run(stop))
        for _ in range(100):
            if hub.phase=='WAITING_AUTH': break
            await asyncio.sleep(.01)
        stop.set();await task
    asyncio.run(run())
    assert hub.status()['universe_count']==1 and not hub.status()['authenticated']
    assert 'last' not in hub.scan()['rows'][0]
    assert hub.status()['received_symbols']==0


def test_overflow_snapshot_feed_covers_every_remaining_symbol(hub,monkeypatch):
    hub.universe={i*256+1:dict(key=f'NSE:S{i}',symbol=f'S{i}',name='Fixture',kind='EQ') for i in range(1,702)}
    batches=[]
    def snapshots(keys):
        batches.append(keys)
        return {key:dict(instrument_token=int(key.split('S')[-1])*256+1,last_price=100,timestamp=datetime.now(IST).isoformat()) for key in keys}
    monkeypatch.setattr(hub.kite,'_quotes',snapshots)
    async def run():
        stop=asyncio.Event();task=asyncio.create_task(hub._snapshots(set(hub.universe),hub.signature(),stop))
        for _ in range(200):
            if hub.snapshot_batches>=2: break
            await asyncio.sleep(.01)
        stop.set();await task
    asyncio.run(run())
    assert hub.status()['received_symbols']==701 and max(map(len,batches))==500
    assert all(q['transport']=='KITE_REST_SNAPSHOT' for q in hub.quotes.values())


def test_stream_tls_exception_keeps_certificates_and_modern_ciphers():
    import ssl
    context=stream_tls_context()
    assert context.minimum_version==ssl.TLSVersion.TLSv1_2 and context.check_hostname
    assert context.verify_mode==ssl.CERT_REQUIRED
    assert all(cipher['protocol']=='TLSv1.3' or ('ECDHE' in cipher['name'] and ('GCM' in cipher['name'] or 'CHACHA20' in cipher['name'])) for cipher in context.get_ciphers())


def test_fresh_derivative_cannot_hide_stale_underlying(hub):
    engine=hub.engine;symbol=next(k for k,i in engine.state['instruments'].items() if i['kind']=='FUT')
    engine.state['source']='KITE'
    q=engine.state['quotes'][symbol]
    q.update(received_at=time.time(),exchange_time=datetime.now(IST).isoformat(),spot_received_at=time.time()-60,spot_exchange_time=datetime.now(IST).isoformat())
    assert not engine._fresh(symbol)
    q['spot_received_at']=time.time()
    assert engine._fresh(symbol)
