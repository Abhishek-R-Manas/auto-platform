"""Recovery/fault drills use isolated controlled books and a read-only owner backup."""
from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from .schemas import Portfolio, Control
from .runtime import ForwardReplay
from ..research.catalog import digest, iso

AT=datetime(2026,11,4,4,30,tzinfo=timezone.utc)

class DrillKite:
    master_hash='CONTROLLED_DRILL_MASTER';master_date=AT.date()
    def status(self):return dict(authenticated=True)
    def master(self):
        return [dict(exchange='NSE',tradingsymbol='DRILL',instrument_type='EQ',segment='NSE',lot_size='1',tick_size='0.05',instrument_token='257',expiry='',strike='0',name='CONTROLLED DRILL')]

class DrillHub:
    def __init__(self):self.quotes={};self.ready=True
    def set_forward_instruments(self,items):pass
    def status(self):return dict(enabled=True,phase='STREAMING' if self.ready else 'RECONNECTING',connections={'1':'CONNECTED' if self.ready else 'DISCONNECTED'})
    def forward_quotes(self,items):return copy.deepcopy(self.quotes)

class DrillLab:
    def __init__(self):self.provider=type('Provider',(),{'key':'','model':''})()
    def verify_audit(self):return dict(ok=True)
    def generations(self):return []

class DrillConfig:
    def save_ai(self,*args):raise AssertionError('Drills never persist credentials')

def quote(at,quantity=1,price=100):
    return dict(last=price,bid=price-.05,ask=price+.05,bid_quantity=quantity,ask_quantity=quantity,exchange_time=iso(at),received_at=iso(at),cumulative_volume=100,transport='CONTROLLED_DRILL')

def protocol():
    return Portfolio.model_validate(dict(request_id='controlled-drill',name='Isolated controlled recovery drill',capital=1000000,
        bindings={'DRILL':'NSE:DRILL'},strategies=[dict(id='drill',name='Controlled scheduled drill',family='SCHEDULED',signal_instruments=['DRILL'],legs=[dict(instrument_id='DRILL')],size_mode='LOTS',lots=3,stop_pct=2,schedule=[dict(at=iso(AT),action='ENTER')])],
        calendar_valid_until='2026-12-31',calendar_source='CONTROLLED_TEST_CALENDAR',reviewer='Controlled test',review_note='Isolated software verification, not market or economic evidence',data_rights_confirmed=True,assumptions_reviewed=True,
        execution=dict(max_quote_age_seconds=30,latency_seconds=0,participation_pct=100)))

def unavailable_monitor():
    from .provider import MonitorProvider
    provider=MonitorProvider();provider.key='';provider.model=''
    return provider


def run_drills(owner,identifier):
    from .service import ForwardService
    checks=[]
    before_doc,before_state=owner._load(identifier);before=digest(dict(document=before_doc,state=before_state))
    with tempfile.TemporaryDirectory(prefix='artha-forward-drill-') as directory:
        root=Path(directory);hub=DrillHub()
        service=ForwardService(root/'isolated',DrillKite(),hub,DrillLab(),DrillConfig(),provider=unavailable_monitor(),clock=lambda:AT)
        try:
            portfolio=service.create(protocol().model_copy(update={'bar_source':'QUOTE_SAMPLES'}));key=portfolio['id'];doc,state=service._load(key)
            runtime=ForwardReplay(doc,state);runtime.state['forward']['paused']=False
            runtime.process({'DRILL':quote(AT,1)},AT,[])
            runtime.enter(runtime.specs['drill']);runtime.process({'DRILL':quote(AT+timedelta(seconds=1),1)},AT+timedelta(seconds=1),[])
            assert runtime.state['positions'] and sum(abs(l['quantity']) for _,_,l in runtime.lots())==1
            checks.append(dict(name='partial_fill',passed=True,evidence='Three requested shares filled only one share of observed depth'))
            watermark=digest(dict(cash=runtime.state['cash'],positions=runtime.state['positions'],orders=runtime.state['orders']))
            same=quote(AT+timedelta(seconds=1),1)
            runtime.process({'DRILL':same},AT+timedelta(seconds=2),[])
            assert digest(dict(cash=runtime.state['cash'],positions=runtime.state['positions'],orders=runtime.state['orders']))==watermark
            checks.append(dict(name='duplicate_quote',passed=True,evidence='Identical observation did not replenish depth or duplicate a fill'))
            cash=runtime.state['cash'];runtime.process({'DRILL':quote(AT,100)},AT+timedelta(minutes=2),[])
            assert runtime.state['cash']==cash and 'STALE_OR_MISSING_QUOTES' in runtime.state['forward']['blockers']
            checks.append(dict(name='stale_data',passed=True,evidence='Old observation supplied no executable entry liquidity'))
            seen=copy.deepcopy(runtime.state['forward']['seen'])
            runtime.process({'DRILL':quote(AT+timedelta(hours=1),100)},AT+timedelta(minutes=3),[])
            assert runtime.state['forward']['seen']==seen
            checks.append(dict(name='future_observation',passed=True,evidence='Future observation was rejected'))
            runtime.close_book('drill','Controlled thin-depth exit')
            assert runtime.state['positions'] and any(o['reduce_only'] for o in runtime.state['orders'].values())
            checks.append(dict(name='uncertain_exit',passed=True,evidence='Liquidation request remained pending while fresh depth was unavailable'))
            old=service._load(key)[1]
            try:
                with service.transaction():
                    broken=ForwardReplay(doc,old);broken.event('FAULT','Controlled transaction interruption');service._commit(doc,broken)
                    raise RuntimeError('Controlled rollback')
            except RuntimeError:pass
            assert service._load(key)[1]==old and service.audit()['ok']
            checks.append(dict(name='atomic_rollback',passed=True,evidence='Interrupted event/checkpoint transaction left neither change committed'))
            try:
                with service.transaction():
                    broken=ForwardReplay(doc,old);broken.state['cash']-=1
                    service._commit(doc,broken)
            except RuntimeError:pass
            else:raise AssertionError('An unreconciled cash change was committed')
            assert service._load(key)[1]==old and service.audit()['ok']
            checks.append(dict(name='accounting_reconciliation',passed=True,evidence='A one-paise cash/ledger mismatch was rejected and rolled back'))
            from ..candidates.provider import ModelError
            class FailedMonitor:
                def status(self):return dict(configured=True)
                def request(self,*args):raise ModelError('Controlled unavailable provider; no network request')
            service.provider=FailedMonitor()
            job=service.request_monitor(key,'controlled-provider-outage')
            for _ in range(200):
                service.tick()
                if next(item for item in service.jobs() if item['id']==job['id'])['status']=='FAILED':break
                time.sleep(.01)
            else:raise AssertionError('Controlled provider failure was not retained')
            assert service.last_error is None and service.status()['reserved_output_tokens']>0
            checks.append(dict(name='provider_failure',passed=True,evidence='Failed model request retained its reservation while deterministic supervision continued'))

            resumed=service.portfolio(key);service.control(key,Control(action='RESUME',request_id='drill-resume',reviewer='Controlled test',note='Controlled recovery exercise',expected_revision=resumed['revision']))
            hub.ready=False;service.tick()
            assert service.portfolio(key)['status']=='PAUSED' and any(item['code']=='MARKET_TRANSPORT_NOT_READY' for item in service.incidents(key))
            checks.append(dict(name='feed_loss',passed=True,evidence='Transport loss independently paused a running isolated book and retained an incident'))
            hub.ready=True;hub.quotes={'DRILL':quote(AT,100)}
            resumed=service.portfolio(key);service.control(key,Control(action='RESUME',request_id='drill-recovered-resume',reviewer='Controlled test',note='Controlled transport recovered before restart',expected_revision=resumed['revision']))

        finally:service.close()
        restored=ForwardService(root/'isolated',DrillKite(),DrillHub(),DrillLab(),DrillConfig(),provider=unavailable_monitor(),clock=lambda:AT)
        try:
            assert restored.portfolio(key)['status']=='PAUSED' and restored.audit()['ok']
            checks.append(dict(name='restart_paused',passed=True,evidence='A running controlled portfolio restarted with new entries paused'))
            assert not restored.provider.status()['configured']
            restored.tick();assert restored.last_error is None
            checks.append(dict(name='ai_outage_independence',passed=True,evidence='Deterministic supervision ran without any model credentials or request'))
        finally:restored.close()
        checks.extend(derivative_drills(root))
        backup=root/'owner-forward-backup.sqlite3'
        with owner.lock:
            saved=sqlite3.connect(backup)
            try:
                owner.db.backup(saved)
                assert saved.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                source_rows=owner.db.execute('SELECT count(*) FROM events').fetchone()[0]
                assert saved.execute('SELECT count(*) FROM events').fetchone()[0]==source_rows
                row=saved.execute('SELECT document,state FROM portfolios WHERE id=?',(identifier,)).fetchone()
                assert digest(dict(document=json.loads(row[0]),state=json.loads(row[1])))==before
            finally:saved.close()
        checks.append(dict(name='isolated_restore',passed=True,evidence='Online SQLite backup restored with integrity, event count and original checkpoint/hash preserved'))
    after_doc,after_state=owner._load(identifier)
    assert digest(dict(document=after_doc,state=after_state))==before
    return dict(passed=all(c['passed'] for c in checks),checks=checks,owner_account_unchanged=True,controlled_faults=True,real_provider_faults_injected=False,
        limitations=['Controlled fault coverage is not proof of all future recovery cases','Owner database was read/online-backed-up, never fault-injected','No broker or model credentials were used in fault fixtures'])



def derivative_drills(root):
    from .service import ForwardService
    class DerivativeKite(DrillKite):
        @staticmethod
        def underlying_key(row):return 'NSE:DRILL'
        def master(self):
            return super().master()+[dict(exchange='NFO',tradingsymbol=symbol,instrument_type='CE',segment='NFO-OPT',lot_size='10',tick_size='0.05',instrument_token=str(token),expiry='2026-11-26',strike=str(strike),name='DRILL') for symbol,token,strike in [('DHEDGE',514,110),('DSHORT',770,100)]]
    body=protocol().model_dump(mode='json');body.update(request_id='isolated-option-drill',bindings={'DRILL':'NSE:DRILL','HEDGE':'NFO:DHEDGE','SHORT':'NFO:DSHORT'})
    body['strategies'][0].update(lots=2,legs=[dict(instrument_id='HEDGE',ratio=1),dict(instrument_id='SHORT',ratio=-1)],exit_dte_days=0)
    body['supervision']['max_spread_bps']=1000
    service=ForwardService(root/'options',DerivativeKite(),DrillHub(),DrillLab(),DrillConfig(),provider=unavailable_monitor(),clock=lambda:AT)
    try:
        key=service.create(Portfolio.model_validate(body))['id'];doc,state=service._load(key);runtime=ForwardReplay(doc,state);runtime.state['forward']['paused']=False
        quotes={'DRILL':quote(AT,1000),'HEDGE':quote(AT,20,2),'SHORT':quote(AT,10,6)}
        runtime.process(quotes,AT,[]);assert runtime.enter(runtime.specs['drill'])
        at=AT+timedelta(seconds=1)
        runtime.process({key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in quotes.items()},at,[])
        held=lambda identifier:sum(lot['quantity'] for _,asset,lot in runtime.lots() if asset==identifier)
        assert held('HEDGE')==20 and held('SHORT')==-10
        runtime.pause('Controlled options exit');runtime.close_book('drill','Controlled partial-leg unwind')
        at+=timedelta(seconds=1);updates={key:dict(value,exchange_time=iso(at),received_at=iso(at)) for key,value in quotes.items()};updates['SHORT']['ask_quantity']=0
        runtime.process(updates,at,[])
        assert held('HEDGE')>=abs(held('SHORT')) and held('SHORT')==-10
        checks=[dict(name='partial_option_legs',passed=True,evidence='Thin short-leg buyback could not remove the filled hedge required for remaining short exposure')]
        cash=runtime.state['cash'];expiry=datetime.fromisoformat(runtime.instruments['SHORT']['expiry'])
        runtime.process({},expiry+timedelta(seconds=1),[])
        assert runtime.state['obligations'] and runtime.state['positions'] and runtime.state['halt']=='Unresolved expiry obligation'
        assert runtime.state['cash']==cash==runtime.state['ledger_total']
        checks.append(dict(name='missing_official_settlement',passed=True,evidence='Expired unresolved legs stayed visible without invented settlement, delivery or cash flow'))
        return checks
    finally:service.close()
