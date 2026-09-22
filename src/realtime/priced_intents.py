"""MNQ priced-intent bridge for offline realtime execution.

Provides an offline, causal, fail-closed bridge to transport prices from mechanical
backtest strategies (such as SmcFvgStrategy) to realtime OrderIntents without enabling live.

Design & Invariants:
1. Conservative tick rounding:
   - For LONG: entry is rounded up (ceil), stop is rounded down (floor),
     ensuring stop distance (entry - stop) is never reduced and risk is never understated;
     target is rounded down (floor).
   - For SHORT: entry is rounded down (floor), stop is rounded up (ceil),
     ensuring stop distance (stop - entry) is never reduced and risk is never understated;
     target is rounded up (ceil).
   - Bracket integrity (stop < entry < target for long, target < entry < stop for short)
     is strictly verified after rounding.
2. Unambiguous identity linkage:
   - Context is keyed strictly by canonical Signal identity (source, event_id).
   - No future state, no ambiguous timestamp searches.
3. Fail-closed controls:
   - Non-MNQ symbols are rejected.
   - Non-finite or non-positive prices are rejected.
   - Missing, conflicting duplicate, or already-consumed contexts raise ValueError.
   - Identity, action, or origin mismatch between Signal, RiskDecision, and OrderIntent fails closed.
4. Clean separation:
   - default_intent_factory remains priceless and unchanged.
   - PracticeExecutionAdapter retains sole responsibility for risk check, 1-micro clamp,
     allowlist, cutoff, and broker gateway routing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from src.backtest.history import Bar as BacktestBar
from src.backtest.markets import MNQ, MarketSpec
from src.backtest.smc_fvg import SmcFvgStrategy
from src.backtest.strategy import Signal as BacktestSignal
from src.realtime.clock import Clock
from src.realtime.events import (
    Bar as RealtimeBar,
    CanonicalEvent,
    OrderIntent,
    RiskDecision,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    Signal as RealtimeSignal,
    identity_key,
    is_authorized,
)
from src.realtime.interfaces import (
    require_order_intent,
    require_risk_decision,
    require_signal,
)

MNQ_SYMBOL = "MNQ"
MNQ_TICK_SIZE = 0.25
MNQ_DOLLARS_PER_POINT = 2.0


def round_mnq_prices_conservative(
    action: str,
    raw_entry: float,
    raw_stop: float,
    raw_target: float,
    tick_size: float = MNQ_TICK_SIZE,
) -> tuple[float, float, float]:
    """Applies conservative tick rounding for MNQ order brackets.

    Policy:
    - Long:
      - entry: rounded UP (ceil to tick), so entry is not lowered.
      - stop: rounded DOWN (floor to tick), expanding stop distance (entry - stop)
        so dollar risk is never understated.
      - target: rounded DOWN (floor to tick), conservative target distance.
    - Short:
      - entry: rounded DOWN (floor to tick), so entry is not raised.
      - stop: rounded UP (ceil to tick), expanding stop distance (stop - entry)
        so dollar risk is never understated.
      - target: rounded UP (ceil to tick), conservative target distance.
    - Preserves strict bracket polarity post-rounding:
      - Long: stop_price < entry_price < target_price
      - Short: target_price < entry_price < stop_price
    - Returns: (entry_price, stop_price, target_price) as exact multiples of tick_size.
    """
    for name, val in (("entry", raw_entry), ("stop", raw_stop), ("target", raw_target)):
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val) or val <= 0:
            raise ValueError(f"{name} price must be a finite positive number, got {val!r}")

    if not isinstance(tick_size, (int, float)) or isinstance(tick_size, bool) or not math.isfinite(tick_size) or tick_size <= 0:
        raise ValueError(f"tick_size must be a finite positive number, got {tick_size!r}")

    if action == SIGNAL_LONG:
        if not (raw_stop < raw_entry < raw_target):
            raise ValueError(
                f"Invalid LONG brackets: require stop ({raw_stop}) < entry ({raw_entry}) < target ({raw_target})"
            )
        entry_price = round(math.ceil(raw_entry / tick_size) * tick_size, 4)
        stop_price = round(math.floor(raw_stop / tick_size) * tick_size, 4)
        target_price = round(math.floor(raw_target / tick_size) * tick_size, 4)

        # Safety: avoid level collapse if raw_stop was very close to raw_entry
        if stop_price >= entry_price:
            stop_price = round(entry_price - tick_size, 4)
        if target_price <= entry_price:
            target_price = round(entry_price + tick_size, 4)

        if not (stop_price < entry_price < target_price):
            raise ValueError(
                f"Post-rounding LONG levels collapsed: {stop_price} < {entry_price} < {target_price}"
            )

    elif action == SIGNAL_SHORT:
        if not (raw_target < raw_entry < raw_stop):
            raise ValueError(
                f"Invalid SHORT brackets: require target ({raw_target}) < entry ({raw_entry}) < stop ({raw_stop})"
            )
        entry_price = round(math.floor(raw_entry / tick_size) * tick_size, 4)
        stop_price = round(math.ceil(raw_stop / tick_size) * tick_size, 4)
        target_price = round(math.ceil(raw_target / tick_size) * tick_size, 4)

        # Safety: avoid level collapse if raw_stop was very close to raw_entry
        if stop_price <= entry_price:
            stop_price = round(entry_price + tick_size, 4)
        if target_price >= entry_price:
            target_price = round(entry_price - tick_size, 4)

        if not (target_price < entry_price < stop_price):
            raise ValueError(
                f"Post-rounding SHORT levels collapsed: {target_price} < {entry_price} < {stop_price}"
            )
    else:
        raise ValueError(f"Action must be {SIGNAL_LONG!r} or {SIGNAL_SHORT!r}, got {action!r}")

    return entry_price, stop_price, target_price


@dataclass(frozen=True)
class PricingContext:
    """Immutable price snapshot linked to a specific realtime Signal identity."""

    signal_identity: tuple[str, str]
    symbol: str
    action: str
    timestamp: datetime
    raw_entry: float
    raw_stop: float
    raw_target: float
    entry_price: float
    stop_price: float
    target_price: float
    dollars_per_point: float
    size: int


class MnqPricedIntentBridge:
    """Causal, fail-closed bridge connecting backtest signal prices to OrderIntents."""

    def __init__(
        self,
        symbol: str = MNQ_SYMBOL,
        dollars_per_point: float = MNQ_DOLLARS_PER_POINT,
        tick_size: float = MNQ_TICK_SIZE,
    ) -> None:
        self.symbol = symbol
        self.dollars_per_point = dollars_per_point
        self.tick_size = tick_size
        self._contexts: dict[tuple[str, str], PricingContext] = {}
        self._consumed: set[tuple[str, str]] = set()

    def register_context(
        self,
        signal: RealtimeSignal,
        backtest_signal: BacktestSignal,
        size: int = 1,
    ) -> PricingContext:
        """Registers the pricing context for a newly generated realtime Signal."""
        if not isinstance(signal, RealtimeSignal):
            raise TypeError(f"signal must be RealtimeSignal, got {type(signal).__name__}")
        if not isinstance(backtest_signal, BacktestSignal):
            raise TypeError(f"backtest_signal must be BacktestSignal, got {type(backtest_signal).__name__}")

        if signal.symbol != self.symbol:
            raise ValueError(f"Bridge accepts symbol {self.symbol!r} only, got {signal.symbol!r}")

        expected_action = SIGNAL_LONG if backtest_signal.direction == "long" else SIGNAL_SHORT
        if signal.action != expected_action:
            raise ValueError(
                f"Signal action {signal.action!r} does not match backtest direction {backtest_signal.direction!r}"
            )

        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"size must be a positive integer, got {size!r}")

        entry_px, stop_px, target_px = round_mnq_prices_conservative(
            action=signal.action,
            raw_entry=backtest_signal.entry,
            raw_stop=backtest_signal.stop,
            raw_target=backtest_signal.target,
            tick_size=self.tick_size,
        )

        key = identity_key(signal)
        if key in self._consumed:
            raise ValueError(f"Pricing context for signal {key} has already been consumed")

        if key in self._contexts:
            existing = self._contexts[key]
            # Check for conflict
            fp_new = (entry_px, stop_px, target_px, size)
            fp_existing = (existing.entry_price, existing.stop_price, existing.target_price, existing.size)
            if fp_new != fp_existing:
                raise ValueError(f"Conflicting duplicate pricing context for signal {key}")
            return existing

        ctx = PricingContext(
            signal_identity=key,
            symbol=self.symbol,
            action=signal.action,
            timestamp=signal.timestamp,
            raw_entry=backtest_signal.entry,
            raw_stop=backtest_signal.stop,
            raw_target=backtest_signal.target,
            entry_price=entry_px,
            stop_price=stop_px,
            target_price=target_px,
            dollars_per_point=self.dollars_per_point,
            size=size,
        )
        self._contexts[key] = ctx
        return ctx

    def create_intent(
        self,
        signal: RealtimeSignal,
        decision: RiskDecision,
        sequence: int,
        clock: Clock,
    ) -> OrderIntent:
        """Factory method producing an authorized, priced OrderIntent."""
        require_signal(signal)
        require_risk_decision(decision)

        key = identity_key(signal)
        dec_key = (decision.signal_source, decision.signal_id)
        if dec_key != key:
            raise ValueError(
                f"RiskDecision references signal {dec_key}, but intent requested for {key}"
            )

        if decision.origin != signal.origin:
            raise ValueError(
                f"RiskDecision origin {decision.origin!r} does not match Signal origin {signal.origin!r}"
            )

        if not is_authorized(decision):
            raise ValueError("Cannot create OrderIntent for unauthorized RiskDecision")

        if key not in self._contexts:
            raise ValueError(f"Missing pricing context for signal {key}")

        if key in self._consumed:
            raise ValueError(f"Pricing context for signal {key} has already been consumed")

        ctx = self._contexts[key]
        if ctx.symbol != signal.symbol:
            raise ValueError(f"Context symbol {ctx.symbol!r} does not match signal {signal.symbol!r}")
        if ctx.action != signal.action:
            raise ValueError(f"Context action {ctx.action!r} does not match signal {signal.action!r}")

        intent = OrderIntent(
            event_id=f"priced-intent-{sequence}",
            source="mnq-priced-intent-bridge",
            timestamp=clock.now(),
            sequence=sequence,
            symbol=signal.symbol,
            action=signal.action,
            risk_decision_id=decision.event_id,
            origin=signal.origin,
            size=ctx.size,
            entry_price=ctx.entry_price,
            stop_price=ctx.stop_price,
            target_price=ctx.target_price,
            dollars_per_point=ctx.dollars_per_point,
        )
        validated_intent = require_order_intent(intent)
        self._consumed.add(key)
        return validated_intent

    def __call__(
        self,
        signal: RealtimeSignal,
        decision: RiskDecision,
        sequence: int,
        clock: Clock,
    ) -> OrderIntent:
        """Implements the IntentFactory protocol for direct injection into PaperRealtimeSession."""
        return self.create_intent(signal, decision, sequence, clock)


class MnqPricedStrategyAdapter:
    """Strategy adapter that wraps SmcFvgStrategy and binds price context into MnqPricedIntentBridge."""

    def __init__(
        self,
        strategy: SmcFvgStrategy | None = None,
        bridge: MnqPricedIntentBridge | None = None,
        *,
        source: str = "smc_fvg_m5",
        requested_size: int = 1,
    ) -> None:
        if strategy is None:
            strategy = SmcFvgStrategy(market=MNQ)
        if bridge is None:
            bridge = MnqPricedIntentBridge()
        self.strategy = strategy
        self.bridge = bridge
        self.source = source
        self.requested_size = requested_size
        self._history: list[BacktestBar] = []
        self._seq = 0

    def on_event(self, event: CanonicalEvent) -> RealtimeSignal | None:
        """Processes a market data event and proposes priced signals causally."""
        if not isinstance(event, RealtimeBar):
            return None
        if event.symbol != self.bridge.symbol:
            return None

        # Build backtest Bar causally from the realtime Bar event
        b = BacktestBar(
            timestamp=event.timestamp,
            open=event.open,
            high=event.high,
            low=event.low,
            close=event.close,
            volume=event.volume,
        )
        self._history.append(b)

        # Evaluate strategy on closed bars up to current event
        backtest_sig = self.strategy.evaluate(self._history)
        if backtest_sig is None:
            return None

        self._seq += 1
        action = SIGNAL_LONG if backtest_sig.direction == "long" else SIGNAL_SHORT
        realtime_sig = RealtimeSignal(
            event_id=f"sig-{event.event_id}-{self._seq}",
            source=self.source,
            timestamp=event.timestamp,
            sequence=self._seq,
            symbol=event.symbol,
            action=action,
            origin=event.origin,
        )

        # Register pricing context into the bridge
        self.bridge.register_context(
            realtime_sig,
            backtest_sig,
            size=self.requested_size,
        )

        return realtime_sig


__all__ = [
    "MNQ_DOLLARS_PER_POINT",
    "MNQ_SYMBOL",
    "MNQ_TICK_SIZE",
    "MnqPricedIntentBridge",
    "MnqPricedStrategyAdapter",
    "PricingContext",
    "round_mnq_prices_conservative",
]
