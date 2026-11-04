"""Shared estimated transaction charges; profiles are explicit and effective dated."""
from decimal import Decimal, ROUND_HALF_UP

DEFAULTS=dict(brokerage=20,brokerage_rate=.0003,equity_delivery_brokerage=0,
    equity_stt_delivery=.001,equity_stt_intraday_sell=.00025,futures_stt_sell=.0005,options_stt_sell=.0015,
    exchange_eq=.0000307,exchange_fut=.0000183,exchange_opt=.0003553,
    stamp_eq=.00015,stamp_eq_intraday=.00003,stamp_fut=.00002,stamp_opt=.00003,
    sebi_rate=.000001,gst_rate=.18,dp_fee=15.34)


def estimate(kind,side,turnover,dp=False,product="DELIVERY",profile=None):
    rates={**DEFAULTS,**(profile or {})}
    get=lambda name:Decimal(str(rates[name]))
    amount=Decimal(str(turnover))
    if kind=="EQ" and product=="DELIVERY": brokerage=get("equity_delivery_brokerage")
    elif kind=="OPT": brokerage=get("brokerage")
    else: brokerage=min(get("brokerage"),amount*get("brokerage_rate"))
    if kind=="EQ": stt=get("equity_stt_delivery") if product=="DELIVERY" else get("equity_stt_intraday_sell") if side=="SELL" else Decimal(0)
    else: stt=get("futures_stt_sell" if kind=="FUT" else "options_stt_sell") if side=="SELL" else Decimal(0)
    exchange=amount*get("exchange_"+kind.lower())
    sebi=amount*get("sebi_rate")
    stamp_key="stamp_eq_intraday" if kind=="EQ" and product=="INTRADAY" else "stamp_"+kind.lower()
    result=dict(brokerage=brokerage,stt=(amount*stt).quantize(Decimal("1"),rounding=ROUND_HALF_UP),
        exchange=exchange,sebi=sebi,stamp=amount*get(stamp_key) if side=="BUY" else Decimal(0),
        gst=(brokerage+exchange+sebi)*get("gst_rate"),dp=get("dp_fee") if dp else Decimal(0))
    result={k:float(v.quantize(Decimal(".01"),rounding=ROUND_HALF_UP)) for k,v in result.items()}
    result["total"]=round(sum(result.values()),2)
    return result
