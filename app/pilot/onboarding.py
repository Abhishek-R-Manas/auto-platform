"""Side-effect-free manual-account preflight; no broker write, saved mandate or arming."""
from datetime import datetime,timedelta
from pydantic import ValidationError
from ..engine import IST
from ..finance import paise
from .calendar import manual_policy
from .universe import eligible
from .limits import broker_funded,label as budget_label

def review(service,payload):
    from .service import ManualMandate,TERMINAL
    checks=[];errors={};now=service.clock()
    def check(key,label,ok,detail,step,field=None,pending=False):
        checks.append(dict(id=key,label=label,status='PASS' if ok else 'PENDING' if pending else 'FAIL',detail=detail,step=step,field=field))
    values=dict(payload);values['confirmation']='ENABLE MANUAL NSE DELIVERY TRADING'
    try:body=ManualMandate.model_validate(values)
    except ValidationError as exc:
        body=None
        for error in exc.errors(include_input=False,include_context=False,include_url=False):
            field=str(error['loc'][0]) if error['loc'] else 'limits'
            if not error['loc'] and 'Selected stocks' in error['msg']:field='symbols'
            if not error['loc'] and 'All-stocks permission' in error['msg']:field='instrument_scope'
            errors.setdefault(field,error['msg'])
    account=service._get('snapshot');status=service.readiness();fresh=status['execution_fresh']
    check('account','Current broker account',fresh,'Account synchronized for this session.' if fresh else 'Select Refresh account to verify the current login before saving.',1,'account_id',not fresh)
    if body and fresh and account['account_id']!=body.account_id:errors['account_id']='Account differs from the current Zerodha login. Refresh account and use its exact ID.'
    if body:
        if not now<body.expires_at<=now+timedelta(days=7):errors['expires_at']='Choose a future expiry within seven days.'
        try:
            policy=manual_policy(now)
            if body.expires_at.astimezone(IST).date().isoformat()>policy['config']['calendar_valid_until']:errors['expires_at']='Authority cannot extend beyond the reviewed calendar: '+policy['config']['calendar_valid_until']+'.'
        except ValueError as exc:errors['expires_at']=str(exc)
        if status['setup_verified']:
            allowed={r['tradingsymbol'] for r in service.kite.master() if eligible(r)}
            if body.instrument_scope=='ALL_NSE_EQ' and not allowed:errors['instrument_scope']='The current instrument master has no supported NSE delivery stocks. Refresh account and market metadata.'
            if not set(body.symbols)<=allowed:errors['symbols']='Choose current NSE delivery stocks or ETFs. Futures, options and indices are unavailable here.'
    limits_valid=body is not None and not errors
    check('limits','Account and trading permissions',limits_valid,'The funding policy and permitted instruments are valid.' if limits_valid else 'Complete the highlighted fields. Order and loss limits must fit inside your allocation.',2,'limits',body is None)
    uncapped=bool(body and body.limit_mode=='BROKER_FUNDS')
    cash=status['available_cash_paise']
    # Setup can stay verified, but this preflight requires a fresh execution snapshot.
    check('funding','Broker cash available' if uncapped else 'Cash covers your allocation',bool(body and fresh and cash is not None and cash>0 and (uncapped or paise(body.capital)<=cash)),'Broker-funded mode uses current cash-only funds; charges and outstanding reservations are checked for every order.' if uncapped else 'Allocated capital must be no greater than current cash-only funds. Charges and reservations are checked again for each order.',2,None if uncapped else 'capital',not fresh or body is None)
    permissions=next(g['ok'] for g in status['gates'] if g['id']=='broker_permissions')
    check('broker_permissions','NSE delivery and native stop permissions',permissions,'Zerodha must report NSE, CNC, LIMIT and SL access for this account.',1,None,not fresh)
    for field,label,detail in [
        ('static_ip_confirmed','Static IP whitelist confirmed','Complete Profile → IP Whitelist in the Kite Connect developer dashboard. This is your confirmation; the read APIs do not verify the whitelist.'),
        ('delivery_authorized','Delivery-sale authorization confirmed','Verify DDPI/POA or the applicable daily CDSL authorization in Kite. Do not enter TPIN or OTP here. Broker acceptance is not established by this confirmation.'),
        ('protective_orders_authorized','Native protective exits authorized','Authorize DAY stop-limit exits for verified buys and cancellation of entry remainders. Stops may remain unfilled after gaps and expire at day end.')]:
        confirmed=bool(body and getattr(body,field))
        check(field,label,confirmed,detail,3,field,pending=not confirmed)
    loss=service._loss(account) if fresh else None
    risk_ok=bool(body and fresh and (uncapped or (loss<paise(body.daily_loss) and service._get('loss_halt_day')!=str(now.date()))))
    check('daily_loss','No app-defined loss budget' if uncapped else 'Daily loss threshold',risk_ok,'Broker-funded mode sets no app daily-loss halt or per-trade loss budget. Native stop coverage is checked separately.' if uncapped else 'The account-wide gross negative P&L must remain below your chosen daily loss threshold; the daily halt must not be latched.',4,None if uncapped else 'daily_loss',not fresh or body is None)
    protection=service.protection()
    check('protection','Existing positions and stop coverage',not(protection['unprotected_units'] or protection['error'] or protection['suspended']),'No Artha exposure needs protection.' if not any(p['owned_units'] for p in protection['symbols']) else 'All Artha-owned units need acknowledged stop coverage, with no protection error or suspension.',4)
    orders=service.orders();uncertain=any(o['status'] in {'UNCERTAIN','SENDING','CANCEL_SENDING','CANCEL_PENDING'} for o in orders)
    check('uncertain','No unresolved broker actions',not uncertain,'Resolve uncertain sends and pending cancellations in Orders and Kite before continuing.',4)
    # Initial authority cannot replace a journal with order history.
    check('reallocation','Authority can be saved',not orders and not service.has_exposure(),'Trading limits cannot be replaced after broker orders exist without reviewed reallocation.',4)
    check('audit','Order journal integrity',service.audit()['ok'],'The durable local order journal must pass integrity verification.',4)
    check('transport','Server broker connection',service.broker.enabled,'The managed server must have the reviewed broker-order connection enabled.',4)
    ready=all(c['status']=='PASS' for c in checks if c['id']!='transport')
    return dict(limit_mode=body.limit_mode if body else None,budget_mode_label=budget_label(body.model_dump()) if body else 'Not validated',instrument_scope=body.instrument_scope if body else None,instrument_scope_label=('All supported NSE stocks and ETFs' if body.instrument_scope=='ALL_NSE_EQ' else ', '.join(body.symbols)) if limits_valid else 'Not validated',limits_valid=limits_valid,permissions_valid=bool(body and all(getattr(body,f) for f in ('static_ip_confirmed','delivery_authorized','protective_orders_authorized'))),risk_passed=all(c['status']=='PASS' for c in checks if c['step']==4 and c['id']!='transport'),can_save=ready,field_errors=errors,checks=checks,available_cash_paise=cash,gross_daily_loss_paise=loss,execution_fresh=fresh,account_id=account['account_id'] if account and status['setup_verified'] else None,observed_at=account['observed_at'] if account else None,real_orders_sent=False,authority_saved=False,calendar=status['calendar'],per_order_checks='Quotes, depth, tick size, charges, reserved funds and planned trade risk are validated when you preview each actual order.')
