"""Synchronized quote snapshots produce explicitly estimated European IV surfaces."""
from datetime import datetime,timedelta,timezone
import math
from zoneinfo import ZoneInfo
from ..finance import implied_volatility,option_greeks
from ..research.schemas import aware,iso

UNDERLYINGS={'NIFTY':'NSE:NIFTY 50','BANKNIFTY':'NSE:NIFTY BANK'}

def quote_time(raw,at,maximum):
    stamp=raw.get('timestamp')
    if not stamp:raise ValueError('Quote has no exchange timestamp')
    stamp=datetime.fromisoformat(stamp) if isinstance(stamp,str) else stamp
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=ZoneInfo('Asia/Kolkata'))
    age=(aware(at)-aware(stamp)).total_seconds()
    if not 0<=age<=maximum:raise ValueError('Executable option/underlying quotes are stale or future-dated')
    return aware(stamp)

def interpolate(points,target=.25):
    ordered=sorted((abs(point['delta']),point['iv_pct']) for point in points)
    for (left,a),(right,b) in zip(ordered,ordered[1:]):
        if left<=target<=right:return a if right==left else a+(b-a)*(target-left)/(right-left)
    return None

def kite_surfaces(kite,policy,at):
    clock=at if callable(at) else lambda:at
    at=clock()
    if not kite.status()['authenticated']:raise ValueError('Sign in to Kite before requesting IV/skew')
    master=kite.master();by_key={row['exchange']+':'+row['tradingsymbol']:row for row in master}
    keys=[UNDERLYINGS[name] for name in policy['underlyings']]
    spot_quotes=kite._quotes(keys);at=clock();selected=[];groups=[]
    for name in policy['underlyings']:
        key=UNDERLYINGS[name];spot=spot_quotes.get(key,{})
        quote_time(spot,at,policy['max_quote_age_seconds']);price=float(spot.get('last_price',0))
        if not math.isfinite(price) or price<=0:raise ValueError('Underlying price is unavailable')
        rows=[row for row in master if row['exchange']=='NFO' and row['name']==name and row['instrument_type'] in {'CE','PE'} and row.get('expiry') and datetime.fromisoformat(row['expiry']).replace(hour=15,minute=30,tzinfo=ZoneInfo('Asia/Kolkata'))>aware(at)]
        expiries=sorted({row['expiry'] for row in rows})[:policy['expiries']]
        if not expiries:raise ValueError('No current option contracts for the selected underlying')
        for index,expiry in enumerate(expiries):
            candidates=[row for row in rows if row['expiry']==expiry];strikes=sorted({float(row['strike']) for row in candidates});atm=min(range(len(strikes)),key=lambda i:abs(strikes[i]-price));width=policy['strikes_each_side'];wanted=set(strikes[max(0,atm-width):atm+width+1])
            chosen=[row for row in candidates if float(row['strike']) in wanted];selected.extend(chosen);groups.append((name,expiry,index,chosen))
    if len(selected)+len(keys)>500:raise ValueError('Option analytics exceed the 500-contract snapshot budget')
    raw=kite._quotes(keys+['NFO:'+row['tradingsymbol'] for row in selected]);at=clock();result=[]
    for name,expiry,index,contracts in groups:
        key=UNDERLYINGS[name];underlying=raw.get(key,{});spot_stamp=quote_time(underlying,at,policy['max_quote_age_seconds']);spot=float(underlying.get('last_price',0))
        if key in by_key and int(underlying.get('instrument_token',0))!=int(by_key[key]['instrument_token']):raise ValueError('Underlying contract identity changed')
        if not math.isfinite(spot) or spot<=0:raise ValueError('Invalid underlying price')
        expiry_at=datetime.fromisoformat(expiry).replace(hour=15,minute=30,tzinfo=ZoneInfo('Asia/Kolkata'));years=(expiry_at-aware(at)).total_seconds()/(365*86400);points=[];rejected=[];stamps=[spot_stamp]
        for contract in contracts:
            identity='NFO:'+contract['tradingsymbol'];q=raw.get(identity,{})
            try:
                stamp=quote_time(q,at,policy['max_quote_age_seconds'])
                if abs((stamp-spot_stamp).total_seconds())>policy['max_timestamp_skew_seconds']:raise ValueError('Option and underlying observations are not synchronized')
                if int(q.get('instrument_token',0))!=int(contract['instrument_token']):raise ValueError('Contract identity mismatch')
                buys=q.get('depth',{}).get('buy',[]);sells=q.get('depth',{}).get('sell',[])
                bid=next(level for level in buys if float(level.get('quantity',0))>0 and float(level.get('price',0))>0);ask=next(level for level in sells if float(level.get('quantity',0))>0 and float(level.get('price',0))>0)
                b=float(bid['price']);a=float(ask['price']);mid=(a+b)/2
                if not all(math.isfinite(v) for v in (b,a,mid)) or b>a or (a-b)/mid*10000>policy['max_spread_bps']:raise ValueError('Invalid or excessively wide option quote')
                for level in (bid,ask):
                    quantity=float(level['quantity'])
                    if not math.isfinite(quantity) or quantity!=int(quantity):raise ValueError('Invalid quote depth')
                strike=float(contract['strike']);kind=contract['instrument_type'];iv=implied_volatility(mid,spot,strike,years,kind,policy['interest_rate'],policy['dividend_yield'])
                if iv is None:raise ValueError('Quote falls outside the declared pricing model')
                greek=option_greeks(spot,strike,years,iv,kind,policy['interest_rate'],policy['dividend_yield']);stamps.append(stamp)
                points.append(dict(symbol=contract['tradingsymbol'],instrument_token=int(contract['instrument_token']),strike=strike,option_type=kind,bid=b,ask=a,iv_pct=iv*100,iv_bid_pct=(low*100 if (low:=implied_volatility(b,spot,strike,years,kind,policy['interest_rate'],policy['dividend_yield'])) is not None else None),iv_ask_pct=(high*100 if (high:=implied_volatility(a,spot,strike,years,kind,policy['interest_rate'],policy['dividend_yield'])) is not None else None),exchange_time=iso(stamp),oi=int(q.get('oi',0)),volume=int(q.get('volume',0)),**{key:greek[key] for key in ('delta','gamma','theta','vega')}))
            except (ValueError,TypeError,KeyError,StopIteration,OverflowError):rejected.append(identity)
        if not points:continue
        closest=min(point['strike'] for point in points if abs(point['strike']-spot)==min(abs(p['strike']-spot) for p in points));atm_points=[p['iv_pct'] for p in points if p['strike']==closest]
        call=interpolate([p for p in points if p['option_type']=='CE']);put=interpolate([p for p in points if p['option_type']=='PE']);atm_iv=sum(atm_points)/len(atm_points)
        result.append(dict(underlying=name,expiry=expiry,tenor='near' if index==0 else 'next' if index==1 else 'third',spot=spot,atm_strike=closest,atm_iv_pct=atm_iv,skew25_pp=put-call if put is not None and call is not None else None,butterfly25_pp=(put+call)/2-atm_iv if put is not None and call is not None else None,points=points,rejected_contracts=rejected,estimated=True,model='EUROPEAN_SPOT_BLACK_SCHOLES',interest_rate=policy['interest_rate'],dividend_yield=policy['dividend_yield'],oldest_exchange_time=iso(min(stamps)),published_at=iso(max(stamps)),valid_until=iso(min(stamps)+timedelta(seconds=policy['max_quote_age_seconds'])),status='COMPLETE' if put is not None and call is not None else 'PARTIAL',limitations=['IV/Greeks are model estimates from a bounded chain snapshot','No trading authority or profitability claim','Missing delta brackets are not extrapolated']))
    if not result:raise ValueError('No usable fresh option chain; verify session, depth and data permissions')
    return result,raw
