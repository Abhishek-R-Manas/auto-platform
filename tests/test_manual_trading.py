"""Manual authority is independent of strategy approval, but shares order risk and durability."""
from datetime import timedelta
import pytest
from pydantic import ValidationError
from test_pilot_charts import AT,Kite,Broker,Forward,intention,send,mandate,suspend_stops
from app.pilot.service import Pilot,ManualMandate,Arm,Submit,CONFIRM

def authority(**changes):
    data=mandate().model_dump();data.pop('portfolio_id');data['confirmation']='ENABLE MANUAL NSE DELIVERY TRADING';data.update(changes);return ManualMandate(**data)

@pytest.fixture
def manual(tmp_path):
    now=[AT];f=Forward();f.qualified=False;b=Broker();k=Kite();s=Pilot(tmp_path,k,f,b,lambda:now[0]);s.reconcile();s.configure_manual(authority());s.reconcile();s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    yield s,b,k,f,now
    s.close()

def test_manual_fill_protection_cancel_exit_and_journal(manual):
    s,b,*_=manual
    first=send(s);assert first['status']=='ACKNOWLEDGED';b.fill(5);s.reconcile()
    assert s.protection()['unprotected_units']==0 and len(b.sent)==2
    assert s.orders()[0]['origin']=='PROTECTIVE' and s.orders()[0]['order']['quantity']==5
    suspend_stops(s,b);exit_order=send(s,intention(request_id='manual-exit',side='SELL'))
    assert exit_order['status']=='ACKNOWLEDGED' and s.audit()['ok']
    assert s.status()['mandate']['mode']=='MANUAL_NSE_CNC'

def test_manual_does_not_call_forward_services(manual):
    s,b,k,f,_=manual
    def forbidden(*a):raise AssertionError('Manual authority must not use strategy evidence')
    f.report=forbidden;f.portfolio=forbidden
    assert s.preview(intention())['real_money'] and s.readiness()['strategy_execution_enabled'] is False

def test_strategy_route_still_requires_qualification(tmp_path):
    f=Forward();f.qualified=False;s=Pilot(tmp_path,Kite(),f,Broker(),lambda:AT)
    with pytest.raises(ValueError,match='Forward qualification'):s.configure(mandate())
    s.close()

@pytest.mark.parametrize('change',[{'capital':0},{'max_order':2001},{'daily_loss':2001},{'max_trade_loss':101},{'confirmation':'yes'},{'portfolio_id':'a'*32}])
def test_manual_limits_require_explicit_valid_values(change):
    with pytest.raises(ValidationError):authority(**change)

def test_manual_cannot_configure_from_cached_display(tmp_path):
    s=Pilot(tmp_path,Kite(),Forward(),Broker(),lambda:AT)
    with pytest.raises(ValueError,match='Reconcile'):s.configure_manual(authority())
    assert s.status()['mandate'] is None;s.close()

def test_manual_account_binding_and_consents(tmp_path):
    s=Pilot(tmp_path,Kite(),Forward(),Broker(),lambda:AT);s.reconcile()
    with pytest.raises(ValueError,match='differs'):s.configure_manual(authority(account_id='OTHER'))
    with pytest.raises(ValueError,match='Confirm'):s.configure_manual(authority(static_ip_confirmed=False))
    with pytest.raises(ValueError,match='equities only'):s.configure_manual(authority(symbols=['OTHER']))
    s.close()

def test_transport_off_blocks_manual_arm_and_send(tmp_path):
    b=Broker();b.enabled=False;s=Pilot(tmp_path,Kite(),Forward(),b,lambda:AT);s.reconcile();s.configure_manual(authority())
    with pytest.raises(ValueError,match='transport'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    with pytest.raises(ValueError,match='paused|disabled'):s.preview(intention())
    assert not b.sent;s.close()

@pytest.mark.parametrize('days',[2,3,5,8])
def test_weekend_holiday_or_unreviewed_calendar_blocks_order(manual,days):
    s,b,k,f,now=manual;now[0]=AT+timedelta(days=days);s.reconcile()
    with pytest.raises(ValueError,match='session|calendar|expired'):s.preview(intention())
    assert not b.sent

def test_freshness_and_loss_halt_for_manual(manual):
    s,b,k,f,now=manual;now[0]+=timedelta(seconds=16)
    assert not s.readiness()['can_arm']
    with pytest.raises(ValueError,match='Reconcile'):s.preview(intention())
    b.data['positions']['net']=[{'pnl':-101}];s.reconcile();assert not s.status()['armed']
    assert not s.readiness()['can_arm'] and not b.sent

def test_manual_uncertain_order_is_not_resent(manual):
    s,b,*_=manual;b.uncertain=True;p=s.preview(intention());body=Submit(preview_id=p['id'],confirmation=CONFIRM)
    assert s.submit(body)['status']=='UNCERTAIN';assert s.submit(body)['status']=='UNCERTAIN';assert len(b.sent)==1
    s.reconcile();assert s.orders()[0]['status']=='OPEN'

def test_manual_restart_keeps_display_but_requires_reconciliation_and_activation(tmp_path):
    now=[AT];b=Broker();k=Kite();f=Forward();s=Pilot(tmp_path,k,f,b,lambda:now[0]);s.reconcile();s.configure_manual(authority());s.close();s=Pilot(tmp_path,k,f,b,lambda:now[0])
    assert s.status()['portfolio_view'] is not None and not s.status()['armed']
    with pytest.raises(ValueError,match='Reconcile'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    s.reconcile();s.arm(Arm(confirmation='ARM REAL MONEY AB1234'));assert s.status()['armed'];s.close()

def test_no_reallocation_after_durable_order(manual):
    s,b,*_=manual;send(s);s.reconcile()
    with pytest.raises(ValueError,match='reallocation'):s.configure_manual(authority())

def test_calendar_review_expiry_limits_authority(tmp_path):
    s=Pilot(tmp_path,Kite(),Forward(),Broker(),lambda:AT+timedelta(days=6));s.reconcile()
    with pytest.raises(ValueError,match='calendar'):s.configure_manual(authority(expires_at=AT+timedelta(days=8)))
    s.close()
