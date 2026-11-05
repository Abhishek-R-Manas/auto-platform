"""Setup verification can persist; execution freshness and real authority cannot be inferred."""
import json
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.pilot.service import Pilot,Arm
from test_pilot_charts import AT,Kite,Broker,Forward,intention
from test_manual_trading import authority

@pytest.fixture
def setup(tmp_path):
    clock=[AT];b=Broker();k=Kite();s=Pilot(tmp_path,k,Forward(),b,lambda:clock[0]);s.reconcile()
    yield s,b,k,clock
    s.close()

def payload(**changes):return authority(**changes).model_dump(mode='json')
def checks(report):return {c['id']:c for c in report['checks']}

def test_valid_preflight_has_no_authority_or_journal_side_effect(setup):
    s,b,k,clock=setup;before=s.audit();report=s.review_manual(payload())
    assert report['can_save'] and report['limits_valid'] and report['permissions_valid'] and report['risk_passed']
    assert report['available_cash_paise']==1000000 and report['gross_daily_loss_paise']==0
    assert s.audit()==before and s.status()['mandate'] is None and not s.status()['armed'] and not b.sent

def test_verified_setup_stays_checked_but_cannot_arm_with_stale_data(setup):
    s,b,k,clock=setup;s.configure_manual(authority());clock[0]+=timedelta(seconds=16)
    r=s.readiness();g={g['id']:g['ok'] for g in r['gates']}
    assert r['setup_verified'] and all(g[key] for key in ('reconciliation','broker_permissions','funds','limits','permissions','risk'))
    assert not r['execution_fresh'] and not r['can_arm']
    report=s.review_manual(payload());assert not report['can_save'] and checks(report)['account']['status']=='PENDING'
    with pytest.raises(ValueError,match='Reconcile'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    s.reconcile();assert s.readiness()['execution_fresh'];s.arm(Arm(confirmation='ARM REAL MONEY AB1234'));assert s.status()['armed']

def test_session_rotation_invalidates_verified_progress(setup):
    s,b,k,clock=setup;k.token='new-session';assert not s.readiness()['setup_verified']
    k.token='';assert not s.readiness()['setup_verified'] and not s.readiness()['execution_fresh']

def test_next_date_requires_new_sync_even_with_same_token(setup):
    s,b,k,clock=setup;clock[0]+=timedelta(days=1);assert not s.readiness()['setup_verified']

def test_failed_sync_cannot_leave_old_checks_green(setup):
    s,b,k,clock=setup;b.data['funds']['net']='not-a-balance'
    with pytest.raises(ValueError):s.reconcile()
    assert not s.readiness()['setup_verified']

@pytest.mark.parametrize('field',['static_ip_confirmed','delivery_authorized','protective_orders_authorized'])
def test_permission_is_pending_until_explicitly_confirmed(setup,field):
    s,b,*_=setup;report=s.review_manual(payload(**{field:False}))
    assert report['limits_valid'] and not report['permissions_valid'] and not report['can_save']
    assert checks(report)[field]['status']=='PENDING' and checks(report)[field]['step']==3
    assert not b.sent

def test_read_api_success_is_not_static_ip_proof(setup):
    s,*_=setup;report=s.review_manual(payload(static_ip_confirmed=False));check=checks(report)['static_ip_confirmed']
    assert check['status']=='PENDING' and 'read APIs do not verify' in check['detail']

def test_allocation_above_cash_is_explained_and_cannot_be_saved(setup):
    s,b,*_=setup;body=authority(capital=11000);report=s.review_manual(body.model_dump(mode='json'))
    assert report['limits_valid'] and checks(report)['funding']['status']=='FAIL' and not report['can_save']
    with pytest.raises(ValueError,match='Cash covers'):s.configure_manual(body)
    assert s.status()['mandate'] is None and not b.sent

def test_daily_loss_failure_is_reported_before_saving(setup):
    s,b,*_=setup;b.data['positions']['net']=[dict(pnl=-101)];s.reconcile();report=s.review_manual(payload())
    assert report['gross_daily_loss_paise']==10100 and checks(report)['daily_loss']['status']=='FAIL' and not report['can_save']
    with pytest.raises(ValueError,match='Daily loss threshold'):s.configure_manual(authority())
    assert s.status()['mandate'] is None

@pytest.mark.parametrize('change,field',[({'capital':0},'capital'),({'daily_loss':2001},'limits'),({'expires_at':'bad'},'expires_at'),({'symbols':['NOT-A-CONTRACT']},'symbols')])
def test_inline_errors_refer_to_fields_without_echoing_input(setup,change,field):
    s,*_=setup;values=payload();values.update(change);report=s.review_manual(values)
    assert field in report['field_errors'] and not report['can_save']
    assert 'NOT-A-CONTRACT' not in json.dumps(report) and 'bad' not in json.dumps(report)

def test_unknown_secret_field_is_not_returned(setup):
    s,*_=setup;report=s.review_manual(payload()|{'api_key':'never-echo-this-value'})
    assert not report['limits_valid'] and 'never-echo-this-value' not in json.dumps(report)

def test_no_forced_order_is_needed_to_review_without_transport(setup):
    s,b,*_=setup;b.enabled=False;report=s.review_manual(payload());assert report['can_save'] and checks(report)['transport']['status']=='FAIL'
    s.configure_manual(authority())
    with pytest.raises(ValueError,match='transport'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    assert not b.sent

def test_http_review_is_read_only_and_does_not_save_authority(tmp_path):
    app=create_app(tmp_path/'paper.sqlite3',background=False,config_path=tmp_path/'.env.fixture')
    with TestClient(app,base_url="http://127.0.0.1") as client:
        s=app.state.pilot;s.kite=Kite();s.broker=Broker();s.forward=Forward();s.clock=lambda:AT;s.reconcile();before=s.audit()
        result=client.post('/api/pilot/manual/review',json=payload(),headers={'X-Artha-Client':'paper-workspace'})
        assert result.status_code==200 and result.json()['can_save'] and s.audit()==before
        assert s.status()['mandate'] is None and not s.status()['armed'] and not s.broker.sent


@pytest.mark.parametrize('failure',['permissions','funds'])
def test_manual_activation_rechecks_account_readiness(setup,failure):
    s,b,*_=setup;s.configure_manual(authority())
    if failure=='permissions':b.data['order_types']=['LIMIT']
    else:b.data['funds']['available']['cash']=0
    s.reconcile()
    with pytest.raises(ValueError,match='readiness'):s.arm(Arm(confirmation='ARM REAL MONEY AB1234'))
    assert not s.status()['armed'] and not b.sent
