"""F1 — regression tests for motor fixes (branch fix/motor-r2).

Fix A: EOD ratchet + day_close consolidation in bootstrap_camino.
Fix C: fail-closed on monitoring_cadence / trailing_mode mismatch.
Fix D: end_of_day emission moves the HWM for end_of_day profiles.
Fix E: official Topstep constants (checked here, changed in funded_profiles).

Ronda 2 (Codex §4): C1 entry-vs-session last bar (+ MEDIO 6 flat_cutoff filter),
C2 deferred day_close pass, C3 strict trailing_mode + EOD flush + cross-session
fail-closed, C5 synced Topstep metadata + real-day tests.

Ronda 3 (F1_REVIEW_CODEX_R2): el flush day_close nunca sobrescribe un breach
+ aserción de exclusión mutua de estados terminales al retorno.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

LAB_ORB = Path(__file__).resolve().parents[1] / "lab_artifacts" / "orb_protocol"
if str(LAB_ORB) not in sys.path:
    sys.path.insert(0, str(LAB_ORB))

import bootstrap_camino as bc  # noqa: E402

EXT_SRC = os.environ.get(
    "FARS_EXT_NQ_BREAKOUT_SRC", r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src"
)
if EXT_SRC not in sys.path:
    sys.path.insert(0, EXT_SRC)

try:
    from nq_breakout.backtest import Config, SimFlags, run_backtest  # noqa: E402
except ModuleNotFoundError:  # repo externo nq-intraday-breakout no disponible (CI)
    Config = SimFlags = run_backtest = None  # type: ignore[assignment]

from src.account_engine import AccountEngineConfig, run_account_simulation  # noqa: E402
from src.backtest.executor import ExecutedTrade  # noqa: E402
from src.funded_profiles import apex_25k_profile, topstep_50k_profile  # noqa: E402


# ---------------------------------------------------------------------------
# Fix A helpers
# ---------------------------------------------------------------------------

def _same_day(n: int) -> list[datetime]:
    base = datetime(2026, 1, 5, 10, 0)
    return [base + timedelta(minutes=15 * i) for i in range(n)]


def _two_days(first_n: int) -> list[datetime]:
    out = [datetime(2026, 1, 5, 10, 0) + timedelta(minutes=15 * i) for i in range(first_n)]
    out.append(datetime(2026, 1, 6, 10, 0))
    return out


def _three_days(first_n: int, second_n: int) -> list[datetime]:
    """Tres días reales de trading: first_n trades el día 1, second_n el día 2, uno el día 3."""
    out = [datetime(2026, 1, 5, 10, 0) + timedelta(minutes=15 * i) for i in range(first_n)]
    out += [datetime(2026, 1, 6, 10, 0) + timedelta(minutes=15 * i) for i in range(second_n)]
    out.append(datetime(2026, 1, 7, 10, 0))
    return out


def _run(r_vals: list[float], timeline: list[datetime], **kw) -> dict:
    base = dict(
        initial_balance=50_000.0,
        risk_pct=1.0,  # min(equity, 200) -> size 1: trade_pnl == r * $200
        max_risk_dollars_order=200.0,
        stop_pts=100.0,
        point_value=2.0,
        min_trading_days=0,
    )
    base.update(kw)
    return bc.simulate_funded_trajectory(np.array(r_vals, dtype=float), timeline, **base)


# ---------------------------------------------------------------------------
# Fix A — trailing_update_cadence
# ---------------------------------------------------------------------------

def test_f1a_eod_floor_does_not_ratchet_intraday():
    """Two same-day trades (+$1,000 then -$500, then -$1,500).

    Legacy ratchets the floor to $49,000 after trade 1 -> trade 3 breaches.
    EOD keeps the floor at $48,000 all day -> trade 3 survives (intraday
    surveillance against the EOD floor, no breach).
    """
    r = [5.0, -2.5, -7.5]  # +1000, -500, -1500 with size-1 $200/contract sizing
    kw = dict(max_dd_limit_usd=2_000.0, dd_floor_ceiling=50_000.0)

    legacy = _run(r, _same_day(3), **kw)
    assert legacy["terminal_condition"] == "breach_trailing"

    eod = _run(r, _same_day(3), trailing_update_cadence="end_of_day", **kw)
    assert eod["terminal_condition"] == "horizon_exhausted"
    assert eod["final_equity"] == pytest.approx(49_000.0)


def test_f1a_eod_floor_still_watches_breach_intraday():
    """EOD is ratchet timing, not blindness: a same-day drop through the EOD
    floor must still breach on that trade."""
    r = [5.0, -15.0]  # +1000 then -3000 -> equity 48,000 <= EOD floor 48,000
    kw = dict(max_dd_limit_usd=2_000.0, dd_floor_ceiling=50_000.0,
              daily_loss_limit_usd=1_000_000_000.0)
    eod = _run(r, _same_day(2), trailing_update_cadence="end_of_day", **kw)
    assert eod["terminal_condition"] == "breach_trailing"


def test_f1a_day_close_uses_final_day_pnl():
    """Day 1: +$2,000 then -$500 (final +$1,500). Day 2: +$1,600.

    Legacy takes the intraday max ($2,000 > 50% of the $3,000 target) and
    raises the effective target to $4,000 -> day-2 equity $53,100 is not a pass.
    day_close consolidates the FINAL day P&L ($1,500) and evaluates the pass
    only at consolidation (ronda 2, Codex §4.2): at the day-2 flush the best
    day is $1,600 -> effective target $3,200 > $3,100 equity gain -> NO pass.
    (The pre-ronda-2 code passed here using the stale $1,500 best intraday.)
    """
    r = [10.0, -2.5, 8.0]
    kw = dict(
        profit_target_usd=3_000.0,
        consistency_max_day_ratio=0.5,
        max_dd_limit_usd=1_000_000_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
    )
    legacy = _run(r, _two_days(2), **kw)
    assert legacy["passed"] is False

    closed = _run(r, _two_days(2), consistency_day_consolidation="day_close", **kw)
    assert closed["passed"] is False
    assert closed["terminal_condition"] == "horizon_exhausted"
    assert closed["final_equity"] == pytest.approx(53_100.0)


def test_f1a_day_close_no_premature_pass():
    """Codex §4.2 repro: day 1 +$1,500, day 2 +$1,600, target $3,000, ratio 0.5.

    At the close the best day ($1,600) demands an adjusted target of $3,200,
    above the $3,100 equity gain -> must NOT pass. The pre-ronda-2 code
    returned passed=True at $53,100 using the not-yet-consolidated best day.
    """
    r = [7.5, 8.0]  # +1500 day 1, +1600 day 2 with size-1 $200/contract sizing
    kw = dict(
        profit_target_usd=3_000.0,
        consistency_max_day_ratio=0.5,
        max_dd_limit_usd=1_000_000_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
        min_trading_days=2,
    )
    out = _run(r, _two_days(1), consistency_day_consolidation="day_close", **kw)
    assert out["passed"] is False
    assert out["terminal_condition"] == "horizon_exhausted"
    assert out["final_equity"] == pytest.approx(53_100.0)


def test_f1a_day_close_three_days_best_day_holds():
    """Day 1 +$2,000, day 2 -$500, day 3 +$1,600 (target $3,000, ratio 0.5).

    The losing close must not lower the best day: best stays $2,000, so the
    effective target stays $4,000 and $53,100 is not a pass. If the best day
    ever reset (to 0 or to the loss), the target would drop to $3,000 and this
    run would wrongly pass — so passed=False proves the max-hold contract.
    """
    r = [10.0, -2.5, 8.0]
    kw = dict(
        profit_target_usd=3_000.0,
        consistency_max_day_ratio=0.5,
        max_dd_limit_usd=1_000_000_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
    )
    for mode in ("intraday_max", "day_close"):
        out = _run(r, _three_days(1, 1), consistency_day_consolidation=mode, **kw)
        assert out["passed"] is False
        assert out["final_equity"] == pytest.approx(53_100.0)


def test_f1a_day_close_last_day_flush_passes():
    """A single final winning trade must still pass — at the last-day flush.

    Guards against overcorrection when deferring the day_close pass check:
    one +$3,000 trade on a single day (target $3,000, no consistency rule,
    min 1 trading day) passes with terminal_condition="target" even though no
    intraday pass check ever runs in day_close mode.
    """
    out = _run(
        [15.0],
        _same_day(1),
        consistency_day_consolidation="day_close",
        profit_target_usd=3_000.0,
        max_dd_limit_usd=1_000_000_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
        min_trading_days=1,
    )
    assert out["passed"] is True
    assert out["terminal_condition"] == "target"
    assert out["final_equity"] == pytest.approx(53_000.0)


def test_f1a_day_close_losses_do_not_reset_best_day():
    """A losing day after a winning day must not lower best_day_pnl (only up).

    Day 1 final +$2,000; day 2 final -$500 — two REAL days (ronda 2, Codex
    MEDIO 8: the old version ran both trades on the same day despite the
    day-1/day-2 docstring). The observable contract is that the losing close
    leaves the pass/target logic untouched (no reset to 0 or to the loss):
    with a $3,000 target and ratio 0.5, best stays $2,000, so the effective
    target stays $4,000 in both modes and neither passes at $51,500.
    """
    r = [10.0, -2.5]
    kw = dict(
        profit_target_usd=3_000.0,
        consistency_max_day_ratio=0.5,
        max_dd_limit_usd=1_000_000_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
    )
    for mode in ("intraday_max", "day_close"):
        out = _run(r, _two_days(1), consistency_day_consolidation=mode, **kw)
        assert out["passed"] is False
        assert out["final_equity"] == pytest.approx(51_500.0)


def test_f1a_rejects_unknown_modes():
    with pytest.raises(ValueError, match="trailing_update_cadence"):
        _run([1.0], _same_day(1), trailing_update_cadence="weekly")
    with pytest.raises(ValueError, match="consistency_day_consolidation"):
        _run([1.0], _same_day(1), consistency_day_consolidation="weekly")


# ---------------------------------------------------------------------------
# Fix B ronda 2 (Codex §4.1 + MEDIO 6) — último bar de ENTRADA vs de SESIÓN
# ---------------------------------------------------------------------------

def _bars_5min(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """rows: (hh:mm, open, high, low, close) del 2026-01-05."""
    return pd.DataFrame(
        [
            {"date": pd.Timestamp(f"2026-01-05 {hhmm}"), "open": o,
             "high": h, "low": low, "close": c}
            for hhmm, o, h, low, c in rows
        ]
    )


def _feed_entry_parity() -> pd.DataFrame:
    """Ventana 08:30-10:00 plana @100; breakout 10:05 (cerrado por target en
    10:10); segundo breakout exactamente en el bar 14:30 de corte de entradas;
    plano hasta 15:10."""
    rows = []
    t = datetime(2026, 1, 5, 8, 30)
    while t.time() < time(10, 0):
        rows.append((t.strftime("%H:%M"), 100.0, 100.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    rows.append(("10:00", 100.0, 100.0, 100.0, 100.0))
    rows.append(("10:05", 100.0, 101.0, 99.9, 100.5))   # entrada long
    rows.append(("10:10", 100.0, 103.0, 100.0, 102.0))  # toca target -> cierra
    t = datetime(2026, 1, 5, 10, 15)
    while t.time() < time(14, 30):
        rows.append((t.strftime("%H:%M"), 100.0, 100.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    rows.append(("14:30", 100.0, 101.0, 99.9, 100.5))   # breakout en el corte
    t = datetime(2026, 1, 5, 14, 35)
    while t.time() <= time(15, 10):
        rows.append((t.strftime("%H:%M"), 100.0, 100.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    return _bars_5min(rows)


def _entry_keys(trades: pd.DataFrame) -> set:
    return {(pd.Timestamp(t), d) for t, d in zip(trades["entry_time"], trades["direction"])}


@pytest.mark.skipif(
    run_backtest is None,
    reason="nq_breakout externo no disponible (FARS_EXT_NQ_BREAKOUT_SRC)",
)
def test_f1b_split_entries_match_legacy_keys():
    """Codex §4.1: con entry_end=14:30 + flat_time=15:10 las ENTRADAS deben ser
    exactamente las del legacy — claves (entry_time, direction) idénticas,
    0 añadidas / 0 retiradas (no basta n/media). Solo las salidas cambian.
    El código pre-ronda-2 habilitaba la entrada de las 14:30 (último bar de
    sesión movido a 15:10); el correcto la bloquea (último bar de ENTRADAS)."""
    tiny = dict(stop_dollars=20.0, target_dollars=40.0)
    feed = _feed_entry_parity()
    legacy = run_backtest(feed, Config(**tiny), SimFlags()).trades
    split = run_backtest(
        feed,
        Config(entry_end=time(14, 30), flat_time=time(15, 10), **tiny),
        SimFlags(),
    ).trades
    assert _entry_keys(split) == _entry_keys(legacy)
    assert len(split) == 1  # solo la entrada de las 10:05 (test no vacuo)
    assert (pd.Timestamp("2026-01-05 14:30"), "long") not in _entry_keys(split)


def _feed_flat_visibility() -> pd.DataFrame:
    """Breakdown 14:25 (entrada short) y plano hasta 15:10."""
    rows = []
    t = datetime(2026, 1, 5, 8, 30)
    while t.time() < time(10, 0):
        rows.append((t.strftime("%H:%M"), 100.0, 100.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    rows.append(("10:00", 100.0, 100.0, 100.0, 100.0))
    t = datetime(2026, 1, 5, 10, 5)
    while t.time() < time(14, 25):
        rows.append((t.strftime("%H:%M"), 100.0, 100.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    rows.append(("14:25", 100.0, 100.0, 99.0, 99.9))  # entrada short
    t = datetime(2026, 1, 5, 14, 30)
    while t.time() <= time(15, 10):
        rows.append((t.strftime("%H:%M"), 100.0, 100.0, 100.0, 100.0))
        t += timedelta(minutes=5)
    return _bars_5min(rows)


@pytest.mark.skipif(
    run_backtest is None,
    reason="nq_breakout externo no disponible (FARS_EXT_NQ_BREAKOUT_SRC)",
)
def test_f1b_scan_false_uses_flat_cutoff():
    """Codex MEDIO 6: con scan_overnight_bars=False el feed se filtra hasta
    flat_time (no trade_end). El short abierto a las 14:25 debe cerrar EOD a
    las 15:10; con el filtro viejo cerraba a las 14:30 sin flat real."""
    feed = _feed_flat_visibility()
    tiny = dict(stop_dollars=20.0, target_dollars=40.0)
    res = run_backtest(
        feed,
        Config(entry_end=time(14, 30), flat_time=time(15, 10), **tiny),
        SimFlags(scan_overnight_bars=False),
    ).trades
    assert len(res) == 1
    assert res.iloc[0]["direction"] == "short"
    assert pd.Timestamp(res.iloc[0]["exit_time"]) == pd.Timestamp("2026-01-05 15:10")
    assert res.iloc[0]["exit_reason"] == "eod_short"


# ---------------------------------------------------------------------------
# Fix C — fail-closed on monitoring_cadence / trailing_mode mismatch
# ---------------------------------------------------------------------------

def _engine_trade(trade_id: str, entry_time: datetime, net_pnl: float,
                  budgeted_risk_dollars: float = 200.0) -> ExecutedTrade:
    return ExecutedTrade(
        trade_id=trade_id,
        direction="long",
        entry_time=entry_time,
        exit_time=entry_time + timedelta(minutes=10),
        entry_price=100.0,
        exit_price=101.0,
        stop_price=99.0,
        target_price=105.0,
        quantity=1,
        gross_pnl=net_pnl + 2.0,
        commission=1.0,
        net_pnl=net_pnl,
        r_result=net_pnl / budgeted_risk_dollars,
        exit_reason="take_profit" if net_pnl > 0 else "stop_loss",
        budgeted_risk_dollars=budgeted_risk_dollars,
        effective_risk_dollars=budgeted_risk_dollars,
    )


def test_f1c_cadence_mismatch_raises_fail_closed():
    """monitoring_cadence != trailing_mode debe negar la corrida con ValueError
    (antes re-derivaba un perfil Apex en silencio: bug de seguridad)."""
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    bad_cfg = AccountEngineConfig(risk_pct=0.01, trailing_mode="intraday_event")

    with pytest.raises(ValueError) as exc_topstep:
        run_account_simulation(
            topstep_50k_profile(), [_engine_trade("t1", t0, 100.0)], bad_cfg
        )
    msg = str(exc_topstep.value)
    assert "topstep-combine-50k" in msg
    assert "monitoring_cadence='closed_trade'" in msg
    assert "trailing_mode='intraday_event'" in msg
    assert "no calza" in msg

    with pytest.raises(ValueError, match="no calza"):
        run_account_simulation(
            apex_25k_profile(), [_engine_trade("t1", t0, 100.0)], bad_cfg
        )


def test_f1c_matching_cadence_still_runs():
    """El fail-closed no debe romper las combinaciones que si calzan."""
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    res = run_account_simulation(
        topstep_50k_profile(),
        [_engine_trade("t1", t0, 100.0)],
        AccountEngineConfig(risk_pct=0.004, trailing_mode="closed_trade"),
    )
    assert res.trades_executed == 1


def test_f1c_unknown_trailing_mode_raises():
    """Codex §4.3: trailing_mode fuera de {"closed_trade", "intraday_event"} se
    niega con ValueError (antes caía en silencio a "closed_trade")."""
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="trailing_mode"):
        run_account_simulation(
            topstep_50k_profile(),
            [_engine_trade("t1", t0, 100.0)],
            AccountEngineConfig(risk_pct=0.004, trailing_mode="TYPO"),
        )


# ---------------------------------------------------------------------------
# Fix D — end_of_day emission ratchets the HWM at day close, not per trade
# ---------------------------------------------------------------------------

def test_f1d_topstep_floor_ratchets_at_day_close_not_per_trade():
    """Dos trades en dias distintos con perfil Topstep (+$1,000 dia 1, +$100 dia 2).

    Sin Fix D el HWM nunca se mueve (update_cadence=end_of_day sin eventos que
    lo muevan) y el piso queda congelado en $48,000 (sesgo optimista). Con
    Fix D, al cambiar de dia se emite end_of_day con el cierre ($51,000):
    HWM=$51,000 y piso=min(51,000-2,000, 50,000)=$49,000; y con el flush de
    ronda 2 (Codex §4.3) el día 2 cierra a $51,100 (HWM/piso finales).
    """
    t1 = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)  # 09:00 CT
    t2 = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)  # dia siguiente
    trades = [
        _engine_trade("d1", t1, 1_000.0),
        _engine_trade("d2", t2, 100.0),
    ]
    res = run_account_simulation(
        topstep_50k_profile(),
        trades,
        AccountEngineConfig(risk_pct=0.004, trailing_mode="closed_trade"),
    )
    assert res.trades_executed == 2
    # Tras el trade 1 (mismo dia) el piso aun no ratcheto: 50,000 - 2,000.
    assert res.records[0].floor_after == pytest.approx(48_000.0)
    # Al cerrar el dia 1 el HWM subio al cierre ($51,000) y el piso a $49,000.
    assert res.records[1].floor_after == pytest.approx(49_000.0)
    # Ronda 2 (Codex §4.3): el flush final cierra el DIA 2 al balance final
    # ($51.100): HWM=$51.100 y piso=min(51.100-2.000, 50.000)=$49.100.
    assert res.high_watermark == pytest.approx(51_100.0)
    assert res.final_floor == pytest.approx(49_100.0)
    assert res.final_balance == pytest.approx(51_100.0)


def test_f1d_single_final_winning_trade_closes_last_day():
    """Codex §4.3: un solo trade +$1.000 debe cerrar el último día — HWM
    $51.000 y piso $49.000 al cierre (antes quedaban $50.000/$48.000 porque no
    existía flush EOD después del loop)."""
    t0 = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)  # 09:00 CT, misma sesión
    res = run_account_simulation(
        topstep_50k_profile(),
        [_engine_trade("solo", t0, 1_000.0)],
        AccountEngineConfig(risk_pct=0.004, trailing_mode="closed_trade"),
    )
    assert res.trades_executed == 1
    assert res.final_balance == pytest.approx(51_000.0)
    assert res.high_watermark == pytest.approx(51_000.0)
    assert res.final_floor == pytest.approx(49_000.0)


def _engine_trade_window(
    trade_id: str,
    entry_time: datetime,
    exit_time: datetime,
    net_pnl: float,
    budgeted_risk_dollars: float = 200.0,
) -> ExecutedTrade:
    return ExecutedTrade(
        trade_id=trade_id,
        direction="long",
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=100.0,
        exit_price=101.0,
        stop_price=99.0,
        target_price=105.0,
        quantity=1,
        gross_pnl=net_pnl + 2.0,
        commission=1.0,
        net_pnl=net_pnl,
        r_result=net_pnl / budgeted_risk_dollars,
        exit_reason="take_profit" if net_pnl > 0 else "stop_loss",
        budgeted_risk_dollars=budgeted_risk_dollars,
        effective_risk_dollars=budgeted_risk_dollars,
    )


def test_f1d_cross_session_trade_raises_fail_closed():
    """Codex §4.3 boundary: entrada 21:50Z (sesión 2026-09-01 CT) y cierre
    23:59Z (sesión 2026-09-02 CT) con perfil EOD y sin mark de la posición
    abierta → ValueError fail-closed (no atribuir el balance posterior al día
    anterior)."""
    entry = datetime(2026, 9, 1, 21, 50, tzinfo=timezone.utc)
    exit_ = datetime(2026, 9, 1, 23, 59, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="frontera de sesi"):
        run_account_simulation(
            topstep_50k_profile(),
            [_engine_trade_window("x1", entry, exit_, 100.0)],
            AccountEngineConfig(risk_pct=0.004, trailing_mode="closed_trade"),
        )


# ---------------------------------------------------------------------------
# Fix E — official Topstep constants
# ---------------------------------------------------------------------------

def test_f1e_topstep_official_constants():
    """minimum_trading_days=2 (oficial 'as few as two days') y advisory de
    consistencia 55% con target=best_day/0.55."""
    profile = topstep_50k_profile()
    assert profile.minimum_trading_days == 2
    soft = profile.operational.advisory_constraints["consistency_soft"]
    assert "55%" in soft
    assert "best_day/0.55" in soft


# ---------------------------------------------------------------------------
# Ronda 3 (F1_REVIEW_CODEX_R2) — el flush day_close nunca sobrescribe un breach
# ---------------------------------------------------------------------------

def test_f1r3_trailing_breach_survives_qualifying_day_close_flush():
    """Regresión crítica C2: subida a $57,000 y crash a $53,000 en el mismo día
    (target $3,000, max DD $2,000, modo day_close sin regla de consistencia).

    El último trade rompe el trailing (57,000-53,000=4,000 >= 2,000) con la
    equity EXACTAMENTE en el target ($53,000), de modo que el flush final
    "calificaría" como pase. El código pre-ronda-3 sobrescribía el breach y
    devolvía hit_dd=True con passed=True y terminal="target" (estado doble).
    Debe quedar breach_trailing, passed=False, sin estado doble.
    """
    r = [10.0, 10.0, 10.0, 5.0, -20.0]  # +2000 x3, +1000, -4000 (size 1, $200)
    out = _run(
        r,
        _same_day(5),
        consistency_day_consolidation="day_close",
        profit_target_usd=3_000.0,
        max_dd_limit_usd=2_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
    )
    assert out["final_equity"] == pytest.approx(53_000.0)
    assert out["hit_dd"] is True
    assert out["passed"] is False
    assert out["terminal_condition"] == "breach_trailing"
    assert not (out["passed"] and (out["hit_dd"] or out["hit_daily"]))


@pytest.mark.parametrize(
    "hit_dd,hit_daily,passed,terminal",
    [
        # Estado doble clásico de la regresión: breach + pase a la vez.
        (True, False, True, "target"),
        (False, True, True, "target"),
        (True, False, True, "breach_trailing"),
        # Hit sin terminal de breach.
        (True, False, False, "horizon_exhausted"),
        (False, True, False, "horizon_exhausted"),
        # Pase sin terminal de target.
        (False, False, True, "horizon_exhausted"),
        (False, False, True, "breach_trailing"),
        # Terminal sin su estado.
        (False, False, False, "target"),
        (False, False, False, "breach_trailing"),
        (False, False, False, "breach_daily"),
    ],
)
def test_f1r3_terminal_exclusivity_assertion_rejects_double_state(
    hit_dd, hit_daily, passed, terminal
):
    """La aserción de exclusión mutua falla ruidosamente ante cualquier
    combinación contradictoria (breach+pase, hit sin breach, target sin pase,
    breach sin su flag)."""
    with pytest.raises(AssertionError):
        bc._validate_terminal_exclusivity(hit_dd, hit_daily, passed, terminal)


@pytest.mark.parametrize(
    "hit_dd,hit_daily,passed,terminal",
    [
        (False, False, True, "target"),
        (True, False, False, "breach_trailing"),
        (False, True, False, "breach_daily"),
        (False, False, False, "horizon_exhausted"),
    ],
)
def test_f1r3_terminal_exclusivity_assertion_accepts_clean_states(
    hit_dd, hit_daily, passed, terminal
):
    """Los cuatro estados terminales limpios no disparan la aserción."""
    bc._validate_terminal_exclusivity(hit_dd, hit_daily, passed, terminal)


def test_f1r3_day_close_flush_still_passes_legit_climb():
    """El guard ronda 3 no debe impedir el pase legítimo: día 1 +$1,000
    (bajo el target), día 2 +$2,500 que cruza el target $3,000 sin ningún
    breach — el flush del último día declara target (min 2 días de trading)."""
    out = _run(
        [5.0, 12.5],
        _two_days(1),
        consistency_day_consolidation="day_close",
        profit_target_usd=3_000.0,
        max_dd_limit_usd=1_000_000_000.0,
        daily_loss_limit_usd=1_000_000_000.0,
        min_trading_days=2,
    )
    assert out["final_equity"] == pytest.approx(53_500.0)
    assert out["hit_dd"] is False
    assert out["hit_daily"] is False
    assert out["passed"] is True
    assert out["terminal_condition"] == "target"
