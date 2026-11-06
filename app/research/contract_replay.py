"""Fixed-basket policy on the shared replay engine; no separate accounting or broker path."""
import copy
from datetime import timedelta

from .catalog import aware, iso
from .replay import Replay
from ..charges import estimate
from ..finance import paise, tick_price


def arrival_rows(rows, protocol):
    result = []
    for original in rows:
        row = copy.deepcopy(original)
        exchange = aware(row.get("exchange_time") or row["timestamp"])
        received = row.get("received_at")
        available = row.get("available_at")
        if protocol["clock_mode"] == "RECORDED_RECEIPT":
            if not received or not available or not exchange <= aware(received) <= aware(available):
                raise ValueError("Recorded exchange, receipt and availability clocks are required")
            row["timestamp"] = iso(available)
        elif received or available:
            raise ValueError("Recorded arrival clocks cannot be discarded for an assumed-clock replay")
        row["exchange_time"] = iso(exchange)
        row["open_at"] = row["timestamp"]
        result.append(row)
    return sorted(result, key=lambda r: (aware(r["timestamp"]), r["instrument_id"]))


class ContractReplay(Replay):
    def __init__(self, dataset, config, emit, state=None):
        super().__init__(dataset, config, emit, state)
        self.protocol = config["contract_protocol"]
        self.owner = config["strategies"][0]["id"]

    def fresh(self, identifier):
        quote = self.quote(identifier)
        if not super().fresh(identifier):
            return False
        at = aware(self.state["clock"])
        stamp = aware(quote.get("exchange_time") or quote["timestamp"])
        return 0 <= (at-stamp).total_seconds() <= self.config["execution"]["max_quote_age_seconds"] and (
            not quote.get("available_at") or aware(quote["available_at"]) <= at)

    def fee_profile(self):
        profile = super().fee_profile()
        multiplier = self.protocol["fee_multiplier"]
        return {k: v*multiplier if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                for k, v in profile.items()}

    def strategy_exits_enabled(self, spec):
        return False

    def protected_exits(self, rows):
        # The protocol has no implicit per-leg stop/target; independent account halts remain.
        return

    def enter(self, spec, direction=1, selected=None):
        local = self.state["local"][self.owner]
        if local.get("entry_attempted"):
            self.event("SKIP", "One-shot protocol already attempted its scheduled entry", strategy=self.owner)
            return
        local["entry_attempted"] = True
        if aware(self.state["clock"]) != aware(self.protocol["entry_at"]):
            self.event("SKIP", "No exact scheduled decision observation; late entry is forbidden", strategy=self.owner)
            return
        return super().enter(spec, direction, selected)

    def entry_checks(self, spec, legs, exclude=None):
        checks = super().entry_checks(spec, legs, exclude)
        at = aware(self.state["clock"])
        active = aware(self.protocol["entry_at"]) <= at < min(aware(self.protocol["entry_deadline"]), aware(self.protocol["exit_at"]))
        checks.append(dict(name="contract_entry_window", status="PASS" if active else "FAIL",
                           reason="Frozen entry window; no entry at or after exit"))
        previews = []
        quality = self.fresh(self.protocol["reference_id"])
        units = self.protocol["units"]
        cost = 0
        for definition in spec["legs"]:
            identifier = definition["instrument_id"]
            q = self.quote(identifier)
            if not self.fresh(identifier):
                quality = False
                continue
            mid = (q["ask"]+q["bid"])/2
            spread = (q["ask"]-q["bid"])/mid*10000
            quality = quality and spread <= self.protocol["max_spread_bps"] and min(q["bid_quantity"] or 0, q["ask_quantity"] or 0) >= self.protocol["min_depth_units"]
            item = self.terms(identifier)
            quality = quality and item["lot_size"] == self.protocol["lot_size"] and paise(item["tick_size"]) == self.protocol["tick_size_paise"]
            side = "BUY" if definition["ratio"] > 0 else "SELL"
            reference = q["ask"] if side == "BUY" else q["bid"]
            slip = self.config["risk"]["slippage_bps"]/10000
            price = tick_price(reference*(1+slip if side == "BUY" else 1-slip), item["tick_size"], side)
            held = [lot for _, _, lot in self.lots(book=self.owner, identifier=identifier)]
            if held:
                average = sum(l["price"]*abs(l["quantity"]) for l in held)/sum(abs(l["quantity"]) for l in held)/100
                price = max(price, average) if side == "BUY" else min(price, average)
            previews.append(dict(kind=item["option_type"], side=side, strike_paise=paise(item["strike"]), premium_paise=paise(price)))
            for trade_side in ("BUY", "SELL"):
                cost += paise(estimate("OPT", trade_side, price*units, product="DERIVATIVE", profile=self.fee_profile())["total"])
        checks.append(dict(name="contract_quote_quality", status="PASS" if quality else "FAIL",
                           reason="All fixed legs and reference require fresh data, dated terms and declared spread/depth"))
        bounded = False
        if len(previews) == len(spec["legs"]):
            from ..contracts import expiry_payoff
            bound = expiry_payoff(previews, units, max(cost, self.protocol["estimated_costs_paise"]))
            bounded = bound["max_loss_paise"] <= self.protocol["max_expiry_loss_paise"]
        checks.append(dict(name="contract_expiry_loss_budget", status="PASS" if bounded else "FAIL",
                           reason="Adverse quote/filled-price complete-basket expiry loss and estimated round-trip costs must fit the frozen budget"))
        return checks

    def order_eligible(self, order, rows):
        if order["reduce_only"] and order["side"] == "SELL":
            item = self.terms(order["instrument_id"])
            shorts = [lot for _, identifier, lot in self.lots(book=self.owner)
                      if lot["quantity"] < 0 and self.terms(identifier)["option_type"] == item["option_type"]]
            if shorts:
                self.event("WAIT_PROTECTION", "Close short options before releasing their hedge", strategy=self.owner, order_id=order["id"])
                return False
        return True

    def match(self, rows):
        at = aware(self.state["clock"])
        local = self.state["local"][self.owner]
        if at >= aware(self.protocol["exit_at"]):
            local["exit_requested"] = True
            self.close_book(self.owner, "Frozen scheduled basket exit; cancel entry remainders")
        elif at >= aware(self.protocol["entry_deadline"]):
            pending = [o for o in self.state["orders"].values() if not o["reduce_only"] and o["status"] in {"PENDING_ACK", "OPEN", "PARTIAL"}]
            if pending:
                for order in pending:
                    self.cancel(order, "Frozen entry window expired")
                local["entry_aborted"] = True
                self.close_book(self.owner, "Incomplete basket; unwind after entry-window expiry")
        # Short buybacks precede hedge sales; positive entry hedges precede negative entries.
        self.state["orders"] = dict(sorted(self.state["orders"].items(),
            key=lambda pair: (not pair[1]["reduce_only"], pair[1]["side"] != "BUY", pair[0])))
        super().match(rows)
