"""Money calculations in integer paise; model estimates use floating point."""
import math
from decimal import Decimal, ROUND_HALF_UP


def paise(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Amounts must be finite numbers")
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rupees(value):
    return round(value / 100, 2)


def tick_price(price, tick=.05, side="BUY"):
    units = Decimal(str(price)) / Decimal(str(tick))
    rounding = "ROUND_CEILING" if side == "BUY" else "ROUND_FLOOR"
    return float(units.to_integral_value(rounding=rounding) * Decimal(str(tick)))


def fees(kind, side, turnover, dp=False):
    """Current illustrative paper profile; historical research passes dated profiles."""
    from .charges import estimate
    return estimate(kind,side,turnover,dp=dp)


def _normal(x):
    return .5 * (1 + math.erf(x / math.sqrt(2)))


def option_value(spot, strike, years, volatility, option_type, rate=.065, dividend_yield=0):
    if years <= 0:
        return max(0, spot - strike) if option_type == "CE" else max(0, strike - spot)
    if min(spot, strike, volatility) <= 0:
        raise ValueError("Invalid option model inputs")
    root = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate-dividend_yield+volatility**2/2)*years)/(volatility*root)
    d2 = d1-volatility*root
    discount=math.exp(-rate*years);carry=math.exp(-dividend_yield*years)
    if option_type == "CE": return spot*carry*_normal(d1)-strike*discount*_normal(d2)
    return strike*discount*_normal(-d2)-spot*carry*_normal(-d1)


def option_greeks(spot, strike, years, volatility, option_type, rate=.065, dividend_yield=0):
    years=max(years,1/(365*24*60));root=math.sqrt(years)
    d1=(math.log(spot/strike)+(rate-dividend_yield+volatility**2/2)*years)/(volatility*root)
    d2=d1-volatility*root;density=math.exp(-d1*d1/2)/math.sqrt(2*math.pi)
    discount=math.exp(-rate*years);carry=math.exp(-dividend_yield*years)
    theta=-spot*carry*density*volatility/(2*root)
    if option_type=="CE":
        delta=carry*_normal(d1)
        theta+=dividend_yield*spot*carry*_normal(d1)-rate*strike*discount*_normal(d2)
    else:
        delta=carry*(_normal(d1)-1)
        theta+=rate*strike*discount*_normal(-d2)-dividend_yield*spot*carry*_normal(-d1)
    return dict(delta=delta,gamma=carry*density/(spot*volatility*root),theta=theta/365,
                vega=spot*carry*density*root/100,iv=volatility*100)


def implied_volatility(price, spot, strike, years, option_type, rate=.065, dividend_yield=0):
    if years <= 0 or price <= 0:
        return None
    low, high = .001, 5.0
    if not option_value(spot, strike, years, low, option_type, rate, dividend_yield) <= price <= option_value(spot, strike, years, high, option_type, rate, dividend_yield):
        return None
    for _ in range(70):
        mid = (low + high) / 2
        if option_value(spot, strike, years, mid, option_type, rate, dividend_yield) > price:
            high = mid
        else:
            low = mid
    return (low + high) / 2
