"""MVP-2 independent accounting, clock, protocol, retry and packet boundaries."""
import copy
import csv
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.contracts import ContractBook
from app.contract_research import ContractResearch, verify_packet
from app.research.catalog import Catalog
from app.research.jobs import Research, execute
from app.research.schemas import ImportData
from test_contracts import definition

HEADERS = {"X-Artha-Client": "paper-workspace"}
POLICY = dict(trade_pct=2, open_pct=5, daily_pct=2, weekly_pct=5, drawdown_pct=10,
              margin_pct=75, slippage_bps=0, overnight_stress_pct=50,
              gross_exposure_pct=1000, underlying_exposure_pct=500)


def source_body(clocks=True, delayed=False, missing_exit=False):
    d = definition()
    legs = d["strategy"]["legs"]
    instruments = [dict(id="NIFTY", symbol="NIFTY 50", kind="INDEX", underlying="NIFTY")]
    instruments += [dict(id=l["instrument_id"], symbol=l["instrument_id"], kind="OPT", underlying="NIFTY", exchange="NFO",
                         lot_size=65, tick_size=.05, strike=l["strike_paise"]/100, option_type=l["kind"], expiry=l["expiry"])
                    for l in legs]
    times = ["2026-11-06T09:30:00+05:30", "2026-11-06T09:30:01+05:30", "2026-11-06T09:30:02+05:30",
             "2026-11-06T15:00:00+05:30", "2026-11-06T15:00:01+05:30", "2026-11-06T15:00:02+05:30",
             "2026-11-06T15:00:10+05:30"]
    rows = []
    for n, at in enumerate(times):
        for i in instruments:
            if missing_exit and n >= 3 and i["id"] == "C25100":
                continue
            price = 25000 if i["kind"] == "INDEX" else (100 if n < 3 else 110) if i["id"] == "C25000" else (60 if n < 3 else 65)
            row = dict(instrument_id=i["id"], timestamp=at, close=price, bid=price-.05, ask=price+.05,
                       bid_quantity=650, ask_quantity=650, volume=1000, iv=.2)
            if clocks:
                row.update(received_at=at, available_at=at)
            if delayed and n == 1 and i["id"] == "C25000":
                row.update(close=1, bid=.95, ask=1.05, received_at="2026-11-06T15:00:20+05:30", available_at="2026-11-06T15:00:20+05:30")
            rows.append(row)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    fees = {key: 0 for key in __import__("app.charges", fromlist=["DEFAULTS"]).DEFAULTS}
    fees["source"] = "INDEPENDENT_ZERO_FEE_FIXTURE"
    manifest = dict(name="Controlled MVP-2 quote tape", source="MVP2_FIXTURE", source_kind="SYNTHETIC", frequency="QUOTE",
                    observation_clock="EXCHANGE_AND_RECEIPT" if clocks else "UNSPECIFIED",
                    instruments=instruments, fee_rules=[fees])
    return ImportData.model_validate(dict(manifest=manifest, content=buf.getvalue()))


@pytest.fixture
def service(tmp_path):
    research = Research(tmp_path / "research")
    research.schedule = lambda identifier: None
    db = sqlite3.connect(tmp_path / "workspace.sqlite3", check_same_thread=False)
    workspace = SimpleNamespace(db=db, lock=threading.RLock(), research=research)
    book = ContractBook(workspace)
    service = ContractResearch(workspace, book)
    yield service
    research.close()
    db.close()


def protocol(service, **changes):
    dataset = service.research.catalog.import_data(source_body())
    saved = service.book.save(dict(request_id="initial-contract", definition=definition(), dataset_id=dataset["id"]))
    body = dict(request_id="freeze-protocol", contract_id=saved["id"], dataset_id=dataset["id"],
                capital_paise=100000000, risk_policy=POLICY,
                observation_cutoff="2026-11-06T15:00:10+05:30", entry_window_seconds=10,
                allow_unverified_data=True, max_trials=6)
    body.update(changes)
    return service.freeze(body)


def trial(service, p, kind="BASELINE", request_id="first-trial"):
    item = service.start(dict(request_id=request_id, protocol_id=p["id"], kind=kind))
    execute(service.research.root, item["run_id"])
    return service.trial(item["id"])


def test_quote_receipt_contract_retains_both_clocks_and_quarantines_time_travel(service):
    store = service.research.catalog
    good = store.import_data(source_body())
    rows = store.rows(good["id"])
    assert rows[0]["received_at"] and rows[0]["available_at"]
    bad = source_body()
    bad.content = bad.content.replace("2026-11-06T09:30:00+05:30,2026-11-06T09:30:00+05:30", "2026-11-06T09:29:59+05:30,2026-11-06T09:29:59+05:30", 1)
    assert store.import_data(bad)["status"] == "QUARANTINED"


def test_protocol_freezes_exact_semantics_and_changed_policy_has_new_identity(service):
    p = protocol(service)
    assert service.get_protocol(p["id"]) == p
    core = p["core"]
    assert core["definition"]["strategy"]["legs"][0]["instrument_id"] == "C25000"
    assert core["run_config"]["strategies"][0]["target_multiple"] is None
    assert core["run_config"]["contract_protocol"]["clock_mode"] == "RECORDED_RECEIPT"
    assert core["dataset_snapshot"]["parquet_hash"]
    assert core["risk_policy_hash"]
    same = protocol(service)
    assert same == p
    different = protocol(service, request_id="different-protocol", capital_paise=200000000)
    assert different["id"] != p["id"]


def test_synthetic_or_incomplete_data_requires_explicit_acknowledgment(service):
    with pytest.raises(ValueError, match="acknowledge|Acknowledge"):
        protocol(service, allow_unverified_data=False)


def test_exact_basket_fill_order_and_independent_after_cost_accounting(service):
    p = protocol(service)
    run = trial(service, p)
    assert run["status"] == "COMPLETED", run
    report = service.research.report(run["run_id"])
    assert not report["positions"] and not report["pending_orders"]
    assert report["account"]["equity"] == pytest.approx(1000312)
    fills = service.research.events(run["run_id"], limit=100, kind="FILL")
    assert [(f["instrument_id"], f["quantity"]) for f in fills] == [("C25000", 65), ("C25100", -65), ("C25100", 65), ("C25000", -65)]
    assert report["diagnostics"]["ledger_reconciled"] and report["diagnostics"]["attribution_reconciled"]


def test_repeat_has_identical_economic_hash_but_distinct_durable_attempt(service):
    p = protocol(service)
    a = trial(service, p)
    b = trial(service, p, kind="REPRODUCE", request_id="repeat-trial")
    assert a["id"] != b["id"] and a["run_id"] != b["run_id"]
    assert service.research.report(a["run_id"])["economic_hash"] == service.research.report(b["run_id"])["economic_hash"]
    assert service.start(dict(request_id="first-trial", protocol_id=p["id"], kind="BASELINE"))["run_id"] == a["run_id"]
    with pytest.raises(ValueError, match="different"):
        service.start(dict(request_id="first-trial", protocol_id=p["id"], kind="COST_STRESS"))


def test_receipt_after_cutoff_cannot_change_a_fill(service):
    p = protocol(service)
    dataset = service.research.catalog.import_data(source_body(delayed=True))
    body = copy.deepcopy(p["core"]["request"])
    body.update(request_id="late-observation-protocol", dataset_id=dataset["id"])
    p = service.freeze(body)
    item = trial(service, p)
    fills = service.research.events(item["run_id"], limit=100, kind="FILL")
    assert all(f["price"] > 10 for f in fills)
    assert all(datetime.fromisoformat(f["time"]) <= datetime.fromisoformat("2026-11-06T15:00:10+05:30") for f in fills)


def test_missing_exit_liquidity_keeps_hedge_until_short_is_closed(service):
    p = protocol(service)
    dataset = service.research.catalog.import_data(source_body(missing_exit=True))
    body = copy.deepcopy(p["core"]["request"])
    body.update(request_id="missing-exit-protocol", dataset_id=dataset["id"])
    p = service.freeze(body)
    item = trial(service, p)
    report = service.research.report(item["run_id"])
    assert report["positions"]
    fills = service.research.events(item["run_id"], limit=100, kind="FILL")
    assert not any(f["instrument_id"] == "C25000" and f["quantity"] < 0 for f in fills)


def test_packet_integrity_optional_trace_reproduction_and_tamper_detection(service):
    p = protocol(service)
    trial(service, p)
    trial(service, p, kind="REPRODUCE", request_id="repeat-trial")
    packet = service.capture(dict(request_id="capture-packet", protocol_id=p["id"]))
    exported = service.export(packet["id"], include_trace=True, rights_acknowledged=True)
    check = verify_packet(exported)
    assert check["integrity_valid"] and check["trace_valid"] and check["repeat_results_match"]
    assert not check["source_authenticity_verified"] and not check["live_authority"]
    altered = copy.deepcopy(exported)
    altered["core"]["protocol"]["core"]["definition"]["strategy"]["lots"] = 10
    assert not verify_packet(altered)["integrity_valid"]
    summary = service.export(packet["id"])
    assert not summary["attachments"]
    assert verify_packet(summary)["integrity_valid"]
    assert not verify_packet(summary)["trace_valid"]


def test_failure_budget_and_stale_packet_are_visible(service):
    p = protocol(service, max_trials=2)
    trial(service, p)
    packet = service.capture(dict(request_id="capture-packet", protocol_id=p["id"]))
    trial(service, p, kind="COST_STRESS", request_id="stress-trial")
    with pytest.raises(ValueError, match="historical|changed|stale"):
        service.export(packet["id"])
    historical = service.export(packet["id"], allow_historical=True)
    assert historical["historical"]
    with pytest.raises(ValueError, match="budget"):
        service.start(dict(request_id="third-trial", protocol_id=p["id"], kind="REPRODUCE"))


def test_protocol_tampering_and_run_rebinding_fail_closed(service):
    p = protocol(service)
    with service.workspace.db:
        service.workspace.db.execute("UPDATE mvp_protocols SET document=replace(document, 'LOCAL_WORKSPACE', 'OTHER_WORKSPACE')")
    with pytest.raises(ValueError, match="integrity"):
        service.get_protocol(p["id"])


def test_scheduled_timer_uses_prior_quotes_without_inventing_market_observations(service):
    body=source_body()
    rows=list(csv.DictReader(io.StringIO(body.content)))
    for row in rows:
        at=datetime.fromisoformat(row["timestamp"])
        if at.hour==9 and at.minute==30 and at.second==0:
            at-=timedelta(milliseconds=500)
        elif at.hour==15 and at.minute==0 and at.second==0:
            at+=timedelta(milliseconds=500)
        for key in ("timestamp","received_at","available_at"):
            row[key]=at.isoformat()
    buffer=io.StringIO()
    writer=csv.DictWriter(buffer,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows)
    body.content=buffer.getvalue()
    dataset=service.research.catalog.import_data(body)
    saved=service.book.save(dict(request_id="timer-contract",definition=definition(),dataset_id=dataset["id"]))
    p=service.freeze(dict(request_id="timer-protocol",contract_id=saved["id"],dataset_id=dataset["id"],capital_paise=100000000,
                          risk_policy=POLICY,observation_cutoff="2026-11-06T15:00:10+05:30",allow_unverified_data=True))
    item=trial(service,p)
    events=service.research.events(item["run_id"],limit=1000)
    clocks=[e for e in events if e["kind"]=="CLOCK"]
    assert any(datetime.fromisoformat(e["time"])==datetime.fromisoformat("2026-11-06T09:30:00+05:30") for e in clocks)
    entry=next(e for e in events if e["kind"]=="ORDER_INTENT" and e["reason"]=="Strategy basket entry")
    assert datetime.fromisoformat(entry["time"])==datetime.fromisoformat("2026-11-06T09:30:00+05:30")
    assert all(datetime.fromisoformat(e["time"])!=datetime.fromisoformat(entry["time"]) for e in events if e["kind"]=="FILL")


def test_cancelled_attempt_stays_in_budget_and_packet_inventory(service):
    p=protocol(service,max_trials=2)
    item=service.start(dict(request_id="cancelled-attempt",protocol_id=p["id"],kind="BASELINE"))
    service.control(item["id"],"CANCEL")
    execute(service.research.root,item["run_id"])
    assert service.trial(item["id"])["status"]=="CANCELLED"
    trial(service,p,request_id="successful-attempt")
    packet=service.capture(dict(request_id="failed-inventory",protocol_id=p["id"]))
    assert {t["status"] for t in packet["core"]["packet"]["core"]["trials"]}=={"CANCELLED","COMPLETED"}


def test_dispatch_interruption_recovers_the_same_reserved_attempt(service,monkeypatch):
    p=protocol(service)
    original=service.research.create
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError("simulated lost response after durable creation")
    monkeypatch.setattr(service.research,"create",interrupted)
    with pytest.raises(ValueError,match="interrupted"):
        service.start(dict(request_id="lost-dispatch",protocol_id=p["id"],kind="BASELINE"))
    reserved=service.trials(p["id"])
    assert len(reserved)==1 and len(service.research.runs())==1
    monkeypatch.setattr(service.research,"create",original)
    recovered=service.start(dict(request_id="lost-dispatch",protocol_id=p["id"],kind="BASELINE"))
    assert recovered["id"]==reserved[0]["id"] and len(service.research.runs())==1
    execute(service.research.root,recovered["run_id"])
    assert service.trial(recovered["id"])["status"]=="COMPLETED"


def test_cost_stress_is_predeclared_and_economically_adverse(service):
    p=protocol(service)
    baseline=trial(service,p)
    stress=trial(service,p,kind="COST_STRESS",request_id="stress-costs")
    report=service.research.report(stress["run_id"])
    assert report["config"]["contract_protocol"]["fee_multiplier"]==2
    assert report["config"]["execution"]["latency_seconds"]==1
    assert report["config"]["risk"]["slippage_bps"]==1
    assert stress["summary"]["net_pnl"]<baseline["summary"]["net_pnl"]
    packet=service.capture(dict(request_id="stress-packet",protocol_id=p["id"]))
    assert verify_packet(service.export(packet["id"],True,True))["trace_valid"]


def test_summary_export_omits_quote_positions_and_trace_requires_ack(service):
    p=protocol(service);trial(service,p)
    packet=service.capture(dict(request_id="summary-packet",protocol_id=p["id"]))
    summary=service.export(packet["id"])
    assert not summary["attachments"]
    assert "positions" not in summary["core"]["trials"][0]["report"]
    assert "account" not in summary["core"]["trials"][0]["report"]
    with pytest.raises(ValueError,match="acknowledgment"):
        service.export(packet["id"],include_trace=True)


def test_changed_build_is_historical_and_cannot_start_more_trials(service,monkeypatch):
    import app.contract_research as module
    p=protocol(service);trial(service,p)
    packet=service.capture(dict(request_id="build-packet",protocol_id=p["id"]))
    monkeypatch.setattr(module,"CURRENT_BUILD",dict(module.CURRENT_BUILD,code_hash="f"*64))
    with pytest.raises(ValueError,match="another build"):
        service.start(dict(request_id="new-build-attempt",protocol_id=p["id"],kind="BASELINE"))
    with pytest.raises(ValueError,match="historical"):
        service.export(packet["id"])
    assert service.export(packet["id"],allow_historical=True)["historical"]
    assert service.packets(p["id"])[0]["historical"]


def test_backup_checks_records_and_detects_event_chain_mutation(service):
    from app.contract_research import check_research_database
    p=protocol(service);trial(service,p)
    service.capture(dict(request_id="backup-packet",protocol_id=p["id"]))
    checked=check_research_database(service.workspace.db)
    assert checked["protocols_verified"]==1 and checked["packets_verified"]==1
    with service.workspace.db:
        service.workspace.db.execute("UPDATE mvp_research_events SET document=replace(document,'PROTOCOL_FROZEN','BROKEN') WHERE sequence=1")
    with pytest.raises(ValueError,match="event-chain"):
        check_research_database(service.workspace.db)


def test_failed_worker_stays_in_inventory_and_packet(service):
    p=protocol(service)
    item=service.start(dict(request_id="failed-worker",protocol_id=p["id"],kind="BASELINE"))
    from app.research.jobs import open_job,metadata,save_meta
    from contextlib import closing
    with closing(open_job(service.research.path(item["run_id"]))) as db:
        meta=metadata(db);meta["engine_hash"]="f"*64
        with db:save_meta(db,meta)
    execute(service.research.root,item["run_id"])
    # Restore expected identity to read the recorded failure, never the terminal result.
    with closing(open_job(service.research.path(item["run_id"]))) as db:
        meta=metadata(db);meta["engine_hash"]=p["core"]["build"]["code_hash"]
        with db:save_meta(db,meta)
    assert service.trial(item["id"])["status"]=="FAILED"
    with pytest.raises(ValueError,match="new budgeted attempt"):
        service.research.control(item["run_id"],"RESUME")
    trial(service,p,request_id="successful-worker")
    packet=service.capture(dict(request_id="failure-packet",protocol_id=p["id"]))
    assert {t["status"] for t in packet["core"]["packet"]["core"]["trials"]}=={"FAILED","COMPLETED"}


def test_api_boundary_explicit_example_and_no_financial_mutation(tmp_path,monkeypatch):
    monkeypatch.delenv("ARTHA_LIVE_TRANSPORT_ENABLED",raising=False)
    app=create_app(tmp_path/"api.sqlite3",background=False)
    with TestClient(app,base_url="http://127.0.0.1") as client:
        before=client.get("/api/state").json()
        assert client.post("/api/contract-research/example",json=dict(request_id="api-example")).status_code==403
        response=client.post("/api/contract-research/example",json=dict(request_id="api-example"),headers=HEADERS)
        assert response.status_code==200,response.text
        value=response.json()
        assert value["controlled_example"] and not value["live_orders_enabled"]
        malformed=client.post("/api/contract-research/protocols",json=dict(request_id="private-sentinel",organization_ref="private-sentinel"),headers=HEADERS)
        assert malformed.status_code==422 and "private-sentinel" not in malformed.text
        assert client.get("/api/contract-research/protocols").json()==[]
        after=client.get("/api/state").json()
        assert all(before[key]==after[key] for key in ("account","orders","positions"))

def test_incomplete_basket_entry_expires_and_unwinds_without_a_short(service):
    body=source_body()
    rows=list(csv.DictReader(io.StringIO(body.content)))
    rows=[r for r in rows if not (r["instrument_id"]=="C25100" and r["timestamp"].startswith("2026-11-06T09:30:01"))]
    for row in rows:
        if row["instrument_id"]=="C25100" and row["timestamp"].startswith("2026-11-06T09:30:02"):
            row["bid_quantity"]=row["ask_quantity"]=0
    buf=io.StringIO();writer=csv.DictWriter(buf,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows);body.content=buf.getvalue()
    dataset=service.research.catalog.import_data(body)
    p=protocol(service)
    request=copy.deepcopy(p["core"]["request"]);request.update(request_id="partial-entry-window",dataset_id=dataset["id"])
    p=service.freeze(request);item=trial(service,p)
    fills=service.research.events(item["run_id"],limit=100,kind="FILL")
    assert [(f["instrument_id"],f["quantity"]) for f in fills]==[("C25000",65),("C25000",-65)]
    assert not item["summary"]["positions"] and not item["summary"]["pending_orders"]
    events=service.research.events(item["run_id"],limit=1000)
    assert any(e["kind"]=="CANCELLED" and "window" in e["reason"] for e in events)
    packet=service.capture(dict(request_id="incomplete-packet",protocol_id=p["id"]))
    assert packet["core"]["packet"]["core"]["findings"]["operational_readiness"]["status"]=="FAIL"


def test_event_index_structure_checked_even_with_recomputed_envelope(service):
    from app.research.catalog import digest
    p=protocol(service);trial(service,p)
    packet=service.capture(dict(request_id="chain-packet",protocol_id=p["id"]))
    exported=service.export(packet["id"],True,True)
    item=exported["core"]["trials"][0];trace=exported["attachments"][item["id"]]
    trace["event_index"][0]["sequence"]=2
    item["trace_hash"]=digest(trace);exported["packet_hash"]=digest(exported["core"])
    assert verify_packet(exported)["reason"]=="Execution event chain mismatch"

def test_cancel_after_fills_preserves_committed_costs_and_partial_trace(service,monkeypatch):
    from app.research.contract_replay import ContractReplay
    from app.research.jobs import open_job
    from contextlib import closing
    p=protocol(service)
    item=service.start(dict(request_id="partial-cancel",protocol_id=p["id"],kind="BASELINE"))
    step=ContractReplay.step
    def stop_after_entry(replay,rows,cursor,**kwargs):
        step(replay,rows,cursor,**kwargs)
        if cursor==3:
            with closing(open_job(service.research.path(item["run_id"]))) as db:
                with db:db.execute("INSERT OR REPLACE INTO control VALUES(1,?)",("CANCEL",))
    monkeypatch.setattr(ContractReplay,"step",stop_after_entry)
    execute(service.research.root,item["run_id"])
    assert service.trial(item["id"])["status"]=="CANCELLED"
    packet=service.capture(dict(request_id="partial-cancel-packet",protocol_id=p["id"]))
    exported=service.export(packet["id"],True,True)
    partial=exported["core"]["trials"][0]
    assert partial["partial_execution"]["committed_fills"]==2
    assert not partial["partial_execution"]["terminal_report_available"]
    assert len(exported["attachments"][item["id"]]["fills"])==2
    assert verify_packet(exported)["trace_valid"]
