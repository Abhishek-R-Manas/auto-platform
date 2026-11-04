"""Execution contract and the only installed implementation: a paper simulator.

Adapters return proposals; the OMS owns risk approval and accounting. No network
client, credential, or broker route is part of this module.
"""
from dataclasses import dataclass
from typing import Protocol

from .finance import tick_price


@dataclass(frozen=True)
class ExecutionResult:
    quantity: int
    price: float | None
    reference: float
    available: int
    reason: str


class ExecutionAdapter(Protocol):
    mode: str
    version: str

    def propose(self, order, instrument, quote, slippage_bps, used) -> ExecutionResult: ...


class PaperExecutionAdapter:
    mode = "PAPER"
    version = "top-of-book/1.0.0"
    assumptions = ("Top-of-book depth only; no exchange queue priority",
                   "Depth renews on each quote observation",
                   "Adverse basis-point slippage and side-aware tick rounding",
                   "Limit executions never exceed the requested limit",
                   "Charges and margin are estimates")

    def propose(self, order, instrument, quote, slippage_bps, used):
        buy = order["side"] == "BUY"
        reference = quote["ask"] if buy else quote["bid"]
        available = max(0, int(quote["ask_quantity"] if buy else quote["bid_quantity"]) - used)
        if order["order_type"] == "LIMIT" and (
                (buy and reference > order["limit_price"]) or
                (not buy and reference < order["limit_price"])):
            return ExecutionResult(0, None, reference, available, "Waiting for an executable limit quote")
        quantity = min(order["quantity"] - order["filled_quantity"], available)
        quantity = quantity // instrument["lot_size"] * instrument["lot_size"]
        if not quantity:
            return ExecutionResult(0, None, reference, available, "Insufficient unconsumed depth for one whole lot")
        price = tick_price(reference * (1 + (1 if buy else -1) * slippage_bps / 10000),
                           instrument["tick_size"], order["side"])
        if order["order_type"] == "LIMIT":
            price = min(price, order["limit_price"]) if buy else max(price, order["limit_price"])
        return ExecutionResult(quantity, price, reference, available, "Simulated executable top-of-book fill")
