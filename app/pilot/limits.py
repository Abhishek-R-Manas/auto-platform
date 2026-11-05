"""Explicit optional owner budgets. No infinity sentinels or inferred allocations."""
BUDGET_FIELDS=('capital','daily_loss','max_trade_loss','max_order','max_orders_per_day')
def broker_funded(mandate):
    return mandate.get('mode','MANUAL_NSE_CNC')=='MANUAL_NSE_CNC' and mandate.get('limit_mode','CUSTOM')=='BROKER_FUNDS'
def label(mandate):
    return 'Broker funds · no app-defined caps' if broker_funded(mandate) else 'Owner-defined limits'
