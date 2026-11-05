"""Manual delivery universe eligibility; account, quote and risk gates are independent."""
def eligible(row):
    try:
        return row.get('exchange')=='NSE' and row.get('instrument_type')=='EQ' and row.get('segment')!='INDICES' and int(row.get('lot_size',0))==1
    except (TypeError,ValueError):return False

def all_equities(mandate):
    return mandate.get('mode')=='MANUAL_NSE_CNC' and mandate.get('instrument_scope','SELECTED')=='ALL_NSE_EQ'

def label(mandate):
    return 'All supported NSE stocks and ETFs' if all_equities(mandate) else ', '.join(mandate.get('symbols',[])) or 'Not configured'
