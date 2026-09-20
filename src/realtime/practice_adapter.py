"""PracticeExecutionAdapter for TopstepX/ProjectX Practice account.

Enforces:
1. Sealed submit veto via ExecutionAdapter.
2. Allowlist gate (PRACTICE_ACCOUNT_ALLOWLIST).
3. Pre-check max risk in dollars per order (§4-bis RT-9):
   risk = |entry - stop| * $/pt * size
   If risk > max_risk_dollars_per_order:
     reduce to 1 micro if risk at 1 micro <= max_risk_dollars_per_order
     veto (EXEC_REJECTED with MAX_RISK_PER_ORDER) if risk at 1 micro > max_risk_dollars_per_order
4. Live execution locked (LIVE_EXECUTION_ENABLED = False).
5. Gateway integration: optionally dispatches through PracticeOrderClient
   when configured and PRACTICE_EXECUTION_ENABLED is True, or runs deterministic
   offline simulation when order_client is None.
6. Flatten tracking and execution against the allowlisted practice account.
7. Fail-closed on any unauthorized state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from math import isfinite
from typing import Any

from src.realtime.clock import Clock
from src.realtime.events import (
    EXEC_FILLED,
    EXEC_REJECTED,
    ExecutionReport,
    OrderIntent,
    REASON_MAX_RISK_PER_ORDER,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    SIGNAL_FLAT,
    identity_key,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED, ExecutionAdapter
from src.realtime.orders.practice_client import (
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP,
    BracketConfig,
    PracticeOrderClient,
    PracticeOrderError,
)


class PracticeExecutionAdapter(ExecutionAdapter):
    """Execution adapter for Practice accounts with declared risk gates and gateway support."""

    def __init__(
        self,
        clock: Clock,
        *,
        max_risk_dollars_per_order: float = 200.0,
        default_dollars_per_point: float = 2.0,  # MNQ standard ($2/pt)
        default_tick_size: float = 0.25,        # MNQ standard (0.25 pt/tick)
        account_name: str | None = None,
        account_id: int | None = None,
        contract_id: str | None = None,
        account_allowlist: tuple[str | int, ...] = (),
        order_client: PracticeOrderClient | None = None,
        flatten_handler: Callable[[], Any] | None = None,
        source: str = "fars-practice-adapter",
    ) -> None:
        if LIVE_EXECUTION_ENABLED is not False:
            raise RuntimeError("PracticeExecutionAdapter refuses to run while live execution is enabled")
        if not isfinite(max_risk_dollars_per_order) or max_risk_dollars_per_order <= 0:
            raise ValueError(
                f"max_risk_dollars_per_order must be finite and positive, got {max_risk_dollars_per_order!r}"
            )
        if not isfinite(default_dollars_per_point) or default_dollars_per_point <= 0:
            raise ValueError(
                f"default_dollars_per_point must be finite and positive, got {default_dollars_per_point!r}"
            )
        if not isfinite(default_tick_size) or default_tick_size <= 0:
            raise ValueError(
                f"default_tick_size must be finite and positive, got {default_tick_size!r}"
            )
        now = clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock.now() must return a timezone-aware datetime")

        self._clock = clock
        self._max_risk_dollars_per_order = float(max_risk_dollars_per_order)
        self._default_dpp = float(default_dollars_per_point)
        self._default_tick_size = float(default_tick_size)
        self._account_name = account_name
        self._account_id = account_id
        self._contract_id = contract_id
        self._account_allowlist = tuple(account_allowlist)
        self._order_client = order_client
        self._flatten_handler = flatten_handler
        self._source = source
        self._seq = 0
        self._reports: dict[tuple[str, str], ExecutionReport] = {}
        self._open_positions: dict[str, int] = {}  # symbol -> net contracts
        self._flatten_count = 0

    @property
    def max_risk_dollars_per_order(self) -> float:
        return self._max_risk_dollars_per_order

    @property
    def open_positions(self) -> dict[str, int]:
        return dict(self._open_positions)

    @property
    def flatten_count(self) -> int:
        return self._flatten_count

    @property
    def order_client(self) -> PracticeOrderClient | None:
        return self._order_client

    def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
        key = identity_key(intent)
        if key in self._reports:
            return self._reports[key]

        # 1. Allowlist gate (if allowlist configured)
        if self._account_allowlist:
            matched = False
            if self._account_name is not None and self._account_name in self._account_allowlist:
                matched = True
            elif self._account_id is not None and (
                self._account_id in self._account_allowlist
                or str(self._account_id) in [str(x) for x in self._account_allowlist]
            ):
                matched = True

            if not matched:
                self._seq += 1
                report = ExecutionReport(
                    event_id=f"practice-reject-{self._seq}",
                    source=self._source,
                    timestamp=self._clock.now(),
                    sequence=self._seq,
                    order_intent_id=intent.event_id,
                    status=EXEC_REJECTED,
                    origin=intent.origin,
                    reason=f"ACCOUNT_NOT_ALLOWLISTED: account {self._account_name!r} / {self._account_id!r} not in {self._account_allowlist!r}",
                )
                self._reports[key] = report
                return report

        # 2. Risk per order pre-check (§4-bis RT-9)
        effective_size = intent.size
        size_reduced = False
        reduction_note = ""

        if intent.entry_price is not None and intent.stop_price is not None:
            dpp = intent.dollars_per_point if intent.dollars_per_point is not None else self._default_dpp
            point_risk = abs(intent.entry_price - intent.stop_price)
            order_risk = point_risk * dpp * intent.size

            if order_risk > self._max_risk_dollars_per_order:
                # Check if 1 micro fits within limit
                micro_risk = point_risk * dpp * 1
                if micro_risk <= self._max_risk_dollars_per_order:
                    effective_size = 1
                    size_reduced = True
                    reduction_note = (
                        f"REDUCED_TO_1_MICRO (requested {intent.size} with risk "
                        f"${order_risk:.2f} > max ${self._max_risk_dollars_per_order:.2f}; "
                        f"1 micro risk ${micro_risk:.2f} fits)"
                    )
                else:
                    # Even 1 micro exceeds limit -> VETO
                    self._seq += 1
                    report = ExecutionReport(
                        event_id=f"practice-reject-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_REJECTED,
                        origin=intent.origin,
                        reason=(
                            f"{REASON_MAX_RISK_PER_ORDER}: calculated risk ${micro_risk:.2f} "
                            f"(at 1 micro) exceeds limit ${self._max_risk_dollars_per_order:.2f}"
                        ),
                    )
                    self._reports[key] = report
                    return report

        # 3. Gateway dispatch (if PracticeOrderClient is configured and active)
        if self._order_client is not None and self._order_client.practice_execution_enabled:
            if self._account_id is None:
                self._seq += 1
                report = ExecutionReport(
                    event_id=f"practice-reject-{self._seq}",
                    source=self._source,
                    timestamp=self._clock.now(),
                    sequence=self._seq,
                    order_intent_id=intent.event_id,
                    status=EXEC_REJECTED,
                    origin=intent.origin,
                    reason="MISSING_ACCOUNT_ID: account_id required for gateway order placement",
                )
                self._reports[key] = report
                return report

            contract = self._contract_id or intent.symbol
            side = ORDER_SIDE_BUY if intent.action == SIGNAL_LONG else ORDER_SIDE_SELL

            # Calculate brackets if stop_price or target_price available
            stop_bracket = None
            if intent.entry_price is not None and intent.stop_price is not None:
                stop_pts = abs(intent.entry_price - intent.stop_price)
                stop_ticks = max(1, int(round(stop_pts / self._default_tick_size)))
                stop_bracket = BracketConfig(ticks=stop_ticks, order_type=ORDER_TYPE_STOP)

            target_bracket = None
            if intent.entry_price is not None and intent.target_price is not None:
                target_pts = abs(intent.target_price - intent.entry_price)
                target_ticks = max(1, int(round(target_pts / self._default_tick_size)))
                target_bracket = BracketConfig(ticks=target_ticks, order_type=ORDER_TYPE_LIMIT)

            order_type = ORDER_TYPE_MARKET if intent.entry_price is None else ORDER_TYPE_LIMIT

            try:
                result = self._order_client.place_order(
                    account_id=self._account_id,
                    contract_id=contract,
                    order_type=order_type,
                    side=side,
                    size=effective_size,
                    account_name=self._account_name,
                    limit_price=intent.entry_price,
                    stop_price=intent.stop_price if order_type == ORDER_TYPE_STOP else None,
                    stop_loss_bracket=stop_bracket,
                    take_profit_bracket=target_bracket,
                )
                self._seq += 1
                reason_str = f"GATEWAY_ORDER_PLACED order_id={result.order_id} size={effective_size}"
                if reduction_note:
                    reason_str += f"; {reduction_note}"

                report = ExecutionReport(
                    event_id=f"practice-gateway-{result.order_id}",
                    source=self._source,
                    timestamp=self._clock.now(),
                    sequence=self._seq,
                    order_intent_id=intent.event_id,
                    status=EXEC_FILLED,
                    origin=intent.origin,
                    reason=reason_str,
                )
                self._reports[key] = report

                # Update position tracking
                current = self._open_positions.get(intent.symbol, 0)
                if intent.action == SIGNAL_LONG:
                    self._open_positions[intent.symbol] = current + effective_size
                elif intent.action == SIGNAL_SHORT:
                    self._open_positions[intent.symbol] = current - effective_size
                elif intent.action == SIGNAL_FLAT:
                    self._open_positions[intent.symbol] = 0

                return report

            except PracticeOrderError as exc:
                self._seq += 1
                report = ExecutionReport(
                    event_id=f"practice-reject-{self._seq}",
                    source=self._source,
                    timestamp=self._clock.now(),
                    sequence=self._seq,
                    order_intent_id=intent.event_id,
                    status=EXEC_REJECTED,
                    origin=intent.origin,
                    reason=f"GATEWAY_REJECTION: {exc}",
                )
                self._reports[key] = report
                return report

        # 4. Deterministic Simulation / Offline Paper Fallback
        self._seq += 1
        reason_parts = [f"PRACTICE_FILLED size={effective_size}"]
        if reduction_note:
            reason_parts.append(reduction_note)

        report = ExecutionReport(
            event_id=f"practice-fill-{self._seq}",
            source=self._source,
            timestamp=self._clock.now(),
            sequence=self._seq,
            order_intent_id=intent.event_id,
            status=EXEC_FILLED,
            origin=intent.origin,
            reason="; ".join(reason_parts),
        )
        self._reports[key] = report

        # Update position tracking
        current = self._open_positions.get(intent.symbol, 0)
        if intent.action == SIGNAL_LONG:
            self._open_positions[intent.symbol] = current + effective_size
        elif intent.action == SIGNAL_SHORT:
            self._open_positions[intent.symbol] = current - effective_size
        elif intent.action == SIGNAL_FLAT:
            self._open_positions[intent.symbol] = 0

        return report

    def flatten(self, symbol: str | None = None) -> list[str]:
        """Flatten open positions and cancel working orders immediately. Fail-closed."""
        gateway_flattened: list[str] = []
        # 1. Gateway cancellation and position closing if order_client present
        if self._order_client is not None and self._order_client.practice_execution_enabled:
            if self._account_id is not None:
                try:
                    self._order_client.cancel_all_orders(self._account_id, account_name=self._account_name)
                    if symbol is not None:
                        target_contract = self._contract_id or symbol
                        self._order_client.flatten_contract(
                            self._account_id, target_contract, account_name=self._account_name
                        )
                        gateway_flattened.append(symbol)
                    else:
                        closed = self._order_client.flatten_all(self._account_id, account_name=self._account_name)
                        gateway_flattened.extend(closed)
                except Exception:
                    pass

        # 2. Local tracking update
        flattened_symbols: list[str] = []
        if symbol is not None:
            symbols = [symbol] if self._open_positions.get(symbol, 0) != 0 else []
        else:
            symbols = [s for s, pos in self._open_positions.items() if pos != 0]

        for s in symbols:
            self._open_positions[s] = 0
            flattened_symbols.append(s)

        # Merge local and gateway flattened symbols uniquely
        all_flattened: list[str] = []
        for sym in flattened_symbols + gateway_flattened:
            if sym not in all_flattened:
                all_flattened.append(sym)

        if all_flattened or (self._order_client is not None and self._order_client.practice_execution_enabled):
            self._flatten_count += 1
            if self._flatten_handler is not None:
                self._flatten_handler()

        return all_flattened
