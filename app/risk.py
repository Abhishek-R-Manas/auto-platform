"""Pure money/risk checks shared by paper and historical execution."""
def budget_checks(*, equity, trade_risk, open_risk, funding_needed, funding_available,
                  margin, stress, risk, futures=True):
    values=[("trade_risk",trade_risk,equity*risk["trade_pct"]/100,"Per-trade planned loss exceeds the risk budget"),
            ("portfolio_risk",open_risk,equity*risk["open_pct"]/100,"Aggregate open risk exceeds the portfolio budget"),
            ("funding",funding_needed,funding_available,"Insufficient virtual cash or margin after pending-order reservations"),
            ("futures_margin",margin,equity*risk["margin_pct"]/100,"Futures margin utilisation exceeds the configured limit"),
            ("joint_stress",stress,equity*risk["overnight_stress_pct"]/100,"Illustrative joint portfolio stress exceeds the risk budget")]
    return [dict(name=name,status="PASS" if (name=="futures_margin" and not futures) or observed<=limit else "FAIL",
                 reason="Within policy" if (name=="futures_margin" and not futures) or observed<=limit else message,
                 observed=round(observed/100,2),limit=round(limit/100,2)) for name,observed,limit,message in values]


def fill_cash_delta(kind, signed_quantity, price_paise, fee_paise, realised_paise=0):
    return (realised_paise if kind=="FUT" else -signed_quantity*price_paise)-fee_paise
