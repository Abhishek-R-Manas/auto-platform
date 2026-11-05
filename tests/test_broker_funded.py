"""No app financial ceilings does not authorize unconfirmed or unfunded orders."""
import copy,json
from datetime import timedelta
import pytest
from pydantic import ValidationError
from app.pilot.service import Pilot,ManualMandate,Mandate,Intention,Arm,Submit,CONFIRM
from app.pilot.limits import BUDGET_FIELDS,broker_funded
from app.research.catalog import digest
from tools.forward_ops import check_database
from test_pilot_charts import AT,Kite,Broker,Forward,intention,mandate
from test_manual_trading import authority

def no_caps(**changes):
    values={key:None for key in BUDGET_FIELDS}|{'limit_mode':'BROKER_FUNDS'}|changes
    return authority(**values)

class LiquidKite(Kite):
    def _quotes(self,keys):
        result=super()._quotes(keys)
        for q in result.values():q['depth']['buy'][0]['quantity']=1000000;q['depth']['sell'][0]['quantity']=1000000
        return result

def set_cash(broker,cash):
    broker.data['funds']={'net':cash,'available':dict(cash=cash,live_balance=cash,collateral=0,adhoc_margin=0)}

@pytest.fixture
def uncapped(tmp_path):
    now=[AT];b=Broker();set_cash(b,2000000);k=LiquidKite();s=Pilot(tmp_path,k,Forward(),b,lambda:now[0]);s.reconcile();s.configure_manual(no_caps());s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    yield s,b,k,now
    s.close()

def test_large_order_and_quantity_have_no_old_app_ceiling(uncapped):
    s,b,*_=uncapped;p=s.preview(intention(quantity=12000));assert p['evidence']['notional_paise']==120000000
    o=s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM));assert o['status']=='ACKNOWLEDGED' and len(b.sent)==1
    assert p['evidence']['risk_checks'][0]['status']=='NOT_SET'
    assert all(s.status()['mandate'][key] is None for key in BUDGET_FIELDS)
    assert 'Infinity' not in json.dumps(s.status()) and s.audit()['ok']

def test_actual_cash_and_pending_reservations_still_bound_orders(uncapped):
    s,b,*_=uncapped;set_cash(b,1000);s.reconcile()
    with pytest.raises(ValueError,match='actual broker funds'):s.preview(intention(quantity=11))
    p=s.preview(intention(quantity=5));s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM));s.reconcile()
    with pytest.raises(ValueError,match='actual broker funds'):s.preview(intention(quantity=5,request_id='another-request'))
    assert len(b.sent)==1

def test_daily_entry_count_is_not_limited_to_ten(uncapped):
    s,b,*_=uncapped
    for index in range(12):
        s.reconcile();p=s.preview(intention(quantity=1,request_id='many-orders-'+str(index)));s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM))
    assert len(b.sent)==12 and s.audit()['ok']

def test_no_inferred_daily_loss_halt_or_per_trade_budget(uncapped):
    s,b,*_=uncapped;b.data['positions']['net']=[dict(pnl=-20000)];s.reconcile()
    assert s.status()['armed'] and s._get('loss_halt_day') is None
    assert s.preview(intention(quantity=1000,stop_loss=50))['evidence']['planned_risk_paise']>1000000

def test_protective_exits_still_follow_verified_fills(uncapped):
    s,b,*_=uncapped;p=s.preview(intention());s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM));b.fill(5);s.reconcile()
    assert s.protection()['unprotected_units']==0 and len(b.sent)==2 and b.sent[-1][0]['order_type']=='SL'

def test_no_caps_still_rejects_stale_and_uncertain_requests(uncapped):
    s,b,k,now=uncapped;now[0]+=timedelta(seconds=16)
    with pytest.raises(ValueError,match='Reconcile'):s.preview(intention())
    s.reconcile();b.uncertain=True;p=s.preview(intention());body=Submit(preview_id=p['id'],confirmation=CONFIRM)
    assert s.submit(body)['status']=='UNCERTAIN' and s.submit(body)['status']=='UNCERTAIN' and len(b.sent)==1
    assert not s.status()['armed']

def test_no_caps_keeps_owner_permissions_and_confirmations_required(tmp_path):
    s=Pilot(tmp_path,Kite(),Forward(),Broker(),lambda:AT);s.reconcile()
    with pytest.raises(ValueError,match='Confirm'):s.configure_manual(no_caps(static_ip_confirmed=False))
    s.configure_manual(no_caps())
    with pytest.raises(ValueError,match='exact broker account'):s.arm(Arm(confirmation='yes'))
    s.arm(Arm(confirmation='ARM REAL MONEY AB1234'));p=s.preview(intention())
    with pytest.raises(ValueError,match='confirmation'):s.submit(Submit(preview_id=p['id'],confirmation='yes'))
    assert not s.broker.sent;s.close()

def test_custom_values_can_exceed_old_software_ceilings(tmp_path):
    b=Broker();set_cash(b,2000000);s=Pilot(tmp_path,LiquidKite(),Forward(),b,lambda:AT);s.reconcile()
    own=authority(capital=500000,max_order=200000,daily_loss=100000,max_trade_loss=50000,max_orders_per_day=100)
    assert s.review_manual(own.model_dump(mode='json'))['can_save'];s.configure_manual(own);s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    assert s.preview(intention(quantity=2000))['evidence']['notional_paise']==20000000;s.close()

@pytest.mark.parametrize('values',[{'limit_mode':'CUSTOM','capital':None},{'limit_mode':'BROKER_FUNDS'},{'limit_mode':'ALL'},{'capital':float('inf')},{'capital':float('nan')},{'max_orders_per_day':0}])
def test_ambiguous_or_invalid_budgets_are_rejected(values):
    with pytest.raises(ValidationError):authority(**values)

def test_no_caps_is_explicit_and_not_inferred_from_missing_values():
    values=authority().model_dump(mode='json')
    for key in BUDGET_FIELDS:values.pop(key)
    with pytest.raises(ValidationError):ManualMandate.model_validate(values)
    assert all(no_caps().model_dump()[key] is None for key in BUDGET_FIELDS)

def test_strategy_authority_cannot_disable_reviewed_budgets():
    values=mandate().model_dump(mode='json')|{'limit_mode':'BROKER_FUNDS'}
    with pytest.raises(ValidationError):Mandate.model_validate(values)

def test_legacy_saved_limits_remain_enforced(tmp_path):
    s=Pilot(tmp_path,Kite(),Forward(),Broker(),lambda:AT);s.reconcile();s.configure_manual(authority());legacy=s._get('mandate');legacy.pop('limit_mode');legacy.pop('hash');legacy['hash']=digest(legacy)
    with s.transaction():s._set('mandate',legacy);s._event('MANDATE',legacy)
    assert not broker_funded(legacy);s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    with pytest.raises(ValueError,match='Per-order'):s.preview(intention(quantity=11))
    assert s.status()['mandate']['max_order']==1000;s.close()

def test_no_cap_review_never_saves_or_activates(tmp_path):
    s=Pilot(tmp_path,Kite(),Forward(),Broker(),lambda:AT);s.reconcile();before=s.audit()
    r=s.review_manual(no_caps().model_dump(mode='json'));assert r['can_save'] and r['limit_mode']=='BROKER_FUNDS' and 'no app-defined caps' in r['budget_mode_label']
    assert s.audit()==before and s.status()['mandate'] is None and not s.status()['armed'];s.close()

def test_null_budgets_survive_restart_and_backup_checks(tmp_path):
    k=Kite();b=Broker();f=Forward();s=Pilot(tmp_path,k,f,b,lambda:AT);s.reconcile();s.configure_manual(no_caps());s.close()
    assert check_database(tmp_path/'pilot.sqlite3')['integrity']=='ok'
    s=Pilot(tmp_path,k,f,b,lambda:AT);assert broker_funded(s.status()['mandate']) and not s.status()['armed']
    with pytest.raises(ValueError,match='Reconcile'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    s.close()

def test_schema_has_no_fixed_order_quantity_or_price_ceiling():
    assert intention(quantity=20000).quantity==20000
    assert intention(limit_price=2000000,stop_loss=1999999).limit_price==2000000


def test_limit_order_can_exceed_displayed_top_depth_and_fill_partially(tmp_path):
    b=Broker();set_cash(b,2000000);s=Pilot(tmp_path,Kite(),Forward(),b,lambda:AT);s.reconcile();s.configure_manual(no_caps());s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    p=s.preview(intention(quantity=2000));s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM));b.fill(2,status='OPEN');s.reconcile()
    parent=next(o for o in s.orders() if o['order']['side']=='BUY');stop=next(o for o in s.orders() if o.get('origin')=='PROTECTIVE')
    assert parent['filled_quantity']==2 and parent['status']=='OPEN' and stop['order']['quantity']==2 and s.protection()['unprotected_units']==0
    assert s.audit()['ok'];s.close()

def test_owner_limit_price_is_not_restricted_by_old_app_deviation(uncapped):
    s,b,*_=uncapped;p=s.preview(intention(limit_price=90,stop_loss=80));assert p['order']['limit_price']==90 and not b.sent
