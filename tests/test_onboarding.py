"""Credential and onboarding acceptance uses only controlled keys and broker responses."""
from datetime import datetime,timedelta
import json,os,stat
from urllib.parse import parse_qs,urlsplit
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.configuration import PrivateConfig
from app.engine import IST

HEADERS={'X-Artha-Client':'paper-workspace'}
KEY='controlled_key_123'
SECRET='controlled_secret_123'

@pytest.fixture
def client(tmp_path,monkeypatch):
    for name in ('KITE_API_KEY','KITE_API_SECRET','KITE_ACCESS_TOKEN'):monkeypatch.delenv(name,raising=False)
    app=create_app(tmp_path/'paper.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as instance:yield instance


def save(client):return client.post('/api/onboarding/kite/credentials',json=dict(api_key=KEY,api_secret=SECRET),headers=HEADERS)

def session(client,monkeypatch):
    assert save(client).status_code==200
    monkeypatch.setattr(client.app.state.kite,'_request',lambda *a,**kw:dict(access_token='controlled_session'))
    login=client.get('/api/kite/login?return_to=setup',follow_redirects=False)
    state=parse_qs(parse_qs(urlsplit(login.headers['location']).query)['redirect_params'][0])['state'][0]
    result=client.get('/api/kite/callback',params=dict(state=state,request_token='controlled_request'),follow_redirects=False)
    assert result.status_code==303 and result.headers['location']=='/#setup'


def test_private_save_is_atomic_owner_only_and_preserves_ai_config(tmp_path):
    path=tmp_path/'.env.local';path.write_text("ARTHA_AI_API_KEY='controlled-ai-key'\nARTHA_AI_MODEL=controlled-model\nKITE_ACCESS_TOKEN=old-session\n");path.chmod(0o600)
    config=PrivateConfig(path);config.save_kite(KEY,SECRET);values=config.read()
    assert values['ARTHA_AI_API_KEY']=='controlled-ai-key' and values['ARTHA_AI_MODEL']=='controlled-model'
    assert values['KITE_ACCESS_TOKEN']=='' and values['KITE_API_SECRET']==SECRET
    assert stat.S_IMODE(path.stat().st_mode)==0o600 and not list(tmp_path.glob('.artha-config-*'))


def test_config_rejects_symlinks_and_broad_permissions(tmp_path):
    real=tmp_path/'private';real.write_text('KITE_API_KEY=controlled-key\n');real.chmod(0o600)
    link=tmp_path/'.env.local';link.symlink_to(real)
    with pytest.raises(ValueError,match='regular'):PrivateConfig(link).save_kite(KEY,SECRET)
    real.chmod(0o644)
    with pytest.raises(ValueError,match='owner'):PrivateConfig(real).read()


def test_config_values_are_data_not_shell_commands(tmp_path):
    path=tmp_path/'.env.local';path.write_text("ARTHA_AI_API_KEY='controlled-$(must-not-execute)'\n");path.chmod(0o600)
    config=PrivateConfig(path);config.save_kite(KEY,SECRET)
    assert config.read()['ARTHA_AI_API_KEY']=='controlled-$(must-not-execute)'


def test_save_updates_runtime_without_echoing_credentials(client):
    response=save(client);assert response.status_code==200
    assert client.app.state.kite.key==KEY and client.app.state.kite.secret==SECRET
    assert not client.app.state.kite.token
    for payload in (response.text,client.get('/api/onboarding/status').text,client.get('/api/state').text):
        assert KEY not in payload and SECRET not in payload
    status=client.get('/api/onboarding/status').json()
    assert status['session']['login_available'] and not status['session']['authenticated']
    assert status['callback_url']=='http://127.0.0.1/api/kite/callback'


@pytest.mark.parametrize('body',[dict(api_key='bad',api_secret=SECRET),dict(api_key=KEY,api_secret='bad secret '+SECRET),dict(api_key=KEY,api_secret=SECRET,extra_secret=SECRET)])
def test_validation_errors_never_echo_secret_values(client,body):
    response=client.post('/api/onboarding/kite/credentials',json=body,headers=HEADERS)
    assert response.status_code==422 and SECRET not in response.text and KEY not in response.text


def test_credentials_require_same_origin_mutation_boundary(client):
    assert client.post('/api/onboarding/kite/credentials',json=dict(api_key=KEY,api_secret=SECRET)).status_code==403
    assert client.post('/api/onboarding/kite/credentials',json=dict(api_key=KEY,api_secret=SECRET),headers={**HEADERS,'Origin':'https://untrusted.example'}).status_code==403
    assert not client.app.state.onboarding.config.path.exists()


def test_oauth_returns_to_setup_without_request_token_in_url(client,monkeypatch):
    session(client,monkeypatch)
    assert client.get('/api/onboarding/status').json()['session']['authenticated']
    assert client.get('/api/kite/login?return_to=https://untrusted.example',follow_redirects=False).status_code==422


def test_replacing_credentials_invalidates_pending_login_state(client):
    save(client);login=client.get('/api/kite/login?return_to=setup',follow_redirects=False)
    state=parse_qs(parse_qs(urlsplit(login.headers['location']).query)['redirect_params'][0])['state'][0]
    save(client)
    assert client.get('/api/kite/callback',params=dict(state=state,request_token='controlled_request'),follow_redirects=False).status_code==400


def test_quote_check_can_verify_access_without_claiming_fresh_prices(client,monkeypatch):
    session(client,monkeypatch)
    old=(datetime.now(IST)-timedelta(hours=2)).isoformat()
    monkeypatch.setattr(client.app.state.kite,'_quotes',lambda keys:{'NSE:RELIANCE':dict(last_price=100,timestamp=old)})
    before=client.get('/api/state').json()
    result=client.post('/api/onboarding/kite/check',json=dict(kind='quotes'),headers=HEADERS).json()
    assert result['access_verified'] and result['fresh'] is False
    status=client.get('/api/onboarding/status').json()
    assert status['can_connect'] and status['market']['fresh_symbols']==0 and not status['paper_ready']
    assert client.get('/api/state').json()['account']==before['account']
    assert client.get('/api/state').json()['orders']==before['orders']


def test_connect_requires_verified_data_not_just_a_token(client,monkeypatch):
    session(client,monkeypatch)
    assert client.post('/api/onboarding/kite/connect',json={},headers=HEADERS).status_code==400
    monkeypatch.setattr(client.app.state.kite,'_quotes',lambda keys:{'NSE:RELIANCE':dict(last_price=100,timestamp=datetime.now(IST).isoformat())})
    assert client.post('/api/onboarding/kite/check',json=dict(kind='quotes'),headers=HEADERS).json()['access_verified']
    assert client.post('/api/onboarding/kite/connect',json={},headers=HEADERS).json()['started']
    assert client.app.state.market_hub.store.enabled()
    assert client.get('/api/state').json()['source']=='DEMO'


def test_history_check_is_read_only_and_separate_from_quote_access(client,monkeypatch):
    session(client,monkeypatch)
    monkeypatch.setattr(client.app.state.kite,'master',lambda:[dict(exchange='NSE',tradingsymbol='RELIANCE',instrument_type='EQ',instrument_token='257')])
    calls=[]
    def request(method,path,**kwargs):calls.append((method,path));return dict(candles=[[(datetime.now(IST)-timedelta(days=1)).isoformat(),100,101,99,100,1000]])
    monkeypatch.setattr(client.app.state.kite,'_request',request)
    result=client.post('/api/onboarding/kite/check',json=dict(kind='history'),headers=HEADERS).json()
    assert result['status']=='PASSED' and result['candles']==1
    assert calls==[('GET','/instruments/historical/257/day')]
    assert not client.get('/api/onboarding/status').json()['can_connect']
    assert not client.get('/api/research/datasets').json()


def test_provider_errors_are_safe_and_not_fabricated_success(client,monkeypatch):
    session(client,monkeypatch)
    def fail(keys):raise ValueError('credential-bearing diagnostic '+SECRET)
    monkeypatch.setattr(client.app.state.kite,'_quotes',fail)
    response=client.post('/api/onboarding/kite/check',json=dict(kind='quotes'),headers=HEADERS)
    assert response.json()['status']=='FAILED' and not response.json()['access_verified'] and SECRET not in response.text


def test_session_rotation_invalidates_old_data_verification(client,monkeypatch):
    session(client,monkeypatch)
    monkeypatch.setattr(client.app.state.kite,'_quotes',lambda keys:{'NSE:RELIANCE':dict(last_price=100,timestamp=datetime.now(IST).isoformat())})
    client.post('/api/onboarding/kite/check',json=dict(kind='quotes'),headers=HEADERS)
    client.app.state.kite.token='new-controlled-session'
    assert not client.get('/api/onboarding/status').json()['can_connect']


def test_existing_active_paper_positions_protect_credential_replacement(client):
    engine=client.app.state.engine
    engine.submit(dict(symbol='RELIANCE',side='BUY',quantity=1,stop_loss=1470))
    assert engine.state['positions'];engine.state['source']='KITE'
    assert save(client).status_code==400 and not client.app.state.onboarding.config.path.exists()
    engine.state['source']='DEMO'


def test_saved_configuration_overrides_stale_environment_on_recreation(tmp_path,monkeypatch):
    monkeypatch.setenv('KITE_API_KEY','old-environment-key');monkeypatch.setenv('KITE_ACCESS_TOKEN','old-environment-session')
    app=create_app(tmp_path/'account.sqlite3',background=False)
    with TestClient(app,base_url='http://127.0.0.1') as first:assert save(first).status_code==200
    reopened=create_app(tmp_path/'account.sqlite3',background=False)
    with TestClient(reopened,base_url='http://127.0.0.1') as second:
        assert second.app.state.kite.key==KEY and second.app.state.kite.secret==SECRET and not second.app.state.kite.token


def test_failed_broker_login_returns_to_setup_with_safe_error(client,monkeypatch):
    save(client)
    def fail(*args,**kwargs):raise ValueError('private provider diagnostic '+SECRET)
    monkeypatch.setattr(client.app.state.kite,'_request',fail)
    login=client.get('/api/kite/login?return_to=setup',follow_redirects=False)
    state=parse_qs(parse_qs(urlsplit(login.headers['location']).query)['redirect_params'][0])['state'][0]
    response=client.get('/api/kite/callback',params=dict(state=state,request_token='controlled_request'),follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/#setup'
    status=client.get('/api/onboarding/status');assert status.json()['authentication_error'] and SECRET not in status.text
