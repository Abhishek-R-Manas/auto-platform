"""Hand-calculated broker snapshot presentation checks; all fixtures are fictional."""
import copy
from decimal import Decimal
from app.portfolio import portfolio_view


def snapshot():
    holdings=[dict(exchange='NSE',tradingsymbol='RELIANCE',product='CNC',quantity=10,t1_quantity=0,average_price=1400,last_price=1500,close_price=1490,pnl=1000,day_change=10,used_quantity=2,collateral_quantity=3),dict(exchange='NSE',tradingsymbol='INFY',product='CNC',quantity=15,t1_quantity=0,average_price=1700,last_price=1600,close_price=1610,pnl=-1500,day_change=-10),dict(exchange='NSE',tradingsymbol='TCS',product='CNC',quantity=2,t1_quantity=1,average_price=3100,last_price=3300,close_price=3250,pnl=600,day_change=50,mtf=dict(quantity=1,average_price=3200))]
    positions=[dict(exchange='NSE',tradingsymbol='RELIANCE',product='MIS',quantity=5,average_price=1490,last_price=1500,pnl=50,realised=0,unrealised=50,m2m=50,overnight_quantity=0,multiplier=1),dict(exchange='NFO',tradingsymbol='NIFTY025000CE',product='NRML',quantity=-65,average_price=300,last_price=250,pnl=3250,realised=0,unrealised=3250,m2m=3000,overnight_quantity=-65,multiplier=1),dict(exchange='NSE',tradingsymbol='INFY',product='MIS',quantity=0,average_price=0,last_price=1600,pnl=-25,realised=-25,unrealised=0,m2m=-25,multiplier=1)]
    return dict(account_id='CONTROLLED123',exchanges=['NSE','NFO'],products=['CNC','MIS','NRML'],order_types=['LIMIT'],funds=dict(net=100000,available=dict(cash=100000,live_balance=100000,collateral=0,adhoc_margin=0)),orders=[],trades=[],holdings=holdings,positions=dict(net=positions),observed_at='2026-11-05T10:00:00+05:30')


def test_holdings_totals_match_hand_calculation():
    view=portfolio_view(snapshot());s=view['holdings_summary']
    assert s['invested_paise']==5200000 and s['value_paise']==5220000 and s['pnl_paise']==20000 and s['day_pnl_paise']==15000
    assert s['count']==3 and round(s['net_change_pct'],6)==round(200/52000*100,6)


def test_t1_mtf_weighted_cost_and_collateral_do_not_double_count():
    rows=portfolio_view(snapshot())['holdings'];tcs=rows[2]
    assert tcs['quantity']==4 and tcs['t1_quantity']==1 and tcs['mtf_quantity']==1
    assert tcs['average_price']==3125 and tcs['invested_paise']==1250000 and tcs['pnl_paise']==70000
    assert rows[0]['quantity']==10 and rows[0]['invested_paise']==1400000


def test_position_pnl_uses_broker_values_and_includes_closed_results():
    view=portfolio_view(snapshot());s=view['positions_summary']
    assert s['open_count']==2 and s['count']==3 and s['pnl_paise']==327500
    assert s['realised_paise']==-2500 and s['unrealised_paise']==330000
    assert view['positions'][1]['quantity']==-65 and view['positions'][1]['value_paise']==-1625000


def test_missing_values_do_not_become_zero_balances():
    source=snapshot();source['holdings'][0]['last_price']=None
    view=portfolio_view(source);assert view['holdings'][0]['value_paise'] is None and view['holdings_summary']['value_paise'] is None
    assert view['holdings_summary']['incomplete_rows']==1


def test_missing_mtf_cost_does_not_invent_an_average():
    source=snapshot();del source['holdings'][2]['mtf']['average_price']
    view=portfolio_view(source);assert view['holdings'][2]['invested_paise'] is None and view['holdings_summary']['pnl_paise'] is None


def test_snapshot_presentation_is_pure_and_excludes_credentials_and_account_id():
    source=snapshot();before=copy.deepcopy(source);source['private_secret']='never-return-this';view=portfolio_view(source)
    assert view['execution_authority'] is False and 'account_id' not in view and 'private_secret' not in str(view)
    del source['private_secret'];assert source==before


def test_empty_and_unsynced_portfolios_remain_distinct():
    assert portfolio_view(None) is None
    source=snapshot();source['holdings']=[];source['positions']['net']=[];view=portfolio_view(source)
    assert view['holdings_summary']['value_paise']==0 and view['positions_summary']['count']==0


def test_invalid_numbers_are_unavailable_and_money_rounds_to_paise():
    source=snapshot();source['holdings'][0].update(quantity=3,average_price='0.335',last_price='0.345',close_price='0.340')
    row=portfolio_view(source)['holdings'][0];assert row['invested_paise']==101 and row['value_paise']==104 and row['pnl_paise']==3
    source['holdings'][0].update(quantity=True,average_price=float('inf'));row=portfolio_view(source)['holdings'][0]
    assert row['quantity'] is None and row['invested_paise'] is None


def test_position_multiplier_changes_exposure_not_broker_pnl():
    source=snapshot();source['positions']['net'][0].update(multiplier=1000,pnl=12.34)
    row=portfolio_view(source)['positions'][0];assert row['value_paise']==750000000 and row['pnl_paise']==1234
