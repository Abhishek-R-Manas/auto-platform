"""A paper-only OMS with a transactionally committed state, cash ledger and audit.

No broker order client exists in this module. All prices and fills are simulated.
"""
import copy
import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .risk import budget_checks, fill_cash_delta
from .execution import ExecutionAdapter, PaperExecutionAdapter
from .strategies import VERSION, SPECIFICATION, identity, evaluate

from .finance import fees, paise, rupees, tick_price, option_value, option_greeks, implied_volatility

IST = ZoneInfo("Asia/Kolkata")
ACTIVE = {"OPEN", "PARTIAL"}
DEFAULT_RISK = dict(trade_pct=.25, open_pct=.75, daily_pct=1.0, weekly_pct=2.0,
                    drawdown_pct=5.0, margin_pct=40.0, slippage_bps=2.0,
                    overnight_stress_pct=5.0)


def demo_instruments(now):
    expiry = (now + timedelta(days=14)).replace(hour=15, minute=30)
    items = [
        dict(symbol="RELIANCE", name="Reliance Industries", kind="EQ", underlying="RELIANCE", base=1500, lot_size=1),
        dict(symbol="HDFCBANK", name="HDFC Bank", kind="EQ", underlying="HDFCBANK", base=1800, lot_size=1),
        dict(symbol="INFY", name="Infosys", kind="EQ", underlying="INFY", base=1600, lot_size=1),
        dict(symbol="NIFTY-FUT-DEMO", name="Nifty · demo future", kind="FUT", underlying="NIFTY", base=24040, lot_size=25),
        dict(symbol="NIFTY-CALL-DEMO", name="Nifty 24,000 · demo call", kind="OPT", underlying="NIFTY", base=0, lot_size=25, strike=24000, option_type="CE"),
        dict(symbol="NIFTY-PUT-DEMO", name="Nifty 24,000 · demo put", kind="OPT", underlying="NIFTY", base=0, lot_size=25, strike=24000, option_type="PE"),
    ]
    for item in items:
        item.update(tick_size=.05, expiry=expiry.isoformat() if item["kind"] != "EQ" else None,
                    exchange="NSE" if item["kind"] == "EQ" else "NFO", margin_rate=.18,
                    specification="ILLUSTRATIVE" if item["kind"] != "EQ" else "DEMO")
    return {item["symbol"]: item for item in items}


class PaperEngine:
    def __init__(self, path, capital=1_000_000):
        self.lock = threading.RLock()
        self.execution: ExecutionAdapter = PaperExecutionAdapter()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY, time TEXT NOT NULL, level TEXT NOT NULL,
                kind TEXT NOT NULL, message TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cash_ledger(id INTEGER PRIMARY KEY, time TEXT NOT NULL,
                kind TEXT NOT NULL, amount INTEGER NOT NULL, balance INTEGER NOT NULL, reference TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fills(id TEXT PRIMARY KEY, order_id TEXT NOT NULL, time TEXT NOT NULL,
                symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price INTEGER NOT NULL,
                fees INTEGER NOT NULL, realised INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS archives(id INTEGER PRIMARY KEY, ended TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS market_ticks(id INTEGER PRIMARY KEY, time TEXT NOT NULL,
                source TEXT NOT NULL, symbol TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(sequence INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL, order_id TEXT, document TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS decisions_session_sequence ON decisions(session_id,sequence);
            CREATE INDEX IF NOT EXISTS decisions_order ON decisions(order_id);
            CREATE TABLE IF NOT EXISTS instrument_history(id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
                source TEXT NOT NULL, document TEXT NOT NULL);
        """)
        row = self.db.execute("SELECT document FROM state WHERE id=1").fetchone()
        self.events, self.ledger, self.new_fills, self.new_ticks, self.new_decisions = [], [], [], [], []
        if row:
            self.state = json.loads(row[0])
            if self.state.get("schema") != 1:
                raise RuntimeError("Unsupported database schema")
            self._invariants()
            with self.atomic():
                self.state.setdefault("session_id", str(uuid.uuid4()))
                self.state["strategies"][0]["version"] = VERSION
                if not self.db.execute("SELECT 1 FROM instrument_history LIMIT 1").fetchone():
                    self._record_instruments()
                self.state["paused"]=True
                for order in self.state["orders"]:
                    if order["status"] in ACTIVE and not order["reduce_only"]:
                        order.update(status="CANCELLED",reason="Restart readiness review required")
                        self._decision("CANCEL",order["symbol"],order["reason"],order=order)
                self._event("WARN","RECOVERY","State restored. Entries paused for readiness review; protective exits retained.")
        else:
            clock = datetime.now(IST).replace(hour=9, minute=30, second=0, microsecond=0)
            self.state = dict(schema=1, session_id=str(uuid.uuid4()), mode="PAPER", source="DEMO", clock=clock.isoformat(), tick=0,
                cash=paise(capital), capital=paise(capital), equity_peak=paise(capital),
                day_start=paise(capital), week_start=paise(capital), day=clock.date().isoformat(),
                week=str(clock.isocalendar()[:2]), paused=False, halt_reason=None, auto_feed=True,
                feed_error=None, risk=DEFAULT_RISK.copy(), instruments=demo_instruments(clock),
                quotes={}, positions={}, orders=[], history=[], prices={}, used_liquidity={},
                dp_charged=[], strategies=[dict(id="trend-baseline", name="Trend crossover", version=VERSION, symbol="RELIANCE",
                    enabled=False, fast=5, slow=20, stop_pct=2, reward_ratio=2,
                    status="Disabled", last_signal="Waiting for observations", evaluated_tick=-1)])
            with self.atomic():
                self._ledger("CAPITAL", paise(capital), "initial-virtual-capital", update=False)
                self._demo_quotes()
                self._record_instruments()
                self._event("INFO", "SESSION", "Paper workspace created. All balances and executions are virtual.")
                self._record_equity()
        if not self.reconcile()["ok"]:
            raise RuntimeError("Cash ledger does not reconcile; startup blocked")

    @contextmanager
    def atomic(self):
        # ponytail: one account and one process; use an account-partitioned store when multi-user is required.
        with self.lock:
            previous = copy.deepcopy(self.state)
            self.events, self.ledger, self.new_fills, self.new_ticks, self.new_decisions = [], [], [], [], []
            try:
                yield
                self._invariants()
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO state VALUES(1,?)", (json.dumps(self.state, allow_nan=False),))
                    self.db.executemany("INSERT INTO journal(time,level,kind,message,details) VALUES(?,?,?,?,?)", self.events)
                    self.db.executemany("INSERT INTO cash_ledger(time,kind,amount,balance,reference) VALUES(?,?,?,?,?)", self.ledger)
                    self.db.executemany("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)", self.new_fills)
                    self.db.executemany("INSERT INTO market_ticks(time,source,symbol,document) VALUES(?,?,?,?)", self.new_ticks)
                    self.db.executemany("INSERT INTO decisions(id,session_id,order_id,document) VALUES(?,?,?,?)", self.new_decisions)
            except BaseException:
                self.db.rollback()
                self.state = previous
                raise
            finally:
                self.events, self.ledger, self.new_fills, self.new_ticks, self.new_decisions = [], [], [], [], []

    def _invariants(self):
        if self.state["mode"] != "PAPER" or type(self.execution) is not PaperExecutionAdapter:
            raise RuntimeError("Live trading is not implemented")
        if type(self.state["cash"]) is not int:
            raise RuntimeError("Cash must use integer paise")
        for symbol, position in self.state["positions"].items():
            item = self.state["instruments"][symbol]
            if not position["quantity"] or abs(position["quantity"]) % item["lot_size"]:
                raise RuntimeError("Invalid position quantity")
            if item["kind"] != "FUT" and position["quantity"] < 0:
                raise RuntimeError("Uncovered shorts are not supported")
        if len({o["client_id"] for o in self.state["orders"]}) != len(self.state["orders"]):
            raise RuntimeError("Duplicate client order identifiers")

    def _record_instruments(self):
        self.db.execute("INSERT INTO instrument_history(observed_at,source,document) VALUES(?,?,?)",
            (datetime.now(IST).isoformat(),self.state["source"],json.dumps(dict(clock=self.state["clock"],instruments=self.state["instruments"]),allow_nan=False)))

    def _event(self, level, kind, message, details=None):
        self.events.append((self.state["clock"], level, kind, message, json.dumps(details or {}, allow_nan=False)))

    def _ledger(self, kind, amount, reference, update=True):
        if update:
            self.state["cash"] += amount
        self.ledger.append((self.state["clock"], kind, amount, self.state["cash"], reference))

    def reconcile(self):
        with self.lock:
            ledger = self.db.execute("SELECT COALESCE(SUM(amount),0) FROM cash_ledger").fetchone()[0]
            return dict(ok=ledger == self.state["cash"], ledger_cash=rupees(ledger), account_cash=rupees(self.state["cash"]))

    def close(self):
        self.db.close()

    def _demo_quotes(self):
        tick = self.state["tick"]
        clock = datetime.fromisoformat(self.state["clock"])
        spot = 24000 * (1 + .0045 * math.sin(tick / 18) + .002 * math.sin(tick / 5))
        for index, item in enumerate(self.state["instruments"].values()):
            if item["kind"] == "EQ":
                mid = item["base"] * (1 + .005 * math.sin(tick / (12 + index * 3)) + .002 * math.sin(tick / 4 + index))
            elif item["kind"] == "FUT":
                mid = spot + 40
            else:
                years = max((datetime.fromisoformat(item["expiry"]) - clock).total_seconds() / (365 * 86400), .00001)
                mid = option_value(spot, item["strike"], years, .20, item["option_type"])
            spread = max(.1, mid * (.0002 if item["kind"] != "OPT" else .003))
            bid = max(item["tick_size"], tick_price(mid - spread / 2, item["tick_size"], "SELL"))
            ask = tick_price(mid + spread / 2, item["tick_size"])
            quote = dict(last=round(mid, 2), bid=bid, ask=ask,
                         bid_quantity=500 if item["kind"] == "EQ" else 100,
                         ask_quantity=500 if item["kind"] == "EQ" else 100,
                         received_at=time.time(), exchange_time=self.state["clock"],
                         spot=spot if item["underlying"] == "NIFTY" else mid, iv=.20)
            self.state["quotes"][item["symbol"]] = quote
            self.new_ticks.append((self.state["clock"],self.state["source"],item["symbol"],json.dumps(quote,allow_nan=False)))
        self.state["feed_error"]=None

    def _fresh(self, symbol):
        quote = self.state["quotes"].get(symbol)
        if not quote or time.time() - quote["received_at"] > 30 or (self.state["source"]=="KITE" and self.state["feed_error"]):
            return False
        if self.state["source"] == "KITE":
            exchange = datetime.fromisoformat(quote["exchange_time"])
            if abs((datetime.now(IST) - exchange).total_seconds()) > 30:
                return False
            if self.state["instruments"][symbol]["kind"]!="EQ":
                spot_time=datetime.fromisoformat(quote.get("spot_exchange_time",quote["exchange_time"]))
                if abs((datetime.now(IST)-spot_time).total_seconds())>30 or time.time()-quote.get("spot_received_at",quote["received_at"])>30:
                    return False
        return 0 < quote["bid"] <= quote["ask"]

    def _mark(self, symbol, quantity):
        quote = self.state["quotes"][symbol]
        return paise(quote["bid"] if quantity > 0 else quote["ask"])

    def _position_risk(self, symbol, position):
        count = abs(position["quantity"])
        sign = 1 if position["quantity"] > 0 else -1
        return max(0, sign * (position["basis"] - paise(position["stop_loss"]) * count))

    def _order_cost(self, order):
        item = self.state["instruments"][order["symbol"]]
        quote = self.state["quotes"][order["symbol"]]
        price = order.get("limit_price") if order["order_type"] == "LIMIT" else quote["ask"] if order["side"] == "BUY" else quote["bid"]
        return max(.05, price)

    def _reserved(self, exclude=None):
        cash, risk = 0, 0
        for order in self.state["orders"]:
            if order["status"] not in ACTIVE or order["reduce_only"] or order["id"] == exclude:
                continue
            remaining = order["quantity"] - order["filled_quantity"]
            item = self.state["instruments"][order["symbol"]]
            price = self._order_cost(order)
            cash += paise(price * remaining * (item["margin_rate"] if item["kind"] == "FUT" else 1))
            risk += paise(abs(price - order["stop_loss"]) * remaining)
            cash += paise(fees(item["kind"], order["side"], price * remaining)["total"])
        return cash, risk

    def _account(self):
        equity = self.state["cash"]
        margin, open_risk, futures_loss, exposure, unrealised_total = 0, 0, 0, 0, 0
        for symbol, p in self.state["positions"].items():
            item = self.state["instruments"][symbol]
            value = self._mark(symbol, p["quantity"]) * abs(p["quantity"])
            exposure += value
            unrealised_total += (value-p["basis"])*(1 if p["quantity"]>0 else -1)
            if item["kind"] == "FUT":
                unrealised = (value - p["basis"]) * (1 if p["quantity"] > 0 else -1)
                equity += unrealised
                futures_loss += min(unrealised, 0)
                margin += int(value * item["margin_rate"])
            else:
                equity += value
            open_risk += self._position_risk(symbol, p)
        reserved_cash, reserved_risk = self._reserved()
        return dict(equity=equity, cash=self.state["cash"], margin_used=margin,
                    available_cash=max(0, self.state["cash"] + futures_loss - margin - reserved_cash),
                    reserved_cash=reserved_cash, planned_risk=open_risk + reserved_risk,
                    exposure=exposure, pnl=equity-self.state["capital"],
                    day_pnl=equity-self.state["day_start"], week_pnl=equity-self.state["week_start"],
                    stress_loss=self._stress_loss(),
                    drawdown_pct=max(0, (self.state["equity_peak"]-equity)/max(1,self.state["equity_peak"])*100),
                    capital=self.state["capital"],unrealised=unrealised_total,realised_net=equity-self.state["capital"]-unrealised_total)

    def _stress_loss(self,extra=None):
        """Illustrative +/-5% spot, +/-10 IV-point, one-day joint scenarios.

        Worst losses by underlying are added conservatively. These model estimates
        are neither maximum losses nor statistically calibrated stress boundaries.
        """
        exposures=[]
        for symbol,p in self.state["positions"].items():
            exposures.append((symbol,p["quantity"],self._mark(symbol,p["quantity"])/100))
        for order in self.state["orders"]:
            if order["status"] in ACTIVE and not order["reduce_only"] and (not extra or order["id"]!=extra.get("id")):
                exposures.append((order["symbol"],(order["quantity"]-order["filled_quantity"])*(1 if order["side"]=="BUY" else -1),self._order_cost(order)))
        if extra:
            exposures.append((extra["symbol"],(extra["quantity"]-extra.get("filled_quantity",0))*(1 if extra["side"]=="BUY" else -1),self._order_cost(extra)))
        groups={}
        for symbol,quantity,mark in exposures:
            item=self.state["instruments"][symbol]
            groups.setdefault(item["underlying"],[]).append((item,quantity,mark))
        total=0
        for positions in groups.values():
            worst=0
            for move in (-.05,.05):
                for vol_shift in (-.10,.10):
                    result=0
                    for item,quantity,mark in positions:
                        if item["kind"]!="OPT":
                            result+=mark*move*quantity
                        else:
                            quote=self.state["quotes"][item["symbol"]]
                            greek=self._greeks(item)
                            if not greek:
                                result-=abs(quantity)*mark
                                continue
                            years=max(0,(datetime.fromisoformat(item["expiry"])-datetime.fromisoformat(self.state["clock"])).total_seconds()/(365*86400)-1/365)
                            stressed=option_value(quote["spot"]*(1+move),item["strike"],years,max(.01,greek["iv"]/100+vol_shift),item["option_type"])
                            result+=(stressed-mark)*quantity
                    worst=min(worst,result)
            total+=-worst
        return paise(total)

    def _normalise(self, request):
        symbol = request.get("symbol")
        if symbol not in self.state["instruments"]:
            raise ValueError("Unknown instrument")
        quantity = request.get("quantity", 1)
        if type(quantity) is not int or quantity <= 0 or quantity > 10_000_000:
            raise ValueError("Quantity must be a positive whole number")
        if request.get("side") not in {"BUY", "SELL"}:
            raise ValueError("Choose BUY or SELL")
        if request.get("order_type", "MARKET") not in {"MARKET", "LIMIT"}:
            raise ValueError("Only paper MARKET and LIMIT orders are supported")
        rationale = request.get("rationale")
        if rationale is not None and (not isinstance(rationale,str) or len(rationale)>1000):
            raise ValueError("Rationale must be text of at most 1000 characters")
        result = dict(rationale=rationale.strip() if rationale else None,symbol=symbol, side=request["side"], quantity=quantity,
                      order_type=request.get("order_type", "MARKET"),
                      reduce_only=bool(request.get("reduce_only", False)),
                      stop_loss=request.get("stop_loss"), take_profit=request.get("take_profit"),
                      limit_price=request.get("limit_price"))
        for field in ("stop_loss", "take_profit", "limit_price"):
            if result[field] is not None:
                result[field] = rupees(paise(result[field]))
                if result[field] <= 0:
                    raise ValueError("Prices must be positive")
        if result["order_type"] == "LIMIT" and result["limit_price"] is None:
            raise ValueError("A limit price is required")
        if not result["reduce_only"] and result["stop_loss"] is None:
            raise ValueError("Every entry needs a protective stop")
        return result

    def _evaluate(self, order):
        """One authoritative risk evaluation, used by preview, sizing and orders."""
        checks = []
        def check(name, passed, failure, observed=None, limit=None):
            checks.append(dict(name=name, status="PASS" if passed else "FAIL", reason=failure if not passed else "Within policy",
                               observed=observed, limit=limit))
            return passed
        item = self.state["instruments"][order["symbol"]]
        remaining = order["quantity"] - order.get("filled_quantity", 0)
        if not check("whole_lots", remaining > 0 and remaining % item["lot_size"] == 0,
                     "Quantity must be a multiple of the instrument lot size", remaining, item["lot_size"]): return checks
        if not check("tick_size", order["order_type"] != "LIMIT" or paise(order["limit_price"]) % max(1,paise(item["tick_size"])) == 0,
                     "Limit price must align with the instrument tick size", order.get("limit_price"), item["tick_size"]): return checks
        if not check("fresh_data", self._fresh(order["symbol"]), "Market data is stale or unavailable"):
            return checks
        p = self.state["positions"].get(order["symbol"])
        if order["reduce_only"]:
            if not check("reduce_only", bool(p) and (p["quantity"] > 0) != (order["side"] == "BUY") and remaining <= abs(p["quantity"]),
                         "An exit must reduce an existing position without reversing it"): return checks
            other = sum(o["quantity"]-o["filled_quantity"] for o in self.state["orders"]
                        if o["status"] in ACTIVE and o["reduce_only"] and o["symbol"] == order["symbol"] and o["id"] != order.get("id"))
            check("exit_reservation", other + remaining <= abs(p["quantity"]), "An exit order already reserves this position",
                  other + remaining, abs(p["quantity"]))
            return checks
        check("entry_pause", not self.state["paused"] and not self.state["halt_reason"], self.state["halt_reason"] or "New entries are paused")
        check("feed_health", not self.state["feed_error"], "Market feed is not healthy; new entries require successful data recovery")
        now = datetime.now(IST)
        check("execution_session", self.state["source"] != "KITE" or (now.weekday() < 5 and (9,15) <= (now.hour,now.minute) < (15,20)),
              "Outside the configured paper execution session")
        clock = datetime.fromisoformat(self.state["clock"])
        check("option_intraday", item["kind"] != "OPT" or (clock.hour,clock.minute) < (15,15),
              "Options entry cutoff reached; initial options policy is intraday")
        check("expiry", not item.get("expiry") or clock < datetime.fromisoformat(item["expiry"]).replace(hour=14,minute=45),
              "Derivative expiry cutoff reached")
        check("permitted_direction", item["kind"] == "FUT" or order["side"] != "SELL", "Uncovered equity and option shorts are disabled in Phase 1")
        check("position_ownership", not p or p["entry_order"] == order.get("id"), "A position already exists; close it before another entry")
        check("pending_entry", not any(o["symbol"] == order["symbol"] and o["status"] in ACTIVE and not o["reduce_only"] and o["id"] != order.get("id") for o in self.state["orders"]),
              "A pending entry already reserves this instrument")
        price = self._order_cost(order)
        sign = 1 if order["side"] == "BUY" else -1
        check("protective_stop", sign * (price-order["stop_loss"]) > 0, "Protective stop must be below a long entry or above a short entry", order["stop_loss"], price)
        check("profit_target", order.get("take_profit") is None or sign * (order["take_profit"]-price) > 0, "Target must be beyond the entry in the profitable direction")
        account = self._account()
        equity = account["equity"]
        slip = price * self.state["risk"]["slippage_bps"] / 10000
        estimated_fees = fees(item["kind"],order["side"],price*remaining)["total"] + fees(item["kind"],"SELL" if sign>0 else "BUY",price*remaining,dp=item["kind"]=="EQ")["total"]
        new_risk = paise((abs(price-order["stop_loss"])+2*slip)*remaining + estimated_fees)
        position_risk = self._position_risk(order["symbol"],p) if p else 0
        other_cash, other_risk = self._reserved(order.get("id"))
        open_risk = sum(self._position_risk(s,p) for s,p in self.state["positions"].items())
        needed = paise((price+slip)*remaining*(item["margin_rate"] if item["kind"]=="FUT" else 1) + estimated_fees)
        futures_loss = sum(min(0,(self._mark(s,p["quantity"])*abs(p["quantity"])-p["basis"])*(1 if p["quantity"]>0 else -1)) for s,p in self.state["positions"].items() if self.state["instruments"][s]["kind"]=="FUT")
        free = self.state["cash"] + futures_loss - account["margin_used"] - other_cash
        pending_margin = sum(paise(self._order_cost(o)*(o["quantity"]-o["filled_quantity"])*self.state["instruments"][o["symbol"]]["margin_rate"]) for o in self.state["orders"] if o["status"] in ACTIVE and not o["reduce_only"] and o["id"]!=order.get("id") and self.state["instruments"][o["symbol"]]["kind"]=="FUT")
        checks.extend(budget_checks(equity=equity,trade_risk=new_risk+position_risk,open_risk=new_risk+open_risk+other_risk,
            funding_needed=needed,funding_available=free,margin=account["margin_used"]+pending_margin+needed,
            stress=self._stress_loss(order),risk=self.state["risk"],futures=item["kind"]=="FUT"))
        check("option_model", item["kind"] != "OPT" or bool(self._greeks(item)), "Option model inputs are unavailable or invalid")
        return checks

    def _check(self, order):
        return next((c["reason"] for c in self._evaluate(order) if c["status"] == "FAIL"), None)

    def _decision(self, action, symbol, reason, *, order=None, checks=None, strategy=None, inputs=None, sizing=None, execution=None):
        identifier = str(uuid.uuid4())
        policy = copy.deepcopy(self.state["risk"])
        record = dict(id=identifier, session_id=self.state["session_id"], time=self.state["clock"],
            observed_at=datetime.now(IST).isoformat(), tick=self.state["tick"], source=self.state["source"],
            action=action, symbol=symbol, reason=reason, order_id=order["id"] if order else None,
            order=copy.deepcopy(order), strategy=copy.deepcopy(strategy), inputs=copy.deepcopy(inputs),
            quote=copy.deepcopy(self.state["quotes"].get(symbol)), instrument=copy.deepcopy(self.state["instruments"].get(symbol)),
            market_snapshot=copy.deepcopy(self.state["quotes"]),positions=copy.deepcopy(self.state["positions"]),
            account={k:rupees(v) if k!="drawdown_pct" else v for k,v in self._account().items()},
            risk_policy=dict(version="risk/1.0.0", config=policy,
                config_hash=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()),
            checks=copy.deepcopy(checks or []), sizing=copy.deepcopy(sizing), execution=copy.deepcopy(execution),
            valuation_model=dict(version="european-black-scholes/1.0.0",annual_interest_rate=.065,dividend_yield=0),
            execution_model=dict(mode=self.execution.mode,version=self.execution.version,charges_version="estimated-charges/1.0.0",assumptions=list(self.execution.assumptions)))
        self.new_decisions.append((identifier,self.state["session_id"],record["order_id"],json.dumps(record,allow_nan=False)))
        return identifier

    def decisions(self, before=None, limit=50, all_sessions=False):
        with self.lock:
            clauses, values = [], []
            if not all_sessions:
                clauses.append("session_id=?")
                values.append(self.state["session_id"])
            if before is not None:
                clauses.append("sequence<?")
                values.append(before)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self.db.execute("SELECT sequence,document FROM decisions"+where+" ORDER BY sequence DESC LIMIT ?",(*values,limit)).fetchall()
            return [dict(json.loads(row["document"]), sequence=row["sequence"]) for row in rows]

    def calculate_size(self, request):
        with self.atomic():
            result = self.size(request)
            result["decision_id"] = self._decision("SIZE",request["symbol"],result["explanation"],sizing=result)
            return result

    def preview(self, request):
        with self.lock:
            order = self._normalise(request)
            reason = self._check(order)
            item = self.state["instruments"][order["symbol"]]
            price = self._order_cost(order)
            charge = fees(item["kind"],order["side"],price*order["quantity"])
            return dict(allowed=reason is None, reason=reason, estimated_price=price,
                        estimated_fees=charge, estimated_stop_loss=round(abs(price-(order["stop_loss"] or price))*order["quantity"],2),
                        estimated_funding=round(price*order["quantity"]*(item["margin_rate"] if item["kind"]=="FUT" else 1)+charge["total"],2),
                        lot_size=item["lot_size"], mode="PAPER", checks=self._evaluate(order), execution_model=self.execution.version)

    def size(self, request):
        with self.lock:
            item = self.state["instruments"].get(request["symbol"])
            if not item:
                raise ValueError("Unknown instrument")
            lot = item["lot_size"]
            low, high = 0, min(100_000,10_000_000//lot, int(max(0,self._account()["equity"])/max(1,paise(self.state["quotes"][item["symbol"]]["ask"])*lot)*10)+1)
            while low < high:
                mid = (low+high+1)//2
                candidate = self._normalise(dict(request,quantity=mid*lot,reduce_only=False))
                if self._check(candidate) is None:
                    low = mid
                else:
                    high = mid-1
            reason = None if low else self._check(self._normalise(dict(request,quantity=lot,reduce_only=False)))
            boundary = self._check(self._normalise(dict(request,quantity=(low+1)*lot,reduce_only=False))) if low < 100_000 else "Sizing search safety ceiling"
            return dict(quantity=low*lot, lots=low, reason=reason, limiting_reason=boundary,
                        selected_quantity=request.get("quantity"), stop_loss=request.get("stop_loss"),
                        entry_reference=self._order_cost(self._normalise(dict(request,quantity=lot,reduce_only=False))),
                        equity=rupees(self._account()["equity"]),
                        risk_budget=rupees(int(self._account()["equity"]*self.state["risk"]["trade_pct"]/100)),
                        explanation=f"Largest eligible whole-lot quantity is {low*lot}; next lot blocked by: {boundary or 'search ceiling'}. Checks include stop distance, round-trip charges, slippage, funding, margin and portfolio risk.")

    def submit(self, request):
        with self.atomic():
            return copy.deepcopy(self._submit(request))

    def _submit(self, request):
        order = self._normalise(request)
        signature = hashlib.sha256(json.dumps({k:v for k,v in order.items() if k!="rationale" or v is not None},sort_keys=True).encode()).hexdigest()
        client_id = request.get("client_id") or str(uuid.uuid4())
        if not isinstance(client_id,str) or not 1 <= len(client_id) <= 100:
            raise ValueError("Invalid client identifier")
        for existing in self.state["orders"]:
            if existing["client_id"] == client_id:
                if existing["signature"] != signature:
                    raise ValueError("Client identifier already used for different order parameters")
                return existing
        order.update(id=str(uuid.uuid4()),client_id=client_id,signature=signature,
                     time=self.state["clock"],status="OPEN",filled_quantity=0,fill_notional=0,
                     average_price=0,fees=0,fee_breakdown={},submitted_at=time.time(),source=request.get("source","MANUAL"),reason="")
        checks = self._evaluate(order)
        reason = next((c["reason"] for c in checks if c["status"]=="FAIL"),None)
        sizing = None if order["reduce_only"] else self.size(order)
        strategy = request.get("strategy_context")
        order["strategy"] = copy.deepcopy(strategy)
        if reason:
            order.update(status="REJECTED",reason=reason)
            self._event("WARN","REJECTION",reason,dict(symbol=order["symbol"],order_id=order["id"]))
        order["decision_id"] = self._decision("REJECT" if reason else "ACCEPT",order["symbol"],
            reason or request.get("decision_reason") or ("Manual rationale: "+order["rationale"] if order.get("rationale") else None) or ("Reduce existing exposure" if order["reduce_only"] else "Manual entry requested; all applicable risk checks passed"),
            order=order,checks=checks,sizing=sizing,strategy=strategy,inputs=request.get("signal_inputs"))
        self.state["orders"].append(order)
        if not reason:
            self._event("INFO","ORDER",f"Paper {order['side']} order accepted for {order['symbol']}",dict(order_id=order["id"]))
            self._match(order)
        self._risk_halt()
        self._record_equity()
        return order

    def _match(self, order):
        if order["status"] not in ACTIVE:
            return
        checks = self._evaluate(order)
        reason = next((c["reason"] for c in checks if c["status"]=="FAIL"),None)
        if reason:
            order.update(status="CANCELLED",reason=reason)
            self._decision("CANCEL",order["symbol"],reason,order=order,checks=checks)
            self._event("WARN","CANCELLATION",f"Unfilled remainder cancelled: {reason}",dict(order_id=order["id"]))
            return
        item = self.state["instruments"][order["symbol"]]
        quote = self.state["quotes"][order["symbol"]]
        buy = order["side"] == "BUY"
        liquidity_key = order["symbol"]+":"+order["side"]
        proposal = self.execution.propose(order,item,quote,self.state["risk"]["slippage_bps"],self.state["used_liquidity"].get(liquidity_key,0))
        if not proposal.quantity:
            self._decision("WAIT",order["symbol"],proposal.reason,order=order,checks=checks)
            return
        price, count = proposal.price, proposal.quantity
        previous_notional = order["fill_notional"]
        cumulative = previous_notional + paise(price)*count
        charge = fees(item["kind"],order["side"],rupees(cumulative))
        previous_fee = paise(order["fee_breakdown"].get("total",0))
        fee = paise(charge["total"])-previous_fee
        dp_key = self.state["day"]+":"+order["symbol"]
        if item["kind"]=="EQ" and not buy and dp_key not in self.state["dp_charged"]:
            self.state["dp_charged"].append(dp_key)
            fee += paise(15.34)
            order["dp_fee"] = 15.34
        position = self.state["positions"].get(order["symbol"])
        realised = 0
        if order["reduce_only"]:
            allocation = position["basis"]*count//abs(position["quantity"])
            realised = (paise(price)*count-allocation)*(1 if position["quantity"]>0 else -1)
            position["basis"] -= allocation
            position["quantity"] += count if position["quantity"]<0 else -count
            if position["quantity"] == 0:
                del self.state["positions"][order["symbol"]]
        else:
            if not position:
                position = dict(quantity=0,basis=0,stop_loss=order["stop_loss"],take_profit=order["take_profit"],
                                entry_order=order["id"],opened=self.state["clock"],source=order["source"],strategy=copy.deepcopy(order.get("strategy")))
                self.state["positions"][order["symbol"]]=position
            position["quantity"] += count*(1 if buy else -1)
            position["basis"] += paise(price)*count
        delta = fill_cash_delta(item["kind"],count*(1 if buy else -1),paise(price),fee,realised if order["reduce_only"] else 0)
        # Futures gap losses may exceed cash: record the loss and halt, never erase it.
        self._ledger("FILL",delta,order["id"])
        order.update(fill_notional=cumulative,filled_quantity=order["filled_quantity"]+count,
                     fee_breakdown=charge,fees=rupees(paise(order["fees"])+fee))
        order["average_price"] = rupees(round(cumulative/order["filled_quantity"]))
        order["status"] = "FILLED" if order["filled_quantity"]==order["quantity"] else "PARTIAL"
        self.state["used_liquidity"][liquidity_key]=self.state["used_liquidity"].get(liquidity_key,0)+count
        fill_id=str(uuid.uuid4())
        self.new_fills.append((fill_id,order["id"],self.state["clock"],order["symbol"],order["side"],count,paise(price),fee,realised))
        self._event("INFO","FILL",f"{order['side']} {count} {order['symbol']} at ₹{price:,.2f}",dict(order_id=order["id"],fees=rupees(fee),realised=rupees(realised)))
        self._decision("FILL",order["symbol"],proposal.reason,order=order,checks=checks,strategy=order.get("strategy"),
            execution=dict(delay_ms=round(max(0,time.time()-order.get("submitted_at",time.time()))*1000,2),fill_id=fill_id,quantity=count,price=price,reference=proposal.reference,
                available_depth=proposal.available,slippage_bps=self.state["risk"]["slippage_bps"],fees=rupees(fee),realised=rupees(realised)))

    def cancel(self, order_id):
        with self.atomic():
            order = next((o for o in self.state["orders"] if o["id"]==order_id),None)
            if not order:
                raise ValueError("Order not found")
            if order["status"] not in ACTIVE:
                raise ValueError("Only pending remainders can be cancelled")
            order.update(status="CANCELLED",reason="Cancelled by user")
            self._decision("CANCEL",order["symbol"],order["reason"],order=order)
            self._event("INFO","CANCELLATION","Unfilled order remainder cancelled",dict(order_id=order_id))
            return copy.deepcopy(order)

    def _exit(self,symbol,source="EXIT"):
        p=self.state["positions"].get(symbol)
        if not p:
            raise ValueError("Position not found")
        for order in self.state["orders"]:
            if order["symbol"]==symbol and order["status"] in ACTIVE:
                order.update(status="CANCELLED",reason="Replaced by position exit")
                self._decision("CANCEL",order["symbol"],order["reason"],order=order)
        return self._submit(dict(symbol=symbol,side="SELL" if p["quantity"]>0 else "BUY",quantity=abs(p["quantity"]),
                                 order_type="MARKET",reduce_only=True,source=source,client_id=str(uuid.uuid4()),
                                 strategy_context=p.get("strategy"),decision_reason={"STOP":"Protective stop triggered","TARGET":"Profit target triggered","OPTION_CUTOFF":"Options intraday exit cutoff","EXPIRY_CUTOFF":"Derivative expiry exit cutoff","RISK_EXIT":self.state["halt_reason"],"TREND_EXIT":"Bearish strategy crossover","FLATTEN":"User requested account flatten","EXIT":"User requested position close"}.get(source,source)))

    def close_position(self,symbol):
        with self.atomic():
            return copy.deepcopy(self._exit(symbol))

    def flatten(self):
        with self.atomic():
            self.state["paused"]=True
            for order in self.state["orders"]:
                if order["status"] in ACTIVE and not order["reduce_only"]:
                    order.update(status="CANCELLED",reason="Paused and close requested")
                    self._decision("CANCEL",order["symbol"],order["reason"],order=order)
            results=[copy.deepcopy(self._exit(s,"FLATTEN")) for s in list(self.state["positions"])]
            self._event("WARN","CONTROL","New entries paused; simulated exits requested. Check fills and remaining positions.")
            return results

    def set_paused(self,paused):
        with self.atomic():
            if not paused and self.state["halt_reason"]:
                raise ValueError("Risk halt requires a new eligible session or explicit paper-policy review")
            self.state["paused"]=paused
            if paused:
                for o in self.state["orders"]:
                    if o["status"] in ACTIVE and not o["reduce_only"]:
                        o.update(status="CANCELLED",reason="New entries paused")
                        self._decision("CANCEL",o["symbol"],o["reason"],order=o)
            self._event("INFO","CONTROL","New entries paused; protective exits remain active." if paused else "New entries resumed.")

    def configure_risk(self,settings):
        with self.atomic():
            if self.state["positions"] or any(o["status"] in ACTIVE for o in self.state["orders"]):
                raise ValueError("Risk policy can only change while flat with no pending orders")
            if set(settings)!=set(DEFAULT_RISK):
                raise ValueError("Complete risk policy required")
            values={key:float(value) for key,value in settings.items()}
            if not all(math.isfinite(v) and 0<v<=50 for v in values.values()):
                raise ValueError("Risk values must be finite and between 0 and 50")
            if not values["trade_pct"]<=values["open_pct"]<=values["daily_pct"]<=values["weekly_pct"]<=values["drawdown_pct"]:
                raise ValueError("Risk limits must increase from trade through drawdown")
            self.state["risk"]=values
            self._event("WARN","POLICY","Paper risk policy updated",values)
            self._risk_halt()

    def change_capital(self,amount):
        value=paise(amount)
        if not 10_000 <= value <= 100_000_000_000:
            raise ValueError("Virtual capital must be between ₹100 and ₹100 crore")
        with self.atomic():
            if self.state["positions"] or any(o["status"] in ACTIVE for o in self.state["orders"]):
                raise ValueError("Capital changes require a flat account and no pending orders")
            delta=value-self.state["capital"]
            if self.state["cash"]+delta<0:
                raise ValueError("Withdrawal exceeds virtual cash")
            self._ledger("CAPITAL_ADJUSTMENT",delta,"virtual-capital-adjustment")
            for field in ("capital","equity_peak","day_start","week_start"):
                self.state[field]+=delta
            self._event("INFO","CAPITAL",f"Virtual capital allocation changed to ₹{amount:,.2f}; existing P&L preserved.")
            self._risk_halt()
            self._record_equity()

    def configure_strategy(self,enabled,symbol="RELIANCE",fast=5,slow=20,stop_pct=2):
        with self.atomic():
            if symbol not in self.state["instruments"] or not 2<=fast<slow<=100 or not .1<=stop_pct<=20:
                raise ValueError("Invalid strategy configuration")
            strategy=self.state["strategies"][0]
            strategy.update(enabled=enabled,symbol=symbol,fast=fast,slow=slow,stop_pct=stop_pct,
                            status="Warming up" if enabled else "Disabled",evaluated_tick=-1)
            self._decision("CONFIGURE_STRATEGY",symbol,"Strategy configuration saved",strategy=identity(strategy))
            self._event("INFO","STRATEGY","Trend baseline enabled for paper trading" if enabled else "Trend baseline disabled",dict(symbol=symbol))

    def new_session(self,amount):
        value=paise(amount)
        if not 10_000<=value<=100_000_000_000:
            raise ValueError("Virtual capital must be between ₹100 and ₹100 crore")
        with self.atomic():
            if self.state["positions"] or any(o["status"] in ACTIVE for o in self.state["orders"]):
                raise ValueError("A new session requires a flat account with no pending orders")
            self.db.execute("INSERT INTO archives(ended,document) VALUES(?,?)",(self.state["clock"],json.dumps(self.state)))
            self._ledger("SESSION_CLOSE",-self.state["cash"],"archived-paper-session")
            self.state["fill_offset"]=self.db.execute("SELECT COALESCE(MAX(rowid),0) FROM fills").fetchone()[0]
            self.state["journal_offset"]=self.db.execute("SELECT COALESCE(MAX(id),0) FROM journal").fetchone()[0]
            self.state.update(session_id=str(uuid.uuid4()),capital=value,equity_peak=value,day_start=value,week_start=value,
                              positions={},orders=[],history=[],prices={},used_liquidity={},dp_charged=[],
                              paused=False,halt_reason=None)
            self.state["strategies"][0].update(enabled=False,status="Disabled",last_signal="Waiting for observations",evaluated_tick=-1)
            if self.state["source"]=="DEMO":
                clock=datetime.now(IST).replace(hour=9,minute=30,second=0,microsecond=0)
                self.state.update(clock=clock.isoformat(),tick=0,day=clock.date().isoformat(),week=str(clock.isocalendar()[:2]))
                self.state["instruments"]=demo_instruments(clock)
                self._demo_quotes()
            self._record_instruments()
            self._ledger("CAPITAL",value,"new-virtual-session")
            self._event("INFO","SESSION","New paper session started. Previous results retained in the session archive.",dict(capital=amount))
            self._record_equity()

    def _strategies(self,updated=None):
        for strategy in self.state["strategies"]:
            if not strategy["enabled"] or strategy["evaluated_tick"] == self.state["tick"]:
                continue
            symbol = strategy["symbol"]
            if updated is not None and symbol not in updated:
                continue
            decision = evaluate(strategy,self.state["prices"].get(symbol,[]))
            context = identity(strategy)
            if self.state["paused"] or self.state["halt_reason"] or not self._fresh(symbol):
                decision.update(signal="HOLD",reason=self.state["halt_reason"] or ("New entries paused" if self.state["paused"] else "Strategy quote is stale"))
            signal = decision["signal"]
            strategy["status"] = "Warming up" if signal=="WARMUP" else "Observing"
            strategy["last_signal"] = decision["reason"]
            self._decision(signal,symbol,decision["reason"],strategy=context,inputs=decision["inputs"])
            if signal=="BUY" and symbol not in self.state["positions"]:
                quote=self.state["quotes"][symbol]
                stop=round(quote["bid"]*(1-strategy["stop_pct"]/100),2)
                candidate=dict(symbol=symbol,side="BUY",order_type="MARKET",stop_loss=stop,
                    take_profit=round(quote["ask"]+(quote["ask"]-stop)*strategy["reward_ratio"],2))
                sized=self.size(candidate)
                if sized["quantity"]:
                    result=self._submit(dict(candidate,quantity=sized["quantity"],source="TREND",
                        client_id=f"trend-{self.state['session_id']}-{self.state['source']}-{self.state['tick']}",
                        strategy_context=context,decision_reason=decision["reason"],signal_inputs=decision["inputs"]))
                    strategy["last_signal"]=f"Bullish crossover · {result['status'].lower()}"
                else:
                    strategy["last_signal"]="Signal skipped: "+str(sized["reason"])
                    self._decision("SKIP",symbol,strategy["last_signal"],strategy=context,inputs=decision["inputs"],sizing=sized)
            elif signal=="BUY":
                strategy["last_signal"]="Signal skipped: an existing position already owns this instrument"
                self._decision("SKIP",symbol,strategy["last_signal"],strategy=context,inputs=decision["inputs"])
            elif signal=="EXIT":
                p=self.state["positions"].get(symbol)
                if p and p["source"]=="TREND":
                    self._exit(symbol,"TREND_EXIT")
                    strategy["last_signal"]="Bearish crossover · exit requested"
                else:
                    strategy["last_signal"]="Signal skipped: no strategy-owned position to close"
                    self._decision("SKIP",symbol,strategy["last_signal"],strategy=context,inputs=decision["inputs"])
            strategy["evaluated_tick"]=self.state["tick"]

    def _risk_halt(self):
        account=self._account()
        reason=None
        if account["day_pnl"] < -self.state["day_start"]*self.state["risk"]["daily_pct"]/100:
            reason="Daily loss threshold breached"
        if account["week_pnl"] < -self.state["week_start"]*self.state["risk"]["weekly_pct"]/100:
            reason="Weekly loss threshold breached"
        if account["drawdown_pct"] >= self.state["risk"]["drawdown_pct"]:
            reason="Drawdown threshold breached"
        if account["margin_used"] > account["equity"]*self.state["risk"]["margin_pct"]/100:
            reason="Margin utilisation threshold breached"
        if account["cash"]<0 or account["equity"]<=0:
            reason="Virtual account funding exhausted; loss exceeded available equity"
        if reason and not self.state["halt_reason"]:
            self.state["halt_reason"]=reason
            self.state["paused"]=True
            for order in self.state["orders"]:
                if order["status"] in ACTIVE and not order["reduce_only"]:
                    order.update(status="CANCELLED",reason=reason)
                    self._decision("CANCEL",order["symbol"],order["reason"],order=order)
            self._event("ERROR","RISK_HALT",reason+". Entries blocked; simulated position exits requested.")
            for symbol in list(self.state["positions"]):
                existing_exit=any(o["symbol"]==symbol and o["reduce_only"] and o["status"] in ACTIVE for o in self.state["orders"])
                if not existing_exit:
                    self._exit(symbol,"RISK_EXIT")

    def _record_equity(self):
        account=self._account()
        self.state["equity_peak"]=max(self.state["equity_peak"],account["equity"])
        self.state["history"].append(dict(time=self.state["clock"],equity=rupees(account["equity"]),pnl=rupees(account["pnl"])))
        self.state["history"]=self.state["history"][-360:]

    def _roll_session(self,new_clock):
        old_clock=datetime.fromisoformat(self.state["clock"])
        if new_clock.date()!=old_clock.date():
            for order in self.state["orders"]:
                if order["status"] in ACTIVE:
                    order.update(status="CANCELLED",reason="DAY order expired at session boundary")
                    self._decision("CANCEL",order["symbol"],order["reason"],order=order)
            for symbol,p in self.state["positions"].items():
                if self.state["instruments"][symbol]["kind"]=="FUT":
                    value=self._mark(symbol,p["quantity"])*abs(p["quantity"])
                    pnl=(value-p["basis"])*(1 if p["quantity"]>0 else -1)
                    self._ledger("FUTURES_MTM",pnl,symbol)
                    p["basis"]=value
                    self._event("INFO","SETTLEMENT",f"Simulated daily futures MTM for {symbol}",dict(amount=rupees(pnl)))
            self.state["day_start"]=self._account()["equity"]
            self.state["day"]=new_clock.date().isoformat()
            self.state["dp_charged"]=[]
            if str(new_clock.isocalendar()[:2])!=self.state["week"]:
                self.state["week_start"]=self._account()["equity"]
                self.state["week"]=str(new_clock.isocalendar()[:2])
            if self.state["halt_reason"]=="Daily loss threshold breached":
                self.state["halt_reason"]=None
            self._event("INFO","SESSION","New paper session. Entry pause remains until you resume.")
        self.state["clock"]=new_clock.isoformat()

    def step(self,count=1):
        if self.state["source"]!="DEMO":
            raise ValueError("Manual stepping is only available for synthetic data")
        for _ in range(count):
            with self.atomic():
                clock=datetime.fromisoformat(self.state["clock"])+timedelta(minutes=1)
                if (clock.hour,clock.minute)>(15,30):
                    clock=(clock+timedelta(days=1)).replace(hour=9,minute=15)
                    while clock.weekday()>=5:
                        clock+=timedelta(days=1)
                self._roll_session(clock)
                self.state["tick"]+=1
                self.state["used_liquidity"]={}
                self._demo_quotes()
                self._on_quotes()

    def apply_quotes(self,updates,advance=0,clock=None,expected_source=None):
        with self.atomic():
            if expected_source and expected_source!=self.state["source"]:
                return
            if clock:
                self._roll_session(clock)
            self.state["tick"]+=advance
            for symbol,change in updates.items():
                if symbol not in self.state["instruments"]:
                    continue
                quote=self.state["quotes"].setdefault(symbol,{})
                quote.update(change)
                numeric = ("last","bid","ask","spot")
                if any(not isinstance(quote.get(k),(int,float)) or not math.isfinite(quote[k]) or quote[k]<=0 for k in numeric):
                    raise ValueError("Invalid quote: positive finite prices and underlying spot required")
                if quote["bid"] > quote["ask"] or any(type(quote.get(k)) is not int or quote[k]<0 for k in ("bid_quantity","ask_quantity")):
                    raise ValueError("Invalid quote: crossed prices or invalid executable depth")
                if quote.get("iv") is not None and (not isinstance(quote["iv"],(int,float)) or not math.isfinite(quote["iv"]) or quote["iv"]<=0):
                    raise ValueError("Invalid quote: volatility must be positive and finite")
                try:
                    stamp=datetime.fromisoformat(quote["exchange_time"])
                    if stamp.tzinfo is None: raise ValueError()
                except (KeyError,TypeError,ValueError):
                    raise ValueError("Invalid quote: timezone-aware exchange timestamp required") from None
                received=change.get("received_at",time.time())
                if not isinstance(received,(int,float)) or not math.isfinite(received) or received>time.time()+2:
                    raise ValueError("Invalid quote receipt timestamp")
                quote["received_at"]=received
                for side in ("BUY","SELL"):
                    self.state["used_liquidity"].pop(symbol+":"+side,None)
                self.new_ticks.append((self.state["clock"],self.state["source"],symbol,json.dumps(quote,allow_nan=False)))
            self.state["feed_error"]=None
            self._on_quotes(set(updates))

    def _on_quotes(self,updated=None):
        for symbol in self.state["instruments"]:
            if (updated is None or symbol in updated) and self._fresh(symbol):
                history=self.state["prices"].setdefault(symbol,[])
                history.append(self.state["quotes"][symbol]["last"])
                self.state["prices"][symbol]=history[-160:]
        # Protective exits have priority over pending entries and strategy signals.
        clock=datetime.fromisoformat(self.state["clock"])
        for symbol,p in list(self.state["positions"].items()):
            if not self._fresh(symbol):
                continue
            item=self.state["instruments"][symbol]
            mark=self._mark(symbol,p["quantity"])/100
            sign=1 if p["quantity"]>0 else -1
            source=None
            if sign*(mark-p["stop_loss"])<=0:
                source="STOP"
            elif p["take_profit"] and sign*(mark-p["take_profit"])>=0:
                source="TARGET"
            elif item["kind"]=="OPT" and ((clock.hour,clock.minute)>=(15,20) or datetime.fromisoformat(p["opened"]).date()!=clock.date()):
                source="OPTION_CUTOFF"
            elif item.get("expiry") and clock>=datetime.fromisoformat(item["expiry"]).replace(hour=14,minute=45):
                source="EXPIRY_CUTOFF"
            existing_exit=any(o["symbol"]==symbol and o["reduce_only"] and o["status"] in ACTIVE for o in self.state["orders"])
            if source and not existing_exit:
                self._exit(symbol,source)
        self._risk_halt()
        for order in self.state["orders"]:
            if order["status"] in ACTIVE:
                self._match(order)
        self._strategies(updated)
        self._risk_halt()
        self._record_equity()

    def _greeks(self,item):
        if item["kind"]!="OPT":
            return None
        quote=self.state["quotes"].get(item["symbol"],{})
        spot=quote.get("spot")
        if not spot:
            return None
        years=(datetime.fromisoformat(item["expiry"])-datetime.fromisoformat(self.state["clock"])).total_seconds()/(365*86400)
        if years<=0:
            return None
        iv=quote.get("iv") or implied_volatility((quote["bid"]+quote["ask"])/2,spot,item["strike"],years,item["option_type"])
        if not iv:
            return None
        return {k:round(v,6) for k,v in option_greeks(spot,item["strike"],years,iv,item["option_type"]).items()}

    def market_error(self,message):
        with self.atomic():
            if self.state["feed_error"]!=message:
                self._event("ERROR","FEED",message)
            self.state["feed_error"]=message

    def switch_source(self,source,instruments=None,quotes=None):
        with self.atomic():
            if self.state["positions"] or any(o["status"] in ACTIVE for o in self.state["orders"]):
                raise ValueError("Changing market source requires a flat account and no pending orders")
            if source not in {"DEMO","KITE"}:
                raise ValueError("Unknown market source")
            self.state["source"]=source
            self.state["quotes"]=quotes or {}
            self.state["prices"]={}
            self.state["used_liquidity"]={}
            self.state["feed_error"]=None
            self.state["strategies"][0].update(enabled=False,status="Disabled")
            if source=="DEMO":
                now=datetime.now(IST).replace(hour=9,minute=30,second=0,microsecond=0)
                self._roll_session(now)
                self.state["instruments"]=demo_instruments(now)
                self._demo_quotes()
            else:
                self._roll_session(datetime.now(IST))
                self.state["instruments"]=instruments
            self._record_instruments()
            self._event("WARN","SOURCE",f"Market source changed to {source}; execution remains paper-only.")

    def option_groups(self):
        groups = {}
        clock = datetime.fromisoformat(self.state["clock"])
        for symbol,item in self.state["instruments"].items():
            if item["kind"] != "OPT": continue
            q = self.state["quotes"].get(symbol,{})
            p = self.state["positions"].get(symbol,{})
            quantity = p.get("quantity",0)
            greek = self._greeks(item)
            group = groups.setdefault(item["underlying"],dict(underlying=item["underlying"],delta=0.,gamma=0.,theta=0.,vega=0.,legs=[]))
            if greek:
                for key in ("delta","gamma","theta","vega"): group[key] += greek[key]*quantity
            spread = q.get("ask",0)-q.get("bid",0)
            spread_pct = spread/max(.01,(q.get("ask",0)+q.get("bid",0))/2)*100
            depth = min(q.get("bid_quantity",0),q.get("ask_quantity",0))
            group["legs"].append(dict(symbol=symbol,quantity=quantity,expiry=item["expiry"],
                days_to_expiry=max(0,(datetime.fromisoformat(item["expiry"])-clock).total_seconds()/86400),
                greeks=greek,spread=round(spread,2),spread_pct=round(spread_pct,2),
                bid_quantity=q.get("bid_quantity",0),ask_quantity=q.get("ask_quantity",0),lot_size=item["lot_size"],
                fresh=self._fresh(symbol),liquidity="STALE" if not self._fresh(symbol) else "THIN" if depth<item["lot_size"] or spread_pct>5 else "QUOTED"))
        return list(groups.values())

    def snapshot(self):
        with self.lock:
            account=self._account()
            positions=[]
            totals=dict(delta=0.,gamma=0.,theta=0.,vega=0.)
            exposure={}
            for symbol,p in self.state["positions"].items():
                item=self.state["instruments"][symbol]
                mark=self._mark(symbol,p["quantity"])
                sign=1 if p["quantity"]>0 else -1
                pnl=(mark*abs(p["quantity"])-p["basis"])*sign
                greek=self._greeks(item)
                if greek:
                    for key in totals:
                        totals[key]+=greek[key]*p["quantity"]
                delta=p["quantity"]*(greek["delta"] if greek else 1)
                exposure[item["underlying"]]=exposure.get(item["underlying"],0)+delta
                positions.append(dict(symbol=symbol,name=item["name"],kind=item["kind"],quantity=p["quantity"],
                    average_price=rupees(round(p["basis"]/abs(p["quantity"]))),mark=rupees(mark),
                    unrealised=rupees(pnl),stop_loss=p["stop_loss"],take_profit=p["take_profit"],
                    planned_risk=rupees(self._position_risk(symbol,p)),source=p["source"],opened=p["opened"],greeks=greek))
            market=[]
            for symbol,item in self.state["instruments"].items():
                quote=self.state["quotes"].get(symbol,{})
                prices=self.state["prices"].get(symbol,[])
                market.append(dict(item,**quote,fresh=self._fresh(symbol),greeks=self._greeks(item),
                                   change_pct=round((quote.get("last",item["base"])/max(.01,prices[0] if prices else quote.get("last",1))-1)*100,2),
                                   sparkline=prices[-40:]))
            journal=[dict(row) for row in self.db.execute("SELECT * FROM journal WHERE id>? ORDER BY id DESC LIMIT 120",(self.state.get("journal_offset",0),))]
            for event in journal:
                event["details"]=json.loads(event["details"])
            fills=[dict(row) for row in self.db.execute("SELECT * FROM fills WHERE rowid>? ORDER BY rowid DESC LIMIT 200",(self.state.get("fill_offset",0),))]
            for fill in fills:
                for key in ("price","fees","realised"):
                    fill[key]=rupees(fill[key])
            view_account={key:rupees(value) if key!="drawdown_pct" else round(value,3) for key,value in account.items()}
            costs=rupees(self.db.execute("SELECT COALESCE(SUM(fees),0) FROM fills WHERE rowid>?",(self.state.get("fill_offset",0),)).fetchone()[0])
            return dict(mode="PAPER",source=self.state["source"],clock=self.state["clock"],tick=self.state["tick"],
                paused=self.state["paused"],halt_reason=self.state["halt_reason"],auto_feed=self.state["auto_feed"],
                feed_error=self.state["feed_error"],account=dict(view_account,total_fees=costs),
                risk=self.state["risk"],positions=positions,market=market,
                orders=list(reversed(copy.deepcopy(self.state["orders"][-200:]))),fills=fills,journal=journal,
                session_id=self.state["session_id"],decisions=self.decisions(limit=20),option_groups=self.option_groups(),
                strategy_specification=SPECIFICATION,execution_model=dict(version=self.execution.version,assumptions=list(self.execution.assumptions)),
                strategies=copy.deepcopy(self.state["strategies"]),history=copy.deepcopy(self.state["history"]),
                greeks={key:round(value,4) for key,value in totals.items()},underlying_delta=exposure,
                reconcile=self.reconcile(),live_trading_available=False,
                limitations=["Synthetic prices and illustrative demo contract sizes" if self.state["source"]=="DEMO" else "Live quotes; all orders and balances are simulated",
                    "Fees, margin, depth and fills are estimates, not broker-confirmed executions",
                    "Long equities, long options and both directions in futures; uncovered option shorts and spreads are not enabled",
                    "Options Greeks use a European model with fixed interest rate and zero dividend yield",
                    "Trend baseline is unvalidated; no LLM or profitability prediction is connected"])
