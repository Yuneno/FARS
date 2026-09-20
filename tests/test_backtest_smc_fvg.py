"""Deterministic signal tests for the causal SMC-FVG port."""

from datetime import UTC, datetime, timedelta
import math
import pytest

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
    # RE-FREEZE DECLARADO (auditoria_fillbar + HERMES_REVISION_FILLBAR.md):
    # La prueba legada asumía que el TP se cobraba en la propia vela M5 del fill (barra 5).
    # Con la regla limpia A1, en la vela del fill solo SL cuenta; TP se evalúa a partir de la
    # vela siguiente.
    # 1) En la vela del fill (barra 5), el límite se llena a 111.0 pero la posición queda abierta (n_trades == 0).
    # 2) Con una vela posterior (barra 6) que cotiza el target (112.5), se preserva la
    #    aserción legada EXACTA de ejecución a take_profit.
    bars = _bullish_structure() + [_bar(5, 112, 113, 110.5, 112.5)]

    # 1. En la vela del fill la orden se llena pero NO cierra por TP
    result_fill = run_backtest(
        bars,
        SmcFvgStrategy(swing_w=1, min_risk_pts=0.0),
        smc_fvg_config(),
    )
    assert result_fill.n_trades == 0
    assert result_fill.unresolved_positions == 1
    assert result_fill.open_position["entry_price"] == 111.0

    # 2. Aserción legada exacta preservada en vela posterior que alcanza target
    bars_with_subsequent = bars + [_bar(6, 112.0, 113.0, 111.5, 112.5)]
    result = run_backtest(
        bars_with_subsequent,
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


def _bearish_structure():
    return [
        _bar(0, 100, 105, 99, 100),
        _bar(1, 100, 110, 95, 100),  # confirmed swing high at t=2
        _bar(2, 100, 101, 90, 95),   # confirmed swing low at t=3
        _bar(3, 95, 99, 92, 94),
        _bar(4, 88, 89, 85, 86),     # BOS + bearish FVG below low[t-2]=90
    ]


def test_smc_fvg_bearish_signal_emits_short_limit():
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    bars = _bearish_structure()

    assert strategy.evaluate(bars[:-1]) is None
    signal = strategy.evaluate(bars)

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry == 89.0  # high[t]
    assert signal.stop == 90.0 + 89.0e-4  # low[t-2] + epsilon
    assert signal.target == signal.entry - 1.5 * (signal.stop - signal.entry)
    assert strategy.decisions[-1].decision == "accepted"


def test_smc_fvg_strict_causality_no_lookahead():
    # Verify that future bars do not alter the signal or decision generated at bar t
    bars = _bullish_structure()
    s1 = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    sig1 = s1.evaluate(bars)
    dec1 = s1.decisions[-1]

    # Future bars provided bar-by-bar
    s2 = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    sig_at_4 = None
    for i in range(len(bars)):
        sig_at_4 = s2.evaluate(bars[: i + 1])
    dec_at_4 = s2.decisions[-1]

    assert sig1 == sig_at_4
    assert dec1.direction == dec_at_4.direction
    assert dec1.entry == dec_at_4.entry
    assert dec1.stop == dec_at_4.stop
    assert dec1.target == dec_at_4.target
    assert dec1.decision == dec_at_4.decision


def test_smc_fvg_partial_tp_and_breakeven_stop_execution():
    # Bar 0-4: bullish signal at bar 4 (entry=111.0, stop=110.0 - eps, risk ~ 1.0111)
    # tp1 = 111.0 + risk ~ 112.0111
    # Bar 5: low dips to 110.5 (fills limit entry at 111.0), high 111.5
    # Bar 6: high touches 112.5 (hits tp1, books 0.5R, moves stop to 111.0)
    # Bar 7: low dips to 110.0 (hits break-even stop at 111.0)
    bars = _bullish_structure() + [
        _bar(5, 112, 112, 110.5, 111.2),
        _bar(6, 111.2, 112.5, 111.1, 112.0),
        _bar(7, 112.0, 112.0, 110.0, 110.5),
    ]
    strategy = SmcFvgStrategy(swing_w=1, target_rr=3.0, min_risk_pts=0.0)
    config = smc_fvg_config(
        initial_balance=50_000.0,
        risk_per_trade=0.01,
        fixed_quantity=1,
    )
    result = run_backtest(bars, strategy, config)
    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "break_even_stop"
    assert trade.entry_price == 111.0
    assert trade.stop_price == 111.0
    assert trade.r_result > 0.0


def test_causality_signal_bar_cannot_fill_same_bar():
    """A signal formed at the close of bar t cannot fill on bar t itself."""
    bars = _bullish_structure()  # bars 0-4; bar 4 emits long limit at 111.0
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    config = smc_fvg_config(fixed_quantity=1)

    result = run_backtest(bars, strategy, config)
    assert result.n_trades == 0
    assert result.unresolved_positions == 0


def test_causality_limit_order_fills_only_when_next_bar_touches_level():
    """A pending limit fills only when a subsequent bar touches the limit price."""
    # Bar 5 does not touch entry 111.0 (low is 111.5).
    # Bar 6 touches entry 111.0 (low is 110.5, high is 111.8 - does not reach TP).
    bars = _bullish_structure() + [
        _bar(5, 114, 115, 111.5, 114),
        _bar(6, 111.5, 111.8, 110.5, 111.2),
    ]
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    config = smc_fvg_config(fixed_quantity=1)

    result = run_backtest(bars, strategy, config)
    # The order was filled on bar 6 and remains open at end of data
    assert result.n_trades == 0
    assert result.unresolved_positions == 1


def test_intrabar_resolution_pessimistic_entry_and_stop_same_bar():
    """When a single bar touches entry, TP, and stop, pessimistic resolution awards stop loss."""
    # Bar 5 touches entry 111.0, TP1 112.0, target 112.5, and stop 109.98
    bars = _bullish_structure() + [
        _bar(5, 111.0, 115.0, 105.0, 110.0),
    ]
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    config = smc_fvg_config(fixed_quantity=1)

    result = run_backtest(bars, strategy, config)
    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_time == bars[5].timestamp


def test_break_even_stop_does_not_trigger_on_tp1_trigger_bar():
    """When TP1 is reached and stop moves to BE, BE stop does not execute on the TP1 bar itself."""
    # Bar 5 fills entry at 111.0 (low=110.5, high=111.8; does not reach TP1=112.0)
    # Bar 6 dips to 110.8 (< entry 111.0) and rallies to 112.5 (touches TP1 112.0, moves stop to 111.0).
    # Crucially, bar 6 must NOT be stopped out at break-even on bar 6.
    # Bar 7 dips to 110.0 and hits the newly armed break-even stop at 111.0.
    bars = _bullish_structure() + [
        _bar(5, 111.5, 111.8, 110.5, 111.2),
        _bar(6, 111.2, 112.5, 110.8, 112.0),
        _bar(7, 112.0, 112.0, 110.0, 110.5),
    ]
    strategy = SmcFvgStrategy(swing_w=1, target_rr=3.0, min_risk_pts=0.0)
    config = smc_fvg_config(fixed_quantity=1)

    result = run_backtest(bars, strategy, config)
    assert result.n_trades == 1
    trade = result.trades[0]
    assert trade.exit_time == bars[7].timestamp
    assert trade.exit_reason == "break_even_stop"
    assert trade.entry_price == 111.0
    assert trade.stop_price == 111.0


@pytest.mark.parametrize("quantity", [1, 2, 3, 5])
@pytest.mark.parametrize("scenario", ["tp1_then_be", "tp1_then_target", "direct_stop_loss"])
def test_discrete_partial_contracts_long(quantity: int, scenario: str):
    """Verify discrete contracts execution for Longs across Q=1,2,3,5."""
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    config = smc_fvg_config(
        initial_balance=50_000.0,
        fixed_quantity=quantity,
        commission_per_side=2.0,
        discrete_partial_contracts=True,
    )
    base = _bullish_structure()
    if scenario == "tp1_then_be":
        bars = base + [
            _bar(5, 111.5, 111.8, 110.5, 111.2),
            _bar(6, 111.2, 112.2, 111.0, 112.0),
            _bar(7, 112.0, 112.0, 109.5, 110.0),
        ]
        result = run_backtest(bars, strategy, config)
        assert result.n_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "break_even_stop"
        assert t.quantity == quantity
        risk_pts = t.stop_risk_dollars / (config.dollar_per_point * quantity)
        q_tp1 = quantity // 2
        q_rem = quantity - q_tp1
        expected_gross = q_tp1 * risk_pts * config.dollar_per_point
        expected_comm = config.commission_per_side * 2 * quantity
        assert math.isclose(t.gross_pnl, expected_gross, abs_tol=1e-3)
        assert math.isclose(t.commission, expected_comm, abs_tol=1e-3)
        assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-3)
        if quantity == 1:
            assert t.gross_pnl == 0.0
            assert t.net_pnl == -expected_comm

    elif scenario == "tp1_then_target":
        bars = base + [
            _bar(5, 111.5, 111.8, 110.5, 111.2),
            _bar(6, 111.2, 112.5, 111.0, 112.0),
            _bar(7, 112.0, 113.0, 111.5, 112.8),
        ]
        result = run_backtest(bars, strategy, config)
        assert result.n_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "take_profit"
        assert t.quantity == quantity
        risk_pts = t.stop_risk_dollars / (config.dollar_per_point * quantity)
        q_tp1 = quantity // 2
        q_rem = quantity - q_tp1
        expected_gross = (q_tp1 * risk_pts + q_rem * 1.5 * risk_pts) * config.dollar_per_point
        expected_comm = config.commission_per_side * 2 * quantity
        assert math.isclose(t.gross_pnl, expected_gross, abs_tol=1e-3)
        assert math.isclose(t.commission, expected_comm, abs_tol=1e-3)
        assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-3)
        if quantity == 1:
            assert math.isclose(t.gross_pnl, 1.5 * risk_pts * config.dollar_per_point, abs_tol=1e-3)

    elif scenario == "direct_stop_loss":
        bars = base + [
            _bar(5, 111.5, 111.8, 110.5, 111.2),
            _bar(6, 111.0, 111.2, 108.0, 109.0),
        ]
        result = run_backtest(bars, strategy, config)
        assert result.n_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.quantity == quantity
        risk_pts = t.stop_risk_dollars / (config.dollar_per_point * quantity)
        expected_gross = -quantity * risk_pts * config.dollar_per_point
        expected_comm = config.commission_per_side * 2 * quantity
        assert math.isclose(t.gross_pnl, expected_gross, abs_tol=1e-3)
        assert math.isclose(t.commission, expected_comm, abs_tol=1e-3)
        assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-3)


@pytest.mark.parametrize("quantity", [1, 2, 3, 7])
@pytest.mark.parametrize("scenario", ["tp1_then_be", "tp1_then_target", "direct_stop_loss"])
def test_discrete_partial_contracts_short(quantity: int, scenario: str):
    """Verify discrete contracts execution for Shorts across Q=1,2,3,7."""
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    config = smc_fvg_config(
        initial_balance=50_000.0,
        fixed_quantity=quantity,
        commission_per_side=2.0,
        discrete_partial_contracts=True,
    )
    base = _bearish_structure()
    if scenario == "tp1_then_be":
        bars = base + [
            _bar(5, 88.5, 89.5, 88.2, 88.8),
            _bar(6, 88.8, 88.9, 87.8, 88.0),
            _bar(7, 88.0, 90.5, 88.0, 89.5),
        ]
        result = run_backtest(bars, strategy, config)
        assert result.n_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "break_even_stop"
        assert t.quantity == quantity
        risk_pts = t.stop_risk_dollars / (config.dollar_per_point * quantity)
        q_tp1 = quantity // 2
        q_rem = quantity - q_tp1
        expected_gross = q_tp1 * risk_pts * config.dollar_per_point
        expected_comm = config.commission_per_side * 2 * quantity
        assert math.isclose(t.gross_pnl, expected_gross, abs_tol=1e-3)
        assert math.isclose(t.commission, expected_comm, abs_tol=1e-3)
        assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-3)
        if quantity == 1:
            assert t.gross_pnl == 0.0
            assert t.net_pnl == -expected_comm

    elif scenario == "tp1_then_target":
        bars = base + [
            _bar(5, 88.5, 89.5, 88.2, 88.8),
            _bar(6, 88.8, 88.9, 87.8, 88.0),
            _bar(7, 88.0, 88.2, 87.0, 87.2),
        ]
        result = run_backtest(bars, strategy, config)
        assert result.n_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "take_profit"
        assert t.quantity == quantity
        risk_pts = t.stop_risk_dollars / (config.dollar_per_point * quantity)
        q_tp1 = quantity // 2
        q_rem = quantity - q_tp1
        expected_gross = (q_tp1 * risk_pts + q_rem * 1.5 * risk_pts) * config.dollar_per_point
        expected_comm = config.commission_per_side * 2 * quantity
        assert math.isclose(t.gross_pnl, expected_gross, abs_tol=1e-3)
        assert math.isclose(t.commission, expected_comm, abs_tol=1e-3)
        assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-3)

    elif scenario == "direct_stop_loss":
        bars = base + [
            _bar(5, 88.5, 89.5, 88.2, 88.8),
            _bar(6, 88.8, 91.0, 88.5, 90.5),
        ]
        result = run_backtest(bars, strategy, config)
        assert result.n_trades == 1
        t = result.trades[0]
        assert t.exit_reason == "stop_loss"
        assert t.quantity == quantity
        risk_pts = t.stop_risk_dollars / (config.dollar_per_point * quantity)
        expected_gross = -quantity * risk_pts * config.dollar_per_point
        expected_comm = config.commission_per_side * 2 * quantity
        assert math.isclose(t.gross_pnl, expected_gross, abs_tol=1e-3)
        assert math.isclose(t.commission, expected_comm, abs_tol=1e-3)
        assert math.isclose(t.net_pnl, t.gross_pnl - t.commission, abs_tol=1e-3)


def test_discrete_vs_continuous_equivalence_at_quantity_2():
    """At Q=2, discrete (1 at TP1, 1 at target) is mathematically identical to 50% continuous partial."""
    strategy = SmcFvgStrategy(swing_w=1, min_risk_pts=0.0)
    bars = _bullish_structure() + [
        _bar(5, 111.5, 111.8, 110.5, 111.2),
        _bar(6, 111.2, 112.5, 111.0, 112.0),
        _bar(7, 112.0, 113.0, 111.5, 112.8),
    ]
    cfg_cont = smc_fvg_config(fixed_quantity=2, commission_per_side=2.0, discrete_partial_contracts=False)
    cfg_disc = smc_fvg_config(fixed_quantity=2, commission_per_side=2.0, discrete_partial_contracts=True)

    res_cont = run_backtest(bars, strategy.fresh(), cfg_cont)
    res_disc = run_backtest(bars, strategy.fresh(), cfg_disc)

    assert res_cont.n_trades == 1
    assert res_disc.n_trades == 1
    tc = res_cont.trades[0]
    td = res_disc.trades[0]

    assert math.isclose(tc.gross_pnl, td.gross_pnl, abs_tol=1e-4)
    assert math.isclose(tc.commission, td.commission, abs_tol=1e-4)
    assert math.isclose(tc.net_pnl, td.net_pnl, abs_tol=1e-4)




