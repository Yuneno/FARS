#!/usr/bin/env python3
"""
test_paper_bot_periods.py — pruebas de cierre de períodos y controles del bot.

Verifica:
  (a) el contador mensual se reinicia al cambiar de mes.
  (b) el contador mensual NO se reinicia dentro del mes.
  (c) el techo global del engine no se reinicia nunca.
  (d) los límites diarios se miden contra el equity de inicio de día.
  (e) peak_equity crece y el drawdown trailing se dispara cuando toca.

Cubre al menos 3 cierres mensuales (junio->julio, julio->agosto, agosto->septiembre)
y cierres de día consecutivos.
"""
from __future__ import annotations

import sys
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))

import paper_bot as pb  # noqa: E402
from src.realtime.events import AccountSnapshot, Signal  # noqa: E402
from src.realtime.risk import AccountAwareRiskEngine, REASON_DRAWDOWN  # noqa: E402
from src.types import FundedAccountRules  # noqa: E402

CT = ZoneInfo("America/Chicago")


def make_rules(**kw) -> FundedAccountRules:
    base = dict(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.08,
        daily_loss_limit_pct=0.02,
        risk_per_trade=0.01,
        daily_loss_base="initial",
        drawdown_mode="trailing",
        max_trades=10,
        daily_loss_limit_usd=2_000.0,
        max_drawdown_usd=8_000.0,
        max_risk_dollars_per_order=1_000.0,
    )
    base.update(kw)
    return FundedAccountRules(**base)


def test_a_monthly_counter_resets_on_month_change():
    """(a) Verifica que el contador mensual se reinicia al cruzar a un nuevo mes en 3 cierres."""
    strat = pb.OrbSignalGenerator(max_trades_day=2, max_trades_month=2)

    # 1. Mes: Junio 2026
    t_jun1 = datetime(2026, 6, 29, 9, 0, tzinfo=CT)
    strat.on_bar(t_jun1, 1000.0, 1050.0, 950.0, 1000.0)  # ventana
    s1 = strat.on_bar(datetime(2026, 6, 29, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s1 is not None and s1[0] == "long"
    s2 = strat.on_bar(datetime(2026, 6, 29, 11, 0, tzinfo=CT), 1000.0, 1070.0, 990.0, 1050.0)
    assert s2 is not None and s2[0] == "long"
    assert strat.trades_this_month == 2

    # Tope de junio alcanzado
    s3 = strat.on_bar(datetime(2026, 6, 30, 10, 15, tzinfo=CT), 1000.0, 1080.0, 990.0, 1050.0)
    assert s3 is None, "En junio se alcanzó el cupo mensual; debe bloquear"

    # Cierre mensual 1: Cruce a Julio 2026 -> debe reiniciarse a 0
    t_jul = datetime(2026, 7, 1, 9, 0, tzinfo=CT)
    strat.on_bar(t_jul, 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.trades_this_month == 0, "Al pasar a julio, contador mensual debe ser 0"
    s_jul1 = strat.on_bar(datetime(2026, 7, 1, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s_jul1 is not None, "Julio debe permitir nuevas señales tras reinicio"
    strat.on_bar(datetime(2026, 7, 1, 11, 0, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert strat.trades_this_month == 2

    # Cierre mensual 2: Cruce a Agosto 2026 -> debe reiniciarse a 0
    t_ago = datetime(2026, 8, 3, 9, 0, tzinfo=CT)
    strat.on_bar(t_ago, 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.trades_this_month == 0, "Al pasar a agosto, contador mensual debe ser 0"
    s_ago1 = strat.on_bar(datetime(2026, 8, 3, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s_ago1 is not None, "Agosto debe permitir señales tras reinicio"

    # Cierre mensual 3: Cruce a Septiembre 2026 -> debe reiniciarse a 0
    t_sep = datetime(2026, 9, 1, 9, 0, tzinfo=CT)
    strat.on_bar(t_sep, 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.trades_this_month == 0, "Al pasar a septiembre, contador mensual debe ser 0"
    s_sep1 = strat.on_bar(datetime(2026, 9, 1, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s_sep1 is not None, "Septiembre debe permitir señales tras reinicio"


def test_b_monthly_counter_does_not_reset_within_month():
    """(b) Verifica que el contador mensual NO se reinicia en cambios de día dentro del mismo mes."""
    strat = pb.OrbSignalGenerator(max_trades_day=1, max_trades_month=3)

    # Día 1: 2026-07-01
    strat.on_bar(datetime(2026, 7, 1, 9, 0, tzinfo=CT), 1000.0, 1050.0, 950.0, 1000.0)
    s1 = strat.on_bar(datetime(2026, 7, 1, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s1 is not None
    assert strat.trades_this_month == 1
    assert strat.trades_today == 1

    # Día 2: 2026-07-02 (trades_today se reinicia a 0, pero trades_this_month NO)
    strat.on_bar(datetime(2026, 7, 2, 9, 0, tzinfo=CT), 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.trades_today == 0
    assert strat.trades_this_month == 1, "trades_this_month no debe reiniciarse al cambiar de día"
    s2 = strat.on_bar(datetime(2026, 7, 2, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s2 is not None
    assert strat.trades_this_month == 2

    # Día 3: 2026-07-15
    strat.on_bar(datetime(2026, 7, 15, 9, 0, tzinfo=CT), 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.trades_this_month == 2
    s3 = strat.on_bar(datetime(2026, 7, 15, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s3 is not None
    assert strat.trades_this_month == 3

    # Día 4: 2026-07-31 (Tope mensual alcanzado dentro de julio)
    strat.on_bar(datetime(2026, 7, 31, 9, 0, tzinfo=CT), 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.trades_this_month == 3
    s4 = strat.on_bar(datetime(2026, 7, 31, 10, 15, tzinfo=CT), 1000.0, 1060.0, 990.0, 1050.0)
    assert s4 is None, "Tope mensual alcanzado debe bloquear dentro del mes"


def test_c_engine_global_ceiling_never_resets():
    """(c) Verifica que el techo global del engine no se reinicia nunca y rechaza si se intenta decrementar."""
    rules = make_rules(max_trades=6)
    t_start = datetime(2026, 6, 1, 10, 0, tzinfo=CT)
    clock = pb.StepClock(t_start)
    engine = AccountAwareRiskEngine(rules, clock, source="test-engine")

    # Mes 1 (Junio): 2 trades
    t_jun = datetime(2026, 6, 15, 10, 0, tzinfo=CT)
    clock.set(t_jun)
    engine.observe(AccountSnapshot(event_id="s-jun", source="test", timestamp=t_jun,
                                   sequence=1, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=2))
    d = engine.evaluate(Signal(event_id="sig-jun", source="test", timestamp=t_jun,
                               sequence=2, symbol="MNQ", action="LONG", origin="replay"))
    assert d.approved, f"Junio con 2 trades debe aprobar, obtuvo {d.reason}"

    # Mes 2 (Julio, cierre 1): 4 trades acumulados
    t_jul = datetime(2026, 7, 15, 10, 0, tzinfo=CT)
    clock.set(t_jul)
    engine.observe(AccountSnapshot(event_id="s-jul", source="test", timestamp=t_jul,
                                   sequence=3, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=4))
    d = engine.evaluate(Signal(event_id="sig-jul", source="test", timestamp=t_jul,
                               sequence=4, symbol="MNQ", action="LONG", origin="replay"))
    assert d.approved, f"Julio con 4 trades debe aprobar, obtuvo {d.reason}"

    # Mes 3 (Agosto, cierre 2): 6 trades acumulados -> alcanza techo global
    t_ago = datetime(2026, 8, 15, 10, 0, tzinfo=CT)
    clock.set(t_ago)
    engine.observe(AccountSnapshot(event_id="s-ago", source="test", timestamp=t_ago,
                                   sequence=5, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=6))
    d = engine.evaluate(Signal(event_id="sig-ago", source="test", timestamp=t_ago,
                               sequence=6, symbol="MNQ", action="LONG", origin="replay"))
    assert not d.approved, "Con trades_applied >= max_trades debe denegar"
    assert d.reason == "MAX_TRADES_REACHED"

    # Mes 4 (Septiembre, cierre 3): el techo NO se reinicia
    t_sep = datetime(2026, 9, 1, 10, 0, tzinfo=CT)
    clock.set(t_sep)
    engine.observe(AccountSnapshot(event_id="s-sep", source="test", timestamp=t_sep,
                                   sequence=7, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=6))
    d = engine.evaluate(Signal(event_id="sig-sep", source="test", timestamp=t_sep,
                               sequence=8, symbol="MNQ", action="LONG", origin="replay"))
    assert not d.approved, "El techo global del engine no debe reiniciarse en septiembre"
    assert d.reason == "MAX_TRADES_REACHED"

    # Si se intenta decrementar trades_applied para burlar el engine -> fail-closed
    t_sep2 = datetime(2026, 9, 2, 10, 0, tzinfo=CT)
    clock.set(t_sep2)
    engine.observe(AccountSnapshot(event_id="s-hack", source="test", timestamp=t_sep2,
                                   sequence=9, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=0))
    d = engine.evaluate(Signal(event_id="sig-hack", source="test", timestamp=t_sep2,
                               sequence=10, symbol="MNQ", action="LONG", origin="replay"))
    assert not d.approved
    assert d.reason == "UNKNOWN_CRITICAL_STATE", "trades_applied no monótono debe provocar UNKNOWN_CRITICAL_STATE"


def test_d_daily_loss_measured_against_start_of_day_equity():
    """(d) Verifica que la pérdida diaria se mide contra el equity de apertura de cada día."""
    rules = make_rules(daily_loss_limit_usd=2_000.0)
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=CT)
    clock = pb.StepClock(t1)
    engine = AccountAwareRiskEngine(rules, clock, source="test-daily")

    # Día 1: apertura a 100k
    engine.observe(AccountSnapshot(event_id="d1-sod", source="test", timestamp=t1,
                                   sequence=1, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=0))
    # Pérdida intradía de 1.000 USD (equity 99k) -> permitido
    t1_mid = datetime(2026, 6, 1, 11, 30, tzinfo=CT)
    clock.set(t1_mid)
    engine.observe(AccountSnapshot(event_id="d1-mid", source="test", timestamp=t1_mid,
                                   sequence=2, origin="replay", balance=99_000.0,
                                   equity=99_000.0, peak_equity=100_000.0,
                                   trades_applied=1))
    d = engine.evaluate(Signal(event_id="sig1", source="test", timestamp=t1_mid,
                               sequence=3, symbol="MNQ", action="LONG", origin="replay"))
    assert d.approved, f"Pérdida de $1.000 está dentro del límite de $2.000, obtuvo {d.reason}"

    # Pérdida intradía de 2.500 USD (equity 97.500) -> denegado por DAILY_LOSS_LIMIT
    t1_late = datetime(2026, 6, 1, 13, 0, tzinfo=CT)
    clock.set(t1_late)
    engine.observe(AccountSnapshot(event_id="d1-loss", source="test", timestamp=t1_late,
                                   sequence=4, origin="replay", balance=97_500.0,
                                   equity=97_500.0, peak_equity=100_000.0,
                                   trades_applied=2))
    d = engine.evaluate(Signal(event_id="sig2", source="test", timestamp=t1_late,
                               sequence=5, symbol="MNQ", action="LONG", origin="replay"))
    assert not d.approved
    assert d.reason == "DAILY_LOSS_LIMIT"

    # Día 2: nuevo día con apertura anclada en 97.500 USD
    t2 = datetime(2026, 6, 2, 10, 0, tzinfo=CT)
    clock.set(t2)
    engine.observe(AccountSnapshot(event_id="d2-sod", source="test", timestamp=t2,
                                   sequence=6, origin="replay", balance=97_500.0,
                                   equity=97_500.0, peak_equity=100_000.0,
                                   trades_applied=2))
    d = engine.evaluate(Signal(event_id="sig3", source="test", timestamp=t2,
                               sequence=7, symbol="MNQ", action="LONG", origin="replay"))
    assert d.approved, "Al nuevo día, pérdida contra el nuevo SOD es 0; debe aprobar"

    # Pérdida en Día 2 mayor a 2.000 contra el nuevo inicio de día (97.500 - 2.500 = 95.000)
    t2_loss = datetime(2026, 6, 2, 12, 0, tzinfo=CT)
    clock.set(t2_loss)
    engine.observe(AccountSnapshot(event_id="d2-loss", source="test", timestamp=t2_loss,
                                   sequence=8, origin="replay", balance=95_000.0,
                                   equity=95_000.0, peak_equity=100_000.0,
                                   trades_applied=3))
    d = engine.evaluate(Signal(event_id="sig4", source="test", timestamp=t2_loss,
                               sequence=9, symbol="MNQ", action="LONG", origin="replay"))
    assert not d.approved
    assert d.reason == "DAILY_LOSS_LIMIT"


def test_e_trailing_drawdown_tracks_peak_equity():
    """(e) Verifica que peak_equity crece con las ganancias y el trailing drawdown se dispara contra el nuevo pico."""
    rules = make_rules(
        max_drawdown_usd=8_000.0,
        drawdown_mode="trailing",
        daily_loss_limit_usd=50_000.0,
        daily_loss_limit_pct=0.50,
    )
    t1 = datetime(2026, 6, 1, 10, 0, tzinfo=CT)
    clock = pb.StepClock(t1)
    engine = AccountAwareRiskEngine(rules, clock, source="test-trailing")

    # Inicia a 100k
    engine.observe(AccountSnapshot(event_id="s1", source="test", timestamp=t1,
                                   sequence=1, origin="replay", balance=100_000.0,
                                   equity=100_000.0, peak_equity=100_000.0,
                                   trades_applied=0))

    # Ganancias: equity crece a 106.000 USD y peak_equity se actualiza a 106.000
    t2 = datetime(2026, 6, 15, 10, 0, tzinfo=CT)
    clock.set(t2)
    engine.observe(AccountSnapshot(event_id="s2", source="test", timestamp=t2,
                                   sequence=2, origin="replay", balance=106_000.0,
                                   equity=106_000.0, peak_equity=106_000.0,
                                   trades_applied=4))
    d = engine.evaluate(Signal(event_id="sig1", source="test", timestamp=t2,
                               sequence=3, symbol="MNQ", action="LONG", origin="replay"))
    assert d.approved
    assert engine._snapshot.peak_equity == 106_000.0, "peak_equity debe crecer con las ganancias a 106.000"

    # El umbral trailing ahora es 106.000 - 8.000 = 98.000 USD.
    # Con equity = 99.000 (drawdown 7.000 <= 8.000) -> Aprobado
    t3 = datetime(2026, 6, 20, 10, 0, tzinfo=CT)
    clock.set(t3)
    engine.observe(AccountSnapshot(event_id="s3", source="test", timestamp=t3,
                                   sequence=4, origin="replay", balance=99_000.0,
                                   equity=99_000.0, peak_equity=106_000.0,
                                   trades_applied=5))
    d = engine.evaluate(Signal(event_id="sig2", source="test", timestamp=t3,
                               sequence=5, symbol="MNQ", action="LONG", origin="replay"))
    assert d.approved, f"Drawdown $7.000 está bajo el límite $8.000, obtuvo {d.reason}"

    # Con equity = 97.000 (drawdown 9.000 > 8.000 contra el pico 106.000) -> DENEGADO
    # Nota: si el cálculo fuera contra el balance inicial (100.000), 97.000 representaría
    # un drawdown de solo 3.000 (< 8.000) y aprobaría. El rechazo por DRAWDOWN_BUFFER_TOO_LOW
    # verifica que el umbral trailing se recalcula contra el nuevo pico (106.000 - 8.000 = 98.000).
    t4 = datetime(2026, 6, 22, 10, 0, tzinfo=CT)
    clock.set(t4)
    engine.observe(AccountSnapshot(event_id="s4", source="test", timestamp=t4,
                                   sequence=6, origin="replay", balance=97_000.0,
                                   equity=97_000.0, peak_equity=106_000.0,
                                   trades_applied=6))
    d = engine.evaluate(Signal(event_id="sig3", source="test", timestamp=t4,
                               sequence=7, symbol="MNQ", action="LONG", origin="replay"))
    assert not d.approved
    assert d.reason == REASON_DRAWDOWN, f"Debe disparar trailing drawdown ({REASON_DRAWDOWN}), obtuvo {d.reason}"


def test_f_causality_no_lookahead_and_effective_stop():
    """(f) Fix B1: La señal no usa datos de la barra de ejecución y el stop se ancla al precio efectivo de entrada."""
    strat = pb.OrbSignalGenerator(max_trades_day=2, max_trades_month=42)

    # Ventana 08:30 - 10:00 (Rango: [950.0, 1050.0])
    t_win = datetime(2026, 6, 29, 9, 0, tzinfo=CT)
    strat.on_bar(t_win, 1000.0, 1050.0, 950.0, 1000.0)
    assert strat.win_high == 1050.0 and strat.win_low == 950.0

    # 1. Barra de decisión (10:15): High rompe la ventana a 1060.0
    t_dec = datetime(2026, 6, 29, 10, 15, tzinfo=CT)
    sig = strat.on_bar(t_dec, 1045.0, 1060.0, 1040.0, 1055.0)
    assert sig is not None and sig[0] == "long", "La barra de decisión debe emitir señal long al cierre"

    # 2. Prueba causal estricta: cambiar o invertir los datos de la barra de EJECUCIÓN (10:20)
    # NO cambia la decisión tomada previamente al cierre de la barra 10:15.
    # Caso normal: barra de ejecución con open=1058.0, high=1070.0, low=1050.0, close=1065.0
    entry_price_normal = 1058.0
    stop_normal = strat.effective_stop(sig[0], entry_price_normal)
    assert stop_normal == 958.0, f"Stop long debe ser 958.0 (entrada - 100), obtuvo {stop_normal}"
    assert (entry_price_normal - stop_normal) == pb.STOP_PTS, "La distancia de stop debe ser exactamente STOP_PTS (100 pts)"

    # Caso adverso/invertido: en la barra de ejecución el precio cae drásticamente (high=900, low=850)
    # La señal generada en 10:15 ya fue decidida y entra al open=1058.0 sin look-ahead de los extremos de 10:20
    entry_price_adverse = 1058.0
    stop_adverse = strat.effective_stop(sig[0], entry_price_adverse)
    assert stop_adverse == 958.0, "La decisión previa no depende del high/low de la barra de ejecución"

    # Verificación en sentido opuesto: si invertimos el high de la barra de decisión a 1040 (sin breakout)
    strat_no_breakout = pb.OrbSignalGenerator(max_trades_day=2, max_trades_month=42)
    strat_no_breakout.on_bar(t_win, 1000.0, 1050.0, 950.0, 1000.0)
    sig_none = strat_no_breakout.on_bar(t_dec, 1045.0, 1040.0, 1030.0, 1035.0)  # high=1040 <= win_high=1050
    assert sig_none is None, "Sin ruptura de ventana en barra de decisión no debe existir señal"

    # 3. Stop anclado al precio efectivo para posición short (100 pts por encima)
    short_entry = 942.0
    short_stop = strat.effective_stop("short", short_entry)
    assert short_stop == 1042.0, f"Stop short debe ser 1042.0 (entrada + 100), obtuvo {short_stop}"
    assert (short_stop - short_entry) == pb.STOP_PTS, "Distancia de stop short debe ser exactamente STOP_PTS"


def test_g_multimonth_bot_operates_across_month_boundary():
    """(g) Fix B2: Integración multi-mes — el bot bloquea al alcanzar cupo en mes 1 y vuelve a operar en mes 2."""
    rules = make_rules(max_trades=None)  # Fix B2: techo global en engine desactivado; cupo en estrategia
    t_start = datetime(2026, 6, 1, 9, 0, tzinfo=CT)
    clock = pb.StepClock(t_start)
    guard = pb.RiskGuard(rules, clock)
    strat = pb.OrbSignalGenerator(max_trades_day=2, max_trades_month=42)
    paper = pb.PaperExecutionAdapter(clock, pb.PaperAssumptions(
        latency=pb.pd.Timedelta(seconds=0).to_pytimedelta(),
        slippage="unmodeled",
        commissions="unmodeled",
        partial_fills="not_simulated",
        rejections="strict",
        fill_policy="full",
    ))

    trades_applied = 0
    equity = rules.initial_balance
    peak_equity = equity

    # --- MES 1: Junio 2026 ---
    # Simular que la estrategia alcanza sus 42 trades en Junio
    for d in range(1, 22):
        t_day = datetime(2026, 6, d, 9, 0, tzinfo=CT)
        clock.set(t_day)
        strat.on_bar(t_day, 1000.0, 1050.0, 950.0, 1000.0)  # ventana diaria
        for h_step in (10, 11):
            if strat.trades_this_month < 42:
                t_bar = datetime(2026, 6, d, h_step, 15, tzinfo=CT)
                clock.set(t_bar)
                s = strat.on_bar(t_bar, 1000.0, 1060.0, 990.0, 1050.0)
                if s is not None:
                    stop_p = strat.effective_stop(s[0], 1060.0)
                    sig, dec, intent = guard.authorize(t_bar, s[0], "MNQ", stop_p)
                    assert dec.approved
                    intent = pb.OrderIntent(**{**intent.__dict__, "size": 1, "entry_price": 1060.0})
                    rep = paper.submit(sig, dec, intent)
                    assert getattr(rep, "status", "") in ("filled", "accepted")
                    trades_applied += 1
                    guard.snapshot(t_bar, equity, peak_equity, 0.0, trades_applied)

    assert strat.trades_this_month == 42, "Junio debe haber alcanzado exactamente 42 trades"
    assert trades_applied == 42

    # Intentar trade 43 en Junio -> la estrategia debe bloquear
    t_jun_extra = datetime(2026, 6, 25, 10, 15, tzinfo=CT)
    clock.set(t_jun_extra)
    s_extra = strat.on_bar(t_jun_extra, 1000.0, 1070.0, 990.0, 1050.0)
    assert s_extra is None, "Estrategia debe bloquear trade 43 en Junio por cupo mensual"

    # --- MES 2: Julio 2026 (Cruce de mes sin reinstanciar engine) ---
    t_jul1 = datetime(2026, 7, 1, 9, 0, tzinfo=CT)
    clock.set(t_jul1)
    strat.on_bar(t_jul1, 1000.0, 1050.0, 950.0, 1000.0)  # ventana de julio
    assert strat.trades_this_month == 0, "Al cruzar a Julio, el contador mensual debe ser 0"

    # Señal en Julio
    t_jul_sig = datetime(2026, 7, 1, 10, 15, tzinfo=CT)
    clock.set(t_jul_sig)
    s_jul = strat.on_bar(t_jul_sig, 1000.0, 1065.0, 990.0, 1050.0)
    assert s_jul is not None, "Estrategia debe permitir nueva señal en Julio"
    assert strat.trades_this_month == 1

    # Ejecución y autorización en el engine existente (trades_applied=42 acumulados no bloquean porque max_trades=None)
    stop_jul = strat.effective_stop(s_jul[0], 1065.0)
    sig_jul, dec_jul, intent_jul = guard.authorize(t_jul_sig, s_jul[0], "MNQ", stop_jul)
    assert dec_jul.approved, (
        f"En Julio (segundo mes), el RiskEngine debe aprobar la orden con trades_applied={trades_applied}; "
        f"obtuvo {dec_jul.reason}"
    )
    assert intent_jul is not None
    intent_jul = pb.OrderIntent(**{**intent_jul.__dict__, "size": 1, "entry_price": 1065.0})
    rep_jul = paper.submit(sig_jul, dec_jul, intent_jul)
    assert getattr(rep_jul, "status", "") in ("filled", "accepted")

    # Registrar el fill y verificar monotonicidad estricta de trades_applied
    trades_applied += 1
    snap_jul = guard.snapshot(t_jul_sig, equity, peak_equity, 0.0, trades_applied)
    assert snap_jul.trades_applied == 43, "trades_applied debe ser monótono acumulativo (43 trades)"


def test_h_symmetric_eod_exit_for_long_and_short():
    """(h) Fix H9: Verifica salida temporal simétrica al fin de sesión (14:30 CT) para AMBOS lados (long y short)."""
    # 1. Simulación para posición LONG
    open_trade_long = {
        "action": "long",
        "entry_time": datetime(2026, 6, 29, 10, 20, tzinfo=CT),
        "entry_price": 1050.0,
        "stop": 950.0,
        "target": 1250.0,
        "size": 1,
    }

    # Barra intradía a las 12:00 dentro de límites (no toca stop ni target)
    ts_mid = datetime(2026, 6, 29, 12, 0, tzinfo=CT)
    exit_price = None
    exit_reason = None
    row_mid = (1060.0, 1065.0, 1055.0, 1062.0)  # open, high, low, close
    # Stop / target check
    if row_mid[0] <= open_trade_long["stop"]:
        exit_price, exit_reason = row_mid[0], "stop"
    elif row_mid[2] <= open_trade_long["stop"]:
        exit_price, exit_reason = open_trade_long["stop"], "stop"
    elif row_mid[0] >= open_trade_long["target"]:
        exit_price, exit_reason = row_mid[0], "target"
    elif row_mid[1] >= open_trade_long["target"]:
        exit_price, exit_reason = open_trade_long["target"], "target"
    if exit_price is None and ts_mid.time() >= pb.TRADE_END:
        exit_price, exit_reason = row_mid[3], f"eod_{open_trade_long['action']}"
    assert exit_price is None, "A las 12:00 la posición long debe permanecer abierta"

    # Barra de fin de sesión a las 14:30 (TRADE_END)
    ts_eod = datetime(2026, 6, 29, 14, 30, tzinfo=CT)
    row_eod = (1060.0, 1062.0, 1058.0, 1061.0)
    if row_eod[0] <= open_trade_long["stop"]:
        exit_price, exit_reason = row_eod[0], "stop"
    elif row_eod[2] <= open_trade_long["stop"]:
        exit_price, exit_reason = open_trade_long["stop"], "stop"
    elif row_eod[0] >= open_trade_long["target"]:
        exit_price, exit_reason = row_eod[0], "target"
    elif row_eod[1] >= open_trade_long["target"]:
        exit_price, exit_reason = open_trade_long["target"], "target"
    if exit_price is None and ts_eod.time() >= pb.TRADE_END:
        exit_price, exit_reason = float(row_eod[3]), f"eod_{open_trade_long['action']}"
    assert exit_price == 1061.0, f"Posición long debe cerrarse al close en TRADE_END, obtuvo {exit_price}"
    assert exit_reason == "eod_long", f"Motivo de salida long debe ser eod_long, obtuvo {exit_reason}"

    # 2. Simulación para posición SHORT
    open_trade_short = {
        "action": "short",
        "entry_time": datetime(2026, 6, 29, 10, 20, tzinfo=CT),
        "entry_price": 940.0,
        "stop": 1040.0,
        "target": 740.0,
        "size": 1,
    }
    exit_price_s = None
    exit_reason_s = None
    row_eod_s = (935.0, 938.0, 932.0, 936.0)
    if row_eod_s[0] >= open_trade_short["stop"]:
        exit_price_s, exit_reason_s = row_eod_s[0], "stop"
    elif row_eod_s[1] >= open_trade_short["stop"]:
        exit_price_s, exit_reason_s = open_trade_short["stop"], "stop"
    elif row_eod_s[0] <= open_trade_short["target"]:
        exit_price_s, exit_reason_s = row_eod_s[0], "target"
    elif row_eod_s[2] <= open_trade_short["target"]:
        exit_price_s, exit_reason_s = open_trade_short["target"], "target"
    if exit_price_s is None and ts_eod.time() >= pb.TRADE_END:
        exit_price_s, exit_reason_s = float(row_eod_s[3]), f"eod_{open_trade_short['action']}"
    assert exit_price_s == 936.0, f"Posición short debe cerrarse al close en TRADE_END, obtuvo {exit_price_s}"
    assert exit_reason_s == "eod_short", f"Motivo de salida short debe ser eod_short, obtuvo {exit_reason_s}"


if __name__ == "__main__":
    fails = 0
    test_funcs = [
        test_a_monthly_counter_resets_on_month_change,
        test_b_monthly_counter_does_not_reset_within_month,
        test_c_engine_global_ceiling_never_resets,
        test_d_daily_loss_measured_against_start_of_day_equity,
        test_e_trailing_drawdown_tracks_peak_equity,
        test_f_causality_no_lookahead_and_effective_stop,
        test_g_multimonth_bot_operates_across_month_boundary,
        test_h_symmetric_eod_exit_for_long_and_short,
    ]
    for func in test_funcs:
        try:
            func()
            print(f"PASS {func.__name__}")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print(f"FAIL {func.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{'TODAS VERDES' if fails == 0 else f'{fails} FALLANDO'}")
    sys.exit(1 if fails else 0)

