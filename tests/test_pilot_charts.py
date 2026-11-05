import copy,threading
from datetime import datetime,timedelta
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.api import create_app
from app.engine import IST
from app.charts import MarketCharts,ChartRequest
from app.pilot.broker import KiteBroker
from app.pilot.service import Pilot,Mandate,Intention,Submit,Arm,CONFIRM

AT=datetime(2026,11,5,10,0,tzinfo=IST)
class Kite:
    def __init__(self):self.key='fixture-key';self.token='fixture-session';self.lock=threading.RLock();self.master_hash='fixture-master';self.age=0;self.bad=False;self.calls=0
    def status(self):return {'authenticated':bool(self.token)}
    def master(self):return [dict(exchange='NSE',tradingsymbol='TEST',instrument_type='EQ',instrument_token='257',lot_size='1',tick_size='.05')]
    def _quotes(self,keys):return {'NSE:TEST':dict(instrument_token=257,timestamp=(AT-timedelta(seconds=self.age)).isoformat(),depth=dict(buy=[dict(price=99.95,quantity=1000)],sell=[dict(price=100.05,quantity=1000)]))}
    def _request(self,*args,**kwargs):
        self.calls+=1
        return {'candles':[['2026-11-04T00:00:00+05:30',100,110,90,105,1000],['2026-11-05T00:00:00+05:30',105,120,100,110,500]] if not self.bad else [['2026-11-04T00:00:00+05:30',100,90,80,105,1000]]}

class Forward:
    qualified=True
    def __init__(self):self.hash='approved';self.doc=dict(bindings={'TEST':dict(kind='EQ',key='NSE:TEST')},build={'code_hash':'fixture'},manifest={'calendar':dict(timezone='Asia/Kolkata',session={'open':'09:15','close':'15:30'},holidays=[],special_sessions={},settlement_days=1)},config=dict(capital=100000,risk=dict(trade_pct=5,open_pct=10),calendar_valid_until='2026-12-31'))
    def report(self,identifier):return dict(ready_for_phase5_review=self.qualified,evidence_hash=self.hash)
    def portfolio(self,identifier):return dict(document=copy.deepcopy(self.doc))

class Broker:
    enabled=True
    def __init__(self):
        self.sent=[];self.cancelled=[];self.uncertain=False;self.hook=None
        self.data=dict(account_id='AB1234',exchanges=['NSE'],products=['CNC'],order_types=['LIMIT','SL'],funds={'net':10000,'available':dict(cash=10000,live_balance=10000,collateral=0,adhoc_margin=0)},orders=[],trades=[],holdings=[],positions={'net':[]})
    def snapshot(self):return copy.deepcopy(self.data)
    def place(self,order,tag):
        if self.hook:self.hook()
        self.sent.append((order,tag));identifier=str(1000+len(self.sent))
        self.data['orders'].append(dict(order_id=identifier,tradingsymbol=order['symbol'],exchange='NSE',product='CNC',transaction_type=order['side'],order_type=order.get('order_type','LIMIT'),trigger_price=order.get('stop_loss',0) if order.get('order_type')=='SL' else 0,quantity=order['quantity'],price=order['limit_price'],tag=tag,filled_quantity=0,average_price=0,status='OPEN'))
        if self.uncertain:raise TimeoutError('fixture secret must never leak')
        return {'order_id':identifier}
    def cancel(self,identifier):self.cancelled.append(identifier);return {'order_id':identifier}
    def fill(self,count,status='COMPLETE'):
        o=self.data['orders'][-1];o.update(filled_quantity=count,average_price=100,status=status)
        self.data['trades']=[dict(order_id=o['order_id'],quantity=count,average_price=100,trade_id='trade1')]
        self.data['positions']['net']=[dict(exchange='NSE',tradingsymbol='TEST',product='CNC',quantity=count,pnl=0)]

def mandate(**kw):return Mandate(**dict(account_id='AB1234',portfolio_id='a'*32,symbols=['TEST'],capital=2000,daily_loss=100,max_trade_loss=50,protective_orders_authorized=True,max_order=1000,max_orders_per_day=3,expires_at=AT+timedelta(days=1),static_ip_confirmed=True,delivery_authorized=True,reviewer='controlled owner',**kw))
def intention(**kw):return Intention(**({'request_id':'controlled1','symbol':'TEST','side':'BUY','quantity':5,'limit_price':100,'stop_loss':99,'rationale':'Controlled acceptance'}|kw))

@pytest.fixture
def pilot(tmp_path):
    now=[AT];kite=Kite();forward=Forward();broker=Broker();service=Pilot(tmp_path,kite,forward,broker,lambda:now[0]);service.configure(mandate());service.reconcile();service.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    yield service,broker,kite,forward,now
    service.close()

def send(service,body=None):
    preview=service.preview(body or intention());return service.submit(Submit(preview_id=preview['id'],confirmation=CONFIRM))

def test_chart_completed_candles_persist_without_auth(tmp_path):
    kite=Kite();c=MarketCharts(tmp_path,kite,lambda:AT)
    result=c.refresh(ChartRequest(symbol='NSE:TEST'))
    assert len(result['data']['candles'])==1 and result['data']['latest_completed_close']==105
    assert result['data']['executable'] is False
    c.close();kite.token='';c=MarketCharts(tmp_path,kite,lambda:AT)
    assert c.get('NSE:TEST')['data']['latest_completed_close']==105
    with pytest.raises(ValueError,match='Sign in'):c.refresh(ChartRequest(symbol='NSE:TEST'))
    c.close()

def test_chart_rejects_malformed_without_destroying_cache(tmp_path):
    kite=Kite();now=[AT];c=MarketCharts(tmp_path,kite,lambda:now[0]);before=c.refresh(ChartRequest(symbol='NSE:TEST'))
    kite.bad=True;now[0]+=timedelta(minutes=2)
    with pytest.raises(ValueError,match='Invalid'):c.refresh(ChartRequest(symbol='NSE:TEST'))
    assert c.get('NSE:TEST')['data']==before['data'];c.close()

def test_chart_refresh_pacing(tmp_path):
    kite=Kite();c=MarketCharts(tmp_path,kite,lambda:AT);c.refresh(ChartRequest(symbol='NSE:TEST'));c.refresh(ChartRequest(symbol='NSE:TEST'));assert kite.calls==1;c.close()

def test_default_transport_cannot_write():
    broker=KiteBroker(Kite(),enabled=False)
    with pytest.raises(ValueError,match='disabled'):broker.place(intention().model_dump(),'tag')
    with pytest.raises(ValueError,match='allowlist'):broker.request('POST','/orders/amo')
    with pytest.raises(ValueError,match='allowlist'):broker.request('GET','https://attacker.invalid/')

def test_send_reservation_commits_before_side_effect_and_duplicate_is_idempotent(pilot):
    s,b,*_=pilot
    b.hook=lambda:assert_reserved(s)
    preview=s.preview(intention());body=Submit(preview_id=preview['id'],confirmation=CONFIRM)
    first=s.submit(body);second=s.submit(body)
    assert first==second and len(b.sent)==1 and first['status']=='ACKNOWLEDGED'
    assert s.audit()['ok']

def assert_reserved(s):assert s.orders()[0]['status']=='SENDING' and s.db.in_transaction is False

def test_timeout_retains_intention_and_reconciles_by_tag_without_resending(pilot):
    s,b,*_=pilot;b.uncertain=True;preview=s.preview(intention());body=Submit(preview_id=preview['id'],confirmation=CONFIRM)
    item=s.submit(body);assert item['status']=='UNCERTAIN' and not s.status()['armed']
    assert s.submit(body)['status']=='UNCERTAIN' and len(b.sent)==1
    s.reconcile();assert s.orders()[0]['status']=='OPEN' and len(b.sent)==1 and s.audit()['ok']

def test_partial_fill_comes_only_from_broker_trades(pilot):
    s,b,*_=pilot;send(s);b.fill(2,status='OPEN');s.reconcile();o=next(o for o in s.orders() if o['order']['side']=='BUY')
    assert o['filled_quantity']==2 and o['status']=='OPEN' and len(o['trades'])==1
    b.data['orders'][0]['filled_quantity']=3
    with pytest.raises(ValueError,match='reconciliation'):s.reconcile()
    assert not s.status()['armed'] and next(o for o in s.orders() if o['order']['side']=='BUY')['filled_quantity']==2

def test_account_mismatch_blocks_and_pauses(pilot):
    s,b,*_=pilot;b.data['account_id']='OTHER'
    with pytest.raises(ValueError):s.reconcile()
    assert not s.status()['armed']

@pytest.mark.parametrize('change', [{'symbol':'OTHER'},{'limit_price':100.01},{'quantity':11},{'quantity':True},{'limit_price':float('nan')},{'quantity':'1'}])
def test_invalid_or_out_of_mandate_orders_never_reach_broker(pilot,change):
    s,b,*_=pilot
    with pytest.raises((ValueError,ValidationError)):send(s,intention(**change))
    assert not b.sent

def test_stale_quote_blocks(pilot):
    s,b,k,*_=pilot;k.age=31
    with pytest.raises(ValueError,match='Fresh'):s.preview(intention())
    assert not b.sent

def test_unowned_stock_cannot_be_sold(pilot):
    s,b,*_=pilot;b.data['holdings']=[dict(exchange='NSE',tradingsymbol='TEST',quantity=100,day_change=0)]
    s.reconcile()
    with pytest.raises(ValueError,match='pilot-owned'):s.preview(intention(side='SELL'))

def test_pending_sell_reserves_actual_owned_fills(pilot):
    s,b,*_=pilot;send(s);b.fill(5);s.reconcile();suspend_stops(s,b)
    result=send(s,intention(request_id='controlled2',side='SELL',quantity=4));assert result['status']=='ACKNOWLEDGED'
    s.reconcile()
    with pytest.raises(ValueError,match='pilot-owned'):s.preview(intention(request_id='controlled3',side='SELL',quantity=2))

def test_external_pending_order_blocks(pilot):
    s,b,*_=pilot;b.data['orders']=[dict(tradingsymbol='TEST',status='OPEN',order_id='external')];s.reconcile()
    with pytest.raises(ValueError,match='external'):s.preview(intention())

def test_daily_loss_halt_is_latched(pilot):
    s,b,*_=pilot;b.data['positions']['net']=[dict(pnl=-101)];s.reconcile();assert not s.status()['armed']
    b.data['positions']['net']=[];s.reconcile()
    with pytest.raises(ValueError,match='loss halt'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))

def test_preview_expiry_and_changed_session(pilot):
    s,b,k,f,now=pilot;p=s.preview(intention());now[0]+=timedelta(seconds=21)
    with pytest.raises(ValueError,match='expired'):s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM))
    now[0]=AT;k.token='changed'
    with pytest.raises(ValueError,match='Reconcile'):s.preview(intention())
    assert not b.sent

def test_qualification_revocation_blocks_tick_and_new_orders(pilot):
    s,b,k,f,now=pilot;f.qualified=False;s.tick();assert not s.status()['armed']
    with pytest.raises(ValueError):s.preview(intention())
    assert not b.sent

def test_cancel_ack_is_not_completed_cancellation(pilot):
    s,b,*_=pilot;item=send(s);s.reconcile();cancel=s.cancel(item['id']);assert cancel['status']=='CANCEL_PENDING'
    with pytest.raises(ValueError,match='uncertain or pending'):s.cancel(item['id'])
    assert len(b.cancelled)==1

def test_restart_pauses_and_never_retries(tmp_path):
    k=Kite();f=Forward();b=Broker();s=Pilot(tmp_path,k,f,b,lambda:AT);s.configure(mandate());s.reconcile();s.arm(Arm(confirmation='ARM REAL MONEY AB1234'));send(s);s.close()
    s=Pilot(tmp_path,k,f,b,lambda:AT);assert not s.status()['armed'] and s.orders()[0]['status']=='ACKNOWLEDGED';assert len(b.sent)==1;s.close()

def test_tampering_blocks(pilot):
    s,b,*_=pilot;item=send(s);item['order']['quantity']=1
    with s.db:s.db.execute('UPDATE intents SET document=? WHERE id=?',(__import__('json').dumps(item),item['id']))
    assert not s.audit()['ok']

def test_no_unqualified_mandate(tmp_path):
    f=Forward();f.qualified=False;s=Pilot(tmp_path,Kite(),f,Broker(),lambda:AT)
    with pytest.raises(ValueError,match='qualification'):s.configure(mandate())
    assert s.status()['mandate'] is None;s.close()

def test_api_isolated_pilot_and_chart_cache(tmp_path,monkeypatch):
    monkeypatch.delenv('ARTHA_LIVE_TRANSPORT_ENABLED',raising=False)
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as c:
        assert c.get('/api/health').json()['pilot']['transport_enabled'] is False
        assert c.get('/api/pilot/status').json()['armed'] is False
        assert c.post('/api/pilot/reconcile',json={}).status_code==403
        assert c.post('/api/pilot/arm',headers={'X-Artha-Client':'paper-workspace'},json={'confirmation':'anything'}).status_code==400
        assert c.get('/api/market/charts').json()['cached'] is False
        assert c.get('/api/state').json()['mode']=='PAPER'
        assert c.get('/api/state').json()['reconcile']['ok']


def test_reducing_sell_remains_available_after_entry_pause(pilot):
    s,b,*_=pilot;send(s);b.fill(5);s.reconcile();suspend_stops(s,b);s.pause()
    assert send(s,intention(request_id='reduce001',side='SELL',quantity=2))['status']=='ACKNOWLEDGED'

def test_reducing_sell_survives_loss_halt_and_revoked_qualification(pilot):
    s,b,k,f,now=pilot;send(s);b.fill(5);b.data['positions']['net'][0]['pnl']=-101;s.reconcile();suspend_stops(s,b);f.qualified=False
    assert not s.status()['armed']
    assert send(s,intention(request_id='reduce002',side='SELL',quantity=2))['status']=='ACKNOWLEDGED'

def test_changed_snapshot_and_mandate_are_detected(pilot):
    s,b,*_=pilot
    with s.transaction():
        snap=s._get('snapshot');snap['funds']['net']=999999;s._set('snapshot',snap)
    assert not s.audit()['ok']
    with pytest.raises(ValueError,match='integrity'):s.preview(intention())

def test_stale_reconciliation_and_outside_session_block(pilot):
    s,b,k,f,now=pilot;now[0]+=timedelta(seconds=16)
    with pytest.raises(ValueError,match='Reconcile'):s.preview(intention())
    now[0]=AT.replace(hour=16);s.reconcile()
    with pytest.raises(ValueError,match='session'):s.preview(intention())

def test_reservations_block_excess_capital_even_with_broker_cash(pilot):
    s,b,k,f,now=pilot
    for n in range(2):
        send(s,intention(request_id='reserve00'+str(n),quantity=9));s.reconcile()
    with pytest.raises(ValueError,match='capital reservation'):s.preview(intention(request_id='reserve002',quantity=5))


def test_latched_integrity_failure_survives_restart(tmp_path):
    k=Kite();f=Forward();b=Broker();s=Pilot(tmp_path,k,f,b,lambda:AT);s.configure(mandate());s.reconcile()
    with s.transaction():
        snap=s._get('snapshot');snap['funds']['net']=999999;s._set('snapshot',snap)
    s.db.close();s=Pilot(tmp_path,k,f,b,lambda:AT)
    assert not s.audit()['ok'] and not s.status()['armed'];s.close()

def test_duplicate_broker_tags_halt_reconciliation(pilot):
    s,b,*_=pilot;send(s);extra=copy.deepcopy(b.data['orders'][0]);extra['order_id']='99999';b.data['orders'].append(extra)
    with pytest.raises(ValueError):s.reconcile()
    assert not s.status()['armed']

def test_wrong_order_confirmation_never_sends(pilot):
    s,b,*_=pilot;p=s.preview(intention())
    with pytest.raises(ValueError,match='confirmation'):s.submit(Submit(preview_id=p['id'],confirmation='yes'))
    assert not b.sent

def test_backup_checks_pilot_evidence(pilot,tmp_path):
    from tools.forward_ops import check_database
    s,b,*_=pilot;send(s)
    assert check_database(tmp_path/'pilot.sqlite3')['pilot_events_verified']>0


def test_expired_mandate_blocks_entries_but_cancellation_remains_available(pilot):
    s,b,k,f,now=pilot;item=send(s);s.reconcile();now[0]=AT+timedelta(days=2);s.reconcile()
    with pytest.raises(ValueError,match='expired'):s.preview(intention(request_id='expired00'))
    assert s.cancel(item['id'])['status']=='CANCEL_PENDING'


def suspend_stops(s,b):
    for o in s.orders():
        if o.get('origin')=='PROTECTIVE' and o['status'] not in {'CANCELLED','REJECTED','COMPLETE'}:
            s.cancel(o['id'])
            next(x for x in b.data['orders'] if x['order_id']==o['broker_order_id'])['status']='CANCELLED'
            s.reconcile()


def test_partial_fills_get_exact_native_stop_quantities(pilot):
    s,b,*_=pilot;send(s);b.fill(2,status='OPEN');s.reconcile()
    stops=[o for o in s.orders() if o.get('origin')=='PROTECTIVE'];assert len(stops)==1
    assert stops[0]['order']['quantity']==2 and stops[0]['order']['order_type']=='SL'
    assert stops[0]['order']['stop_loss']==99 and s.protection()['unprotected_units']==0
    s.reconcile();assert len(b.sent)==2


def test_uncertain_stop_never_repeats(pilot):
    s,b,*_=pilot;send(s);b.fill(5);b.uncertain=True;s.reconcile()
    assert s.protection()['unprotected_units']==5 and not s.status()['armed'] and len(b.sent)==2
    s.reconcile();assert len(b.sent)==2


def test_missing_stop_or_excess_planned_loss_blocks_buy(pilot):
    s,b,*_=pilot
    with pytest.raises(ValueError):s.preview(intention(stop_loss=None))
    with pytest.raises(ValueError,match='budget'):s.preview(intention(stop_loss=50))
    assert not b.sent


def test_loss_halt_requests_cancellation_of_known_entry_remainder(pilot):
    s,b,*_=pilot;item=send(s);b.data['positions']['net']=[dict(pnl=-101)];s.reconcile()
    assert b.cancelled==[item['broker_order_id']] and s.orders()[0]['status']=='CANCEL_PENDING'
    s.reconcile();assert len(b.cancelled)==1

def test_pause_cancels_entries_but_retains_protective_stops(pilot):
    s,b,*_=pilot;send(s);b.fill(2,status='OPEN');s.reconcile();s.pause(cancel_entries=True)
    assert b.cancelled==['1001']
    stop=next(o for o in s.orders() if o.get('origin')=='PROTECTIVE');assert stop['status']=='ACKNOWLEDGED'


@pytest.mark.parametrize('changes',[{'limit_price':100.001},{'stop_loss':99.001}])
def test_fractional_paise_cannot_reach_broker(pilot,changes):
    s,b,*_=pilot
    with pytest.raises(ValueError,match='whole paise'):s.preview(intention(**changes))
    assert not b.sent


def test_fill_outside_authorized_limit_is_not_accepted(pilot):
    s,b,*_=pilot;send(s);b.fill(5);b.data['orders'][0]['average_price']=101;b.data['trades'][0]['average_price']=101
    with pytest.raises(ValueError,match='reconciliation'):s.reconcile()
    assert not s.status()['armed'] and len(b.sent)==1


def test_collateral_does_not_fund_delivery_purchases(pilot):
    s,b,*_=pilot;b.data['funds']['available']['collateral']=9999;s.reconcile()
    with pytest.raises(ValueError):s.preview(intention())
    assert not b.sent


def test_quote_fetch_cannot_extend_snapshot_or_quote_freshness(pilot):
    s,b,k,f,now=pilot;quotes=k._quotes
    def slow(keys):
        now[0]+=timedelta(seconds=31);return quotes(keys)
    k._quotes=slow
    with pytest.raises(ValueError):s.preview(intention())
    assert not b.sent


def test_later_partial_fills_add_only_missing_native_protection(pilot):
    s,b,*_=pilot;send(s);b.fill(2,status='OPEN');s.reconcile()
    b.data['orders'][0].update(filled_quantity=5,average_price=100,status='COMPLETE');b.data['trades'][0]['quantity']=5;b.data['positions']['net'][0]['quantity']=5
    s.reconcile();stops=[o for o in s.orders() if o.get('origin')=='PROTECTIVE']
    assert sorted(o['order']['quantity'] for o in stops)==[2,3] and len(b.sent)==3 and s.protection()['unprotected_units']==0
    s.reconcile();assert len(b.sent)==3


def test_changed_native_stop_trigger_halts_without_replacement(pilot):
    s,b,*_=pilot;send(s);b.fill(5);s.reconcile();b.data['orders'][1]['trigger_price']=1
    with pytest.raises(ValueError):s.reconcile()
    assert not s.status()['armed'] and len(b.sent)==2
