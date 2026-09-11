"""Deterministic signal tests for the causal SMC-FVG port."""

from datetime import UTC, datetime, timedelta

from src.backtest.executor import run_backtest
from src.backtest.history import Bar
from src.backtest.smc_fvg import SmcFvgStrategy, smc_fvg_config


def _bar(index, open_, high, low, close):
    return Bar(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * index),
        open_,
        high,
        low,
        close,
        10.0,
    )


def _bullish_structure():
    return [
        _bar(0, 100, 101, 95, 100),
        _bar(1, 100, 105, 90, 100),  # confirmed swing low at t=2
        _bar(2, 100, 110, 99, 105),  # confirmed swing high at t=3
        _bar(3, 105, 108, 101, 106),
        _bar(4, 112, 115, 111, 114),  # BOS + bullish FVG over high[t-2]=110
    ]


def test_smc_fvg_emits_limit_from_confirmed_closed_bar_only():
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    bars = _bullish_structure()

    assert strategy.evaluate(bars[:-1]) is None
    signal = strategy.evaluate(bars)

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry == 111.0
    assert signal.stop == 110.0 - 111.0e-4
    assert signal.target == signal.entry + 1.5 * (signal.entry - signal.stop)
    assert strategy.decisions[-1].decision == "accepted"


def test_smc_fvg_rejects_candidate_below_minimum_risk():
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=8.0)

    assert strategy.evaluate(_bullish_structure()) is None
    assert strategy.decisions[-1].decision == "risk_below_min"


def test_smc_fvg_fresh_copies_parameters_without_state():
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    assert strategy.evaluate(_bullish_structure()) is not None

    fresh = strategy.fresh()

    assert fresh.parameters() == strategy.parameters()
    assert fresh.decisions == []


def test_smc_fvg_limit_fills_on_retracement_to_fvg_edge():
    bars = _bullish_structure() + [_bar(5, 112, 113, 110.5, 112.5)]

    result = run_backtest(
        bars,
        SmcFvgStrategy(swing_w=1, min_risk_pts=0.0),
        smc_fvg_config(),
    )

    assert result.n_trades == 1
    assert result.trades[0].entry_price == 111.0
    assert result.trades[0].exit_reason == "take_profit"


def test_smc_fvg_limit_expiry_marks_decision_without_a_fill():
    bars = _bullish_structure() + [
        _bar(5, 114, 115, 113, 114),
        _bar(6, 114, 115, 113, 114),
    ]
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)

    result = run_backtest(
        bars,
        strategy,
        smc_fvg_config(pending_order_wait_bars=2),
    )

    assert result.n_trades == 0
    assert strategy.decisions[0].decision == "expired"
