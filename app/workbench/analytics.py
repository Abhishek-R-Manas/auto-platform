"""Expiry contracts are exact before declared costs; earlier/multi-expiry values are model scenarios."""
from datetime import datetime,timedelta,timezone
import math
from decimal import Decimal
from ..finance import option_value,option_greeks

def analyze(body,now=None):
    now=now or datetime.now(timezone.utc);b=body.basket;legs=[l for l in b.legs if l.enabled];manual=b.manual_pnl if b.manual_pnl_enabled else 0;cost_decimal=Decimal(str(b.cost_estimate))-Decimal(str(manual));cost=float(cost_decimal)
    if not legs:raise ValueError("Enable at least one leg for analysis")
    expiries=sorted({leg.expiry for leg in legs if leg.kind in {'CE','PE','FUT'} and leg.expiry is not None})
    horizon=expiries[0] if expiries else now+timedelta(days=30)
    years=max(0,(horizon-now).total_seconds()/(365*86400));single=len(expiries)<=1
    def signed(leg):return leg.lots*leg.lot_size*(1 if leg.side=='BUY' else -1)
    def terminal(spot):
        s=Decimal(str(spot));total=-cost_decimal
        for l in legs:
            strike=Decimal(str(l.strike));value=max(Decimal(0),s-strike) if l.kind=='CE' else max(Decimal(0),strike-s) if l.kind=='PE' else s
            total+=signed(l)*(value-Decimal(str(l.premium)))
        return float(total)
    def scenario(spot,at):
        result=-cost
        for l in legs:
            t=max(0,((l.expiry or horizon)-at).total_seconds()/(365*86400))
            iv=max(.001,((l.iv_pct or b.volatility_pct)+body.iv_shift_pp)/100)
            value=option_value(spot,l.strike,t,iv,l.kind,b.interest_rate,b.dividend_yield) if l.kind in {'CE','PE'} else spot
            result+=signed(l)*(value-l.premium)
        return result
    strikes=sorted({l.strike for l in legs if l.kind in {'CE','PE'}});knots=[0.,*strikes]
    tail=sum(signed(l) for l in legs if l.kind in {'CE','EQ','FUT'})
    values=[terminal(x) for x in knots];breaks=[]
    if single:
        for x,y in zip(knots,knots[1:]):
            a,c=terminal(x),terminal(y)
            if abs(a)<1e-8:breaks.append(x)
            if a*c<0:breaks.append(x+(y-x)*(-a)/(c-a))
        last=knots[-1];value=terminal(last)
        if abs(value)<1e-8:breaks.append(last)
        if tail and (root:=last-value/tail)>last:breaks.append(root)
    breaks=sorted({round(x,8) for x in breaks if x>0})
    maximum=None if not single or tail>0 else max(values);minimum=None if not single or tail<0 else min(values)
    pop=None;expected=None
    if single and years>0:
        sigma=b.volatility_pct/100;mu=math.log(b.spot)+(b.interest_rate-b.dividend_yield-.5*sigma*sigma)*years;sd=sigma*math.sqrt(years)
        def cdf(x):return 0 if x<=0 else 1 if math.isinf(x) else .5*(1+math.erf((math.log(x)-mu)/(sd*math.sqrt(2))))
        partitions=sorted({0.,*strikes,*breaks});partitions.append(float('inf'));pop=0
        for lo,hi in zip(partitions,partitions[1:]):
            midpoint=(lo+hi)/2 if math.isfinite(hi) else lo+b.spot
            if terminal(midpoint)>1e-8:pop+=cdf(hi)-cdf(lo)
        expected=-cost
        for l in legs:
            future=option_value(b.spot,l.strike,years,sigma,l.kind,b.interest_rate,b.dividend_yield)*math.exp(b.interest_rate*years) if l.kind in {'CE','PE'} else b.spot*math.exp((b.interest_rate-b.dividend_yield)*years)
            expected+=signed(l)*(future-l.premium)
    greek=dict(delta=0.,gamma=0.,theta=0.,vega=0.)
    for l in legs:
        if l.kind in {'CE','PE'}:
            t=max(0,(l.expiry-now).total_seconds()/(365*86400))
            g=option_greeks(b.spot,l.strike,t,(l.iv_pct or b.volatility_pct)/100,l.kind,b.interest_rate,b.dividend_yield) if t>0 else dict(delta=(1 if b.spot>l.strike else 0 if b.spot<l.strike else .5) if l.kind=='CE' else (-1 if b.spot<l.strike else 0 if b.spot>l.strike else -.5),gamma=0,theta=0,vega=0)
            for k in greek:greek[k]+=signed(l)*g[k]
        else:greek['delta']+=signed(l)
    target_at=min(horizon,now+timedelta(days=body.days_forward));target_spot=b.spot*(1+body.target_move_pct/100)
    points=sorted({max(.00001,b.spot*(1-body.range_pct/100))+(2*b.spot*body.range_pct/100)*i/200 for i in range(201)}|{x for x in [*strikes,*breaks,b.spot,target_spot] if x>0})
    rows=[dict(spot=x,at_expiry=terminal(x) if single else None,at_target=scenario(x,target_at)) for x in points]
    target=scenario(target_spot,target_at)
    premium=sum(signed(l)*l.premium for l in legs if l.kind!='FUT')
    leg_rows=[];target_greeks=dict(delta=0.,gamma=0.,theta=0.,vega=0.)
    for index,l in enumerate(b.legs):
        units=signed(l);t=max(0,((l.expiry or horizon)-now).total_seconds()/(365*86400));tt=max(0,((l.expiry or horizon)-target_at).total_seconds()/(365*86400));iv=(l.iv_pct or b.volatility_pct)/100
        intrinsic=max(0,b.spot-l.strike) if l.kind=='CE' else max(0,l.strike-b.spot) if l.kind=='PE' else b.spot
        value=option_value(b.spot,l.strike,t,iv,l.kind,b.interest_rate,b.dividend_yield) if l.kind in {'CE','PE'} else b.spot
        shifted=max(.001,iv+body.iv_shift_pp/100)
        tv=option_value(target_spot,l.strike,tt,shifted,l.kind,b.interest_rate,b.dividend_yield) if l.kind in {'CE','PE'} else target_spot
        def gs(spot,t,vol):
            if l.kind not in {'CE','PE'}:return dict(delta=1.,gamma=0.,theta=0.,vega=0.)
            if t>0:return option_greeks(spot,l.strike,t,vol,l.kind,b.interest_rate,b.dividend_yield)
            return dict(delta=(1 if spot>l.strike else 0 if spot<l.strike else .5) if l.kind=='CE' else (-1 if spot<l.strike else 0 if spot>l.strike else -.5),gamma=0.,theta=0.,vega=0.)
        g={k:v*units for k,v in gs(b.spot,t,iv).items() if k in target_greeks}
        if l.enabled:
            for k,v in gs(target_spot,tt,shifted).items():
                if k in target_greeks:target_greeks[k]+=v*units
        leg_rows.append(dict(index=index,enabled=l.enabled,units=units,key=l.key,kind=l.kind,model_price=value,intrinsic=intrinsic,time_value=max(0,value-intrinsic) if l.kind in {'CE','PE'} else None,current_model_pnl=units*(value-l.premium),target_pnl=units*(tv-l.premium),greeks=g))
    table=[]
    for index in range(-10,11):
        move=body.table_center_move_pct+index*body.table_step_pct;spot=b.spot*(1+move/100)
        if spot>0:table.append(dict(spot=spot,move_pct=move,at_target=scenario(spot,target_at),at_expiry=terminal(spot) if single else None))
    heatmap=[]
    for fraction in (0,.25,.5,.75,1):
        at=now+timedelta(seconds=max(0,(horizon-now).total_seconds())*fraction)
        heatmap.append(dict(at=at.isoformat(),values=[dict(move_pct=move,pnl=scenario(b.spot*(1+move/100),at)) for move in (-10,-5,0,5,10)]))
    return dict(points=rows,net_debit=premium,net_credit=-premium,cost_estimate=b.cost_estimate,manual_pnl_applied=manual,leg_analysis=leg_rows,target_greeks=target_greeks,payoff_table=table,heatmap=heatmap,valuation_at=now.isoformat(),max_profit=max(0,maximum) if maximum is not None else None,max_loss=max(0,-minimum) if minimum is not None else None,best_case_pnl=maximum,worst_case_pnl=minimum,
        unlimited_profit=bool(single and tail>0),unlimited_loss=bool(single and tail<0),breakevens=breaks if single else [],model_pop_pct=pop*100 if pop is not None else None,model_expected_pnl=expected,
        target_pnl=target,target_spot=target_spot,target_at=target_at.isoformat(),horizon=horizon.isoformat(),single_expiry=single,greeks=greek,
        reward_risk=maximum/(-minimum) if maximum is not None and minimum is not None and minimum<0 else None,
        margin=None,margin_status='Broker margin verification required',source=b.source,
        assumptions=dict(interest_rate=b.interest_rate,dividend_yield=b.dividend_yield,underlying_volatility_pct=b.volatility_pct,iv_shift_pp=body.iv_shift_pp),
        limitations=['Exact expiry payoff applies only to the supplied single-expiry contracts, premiums, quantities and fixed cost estimate','Earlier-date P&L and model POP/expected P&L use European Black–Scholes and a risk-neutral lognormal distribution; they are not forecasts','Multi-expiry bounds and POP are not inferred; surviving contracts retain modeled time value','Gap execution, physical settlement, actual broker margin and final charges are not modeled here','Analysis and saved drafts never place orders or qualify a strategy'])
