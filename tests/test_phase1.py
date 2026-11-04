"""Acceptance tests for decision evidence, shared boundaries and recovery."""
import copy
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from app.engine import PaperEngine, IST
from app.execution import PaperExecutionAdapter
from app.strategies import evaluate, identity, VERSION
from app.market import KiteData
from tools.backup import backup


@pytest.fixture
def engine(tmp_path):
    instance=PaperEngine(tmp_path/"phase1.sqlite3")
    yield instance
    instance.close()


def entry(engine, **changes):
    request=dict(symbol="RELIANCE",side="BUY",quantity=10,stop_loss=1470,client_id="phase1-entry")
    request.update(changes)
    return engine.submit(request)


def test_decision_evidence_is_persistent_and_unchanged_by_later_quotes(engine):
    order=entry(engine)
    evidence=next(d for d in engine.decisions() if d["id"]==order["decision_id"])
    assert evidence["action"]=="ACCEPT"
    assert evidence["checks"] and all(c["status"]=="PASS" for c in evidence["checks"])
    assert evidence["market_snapshot"]["RELIANCE"]==evidence["quote"]
    assert evidence["sizing"]["selected_quantity"]==10
    assert evidence["sizing"]["quantity"]>=10
    assert evidence["risk_policy"]["config_hash"]
    assert evidence["execution_model"]["mode"]=="PAPER"
    before=copy.deepcopy(evidence)
    engine.step()
    assert next(d for d in engine.decisions() if d["id"]==evidence["id"])==before
    fills=[d for d in engine.decisions() if d["action"]=="FILL"]
    assert fills[0]["execution"]["fill_id"]
    assert fills[0]["execution"]["reference"]==before["quote"]["ask"]
    count=len(engine.decisions())
    assert entry(engine)["id"]==order["id"]
    assert len(engine.decisions())==count


def test_rejected_order_has_same_risk_evidence_as_preview(engine):
    request=dict(symbol="RELIANCE",side="BUY",quantity=100_000,stop_loss=1470)
    preview=engine.preview(request)
    order=engine.submit(request)
    record=next(d for d in engine.decisions() if d["action"]=="REJECT")
    assert record["checks"]==preview["checks"]
    assert record["reason"]==preview["reason"]==order["reason"]
    assert not engine.state["positions"]


def test_decisions_rollback_with_account_and_cash(engine):
    count=engine.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    cash=engine.state["cash"]
    with pytest.raises(RuntimeError,match="abort"):
        with engine.atomic():
            engine._submit(dict(symbol="RELIANCE",side="BUY",quantity=10,stop_loss=1470))
            raise RuntimeError("abort")
    assert engine.state["cash"]==cash
    assert not engine.state["positions"]
    assert engine.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]==count
    assert engine.reconcile()["ok"]


def test_audit_pagination_and_archive_preserve_prior_experiments(engine):
    entry(engine)
    engine.close_position("RELIANCE")
    ids={d["id"] for d in engine.decisions()}
    first=engine.decisions(limit=1)[0]
    assert all(d["sequence"]<first["sequence"] for d in engine.decisions(before=first["sequence"]))
    engine.new_session(100_000)
    assert engine.decisions()==[]
    assert ids <= {d["id"] for d in engine.decisions(all_sessions=True)}


def test_versioned_strategy_is_reproducible_and_records_hold_and_entry(engine):
    engine.configure_strategy(True,fast=2,slow=3)
    strategy=engine.state["strategies"][0]
    assert evaluate(strategy,[3,2,1,4])["signal"]=="BUY"
    assert evaluate(strategy,[3,2,1,4])==evaluate(strategy,[3,2,1,4])
    context=identity(strategy)
    engine.state["prices"]["RELIANCE"]=[1500,1499,1498]
    engine.apply_quotes({"RELIANCE":{"last":1501,"bid":1500.9,"ask":1501.1}},advance=1)
    accepted=[d for d in engine.decisions() if d["action"]=="ACCEPT"]
    assert accepted and accepted[0]["strategy"]==context
    assert accepted[0]["strategy"]["version"]==VERSION
    assert accepted[0]["inputs"]["observations"]==[1500,1499,1498,1501]
    engine.apply_quotes({"RELIANCE":{"last":1502,"bid":1501.9,"ask":1502.1}},advance=1)
    assert any(d["action"]=="HOLD" for d in engine.decisions())
    engine.close_position("RELIANCE")
    assert next(d for d in engine.decisions() if d["action"]=="ACCEPT")["strategy"]==context


def test_option_legs_report_net_greeks_liquidity_expiry_and_time_decay(engine):
    for item in [i for i in engine.snapshot()["market"] if i["kind"]=="OPT"]:
        assert entry(engine,symbol=item["symbol"],quantity=25,stop_loss=round(item["bid"]*.95,2),client_id=item["symbol"])["status"]=="FILLED"
    group=engine.snapshot()["option_groups"][0]
    assert len(group["legs"])==2
    assert all(l["quantity"]==25 and l["days_to_expiry"]>0 for l in group["legs"])
    assert group["delta"]==pytest.approx(sum(l["greeks"]["delta"]*l["quantity"] for l in group["legs"]))
    assert group["theta"]<0
    engine.apply_quotes({group["legs"][0]["symbol"]:{"ask_quantity":0}},advance=1)
    assert engine.snapshot()["option_groups"][0]["legs"][0]["liquidity"]=="THIN"
    account=engine.snapshot()["account"]
    assert account["pnl"]==pytest.approx(account["realised_net"]+account["unrealised"])


def test_consistent_wal_backup_restarts_with_audit_and_entries_paused(engine,tmp_path):
    entry(engine)
    source=Path_from_engine(engine)
    destination=tmp_path/"saved.sqlite3"
    backup(source,destination)
    restored=PaperEngine(destination)
    try:
        assert restored.snapshot()["paused"]
        assert restored.state["cash"]==engine.state["cash"]
        assert len(restored.decisions())==len(engine.decisions())
        assert restored.reconcile()["ok"]
        assert destination.stat().st_mode & 0o077==0
    finally:
        restored.close()
    with pytest.raises(FileExistsError):
        backup(source,destination)


def Path_from_engine(engine):
    return engine.db.execute("PRAGMA database_list").fetchone()[2]


def test_legacy_state_upgrades_without_fabricating_old_decisions(tmp_path):
    path=tmp_path/"legacy.sqlite3"
    original=PaperEngine(path)
    entry(original)
    state=copy.deepcopy(original.state)
    state.pop("session_id")
    state["strategies"][0].pop("version")
    original.close()
    with sqlite3.connect(path) as database:
        database.execute("DROP TABLE decisions")
        database.execute("UPDATE state SET document=?",(json.dumps(state),))
    restored=PaperEngine(path)
    try:
        assert restored.state["session_id"]
        assert restored.state["strategies"][0]["version"]==VERSION
        assert restored.state["positions"]["RELIANCE"]["quantity"]==10
        assert restored.decisions()==[]
        assert restored.reconcile()["ok"]
    finally:
        restored.close()


@pytest.mark.parametrize("change",[{"bid":-1},{"ask":1},{"bid_quantity":1.5},{"iv":"bad"},{"exchange_time":"bad"}])
def test_bad_quote_packets_leave_last_observation_intact(engine,change):
    before=copy.deepcopy(engine.state)
    with pytest.raises(ValueError,match="quote"):
        engine.apply_quotes({"RELIANCE":change},advance=1)
    assert engine.state==before


def test_simulator_respects_limits_and_available_whole_lots():
    adapter=PaperExecutionAdapter()
    order=dict(side="BUY",order_type="LIMIT",limit_price=100,quantity=100,filled_quantity=0)
    item=dict(tick_size=.05,lot_size=25)
    quote=dict(ask=99.95,bid=99.9,ask_quantity=60,bid_quantity=50)
    result=adapter.propose(order,item,quote,50,0)
    assert result.price==100 and result.quantity==50
    assert adapter.propose(order,item,quote,50,50).quantity==0
    quote["ask"]=101
    assert adapter.propose(order,item,quote,50,0).quantity==0


def test_controlled_kite_master_and_quote_ingestion(monkeypatch,engine):
    adapter=KiteData()
    now=datetime.now(IST)
    expiry=(now+timedelta(days=14)).date().isoformat()
    header="exchange,tradingsymbol,instrument_type,name,expiry,strike,lot_size,tick_size,instrument_token"
    rows=[f"NSE,{symbol},EQ,{symbol},,0,1,0.05" for symbol in ("RELIANCE","HDFCBANK","INFY")]
    rows += [f"NFO,NIFTY-{kind},{kind},NIFTY,{expiry},{24000 if kind!='FUT' else 0},25,0.05" for kind in ("FUT","CE","PE")]
    rows=[row+","+str((i+1)*256+(1 if row.startswith("NSE,") else 2)) for i,row in enumerate(rows)]
    def request(method,path,**kwargs):
        if path=="/instruments": return "\n".join([header,*rows])
        assert method=="GET" and path=="/quote"
        return {key:dict(instrument_token=256009,last_price=24000 if key=="NSE:NIFTY 50" else 100,timestamp=now.isoformat(),
            depth=dict(buy=[dict(price=99.95,quantity=100)],sell=[dict(price=100.05,quantity=100)])) for _,key in kwargs["params"]}
    monkeypatch.setattr(adapter,"_request",request)
    instruments,quotes=adapter.load()
    assert len(instruments)==len(quotes)==6
    engine.switch_source("KITE",instruments,quotes)
    snapshot=engine.snapshot()
    assert snapshot["source"]=="KITE" and all(i["fresh"] for i in snapshot["market"])
    assert snapshot["mode"]=="PAPER" and not snapshot["live_trading_available"]


def test_instrument_history_records_source_and_new_experiments(engine):
    assert engine.db.execute("SELECT COUNT(*) FROM instrument_history").fetchone()[0]==1
    engine.switch_source("DEMO")
    engine.new_session(100_000)
    rows=engine.db.execute("SELECT * FROM instrument_history ORDER BY id").fetchall()
    assert len(rows)==3
    assert json.loads(rows[0]["document"])["instruments"]["NIFTY-CALL-DEMO"]["lot_size"]==25


def test_manual_rationale_is_bound_to_the_order_and_decision(engine):
    order=entry(engine,rationale="Testing a pullback after a trend")
    record=next(d for d in engine.decisions() if d["id"]==order["decision_id"])
    assert record["reason"]=="Manual rationale: Testing a pullback after a trend"
    with pytest.raises(ValueError,match="different"):
        entry(engine,rationale="Changed explanation")


def test_legacy_order_idempotency_survives_optional_rationale_field(engine):
    import hashlib
    order=entry(engine)
    legacy={key:order[key] for key in ("symbol","side","quantity","order_type","reduce_only","stop_loss","take_profit","limit_price")}
    saved=engine.state["orders"][0]
    saved.pop("rationale",None)
    saved["signature"]=hashlib.sha256(json.dumps(legacy,sort_keys=True).encode()).hexdigest()
    count=len(engine.decisions())
    assert entry(engine)["id"]==order["id"]
    assert len(engine.decisions())==count


def test_strategy_records_configuration_and_reason_for_ignoring_manual_position(engine):
    entry(engine)
    engine.configure_strategy(True,fast=2,slow=3)
    configured=next(d for d in engine.decisions() if d["action"]=="CONFIGURE_STRATEGY")
    assert configured["strategy"]["config"]["fast"]==2
    engine.state["prices"]["RELIANCE"]=[1500,1499,1498]
    engine.apply_quotes({"RELIANCE":{"last":1501,"bid":1500.9,"ask":1501.1}},advance=1)
    assert any(d["action"]=="SKIP" and "existing position" in d["reason"] for d in engine.decisions())
    assert len(engine.state["positions"])==1
