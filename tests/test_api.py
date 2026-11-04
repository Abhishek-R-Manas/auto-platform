from fastapi.testclient import TestClient
import pytest

from app.api import create_app
from app.market import KiteData

HEADERS={"X-Artha-Client":"paper-workspace"}


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/"api.sqlite3",background=False)
    with TestClient(app,base_url="http://127.0.0.1") as instance:
        yield instance


def test_dashboard_and_state_are_real_backend_responses(client):
    page=client.get("/")
    assert page.status_code==200
    assert "Paper trading workspace" in page.text
    state=client.get("/api/state").json()
    assert state["mode"]=="PAPER"
    assert state["account"]["equity"]==1_000_000
    assert not state["live_trading_available"]
    assert len(state["market"])==6
    assert state["reconcile"]["ok"]


def test_request_boundary_rejects_cross_site_mutations(client):
    assert client.post("/api/controls/pause",json={"paused":True}).status_code==403
    response=client.post("/api/controls/pause",json={"paused":True},headers={**HEADERS,"Origin":"https://untrusted.example"})
    assert response.status_code==403


def test_schema_does_not_accept_hidden_live_mode_or_extra_parameters(client):
    body=dict(symbol="RELIANCE",quantity=10,side="BUY",stop_loss=1470,mode="LIVE")
    assert client.post("/api/orders",json=body,headers=HEADERS).status_code==422
    assert client.post("/api/live/orders",json={},headers=HEADERS).status_code==404


def test_short_quantity_is_not_silently_coerced(client):
    body=dict(symbol="RELIANCE",quantity="10",side="BUY",stop_loss=1470)
    assert client.post("/api/orders",json=body,headers=HEADERS).status_code==422


def test_preview_and_submission_use_same_risk_checks(client):
    body=dict(symbol="RELIANCE",quantity=100000,side="BUY",stop_loss=1470)
    preview=client.post("/api/orders/preview",json=body,headers=HEADERS).json()
    order=client.post("/api/orders",json=body,headers=HEADERS).json()
    assert not preview["allowed"]
    assert order["status"]=="REJECTED"
    assert order["reason"]==preview["reason"]


def test_pause_and_close_endpoints(client):
    body=dict(symbol="RELIANCE",quantity=10,side="BUY",stop_loss=1470,client_id="api-buy")
    assert client.post("/api/orders",json=body,headers=HEADERS).json()["status"]=="FILLED"
    response=client.post("/api/controls/flatten",json={},headers=HEADERS)
    assert response.status_code==200
    state=client.get("/api/state").json()
    assert state["paused"]
    assert not state["positions"]
    assert state["reconcile"]["ok"]


def test_csv_export_neutralises_spreadsheet_formulas(client):
    client.post("/api/journal/notes",json={"text":"=HYPERLINK(\"https://example.com\")"},headers=HEADERS)
    response=client.get("/api/journal/export")
    assert response.status_code==200
    assert "'=HYPERLINK" in response.text


def test_invalid_kite_authentication_state_is_rejected(client):
    response=client.get("/api/kite/callback?state=forged&request_token=fake")
    assert response.status_code==400


def test_kite_connection_failure_preserves_demo_session(client,monkeypatch):
    def failing():
        raise ValueError("No authenticated data session")
    monkeypatch.setattr(client.app.state.kite,"load",failing)
    assert client.post("/api/market/source",json={"source":"KITE"},headers=HEADERS).status_code==400
    assert client.get("/api/state").json()["source"]=="DEMO"


def test_market_adapter_has_no_live_order_route():
    with pytest.raises(ValueError,match="allowlist"):
        KiteData()._request("POST","/orders/regular",data={})


def test_security_headers_and_untrusted_host(client):
    response=client.get("/")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"]=="nosniff"
    assert client.get("/api/state",headers={"host":"untrusted.example"}).status_code==400


def test_strategy_and_capital_workflows(client):
    result=client.post("/api/account/capital",json={"amount":100_000},headers=HEADERS)
    assert result.json()["capital"]==100_000
    response=client.post("/api/strategy",json={"enabled":True,"symbol":"RELIANCE","fast":3,"slow":8,"stop_pct":2},headers=HEADERS)
    assert response.status_code==200
    assert response.json()["enabled"]
    assert client.post("/api/controls/step",json={"count":20},headers=HEADERS).status_code==200
    assert client.get("/api/state").json()["reconcile"]["ok"]


def test_decision_api_links_order_evidence_and_validates_pagination(client):
    body=dict(symbol="RELIANCE",quantity=10,side="BUY",stop_loss=1470,client_id="trace-api")
    order=client.post("/api/orders",json=body,headers=HEADERS).json()
    rows=client.get("/api/decisions?limit=1").json()
    assert len(rows)==1 and rows[0]["action"]=="FILL"
    evidence=client.get("/api/decisions/"+order["decision_id"]).json()
    assert evidence["order_id"]==order["id"] and evidence["checks"]
    assert client.get("/api/decisions?limit=101").status_code==422
    assert client.get("/api/decisions?before=0").status_code==422
    assert client.get("/api/decisions/missing").status_code==404
    sized=client.post("/api/orders/size",json={**body,"symbol":"INFY","stop_loss":1570},headers=HEADERS).json()
    assert sized["decision_id"] and sized["explanation"]
    assert client.get("/api/decisions/"+sized["decision_id"]).json()["action"]=="SIZE"
