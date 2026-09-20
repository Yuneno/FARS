"""Signal parity, causal streaming cache and FARS execution integration."""
from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path

import numpy as np
import pytest

from src.backtest.history import Bar
from src.backtest.executor import run_backtest
from src.backtest.markets import MGC, MYM
from src.backtest.smc_ob import SmcObStrategy, _SmcObSignal, _atr, smc_ob_config


def bars_from(h, l, c):
    return [Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=5*i),
                float(ci), float(hi), float(li), float(ci), 10.)
            for i, (hi, li, ci) in enumerate(zip(h, l, c))]


def test_atr_prefix_and_incremental_cache_are_exact_and_causal():
    rng = np.random.default_rng(102)
    c = 1000 + rng.normal(size=2200).cumsum()
    spread = rng.uniform(0.1, 3, len(c))
    spread[::101] = 30
    h, l = c + spread, c - spread
    expected = _atr(h, l, c)
    for n in (1, 200, 700, 2199):
        np.testing.assert_array_equal(_atr(h[:n], l[:n], c[:n]), expected[:n])
    strategy = SmcObStrategy()
    bars = bars_from(h, l, c)
    history = []
    for i, b in enumerate(bars):
        history.append(b)
        strategy.observe(history)
        assert strategy._atr_acc == expected[i]
    ref = _SmcObSignal(10, 3, True, 60)
    ref._ensure_parsed(h, l, c)
    np.testing.assert_array_equal(strategy._detector._p_hi, ref._p_hi)
    np.testing.assert_array_equal(strategy._detector._p_lo, ref._p_lo)
    assert np.any(h - l >= 2 * expected)


def test_pivot_confirmed_at_t_minus_w_only():
    h = np.array([2., 3., 7., 4., 3.])
    l = np.array([1., 0., -3., 1., 2.])
    c = (h + l) / 2
    s = _SmcObSignal(2, 3, False, 60)
    s.update_pivots(3, h[:4], l[:4], c[:4])
    assert not s.ready()
    s.update_pivots(4, h, l, c)
    assert (s.sh_i, s.sl_i, s.sh_p, s.sl_p) == (2, 2, 7., -3.)


def prepared(choch=False, trend=0):
    s = _SmcObSignal(1, 3, choch, 60)
    s.sh_p, s.sh_i, s.sh_cross = 105., 0, False
    s.sl_p, s.sl_i, s.sl_cross = 115., 0, False
    s.trend = trend
    h = np.array([120., 118., 117.])
    l = np.array([100., 98., 108.])
    c = np.array([110., 110., 110.])
    s._ensure_parsed(h, l, c)
    return s, h, l, c


def test_same_bar_both_breaks_bearish_overwrites_bullish():
    s, h, l, c = prepared()
    spec = s.structure_and_arm(2, h, l, c, False)
    assert spec == dict(side=-1, entry=100., sl=121., risk=21., tp1=79., tp=37., w=36)
    assert s.sh_cross and s.sl_cross and s.trend == -1


@pytest.mark.parametrize('choch,trend,expected', [(True, 0, False), (True, -1, True), (False, 0, True)])
def test_choch_filter_still_mutates_structure(choch, trend, expected):
    s, h, l, c = prepared(choch, trend)
    s.sl_cross = True
    spec = s.structure_and_arm(2, h, l, c, False)
    assert (spec is not None) == expected
    assert s.sh_cross and s.trend == 1
    if expected:
        assert spec == dict(side=1, entry=118., sl=97., risk=21., tp1=139., tp=181., w=36)


def test_choch_second_branch_sees_first_branch_trend_mutation():
    s, h, l, c = prepared(True, 0)
    assert s.structure_and_arm(2, h, l, c, False)['side'] == -1


def test_price_based_buffer_and_tied_argmin_choose_first():
    s, _, _, _ = prepared()
    s.sl_cross = True
    h = np.array([10002., 10003., 10005.])
    l = np.array([10000., 10000., 10004.])
    c = (h+l)/2
    s._plen = -1
    s._ensure_parsed(h, l, c)
    spec = s.structure_and_arm(2, h, l, c, False)
    assert spec['entry'] == 10002.
    assert spec['sl'] == pytest.approx(9998.9998)
    assert spec['risk'] == pytest.approx(3.0002)
    assert spec['tp1'] == pytest.approx(10005.0002)
    assert spec['tp'] == pytest.approx(10011.0006)


def bullish_bars():
    return bars_from([101, 105, 110, 108, 115], [95, 90, 99, 101, 111], [100, 100, 105, 106, 114])


def test_wrapper_signal_fresh_pending_observe_and_logging():
    bars = bullish_bars()
    s = SmcObStrategy(market=MYM, swing_w=1, choch_only=False)
    signal = s.evaluate(bars)
    assert signal.direction == 'long'
    assert (signal.entry, signal.stop, signal.target) == pytest.approx((110, 98.45, 144.65))
    fresh = s.fresh()
    assert fresh.parameters() == s.parameters()
    assert not fresh.decisions and fresh._seen == 0
    s.note_trade(bars[-1].timestamp, 2.)
    assert s.decisions[-1].decision == 'closed'
    fresh.set_execution_state(pending=True, position=False, cooldown=0)
    assert fresh.evaluate(bars) is None
    assert not fresh._detector.sh_cross
    observed = s.fresh()
    observed.observe(bars)
    assert not observed._detector.sh_cross
    with pytest.raises(ValueError, match='append-only'):
        s.evaluate(bars[:-1])


def test_executor_limit_partial_then_break_even():
    # RE-FREEZE DECLARADO (bloque-fix-fillbar / auditoria_fillbar + HERMES_REVISION_FILLBAR.md):
    # Con la regla A1 limpia, en la vela del fill (bar 5, low=109 -> fill a 110.0) no se
    # acredita tp1 parcial. Se agrega la barra 6 (high=122) para activar tp1 y mover stop a BE,
    # y la barra 7 (low=109) para ejecutar la salida a break-even.
    bars = bullish_bars() + bars_from([115, 122, 115], [109, 110, 109], [112, 120, 110])
    bars = [Bar(datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(minutes=5*i),
                b.open, b.high, b.low, b.close, b.volume) for i, b in enumerate(bars)]
    cfg = smc_ob_config(fixed_quantity=2)
    result = run_backtest(bars, SmcObStrategy(swing_w=1, choch_only=False), cfg)
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.entry_time == bars[5].timestamp
    assert t.exit_reason == 'break_even_stop'
    assert t.effective_r == pytest.approx(.5)
    assert t.r_result == pytest.approx(t.net_pnl / t.budgeted_risk_dollars)


def test_market_factory_and_defaults():
    cfg = smc_ob_config(market=MGC)
    assert (cfg.dollar_per_point, cfg.tick_size) == (10., .1)
    assert cfg.pending_limit_entry and cfg.discrete_partial_contracts
    assert cfg.partial_take_profit_fraction == .5 and cfg.move_stop_to_break_even
    assert cfg.pending_order_wait_bars == 36 and cfg.cooldown_bars == 0


def test_streaming_signals_match_full_parsed_reference_with_execution_gates():
    rng = np.random.default_rng(609)
    c = 1000 + rng.normal(0, 5, size=1400).cumsum()
    h, l = c + rng.uniform(.1, 5, len(c)), c - rng.uniform(.1, 5, len(c))
    bars = bars_from(h, l, c)
    reference = _SmcObSignal(3, 3, True, 60)
    strategy = SmcObStrategy(swing_w=3)
    history = []
    emitted = 0
    for i, bar in enumerate(bars):
        history.append(bar)
        reference.update_pivots(i, h, l, c)
        pending, position = i % 11 < 2, i % 17 < 3
        strategy.set_execution_state(pending=pending, position=position, cooldown=0)
        expected = reference.structure_and_arm(i, h, l, c, pending) if reference.ready() and not (pending or position) else None
        if position:
            strategy.observe(history)
            actual = None
        else:
            actual = strategy.evaluate(history)
        assert (actual is None) == (expected is None)
        if actual is not None:
            emitted += 1
            assert actual.entry == expected['entry']
            assert actual.stop == expected['sl']
            assert actual.target == expected['tp']
            assert actual.direction == ('long' if expected['side'] == 1 else 'short')
        for name in ('sh_p', 'sh_i', 'sl_p', 'sl_i', 'sh_cross', 'sl_cross', 'trend'):
            assert getattr(strategy._detector, name) == getattr(reference, name)
    assert emitted > 10


def test_runner_por_tramo_reprices_remaining_contracts_in_market_dollars():
    from dataclasses import replace
    from lab_artifacts.smcob_protocol.run_smcob_multimercado import reprice
    from src.backtest.executor import ExecutedTrade
    timestamp = bullish_bars()[0].timestamp
    trade = ExecutedTrade(trade_id='cost-test', direction='long', entry_time=timestamp,
                          exit_time=timestamp, entry_price=100., exit_price=100.,
                          stop_price=100., target_price=130., quantity=3,
                          gross_pnl=100., commission=0., net_pnl=100., r_result=1.,
                          exit_reason='break_even_stop', budgeted_risk_dollars=300.,
                          effective_risk_dollars=240.)
    # floor(3*.5)=1 partial contract; 2 contracts remain and slip.
    result = reprice(trade, MGC)
    assert result.commission == pytest.approx(3.72)
    assert result.slippage_cost == 5.
    assert result.net_pnl == pytest.approx(91.28)
    assert result.r_result == pytest.approx(91.28 / 300)
    target = reprice(replace(trade, exit_reason='take_profit'), MYM)
    assert target.slippage_cost == 0.
    stopped = reprice(replace(trade, exit_reason='stop_loss', stop_price=90.), MYM)
    assert stopped.slippage_cost == .375


def test_reference_detector_ast_exact_when_kai_checkout_available():
    path = Path('C:/Users/yo/Documents/GitHub/kai-backtesting/src/kai_bt/strategies/strat_smc_ob_signal.py')
    if not path.exists():
        pytest.skip('external Kai checkout unavailable; deterministic parity fixtures still run')
    import src.backtest.smc_ob as port
    original = ast.parse(path.read_text(encoding='utf-8'))
    actual = ast.parse(Path(port.__file__).read_text(encoding='utf-8'))
    orig_atr = next(n for n in original.body if isinstance(n, ast.FunctionDef))
    port_atr = next(n for n in actual.body if isinstance(n, ast.FunctionDef))
    assert ast.dump(orig_atr) == ast.dump(port_atr)
    orig_class = next(n for n in original.body if isinstance(n, ast.ClassDef))
    port_class = next(n for n in actual.body if isinstance(n, ast.ClassDef))
    orig_class.name = port_class.name
    arm = next(n for n in orig_class.body if isinstance(n, ast.FunctionDef) and n.name == 'structure_and_arm')
    arm.body = [n for n in arm.body if not isinstance(n, ast.ImportFrom)]
    assert ast.dump(orig_class) == ast.dump(port_class)
