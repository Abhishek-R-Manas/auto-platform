"""Read-only presentation of broker portfolio snapshots. Amounts are integer paise."""
from decimal import Decimal,InvalidOperation
from .finance import paise


def numeric(value,nonnegative=False):
    if value is None or isinstance(value,bool):return None
    try:number=Decimal(str(value))
    except (InvalidOperation,ValueError):return None
    return number if number.is_finite() and abs(number)<=Decimal('1e18') and (not nonnegative or number>=0) else None


def quantity(value,nonnegative=False):
    number=numeric(value,nonnegative)
    return int(number) if number is not None and number==number.to_integral_value() else None


def money_value(value):
    number=numeric(value)
    return paise(number) if number is not None else None


def total(rows,key):
    return sum(r[key] for r in rows) if all(r[key] is not None for r in rows) else None


def percent(pnl,basis):
    return float(Decimal(pnl)/Decimal(basis)*100) if pnl is not None and basis is not None and basis>0 else None


def holding(row,index):
    settled=quantity(row.get('quantity'),True);t1=quantity(row.get('t1_quantity',0),True)
    mtf=row.get('mtf') if isinstance(row.get('mtf'),dict) else {};margin_qty=quantity(mtf.get('quantity',0),True)
    units=settled+t1+margin_qty if all(v is not None for v in (settled,t1,margin_qty)) else None
    avg=numeric(row.get('average_price'),True);mtf_avg=numeric(mtf.get('average_price'),True)
    last=numeric(row.get('last_price'),True);last=last if last else None;close=numeric(row.get('close_price'),True)
    cost=(Decimal(0) if settled+t1==0 else (settled+t1)*avg if avg is not None else None) if settled is not None and t1 is not None else None
    if margin_qty:cost=cost+margin_qty*mtf_avg if cost is not None and mtf_avg is not None else None
    value=units*last if units is not None and last is not None else None
    invested=paise(cost) if cost is not None else None;current=paise(value) if value is not None else None
    pnl=current-invested if current is not None and invested is not None else None
    day=paise((last-close)*units) if last is not None and close and units is not None else None
    return dict(id='h'+str(index),exchange=str(row.get('exchange','')),symbol=str(row.get('tradingsymbol','')),kind='HOLDING',product='CNC + MTF' if margin_qty and (settled or 0)+(t1 or 0) else 'MTF' if margin_qty else str(row.get('product') or 'CNC'),quantity=units,
        settled_quantity=settled,t1_quantity=t1,mtf_quantity=margin_qty,used_quantity=quantity(row.get('used_quantity',0),True),collateral_quantity=quantity(row.get('collateral_quantity',0),True),
        average_price=float(cost/units) if cost is not None and units else float(avg) if avg is not None else None,last_price=float(last) if last is not None else None,
        invested_paise=invested,value_paise=current,pnl_paise=pnl,net_change_pct=percent(pnl,invested),day_pnl_paise=day,day_change_pct=percent(day,current-day) if current is not None and day is not None else None,
        broker_pnl_paise=money_value(row.get('pnl')),discrepancy=bool(row.get('discrepancy')),notes=['Cost and unrealized P&L use reported settled + T+1 + MTF quantities; collateral is not added again','Used quantity is reported separately, not deducted a second time','Fees, MTF financing and corporate-action cost corrections are excluded'])


def position(row,index):
    units=quantity(row.get('quantity'));avg=numeric(row.get('average_price'),True);last=numeric(row.get('last_price'),True);multiplier=numeric(row.get('multiplier',1),True)
    return dict(id='p'+str(index),exchange=str(row.get('exchange','')),symbol=str(row.get('tradingsymbol','')),kind='POSITION',product=str(row.get('product','')),quantity=units,
        average_price=float(avg) if avg is not None else None,last_price=float(last) if last is not None and last else None,multiplier=float(multiplier) if multiplier is not None else None,
        value_paise=paise(units*last*multiplier) if units is not None and last is not None and last and multiplier else None,pnl_paise=money_value(row.get('pnl')),realised_paise=money_value(row.get('realised')),unrealised_paise=money_value(row.get('unrealised')),m2m_paise=money_value(row.get('m2m')),overnight_quantity=quantity(row.get('overnight_quantity')),
        notes=['Position P&L, realised/unrealised and M2M are broker-reported snapshot values','Signed position value is exposure, not account capital or margin'])


def portfolio_view(snapshot):
    if snapshot is None:return None
    holdings=[holding(row,i) for i,row in enumerate(snapshot.get('holdings',[]))]
    positions=[position(row,i) for i,row in enumerate(snapshot.get('positions',{}).get('net',[]))]
    hs={key:total(holdings,key) for key in ('invested_paise','value_paise','pnl_paise','day_pnl_paise')}
    hs.update(count=len(holdings),net_change_pct=percent(hs['pnl_paise'],hs['invested_paise']),day_change_pct=percent(hs['day_pnl_paise'],hs['value_paise']-hs['day_pnl_paise']) if hs['value_paise'] is not None and hs['day_pnl_paise'] is not None else None,incomplete_rows=sum(any(row[k] is None for k in ('invested_paise','value_paise','pnl_paise')) for row in holdings))
    ps={key:total(positions,key) for key in ('pnl_paise','realised_paise','unrealised_paise','m2m_paise')};ps.update(count=len(positions),open_count=sum(p['quantity'] is not None and p['quantity']!=0 for p in positions))
    return dict(observed_at=snapshot.get('observed_at'),holdings=holdings,positions=positions,holdings_summary=hs,positions_summary=ps,source='KITE_SNAPSHOT',execution_authority=False)


def broker_rows(snapshot):
    """Legacy row projection, separate from the execution reconciliation state."""
    if snapshot is None:return None
    fields={'exchange','tradingsymbol','product','quantity','average_price','last_price','pnl','day_change','close_price'}
    return {kind:[{key:value for key,value in row.items() if key in fields} for row in (snapshot['holdings'] if kind=='holdings' else snapshot['positions']['net'])] for kind in ('holdings','positions')}
