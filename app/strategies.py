"""Pure, versioned strategy rules shared by callers of the controlled OMS."""
import hashlib
import json

VERSION = "trend-crossover/1.0.0"
SPECIFICATION = ("After slow+1 fresh quote observations, buy a bullish fast/slow simple "
                 "moving-average crossover; exit strategy-owned positions on a bearish "
                 "crossover. Long only. Stop is stop_pct below bid; target is reward_ratio "
                 "times the ask-to-stop distance. Size through the account risk service.")


def identity(strategy):
    config = {key: strategy[key] for key in ("symbol", "fast", "slow", "stop_pct", "reward_ratio")}
    return dict(id=strategy["id"], version=VERSION, config=config,
                config_hash=hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())


def evaluate(strategy, observations):
    fast, slow = strategy["fast"], strategy["slow"]
    inputs = dict(observations=list(observations[-slow-1:]), required=slow+1)
    if len(observations) < slow+1:
        return dict(signal="WARMUP", reason=f"Need {slow+1} observations; have {len(observations)}", inputs=inputs)
    current_fast = sum(observations[-fast:])/fast
    current_slow = sum(observations[-slow:])/slow
    old_fast = sum(observations[-fast-1:-1])/fast
    old_slow = sum(observations[-slow-1:-1])/slow
    inputs.update(current_fast=current_fast, current_slow=current_slow, old_fast=old_fast, old_slow=old_slow)
    if old_fast <= old_slow and current_fast > current_slow:
        return dict(signal="BUY", reason="Fast average crossed above slow average", inputs=inputs)
    if old_fast >= old_slow and current_fast < current_slow:
        return dict(signal="EXIT", reason="Fast average crossed below slow average", inputs=inputs)
    return dict(signal="HOLD", reason="No moving-average crossover; remain at current exposure", inputs=inputs)
