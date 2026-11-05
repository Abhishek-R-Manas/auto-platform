"""Builder amounts, provenance, contract transformations and read-only broker boundaries."""
import copy,json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from app.workbench.analytics import analyze
from app.workbench.models import Analysis,Basket,SaveDraft,ChainRequest,StudioBasket,StrikeAdjust,MarginQuote,PositionImport,TemplateApply
from app.workbench.studio import Studio
from app.pilot.broker import KiteBroker
from app.research.catalog import digest
from tools.forward_ops import check_database
from test_workbench import AT,EXPIRY,FakeKite,leg,request,workspace


def test_manual_pnl_offsets_all_contract_payoffs_and_not_greeks():
    a=analyze(request([leg()]),AT);b=analyze(request([leg()],manual_pnl=250,cost_estimate=20),AT)
    assert b['max_loss']==770 and b['breakevens']==[107.7]
    assert b['target_pnl']==pytest.approx(a['target_pnl']+230)
    assert b['model_expected_pnl']==pytest.approx(a['model_expected_pnl']+230)
    assert b['greeks']==a['greeks'] and b['manual_pnl_applied']==250
    c=analyze(request([leg()],manual_pnl=250,manual_pnl_enabled=False),AT)
    assert c['target_pnl']==a['target_pnl'] and c['breakevens']==a['breakevens']


def test_disabled_legs_leave_every_analysis_total():
    a=analyze(request([leg()]),AT);b=analyze(request([leg(),leg(side='SELL',enabled=False)]),AT)
    for key in ('max_loss','net_debit','greeks','target_pnl','model_pop_pct','model_expected_pnl'):assert a[key]==b[key]
    assert len(b['leg_analysis'])==2 and not b['leg_analysis'][1]['enabled']
    with pytest.raises(ValueError,match='Enable'):analyze(request([leg(enabled=False)]),AT)


def test_per_leg_and_target_greeks_reconcile_to_totals():
    body=request([leg(),leg(kind='PE',side='SELL',premium=5)],manual_pnl=-150,cost_estimate=20);body.days_forward=4;body.iv_shift_pp=3;body.target_move_pct=2
    a=analyze(body,AT)
    assert sum(l['target_pnl'] for l in a['leg_analysis'])-170==pytest.approx(a['target_pnl'])
    for k in a['greeks']:assert sum(l['greeks'][k] for l in a['leg_analysis'])==pytest.approx(a['greeks'][k])
    assert a['target_greeks']!=a['greeks']
    assert all(l['time_value']>=0 for l in a['leg_analysis'])


def test_configurable_payoff_table_matches_exact_contract_arithmetic():
    body=request([leg()],manual_pnl=50,cost_estimate=10);body.table_step_pct=.5;body.table_center_move_pct=10
    a=analyze(body,AT);assert len(a['payoff_table'])==21
    mid=a['payoff_table'][10];assert mid['spot']==pytest.approx(110) and mid['at_expiry']==pytest.approx(40)
    assert a['payoff_table'][1]['move_pct']-a['payoff_table'][0]['move_pct']==.5
    assert a['heatmap'][-1]['values'][2]['pnl']==pytest.approx(-960)


def model(workspace,template='iron_condor'):
    w,k=workspace
    return Basket.model_validate(w.model_template(TemplateApply(template_id=template,spot=25000,strike_step=50,expiry=EXPIRY,next_expiry=EXPIRY+timedelta(days=7),lot_size=65)))


def test_shift_is_atomic_and_keeps_input_immutable(workspace):
    w,k=workspace;b=model(workspace);original=b.model_dump(mode='json');result=Studio(w).adjust(StrikeAdjust(basket=b,action='SHIFT',steps=2,strike_step=50))
    assert all(new['strike']==old['strike']+100 for old,new in zip(original['legs'],result['basket']['legs']))
    assert b.model_dump(mode='json')==original and all(l['key'] is None and l['price_source']=='MODEL' for l in result['basket']['legs'])


def test_width_cannot_cross_and_hedges_must_be_designated(workspace):
    w,k=workspace;studio=Studio(w);b=model(workspace,'short_straddle')
    result=studio.adjust(StrikeAdjust(basket=b,action='WIDTH'));calls=[l['strike'] for l in result['basket']['legs'] if l['kind']=='CE'];puts=[l['strike'] for l in result['basket']['legs'] if l['kind']=='PE'];assert min(calls)>max(puts)
    with pytest.raises(ValueError,match='cross'):studio.adjust(StrikeAdjust(basket=b,action='WIDTH',steps=-1))
    with pytest.raises(ValueError,match='Select the hedge'):studio.adjust(StrikeAdjust(basket=b,action='HEDGE'))
    b=model(workspace)
    for l in b.legs:l.hedge=l.side=='BUY'
    result=studio.adjust(StrikeAdjust(basket=b,action='HEDGE'))
    assert all(new['strike']==old.strike for old,new in zip(b.legs,result['basket']['legs']) if not old.hedge)
    with pytest.raises(ValueError,match='cross|collapse'):studio.adjust(StrikeAdjust(basket=b,action='HEDGE',steps=-20))


def observed(workspace):
    w,k=workspace;c=w.refresh_chain(ChainRequest(underlying='NIFTY',width=5))['data'];expiry=c['contracts'][0]['expiry']
    b=w.build_template(TemplateApply(source='KITE',template_id='bull_call',spot=25000,expiry=expiry,lot_size=65))
    return Basket.model_validate(b)


def test_observed_adjustment_binds_real_new_contracts_and_side_prices(workspace):
    w,k=workspace;b=observed(workspace);studio=Studio(w);r=studio.adjust(StrikeAdjust(basket=b,action='SHIFT'))
    chain=w.chain('NIFTY')['data']
    for l in r['basket']['legs']:
        q=next(p for p in chain['contracts'] if p['key']==l['key']);assert l['premium']==q['ask' if l['side']=='BUY' else 'bid']
    with pytest.raises(ValueError,match='missing'):studio.adjust(StrikeAdjust(basket=b,action='SHIFT',steps=20))


def test_quote_tracking_preserves_entries_and_distinguishes_stale(workspace):
    w,k=workspace;b=observed(workspace);studio=Studio(w);before=b.model_dump(mode='json');body=StudioBasket(basket=b);mark=studio.mark(body)
    assert mark['fresh'] and mark['source']=='KITE_LAST' and not mark['execution_authority']
    assert b.model_dump(mode='json')==before and sum(l['pnl'] for l in mark['legs'])==pytest.approx(mark['pnl'])
    assert studio.history(body)['observations']==[mark]
    w.clock=lambda:AT+timedelta(seconds=40);stale=studio.mark(body);assert not stale['fresh']
    assert len(studio.history(body)['observations'])==2
    assert check_database(__import__('pathlib').Path(w.db.execute('PRAGMA database_list').fetchone()[2]))['strategy_marks_verified']==2
    changed=b.model_copy(update={'manual_pnl':5});assert not studio.history(StudioBasket(basket=changed))['observations']


def test_mark_tampering_is_detected_by_reads_and_backups(workspace):
    w,k=workspace;studio=Studio(w);b=observed(workspace);m=studio.mark(StudioBasket(basket=b));m['pnl']+=1
    with w.db:w.db.execute('UPDATE strategy_marks SET document=?',(json.dumps(m),))
    with pytest.raises(ValueError,match='integrity'):studio.history(StudioBasket(basket=b))
    from pathlib import Path
    with pytest.raises(ValueError,match='integrity'):check_database(Path(w.db.execute('PRAGMA database_list').fetchone()[2]))


def test_unbound_or_mismatched_contracts_cannot_be_marked_or_margined(workspace):
    w,k=workspace;studio=Studio(w);b=model(workspace)
    with pytest.raises(ValueError,match='exact current'):studio.mark(StudioBasket(basket=b))
    b=observed(workspace);b.legs[0].strike+=50
    with pytest.raises(ValueError,match='differs'):studio.mark(StudioBasket(basket=b))


def test_margin_calls_calculation_only_and_retains_leg_order(workspace):
    w,k=workspace;b=observed(workspace);calls=[]
    class Broker:
        def basket_margin(self,orders,positions):
            calls.append((orders,positions));return dict(initial={'total':50000},final={'total':12000},orders=[dict(charges={'total':20}) for o in orders])
    studio=Studio(w,SimpleNamespace(broker=Broker()));r=studio.margins(MarginQuote(basket=b,consider_positions=True))
    assert r['initial']==50000 and r['final']==12000 and r['estimated_entry_charges']==40 and not r['execution_authority']
    assert calls[0][1] and [o['transaction_type'] for o in calls[0][0]]==[l.side for l in b.legs]
    assert all(o['product']=='NRML' and o['order_type']=='LIMIT' for o in calls[0][0])


def test_margin_transport_requires_no_order_write_capability(monkeypatch):
    import app.pilot.broker as module
    calls=[]
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_bytes(self):yield b'{"status":"success","data":{"initial":{"total":100},"final":{"total":80}}}'
    class Client:
        def __init__(self,**kw):assert not kw['trust_env'] and kw['base_url']=='https://api.kite.trade'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def stream(self,*args,**kw):calls.append((args,kw));return Response()
    monkeypatch.setattr(module.httpx,'Client',Client);k=FakeKite();broker=KiteBroker(k,enabled=False)
    assert broker.basket_margin([],False)['final']['total']==80
    assert calls[0][0]==('POST','/margins/basket') and calls[0][1]['params']=={'consider_positions':'false'} and calls[0][1]['json']==[]
    with pytest.raises(ValueError,match='outside'):broker.request('POST','/margins/basket',[])
    with pytest.raises(ValueError,match='disabled'):broker.place(dict(symbol='X',side='BUY',quantity=1,limit_price=1),'x')


def test_import_requires_exact_snapshot_one_underlying_and_whole_lots(workspace):
    w,k=workspace;r=next(r for r in k.rows if r['exchange']=='NFO');snapshot=dict(positions={'net':[dict(exchange='NFO',tradingsymbol=r['tradingsymbol'],product='NRML',quantity=65,average_price=100)]})
    import threading
    pilot=SimpleNamespace(lock=threading.RLock(),audit=lambda:{'ok':True},_get=lambda key:snapshot)
    studio=Studio(w,pilot);c=studio.candidates();assert len(c['groups'])==1
    identity=c['groups'][0]['positions'][0]['id'];body=PositionImport(snapshot_hash=c['snapshot_hash'],selected=[identity],spot=25000);result=studio.import_positions(body)
    assert result['basket']['legs'][0]['premium']==100 and result['basket']['legs'][0]['lots']==1 and not result['execution_authority']
    snapshot['positions']['net'][0]['average_price']=101
    with pytest.raises(ValueError,match='changed'):studio.import_positions(body)
    snapshot['positions']['net'][0]['quantity']=64
    assert not studio.candidates()['groups'] and 'complete current lot' in studio.candidates()['unavailable'][0]['reason']


def test_saved_disabled_legs_and_manual_pnl_survive_revision(workspace):
    w,k=workspace;b=request([leg(),leg(kind='PE',enabled=False)],manual_pnl=-100).basket
    d=w.save(SaveDraft(request_id='studio-draft-1',basket=b));saved=w.draft(d['id']);assert saved['basket']['manual_pnl']==-100 and not saved['basket']['legs'][1]['enabled']


def test_legacy_draft_without_new_fields_keeps_original_hash(workspace):
    w,k=workspace;b=request([leg()]).basket.model_dump(mode='json');b.pop('manual_pnl');b.pop('manual_pnl_enabled');b['legs'][0].pop('enabled');b['legs'][0].pop('hedge');core=dict(basket=b,parent_id=None);identifier=digest(core);item=dict(core,id=identifier,created_at=AT.isoformat(),status='DRAFT',execution_authority=False)
    with w.db:w.db.execute('INSERT INTO drafts VALUES(?,?,?)',(identifier,'legacy-studio',json.dumps(item)))
    assert w.draft(identifier)==item


def test_multi_expiry_roll_keeps_the_term_structure(workspace):
    w,k=workspace;b=model(workspace,'call_calendar');result=Studio(w).adjust(StrikeAdjust(basket=b,action='EXPIRY',expiry=EXPIRY+timedelta(days=2)))
    dates=[__import__('datetime').datetime.fromisoformat(l['expiry']) for l in result['basket']['legs']]
    assert dates[1]-dates[0]==b.legs[1].expiry-b.legs[0].expiry
    assert min(dates)==EXPIRY+timedelta(days=2)


def test_chain_series_preserves_observation_times_and_never_fabricates_history(workspace):
    from app.workbench.models import ChainSeries
    w,k=workspace;studio=Studio(w);c=w.refresh_chain(ChainRequest())['data'];body=ChainSeries(underlying='NIFTY',expiry=c['expiry'],strikes=[25000])
    one=studio.chain_series(body);assert len(one['observations'])==1 and one['iv_rank_pct'] is None
    w.clock=lambda:AT+timedelta(seconds=40);w.refresh_chain(ChainRequest())
    two=studio.chain_series(body);assert len(two['observations'])==2 and len(two['keys'])==2 and two['iv_sample_count']==2
    assert two['observations'][0]['time']!=two['observations'][1]['time'] and 'not annual' in two['window']
    assert all(p['strike']==25000 for o in two['observations'] for p in o['contracts'])
    empty=studio.chain_series(ChainSeries(underlying='NIFTY',expiry=c['expiry'],strikes=[1]));assert not empty['keys'] and empty['iv_sample_count']==0


def test_chain_series_checks_every_historical_snapshot_hash(workspace):
    from app.workbench.models import ChainSeries
    w,k=workspace;studio=Studio(w);c=w.refresh_chain(ChainRequest())['data'];c['spot']+=1
    with w.db:w.db.execute('UPDATE chains SET document=?',(json.dumps(c),))
    with pytest.raises(ValueError,match='integrity'):studio.chain_series(ChainSeries(underlying='NIFTY',expiry=c['expiry'],strikes=[25000]))


def test_disabled_backtest_leg_retains_the_original_mapping_index(workspace):
    w,k=workspace;b=request([leg(side='SELL',enabled=False),leg(kind='EQ')]).basket;d=w.save(SaveDraft(request_id='studio-mapping',basket=b))
    dataset='a'*64;w.research=SimpleNamespace(catalog=SimpleNamespace(verify=lambda value:{'manifest':{'instruments':[{'id':'NSE:REF','kind':'EQ'}]}}),create=lambda config:config)
    from app.workbench.models import Backtest
    result=w.backtest(Backtest(draft_id=d['id'],dataset_id=dataset,mapping={'underlying':'NSE:REF','1':'NSE:REF'}))
    assert len(result['strategies'][0]['legs'])==1 and not result['strategies'][0]['allow_short']


def test_margin_roi_is_not_inferred_when_including_existing_positions(workspace):
    w,k=workspace;b=observed(workspace)
    class Broker:
        def basket_margin(self,orders,positions):return dict(initial={'total':50000},final={'total':12000},orders=[])
    studio=Studio(w,SimpleNamespace(broker=Broker()));r=studio.margins(MarginQuote(basket=b))
    a=analyze(Analysis(basket=b),AT);assert r['max_profit_on_final_margin_pct']==pytest.approx(a['max_profit']/12000*100)
    assert studio.margins(MarginQuote(basket=b,consider_positions=True))['max_profit_on_final_margin_pct'] is None


def test_cash_index_is_never_sent_as_a_broker_margin_leg(workspace):
    w,k=workspace;b=observed(workspace);b.underlying='NIFTY 50';b.legs=[__import__('app.workbench.models',fromlist=['BasketLeg']).BasketLeg(kind='EQ',key='NSE:NIFTY 50',premium=25000)]
    studio=Studio(w,SimpleNamespace(broker=SimpleNamespace(basket_margin=lambda *a:pytest.fail('Index was sent to broker'))))
    with pytest.raises(ValueError,match='indices'):studio.margins(MarginQuote(basket=b))


def test_zero_underlying_quote_does_not_create_a_tracking_observation(workspace):
    w,k=workspace;b=observed(workspace);original=k._quotes
    def zero(keys):
        values=original(keys);values['NSE:NIFTY 50']['last_price']=0;return values
    k._quotes=zero;studio=Studio(w)
    with pytest.raises(ValueError,match='positive underlying'):studio.mark(StudioBasket(basket=b))
    assert not studio.history(StudioBasket(basket=b))['observations']
