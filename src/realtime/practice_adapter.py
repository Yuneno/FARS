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
    EXEC_ACCEPTED,
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
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_EXPIRED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_WORKING,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_STOP,
    BracketConfig,
    PracticeOrderClient,
    PracticeOrderError,
    is_past_daily_close_cutoff,
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
        self._fingerprints: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._open_positions: dict[str, int] = {}  # symbol -> net contracts
        self._working_orders: dict[int, str] = {}  # order_id -> contract/symbol
        self._flatten_count = 0

    @property
    def max_risk_dollars_per_order(self) -> float:
        return self._max_risk_dollars_per_order

    @property
    def open_positions(self) -> dict[str, int]:
        return dict(self._open_positions)

    @property
    def working_order_ids(self) -> frozenset[int]:
        """Read-only view of currently tracked working order IDs."""
        return frozenset(self._working_orders.keys())

    @property
    def working_order_count(self) -> int:
        """Number of currently tracked working orders."""
        return len(self._working_orders)

    @property
    def flatten_count(self) -> int:
        return self._flatten_count

    @property
    def order_client(self) -> PracticeOrderClient | None:
        return self._order_client

    def _cache_report(
        self,
        key: tuple[str, str],
        report: ExecutionReport,
        fingerprint: tuple[Any, ...],
    ) -> ExecutionReport:
        self._reports[key] = report
        self._fingerprints[key] = fingerprint
        return report

    def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
        key = identity_key(intent)
        fingerprint = (
            intent.symbol,
            intent.action,
            intent.risk_decision_id,
            intent.origin,
            intent.size,
            intent.entry_price,
            intent.stop_price,
            intent.target_price,
            intent.dollars_per_point,
        )
        if key in self._reports:
            previous_fp = self._fingerprints.get(key)
            if previous_fp != fingerprint:
                raise ValueError("duplicate practice intent identity with a conflicting fingerprint")
            return self._reports[key]

        # 1. Market close cutoff check (15:10 CT daily close rule)
        if is_past_daily_close_cutoff(self._clock.now()):
            self._seq += 1
            report = ExecutionReport(
                event_id=f"practice-reject-{self._seq}",
                source=self._source,
                timestamp=self._clock.now(),
                sequence=self._seq,
                order_intent_id=intent.event_id,
                status=EXEC_REJECTED,
                origin=intent.origin,
                reason="MARKET_CLOSE_CUTOFF_REACHED: new orders prohibited after 15:10 CT; flatten strictly enforced",
            )
            return self._cache_report(key, report, fingerprint)

        # 2. Allowlist gate (if allowlist configured)
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
                return self._cache_report(key, report, fingerprint)

        # 3. Prerequisite for LONG/SHORT: require both entry_price and stop_price (fail-closed, parity with gateway)
        if intent.action in (SIGNAL_LONG, SIGNAL_SHORT):
            if intent.entry_price is None or intent.stop_price is None:
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
                        "MISSING_ENTRY_OR_STOP_PRICE: order requires both entry_price "
                        f"and stop_price for risk pre-check and bracket placement (got entry={intent.entry_price!r}, stop={intent.stop_price!r})"
                    ),
                )
                return self._cache_report(key, report, fingerprint)

        # 4. Risk per order pre-check (§4-bis RT-9) & Declared limit: strictly 1 micro
        effective_size = intent.size
        reduction_notes: list[str] = []

        if intent.entry_price is not None and intent.stop_price is not None:
            dpp = intent.dollars_per_point if intent.dollars_per_point is not None else self._default_dpp
            point_risk = abs(intent.entry_price - intent.stop_price)
            calc_risk = point_risk * dpp * intent.size
            micro_risk = point_risk * dpp * 1

            if calc_risk > self._max_risk_dollars_per_order:
                if micro_risk > self._max_risk_dollars_per_order:
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
                    return self._cache_report(key, report, fingerprint)

                effective_size = 1
                reduction_notes.append(
                    f"REDUCED_TO_1_MICRO: risk ${calc_risk:.2f} > ${self._max_risk_dollars_per_order:.2f}"
                )

        if effective_size > 1:
            effective_size = 1
            reduction_notes.append(
                f"CLAMPED_TO_1_MICRO: requested size {intent.size} clamped to declared limit of 1 micro"
            )

        reduction_note = "; ".join(reduction_notes)

        # 3. Gateway dispatch (if PracticeOrderClient is configured and active)
        if self._order_client is not None and self._order_client.practice_execution_enabled:
            if intent.action == SIGNAL_FLAT:
                self._seq += 1
                report = ExecutionReport(
                    event_id=f"practice-reject-{self._seq}",
                    source=self._source,
                    timestamp=self._clock.now(),
                    sequence=self._seq,
                    order_intent_id=intent.event_id,
                    status=EXEC_REJECTED,
                    origin=intent.origin,
                    reason="FLAT_REQUIRES_EXPLICIT_FLATTEN: flat signals cannot be routed as directional gateway orders; use adapter.flatten()",
                )
                return self._cache_report(key, report, fingerprint)

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
                return self._cache_report(key, report, fingerprint)

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

                if result.status == ORDER_STATUS_WORKING:
                    self._working_orders[result.order_id] = contract
                    report = ExecutionReport(
                        event_id=f"practice-gateway-{result.order_id}-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_ACCEPTED,
                        origin=intent.origin,
                        reason=reason_str,
                    )
                    return self._cache_report(key, report, fingerprint)
                elif result.status == ORDER_STATUS_FILLED:
                    report = ExecutionReport(
                        event_id=f"practice-gateway-{result.order_id}-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_FILLED,
                        origin=intent.origin,
                        reason=reason_str,
                    )
                    current = self._open_positions.get(intent.symbol, 0)
                    if intent.action == SIGNAL_LONG:
                        self._open_positions[intent.symbol] = current + effective_size
                    elif intent.action == SIGNAL_SHORT:
                        self._open_positions[intent.symbol] = current - effective_size
                    elif intent.action == SIGNAL_FLAT:
                        self._open_positions[intent.symbol] = 0
                    return self._cache_report(key, report, fingerprint)
                elif result.status == ORDER_STATUS_CANCELLED:
                    report = ExecutionReport(
                        event_id=f"practice-gateway-cancelled-{result.order_id}-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_REJECTED,
                        origin=intent.origin,
                        reason=f"GATEWAY_ORDER_CANCELLED: broker reported order {result.order_id} cancelled",
                    )
                    return self._cache_report(key, report, fingerprint)
                elif result.status == ORDER_STATUS_REJECTED:
                    err_detail = f": {result.error_message}" if result.error_message else ""
                    report = ExecutionReport(
                        event_id=f"practice-gateway-rejected-{result.order_id}-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_REJECTED,
                        origin=intent.origin,
                        reason=f"GATEWAY_ORDER_REJECTED: broker rejected order {result.order_id}{err_detail}",
                    )
                    return self._cache_report(key, report, fingerprint)
                elif result.status == ORDER_STATUS_EXPIRED:
                    report = ExecutionReport(
                        event_id=f"practice-gateway-expired-{result.order_id}-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_REJECTED,
                        origin=intent.origin,
                        reason=f"GATEWAY_ORDER_EXPIRED: broker reported order {result.order_id} expired",
                    )
                    return self._cache_report(key, report, fingerprint)
                else:
                    report = ExecutionReport(
                        event_id=f"practice-gateway-unknown-{result.order_id}-{self._seq}",
                        source=self._source,
                        timestamp=self._clock.now(),
                        sequence=self._seq,
                        order_intent_id=intent.event_id,
                        status=EXEC_REJECTED,
                        origin=intent.origin,
                        reason=f"UNKNOWN_ORDER_STATUS: gateway returned unexpected status {result.status!r}",
                    )
                    return self._cache_report(key, report, fingerprint)

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
                return self._cache_report(key, report, fingerprint)

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

        # Update position tracking
        current = self._open_positions.get(intent.symbol, 0)
        if intent.action == SIGNAL_LONG:
            self._open_positions[intent.symbol] = current + effective_size
        elif intent.action == SIGNAL_SHORT:
            self._open_positions[intent.symbol] = current - effective_size
        elif intent.action == SIGNAL_FLAT:
            self._open_positions[intent.symbol] = 0

        return self._cache_report(key, report, fingerprint)

    def flatten(self, symbol: str | None = None) -> list[str]:
        """Flatten open positions and cancel working orders immediately. Fail-closed."""
        gateway_flattened: list[str] = []
        # 1. Gateway cancellation and position closing if order_client present
        if self._order_client is not None and self._order_client.practice_execution_enabled:
            if self._account_id is not None:
                # Do not catch or silence gateway exceptions
                if symbol is not None:
                    # Symbol-specific flatten: cancel all working orders first, then flatten the specific contract
                    self._order_client.cancel_all_orders(self._account_id, account_name=self._account_name)
                    target_contract = self._contract_id or symbol
                    self._order_client.flatten_contract(
                        self._account_id, target_contract, account_name=self._account_name
                    )
                    gateway_flattened.append(symbol)
                else:
                    # Full flatten: flatten_all is the single owner of cancel_all_orders + closing all positions
                    closed = self._order_client.flatten_all(self._account_id, account_name=self._account_name)
                    gateway_flattened.extend(closed)

                # Verified flatten: query gateway to confirm open positions are 0
                open_pos = self._order_client.search_open_positions(
                    self._account_id, account_name=self._account_name
                )
                target_check = [self._contract_id or symbol] if symbol is not None else None
                unconfirmed = [
                    p for p in open_pos
                    if p.size != 0 and (target_check is None or p.contract_id in target_check)
                ]
                if unconfirmed:
                    raise PracticeOrderError(
                        f"FLATTEN_UNCONFIRMED: open positions persist on gateway after flatten: {unconfirmed}"
                    )

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

        # Clear verified working orders globally (cancel_all was executed for all symbols on gateway)
        self._working_orders.clear()

        return all_flattened

    def check_market_close_cutoff(self) -> bool:
        """Check 15:10 CT cutoff rule: triggers flatten if reached with open positions or working orders."""
        if is_past_daily_close_cutoff(self._clock.now()):
            if any(pos != 0 for pos in self._open_positions.values()) or len(self._working_orders) > 0:
                self.flatten()
                return True
        return False
