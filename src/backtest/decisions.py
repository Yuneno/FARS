"""Join causal AMD+CRT decisions to outcomes from the same backtest run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from src.backtest.amd_crt import ET, AmdCrtDecision, DecisionReason, _session_date
from src.backtest.executor import ExecutedTrade

OutcomeDecisionReason = DecisionReason | Literal["not_executed"]


@dataclass(frozen=True)
class LabeledAmdCrtDecision:
    """One decision-log row, optionally labeled with its executed outcome."""

    day: date
    weekday: int
    direction: Literal["long", "short"]
    crt_confirmed: bool
    ema_regime: Literal["long", "short"] | None
    atr: float | None
    sl_pts: float | None
    tp_pts: float | None
    pre_ny_amplitude: float
    median_amplitude: float
    decision: OutcomeDecisionReason
    timestamp: datetime
    r_result: float | None
    exit_reason: str | None
    executed: bool


def join_decisions_to_outcomes(
    decisions: Sequence[AmdCrtDecision],
    trades: Sequence[ExecutedTrade],
    *,
    session_tz: ZoneInfo = ET,
    session_start: time = time(0, 0),
) -> list[LabeledAmdCrtDecision]:
    """Label decisions with trade outcomes using an exact session-day join.

    Only ``accepted`` decisions are eligible for an outcome. An accepted
    decision without a same-session trade is retained as ``not_executed``;
    this is how executor gap and invalid-level rejections remain observable.
    Rejected decisions are retained without an outcome.

    The function uses :func:`src.backtest.amd_crt._session_date` for both trade
    and decision timestamps. It rejects ambiguous (more than one trade or more
    than one accepted decision per session), unmatched, and temporally
    impossible associations instead of silently choosing a row.
    """
    trades_by_day: dict[date, ExecutedTrade] = {}
    trade_days = [
        _session_date(trade.entry_time, session_tz, session_start)
        for trade in trades
    ]
    duplicate_trade_days = sorted(
        day for day, count in Counter(trade_days).items() if count > 1
    )
    if duplicate_trade_days:
        days = ", ".join(day.isoformat() for day in duplicate_trade_days)
        raise ValueError(f"multiple trades found for session day(s): {days}")
    trades_by_day.update(zip(trade_days, trades, strict=True))

    accepted_days = [decision.day for decision in decisions if decision.decision == "accepted"]
    duplicate_decision_days = sorted(
        day for day, count in Counter(accepted_days).items() if count > 1
    )
    if duplicate_decision_days:
        days = ", ".join(day.isoformat() for day in duplicate_decision_days)
        raise ValueError(f"multiple accepted decisions found for session day(s): {days}")

    rows: list[LabeledAmdCrtDecision] = []
    matched_trade_days: set[date] = set()
    for source in decisions:
        timestamp_day = _session_date(source.timestamp, session_tz, session_start)
        if timestamp_day != source.day:
            raise ValueError(
                "decision day does not match its timestamp under the supplied "
                f"session convention: day={source.day.isoformat()}, "
                f"timestamp_day={timestamp_day.isoformat()}"
            )

        trade = trades_by_day.get(source.day) if source.decision == "accepted" else None
        if trade is not None and trade.entry_time <= source.timestamp:
            raise ValueError(
                "trade entry is not after its accepted decision: "
                f"session_day={source.day.isoformat()}, "
                f"decision_time={source.timestamp.isoformat()}, "
                f"entry_time={trade.entry_time.isoformat()}"
            )

        executed = trade is not None
        labeled_reason: OutcomeDecisionReason = source.decision
        if source.decision == "accepted" and not executed:
            labeled_reason = "not_executed"
        if executed:
            matched_trade_days.add(source.day)

        rows.append(
            LabeledAmdCrtDecision(
                day=source.day,
                weekday=source.weekday,
                direction=source.direction,
                crt_confirmed=source.crt_confirmed,
                ema_regime=source.ema_regime,
                atr=source.atr,
                sl_pts=source.sl_pts,
                tp_pts=source.tp_pts,
                pre_ny_amplitude=source.pre_ny_amplitude,
                median_amplitude=source.median_amplitude,
                decision=labeled_reason,
                timestamp=source.timestamp,
                r_result=trade.r_result if trade is not None else None,
                exit_reason=trade.exit_reason if trade is not None else None,
                executed=executed,
            )
        )

    unmatched_trade_days = sorted(set(trades_by_day) - matched_trade_days)
    if unmatched_trade_days:
        days = ", ".join(day.isoformat() for day in unmatched_trade_days)
        raise ValueError(f"trade(s) have no accepted decision for session day(s): {days}")

    return rows


__all__ = ["LabeledAmdCrtDecision", "join_decisions_to_outcomes"]
