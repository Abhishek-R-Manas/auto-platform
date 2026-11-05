"""Controlled watchlist persistence, push snapshots and display-only candle checks."""
import asyncio,json,threading
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.displaybars import DisplayBars
from app.workbench.models import Watchlist,NewWatchlist
from app.workbench.service import Workspace
from app.live import MarketHub
from app.engine import PaperEngine
from test_workbench import FakeKite,AT

@pytest.fixture
def desk(tmp_path):
    engine=PaperEngine(tmp_path/'paper.sqlite3');kite=FakeKite();hub=MarketHub(kite,engine,tmp_path/'market');workspace=Workspace(tmp_path/'workspace',kite,hub,None,lambda:AT)
    yield workspace,hub,engine,kite
    workspace.close();hub.store.close();engine.close()

def test_old_watchlist_migrates_without_losing_names_or_order(desk):
    w,_,_,_=desk
    with w.db:w.db.execute('INSERT INTO settings VALUES(?,?)',('watchlist',json.dumps(dict(name='Prior saved list',keys=['NSE:TCS','NSE:INFY']))))
    one=w.watchlist();assert one['name']=='Prior saved list' and one['keys']==['NSE:TCS','NSE:INFY']
    two=w.create_watchlist(NewWatchlist(name='Options'));assert not two['keys'] and two['id']!='main'
    assert w.activate_watchlist('main')['keys']==one['keys']

def test_named_lists_reorder_rename_and_delete_are_independent(desk):
    w,_,_,_=desk;first=w.watchlist();second=w.create_watchlist(NewWatchlist(name='Stocks',keys=['NSE:TCS','NSE:RELIANCE']))
    w.save_watchlist(Watchlist(name='Reordered',keys=['NSE:RELIANCE','NSE:TCS']))
    assert w.watchlist()['name']=='Reordered' and w.watchlist()['keys'][0]=='NSE:RELIANCE'
    assert w.activate_watchlist(first['id'])['keys']==first['keys']
    assert len(w.delete_watchlist(second['id'])['watchlists']['lists'])==1
    with pytest.raises(ValueError,match='at least one'):w.delete_watchlist(first['id'])

def test_empty_and_bounded_lists_and_unknown_keys(desk):
    w,_,_,_=desk;w.save_watchlist(Watchlist(name='Empty',keys=[]));assert not w.watchlist()['items']
    for i in range(9):w.create_watchlist(NewWatchlist(name='List '+str(i)))
    with pytest.raises(ValueError,match='10 watchlists'):w.create_watchlist(NewWatchlist(name='Overflow'))
    with pytest.raises(ValueError,match='absent'):w.save_watchlist(Watchlist(keys=['NSE:NOT_A_CONTRACT']))
    with pytest.raises(ValueError):Watchlist(keys=['NSE:TCS','NSE:TCS'])

def test_display_subscription_does_not_change_execution_account(desk):
    w,hub,engine,_=desk;before=engine.snapshot();w.create_watchlist(NewWatchlist(name='Derivatives',keys=['NFO:NIFTY024000CE']))
    w.connect_quotes('NSE:RELIANCE');assert 'NFO:NIFTY024000CE' in hub.workspace_instruments
    assert engine.snapshot()==before and hub.store.enabled()
    assert hub.display_bars.tokens=={v['instrument_token'] for v in hub.workspace_instruments.values()}

def test_stream_snapshot_does_not_fetch_master_or_consume_dirty_quotes(desk):
    w,hub,engine,kite=desk;w.connect_quotes('NSE:RELIANCE');token=257
    q=dict(instrument_token=token,last=1510,bid=1509,ask=1511,close=1500,exchange_time=AT.isoformat(),received_at=AT.timestamp(),transport='KITE_WEBSOCKET',cumulative_volume=100)
    with hub.lock:hub.quotes[token]=q;hub.dirty.add(token)
    def forbidden():raise AssertionError('A display snapshot must not perform a network/master request')
    kite.master=forbidden;before=engine.snapshot();snapshot=w.watchlist('main','NSE:RELIANCE','5minute')
    assert snapshot['selected_quote']['quote']['last']==1510 and snapshot['selected_quote']['fresh']
    assert hub.dirty=={token} and engine.snapshot()==before
    w.clock=lambda:AT+timedelta(seconds=31);assert not w.watchlist()['items'][0]['fresh']

def tick(at=AT,last=100,volume=100,**kw):return dict(instrument_token=257,last=last,cumulative_volume=volume,exchange_time=at.isoformat(),received_at=at.timestamp(),**kw)

def test_observed_bars_use_all_accepted_prices_no_prior_volume_or_gap_filling():
    bars=DisplayBars();bars.select({257});bars.observe(tick());bars.observe(tick(AT+timedelta(seconds=1),105,120));bars.observe(tick(AT+timedelta(seconds=2),98,125))
    row=bars.get(257,'5minute')[0];assert (row['open'],row['high'],row['low'],row['close'],row['volume'])==(100,105,98,98,25) and row['partial']
    bars.observe(tick(AT+timedelta(minutes=20),102,180));rows=bars.get(257,'5minute');assert len(rows)==2 and rows[1]['open']==102 and rows[1]['volume']==0
    bars.observe(tick(AT+timedelta(minutes=19),999,200));assert bars.get(257,'5minute')==rows

def test_daily_bar_uses_actual_provider_ohlc_and_intraday_does_not():
    bars=DisplayBars();bars.select({257});bars.observe(tick(last=102,open=98,high=110,low=90,close=95))
    day=bars.get(257,'day')[0];intraday=bars.get(257,'5minute')[0]
    assert (day['open'],day['high'],day['low'],day['close'],day['volume'])==(98,110,90,102,100)
    assert day['source']=='KITE_DAY_OHLC' and intraday['open']==102 and intraday['volume']==0

def test_stale_off_session_and_unselected_ticks_do_not_build_live_bars():
    bars=DisplayBars();bars.select({257});bars.observe(tick(AT.replace(hour=8)));bars.observe(dict(tick(),received_at=AT.timestamp()+31));bars.observe(dict(tick(),instrument_token=999));assert not bars.get(257,'day')
    bars.observe(tick());bars.select(set());assert not bars.get(257,'day')

def test_sixty_minute_bar_anchors_at_nse_open_and_buffers_are_bounded():
    bars=DisplayBars();bars.select({257})
    for i in range(100):bars.observe(tick(AT+timedelta(minutes=i),100+i,100+i))
    assert len(bars.get(257,'5minute'))==12
    assert bars.get(257,'60minute')[0]['time'].endswith('09:15:00+05:30')

def test_sse_payload_is_same_origin_display_data_without_credentials(tmp_path):
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.post('/api/workspace/watchlists',json={'name':'Blocked'}).status_code==403
        assert client.get('/api/workspace/quotes/stream?watchlist_id=../bad').status_code==422
        assert '/api/workspace/quotes/stream' in client.get('/openapi.json').json()['paths']
        before=client.get('/api/state').json();lists=client.post('/api/workspace/watchlists',json={'name':'API list'},headers={'X-Artha-Client':'paper-workspace'}).json()
        assert lists['name']=='API list' and lists['keys']==[]
        after=client.get('/api/state').json();assert before['account']==after['account'] and before['orders']==after['orders']


def test_list_identity_and_order_persist_across_workspace_reopen(desk,tmp_path):
    w,hub,_,kite=desk;one=w.watchlist();two=w.create_watchlist(NewWatchlist(name='Other list',keys=['NSE:TCS']))
    w.save_watchlist(Watchlist(name='Scoped original',keys=['NSE:INFY','NSE:RELIANCE']),one['id'])
    assert w.watchlist()['id']==two['id'] and w.watchlist()['keys']==['NSE:TCS']
    reopened=Workspace(tmp_path/'workspace',kite,hub,None,lambda:AT)
    try:assert reopened.watchlist(one['id'])['keys']==['NSE:INFY','NSE:RELIANCE'] and reopened.watchlist()['id']==two['id']
    finally:reopened.close()


def test_sse_emits_finite_display_snapshot_and_stops_on_disconnect(desk):
    from app.workbench.api import router
    w,_,engine,_=desk;route=next(r for r in router(w,None).routes if r.path=='/api/workspace/quotes/stream');before=engine.snapshot()
    class Disconnected:
        calls=0
        async def is_disconnected(self):self.calls+=1;return self.calls>1
    async def read():
        response=await route.endpoint(Disconnected(),'main','NSE:RELIANCE','day');events=[]
        async for event in response.body_iterator:events.append(event)
        assert response.headers['x-accel-buffering']=='no' and response.media_type=='text/event-stream'
        return events
    events=asyncio.run(read());assert len(events)==1 and events[0].startswith('event: quotes\ndata: ')
    assert 'controlled-session' not in events[0] and 'controlled-key' not in events[0] and engine.snapshot()==before


def test_cash_index_is_a_watchable_reference_and_cannot_be_bought_as_equity(desk):
    from app.market import KiteData
    w,_,_,kite=desk;w.save_watchlist(Watchlist(name='Index references',keys=['NSE:NIFTY 50']))
    assert w.watchlist()['items'][0]['kind']=='INDEX'
    broker=KiteData();broker.master=kite.master
    def forbidden(*args):raise AssertionError('No quote or order endpoint is allowed for an invalid cash-index order')
    broker._quotes=forbidden
    with pytest.raises(ValueError,match='chart references'):broker.load(['NSE:NIFTY 50'])
