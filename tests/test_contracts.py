"""MVP-1 boundaries: explicit intent, bounded payoff, data evidence and isolated storage."""
import copy
import csv
import io
import json
import sqlite3
from types import SimpleNamespace
from datetime import datetime
import threading

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.contracts import Definition, ContractBook, assess
from app.api import create_app
from app.research.catalog import Catalog
from app.research.schemas import ImportData

HEADERS = {"X-Artha-Client": "paper-workspace"}


def definition():
    return {
        "schema_version": "mvp1/1.0",
        "name": "Independent call spread example",
        "customer": {"team_role": "OPTIONS_RESEARCH", "research_question": "Does the specified spread survive realistic execution costs?"},
        "strategy": {
            "underlying": "NIFTY", "reference_id": "NIFTY", "structure": "CALL_VERTICAL",
            "lots": 1, "lot_size": 65, "tick_size_paise": 5,
            "terms_effective_at": "2026-11-01T00:00:00+05:30",
            "legs": [
                {"instrument_id": "C25000", "kind": "CE", "side": "BUY", "strike_paise": 2500000, "premium_paise": 10000, "expiry": "2026-11-12T15:30:00+05:30"},
                {"instrument_id": "C25100", "kind": "CE", "side": "SELL", "strike_paise": 2510000, "premium_paise": 6000, "expiry": "2026-11-12T15:30:00+05:30"},
            ],
        },
        "rules": {"entry_at": "2026-11-06T09:30:00+05:30", "exit_at": "2026-11-06T15:00:00+05:30", "selection": "FIXED_CONTRACTS", "reentry": "NONE", "adjustments": "NONE", "allow_overnight": False, "partial_fill_policy": "HEDGES_FIRST_UNWIND_ON_FAILURE"},
        "risk": {"max_expiry_loss_paise": 300000, "estimated_costs_paise": 4000},
        "data": {"required_fidelity": "QUOTE", "max_quote_age_seconds": 30, "min_depth_lots": 1, "max_spread_bps": 500, "provider": "UNSELECTED", "intended_use": "LOCAL_RESEARCH"},
    }


@pytest.fixture
def book(tmp_path):
    catalog = Catalog(tmp_path / "research")
    db = sqlite3.connect(tmp_path / "workspace.sqlite3", check_same_thread=False)
    workspace = SimpleNamespace(db=db, lock=threading.RLock(), research=SimpleNamespace(catalog=catalog))
    result = ContractBook(workspace)
    yield result
    db.close()
    catalog.close()


def test_integer_payoff_and_exact_intent_mapping():
    report = assess(Definition.model_validate(definition()))
    assert report["definition_ready"]
    assert report["payoff"]["max_loss_paise"] == 264000
    assert report["payoff"]["max_profit_paise"] == 386000
    assert datetime.fromisoformat(report["intent"]["entry_at"]).isoformat() == "2026-11-06T04:00:00+00:00"
    assert report["intent"]["selection"] == "FIXED_CONTRACTS"
    assert report["intent"]["reentry"] == "NONE"
    assert report["live_orders_enabled"] is False
    assert report["execution_implemented"] is False
    assert not report["data_ready"]
    assert any(x["code"] == "DATASET_REQUIRED" for x in report["findings"])


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["strategy"]["legs"][1].update(side="BUY"), "STRUCTURE"),
    (lambda d: d["strategy"]["legs"][1].update(expiry="2026-11-19T15:30:00+05:30"), "EXPIRY"),
    (lambda d: d["strategy"]["legs"][1].update(instrument_id="C25000"), "INSTRUMENT_ID"),
    (lambda d: d["risk"].update(max_expiry_loss_paise=100), "LOSS_BUDGET"),
    (lambda d: d["strategy"].update(terms_effective_at="2026-11-07T00:00:00+05:30"), "TERMS_CUTOFF"),
    (lambda d: d["rules"].update(exit_at="2026-11-07T15:00:00+05:30"), "SAME_SESSION"),
    (lambda d: d["rules"].update(entry_at="2026-11-06T08:00:00+05:30"), "SESSION"),
    (lambda d: d["rules"].update(exit_at="2026-11-06T09:00:00+05:30"), "TIME_ORDER"),
    (lambda d: d["strategy"]["legs"][0].update(premium_paise=10001), "TICK"),
])
def test_incompatible_definitions_remain_explicit(mutate, code):
    d = definition()
    mutate(d)
    result = assess(Definition.model_validate(d))
    assert not result["definition_ready"]
    assert any(x["code"] == code and x["status"] == "BLOCKED" for x in result["findings"])


@pytest.mark.parametrize("change", [
    lambda d: d["strategy"].update(lots=True),
    lambda d: d["strategy"]["legs"][0].update(premium_paise=float("nan")),
    lambda d: d["rules"].update(entry_at="2026-11-06T09:30:00"),
    lambda d: d["rules"].update(adjustments="ROLL"),
    lambda d: d.update(python="print('unsupported')"),
    lambda d: d["strategy"].update(underlying="BANKNIFTY"),
])
def test_unsupported_or_ambiguous_input_is_rejected(change):
    d = definition()
    change(d)
    with pytest.raises(ValidationError):
        Definition.model_validate(d)


def test_customer_review_requires_real_reference():
    d = definition()
    d["customer"].update(validation_state="REVIEWED")
    report = assess(Definition.model_validate(d))
    assert not report["customer_validated"]
    assert any(x["code"] == "CUSTOMER_EVIDENCE" for x in report["findings"])
    d["customer"].update(evidence_reference="Local customer interview note", budget_owner="Research lead")
    assert assess(Definition.model_validate(d))["customer_validated"]


def import_quotes(catalog, d, frequency="QUOTE", lot_size=65):
    legs = d["strategy"]["legs"]
    instruments = [dict(id="NIFTY", symbol="NIFTY 50", kind="INDEX", underlying="NIFTY")]
    instruments += [dict(id=l["instrument_id"], symbol=l["instrument_id"], kind="OPT", underlying="NIFTY", exchange="NFO", lot_size=lot_size, strike=l["strike_paise"]/100, option_type=l["kind"], expiry=l["expiry"]) for l in legs]
    rows = []
    for at in (d["rules"]["entry_at"], d["rules"]["exit_at"]):
        for i in instruments:
            price = 25000 if i["kind"] == "INDEX" else 100 if i["id"] == "C25000" else 60
            rows.append(dict(instrument_id=i["id"], timestamp=at, open=price, high=price, low=price, close=price, volume=1000, bid=price-.05, ask=price+.05, bid_quantity=650, ask_quantity=650))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return catalog.import_data(ImportData.model_validate(dict(manifest=dict(name="Controlled options data", source="MVP1_TEST", source_kind="SYNTHETIC", frequency=frequency, instruments=instruments), content=buf.getvalue())))


def test_actual_dataset_identity_liquidity_and_provenance(book):
    d = definition()
    dataset = import_quotes(book.catalog, d)
    report = book.check(d, dataset["id"])
    assert report["definition_ready"]
    assert report["dataset_snapshot"]["parquet_hash"] == dataset["parquet_hash"]
    assert any(x["code"] == "QUOTE_COVERAGE" and x["status"] == "PASS" for x in report["findings"])
    assert not report["commercial_data_ready"]
    assert not report["rights_independently_verified"]
    assert any(x["code"] == "SYNTHETIC_DATA" for x in report["findings"])


def test_declared_lot_size_cannot_override_dataset(book):
    d = definition()
    dataset = import_quotes(book.catalog, d, lot_size=50)
    result = book.check(d, dataset["id"])
    assert not result["data_ready"]
    assert any(x["code"] == "DATA_CONTRACT_TERMS" and x["status"] == "BLOCKED" for x in result["findings"])


def test_minute_data_cannot_claim_historical_quotes(book):
    d = definition()
    dataset = import_quotes(book.catalog, d, frequency="MINUTE")
    result = book.check(d, dataset["id"])
    assert any(x["code"] == "DATA_FIDELITY" and x["status"] == "BLOCKED" for x in result["findings"])


def test_immutable_revisions_request_binding_and_restart(book):
    d = definition()
    one = book.save(dict(request_id="request-0001", definition=d))
    assert book.save(dict(request_id="request-0001", definition=d)) == one
    d["name"] = "Revised question"
    with pytest.raises(ValueError, match="different"):
        book.save(dict(request_id="request-0001", definition=d))
    two = book.save(dict(request_id="request-0002", definition=d, parent_id=one["id"]))
    assert two["id"] != one["id"] and two["core"]["parent_id"] == one["id"]
    assert book.get(one["id"])["core"]["definition"]["name"] != d["name"]
    assert ContractBook(book.workspace).get(two["id"]) == two
    with book.workspace.db:
        book.workspace.db.execute("UPDATE mvp_contracts SET document=replace(document, 'Revised question', 'Tampered question') WHERE id=?", (two["id"],))
    with pytest.raises(ValueError, match="integrity"):
        book.get(two["id"])


def test_api_sidebar_roundtrip_and_account_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTHA_LIVE_TRANSPORT_ENABLED", raising=False)
    app = create_app(tmp_path / "paper.sqlite3", background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        before = client.get("/api/state").json()
        payload = {"definition": definition()}
        assert client.post("/api/contracts/check", json=payload).status_code == 403
        checked = client.post("/api/contracts/check", json=payload, headers=HEADERS)
        assert checked.status_code == 200 and checked.json()["definition_ready"]
        saved = client.post("/api/contracts", json={**payload, "request_id": "api-request-1"}, headers=HEADERS)
        assert saved.status_code == 200
        identifier = saved.json()["id"]
        assert client.get("/api/contracts/" + identifier).json() == saved.json()
        assert client.get("/api/contracts/" + identifier + "/export").json()["id"] == identifier
        assert len(client.get("/api/contracts").json()) == 1
        invalid = copy.deepcopy(payload)
        invalid["definition"]["rules"]["adjustments"] = "ROLL"
        response = client.post("/api/contracts/check", json=invalid, headers=HEADERS)
        assert response.status_code == 422
        assert "Strategy Contracts" in client.get("/").text
        after = client.get("/api/state").json()
        assert before["account"] == after["account"] and before["orders"] == after["orders"]
        assert not client.get("/api/pilot/status").json()["armed"]


def test_unequal_width_condor_uses_worst_tail():
    from app.contracts import example
    d = example("IRON_CONDOR")
    d["strategy"]["legs"][3].update(strike_paise=2525000, premium_paise=1500)
    d["risk"]["max_expiry_loss_paise"] = 800000
    result = assess(d)
    assert result["definition_ready"]
    assert result["payoff"]["max_loss_paise"] == 686500
    assert result["payoff"]["max_profit_paise"] == 288500


def test_quoted_size_uses_minimum_lots_not_a_multiplier(book):
    d = definition()
    d["strategy"]["lots"] = 3
    d["risk"]["max_expiry_loss_paise"] = 1000000
    d["data"]["min_depth_lots"] = 4
    dataset = import_quotes(book.catalog, d)
    result = book.check(d, dataset["id"])
    assert any(f["code"] == "QUOTE_LIQUIDITY" and f["status"] == "PASS" for f in result["findings"])


def test_backup_verifies_saved_definitions_and_detects_tampering(book):
    from tools.forward_ops import check_database
    d = definition()
    saved = book.save(dict(request_id="backup-request-1", definition=d))
    path = __import__("pathlib").Path(book.workspace.db.execute("PRAGMA database_list").fetchone()[2])
    assert check_database(path)["strategy_contracts_verified"] == 1
    with book.workspace.db:
        book.workspace.db.execute("UPDATE mvp_contracts SET document=replace(document,'Independent call spread example','Tampered name')")
    with pytest.raises(ValueError, match="integrity"):
        check_database(path)


def test_missing_parent_and_changed_request_do_not_create_partial_records(book):
    d = definition()
    with pytest.raises(ValueError, match="not found"):
        book.save(dict(request_id="missing-parent-1", definition=d, parent_id="a"*64))
    assert book.list() == []
    assert book.workspace.db.execute("SELECT count(*) FROM mvp_contract_requests").fetchone()[0] == 0


def test_dataset_integrity_failure_is_not_downgraded_to_compatible(book):
    d = definition()
    dataset = import_quotes(book.catalog, d)
    path = book.catalog.root / "datasets" / dataset["id"] / "observations.parquet"
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        book.check(d, dataset["id"])


def test_api_errors_do_not_echo_submitted_values(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTHA_LIVE_TRANSPORT_ENABLED", raising=False)
    app = create_app(tmp_path / "paper.sqlite3", background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        d = definition()
        d["rules"]["adjustments"] = "DO_NOT_ECHO_SUBMITTED_VALUE"
        response = client.post("/api/contracts/check", json=dict(definition=d), headers=HEADERS)
        assert response.status_code == 422
        assert "DO_NOT_ECHO_SUBMITTED_VALUE" not in response.text
        assert client.get("/api/contracts/not-an-identifier").status_code == 422
        oversized = client.post("/api/contracts/check", content=" " * 17000, headers={**HEADERS, "Content-Type": "application/json"})
        assert oversized.status_code == 413


def test_reused_request_cannot_return_a_different_saved_contract(book):
    first = book.save(dict(request_id="bound-request-1", definition=definition()))
    d = definition()
    d["name"] = "Another definition"
    second = book.save(dict(request_id="bound-request-2", definition=d))
    with book.workspace.db:
        book.workspace.db.execute("UPDATE mvp_contract_requests SET contract_id=? WHERE request_id=?", (second["id"], "bound-request-1"))
    with pytest.raises(ValueError, match="integrity"):
        book.save(dict(request_id="bound-request-1", definition=definition()))


def test_effective_terms_and_missing_boundary_data_are_checked(book):
    d = definition()
    body = import_quotes(SimpleNamespace(import_data=lambda value: value), d)
    manifest = body.manifest.model_dump(mode="json")
    for instrument in manifest["instruments"]:
        if instrument["kind"] == "OPT":
            instrument["terms"] = [dict(effective_at="2026-11-01T00:00:00+05:30", lot_size=50, tick_size=.05)]
    dataset = book.catalog.import_data(ImportData.model_validate(dict(manifest=manifest, content=body.content)))
    result = book.check(d, dataset["id"])
    assert any(f["code"] == "DATA_CONTRACT_TERMS" and f["status"] == "BLOCKED" for f in result["findings"])
    rows = list(csv.DictReader(io.StringIO(body.content)))
    rows = [r for r in rows if not (r["instrument_id"] == "C25100" and r["timestamp"] == d["rules"]["exit_at"])]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    dataset = book.catalog.import_data(ImportData.model_validate(dict(manifest=body.manifest.model_dump(mode="json"), content=buffer.getvalue())))
    result = book.check(d, dataset["id"])
    assert any(f["code"] == "QUOTE_COVERAGE" and f["status"] == "BLOCKED" for f in result["findings"])


def test_hedging_requirements_and_source_assertions_never_enable_execution():
    d = definition()
    d["data"].update(provider="KITE_PERSONAL", intended_use="CLIENT_RESEARCH", rights_status="OWNER_ASSERTED", rights_reference="Owner declaration")
    result = assess(d)
    assert any(f["code"] == "KITE_DISTRIBUTION" and f["status"] == "BLOCKED" for f in result["findings"])
    assert not result["commercial_data_ready"] and not result["execution_implemented"]
    assert not result["live_orders_enabled"] and not result["backtest_submitted"] and not result["forward_created"]
