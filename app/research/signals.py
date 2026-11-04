"""Bounded declarative strategies and pure indicator/model calculations."""
import math
import statistics
from datetime import datetime

from ..strategies import evaluate as trend_crossover
from .catalog import digest
from .schemas import aware

FAMILIES={
 "TREND":"Fast/slow moving-average crossover; shared with the paper baseline",
 "MEAN_REVERSION":"Enter at a rolling-price z-score extreme; exit toward its mean",
 "BREAKOUT":"Break above prior highs or below prior lows",
 "MOMENTUM":"Rank trailing returns and periodically rebalance selected legs",
 "PAIRS":"Mean reversion of an aligned log-price spread",
 "SCHEDULED":"Pre-specified entry, exit and cancellation decisions",
 "RULES":"Whitelisted indicator comparisons and bounded boolean expressions",
 "LINEAR_MODEL":"Ridge regression fitted only before an explicit training cutoff",
 "VOLATILITY":"Realised-volatility/jump trigger on a reference instrument",
 "MARKET_MAKING":"Requires an additional order-book/queue execution implementation"}
FEATURES={"close","open","high","low","volume","oi","sma","ema","rsi","return","volatility","zscore","feature"}
OPERATORS={"gt":lambda a,b:a>b,"gte":lambda a,b:a>=b,"lt":lambda a,b:a<b,"lte":lambda a,b:a<=b,"eq":lambda a,b:a==b}


def validate_rule(rule,depth=0,budget=None):
    budget=[0] if budget is None else budget
    budget[0]+=1
    if depth>8 or budget[0]>128 or not isinstance(rule,dict): raise ValueError("Rule tree exceeds the bounded expression contract")
    if len(rule)==1 and next(iter(rule)) in {"all","any","not"}:
        kind=next(iter(rule));children=rule[kind] if kind!="not" else [rule[kind]]
        if not isinstance(children,list) or not 1<=len(children)<=30: raise ValueError("Boolean rule needs bounded children")
        for child in children: validate_rule(child,depth+1,budget)
        return
    if set(rule)!={"left","op","right"} or rule["op"] not in OPERATORS: raise ValueError("Unsupported rule operator or fields")
    for side in (rule["left"],rule["right"]):
        if isinstance(side,(int,float)) and not isinstance(side,bool) and math.isfinite(side): continue
        if not isinstance(side,dict) or side.get("feature") not in FEATURES or set(side)-{"feature","instrument_id","period","name"}: raise ValueError("Unsupported feature expression")
        if not isinstance(side.get("period",20),int) or not 1<=side.get("period",20)<=1000: raise ValueError("Feature period out of range")


def value(expression,histories,default):
    if isinstance(expression,(int,float)): return expression
    rows=histories.get(expression.get("instrument_id",default),[])
    if not rows: return None
    feature=expression["feature"];period=expression.get("period",20)
    close=[r["close"] for r in rows]
    if feature in {"close","open","high","low","volume","oi"}: return rows[-1].get(feature)
    if feature=="feature":
        import json
        item=json.loads(rows[-1].get("features_json","{} ")).get(expression.get("name"))
        return item["value"] if item and aware(item["available_at"])<=aware(rows[-1]["timestamp"]) and (not item.get('valid_until') or aware(item['valid_until'])>=aware(rows[-1]['timestamp'])) else None
    if len(close)<period+1: return None
    sample=close[-period:]
    if feature=="sma": return statistics.mean(sample)
    if feature=="ema":
        result=sample[0];alpha=2/(period+1)
        for price in sample[1:]: result=alpha*price+(1-alpha)*result
        return result
    if feature=="return": return close[-1]/close[-period-1]-1
    if feature=="zscore":
        sd=statistics.pstdev(sample)
        return (close[-1]-statistics.mean(sample))/sd if sd else 0
    returns=[b/a-1 for a,b in zip(close[-period-1:-1],close[-period:])]
    if feature=="volatility": return statistics.pstdev(returns)
    if feature=="rsi":
        gains=sum(max(0,r) for r in returns);losses=-sum(min(0,r) for r in returns)
        return 100-100/(1+gains/losses) if losses else 100 if gains else 50
    return None


def rule_result(rule,histories,default,observed):
    if "all" in rule:
        values=[rule_result(r,histories,default,observed) for r in rule["all"]]
        return False if False in values else None if None in values else True
    if "any" in rule:
        values=[rule_result(r,histories,default,observed) for r in rule["any"]]
        return True if True in values else None if None in values else False
    if "not" in rule:
        value_=rule_result(rule["not"],histories,default,observed)
        return None if value_ is None else not value_
    a,b=value(rule["left"],histories,default),value(rule["right"],histories,default)
    observed.append(dict(expression=rule,left=a,right=b))
    return OPERATORS[rule["op"]](a,b) if a is not None and b is not None else None


def _solve(matrix,target):
    # Small bounded ridge system (at most 11 coefficients); no arbitrary user code.
    n=len(target);m=[list(row)+[v] for row,v in zip(matrix,target)]
    for column in range(n):
        pivot=max(range(column,n),key=lambda r:abs(m[r][column]))
        m[column],m[pivot]=m[pivot],m[column]
        if abs(m[column][column])<1e-15: return [0.]*n
        divisor=m[column][column];m[column]=[v/divisor for v in m[column]]
        for row in range(n):
            if row!=column:
                factor=m[row][column];m[row]=[a-factor*b for a,b in zip(m[row],m[column])]
    return [row[-1] for row in m]


def fit_model(rows,cutoff,lags,ridge,start=None):
    prices=[r["close"] for r in rows if aware(r["timestamp"])<=aware(cutoff) and (not start or aware(r["timestamp"])>=aware(start))]
    returns=[b/a-1 for a,b in zip(prices,prices[1:])]
    if len(returns)<lags+20: return None
    samples=[([1.,*returns[i-lags:i]],returns[i]) for i in range(lags,len(returns))]
    n=lags+1
    matrix=[[sum(x[a]*x[b] for x,y in samples)+(ridge if a==b and a else 0) for b in range(n)] for a in range(n)]
    target=[sum(x[a]*y for x,y in samples) for a in range(n)]
    return dict(coefficients=_solve(matrix,target),training_end=cutoff,training_samples=len(samples),lags=lags,
                version="ridge-lag-returns/1.0.0",training_data_hash=digest(prices))


def signal(spec,histories,book,at,local_state,training_end=None,training_start=None):
    params=spec["parameters"];family=spec["family"]
    reference=(spec["signal_instruments"] or [leg["instrument_id"] for leg in spec["legs"] if leg["instrument_id"]])[0] if (spec["signal_instruments"] or any(l["instrument_id"] for l in spec["legs"])) else spec["legs"][0]["selector"]["underlying"]
    rows=histories.get(reference,[])
    inputs=dict(reference=reference,available_at=at,observations=len(rows))
    result=lambda action,reason,**extra:dict(action=action,reason=reason,inputs=inputs,**extra)
    def directional(direction,reason):
        if book:
            if local_state.get("last_direction",1)==direction: return result("HOLD",reason+"; existing exposure already follows this direction")
            return result("REVERSE" if spec["allow_short"] else "EXIT",reason,direction=direction)
        return result("ENTER" if direction>0 or spec["allow_short"] else "HOLD",reason,direction=direction)
    if family=="SCHEDULED":
        done=local_state.setdefault("scheduled",[])
        for index,item in enumerate(spec["schedule"]):
            if index not in done and aware(item["at"])<=aware(at):
                done.append(index)
                return result(item["action"],"Pre-specified scheduled decision",direction=1)
        return result("HOLD","No scheduled decision is due")
    if not rows: return result("WARMUP","No reference observations are available")
    close=[r["close"] for r in rows]
    lookback=params["lookback"]
    inputs["observed_prices"]=close[-max(params["slow"]+1,lookback+1):]
    if family=="TREND":
        outcome=trend_crossover(params,close);inputs.update(outcome["inputs"])
        if outcome["signal"]=="BUY": return directional(1,"Bullish moving-average crossover")
        if outcome["signal"]=="EXIT": return directional(-1,"Bearish moving-average crossover")
        return result(outcome["signal"],outcome["reason"])
    if family=="RULES":
        observed=[]
        if book and spec["exit_rule"] and rule_result(spec["exit_rule"],histories,reference,observed):
            inputs["features"]=observed;return result("EXIT","Declarative exit rule passed")
        enters=rule_result(spec["entry_rule"],histories,reference,observed);inputs["features"]=observed
        if enters is None: return result("WARMUP","Required rule features are not yet available")
        return result("ENTER" if enters and not book else "HOLD","Declarative entry condition passed" if enters else "Declarative entry condition is false",direction=1)
    if family=="LINEAR_MODEL":
        if aware(at)<=aware(training_end): return result("WARMUP","Collecting training-only observations before cutoff")
        model=local_state.get("model")
        if model is None:
            model=fit_model(rows,training_end,params["model_lags"],params["ridge"],training_start)
            if not model: return result("WARMUP","Insufficient pre-cutoff observations for model fit")
            local_state["model"]=model
        returns=[b/a-1 for a,b in zip(close,close[1:])]
        features=[1.,*returns[-model["lags"]:]]
        prediction=sum(a*b for a,b in zip(model["coefficients"],features))
        inputs.update(model=model,features=features,prediction=prediction)
        if prediction>params["threshold"]: return directional(1,"Positive model prediction exceeds threshold")
        if prediction < -params["threshold"]: return directional(-1,"Negative model prediction exceeds threshold")
        return result("HOLD","Model prediction is inside the no-trade interval")
    if len(rows)<lookback+1: return result("WARMUP",f"Need {lookback+1} reference observations")
    if family=="MEAN_REVERSION":
        z=value(dict(feature="zscore",period=lookback),histories,reference);inputs["zscore"]=z
        if book and abs(z)<=params["exit_z"]: return result("EXIT","Price returned toward its rolling mean")
        if not book and z<=-params["entry_z"]: return result("ENTER","Price below rolling mean threshold",direction=1)
        if not book and spec["allow_short"] and z>=params["entry_z"]: return result("ENTER","Price above rolling mean threshold",direction=-1)
    elif family=="BREAKOUT":
        high=max(r["high"] for r in rows[-lookback-1:-1]);low=min(r["low"] for r in rows[-lookback-1:-1]);inputs.update(prior_high=high,prior_low=low)
        if close[-1]>high: return directional(1,"Close broke above prior highs")
        if close[-1]<low: return directional(-1,"Close broke below prior lows")
    elif family=="PAIRS":
        left,right=spec["signal_instruments"]
        a={r["timestamp"]:r["close"] for r in histories.get(left,[])}
        aligned=[math.log(a[r["timestamp"]]/r["close"]) for r in histories.get(right,[]) if r["timestamp"] in a][-lookback:]
        if len(aligned)<lookback: return result("WARMUP","Insufficient time-aligned pairs history")
        sd=statistics.pstdev(aligned);z=(aligned[-1]-statistics.mean(aligned))/sd if sd else 0;inputs["spread_zscore"]=z
        if book and abs(z)<=params["exit_z"]: return result("EXIT","Pair spread reverted toward its mean")
        if not book and abs(z)>=params["entry_z"]: return result("ENTER","Pair spread exceeded threshold",direction=-1 if z>0 else 1)
    elif family=="MOMENTUM":
        if len(rows)%params["rebalance_bars"]==0:
            ranked=[]
            universe={leg['instrument_id'] for leg in spec['legs'] if leg['instrument_id']}
            for identifier,observations in histories.items():
                if identifier in universe and len(observations)>lookback: ranked.append((observations[-1]["close"]/observations[-lookback-1]["close"]-1,identifier))
            ranked.sort(reverse=True);selected=[identifier for score,identifier in ranked[:params["top_n"]] if score>params["threshold"]]
            inputs["ranking"]=ranked
            return result("REBALANCE","Periodic trailing-return ranking",direction=1,selected=selected)
    elif family=="VOLATILITY":
        change=close[-1]/close[-2]-1;vol=value(dict(feature="volatility",period=lookback),histories,reference)
        inputs.update(last_return=change,realised_volatility=vol)
        if not book and abs(change)>=params["threshold"]: return result("ENTER","Observed return exceeded the volatility trigger",direction=1)
    return result("HOLD","No eligible entry or exit condition")


def capabilities(dataset,config):
    manifest=dataset["manifest"];ids={i["id"] for i in manifest["instruments"]}
    blockers=[];warnings=list(dataset["quality"]["warnings"])
    if dataset["status"]!="READY": blockers.append("Dataset is quarantined; correct the source and import a new revision")
    if manifest["prices_adjusted"]: blockers.append("Execution requires raw prices; supply raw data with separate corporate actions")
    if config["benchmark_id"] and config["benchmark_id"] not in ids: blockers.append("Unknown benchmark instrument")
    for spec in config["strategies"]:
        if manifest["frequency"]=="DAY" and (not spec["allow_overnight"] or any(l["product"]=="INTRADAY" for l in spec["legs"])): blockers.append(spec["id"]+": intraday strategies require intraday bars or quote observations")
        if spec["family"]=="MARKET_MAKING": blockers.append(spec["id"]+": order-book/queue simulation is not implemented; bar/quote fills cannot certify this capability")
        required=set(spec["signal_instruments"])|{l["instrument_id"] for l in spec["legs"] if l["instrument_id"]}
        if not required<=ids: blockers.append(spec["id"]+": unknown reference or leg instruments: "+", ".join(sorted(required-ids)))
        for name in ("entry_rule","exit_rule"):
            if spec[name]: validate_rule(spec[name])
        if any(l["selector"] for l in spec["legs"]) and not spec["signal_instruments"]: warnings.append(spec["id"]+": contract selection uses the underlying's available observations")
    if config["execution"]["margin_model"]=="HISTORICAL_GROSS" and not manifest["margin_history_complete"]: blockers.append("Historical margin mode requires declared complete margin observations")
    if config["execution"]["margin_model"]=="CONSERVATIVE_GROSS": warnings.append("Margin uses conservative gross estimates; exchange portfolio offsets are not reconstructed")
    if config["execution"]["bar_ambiguity"]=="FAVORABLE_FIRST": warnings.append("Favorable-first bar ordering is an optimistic sensitivity scenario")
    if config["execution"]["allow_uncovered_options"]: warnings.append("Uncovered option research may lose beyond available capital; explicit tail and funding controls apply")
    return dict(runnable=not blockers,blockers=blockers,warnings=list(dict.fromkeys(warnings)),
        fidelity="QUOTED_TOP_OF_BOOK" if manifest["frequency"]=="QUOTE" else "DAILY_BAR" if manifest["frequency"]=="DAY" else "INTRADAY_BAR",
        families=[dict(id=k,description=v,supported=k!="MARKET_MAKING") for k,v in FAMILIES.items()])
