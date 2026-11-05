import copy,csv,io,math,threading
from datetime import datetime,timedelta
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.engine import IST
from app.finance import option_value
from app.workbench.models import Analysis,Basket,SaveDraft,ChainRequest,TemplateApply,Watchlist
from app.workbench.analytics import analyze
from app.workbench.service import Workspace
from app.workbench.templates import catalog,RECIPES

AT=datetime(2026,11,5,10,tzinfo=IST);EXPIRY=AT+timedelta(days=14)
def leg(kind='CE',side='BUY',strike=100,premium=10,**kw):return dict(kind=kind,side=side,strike=strike,premium=premium,lots=1,lot_size=100,expiry=EXPIRY.isoformat() if kind in {'CE','PE'} else None,**kw)
def request(legs,**kw):return Analysis.model_validate(dict(basket=dict(name='Controlled strategy',spot=100,legs=legs,**kw)))

class FakeKite:
    def __init__(self,clock=None):
        self.clock=clock or (lambda:AT);self.key='controlled-key';self.token='controlled-session';self.secret='';self.lock=threading.RLock();self.master_hash='a'*64;self.master_date=self.clock().date();self.calls=[];self.rows=[]
        for i,(name,price) in enumerate([('RELIANCE',1500),('HDFCBANK',1800),('INFY',1600),('ICICIBANK',1250),('TCS',3300),('NIFTY 50',25000)]):self.rows.append(dict(exchange='NSE',tradingsymbol=name,instrument_type='EQ',name=name,instrument_token=str(257+i*256),lot_size='1',tick_size='.05',strike='0',expiry='',segment='INDICES' if name=='NIFTY 50' else 'NSE',test_price=price))
        for rank in range(2):
            expiry=(self.clock()+timedelta(days=7+rank*7)).date().isoformat()
            for i,strike in enumerate(range(24000,26001,50)):
                for k,kind in enumerate(('CE','PE')):self.rows.append(dict(exchange='NFO',tradingsymbol=f'NIFTY{rank}{strike}{kind}',instrument_type=kind,name='NIFTY',instrument_token=str(100002+rank*50000+i*512+k*256),lot_size='65',tick_size='.05',strike=str(strike),expiry=expiry,segment='NFO-OPT'))
    def status(self):return dict(authenticated=bool(self.token),configured=True,login_available=True,order_access=False)
    def master(self):return self.rows
    def underlying_key(self,row):return 'NSE:NIFTY 50' if row['name']=='NIFTY' else 'NSE:'+row['name']
    def _quotes(self,keys):
        self.calls.append(keys);result={};at=self.clock()
        for key in keys:
            row=next((r for r in self.rows if r['exchange']+':'+r['tradingsymbol']==key),None)
            if not row:continue
            if row['exchange']=='NSE':price=row['test_price']
            else:
                expiry=datetime.fromisoformat(row['expiry']).replace(hour=15,minute=30,tzinfo=IST);years=(expiry-at).total_seconds()/(365*86400);price=option_value(25000,float(row['strike']),years,.2,row['instrument_type'])
            bid=max(.05,math.floor(price/.05)*.05-.05);ask=max(bid+.05,math.ceil(price/.05)*.05+.05)
            result[key]=dict(instrument_token=int(row['instrument_token']),last_price=round(price,2),timestamp=at.isoformat(),ohlc={'close':round(price*.99,2)},volume=10000,oi=20000,depth={'buy':[dict(price=round(bid,2),quantity=6500)],'sell':[dict(price=round(ask,2),quantity=6500)]})
        return result
    def _request(self,method,path,**kw):
        if path=='/instruments':
            buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=[k for k in self.rows[0] if k!='test_price']);writer.writeheader();writer.writerows({k:v for k,v in row.items() if k!='test_price'} for row in self.rows);return buf.getvalue()
        if path=='/quote':return self._quotes([v for k,v in kw['params']])
        if '/historical/' in path:
            at=self.clock();token=int(path.split('/')[-2]);raw=next(r for r in self.rows if int(r['instrument_token'])==token);price=raw.get('test_price',200)
            return {'candles':[[(at-timedelta(days=100-i)).replace(hour=0,minute=0,second=0,microsecond=0).isoformat(),round(price*(.97+i*.0003),2),round(price*(.98+i*.0003),2),round(price*(.96+i*.0003),2),round(price*(.975+i*.0003),2),10000+i*75] for i in range(100)]}
        raise AssertionError('No external/broker endpoint is permitted in this controlled fixture: '+path)

@pytest.fixture
def workspace(tmp_path):
    kite=FakeKite();hub=SimpleNamespace(lock=threading.RLock(),quotes={},status=lambda:{'phase':'STREAMING'});service=Workspace(tmp_path,kite,hub,None,lambda:AT)
    yield service,kite
    service.close()

def test_long_call_exact_expiry_bounds_and_breakeven():
    a=analyze(request([leg()]),AT);assert a['unlimited_profit'] and not a['unlimited_loss']
    assert a['max_loss']==1000 and a['breakevens']==[110]
    assert next(p for p in a['points'] if p['spot']==100)['at_expiry']==-1000

def test_bull_call_spread_matches_contract_arithmetic():
    a=analyze(request([leg(premium=10),leg(side='SELL',strike=120,premium=3)]),AT)
    assert a['max_profit']==1300 and a['max_loss']==700 and a['breakevens']==[107]
    assert not a['unlimited_loss'] and 0<=a['model_pop_pct']<=100

def test_short_call_has_unbounded_loss():
    a=analyze(request([leg(side='SELL')]),AT);assert a['unlimited_loss'] and a['max_loss'] is None and a['max_profit']==1000

def test_put_has_finite_zero_price_tail():
    a=analyze(request([leg(kind='PE')]),AT);assert a['max_profit']==9000 and a['max_loss']==1000 and a['breakevens']==[90]

def test_short_straddle_and_condor_breakevens():
    a=analyze(request([leg(side='SELL'),leg(kind='PE',side='SELL')]),AT);assert a['breakevens']==[80,120] and a['unlimited_loss']
    a=analyze(request([leg('PE','BUY',80,1),leg('PE','SELL',90,3),leg('CE','SELL',110,3),leg('CE','BUY',120,1)]),AT)
    assert a['max_profit']==400 and a['max_loss']==600 and a['breakevens']==[86,114]

def test_calendar_does_not_invent_expiry_bounds_or_pop():
    near=leg(side='SELL');far=leg(premium=12);far['expiry']=(EXPIRY+timedelta(days=14)).isoformat()
    a=analyze(request([near,far]),AT);assert a['single_expiry'] is False and a['max_loss'] is None and a['model_pop_pct'] is None
    assert all(p['at_expiry'] is None for p in a['points']) and all(math.isfinite(p['at_target']) for p in a['points'])

def test_costs_and_position_units_are_not_per_lot():
    a=analyze(request([leg()],cost_estimate=50),AT);assert a['max_loss']==1050 and a['breakevens']==[110.5]
    base=analyze(request([leg()]),AT)
    double=leg();double['lots']=2;b=analyze(request([double]),AT)
    for key in ('delta','gamma','theta','vega'):assert b['greeks'][key]==pytest.approx(base['greeks'][key]*2)

def test_box_spread_no_negative_maximum_loss():
    a=analyze(request([leg('CE','BUY',90,12),leg('CE','SELL',110,2),leg('PE','BUY',110,12),leg('PE','SELL',90,2)]),AT)
    assert a['max_loss']>=0 and a['max_profit']>=0

@pytest.mark.parametrize('identifier',list(RECIPES))
def test_every_catalog_recipe_builds_and_analyzes(workspace,identifier):
    service,_=workspace;b=service.model_template(TemplateApply(template_id=identifier,spot=25000,strike_step=50,expiry=EXPIRY,next_expiry=EXPIRY+timedelta(days=14),lot_size=65))
    a=analyze(Analysis(basket=Basket.model_validate(b)),AT)
    assert b['source']=='HYPOTHETICAL' and all(l['price_source']=='MODEL' for l in b['legs'])
    assert a['points'] and all(math.isfinite(p['at_target']) for p in a['points'])

def test_saved_drafts_are_immutable_and_idempotent(workspace):
    service,_=workspace;b=request([leg()]).basket
    one=service.save(SaveDraft(request_id='controlled-1',basket=b));assert service.save(SaveDraft(request_id='controlled-1',basket=b))==one
    changed=b.model_copy(update={'name':'Revised idea'});two=service.save(SaveDraft(request_id='controlled-2',basket=changed,parent_id=one['id']))
    assert two['id']!=one['id'] and service.draft(one['id'])['basket']['name']=='Controlled strategy'
    with pytest.raises(ValueError,match='different'):service.save(SaveDraft(request_id='controlled-1',basket=changed))

def test_saved_draft_tampering_is_detected(workspace):
    service,_=workspace;item=service.save(SaveDraft(request_id='controlled-1',basket=request([leg()]).basket))
    with service.db:service.db.execute("UPDATE drafts SET document=replace(document,'Controlled strategy','Changed strategy')")
    with pytest.raises(ValueError,match='integrity'):service.draft(item['id'])

def test_genuine_chain_shape_identity_and_saved_access(workspace):
    service,kite=workspace;result=service.refresh_chain(ChainRequest(underlying='NIFTY',width=5));c=result['data']
    assert len(c['contracts'])==22 and c['spot']==25000 and result['fresh']
    assert all(p['lot_size']==65 and p['iv_pct'] is not None for p in c['contracts'])
    kite.token='';assert service.chain()['data']['id']==c['id']
    with pytest.raises(ValueError,match='Sign in'):service.refresh_chain(ChainRequest())

def test_chain_freshness_ages_against_exchange_not_fetch(workspace):
    service,_=workspace;service.refresh_chain(ChainRequest());service.clock=lambda:AT+timedelta(seconds=31)
    assert service.chain()['fresh'] is False

def test_watchlist_separate_from_execution(workspace):
    service,_=workspace;service.save_watchlist(Watchlist(keys=['NSE:RELIANCE','NSE:TCS']));result=service.refresh_quotes()
    assert len(result['items'])==2 and all(p['fresh'] for p in result['items'])
    assert service.watchlist()['keys']==['NSE:RELIANCE','NSE:TCS']

def test_api_boundary_and_paper_account_isolation(tmp_path,monkeypatch):
    monkeypatch.delenv('ARTHA_LIVE_TRANSPORT_ENABLED',raising=False);app=create_app(tmp_path/'paper.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as c:
        before=c.get('/api/state').json();body=request([leg()]).model_dump(mode='json')
        assert c.post('/api/workspace/analyze',json=body).status_code==403
        response=c.post('/api/workspace/analyze',json=body,headers={'X-Artha-Client':'paper-workspace'});assert response.status_code==200
        after=c.get('/api/state').json();assert before['account']==after['account'] and before['orders']==after['orders']
        assert not c.get('/api/pilot/status').json()['transport_enabled']


def test_market_template_uses_observed_ask_bid_and_exact_lots(workspace):
    service,_=workspace;chain=service.refresh_chain(ChainRequest(width=10))['data'];body=TemplateApply(source='KITE',template_id='bull_call',spot=25000,expiry=datetime.fromisoformat(chain['contracts'][0]['expiry']),lot_size=1)
    b=service.build_template(body)
    assert b['source']=='KITE' and [l['lot_size'] for l in b['legs']]==[65,65]
    assert [l['price_source'] for l in b['legs']]==['KITE_ASK','KITE_BID']
    for l in b['legs']:
        q=next(p for p in chain['contracts'] if p['key']==l['key'])
        assert l['premium']==q['ask' if l['side']=='BUY' else 'bid']


def test_screener_is_scenario_ranking_with_defined_loss_filter(workspace):
    from app.workbench.models import Screener
    service,_=workspace;result=service.screen(Screener(template=TemplateApply(template_id='long_call',spot=25000,strike_step=50,expiry=EXPIRY,next_expiry=EXPIRY+timedelta(days=14),lot_size=65),target_move_pct=2,days_forward=7,max_loss=10000))
    assert result['results'] and not result['execution_authority'] and not result['margin_verified']
    assert all(not r['unlimited_loss'] and r['max_loss'] is not None and r['max_loss']<=10000 for r in result['results'])
    assert [r['target_pnl'] for r in result['results']]==sorted((r['target_pnl'] for r in result['results']),reverse=True)


def test_screener_does_not_fabricate_missing_market_chains(workspace):
    from app.workbench.models import Screener
    service,_=workspace;result=service.screen(Screener(template=TemplateApply(source='KITE',template_id='long_call',spot=25000,strike_step=50,expiry=EXPIRY,next_expiry=EXPIRY+timedelta(days=14),lot_size=65)))
    assert not result['results'] and len(result['unavailable'])==len(RECIPES)


def test_expired_option_greeks_do_not_invent_remaining_time():
    l=leg();l['expiry']=(AT-timedelta(days=1)).isoformat();a=analyze(request([l]),AT)
    assert a['greeks']['theta']==0 and a['greeks']['vega']==0 and a['model_pop_pct'] is None


def test_backtest_rejects_incompatible_historical_leg_mapping(workspace):
    from app.workbench.models import Backtest
    service,_=workspace;item=service.save(SaveDraft(request_id='historical-control',basket=request([leg()]).basket));service.research=SimpleNamespace(catalog=SimpleNamespace(verify=lambda i:{'manifest':{'instruments':[{'id':'STOCK','kind':'EQ'}]}}))
    with pytest.raises(ValueError,match='compatible'):service.backtest(Backtest(draft_id=item['id'],dataset_id='a'*64,mapping={'underlying':'STOCK','0':'STOCK'}))


def test_rule_forward_reference_reuses_traded_underlying_identity(workspace):
    from app.workbench.models import PaperBasket
    service,_=workspace;b=Basket(name='Controlled stock rule',kind='RULES',underlying='RELIANCE',spot=1500,legs=[dict(key='NSE:RELIANCE',kind='EQ',side='BUY',lots=1,lot_size=1,premium=1500)],source='KITE');item=service.save(SaveDraft(request_id='forward-rules',basket=b))
    received=[];forward=SimpleNamespace(create=lambda p:received.append(p) or {'status':'PAUSED'})
    result=service.paper_basket(PaperBasket(draft_id=item['id'],capital=100000,reviewer='Controlled owner',review_note='Controlled contract calendar data and execution review',data_rights_confirmed=True,assumptions_reviewed=True),forward)
    assert result['status']=='PAUSED' and len(received[0].bindings)==1 and received[0].strategies[0].signal_instruments==['LEG_0']


def test_saved_scenario_reopens_without_silent_reset():
    b=request([leg()]).basket.model_copy(update={'scenario':__import__('app.workbench.models',fromlist=['Scenario']).Scenario(target_move_pct=5,days_forward=3,iv_shift_pp=2)})
    inherited=analyze(Analysis(basket=b),AT);explicit=analyze(Analysis(basket=b,target_move_pct=5,days_forward=3,iv_shift_pp=2),AT)
    assert inherited['target_pnl']==explicit['target_pnl'] and inherited['target_spot']==105


def test_decimal_premiums_preserve_exact_expiry_arithmetic():
    a=analyze(request([leg(premium=0.1),leg(side='SELL',strike=120,premium=0.03)],cost_estimate=0.01),AT)
    assert a['max_loss']==7.01 and a['max_profit']==1992.99


def test_workspace_backup_verifies_immutable_drafts_and_chains(workspace,tmp_path):
    from tools.forward_ops import check_database
    service,_=workspace;service.refresh_chain(ChainRequest());service.save(SaveDraft(request_id='backup-control',basket=request([leg()]).basket))
    result=check_database(tmp_path/'workspace.sqlite3')
    assert result['workspace_drafts_verified']==1 and result['workspace_chains_verified']==1
    with service.db:service.db.execute("UPDATE drafts SET document=replace(document,'Controlled strategy','Changed strategy')")
    with pytest.raises(ValueError,match='integrity'):check_database(tmp_path/'workspace.sqlite3')


def test_forward_rejects_contract_identity_with_edited_strike(workspace):
    from app.workbench.models import PaperBasket
    service,_=workspace;chain=service.refresh_chain(ChainRequest())['data'];b=service.build_template(TemplateApply(source='KITE',template_id='bull_call',spot=25000,expiry=datetime.fromisoformat(chain['contracts'][0]['expiry'])))
    b['legs'][0]['strike']+=50;draft=service.save(SaveDraft(request_id='edited-strike',basket=Basket.model_validate(b)))
    with pytest.raises(ValueError,match='strike or expiry'):service.paper_basket(PaperBasket(draft_id=draft['id'],capital=100000,reviewer='Controlled owner',review_note='Explicit controlled contract review',data_rights_confirmed=True,assumptions_reviewed=True),None)


def test_different_future_and_option_expiries_do_not_claim_contractual_final_bounds():
    future=leg(kind='FUT',premium=100);future['expiry']=(EXPIRY+timedelta(days=7)).isoformat()
    a=analyze(request([future,leg()]),AT)
    assert not a['single_expiry'] and a['max_loss'] is None and a['max_profit'] is None


def test_strategy_catalog_remains_available_without_broker_master(workspace):
    service,kite=workspace
    def offline():raise ValueError('Controlled unavailable master')
    kite.master=offline;meta=service.meta()
    assert not meta['master_available'] and not meta['underlyings'] and len(meta['templates'])==51
