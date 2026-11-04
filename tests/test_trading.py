import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.engine import PaperEngine
from app.finance import option_value, option_greeks, implied_volatility, fees


@pytest.fixture
def engine(tmp_path):
    instance = PaperEngine(tmp_path / "paper.sqlite3", capital=1_000_000)
    yield instance
    instance.close()


def buy(engine, **overrides):
    request = dict(symbol="RELIANCE", side="BUY", quantity=10,
                   order_type="MARKET", stop_loss=1470, take_profit=1560,
                   client_id="test-order", reduce_only=False)
    request.update(overrides)
    return engine.submit(request)


def test_cash_and_equity_reconcile_after_round_trip(engine):
    start = engine.snapshot()["account"]["equity"]
    opening = buy(engine)
    assert opening["status"] == "FILLED"
    assert engine.snapshot()["positions"][0]["quantity"] == 10
    closing = engine.close_position("RELIANCE")
    assert closing["status"] == "FILLED"
    result = engine.snapshot()
    assert not result["positions"]
    assert result["account"]["equity"] < start
    assert result["account"]["cash"] == result["account"]["equity"]
    assert engine.reconcile()["ok"]


def test_client_identifier_prevents_duplicate_exposure(engine):
    first, second = buy(engine), buy(engine)
    assert first["id"] == second["id"]
    assert len(engine.snapshot()["fills"]) == 1
    with pytest.raises(ValueError, match="different"):
        buy(engine, quantity=11)


def test_concurrent_submissions_are_idempotent(engine):
    with ThreadPoolExecutor(max_workers=6) as pool:
        orders = list(pool.map(lambda _: buy(engine), range(12)))
    assert len({o["id"] for o in orders}) == 1
    assert engine.reconcile()["ok"]


def test_risk_rejection_cannot_change_cash(engine):
    cash = engine.snapshot()["account"]["cash"]
    rejected = buy(engine, quantity=10_000)
    assert rejected["status"] == "REJECTED"
    assert "risk" in rejected["reason"].lower() or "cash" in rejected["reason"].lower()
    assert engine.snapshot()["account"]["cash"] == cash


def test_pending_order_reserves_cash_and_risk(engine):
    pending = buy(engine, order_type="LIMIT", limit_price=1400, stop_loss=1390,
                  take_profit=1450, quantity=100)
    assert pending["status"] == "OPEN"
    before = engine.snapshot()["account"]["available_cash"]
    assert before < engine.snapshot()["account"]["cash"]
    engine.cancel(pending["id"])
    assert engine.snapshot()["account"]["available_cash"] > before


def test_stop_exit_is_active_while_new_entries_are_paused(engine):
    buy(engine)
    engine.set_paused(True)
    engine.apply_quotes({"RELIANCE": {"bid": 1450, "ask": 1450.1, "last": 1450}}, advance=1)
    assert not engine.snapshot()["positions"]
    assert any(o["source"] == "STOP" for o in engine.snapshot()["orders"])
    assert buy(engine, client_id="paused-entry")["status"] == "REJECTED"


def test_limit_price_is_respected(engine):
    order = buy(engine, order_type="LIMIT", limit_price=1490)
    assert order["status"] == "OPEN"
    engine.apply_quotes({"RELIANCE": {"bid": 1489, "ask": 1489.5, "last": 1489.25}}, advance=1)
    filled = next(o for o in engine.snapshot()["orders"] if o["id"] == order["id"])
    assert filled["status"] == "FILLED"
    assert filled["average_price"] <= 1490


def test_partial_fills_do_not_repeat_flat_brokerage(engine):
    engine.apply_quotes({"NIFTY-CALL-DEMO": {"ask_quantity": 25}}, advance=1)
    order = buy(engine, symbol="NIFTY-CALL-DEMO", quantity=50,
                stop_loss=1, take_profit=None, client_id="partial-option")
    # A deliberately excessive loss budget must be rejected, not partially executed.
    assert order["status"] == "REJECTED"
    mark = engine.snapshot()["market"]
    option = next(i for i in mark if i["symbol"] == "NIFTY-CALL-DEMO")
    order = buy(engine, symbol=option["symbol"], quantity=50,
                stop_loss=round(option["bid"] * .95, 2), take_profit=None,
                client_id="partial-valid")
    assert order["status"] == "PARTIAL"
    engine.apply_quotes({option["symbol"]: {"ask_quantity": 25}}, advance=1)
    result = next(o for o in engine.snapshot()["orders"] if o["id"] == order["id"])
    assert result["status"] == "FILLED"
    assert result["fee_breakdown"]["brokerage"] == 20
    assert engine.reconcile()["ok"]


def test_futures_cash_does_not_pay_notional_and_short_can_close(engine):
    future = next(i for i in engine.snapshot()["market"] if i["kind"] == "FUT")
    order = buy(engine, symbol=future["symbol"], side="SELL", quantity=future["lot_size"],
                stop_loss=future["ask"] + 50, take_profit=None)
    assert order["status"] == "FILLED"
    result = engine.snapshot()
    assert result["account"]["cash"] > 990_000
    assert result["account"]["margin_used"] > 0
    engine.close_position(future["symbol"])
    assert not engine.snapshot()["positions"]
    assert engine.reconcile()["ok"]


def test_no_naked_equity_or_option_short(engine):
    assert buy(engine, side="SELL", stop_loss=1530)["status"] == "REJECTED"
    assert buy(engine, symbol="NIFTY-CALL-DEMO", side="SELL", quantity=25,
               stop_loss=1000, take_profit=None, client_id="short-option")["status"] == "REJECTED"


def test_stale_feed_blocks_entry(engine):
    engine.state["quotes"]["RELIANCE"]["received_at"] = time.time() - 90
    assert buy(engine)["status"] == "REJECTED"


def test_restart_restores_positions_orders_and_cash(tmp_path):
    path = tmp_path / "restart.sqlite3"
    first = PaperEngine(path)
    buy(first)
    initial = first.snapshot()
    first.close()
    second = PaperEngine(path)
    restored = second.snapshot()
    assert restored["account"]["cash"] == initial["account"]["cash"]
    assert restored["positions"][0]["quantity"] == 10
    assert len(restored["fills"]) == 1
    assert second.reconcile()["ok"]
    second.close()


def test_dynamic_sizing_and_minimum_lot(engine):
    request = dict(symbol="RELIANCE", side="BUY", stop_loss=1470,
                   order_type="MARKET", limit_price=None, take_profit=1560)
    high = engine.size(request)["quantity"]
    engine.change_capital(100_000)
    low = engine.size(request)["quantity"]
    assert 0 < low < high
    engine.change_capital(10_000)
    request.update(symbol="NIFTY-FUT-DEMO", stop_loss=23900, take_profit=None)
    assert engine.size(request)["quantity"] == 0


def test_capital_cannot_reset_an_open_account(engine):
    buy(engine)
    with pytest.raises(ValueError, match="flat"):
        engine.change_capital(500_000)


def test_nonfinite_values_are_rejected(engine):
    with pytest.raises(ValueError):
        buy(engine, stop_loss=float("nan"))


def test_option_greeks_and_implied_volatility_are_consistent():
    price = option_value(24000, 24000, 14 / 365, .2, "CE")
    iv = implied_volatility(price, 24000, 24000, 14 / 365, "CE")
    assert iv == pytest.approx(.2, abs=1e-5)
    greek = option_greeks(24000, 24000, 14 / 365, .2, "CE")
    assert 0 < greek["delta"] < 1
    assert greek["gamma"] > 0
    assert greek["theta"] < 0
    assert greek["vega"] > 0
    bump = .1
    numerical_delta = (option_value(24000+bump,24000,14/365,.2,"CE") -
                       option_value(24000-bump,24000,14/365,.2,"CE"))/(2*bump)
    assert greek["delta"] == pytest.approx(numerical_delta, abs=1e-5)


def test_fees_are_monotone_and_include_current_futures_stt():
    buy_fee = fees("FUT", "BUY", 600_000)
    sell_fee = fees("FUT", "SELL", 600_000)
    assert sell_fee["stt"] == 300
    assert sell_fee["total"] > buy_fee["total"]


def test_futures_gap_can_exceed_cash_and_is_recorded_honestly(engine):
    future=next(i for i in engine.snapshot()["market"] if i["kind"]=="FUT")
    order=buy(engine,symbol=future["symbol"],side="SELL",quantity=25,
              stop_loss=future["ask"]+50,take_profit=None)
    assert order["status"]=="FILLED"
    engine.apply_quotes({future["symbol"]:{"bid":99999,"ask":100000,"last":99999.5}},advance=1)
    result=engine.snapshot()
    assert result["account"]["cash"]<0
    assert not result["positions"]
    assert result["halt_reason"]
    assert engine.reconcile()["ok"]


def test_daily_loss_halt_requests_exits_instead_of_just_stopping_entries(engine):
    buy(engine,quantity=10)
    # Simulate an external valuation loss in the other ledger component.
    with engine.atomic():
        engine._ledger("TEST_LOSS",-1_200_000,"test-loss")
        engine._risk_halt()
    assert engine.state["halt_reason"]
    assert engine.state["paused"]
    assert not engine.snapshot()["positions"]


def test_options_receive_joint_price_volatility_and_time_stress(engine):
    account=engine.snapshot()["account"]
    assert "stress_loss" in account
    option=next(i for i in engine.snapshot()["market"] if i["kind"]=="OPT")
    result=buy(engine,symbol=option["symbol"],quantity=25,
               stop_loss=round(option["bid"]*.9,2),take_profit=None)
    assert result["status"]=="FILLED"
    assert engine.snapshot()["account"]["stress_loss"]>0


def test_restart_pauses_entries_and_retains_protection(tmp_path):
    path=tmp_path/"restart-controls.sqlite3"
    first=PaperEngine(path)
    buy(first)
    first.close()
    second=PaperEngine(path)
    assert second.state["paused"]
    assert second.state["positions"]["RELIANCE"]["stop_loss"]==1470
    second.close()


def test_feed_switch_does_not_apply_inflight_quotes_from_old_source(engine):
    before=engine.state["tick"]
    engine.apply_quotes({"RELIANCE":{"bid":1,"ask":2,"last":1.5}},advance=1,expected_source="KITE")
    assert engine.state["tick"]==before
    assert engine.state["quotes"]["RELIANCE"]["last"]>1000


def test_new_paper_session_archives_old_results_and_reconciles(engine):
    buy(engine)
    engine.close_position("RELIANCE")
    assert engine.snapshot()["account"]["pnl"]<0
    engine.new_session(100_000)
    result=engine.snapshot()
    assert result["account"]["pnl"]==0
    assert result["account"]["cash"]==100_000
    assert not result["fills"]
    assert not result["orders"]
    assert engine.db.execute("SELECT COUNT(*) FROM archives").fetchone()[0]==1
    assert engine.reconcile()["ok"]


def test_quotes_are_recorded_persistently(engine):
    count=engine.db.execute("SELECT COUNT(*) FROM market_ticks").fetchone()[0]
    assert count==6
    engine.step(2)
    assert engine.db.execute("SELECT COUNT(*) FROM market_ticks").fetchone()[0]==18


def test_feed_error_blocks_entry_until_successful_recovery(engine):
    engine.market_error("Controlled connection failure")
    assert buy(engine)["status"]=="REJECTED"
    engine.step()
    assert buy(engine,client_id="after-feed-recovery")["status"]=="FILLED"


def test_futures_daily_settlement_preserves_equity(engine):
    from datetime import datetime,timedelta
    future=next(i for i in engine.snapshot()["market"] if i["kind"]=="FUT")
    assert buy(engine,symbol=future["symbol"],quantity=25,stop_loss=future["bid"]-50,take_profit=None)["status"]=="FILLED"
    before=engine.snapshot()["account"]["equity"]
    clock=datetime.fromisoformat(engine.state["clock"])+timedelta(days=1)
    engine.apply_quotes({},advance=1,clock=clock)
    assert engine.snapshot()["account"]["equity"]==before
    assert engine.db.execute("SELECT COUNT(*) FROM cash_ledger WHERE kind='FUTURES_MTM'").fetchone()[0]==1
    assert engine.reconcile()["ok"]


def test_options_request_exit_at_intraday_cutoff(engine):
    from datetime import datetime
    option=next(i for i in engine.snapshot()["market"] if i["kind"]=="OPT")
    assert buy(engine,symbol=option["symbol"],quantity=25,stop_loss=round(option["bid"]*.9,2),take_profit=None)["status"]=="FILLED"
    engine.apply_quotes({},advance=1,clock=datetime.fromisoformat(engine.state["clock"]).replace(hour=15,minute=20))
    assert not engine.snapshot()["positions"]
    assert any(o["source"]=="OPTION_CUTOFF" for o in engine.snapshot()["orders"])


def test_capital_adjustment_does_not_erase_losses(engine):
    buy(engine)
    engine.close_position("RELIANCE")
    loss=engine.snapshot()["account"]["pnl"]
    engine.change_capital(100_000)
    assert engine.snapshot()["account"]["pnl"]==loss
    assert engine.reconcile()["ok"]


def test_failed_action_rolls_back_state_and_ledger(engine):
    cash=engine.state["cash"]
    with pytest.raises(ValueError,match="abort"):
        with engine.atomic():
            engine._ledger("TEST",-1000,"never-committed")
            raise ValueError("abort action")
    assert engine.state["cash"]==cash
    assert engine.db.execute("SELECT COUNT(*) FROM cash_ledger WHERE reference='never-committed'").fetchone()[0]==0
    assert engine.reconcile()["ok"]


def test_invalid_quote_packet_does_not_overwrite_last_usable_quote(engine):
    quote=engine.state["quotes"]["RELIANCE"].copy()
    with pytest.raises(ValueError,match="quote"):
        engine.apply_quotes({"RELIANCE":{"bid":float('nan')}})
    assert engine.state["quotes"]["RELIANCE"]==quote
