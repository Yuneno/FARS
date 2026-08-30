"""RT-7 paper execution. Same ExecutionAdapter interface as future live.

Fills are simulated. They are not live-equivalent. Assumptions are explicit
on every ExecutionReport.reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from src.realtime.clock import Clock
from src.realtime.events import (
    EXEC_FILLED,
    EXEC_PARTIAL,
    EXEC_REJECTED,
    ExecutionReport,
    OrderIntent,
    identity_key,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED, ExecutionAdapter

PAPER_FILL_FULL = "full"
PAPER_FILL_PARTIAL = "partial"
PAPER_FILL_REJECT = "reject"
_ALLOWED_FILL_POLICY = {PAPER_FILL_FULL, PAPER_FILL_PARTIAL, PAPER_FILL_REJECT}

_POLICY_STATUS = {
    PAPER_FILL_FULL: EXEC_FILLED,
    PAPER_FILL_PARTIAL: EXEC_PARTIAL,
    PAPER_FILL_REJECT: EXEC_REJECTED,
}


def _require_note(name: str, value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{name} must be a non-empty assumption note, got {value!r}")
    return value


@dataclass(frozen=True)
class PaperAssumptions:
    """Stated paper model. Silent zero-cost defaults are not allowed."""

    latency: timedelta
    slippage: str
    commissions: str
    partial_fills: str
    rejections: str
    fill_policy: Literal["full", "partial", "reject"]

    def __post_init__(self) -> None:
        if not isinstance(self.latency, timedelta) or isinstance(self.latency, bool):
            raise ValueError(f"latency must be a timedelta, got {self.latency!r}")
        if self.latency.total_seconds() < 0:
            raise ValueError(f"latency must be >= 0, got {self.latency!r}")
        _require_note("slippage", self.slippage)
        _require_note("commissions", self.commissions)
        _require_note("partial_fills", self.partial_fills)
        _require_note("rejections", self.rejections)
        if self.fill_policy not in _ALLOWED_FILL_POLICY:
            raise ValueError(
                f"fill_policy must be one of {sorted(_ALLOWED_FILL_POLICY)}, "
                f"got {self.fill_policy!r}"
            )


def default_paper_assumptions() -> PaperAssumptions:
    """Honest unmodeled defaults. Not a claim of zero costs."""
    return PaperAssumptions(
        latency=timedelta(0),
        slippage="unmodeled: OrderIntent has no price",
        commissions="unmodeled: OrderIntent has no quantity",
        partial_fills="not_simulated: each intent is one report",
        rejections="none",
        fill_policy=PAPER_FILL_FULL,
    )


class PaperExecutionAdapter(ExecutionAdapter):
    """Paper fills through the sealed submit veto. Never live."""

    def __init__(
        self,
        clock: Clock,
        assumptions: PaperAssumptions,
        *,
        source: str = "fars-paper",
    ) -> None:
        if LIVE_EXECUTION_ENABLED is not False:
            raise RuntimeError("paper adapter refuses to run while live execution is enabled")
        if not isinstance(assumptions, PaperAssumptions):
            raise TypeError(
                f"assumptions must be PaperAssumptions, got {type(assumptions).__name__}"
            )
        if not isinstance(source, str) or source.strip() == "":
            raise ValueError(f"source must be a non-empty string, got {source!r}")
        now = clock.now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock.now() must return a timezone-aware datetime")
        self._clock = clock
        self._assumptions = assumptions
        self._source = source
        self._seq = 0
        self._fills: dict[tuple[str, str], tuple[ExecutionReport, tuple]] = {}

    def _execute_authorized(self, intent: OrderIntent) -> ExecutionReport:
        key = identity_key(intent)
        fingerprint = (intent.symbol, intent.action, intent.risk_decision_id, intent.origin)
        existing = self._fills.get(key)
        if existing is not None:
            report, previous = existing
            if previous != fingerprint:
                raise ValueError(
                    "duplicate paper intent identity with a conflicting fingerprint"
                )
            return report
        fill_at = self._clock.now() + self._assumptions.latency
        if fill_at.tzinfo is None or fill_at.utcoffset() is None:
            raise ValueError("paper fill timestamp must stay timezone-aware")
        self._seq += 1
        status = _POLICY_STATUS[self._assumptions.fill_policy]
        report = ExecutionReport(
            event_id=f"paper-{self._seq}",
            source=self._source,
            timestamp=fill_at,
            sequence=self._seq,
            order_intent_id=intent.event_id,
            status=status,
            origin=intent.origin,
            reason=self._reason(),
        )
        self._fills[key] = (report, fingerprint)
        return report

    def _reason(self) -> str:
        assumed = self._assumptions
        return (
            "PAPER not_live_equivalent "
            f"latency={assumed.latency} "
            f"slippage={assumed.slippage} "
            f"commissions={assumed.commissions} "
            f"partial_fills={assumed.partial_fills} "
            f"rejections={assumed.rejections} "
            f"fill_policy={assumed.fill_policy}"
        )
