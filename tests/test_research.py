"""Independent economic examples and research-contract acceptance checks."""
import base64
import copy
import csv
from contextlib import closing
from datetime import datetime,timedelta
import io
import itertools
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.api import create_app
from app.charges import DEFAULTS
from app.research.catalog import Catalog,digest
from app.research.schemas import ImportData,Run
from app.research.replay import Replay
from app.research.jobs import Research,open_job,save_meta,execute,metadata
from app.research.fixtures import synthetic_fixture,templates
from app.research.signals import capabilities,validate_rule,fit_model
from app.research.metrics import metrics

ZONE=ZoneInfo('Asia/Kolkata')
HEADERS={'X-Artha-Client':'paper-workspace'}


@pytest.fixture
def catalog(tmp_path):
    store=Catalog(tmp_path/'research')
    yield store
    store.close()


def dataset(store,prices=(100,100,100,100),instruments=None,frequency='DAY',actions=None,volume=1000,extra=None):
    instruments=instruments or [dict(id='EQ:A',symbol='A',kind='EQ',underlying='EQ:A')]
    rows=[]
    for index,price in enumerate(prices):
        at=datetime(2023,1,2,15,30,tzinfo=ZONE)+timedelta(days=index) if frequency=='DAY' else datetime(2023,1,2,9,16,tzinfo=ZONE)+timedelta(minutes=index)
        for item in instruments:
            current=price if not isinstance(price,dict) else price[item['id']]
            row=dict(instrument_id=item['id'],timestamp=at.isoformat(),open=current,high=current+1,low=max(0,current-1),close=current,volume=volume,underlying_price=current,iv=.2,margin_per_lot=current*.18*item.get('lot_size',1),borrow_quantity=100000,borrow_rate=.1)
            if extra: row.update(extra(index,item))
            rows.append(row)
    fields=list(rows[0]);buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    profile={key:0 for key in DEFAULTS};profile['source']='TEST_ZERO_CHARGES'
    body=ImportData.model_validate(dict(manifest=dict(name='Independent accounting fixture',source='TEST_FIXTURE',source_kind='SYNTHETIC',frequency=frequency,instruments=instruments,actions=actions or [],fee_rules=[profile]),format='CSV',content=buffer.getvalue()))
    return store.import_data(body)


def config(d,strategies=None,**updates):
    spec=dict(id='book-a',name='Book A',family='SCHEDULED',legs=[dict(instrument_id='EQ:A')],
        schedule=[dict(at=d['start'],action='ENTER')],size_mode='LOTS',lots=10,allocation_pct=100,stop_pct=20,target_multiple=None,parameters=dict(hold_bars=9999),exit_dte_days=0)
    value=dict(dataset_id=d['id'],name='Independent economic example',capital=100000,strategies=strategies or [spec],
        risk=dict(trade_pct=10,open_pct=30,daily_pct=20,weekly_pct=40,drawdown_pct=80,margin_pct=100,overnight_stress_pct=80,gross_exposure_pct=1000,underlying_exposure_pct=1000,slippage_bps=0),
        execution=dict(spread_bps=0,participation_pct=100,expiry_policy='SETTLE'))
    value.update(updates)
    return Run.model_validate(value).model_dump(mode='json')


def replay(store,d,c,state=None):
    records=[]
    instance=Replay(d,c,lambda events,orders,state:records.extend(copy.deepcopy(events)),state)
    for index,(_,group) in enumerate(itertools.groupby(store.rows(d['id']),key=lambda r:r['timestamp']),1):
        instance.step(list(group),index)
    return instance,records


def test_csv_import_is_immutable_and_duplicate_reimport_has_same_id(catalog):
    body=synthetic_fixture();first=catalog.import_data(body);second=catalog.import_data(body)
    assert first['id']==second['id'] and first['status']=='READY'
    assert first['rows']>2000
    assert len(catalog.datasets())==1
    assert catalog.verify(first['id'])['raw_hash']==first['raw_hash']
    assert catalog.rows(first['id'],instrument_id='NSE:ALPHA',limit=2)[0]['instrument_id']=='NSE:ALPHA'


def test_invalid_duplicate_and_future_feature_rows_are_quarantined(catalog):
    body=synthetic_fixture();lines=body.content.splitlines()
    body.content+='\n'+lines[1]
    d=catalog.import_data(body)
    assert d['status']=='QUARANTINED' and d['quality']['invalid_rows']==1
    c=templates(d)[0]['config']
    assert not capabilities(d,c)['runnable']
    manifest=body.manifest.model_dump(mode='json')
    invalid='instrument_id,timestamp,open,high,low,close,volume,features_json\nNSE:ALPHA,2023-01-02,100,101,99,100,1000,"'+json.dumps({'news':{'value':1,'available_at':'2023-01-03T00:00:00+00:00'}}).replace('"','""')+'"\n'
    future=catalog.import_data(ImportData.model_validate(dict(manifest=manifest,format='CSV',content=invalid)))
    assert future['status']=='QUARANTINED'
    assert 'not available' in future['quality']['errors'][0]['reason']


def test_parquet_import_and_content_integrity(catalog):
    d=dataset(catalog);rows=catalog.rows(d['id'])
    stream=io.BytesIO();pq.write_table(pa.Table.from_pylist(rows),stream)
    copy_manifest=copy.deepcopy(d['manifest']);copy_manifest['name']='Parquet revision';copy_manifest['parent_dataset_id']=d['id']
    imported=catalog.import_data(ImportData.model_validate(dict(manifest=copy_manifest,format='PARQUET',content=base64.b64encode(stream.getvalue()).decode())))
    assert imported['status']=='READY' and imported['rows']==d['rows']
    path=catalog.root/'datasets'/imported['id']/'observations.parquet'
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='integrity'): catalog.verify(imported['id'])


def test_daily_close_signal_fills_next_open_and_pnl_reconciles(catalog):
    d=dataset(catalog,prices=(100,110,120,130));c=config(d)
    c['strategies'][0]['schedule'].append(dict(at=catalog.rows(d['id'])[2]['timestamp'],action='EXIT'))
    engine,events=replay(catalog,d,c)
    fills=[e for e in events if e['kind']=='FILL']
    assert [f['price'] for f in fills]==[110,130]
    assert [f['quantity'] for f in fills]==[10,-10]
    assert engine.state['cash']==10020000
    assert engine.account_view()['equity']==100200
    assert engine.account_view()['books']['book-a']['realised_net']==200
    assert engine.state['cash']==engine.state['ledger_total']


def test_two_books_can_own_same_instrument_without_double_allocating_account(catalog):
    d=dataset(catalog)
    c=config(d);a=c['strategies'][0];a['allocation_pct']=50
    b=copy.deepcopy(a);b['id']='book-b';b['name']='Book B';c['strategies'].append(b)
    engine,events=replay(catalog,d,c)
    assert len(engine.state['positions'])==2
    assert sum(l['quantity'] for _,_,l in engine.lots())==20
    assert engine.account_view()['equity']==100000
    assert sum(v['realised_net']+v['unrealised'] for v in engine.account_view()['books'].values())==0


def test_shared_capital_rejects_oversized_basket(catalog):
    d=dataset(catalog);c=config(d);c['capital']=100;c['strategies'][0]['lots']=10
    engine,events=replay(catalog,d,c)
    assert not list(engine.lots())
    assert any(e['kind']=='REJECT' for e in events)
    assert engine.state['cash']==10000


def test_partial_fills_and_cancel_latency_use_shared_bar_volume(catalog):
    d=dataset(catalog,frequency='MINUTE',extra=lambda index,item:dict(volume=2))
    c=config(d);c['execution']['cancel_latency_seconds']=60
    c['strategies'][0]['schedule'].append(dict(at=catalog.rows(d['id'])[1]['timestamp'],action='CANCEL'))
    engine,events=replay(catalog,d,c)
    fills=[e for e in events if e['kind']=='FILL']
    assert sum(f['quantity'] for f in fills)==4
    assert all(f['quantity']<=2 for f in fills)
    assert any(e['kind']=='CANCELLED' for e in events)


def test_same_bar_ambiguity_has_explicit_adverse_and_favorable_results(catalog):
    d=dataset(catalog,frequency='MINUTE',extra=lambda index,item:dict(high=120,low=80))
    c=config(d);c['strategies'][0].update(stop_pct=10,target_multiple=1)
    adverse,events=replay(catalog,d,c)
    c['execution']['bar_ambiguity']='FAVORABLE_FIRST'
    favorable,_=replay(catalog,d,c)
    assert any(e['kind']=='BAR_AMBIGUITY' for e in events)
    assert adverse.account_view()['equity']==99900
    assert favorable.account_view()['equity']==100100


def test_split_preserves_basis_and_does_not_create_a_false_daily_loss(catalog):
    action=dict(instrument_id='EQ:A',kind='SPLIT',ratio=2,known_at='2023-01-02T00:00:00+05:30',effective_at='2023-01-04T09:15:00+05:30')
    d=dataset(catalog,prices=(100,100,50,50),actions=[action]);c=config(d)
    engine,_=replay(catalog,d,c)
    lot=next(engine.lots())[2]
    assert lot['quantity']==20 and lot['price']==5000
    assert engine.account_view()['equity']==100000
    assert not engine.state['halt']


def test_dividend_entitlement_and_payment_preserve_account_attribution(catalog):
    action=dict(instrument_id='EQ:A',kind='DIVIDEND',amount=5,known_at='2023-01-02T00:00:00+05:30',effective_at='2023-01-04T09:15:00+05:30',payable_at='2023-01-05T09:15:00+05:30')
    d=dataset(catalog,prices=(100,100,95,95),actions=[action]);engine,events=replay(catalog,d,config(d))
    assert engine.account_view()['equity']==100000
    assert engine.account_view()['books']['book-a']['realised_net']==50
    assert engine.account_view()['books']['book-a']['unrealised']==-50
    assert any(e['kind']=='LEDGER' and e['reason']=='DIVIDEND_PAYMENT' for e in events)


def test_futures_gap_can_exhaust_equity_without_erasing_loss(catalog):
    instruments=[dict(id='FUT:A',symbol='Future',kind='FUT',underlying='IDX:A',lot_size=25,expiry='2023-02-01T15:30:00+05:30'),dict(id='IDX:A',symbol='Index',kind='INDEX',underlying='IDX:A')]
    d=dataset(catalog,prices=(100,100,1000,1000),instruments=instruments)
    c=config(d,capital=2000);c['strategies'][0].update(legs=[dict(instrument_id='FUT:A',selector=None,ratio=-1,product='AUTO')],lots=1,stop_pct=2)
    engine,events=replay(catalog,d,c)
    assert engine.state['cash']<0 and engine.state['halt']
    assert not list(engine.lots())
    assert engine.state['cash']==engine.state['ledger_total']
    assert any(e['kind']=='RISK_HALT' for e in events)


def test_cash_option_expiry_and_physical_delivery_have_correct_economics(catalog):
    for settlement in ('CASH','PHYSICAL'):
        under='IDX:A' if settlement=='CASH' else 'EQ:A'
        instruments=[dict(id='OPT:C',symbol='Call',kind='OPT',underlying=under,lot_size=1,expiry='2023-01-04T15:30:00+05:30',strike=100,option_type='CE',settlement=settlement),dict(id=under,symbol='Underlying',kind='INDEX' if settlement=='CASH' else 'EQ',underlying=under)]
        prices=[{'OPT:C':10,under:100},{'OPT:C':10,under:110},{'OPT:C':20,under:120}]
        d=dataset(catalog,prices=prices,instruments=instruments,extra=lambda index,item:dict(underlying_price=100+index*10))
        c=config(d);c['strategies'][0].update(legs=[dict(instrument_id='OPT:C',selector=None,ratio=1,product='AUTO')],lots=1,stop_pct=100)
        engine,events=replay(catalog,d,c)
        assert engine.account_view()['equity']==100010
        assert not any(asset=='OPT:C' for _,asset,_ in engine.lots())
        if settlement=='PHYSICAL': assert sum(l['quantity'] for _,asset,l in engine.lots() if asset==under)==1
        assert any(e['kind']=='SETTLEMENT' for e in events)


def test_missing_physical_delivery_evidence_retains_exposure_and_obligation(catalog):
    instruments=[dict(id='OPT:C',symbol='Physical call',kind='OPT',underlying='IDX:A',lot_size=1,expiry='2023-01-04T15:30:00+05:30',strike=90,option_type='CE',settlement='PHYSICAL'),dict(id='IDX:A',symbol='Index',kind='INDEX',underlying='IDX:A')]
    d=dataset(catalog,prices=({'OPT:C':10,'IDX:A':100},)*3,instruments=instruments,extra=lambda index,item:dict(underlying_price=100))
    c=config(d);c['strategies'][0].update(legs=[dict(instrument_id='OPT:C',selector=None,ratio=1,product='AUTO')],lots=1,stop_pct=100)
    engine,_=replay(catalog,d,c)
    assert engine.state['obligations'] and engine.state['halt']
    assert any(asset=='OPT:C' for _,asset,_ in engine.lots())


def test_models_and_rules_cannot_read_future_information():
    rows=[dict(timestamp=f'2023-01-{day:02}T10:00:00+00:00',close=100+day) for day in range(1,29)]
    cutoff=rows[24]['timestamp']
    a=fit_model(rows,cutoff,2,.01)
    rows[-1]['close']=100000
    assert fit_model(rows,cutoff,2,.01)==a
    with pytest.raises(ValueError): validate_rule(dict(left=dict(feature='python',code='anything'),op='gt',right=1))
    with pytest.raises(ValueError): validate_rule({'all':[{'all':[]} ]})


def test_future_mutation_cannot_change_earlier_strategy_signals(catalog):
    d=dataset(catalog,prices=(100,105,100,105));c=config(d)
    engine,events=replay(catalog,d,c)
    d2=dataset(catalog,prices=(100,105,100,900));c2=config(d2)
    other,changed=replay(catalog,d2,c2)
    boundary=catalog.rows(d['id'])[2]['timestamp']
    first=[e for e in events if e['time']<=boundary and e['kind'] in {'SIGNAL','ACCEPT','FILL'}]
    second=[e for e in changed if e['time']<=boundary and e['kind'] in {'SIGNAL','ACCEPT','FILL'}]
    assert first==second


def direct_job(catalog,d,c,identifier,command='RUN'):
    directory=catalog.root/'runs'/identifier;directory.mkdir(parents=True)
    with closing(open_job(directory/'run.sqlite3')) as db:
        with db:
            save_meta(db,dict(id=identifier,status='QUEUED',config=c,config_hash=digest(c)))
            db.execute('INSERT INTO control VALUES(1,?)',(command,))
    result=execute(str(catalog.root),identifier)
    with closing(open_job(directory/'run.sqlite3')) as db:
        meta=metadata(db);row=db.execute('SELECT document FROM report').fetchone()
    return result,meta,json.loads(row[0]) if row else None


def test_checkpoint_resume_and_repeated_runs_have_identical_economic_hash(catalog):
    d=dataset(catalog);c=config(d)
    _,_,first=direct_job(catalog,d,c,'a'*32)
    result,meta,_=direct_job(catalog,d,c,'b'*32,'PAUSE')
    assert result['status']=='PAUSED'
    with closing(open_job(catalog.root/'runs'/('b'*32)/'run.sqlite3')) as db:
        with db: db.execute("UPDATE control SET command='RUN'")
    assert execute(str(catalog.root),'b'*32)['status']=='COMPLETED'
    with closing(open_job(catalog.root/'runs'/('b'*32)/'run.sqlite3')) as db:
        second=json.loads(db.execute('SELECT document FROM report').fetchone()[0])
    assert first['economic_hash']==second['economic_hash']
    assert first['diagnostics']['ledger_reconciled'] and first['diagnostics']['attribution_reconciled']
    assert 'Synthetic' in first['qualification']['blockers'][0]


def test_reference_derivatives_and_portfolio_templates_execute_with_truthful_results(catalog):
    d=catalog.import_data(synthetic_fixture());references=templates(d)
    for name in ('rotation','future-spread','straddle','credit-spread','option-calendar','short-vol'):
        reference=next(t for t in references if t['id']==name)
        result,meta,report=direct_job(catalog,d,reference['config'],digest(name)[:32])
        assert result['status']=='COMPLETED',meta
        assert report['diagnostics']['fills']>0,(name,report['diagnostics'])
        assert report['diagnostics']['ledger_reconciled']
        assert report['diagnostics']['attribution_reconciled'],(name,report['violations'])


def test_research_api_runs_in_a_process_and_preserves_paper_account(tmp_path):
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as client:
        before=client.get('/api/state').json()['account']
        response=client.post('/api/research/datasets/synthetic',json={},headers=HEADERS)
        assert response.status_code==200,response.text
        d=response.json();reference=client.get('/api/research/templates',params={'dataset_id':d['id']}).json()[0]
        response=client.post('/api/research/runs',json=reference['config'],headers=HEADERS)
        assert response.status_code==200,response.text
        run=response.json()
        for _ in range(200):
            current=client.get('/api/research/runs/'+run['id']).json()
            if current['status'] in {'COMPLETED','FAILED'}: break
            time.sleep(.1)
        assert current['status']=='COMPLETED',current
        report=client.get('/api/research/runs/'+run['id']+'/report').json()
        assert report['diagnostics']['ledger_reconciled']
        assert client.get('/api/research/runs/'+run['id']+'/events?limit=501').status_code==422
        assert client.get('/api/research/runs/'+run['id']+'/export').status_code==200
        packet=client.post('/api/research/runs/'+run['id']+'/handoff',json={},headers=HEADERS).json()
        assert packet['status']=='PAPER_REVIEW_REQUIRED' and not packet['live_orders_enabled']
        assert client.get('/api/state').json()['account']==before
        assert client.post('/api/research/datasets/synthetic',json={}).status_code==403
        assert client.get('/api/live/orders').status_code==404


def test_daily_data_cannot_claim_intraday_strategy_support(catalog):
    d=dataset(catalog);c=config(d);c['strategies'][0]['allow_overnight']=False
    result=capabilities(d,c)
    assert not result['runnable'] and any('intraday' in b for b in result['blockers'])


def test_expired_zero_option_observation_settles_without_fabricated_liquidity(catalog):
    instruments=[dict(id='OPT:C',symbol='Expired call',kind='OPT',underlying='IDX:A',lot_size=1,expiry='2023-01-04T15:30:00+05:30',strike=110,option_type='CE'),dict(id='IDX:A',symbol='Index',kind='INDEX',underlying='IDX:A')]
    prices=[{'OPT:C':10,'IDX:A':100},{'OPT:C':10,'IDX:A':100},{'OPT:C':0,'IDX:A':100}]
    d=dataset(catalog,prices=prices,instruments=instruments,extra=lambda index,item:dict(underlying_price=100))
    assert d['status']=='READY'
    c=config(d);c['strategies'][0].update(legs=[dict(instrument_id='OPT:C',selector=None,ratio=1,product='AUTO')],lots=1,stop_pct=100)
    engine,_=replay(catalog,d,c)
    assert not list(engine.lots()) and engine.account_view()['equity']==99990


def test_opening_evidence_does_not_expose_unfinished_bar_high_low(catalog):
    d=dataset(catalog,frequency='MINUTE',extra=lambda index,item:dict(high=150,low=50))
    c=config(d);c['strategies'][0]['stop_pct']=100
    engine,events=replay(catalog,d,c)
    opening=next(e for e in events if e['kind']=='FILL' and e['phase']=='OPEN')
    assert opening['quote']['high']==opening['quote']['low']==100
    assert opening['quote']['volume']==0


def test_partial_fill_fee_changes_only_apply_to_new_turnover(catalog):
    d=dataset(catalog,frequency='MINUTE',extra=lambda index,item:dict(volume=2))
    rows=catalog.rows(d['id']);buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    manifest=copy.deepcopy(d['manifest'])
    earlier=copy.deepcopy(manifest['fee_rules'][0]);earlier['equity_stt_delivery']=.01
    later=copy.deepcopy(earlier);later.update(effective_from='2023-01-02T09:17:00+05:30',equity_stt_delivery=.02)
    manifest['fee_rules']=[earlier,later]
    d=catalog.import_data(ImportData.model_validate(dict(manifest=manifest,content=buffer.getvalue())))
    c=config(d);c['strategies'][0]['lots']=4
    engine,events=replay(catalog,d,c)
    fills=[e for e in events if e['kind']=='FILL']
    assert [f['fees'] for f in fills]==[2,4]
    assert engine.state['fees']==600


def test_ridge_training_window_excludes_older_and_future_samples():
    rows=[dict(timestamp=(datetime(2023,1,1,tzinfo=ZONE)+timedelta(days=i)).isoformat(),close=100+i) for i in range(70)]
    fitted=fit_model(rows,rows[55]['timestamp'],2,.01,rows[20]['timestamp'])
    rows[0]['close']=999999;rows[-1]['close']=999999
    assert fit_model(rows,rows[55]['timestamp'],2,.01,rows[20]['timestamp'])==fitted
    assert fitted['training_samples']==33


def test_dynamic_future_rollover_closes_old_contract_before_new_entry(catalog):
    instruments=[dict(id='IDX:A',symbol='Index',kind='INDEX',underlying='IDX:A'),
        dict(id='FUT:NEAR',symbol='Near',kind='FUT',underlying='IDX:A',expiry='2023-01-05T15:30:00+05:30'),
        dict(id='FUT:FAR',symbol='Far',kind='FUT',underlying='IDX:A',expiry='2023-02-01T15:30:00+05:30')]
    d=dataset(catalog,prices=(100,)*5,instruments=instruments)
    c=config(d);c['strategies'][0].update(legs=[dict(instrument_id=None,selector=dict(underlying='IDX:A',kind='FUT',option_type=None,expiry_rank=0,min_dte=1,max_dte=90,strike_offset=0,target_delta=None),ratio=1,product='AUTO')],lots=1,stop_pct=5,exit_dte_days=1,roll_on_expiry=True)
    engine,events=replay(catalog,d,c)
    assert any(e['kind']=='ROLLOVER' for e in events)
    fills=[(e['instrument_id'],e['quantity']) for e in events if e['kind']=='FILL']
    assert fills[:3]==[('FUT:NEAR',1),('FUT:NEAR',-1),('FUT:FAR',1)]


def test_suite_and_walk_forward_api_preserve_all_trial_specs(tmp_path,monkeypatch):
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    queued=[]
    monkeypatch.setattr(app.state.research,'schedule',lambda identifier:queued.append(identifier))
    with TestClient(app,base_url='http://127.0.0.1') as client:
        d=client.post('/api/research/datasets/synthetic',json={},headers=HEADERS).json()
        base=client.get('/api/research/templates',params={'dataset_id':d['id']}).json()[0]['config']
        suite=client.post('/api/research/suites',json=dict(base=base,variants=[{'risk.slippage_bps':2},{'risk.slippage_bps':8}]),headers=HEADERS)
        assert suite.status_code==200,suite.text
        results=suite.json()
        assert len(results)==2 and results[0]['config']['experiment_id']==results[1]['config']['experiment_id']
        assert client.post('/api/research/suites',json=dict(base=base,variants=[{'capital.foo':1}]),headers=HEADERS).status_code==400
        folds=client.post('/api/research/walk-forward',json=dict(base=base,train_observations=60,test_observations=20,step_observations=20,embargo_observations=1,max_folds=2),headers=HEADERS)
        assert folds.status_code==200,folds.text
        assert len(folds.json())==2
        for fold in folds.json():
            assert fold['config']['training_end']<fold['config']['start']
            assert fold['config']['windows'][0]['end']<fold['config']['windows'][1]['start']
        assert len(queued)==4
        assert len(client.get('/api/research/runs').json())==4


def test_historical_broker_allowlist_does_not_admit_order_or_traversal_paths():
    from app.market import KiteData
    adapter=KiteData();adapter.key='';adapter.token=''
    with pytest.raises(ValueError,match='not authenticated'):
        adapter._request('GET','/instruments/historical/123/day')
    for method,path in [('POST','/instruments/historical/123/day'),('GET','/instruments/historical/123/../orders'),('POST','/orders/regular')]:
        with pytest.raises(ValueError,match='allowlist'): adapter._request(method,path)


def test_dividend_yield_and_rate_are_applied_to_value_greeks_and_inferred_iv():
    import math
    from app.finance import option_value,option_greeks,implied_volatility
    spot,strike,years,vol,rate,q=100,100,.5,.25,.03,.04
    call=option_value(spot,strike,years,vol,'CE',rate,q)
    put=option_value(spot,strike,years,vol,'PE',rate,q)
    assert call-put==pytest.approx(spot*math.exp(-q*years)-strike*math.exp(-rate*years))
    assert implied_volatility(call,spot,strike,years,'CE',rate,q)==pytest.approx(vol,abs=1e-6)
    numerical=(option_value(spot+.01,strike,years,vol,'CE',rate,q)-option_value(spot-.01,strike,years,vol,'CE',rate,q))/.02
    assert option_greeks(spot,strike,years,vol,'CE',rate,q)['delta']==pytest.approx(numerical,abs=1e-5)


def test_bullish_crossover_exits_a_short_before_reversing(catalog):
    from app.research.signals import signal
    d=dataset(catalog);spec=config(d)['strategies'][0]
    spec.update(family='TREND',allow_short=True,parameters={**spec['parameters'],'fast':2,'slow':3})
    history={'EQ:A':[dict(close=p) for p in (3,2,1,4)]}
    outcome=signal(spec,history,[('book','EQ:A',{'quantity':-1})],d['start'],dict(last_direction=-1))
    assert outcome['action']=='REVERSE' and outcome['direction']==1
    outcome=signal(spec,history,[('book','EQ:A',{'quantity':1})],d['start'],dict(last_direction=1))
    assert outcome['action']=='HOLD'


def test_evaluation_roles_cannot_present_future_training_as_prior_holdout(catalog):
    d=dataset(catalog);c=config(d)
    c['windows']=[dict(label='HOLDOUT',start='2023-01-02T00:00:00+00:00',end='2023-01-03T00:00:00+00:00'),dict(label='TRAIN',start='2023-01-04T00:00:00+00:00',end='2023-01-05T00:00:00+00:00')]
    with pytest.raises(ValueError,match='chronological'): Run.model_validate(c)


def test_missing_feature_cannot_be_inverted_into_an_entry(catalog):
    from app.research.signals import signal
    d=dataset(catalog);spec=config(d)['strategies'][0]
    spec.update(family='RULES',entry_rule={'not':dict(left=dict(feature='sma',period=20),op='gt',right=100)})
    result=signal(spec,{'EQ:A':[dict(close=100)]},[],d['start'],{})
    assert result['action']=='WARMUP'


def test_run_lease_prevents_duplicate_worker_execution(catalog):
    import fcntl
    identifier='c'*32;directory=catalog.root/'runs'/identifier;directory.mkdir(parents=True)
    with (directory/'worker.lock').open('a') as lease:
        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert execute(str(catalog.root),identifier)['status']=='LEASE_HELD'
    assert not (directory/'run.sqlite3').exists()
