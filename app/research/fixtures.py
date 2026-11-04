"""Explicit synthetic fixtures and unvalidated reference configurations."""
import csv
import io
import math
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from ..finance import option_value
from .schemas import ImportData,Run


def synthetic_fixture():
    zone=ZoneInfo('Asia/Kolkata');day=datetime(2023,1,2,15,30,tzinfo=zone);dates=[]
    while len(dates)<160:
        if day.weekday()<5: dates.append(day)
        day+=timedelta(days=1)
    expiry=dates[99];far=dates[149]
    instruments=[dict(id='NSE:ALPHA',symbol='ALPHA TEST EQUITY',kind='EQ',underlying='NSE:ALPHA'),
        dict(id='NSE:BETA',symbol='BETA TEST EQUITY',kind='EQ',underlying='NSE:BETA'),
        dict(id='NSE:INDEX',symbol='TEST INDEX',kind='INDEX',underlying='NSE:INDEX')]
    instruments += [dict(id='NFO:FUT'+str(i),symbol='TEST FUTURE '+str(i),kind='FUT',underlying='NSE:INDEX',exchange='NFO',lot_size=25,expiry=d.isoformat()) for i,d in enumerate((expiry,far),1)]
    for name,d in (('NEAR',expiry),('FAR',far)):
        for kind in ('CE','PE'):
            for strike in (23000,24000,25000):
                instruments.append(dict(id=f'NFO:{name}-{kind}-{strike}',symbol=f'TEST {name} {kind} {strike}',kind='OPT',underlying='NSE:INDEX',exchange='NFO',lot_size=25,expiry=d.isoformat(),strike=strike,option_type=kind))
    fields=['instrument_id','timestamp','open','high','low','close','volume','bid','ask','bid_quantity','ask_quantity','underlying_price','iv','settlement','margin_per_lot','borrow_quantity','borrow_rate']
    buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields);writer.writeheader()
    previous={}
    for tick,at in enumerate(dates):
        spot=24000*(1+.0003*tick+.035*math.sin(tick/9)+.012*math.sin(tick/3))
        for item in instruments:
            if item.get('expiry') and at>datetime.fromisoformat(item['expiry']): continue
            kind=item['kind'];identifier=item['id']
            if kind=='EQ': close=(1500 if identifier=='NSE:ALPHA' else 1800)*(1+.001*tick+.04*math.sin(tick/(7 if identifier=='NSE:ALPHA' else 11)))
            elif kind=='INDEX': close=spot
            elif kind=='FUT': close=spot+40
            else:
                years=max(0,(datetime.fromisoformat(item['expiry'])-at).total_seconds()/(365*86400))
                close=max(.05,option_value(spot,item['strike'],years,.22,item['option_type']))
            close=round(close,2);opening=previous.get(identifier,close)*(1+.001*math.sin(tick));previous[identifier]=close
            row=dict(instrument_id=identifier,timestamp=at.isoformat(),open=round(opening,2),high=round(max(opening,close)*1.008,2),low=max(.01,round(min(opening,close)*.992,2)),close=close,volume=1000000 if kind in {'EQ','INDEX'} else 10000,
                bid=max(.01,round(close*.999,2)),ask=round(close*1.001,2),bid_quantity=500,ask_quantity=500,
                underlying_price=spot if kind in {'OPT','FUT','INDEX'} else close,iv=.22,settlement=close if kind=='FUT' else '',
                margin_per_lot=round(spot*item.get('lot_size',1)*(.18 if kind=='FUT' else .25),2) if kind in {'FUT','OPT'} else 0,
                borrow_quantity=100000 if kind=='EQ' else 0,borrow_rate=.08 if kind=='EQ' else 0)
            writer.writerow(row)
    return ImportData.model_validate(dict(manifest=dict(name='Synthetic multi-asset research fixture',source='Artha deterministic fixture generator / 1.0.0',source_kind='SYNTHETIC',frequency='DAY',instruments=instruments,
        license_note='Generated locally for software validation; not observed historical market data',notes='Illustrative contracts, rates, prices and margins. These results cannot qualify a trading strategy.'),format='CSV',content=buffer.getvalue()))


def templates(dataset):
    manifest=dataset['manifest'];items=manifest['instruments'];eq=[i for i in items if i['kind']=='EQ'];fut=[i for i in items if i['kind']=='FUT'];opt=[i for i in items if i['kind']=='OPT']
    def strategy(identifier,name,family,legs,**kwargs):
        return dict(id=identifier,name=name,family=family,legs=legs,**kwargs)
    result=[]
    def add(identifier,name,strategies,**extras):
        config=dict(dataset_id=dataset['id'],name=name,strategies=strategies,**extras)
        try: config=Run.model_validate(config).model_dump(mode='json')
        except ValueError: return
        result.append(dict(id=identifier,name=name,config=config,validation='Unvalidated reference strategy; inspect its rules and risk settings'))
    if eq:
        asset=eq[0]['id']
        add('stock-trend','Stock swing trend',[strategy('swing','Stock swing trend','TREND',[dict(instrument_id=asset)],signal_instruments=[asset],allocation_pct=50)])
        add('stock-rules','Declarative RSI mean reversion',[strategy('rsi','Declarative RSI','RULES',[dict(instrument_id=asset)],signal_instruments=[asset],allocation_pct=50,
            entry_rule=dict(left=dict(feature='rsi',period=14),op='lt',right=30),exit_rule=dict(left=dict(feature='rsi',period=14),op='gt',right=60))])
        if len(eq)>1:
            add('portfolio','Two strategy books sharing capital',[
                strategy('trend-book','Trend book','TREND',[dict(instrument_id=asset)],allocation_pct=45),
                strategy('mean-book','Mean reversion book','MEAN_REVERSION',[dict(instrument_id=eq[1]['id'])],allocation_pct=45,parameters=dict(entry_z=1.5))])
            add('pairs','Pairs mean reversion',[strategy('pairs','Pairs','PAIRS',[dict(instrument_id=asset),dict(instrument_id=eq[1]['id'],ratio=-1)],signal_instruments=[asset,eq[1]['id']],allow_short=True,allocation_pct=70)])
            add('rotation','Momentum rotation',[strategy('rotation','Momentum rotation','MOMENTUM',[dict(instrument_id=i['id']) for i in eq[:10]],signal_instruments=[asset],allocation_pct=80,parameters=dict(top_n=1,rebalance_bars=10))])
        if dataset['start'] and dataset['end']:
            cutoff=aware(dataset['start'])+(aware(dataset['end'])-aware(dataset['start']))*.5
            add('linear','Training-cutoff linear model',[strategy('model','Lag-return ridge model','LINEAR_MODEL',[dict(instrument_id=asset)],allocation_pct=50,parameters=dict(threshold=.001))],training_end=cutoff.isoformat(),start=(cutoff+timedelta(days=1)).isoformat(),embargo_bars=1)
    if fut:
        asset=fut[0]['id']
        add('future','Futures directional trend',[strategy('future','Futures trend','TREND',[dict(instrument_id=asset)],signal_instruments=[fut[0]['underlying']],stop_pct=.25,allow_short=True,allocation_pct=70)],risk=dict(overnight_stress_pct=30))
        if len(fut)>1:
            add('future-spread','Futures calendar spread',[strategy('calendar','Futures calendar','SCHEDULED',[dict(instrument_id=fut[0]['id']),dict(instrument_id=fut[1]['id'],ratio=-1)],schedule=[dict(at=dataset['start'],action='ENTER')],stop_pct=.25,allocation_pct=90)],risk=dict(trade_pct=1,open_pct=3,overnight_stress_pct=30,margin_pct=70))
    if opt:
        under=opt[0]['underlying'];base=dict(underlying=under,min_dte=2,max_dte=365)
        add('straddle','Long straddle with historical contract selection',[strategy('straddle','Long straddle','SCHEDULED',[
            dict(selector=dict(**base,option_type='CE')),dict(selector=dict(**base,option_type='PE'))],signal_instruments=[under],schedule=[dict(at=dataset['start'],action='ENTER')],stop_pct=2,allocation_pct=70)])
        add('credit-spread','Hedged call credit spread',[strategy('credit','Call credit spread','SCHEDULED',[
            dict(selector=dict(**base,option_type='CE',strike_offset=1)),dict(selector=dict(**base,option_type='CE'),ratio=-1)],signal_instruments=[under],schedule=[dict(at=dataset['start'],action='ENTER')],stop_pct=2,allocation_pct=90)])
        add('option-calendar','Options calendar spread',[strategy('calendar','Option calendar','SCHEDULED',[
            dict(selector=dict(**base,option_type='CE',expiry_rank=1)),dict(selector=dict(**base,option_type='CE'),ratio=-1)],signal_instruments=[under],schedule=[dict(at=dataset['start'],action='ENTER')],stop_pct=2,allocation_pct=90)])
        add('short-vol','Explicit uncovered short-volatility research',[strategy('short-vol','Short straddle','SCHEDULED',[
            dict(selector=dict(**base,option_type='CE'),ratio=-1),dict(selector=dict(**base,option_type='PE'),ratio=-1)],signal_instruments=[under],schedule=[dict(at=dataset['start'],action='ENTER')],stop_pct=2,allocation_pct=90)],execution=dict(allow_uncovered_options=True),risk=dict(overnight_stress_pct=40,margin_pct=80))
    return result


from .catalog import aware
