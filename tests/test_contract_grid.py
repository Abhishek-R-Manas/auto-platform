"""Contract stepping metadata is exact, scoped and independent of broker quotes."""
import copy
from fastapi.testclient import TestClient
from app.api import create_app
from test_workbench import workspace


def test_contract_grid_uses_each_instrument_tick_and_does_not_request_quotes(workspace):
    service,kite=workspace;expiry=min(r['expiry'] for r in kite.rows if r['exchange']=='NFO')
    calls=[r for r in kite.rows if r['expiry']==expiry and r['instrument_type']=='CE'];calls[0]['tick_size']='.1';calls[1]['tick_size']='.25';before=copy.deepcopy(kite.rows);quote_calls=len(kite.calls)
    result=service.contract_grid('NIFTY',expiry,'CE');assert [c['tick_size'] for c in result['contracts'][:2]]==[.1,.25]
    assert all(c['kind']=='CE' and c['expiry'].startswith(expiry) for c in result['contracts'])
    assert len(kite.calls)==quote_calls and kite.rows==before and not result['execution_authority']


def test_contract_grid_keeps_irregular_gaps_and_excludes_invalid_ticks(workspace):
    service,kite=workspace;expiry=min(r['expiry'] for r in kite.rows if r['exchange']=='NFO')
    kite.rows[:]=[r for r in kite.rows if not (r['expiry']==expiry and r['instrument_type']=='CE' and float(r['strike'])==25050)]
    next(r for r in kite.rows if r['expiry']==expiry and r['instrument_type']=='CE' and float(r['strike'])==25150)['tick_size']='nan'
    strikes=[c['strike'] for c in service.contract_grid('NIFTY',expiry,'CE')['contracts']]
    assert 25050 not in strikes and 25150 not in strikes and strikes[strikes.index(25000)+1]==25100
    assert 25050 in [c['strike'] for c in service.contract_grid('NIFTY',expiry,'PE')['contracts']]
    assert service.contract_grid('UNKNOWN',expiry,'CE')['contracts']==[] and service.contract_grid('NIFTY','2000-01-01','CE')['contracts']==[]


def test_contract_grid_api_validates_scope_without_account_changes(tmp_path):
    with TestClient(create_app(tmp_path/'paper.sqlite3',background=False),base_url='http://127.0.0.1') as client:
        before=client.get('/api/state').json()
        assert client.get('/api/workspace/contract-grid',params={'underlying':'NIFTY','expiry':'2026-11-12','kind':'FUT'}).status_code==422
        assert client.get('/api/workspace/contract-grid',params={'underlying':'NIFTY','expiry':'bad','kind':'CE'}).status_code==422
        after=client.get('/api/state').json();assert before['account']==after['account'] and before['orders']==after['orders']
