"""Forward acceptance uses isolated books, controlled broker observations and explicit model fixtures."""
import copy
from datetime import timedelta
import json
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.forward.service import ForwardService
from app.forward.runtime import ForwardReplay
from app.forward.schemas import Control, Monitoring, AIConfig, Portfolio
from app.forward.drills import DrillKite, DrillHub, DrillLab, DrillConfig, AT, quote, protocol
from app.research.catalog import digest, iso
from app.candidates.provider import ModelError
from app.configuration import PrivateConfig

class ControlledMonitor:
    key='';model='CONTROLLED_MODEL'
    def __init__(self,broken=False):self.calls=[];self.broken=broken
    def status(self):return dict(configured=True,model=self.model,provider='CONTROLLED_TEST',message='Controlled model fixture; no genuine provider call')
    def request(self,context,tokens):
        self.calls.append(copy.deepcopy(context))
        if self.broken:raise ModelError('Controlled transport failure; uncertain usage, no retry')
        note=dict(summary='Controlled portfolio health explanation',findings=[dict(severity='INFO',statement='The evidence describes a paper experiment',evidence_ids=[context['facts'][0]['id']],proposed_action='OBSERVE')],limitations=['Controlled test output is not genuine model validation'])
        return note,dict(provider='CONTROLLED_TEST',live_provider_response=False,input_tokens=10,output_tokens=10,raw_output=json.dumps(note))

@pytest.fixture
def service(tmp_path):
    now=[AT];hub=DrillHub();provider=ControlledMonitor();store=ForwardService(tmp_path/'forward',DrillKite(),hub,DrillLab(),PrivateConfig(tmp_path/'.env.local'),provider=provider,clock=lambda:now[0])
    item=store.create(protocol())
    yield store,item['id'],hub,now,provider
    store.close()

def runtime_for(service):
    store,key,*_=service;doc,state=store._load(key);runtime=ForwardReplay(doc,state);runtime.state['forward']['paused']=False
    runtime.process({'DRILL':quote(AT,100)},AT,[])
    return runtime

def bar(at,price=100,identifier='DRILL'):
    return dict(instrument_id=identifier,timestamp=iso(at),open_at=iso(at-timedelta(minutes=5)),open=price,high=price+1,low=price-1,close=price,volume=1000,source='CONTROLLED_COMPLETED_CANDLE')

def finish(store,job_id):
    until=time.monotonic()+5
    while time.monotonic()<until:
        store.tick();job=next(j for j in store.jobs() if j['id']==job_id)
        if job['status'] not in {'QUEUED','RUNNING'}:return job
        time.sleep(.01)
    raise AssertionError('Controlled job exceeded test budget')

def test_protocol_is_immutable_and_create_retry_is_idempotent(service):
    store,key,*_=service
    assert store.create(protocol())['id']==key
    changed=protocol().model_copy(update={'capital':2000000})
    with pytest.raises(ValueError,match='different forward protocol'):store.create(changed)
    assert store.audit(key)['ok'] and store.portfolio(key)['status']=='PAUSED'
    assert store.report(key)['ready_for_phase5_review'] is False

def test_control_revision_and_request_binding(service):
    store,key,*_=service;p=store.portfolio(key)
    body=Control(action='RESUME',request_id='resume-once',reviewer='Controlled owner',note='Resume the controlled paper portfolio',expected_revision=p['revision'])
    assert store.control(key,body)['applied']
    assert store.control(key,body)['applied']
    with pytest.raises(ValueError,match='different inputs'):store.control(key,body.model_copy(update={'action':'PAUSE'}))
    with pytest.raises(ValueError,match='changed'):store.control(key,body.model_copy(update={'request_id':'stale-control'}))
    assert store.portfolio(key)['status']=='RUNNING'

def test_warmup_has_no_quotes_orders_or_financial_effects(service):
    store,key,_,now,_=service
    def history(doc,state,warmup,cutoff):return [bar(AT-timedelta(minutes=5*i),100+i) for i in (3,2,1)]
    store._history=history
    before=store.portfolio(key)['account'];job=store.warmup(key,'warmup-once');result=finish(store,job['id'])
    assert result['status']=='SUCCEEDED',result
    p=store.portfolio(key);assert p['history_counts']['DRILL']==3 and p['account']==before and not p['positions'] and not p['orders'] and p['cursor']==0
    assert not any(e['kind'] in {'SIGNAL','FILL','ORDER_INTENT'} for e in store.events(key))

def test_completed_signal_never_fills_on_its_decision_quote(service):
    r=runtime_for(service);at=AT+timedelta(minutes=5)
    r.process({'DRILL':quote(at,1)},at,[],[bar(at)])
    assert r.state['orders'] and not r.state['positions']
    r.process({'DRILL':quote(at+timedelta(seconds=1),1)},at+timedelta(seconds=1),[])
    assert sum(abs(l['quantity']) for _,_,l in r.lots())==1
    assert r.state['cash']==r.state['ledger_total']

def test_duplicate_and_out_of_order_observations_do_not_replenish_depth(service):
    r=runtime_for(service);r.enter(r.specs['drill']);at=AT+timedelta(seconds=1)
    r.process({'DRILL':quote(at,1)},at,[])
    before=(r.state['cash'],copy.deepcopy(r.state['positions']),copy.deepcopy(r.state['orders']))
    r.process({'DRILL':quote(at,1)},at+timedelta(seconds=1),[])
    assert (r.state['cash'],r.state['positions'],r.state['orders'])==before
    r.process({'DRILL':quote(AT,1000)},at+timedelta(seconds=2),[])
    assert r.state['cash']==before[0]

def test_stale_and_future_inputs_cannot_fill_entries(service):
    r=runtime_for(service);r.enter(r.specs['drill']);cash=r.state['cash']
    r.process({'DRILL':quote(AT,1000)},AT+timedelta(minutes=2),[])
    assert r.state['cash']==cash and not r.state['positions']
    seen=copy.deepcopy(r.state['forward']['seen'])
    r.process({'DRILL':quote(AT+timedelta(hours=1),1000)},AT+timedelta(minutes=3),[])
    assert r.state['forward']['seen']==seen

def test_market_calendar_blocks_after_hours_and_unreviewed_days(service):
    r=runtime_for(service);at=AT.replace(hour=13)
    r.process({'DRILL':quote(at,100)},at,[],[bar(at)])
    assert 'MARKET_CLOSED' in r.state['forward']['blockers'] and not r.state['orders']
    at=AT.replace(year=2027)
    r.process({'DRILL':quote(at,100)},at,[])
    assert 'CALENDAR_REVIEW_EXPIRED' in r.state['forward']['blockers']

def test_backfilled_candles_do_not_trade_earlier_signals_with_later_history(service):
    r=runtime_for(service);at=AT+timedelta(minutes=15)
    r.process({'DRILL':quote(at,100)},at,[],[bar(AT+timedelta(minutes=5),80),bar(AT+timedelta(minutes=10),90),bar(at,100)])
    signals=[e for e in r.records if e['kind']=='SIGNAL']
    assert len(signals)==1 and signals[0]['bar_time']==iso(at)
    assert any(e['kind']=='DATA_GAP' for e in r.records)

def test_partial_exit_remains_visible_without_liquidity(service):
    r=runtime_for(service);r.enter(r.specs['drill']);at=AT+timedelta(seconds=1)
    r.process({'DRILL':quote(at,1)},at,[]);r.pause('Controlled owner pause');r.close_book('drill','Controlled liquidation')
    assert r.state['positions'] and any(o['reduce_only'] for o in r.state['orders'].values())
    at+=timedelta(seconds=40);r.process({},at,[])
    assert r.state['positions'] and any(o['reduce_only'] for o in r.state['orders'].values())
    at+=timedelta(seconds=1);r.process({'DRILL':quote(at,100)},at,[])
    assert not r.state['positions'] and r.state['cash']==r.state['ledger_total']

def test_portfolio_allocation_and_spread_are_independent_of_model(service):
    r=runtime_for(service);spec=r.specs['drill'];spec['lots']=100000
    assert not r.enter(spec)
    assert any(e['kind']=='REJECT' for e in r.records)
    r.state['quotes']['DRILL']['ask']=110
    checks=r.entry_checks(spec,[dict(instrument_id='DRILL',quantity=1,price=110,product='DELIVERY')])
    assert any(c['name']=='spread' and c['status']=='FAIL' for c in checks)

def test_atomic_events_and_account_checkpoint_rollback(service):
    store,key,*_=service;doc,state=store._load(key)
    with pytest.raises(RuntimeError):
        with store.transaction():
            r=ForwardReplay(doc,state);r.event('CONTROLLED_FAULT','Controlled interrupted transaction');store._commit(doc,r);raise RuntimeError('Controlled failure')
    assert store._load(key)[1]==state and store.audit(key)['ok']
    with pytest.raises(RuntimeError,match='ledger'):
        with store.transaction():
            r=ForwardReplay(doc,state);r.state['cash']-=1;store._commit(doc,r)
    assert store._load(key)[1]==state

def test_incident_acknowledgement_does_not_clear_active_failure(service):
    from app.forward.schemas import Acknowledge
    store,key,hub,now,_=service;p=store.portfolio(key)
    store.control(key,Control(action='RESUME',request_id='resume-for-outage',reviewer='Controlled owner',note='Controlled connectivity failure test',expected_revision=p['revision']))
    hub.ready=False;store.tick();incident=next(i for i in store.incidents(key) if i['code']=='MARKET_TRANSPORT_NOT_READY')
    store.tick();assert len([i for i in store.incidents(key) if i['code']==incident['code']])==1
    assert store.acknowledge(incident['id'],Acknowledge(reviewer='Controlled owner',note='Investigating the controlled feed failure'))['status']=='ACKNOWLEDGED'
    with pytest.raises(ValueError,match='still active'):store.acknowledge(incident['id'],Acknowledge(action='RESOLVE',reviewer='Controlled owner',note='Attempting premature resolution'))
    hub.ready=True;hub.quotes={'DRILL':quote(now[0],100)};store.tick()
    assert store.acknowledge(incident['id'],Acknowledge(action='RESOLVE',reviewer='Controlled owner',note='Controlled transport is healthy again'))['status']=='RESOLVED'
    assert store.portfolio(key)['status']=='PAUSED'

def test_model_jobs_are_advisory_budgeted_and_retain_failed_attempts(service):
    store,key,_,_,provider=service;job=store.request_monitor(key,'explain-once');result=finish(store,job['id'])
    assert result['status']=='SUCCEEDED' and result['result']['authority']=='ADVISORY_ONLY'
    assert len(provider.calls)==1 and store.request_monitor(key,'explain-once')['id']==job['id']
    assert not store.portfolio(key)['orders']
    provider.broken=True;failed=store.request_monitor(key,'controlled-provider-failure');result=finish(store,failed['id'])
    assert result['status']=='FAILED' and len(provider.calls)==2
    for _ in range(3):store.tick()
    assert len(provider.calls)==2 and store.status()['reserved_output_tokens']==3000
    assert not store.report(key)['ready_for_phase5_review']

def test_unknown_ai_evidence_is_rejected(service):
    store,key,_,_,provider=service
    def bad(context,tokens):return dict(summary='Untrusted',findings=[dict(evidence_ids=['not-supplied'],severity='INFO',statement='Invalid attribution',proposed_action='OBSERVE')],limitations=['Controlled invalid output']),dict(live_provider_response=False)
    provider.request=bad;job=store.request_monitor(key,'invalid-evidence');result=finish(store,job['id'])
    assert result['status']=='FAILED' and 'outside the supplied snapshot' in result['error']

def test_ai_budget_has_no_automatic_release_after_uncertain_failure(service):
    store,key,_,_,provider=service
    store.set_monitoring(Monitoring(total_reserved_tokens=1500,max_output_tokens=1500))
    provider.broken=True;finish(store,store.request_monitor(key,'first-failure')['id'])
    with pytest.raises(ValueError,match='budget'):store.request_monitor(key,'second-failure')
    assert len(provider.calls)==1

def test_periodic_model_calls_require_explicit_consent():
    with pytest.raises(ValueError,match='consent'):Monitoring(enabled=True,consent=False)

def test_recovery_drills_leave_owner_checkpoint_unchanged(service):
    store,key,*_=service;before=copy.deepcopy(store._load(key));result=store.run_drills(key,'drill-once')
    assert result['passed'] and len(result['checks'])>=9 and result['owner_account_unchanged']
    assert store._load(key)==before and store.run_drills(key,'drill-once')['id']==result['id']

def test_api_boundaries_private_ai_configuration_and_main_account_isolation(tmp_path,monkeypatch):
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN','ARTHA_AI_API_KEY','ARTHA_AI_MODEL'):monkeypatch.delenv(name,raising=False)
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    app.state.forward.kite=DrillKite();app.state.forward.hub=DrillHub();app.state.forward.clock=lambda:AT
    with TestClient(app,base_url='http://127.0.0.1') as client:
        before=client.get('/api/state').json();body=protocol().model_dump(mode='json')
        assert client.post('/api/forward/portfolios',json=body).status_code==403
        response=client.post('/api/forward/portfolios',json=body,headers={'X-Artha-Client':'paper-workspace'});assert response.status_code==200,response.text
        key=response.json()['id'];assert client.get('/api/forward/portfolios/'+key+'/report').json()['ready_for_phase5_review'] is False
        secret='CONTROLLED_PRIVATE_AI_KEY'
        invalid=client.post('/api/forward/ai/configure',json=dict(api_key=secret,model='invalid model'),headers={'X-Artha-Client':'paper-workspace'})
        assert invalid.status_code==422 and secret not in invalid.text
        saved=client.post('/api/forward/ai/configure',json=dict(api_key=secret,model='controlled-model'),headers={'X-Artha-Client':'paper-workspace'})
        assert saved.status_code==200 and secret not in saved.text and secret not in client.get('/api/forward/status').text
        assert (tmp_path/'.env.local').stat().st_mode&0o077==0
        import tools.deploy_phase4 as deployer
        monkeypatch.setattr(deployer,'fetch',lambda path:client.get(path).json())
        assert deployer.connection_status()==dict(kite_keys_still_configured=False,kite_session_after_restart=False)
        after=client.get('/api/state').json();assert after['account']==before['account'] and after['orders']==before['orders'] and after['mode']=='PAPER' and not after['live_trading_available']


class DerivativeKite(DrillKite):
    @staticmethod
    def underlying_key(row):return 'NSE:DRILL'
    def master(self):
        return super().master()+[dict(exchange='NFO',tradingsymbol=symbol,instrument_type=kind,segment='NFO-OPT' if kind!='FUT' else 'NFO-FUT',lot_size='10',tick_size='0.05',instrument_token=str(token),expiry='2026-11-26',strike=str(strike),name='DRILL') for symbol,kind,token,strike in [('DFUT','FUT',258,0),('DHEDGE','CE',514,110),('DSHORT','CE',770,100)]]


def derivative_runtime(tmp_path,options=True):
    body=protocol().model_dump(mode='json');body['request_id']='derivative-protocol';body['bindings']={'DRILL':'NSE:DRILL'}
    strategy=body['strategies'][0];strategy['id']='derivative';strategy['lots']=2;strategy['signal_instruments']=['DRILL']
    if options:
        body['bindings'].update(HEDGE='NFO:DHEDGE',SHORT='NFO:DSHORT');strategy['legs']=[dict(instrument_id='HEDGE',ratio=1),dict(instrument_id='SHORT',ratio=-1)]
    else:body['bindings']['FUT']='NFO:DFUT';strategy['legs']=[dict(instrument_id='FUT',ratio=1)]
    body['supervision']['max_spread_bps']=1000
    store=ForwardService(tmp_path/'derivatives',DerivativeKite(),DrillHub(),DrillLab(),PrivateConfig(tmp_path/'.env.local'),clock=lambda:AT)
    key=store.create(Portfolio.model_validate(body))['id'];doc,state=store._load(key);r=ForwardReplay(doc,state);r.state['forward']['paused']=False
    q={'DRILL':quote(AT,1000),'HEDGE':quote(AT,20,2),'SHORT':quote(AT,10,6)} if options else {'DRILL':quote(AT,1000),'FUT':quote(AT,20,100)}
    r.process(q,AT,[]);return store,r,q


def test_partial_option_leg_exit_cannot_remove_required_hedges(tmp_path):
    store,r,quotes=derivative_runtime(tmp_path)
    try:
        assert r.enter(r.specs['derivative'])
        at=AT+timedelta(seconds=1)
        r.process({key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in quotes.items()},at,[])
        held={identifier:sum(lot['quantity'] for _,asset,lot in r.lots() if asset==identifier) for identifier in ('HEDGE','SHORT')}
        assert held=={'HEDGE':20,'SHORT':-10}
        r.pause('Controlled liquidation');r.close_book('derivative','Controlled partial-leg unwind')
        at+=timedelta(seconds=1);updates={key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in quotes.items()};updates['SHORT']['ask_quantity']=0
        r.process(updates,at,[])
        held={identifier:sum(lot['quantity'] for _,asset,lot in r.lots() if asset==identifier) for identifier in ('HEDGE','SHORT')}
        assert held['HEDGE']>=abs(held['SHORT']) and held['SHORT']==-10
        at+=timedelta(seconds=1);updates={key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in quotes.items()}
        r.process(updates,at,[])
        assert not r.state['positions'] and r.state['cash']==r.state['ledger_total']
    finally:store.close()


def test_futures_daily_mtm_is_estimated_idempotent_and_reconciled(tmp_path):
    store,r,quotes=derivative_runtime(tmp_path,options=False)
    try:
        assert r.enter(r.specs['derivative'])
        at=AT+timedelta(seconds=1);r.process({k:dict(v,exchange_time=iso(at),received_at=iso(at)) for k,v in quotes.items()},at,[])
        close=AT.replace(hour=10,minute=0,second=0)
        r.process({'DRILL':quote(close-timedelta(seconds=1),1000,110),'FUT':quote(close-timedelta(seconds=1),100,110)},close+timedelta(seconds=1),[])
        assert any(e['kind']=='FUTURES_MTM' and e['official'] is False for e in r.records)
        cash=r.state['cash'];r.process({},close+timedelta(seconds=2),[])
        assert r.state['cash']==cash==r.state['ledger_total']
    finally:store.close()


def test_expiry_buffer_blocks_new_derivative_entries(tmp_path):
    store,r,quotes=derivative_runtime(tmp_path,options=False)
    try:
        expiry=r.instruments['FUT']['expiry'];from app.research.schemas import aware
        at=aware(expiry)-timedelta(minutes=30);r.specs['derivative']['exit_dte_days']=0
        r.process({'DRILL':quote(at,1000),'FUT':quote(at,100)},at,[])
        checks=r.entry_checks(r.specs['derivative'],[dict(instrument_id='FUT',quantity=10,price=100,product='DERIVATIVE')])
        assert any(c['name']=='expiry_exit_buffer' and c['status']=='FAIL' for c in checks)
    finally:store.close()


def test_current_contract_term_changes_pause_entries(service):
    store,key,_,now,_=service
    original=store.kite.master
    def changed():
        rows=original();rows[0]['tick_size']='0.10';return rows
    store.kite.master=changed;now[0]+=timedelta(seconds=61)
    assert 'CONTRACT_TERMS_CHANGED' in store.portfolio(key)['readiness']


def test_notification_delivery_requires_authorization_and_never_auto_retries_uncertain_sends(service):
    from app.forward.alerts import AlertConfig,AlertRetry
    store,key,hub,now,_=service;hub.quotes={'DRILL':quote(now[0],100)};token='123456:'+('CONTROLLED_TOKEN_'*2)
    body=AlertConfig(bot_token=token,chat_id='12345',enabled=True,consent=True,daily_limit=2)
    store.configure_alerts(body);calls=[]
    def fail(text):calls.append(text);raise RuntimeError('Controlled uncertain delivery')
    store.alert_provider.send=fail
    with store.transaction():store._incident(key,'CONTROLLED_ALERT','Controlled software incident')
    until=time.monotonic()+3
    while time.monotonic()<until:
        store.tick();items=store.status()['notifications']
        if items[0]['status']=='FAILED_UNCERTAIN':break
        time.sleep(.01)
    assert items[0]['status']=='FAILED_UNCERTAIN' and len(calls)==1
    for _ in range(3):store.tick()
    assert len(calls)==1 and token not in json.dumps(store.status())
    with pytest.raises(ValueError,match='duplicate'):store.retry_notification(items[0]['id'],AlertRetry())
    assert store.retry_notification(items[0]['id'],AlertRetry(acknowledge_possible_duplicate=True))['status']=='QUEUED'


def test_disabled_notification_outbox_has_no_external_calls(service):
    store,key,*_=service;calls=[];store.alert_provider.send=lambda text:calls.append(text)
    with store.transaction():store._incident(key,'LOCAL_ONLY','Controlled local-only alert')
    for _ in range(3):store.tick()
    assert not calls and store.status()['notifications'][0]['status']=='LOCAL_DASHBOARD'


def test_full_backup_restore_checks_include_immutable_data_and_detect_tampering(tmp_path):
    from tools.forward_ops import snapshot,restore_check
    import sqlite3
    data=tmp_path/'data';data.mkdir();db=sqlite3.connect(data/'paper.sqlite3');db.execute('CREATE TABLE controlled(value)');db.execute('INSERT INTO controlled VALUES(42)');db.commit();db.close()
    artifact=data/'research/datasets/controlled/observations.parquet';artifact.parent.mkdir(parents=True);artifact.write_bytes(b'CONTROLLED_IMMUTABLE_ARTIFACT')
    directory=tmp_path/'snapshot';result=snapshot(data,directory)
    assert result['artifacts'] and restore_check(directory)['ok']
    (directory/'research/datasets/controlled/observations.parquet').write_bytes(b'CONTROLLED_TAMPERING')
    with pytest.raises(ValueError,match='artifact'):restore_check(directory)


def test_only_forward_schedules_are_eligible_for_activation(service):
    store,key,*_=service;body=protocol().model_dump(mode='json');body['request_id']='historical-schedule';body['strategies'][0]['schedule'][0]['at']=iso(AT-timedelta(days=1))
    with pytest.raises(ValueError,match='historical instructions'):store.create(Portfolio.model_validate(body))

def test_quoted_depth_participation_caps_fill_quantity(service):
    r=runtime_for(service);r.config['execution']['participation_pct']=10;r.enter(r.specs['drill'])
    at=AT+timedelta(seconds=1);r.process({'DRILL':quote(at,10)},at,[])
    assert sum(abs(lot['quantity']) for _,_,lot in r.lots())==1


def test_model_result_survives_financial_transaction_rollback_without_a_second_call(service):
    store,key,hub,now,provider=service;hub.quotes={'DRILL':quote(now[0],100)}
    job=store.request_monitor(key,'completion-before-rollback');store.tick()
    deadline=time.monotonic()+3
    while not store.futures[job['id']]['future'].done() and time.monotonic()<deadline:time.sleep(.01)
    original=store._commit;fault=[True]
    def fail_once(*args,**kwargs):
        if fault[0]:fault[0]=False;raise RuntimeError('Controlled financial transaction interruption')
        return original(*args,**kwargs)
    store._commit=fail_once;store.tick();assert len(provider.calls)==1
    store._commit=original;result=finish(store,job['id'])
    assert result['status']=='SUCCEEDED' and len(provider.calls)==1


def test_daily_close_signal_uses_next_session_quotes_without_overnight_fabricated_fills(service):
    store,key,*_=service;doc,state=store._load(key);doc=copy.deepcopy(doc);doc['config']['interval_minutes']=1440
    r=ForwardReplay(doc,state);r.state['forward']['paused']=False
    close=AT.replace(hour=10,minute=0,second=0)
    r.process({'DRILL':quote(close-timedelta(seconds=1),100)},close+timedelta(seconds=10),[],[bar(close)])
    assert not r.state['orders'] and r.state['forward']['daily_signals']
    opening=(AT+timedelta(days=1)).replace(hour=3,minute=45,second=0)
    r.process({'DRILL':quote(opening,100)},opening,[])
    assert r.state['orders'] and not r.state['positions']
    r.process({'DRILL':quote(opening+timedelta(seconds=1),100)},opening+timedelta(seconds=1),[])
    assert r.state['positions'] and r.state['cash']==r.state['ledger_total']


def test_daily_signal_is_discarded_after_its_predeclared_next_session_window(service):
    store,key,*_=service;doc,state=store._load(key);doc=copy.deepcopy(doc);doc['config']['interval_minutes']=1440
    r=ForwardReplay(doc,state);r.state['forward']['paused']=False;close=AT.replace(hour=10,minute=0,second=0)
    r.process({'DRILL':quote(close-timedelta(seconds=1),100)},close+timedelta(seconds=10),[],[bar(close)])
    late=(AT+timedelta(days=1)).replace(hour=4,minute=0,second=0);r.process({'DRILL':quote(late,100)},late,[])
    assert not r.state['orders'] and not r.state['forward']['daily_signals']
    assert any(e['kind']=='SKIP' and 'window expired' in e['reason'] for e in r.records)


def test_sessions_require_healthy_observation_time_not_one_quote(service):
    r=runtime_for(service)
    assert not r.state['forward']['sessions']
    at=AT+timedelta(hours=1);r.process({'DRILL':quote(at,100)},at,[])
    assert not r.state['forward']['sessions']
    assert sum(r.state['forward']['covered_seconds'].values())<=5


def test_private_ai_update_preserves_kite_and_alert_credentials(tmp_path):
    path=tmp_path/'.env.local';config=PrivateConfig(path);config.save_kite('CONTROLLED_KITE_KEY','CONTROLLED_KITE_SECRET');config.save_alerts('CONTROLLED_ALERT_TOKEN','12345');before=config.read()
    config.save_ai('CONTROLLED_AI_KEY','controlled-model');after=config.read()
    assert all(after[key]==value for key,value in before.items()) and path.stat().st_mode&0o077==0


def test_private_model_field_rejects_an_accidentally_pasted_api_key():
    with pytest.raises(ValueError,match='model name'):AIConfig(api_key='CONTROLLED_PROVIDER_KEY',model='sk-CONTROLLED_MISPLACED_KEY')


def test_feed_credential_rotation_is_guarded_by_forward_exposure(service):
    from app.onboarding import ConnectionSetup,Credentials
    store,key,*_=service
    setup=object.__new__(ConnectionSetup);setup.lock=__import__('threading').RLock();setup.rotation_lock=store.lock
    setup.forward_exposure=lambda:True;setup.engine=SimpleNamespace(lock=__import__('threading').RLock())
    with pytest.raises(ValueError,match='forward paper positions'):setup.save(Credentials(api_key='CONTROLLED_NEW_KEY',api_secret='CONTROLLED_NEW_SECRET'))


def test_entry_wall_clock_ttl_expires_even_if_candle_cursor_stalls(service):
    r=runtime_for(service);r.enter(r.specs['drill']);cursor=r.state['cursor']
    at=AT+timedelta(minutes=r.config['execution']['order_ttl_bars']*5,seconds=1)
    r.process({'DRILL':quote(at,100)},at,[])
    assert not r.state['positions'] and r.state['cursor']==cursor
    assert any(e['kind']=='CANCELLED' and 'wall-clock TTL' in e['reason'] for e in r.records)


def test_reduce_only_liquidation_remains_pending_after_entry_ttl(service):
    r=runtime_for(service);r.enter(r.specs['drill']);at=AT+timedelta(seconds=1)
    r.process({'DRILL':quote(at,100)},at,[]);r.close_book('drill','Controlled exit request')
    later=at+timedelta(minutes=r.config['execution']['order_ttl_bars']*5,seconds=1)
    r.process({'DRILL':quote(later,0)},later,[])
    assert r.state['positions'] and any(o['reduce_only'] for o in r.state['orders'].values())


def registry_protocol(service):
    store,key,*_=service;doc,_=store._load(key);body=protocol().model_dump(mode='json');candidate='a'*64
    body.update(request_id='controlled-registry-activation',origin='REGISTRY',candidate_ids=[candidate],strategies=[])
    manifest=copy.deepcopy(doc['manifest']);manifest.update(frequency='MINUTE',interval_minutes=5)
    packet=dict(id='controlled-sealed-packet',packet_hash='b'*64,dataset_id='c'*64,protocol_hash='d'*64,review_build=doc['build'],strategy_build=doc['build'])
    strategy=copy.deepcopy(doc['replay_config']['strategies'][0]);study=dict(config=dict(risk=body['risk'],execution=body['execution']),windows=dict(TRAIN=dict(end=iso(AT-timedelta(days=1)))))
    store.lab=SimpleNamespace(verify_audit=lambda:dict(ok=True),paper_packet=lambda key:copy.deepcopy(packet),version=lambda key:dict(study_id='controlled-study',strategy=copy.deepcopy(strategy)),study=lambda key:copy.deepcopy(study),research=SimpleNamespace(catalog=SimpleNamespace(verify=lambda key:dict(manifest=copy.deepcopy(manifest)))),generations=lambda:[])
    return store,body,packet,manifest


def test_registry_activation_preserves_packet_and_strategy_then_detects_revocation(service):
    store,body,packet,_=registry_protocol(service);portfolio=store.create(Portfolio.model_validate(body))
    assert portfolio['status']=='PAUSED' and portfolio['qualification_label']=='APPROVED_CANDIDATE_FORWARD_EVALUATION'
    doc,_=store._load(portfolio['id']);assert doc['candidates'][0]['packet_hash']==packet['packet_hash']
    assert doc['replay_config']['strategies'][0]==store.lab.version(body['candidate_ids'][0])['strategy']
    def revoked(key):raise ValueError('Controlled approval revoked')
    store.lab.paper_packet=revoked
    assert 'CANDIDATE_APPROVAL_REVOKED' in store.portfolio(portfolio['id'])['readiness']


def test_registry_activation_rejects_weaker_risk_and_silent_fixed_asset_rebinding(service):
    store,body,_,manifest=registry_protocol(service);weaker=copy.deepcopy(body);weaker['risk']['drawdown_pct']+=1
    with pytest.raises(ValueError,match='cannot weaken'):store.create(Portfolio.model_validate(weaker))
    manifest['instruments'][0]['symbol']='ANOTHER_ASSET'
    with pytest.raises(ValueError,match='different asset'):store.create(Portfolio.model_validate(body))


def test_declared_forward_split_adjusts_owned_lots_once_without_a_cash_profit(service):
    r=runtime_for(service);r.enter(r.specs['drill']);first=AT+timedelta(seconds=1)
    r.process({'DRILL':quote(first,100)},first,[]);cash=r.state['cash']
    effective=AT+timedelta(seconds=2);r.manifest['actions']=[dict(instrument_id='DRILL',kind='SPLIT',effective_at=iso(effective),ratio=2)]
    r.process({'DRILL':quote(effective,100,50)},effective,[])
    assert sum(lot['quantity'] for _,_,lot in r.lots())==6 and r.state['cash']==cash
    later=effective+timedelta(seconds=1);r.process({'DRILL':quote(later,100,50)},later,[])
    assert sum(lot['quantity'] for _,_,lot in r.lots())==6 and r.state['cash']==r.state['ledger_total']


def test_fractional_corporate_entitlement_remains_an_unresolved_obligation(service):
    r=runtime_for(service);r.enter(r.specs['drill']);first=AT+timedelta(seconds=1)
    r.process({'DRILL':quote(first,100)},first,[])
    effective=AT+timedelta(seconds=2);r.manifest['actions']=[dict(instrument_id='DRILL',kind='SPLIT',effective_at=iso(effective),ratio=1.5)]
    r.process({'DRILL':quote(effective,100)},effective,[])
    assert r.state['halt']=='Unresolved corporate-action evidence'
    assert any(item['kind']=='CORPORATE_ACTION' for item in r.state['obligations'])


# Regressions found during the end-to-end Phase 4 audit.
def test_paid_dispatch_cannot_start_inside_an_uncommitted_transaction(service):
    store,key,_,_,provider=service
    job=store.request_monitor(key,'durable-reservation')
    with pytest.raises(RuntimeError,match='commit'):
        with store.transaction():store._dispatch_jobs()
    assert not provider.calls
    assert next(j for j in store.jobs() if j['id']==job['id'])['status']=='QUEUED'
    assert finish(store,job['id'])['status']=='SUCCEEDED' and len(provider.calls)==1


def test_failed_model_incident_can_be_resolved_only_after_successful_recovery(service):
    from app.forward.schemas import Acknowledge
    store,key,_,_,provider=service;provider.broken=True
    finish(store,store.request_monitor(key,'outage-before-recovery')['id'])
    incident=next(i for i in store.incidents(key) if i['code']=='AI_REQUEST_FAILED')
    provider.broken=False
    finish(store,store.request_monitor(key,'explicit-successful-recovery')['id'])
    cleared=next(i for i in store.incidents(key) if i['id']==incident['id'])
    assert not cleared['condition_active']
    assert store.acknowledge(incident['id'],Acknowledge(action='RESOLVE',reviewer='Controlled owner',note='Verified a successful explicit recovery call'))['status']=='RESOLVED'


def test_hung_candle_job_blocks_and_late_result_is_discarded(service):
    from concurrent.futures import Future
    store,key,hub,now,_=service;hub.quotes={'DRILL':quote(now[0],100)}
    p=store.portfolio(key)
    store.control(key,Control(action='RESUME',request_id='resume-for-hung-candle',reviewer='Controlled owner',note='Test independent candle worker timeout',expected_revision=p['revision']))
    job=store._queue(key,'CANDLES','hung-candle-job')
    future=Future()
    with store.transaction():
        item=next(j for j in store.jobs() if j['id']==job['id']);item.update(status='RUNNING',started_at=store.now());store._save_job(item)
    store.futures[job['id']]=dict(future=future,kind='CANDLES')
    now[0]+=timedelta(seconds=31);hub.quotes={'DRILL':quote(now[0],100)};store.tick()
    assert store.portfolio(key)['status']=='PAUSED'
    assert next(j for j in store.jobs() if j['id']==job['id'])['status']=='TIMED_OUT'
    assert any(i['code']=='CANDLE_WORKER_STALLED' for i in store.incidents(key))
    future.set_result([bar(now[0])]);store.tick()
    assert store.portfolio(key)['history_counts']=={}


def test_missing_completed_candle_blocks_even_with_fresh_executable_quotes(service):
    store,key,hub,now,_=service;hub.quotes={'DRILL':quote(now[0],100)}
    p=store.portfolio(key);store.control(key,Control(action='RESUME',request_id='resume-missing-bars',reviewer='Controlled owner',note='Check completed-bar stream independently',expected_revision=p['revision']))
    now[0]+=timedelta(minutes=5,seconds=121);hub.quotes={'DRILL':quote(now[0],100)}
    store.tick();p=store.portfolio(key)
    assert p['status']=='PAUSED' and 'COMPLETED_BAR_GAP' in p['readiness']
    assert any(i['code']=='COMPLETED_BAR_GAP' for i in store.incidents(key))


def test_paused_daily_signal_is_cancelled_by_explicit_operator_pause(service):
    r=runtime_for(service)
    r.state['forward']['daily_signals']={'drill':dict(bar_time=iso(AT),outcome=dict(action='ENTER',direction=1))}
    r.pause('Operator revoked pending daily instructions')
    assert not r.state['forward']['daily_signals']


def test_quote_arrival_order_does_not_create_false_derivative_staleness(tmp_path):
    store,r,quotes=derivative_runtime(tmp_path)
    try:
        r.document['config']['bar_source']='QUOTE_SAMPLES'
        r.state['quotes']={};r.state['forward']['seen']={}
        # Underlying must be installed before freshness is checked on any option.
        reverse=dict(reversed(list(quotes.items())))
        at=AT+timedelta(seconds=1)
        r.process({key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in reverse.items()},at,[])
        assert set(r.state['forward']['buffers'])==set(quotes)
    finally:store.close()


def test_report_includes_latest_quote_mark_and_preserves_interbar_drawdown(service):
    store,key,*_=service;r=runtime_for(service)
    with store.transaction():store._commit(r.document,r)
    r.enter(r.specs['drill'])
    with store.transaction():store._commit(r.document,r)
    first=AT+timedelta(seconds=1);r.process({'DRILL':quote(first,100)},first,[])
    with store.transaction():store._commit(r.document,r)
    down=first+timedelta(seconds=1);r.process({'DRILL':quote(down,100,90)},down,[])
    worst=r.account_view()['drawdown_pct']
    with store.transaction():store._commit(r.document,r)
    up=down+timedelta(seconds=1);r.process({'DRILL':quote(up,100,100)},up,[])
    with store.transaction():store._commit(r.document,r)
    report=store.report(key)
    assert report['performance']['total_return_pct']==pytest.approx((report['account']['equity']/1000000-1)*100)
    assert report['performance']['max_drawdown_pct']>=worst>0
    assert {'stress_scenarios','benchmark_observations','guardian_review'}<={g['name'] for g in report['gates']}


def test_guardian_review_is_bound_to_evidence_idempotent_and_revocable(service):
    from app.forward.schemas import GuardianReview
    store,key,_,now,_=service;report=store.report(key)
    body=GuardianReview(request_id='guardian-review-once',evidence_hash=report['evidence_hash'],decision='CONTINUE_PAPER',reviewer='Workspace owner',note='Reviewed the exact paper evidence and its limitations',conflicts='No declared conflicts; local owner assertion',expires_at=iso(now[0]+timedelta(days=1)))
    item=store.review(key,body)
    assert store.review(key,body)==item and store.audit(key)['ok']
    assert store.report(key)['guardian']['current'] and not store.report(key)['ready_for_phase5_review']
    with pytest.raises(ValueError,match='gates'):store.review(key,body.model_copy(update={'request_id':'premature-approval','decision':'APPROVE_REVIEW'}))
    stale=body.model_copy(update={'request_id':'stale-evidence','evidence_hash':'0'*64})
    with pytest.raises(ValueError,match='changed'):store.review(key,stale)
    revoked=body.model_copy(update={'request_id':'revoke-review','decision':'REVOKE'})
    store.review(key,revoked)
    assert not store.report(key)['guardian']['current']


def test_guardian_expiry_and_unverified_identity_are_explicit(service):
    from app.forward.schemas import GuardianReview
    store,key,_,now,_=service
    body=GuardianReview(request_id='short-lived-review',evidence_hash=store.report(key)['evidence_hash'],decision='CONTINUE_PAPER',reviewer='Workspace owner',note='Controlled local review with a bounded validity period',conflicts='Controlled assertion',expires_at=iso(now[0]+timedelta(seconds=5)))
    store.review(key,body);now[0]+=timedelta(seconds=6)
    guardian=store.report(key)['guardian']
    assert not guardian['current'] and guardian['identity_assurance']=='LOCAL_OWNER_ASSERTION' and guardian['license_verified'] is False


def test_forge_status_does_not_claim_missing_feeds_licenses_or_traction(service):
    store,*_=service;forge=store.status()['forge']
    assert forge['name']=='Centaur Strategy Forge'
    assert forge['regulatory_status']=='NOT_VERIFIED' and forge['customer_execution_enabled'] is False
    assert all(forge['research_inputs'][name]=='NOT_CONNECTED' for name in ('fii_dii','macro','iv_skew_feed'))
    assert forge['commercial_targets']=={'community_users':1000,'scale_users':10000}



def test_daily_ai_budget_counts_attempts_beyond_the_display_window(service):
    store,key,*_=service
    store.set_monitoring(Monitoring(daily_requests=1,total_reserved_tokens=100000))
    finish(store,store.request_monitor(key,'daily-first')['id'])
    with store.transaction():
        for index in range(205):
            store._save_job(dict(id='history-'+str(index),request_id='history-'+str(index),portfolio_id=key,kind='CANDLES',status='SUCCEEDED',created_at=store.now()))
    with pytest.raises(ValueError,match='budget'):store.request_monitor(key,'daily-cannot-evade')


def test_guardian_review_mutation_is_detected_by_the_event_audit(service):
    from app.forward.schemas import GuardianReview
    store,key,_,now,_=service
    item=store.review(key,GuardianReview(request_id='review-integrity',evidence_hash=store.report(key)['evidence_hash'],decision='CONTINUE_PAPER',reviewer='Controlled guardian',note='Exact report review for integrity regression',conflicts='Controlled test assertion',expires_at=iso(now[0]+timedelta(days=1))))
    changed=dict(item,decision='APPROVE_REVIEW')
    with store.transaction():store.db.execute('UPDATE reviews SET document=? WHERE id=?',(json.dumps(changed),item['id']))
    assert not store.audit(key)['ok']
    with pytest.raises(ValueError,match='integrity'):store.reviews(key)



def test_continuous_option_risk_halts_existing_exposure_without_a_model(tmp_path):
    store,r,quotes=derivative_runtime(tmp_path)
    try:
        assert r.enter(r.specs['derivative'])
        at=AT+timedelta(seconds=1)
        r.process({key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in quotes.items()},at,[])
        assert r.state['positions'] and not r.state['halt']
        # Tighten the fixture's sensitivity budget to exercise the held-position boundary.
        r.policy['vega_ten_point_loss_pct']=.000001
        r.halt_check()
        assert r.state['halt']=='Continuous options/portfolio risk threshold breached'
        assert any(order['reduce_only'] for order in r.state['orders'].values())
        assert not any(not order['reduce_only'] and order['status'] in {'OPEN','PARTIAL','PENDING_ACK'} for order in r.state['orders'].values())
    finally:store.close()


def test_guardian_snapshot_does_not_follow_new_portfolio_events(service):
    from app.forward.schemas import GuardianReview
    store,key,_,now,_=service;captured=store.report(key)['evidence_hash']
    with store.transaction():
        doc,state=store._load(key);runtime=ForwardReplay(doc,state);runtime.event('CONTROLLED_CHANGE','New evidence after guardian captured the report');store._commit(doc,runtime)
    body=GuardianReview(request_id='cannot-follow-new-events',evidence_hash=captured,decision='CONTINUE_PAPER',reviewer='Controlled guardian',note='Review the captured evidence, never a silently updated report',conflicts='Controlled test assertion',expires_at=iso(now[0]+timedelta(days=1)))
    with pytest.raises(ValueError,match='changed'):store.review(key,body)


def test_restore_check_detects_review_table_changes(service,tmp_path):
    import sqlite3
    from tools.forward_ops import check_database
    from app.forward.schemas import GuardianReview
    store,key,_,now,_=service
    item=store.review(key,GuardianReview(request_id='backup-review',evidence_hash=store.report(key)['evidence_hash'],decision='CONTINUE_PAPER',reviewer='Controlled guardian',note='A reviewed paper snapshot included in the backup',conflicts='Controlled test assertion',expires_at=iso(now[0]+timedelta(days=1))))
    saved=tmp_path/'guardian-backup.sqlite3'
    with sqlite3.connect(saved) as db:
        store.db.backup(db)
        db.execute('UPDATE reviews SET document=? WHERE id=?',(json.dumps(dict(item,decision='APPROVE_REVIEW')),item['id']))
    with pytest.raises(ValueError,match='Guardian review'):check_database(saved)



def test_guardian_review_does_not_advance_the_financial_clock(tmp_path):
    from app.forward.schemas import GuardianReview
    store,r,quotes=derivative_runtime(tmp_path)
    try:
        with store.transaction():store._commit(r.document,r)
        key=r.document['id'];at=AT+timedelta(seconds=5);store.clock=lambda:at
        report=store.report(key)
        body=GuardianReview(request_id='clock-preserving-review',evidence_hash=report['evidence_hash'],decision='CONTINUE_PAPER',reviewer='Controlled guardian',note='Review does not manufacture a new market observation',conflicts='Controlled test assertion',expires_at=iso(at+timedelta(days=1)))
        store.review(key,body)
        assert store._load(key)[1]['clock']==iso(AT)
        assert store.report(key)['guardian']['current']
    finally:store.close()



def test_recovery_pack_covers_the_declared_phase4_failure_modes(service):
    store,key,*_=service
    before=copy.deepcopy(store._load(key))
    result=store.run_drills(key,'full-phase4-failure-pack')
    checks={item['name']:item['passed'] for item in result['checks']}
    required={'feed_loss','provider_failure','accounting_reconciliation','partial_option_legs','missing_official_settlement'}
    assert required<=checks.keys() and all(checks[name] for name in required)
    assert store._load(key)==before and result['owner_account_unchanged']
