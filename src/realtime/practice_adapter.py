"""PracticeExecutionAdapter for TopstepX/ProjectX Practice account.

Enforces:
1. Sealed submit veto via ExecutionAdapter.
2. Allowlist gate (PRACTICE_ACCOUNT_ALLOWLIST).
3. Pre-check max risk in dollars per order (§4-bis RT-9):
   risk = |entry - stop| * $/pt * size
   If risk > max_risk_dollars_per_order:
     reduce to 1 micro if risk at 1 micro <= max_risk_dollars_per_order
     veto (EXEC_REJECTED with MAX_RISK_PER_ORDER) if risk at 1 micro > max_risk_dollars_per_order
4. Flatten tracking and execution.
5. Fail-closed on any unauthorized state.
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


class PracticeExecutionAdapter(ExecutionAdapter):
    """Execution adapter for Practice accounts with declared risk gates."""

    def __init__(
        self,
        clock: Clock,
        *,
        max_risk_dollars_per_order: float = 200.0,
        default_dollars_per_point: float = 2.0,  # MNQ standard ($2/pt)
        account_name: str | None = None,
        account_allowlist: tuple[str, ...] = (),
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
        now = clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock.now() must return a timezone-aware datetime")

        self._clock = clock
        self._max_risk_dollars_per_order = float(max_risk_dollars_per_order)
        self._default_dpp = float(default_dollars_per_point)
        self._account_name = account_name
        self._account_allowlist = tuple(account_allowlist)
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

    def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
        key = identity_key(intent)
        if key in self._reports:
            return self._reports[key]

        # 1. Allowlist gate (if allowlist configured)
        if self._account_allowlist:
            if self._account_name is None or self._account_name not in self._account_allowlist:
                self._seq += 1
                report = ExecutionReport(
                    event_id=f"practice-reject-{self._seq}",
                    source=self._source,
                    timestamp=self._clock.now(),
                    sequence=self._seq,
                    order_intent_id=intent.event_id,
                    status=EXEC_REJECTED,
                    origin=intent.origin,
                    reason=f"ACCOUNT_NOT_ALLOWLISTED: account {self._account_name!r} not in {self._account_allowlist!r}",
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

        # 3. Execution / Fill simulation
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
        """Flatten open positions immediately. Fail-closed."""
        flattened_symbols: list[str] = []
        if symbol is not None:
            symbols = [symbol] if self._open_positions.get(symbol, 0) != 0 else []
        else:
            symbols = [s for s, pos in self._open_positions.items() if pos != 0]

        for s in symbols:
            self._open_positions[s] = 0
            flattened_symbols.append(s)

        if flattened_symbols:
            self._flatten_count += 1
            if self._flatten_handler is not None:
                self._flatten_handler()

        return flattened_symbols
