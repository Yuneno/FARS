"""Deterministic signal tests for the causal EMAS port."""

from datetime import UTC, datetime, timedelta

from src.backtest.emas import EmasStrategy
from src.backtest.history import Bar


def _trend_bars():
    base = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    bars = []
    for index in range(22):
        close = 100.0 + index
        low = close - (1.0 if index < 21 else 1.5)
        bars.append(
            Bar(
                base + timedelta(minutes=5 * index),
                close - 0.5,
                close + 1.0,
                low,
                close,
                10.0,
            )
        )
    return bars


def _strategy():
    return EmasStrategy(
        confirm_closes=1,
        stop_min=1.0,
        p_entry=2,
        p_second=3,
        p_pullback=4,
        p_bias=5,
        max_dist_atr=2.0,
        vol3_max=0.0,
        htf_filter=False,
        session_block="",
        blackout="",
    )


def test_emas_emits_market_signal_after_closed_breakout_bar():
    strategy = _strategy()
    bars = _trend_bars()

    assert strategy.evaluate(bars[:-1]) is None
    signal = strategy.evaluate(bars)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.stop_target_as_points is True
    assert signal.stop > 0
    assert signal.target == 3.0 * signal.stop
    assert strategy.decisions[-1].decision == "accepted"
    assert strategy.decisions[-1].timestamp == bars[-1].timestamp


def test_emas_decision_snapshot_does_not_change_with_future_bar():
    strategy = _strategy()
    bars = _trend_bars()
    assert strategy.evaluate(bars) is not None
    snapshot = strategy.decisions[0]
    future = Bar(
        bars[-1].timestamp + timedelta(minutes=5),
        50.0,
        200.0,
        1.0,
        50.0,
        10.0,
    )

    strategy.observe([*bars, future])

    assert strategy.decisions[0] == snapshot


def test_emas_fresh_copies_parameters_without_state():
    strategy = _strategy()
    assert strategy.evaluate(_trend_bars()) is not None

    fresh = strategy.fresh()

    assert fresh.parameters() == strategy.parameters()
    assert fresh.decisions == []
