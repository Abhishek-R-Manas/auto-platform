"""Broad manual stock permission retains current-contract, funding and execution checks."""
import copy
from datetime import timedelta
import pytest
from pydantic import ValidationError
from app.pilot.service import Pilot,ManualMandate,Mandate,Arm,Submit,CONFIRM
from app.pilot.universe import eligible
from app.research.catalog import digest
from test_pilot_charts import AT,Kite,Broker,Forward,intention,mandate
from test_manual_trading import authority

class UniverseKite(Kite):
    def __init__(self):
        super().__init__();self.rows=super().master();self.rows.append(self.row('OTHER',514))
    def row(self,symbol,token=777,**changes):return dict(exchange='NSE',tradingsymbol=symbol,instrument_type='EQ',instrument_token=str(token),lot_size='1',tick_size='.05',segment='NSE')|changes
    def master(self):return copy.deepcopy(self.rows)
    def _quotes(self,keys):
        base=super()._quotes(keys)['NSE:TEST'];return {key:dict(copy.deepcopy(base),instrument_token=int(next(r['instrument_token'] for r in self.rows if 'NSE:'+r['tradingsymbol']==key))) for key in keys}

def wide(**changes):return authority(instrument_scope='ALL_NSE_EQ',symbols=[],**changes)

@pytest.fixture
def all_stocks(tmp_path):
    k=UniverseKite();b=Broker();now=[AT];s=Pilot(tmp_path,k,Forward(),b,lambda:now[0]);s.reconcile();s.configure_manual(wide());s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    yield s,b,k,now
    s.close()

def test_allows_a_current_stock_without_a_comma_list(all_stocks):
    s,b,k,now=all_stocks;p=s.preview(intention(symbol='OTHER'));o=s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM))
    assert o['status']=='ACKNOWLEDGED' and len(b.sent)==1 and b.sent[0][0]['symbol']=='OTHER'
    assert s.status()['mandate']['symbols']==[] and s.status()['mandate']['instrument_scope']=='ALL_NSE_EQ'
    assert s.status()['readiness']['instrument_scope_label']=='All supported NSE stocks and ETFs' and s.audit()['ok']

def test_new_eligible_listing_is_checked_against_updated_master(all_stocks):
    s,b,k,_=all_stocks;k.rows.append(k.row('NEWLISTING',999));k.master_hash='updated-fixture-master'
    p=s.preview(intention(symbol='NEWLISTING'));assert p['evidence']['master_hash']=='updated-fixture-master' and not b.sent

@pytest.mark.parametrize('row',[dict(exchange='BSE'),dict(exchange='NFO',instrument_type='CE'),dict(segment='INDICES'),dict(lot_size='50'),dict(lot_size='bad'),dict(lot_size=None)])
def test_all_scope_rejects_unsupported_instruments_before_sending(all_stocks,row):
    s,b,k,_=all_stocks;k.rows.append(k.row('BLOCKED',888,**row))
    with pytest.raises(ValueError,match='one-unit NSE'):s.preview(intention(symbol='BLOCKED'))
    assert not b.sent

def test_unknown_or_delisted_stock_is_rejected(all_stocks):
    s,b,k,_=all_stocks
    with pytest.raises(ValueError,match='one-unit NSE'):s.preview(intention(symbol='UNKNOWN'))
    k.rows=[r for r in k.rows if r['tradingsymbol']!='OTHER']
    with pytest.raises(ValueError,match='one-unit NSE'):s.preview(intention(symbol='OTHER'))
    assert not b.sent

@pytest.mark.parametrize('change',[{'quantity':11},{'limit_price':100.01}])
def test_all_scope_does_not_bypass_order_limits_or_tick_size(all_stocks,change):
    s,b,*_=all_stocks
    with pytest.raises(ValueError):s.preview(intention(symbol='OTHER',**change))
    assert not b.sent

def test_all_scope_still_requires_fresh_reconciliation_and_quotes(all_stocks):
    s,b,k,now=all_stocks;now[0]+=timedelta(seconds=16)
    with pytest.raises(ValueError,match='Reconcile'):s.preview(intention(symbol='OTHER'))
    s.reconcile();k.age=31
    with pytest.raises(ValueError,match='Fresh'):s.preview(intention(symbol='OTHER'))
    assert not b.sent

def test_all_scope_does_not_authorize_selling_imported_holdings(all_stocks):
    s,b,*_=all_stocks;b.data['holdings']=[dict(exchange='NSE',tradingsymbol='OTHER',quantity=100,day_change=0)]
    s.reconcile()
    with pytest.raises(ValueError,match='pilot-owned'):s.preview(intention(symbol='OTHER',side='SELL'))
    assert not b.sent

def test_external_pending_target_order_blocks_but_unrelated_stock_does_not(all_stocks):
    s,b,*_=all_stocks;b.data['orders']=[dict(tradingsymbol='TEST',status='OPEN',order_id='external')];s.reconcile()
    assert s.preview(intention(symbol='OTHER'))['real_money']
    b.data['orders'].append(dict(tradingsymbol='OTHER',status='OPEN',order_id='external2'));s.reconcile()
    with pytest.raises(ValueError,match='external'):s.preview(intention(symbol='OTHER'))
    assert not b.sent

def test_selected_permission_does_not_expand_implicitly(tmp_path):
    k=UniverseKite();b=Broker();s=Pilot(tmp_path,k,Forward(),b,lambda:AT);s.reconcile();s.configure_manual(authority())
    legacy=s._get('mandate');legacy.pop('instrument_scope');legacy.pop('hash');legacy['hash']=digest(legacy)
    with s.transaction():s._set('mandate',legacy);s._event('MANDATE',legacy)
    s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    with pytest.raises(ValueError,match='outside'):s.preview(intention(symbol='OTHER'))
    assert s.status()['readiness']['instrument_scope_label']=='TEST' and s.audit()['ok'];s.close()

@pytest.mark.parametrize('change',[{'instrument_scope':'ALL'},{'instrument_scope':'ALL_NSE_EQ','symbols':['TEST']},{'instrument_scope':'SELECTED','symbols':[]},{'instrument_scope':'SELECTED','symbols':['*']}])
def test_explicit_scope_has_no_ambiguous_wildcard(change):
    values=authority().model_dump(mode='json')|change
    with pytest.raises(ValidationError):ManualMandate.model_validate(values)

def test_strategy_authority_cannot_request_all_stocks():
    values=mandate().model_dump(mode='json')|{'instrument_scope':'ALL_NSE_EQ'}
    with pytest.raises(ValidationError):Mandate.model_validate(values)

def test_all_scope_review_is_explicit_and_side_effect_free(tmp_path):
    s=Pilot(tmp_path,UniverseKite(),Forward(),Broker(),lambda:AT);s.reconcile();before=s.audit()
    r=s.review_manual(wide().model_dump(mode='json'))
    assert r['can_save'] and r['instrument_scope']=='ALL_NSE_EQ' and r['instrument_scope_label']=='All supported NSE stocks and ETFs'
    assert s.audit()==before and s.status()['mandate'] is None;s.close()

def test_all_scope_requires_an_eligible_master(tmp_path):
    k=UniverseKite();k.rows=[];s=Pilot(tmp_path,k,Forward(),Broker(),lambda:AT);s.reconcile()
    r=s.review_manual(wide().model_dump(mode='json'));assert not r['can_save'] and 'instrument_scope' in r['field_errors'];s.close()

@pytest.mark.parametrize('limits,reason',[({'capital':1000},'capital reservation'),({'max_orders_per_day':1},'Daily pilot order count')])
def test_limits_are_shared_across_stocks_not_reset_per_symbol(tmp_path,limits,reason):
    k=UniverseKite();b=Broker();s=Pilot(tmp_path,k,Forward(),b,lambda:AT);s.reconcile();s.configure_manual(wide(**limits));s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    first=s.preview(intention());s.submit(Submit(preview_id=first['id'],confirmation=CONFIRM));s.reconcile()
    with pytest.raises(ValueError,match=reason):s.preview(intention(symbol='OTHER',request_id='second-stock-order'))
    assert len(b.sent)==1;s.close()

def test_external_order_on_a_managed_position_blocks_new_exposure_in_another_stock(all_stocks):
    s,b,*_=all_stocks;p=s.preview(intention());s.submit(Submit(preview_id=p['id'],confirmation=CONFIRM));b.fill(5);s.reconcile()
    assert s.protection()['unprotected_units']==0 and len(b.sent)==2
    b.data['orders'].append(dict(tradingsymbol='TEST',status='OPEN',order_id='external-managed-stock'));s.reconcile()
    with pytest.raises(ValueError,match='external'):s.preview(intention(symbol='OTHER',request_id='another-stock-order'))
    assert len(b.sent)==2
