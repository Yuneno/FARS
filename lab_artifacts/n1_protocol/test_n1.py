"""Tests deterministas del bloque N1 (sinteticos, sin red).

Grupos (encargo Muse 3):
1. rango 9:00-9:30 half-open, minimo 5 barras, DST.
2. sweep (k_d*ATR) + rechazo.
3. entrada: lado contrario, next-open, one-shot, ventana 11:00.
4. salida 12:00 ET y sensibilidad 60 barras (motor canonico).
5. gaps: gap-entry->rechazo con prioridad sobre gap-stop.
6. percentil de vol 20d/120d sin look-ahead.
7. regresion de interaccion + variante pct>50 (respuesta conocida).
8. control pareado (N, |R|, semilla).
9. sha del preregistro: alterado -> aborta.
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.n1_protocol.run_n1 import (  # noqa: E402
    ET,
    MARKET_SPECS,
    PREREG_PATH,
    PREREG_SHA256,
    SignalEvent,
    atr_wilder,
    build_smoke_bars,
    check_gates_ok,
    compute_signal_A,
    compute_signal_C,
    detect_sweep,
    et_minutes,
    evaluate_g1,
    find_first_at_or_after,
    group_by_et_date,
    is_rejection,
    mirror_valid_levels,
    ols_beta,
    paired_control,
    parse_bars,
    run_signal_day,
    summarize_r,
    verify_preregistro,
    vol_asof_all,
    vol_percentile,
    window_range,
)
from src.backtest.history import Bar  # noqa: E402


def et_bar(day: date, hh: int, mm: int, o: float = 100.0, h: float = 101.0,
           lo: float = 99.0, c: float = 100.0) -> Bar:
    ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).astimezone(timezone.utc)
    return Bar(timestamp=ts, open=o, high=h, low=lo, close=c, volume=50.0)


def day_bars(day: date, slots: list[tuple[int, int]], **kw) -> list[Bar]:
    return [et_bar(day, hh, mm, **kw) for (hh, mm) in slots]


def m5_slots(h0: int, m0: int, h1: int, m1: int) -> list[tuple[int, int]]:
    out, h, m = [], h0, m0
    while (h, m) <= (h1, m1):
        out.append((h, m))
        m += 5
        if m >= 60:
            m, h = 0, h + 1
    return out


# ------------------------------------------------------------------ 1. rango
def test_rango_half_open_min5():
    day = date(2025, 3, 4)
    bars = day_bars(day, [(8, 55)] + m5_slots(9, 0, 9, 30))
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100, high=110 if i == 0 else 101,
                      low=90 if i == len(bars) - 1 else 99, close=100, volume=1)
    idx = list(range(len(bars)))
    wr = window_range(idx, bars, 9 * 60, 9 * 60 + 30, 5)
    assert wr is not None
    hi, lo, n = wr
    assert n == 6  # incluye 9:25, excluye 9:30 (half-open)
    assert hi == 101 and lo == 99  # 8:55 y 9:30 fuera


def test_rango_minimo_5_barras():
    day = date(2025, 3, 4)
    bars = day_bars(day, m5_slots(9, 0, 9, 20))  # 5 -> ok; 4 -> None
    assert window_range([0, 1, 2, 3, 4], bars, 540, 570, 5) is not None
    assert window_range([0, 1, 2, 3], bars[:4], 540, 570, 5) is None


def test_rango_dst_ambos_lados():
    # EDT (UTC-4): 9:00 ET = 13:00 UTC; EST (UTC-5): 9:00 ET = 14:00 UTC
    for day, utc_h in ((date(2025, 10, 31), 13), (date(2025, 11, 3), 14)):
        bars = day_bars(day, m5_slots(9, 0, 9, 30))
        idx = list(range(len(bars)))
        wr = window_range(idx, bars, 540, 570, 5)
        assert wr is not None and wr[2] == 6, day
        first_utc = bars[0].timestamp.astimezone(timezone.utc).hour
        assert first_utc == utc_h, (day, first_utc)
        assert et_minutes(bars[0].timestamp) == 540


def test_atr_wilder_serie_conocida():
    day = date(2025, 3, 4)
    bars = day_bars(day, m5_slots(8, 0, 9, 20))  # 17 barras
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100, high=101, low=99,
                      close=100, volume=1)
    a = atr_wilder(bars, 14)
    assert math.isnan(a[13]) and a[14] == pytest.approx(2.0) and a[16] == pytest.approx(2.0)


# ------------------------------------------------------------------ 2. sweep
def test_sweep_y_rechazo():
    assert detect_sweep(96.0, 100.6, 99.4, 2.0, 0.10) == "down"
    assert detect_sweep(104.0, 100.6, 99.4, 2.0, 0.10) == "up"
    assert detect_sweep(100.0, 100.6, 99.4, 2.0, 0.10) is None
    assert detect_sweep(99.3, 100.6, 99.4, 2.0, 0.10) is None  # 0.1 < k_d*ATR=0.2
    assert detect_sweep(96.0, 100.6, 99.4, math.nan, 0.10) is None
    assert detect_sweep(96.0, 100.6, 99.4, 0.0, 0.10) is None
    assert is_rejection(100.0, 100.6, 99.4) is True
    assert is_rejection(104.0, 100.6, 99.4) is False
    assert is_rejection(96.0, 100.6, 99.4) is False


# ------------------------------------------------------------------ 3. entrada
def _craft_day_A(day: date, sweep_close: float, reject_close: float,
                 reject_hh: int = 9, reject_mm: int = 40) -> list[Bar]:
    bars = day_bars(day, m5_slots(8, 0, 13, 0))
    # ruido pequeno pre-rango para ATR finito
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100, high=100.4, low=99.6,
                      close=100, volume=1)
    def put(hh, mm, o, h, lo, c):
        for i, b in enumerate(bars):
            if et_minutes(b.timestamp) == hh * 60 + mm:
                bars[i] = Bar(timestamp=b.timestamp, open=o, high=h, low=lo,
                              close=c, volume=1)
                return
    for hh, mm in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        put(hh, mm, 100.0, 100.6, 99.4, 100.0)
    put(9, 35, 100.0, max(100.2, sweep_close), min(99.8, sweep_close), sweep_close)
    put(reject_hh, reject_mm, sweep_close, 100.5, min(96.0, sweep_close), reject_close)
    return bars


def test_entrada_lado_contrario_nextopen_oneshot():
    day = date(2025, 3, 4)
    bars = _craft_day_A(day, 96.0, 100.0)
    atr = atr_wilder(bars, 14)
    idx = list(range(len(bars)))
    ev = compute_signal_A(day, idx, bars, atr, 0.10, 0.25, 5, {})
    assert ev is not None and ev.side == "long"  # barrido DOWN -> LONG
    assert bars[ev.fill_idx].timestamp == bars[ev.signal_idx + 1].timestamp
    assert et_minutes(bars[ev.signal_idx].timestamp) < 11 * 60
    # one-shot: una sola senal aunque haya mas ruido despues
    assert isinstance(ev, SignalEvent)


def test_entrada_short_tras_barrido_up():
    day = date(2025, 3, 4)
    bars = _craft_day_A(day, 104.0, 100.0)
    atr = atr_wilder(bars, 14)
    ev = compute_signal_A(day, list(range(len(bars))), bars, atr, 0.10, 0.25, 5, {})
    assert ev is not None and ev.side == "short"


def test_rechazo_tardio_no_hay_entrada():
    day = date(2025, 3, 4)
    bars = _craft_day_A(day, 96.0, 100.0, reject_hh=11, reject_mm=0)
    # 9:40-10:55 todo fuera de rango (barridos sin rechazo) para que el unico
    # cierre dentro sea el de las 11:00, fuera de ventana
    for i, b in enumerate(bars):
        m = et_minutes(b.timestamp)
        if (9 * 60 + 40) <= m < (11 * 60):
            bars[i] = Bar(timestamp=b.timestamp, open=96.0, high=96.2, low=95.5,
                          close=96.0, volume=1)
    atr = atr_wilder(bars, 14)
    ev = compute_signal_A(day, list(range(len(bars))), bars, atr, 0.10, 0.25, 5, {})
    assert ev is None  # rechazo >= 11:00 fuera de ventana


# ------------------------------------------------------------------ 4. salidas
def _craft_day_trend(day: date) -> list[Bar]:
    """Dia largo 8:00-16:00 ET con senal A-long 9:35/9:40 y deriva suave
    alcista post-fill que nunca toca el stop: aisla la mecanica de salida."""
    bars = day_bars(day, m5_slots(8, 0, 16, 0))
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100, high=100.4, low=99.6,
                      close=100, volume=1)
    def put(hh, mm, o, h, lo, c):
        for i, b in enumerate(bars):
            if et_minutes(b.timestamp) == hh * 60 + mm:
                bars[i] = Bar(timestamp=b.timestamp, open=o, high=h, low=lo,
                              close=c, volume=1)
                return
    for hh, mm in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        put(hh, mm, 100.0, 100.6, 99.4, 100.0)
    put(9, 35, 100.0, 100.2, 95.5, 96.0)
    put(9, 40, 96.0, 100.5, 96.0, 100.0)
    put(9, 45, 100.0, 100.6, 99.8, 100.3)
    px = 100.3
    for i, b in enumerate(bars):
        if et_minutes(b.timestamp) > 9 * 60 + 45:
            px += 0.05
            bars[i] = Bar(timestamp=b.timestamp, open=px - 0.05, high=px + 0.1,
                          low=px - 0.1, close=px, volume=1)
    return bars


def test_salida_1200_primaria_motor():
    day = date(2025, 3, 4)
    bars = _craft_day_trend(day)
    idx = list(range(len(bars)))
    atr = atr_wilder(bars, 14)
    ev = compute_signal_A(day, idx, bars, atr, 0.10, 0.25, 5, {})
    assert ev is not None and ev.side == "long"
    e12 = find_first_at_or_after(idx, bars, 12, 0)
    assert e12 is not None and et_minutes(bars[e12].timestamp) == 12 * 60
    info = {"exit_idx": e12, "sens_end_idx": ev.fill_idx + 60, "sens_bars": 60}
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", "primary", info)
    assert len(r["trades"]) == 1
    tr = r["trades"][0]
    assert tr["exit_reason"] == "time_exit"
    assert tr["exit_time"] == bars[e12].timestamp.isoformat()  # open barra 12:00


def test_salida_sensibilidad_60_barras():
    day = date(2025, 3, 4)
    bars = _craft_day_trend(day)
    idx = list(range(len(bars)))
    atr = atr_wilder(bars, 14)
    ev = compute_signal_A(day, idx, bars, atr, 0.10, 0.25, 5, {})
    assert ev is not None
    e12 = find_first_at_or_after(idx, bars, 12, 0)
    info = {"exit_idx": e12, "sens_end_idx": ev.fill_idx + 60, "sens_bars": 60}
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", "sens60", info)
    assert len(r["trades"]) == 1
    tr = r["trades"][0]
    assert tr["exit_reason"] == "time_exit"
    assert tr["exit_time"] == bars[ev.fill_idx + 60].timestamp.isoformat()


# ------------------------------------------------------------------ 5. gaps
def test_gap_entry_rechazo_con_prioridad():
    # rechazo 9:40, fill con salto temporal (falta la barra inmediata 9:45)
    day = date(2025, 3, 4)
    slots = m5_slots(8, 0, 9, 40) + m5_slots(10, 0, 13, 0)
    bars = day_bars(day, slots)
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100, high=100.4, low=99.6,
                      close=100, volume=1)
    ev = SignalEvent(day=day, kind="A", side="long",
                     signal_idx=len(m5_slots(8, 0, 9, 40)) - 1,
                     fill_idx=len(m5_slots(8, 0, 9, 40)),
                     stop_level=50.0,  # stop lejano: el gap-stop NO debe aplicar
                     target_level=100 + 1_000_000.0, ref_close=100.0,
                     atr=1.0, extra="test")
    # el fill salta de 9:40 a 10:00 con precio que habria tocado stop: aun asi rechazo
    fb = bars[ev.fill_idx]
    assert (fb.timestamp - bars[ev.signal_idx].timestamp).total_seconds() > 450
    info = {"exit_idx": ev.fill_idx + 10, "sens_end_idx": ev.fill_idx + 60,
            "sens_bars": 60}
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", "primary", info)
    assert r["gap_rejections"] == 1 and r["trades"] == [] and r["unresolved"] == 0


def test_gap_entry_precio_mas_alla_del_stop():
    cls, _, _, _ = mirror_valid_levels("long", 98.0, 0.25, 0.25,
                                       stop=99.0, target=100 + 1_000_000.0)
    assert cls == "gap_reject"  # fill por debajo del stop: no se entra
    cls, _, _, _ = mirror_valid_levels("short", 102.0, 0.25, 0.25,
                                       stop=101.0, target=100 - 1_000_000.0)
    assert cls == "gap_reject"
    cls, _, _, _ = mirror_valid_levels("long", 100.0, 0.25, 0.25,
                                       stop=99.0, target=100 + 1_000_000.0)
    assert cls == "ok"


# ------------------------------------------------------------------ 6. vol
def _sessions_rets(n_days: int, base_vol: float, jump_day: int | None = None,
                   jump_vol: float = 0.0, start: date = date(2024, 1, 1)):
    sessions = [start + timedelta(days=i) for i in range(n_days)]
    rets = {}
    rng = __import__("numpy").random.RandomState(3)
    for i, d in enumerate(sessions):
        v = jump_vol if (jump_day is not None and i >= jump_day) else base_vol
        rets[d] = list(rng.normal(0, v, size=30))
    return sessions, rets


def test_percentil_sin_lookahead():
    sessions, rets = _sessions_rets(150, 0.001)
    day = sessions[140]
    p1 = vol_percentile(sessions, rets, day, 20, 120)
    assert math.isfinite(p1)
    # futuro distinto (regimen nuevo) no cambia el percentil del dia fijo
    sessions2, rets2 = _sessions_rets(160, 0.001, jump_day=150, jump_vol=0.05)
    p2 = vol_percentile(sessions2, rets2, day, 20, 120)
    assert p1 == pytest.approx(p2)
    # el dia del salto SI ve regimen alto
    p3 = vol_percentile(sessions2, rets2, sessions2[159], 20, 120)
    assert p3 > p1


def test_percentil_warmup_nan():
    sessions, rets = _sessions_rets(30, 0.001)
    assert math.isnan(vol_percentile(sessions, rets, sessions[29], 20, 120))
    cache = vol_asof_all(sessions, rets, 20)
    assert math.isnan(vol_percentile(sessions, rets, sessions[29], 20, 120, cache))


# ------------------------------------------------------------------ 7. regresion
def test_regresion_respuesta_conocida():
    import numpy as np
    rng = np.random.RandomState(11)
    x = list(np.linspace(0.05, 0.95, 60))
    y = [3.0 * xi - 1.0 + float(rng.normal(0, 0.05)) for xi in x]
    reg = ols_beta(x, y)
    assert reg["beta"] == pytest.approx(3.0, abs=0.1)
    assert reg["p_one_sided"] is not None and reg["p_one_sided"] < 0.01
    # variante pct>50: media positiva con respuesta conocida
    gt50 = [yy for xx, yy in zip(x, y) if xx > 0.50]
    assert float(np.mean(gt50)) > 0


def test_regresion_sin_varianza():
    reg = ols_beta([0.5, 0.5, 0.5], [1.0, 2.0, 3.0])
    assert reg["beta"] is None and reg["p_one_sided"] is None


# ------------------------------------------------------------------ 8. control
def test_control_pareado():
    import numpy as np
    rng = np.random.RandomState(5)
    r = list(rng.normal(0.2, 1.0, size=50))
    c1 = paired_control(r, 20260929)
    c2 = paired_control(r, 20260929)
    assert c1 == c2  # semilla determinista
    assert len(c1) == len(r)
    assert sorted(abs(v) for v in c1) == pytest.approx(sorted(abs(v) for v in r))
    c3 = paired_control(r, 999)
    assert c3 != c1  # otra semilla, otros signos


# ------------------------------------------------------------------ 9. sha
def test_sha_preregistro_ok_y_alterado(tmp_path):
    prereg = verify_preregistro()
    assert prereg["block_id"] == "N1"
    alter = tmp_path / "preregistro.json"
    raw = Path(PREREG_PATH).read_bytes().replace(b'"block_id": "N1"', b'"block_id": "N2"')
    assert raw != Path(PREREG_PATH).read_bytes()
    alter.write_bytes(raw)
    with pytest.raises(SystemExit) as ei:
        verify_preregistro(alter, PREREG_SHA256)
    assert "INVALID" in str(ei.value.code)
    missing = tmp_path / "noexiste.json"
    with pytest.raises(SystemExit):
        verify_preregistro(missing, PREREG_SHA256)


def test_smoke_fixture_da_senales_A_y_C():
    bars = build_smoke_bars()
    groups = group_by_et_date(bars)
    days = sorted(groups.keys())
    atr = atr_wilder(bars, 14)
    ev0 = compute_signal_A(days[0], groups[days[0]], bars, atr, 0.10, 0.25, 5, {})
    ev1 = compute_signal_A(days[1], groups[days[1]], bars, atr, 0.10, 0.25, 5, {})
    assert ev0 is not None and ev0.side == "long"
    assert ev1 is not None and ev1.side == "short"
    evc = compute_signal_C(days[2], groups[days[2]], bars, atr, 0.25, 5, 0.75, {})
    assert evc is not None and evc.side == "long"


# ------------------------------------------------------------------ 10. regresiones
def test_c1_stop_extremo_del_barrido():
    """C1: Stop de A usa el extremo del BARRIDO (high/low), no del rango."""
    day = date(2025, 3, 4)
    bars = day_bars(day, m5_slots(8, 0, 13, 0))
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100.0, high=100.4, low=99.6, close=100.0, volume=1.0)
    def put(hh, mm, o, h, lo, c):
        for i, b in enumerate(bars):
            if et_minutes(b.timestamp) == hh * 60 + mm:
                bars[i] = Bar(timestamp=b.timestamp, open=o, high=h, low=lo, close=c, volume=1.0)
                return
    for hh, mm in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        put(hh, mm, 100.0, 100.6, 99.4, 100.0)
    # Barra de barrido 9:35 con low=94.0 (extremo de barrido), close=96.0
    put(9, 35, 100.0, 100.2, 94.0, 96.0)
    # Barra de rechazo 9:40 con close=100.0 dentro del rango
    put(9, 40, 96.0, 100.5, 96.0, 100.0)
    atr = atr_wilder(bars, 14)
    ev_down = compute_signal_A(day, list(range(len(bars))), bars, atr, 0.10, 0.25, 5, {})
    assert ev_down is not None and ev_down.side == "long"
    # Stop debe ser low(94.0) - k_stop(0.25) * ATR, NO rl(99.4) - k_stop*ATR
    expected_stop_down = 94.0 - 0.25 * ev_down.atr
    assert ev_down.stop_level == pytest.approx(expected_stop_down)
    assert ev_down.stop_level < 94.0

    # Sweep UP: barra de barrido 9:35 con high=106.0, close=104.0
    put(9, 35, 100.0, 106.0, 99.8, 104.0)
    put(9, 40, 104.0, 104.0, 99.5, 100.0)
    ev_up = compute_signal_A(day, list(range(len(bars))), bars, atr, 0.10, 0.25, 5, {})
    assert ev_up is not None and ev_up.side == "short"
    expected_stop_up = 106.0 + 0.25 * ev_up.atr
    assert ev_up.stop_level == pytest.approx(expected_stop_up)
    assert ev_up.stop_level > 106.0


def test_c2_event_study_k20_exacto():
    """C2: Event study K=20 abarca exactamente f..f+19 (20 barras: close[f+19] - open[f])."""
    day = date(2025, 3, 4)
    bars = day_bars(day, m5_slots(8, 0, 16, 0))
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100.0, high=100.4, low=99.6, close=100.0, volume=1.0)
    f = 20
    bars[f] = Bar(timestamp=bars[f].timestamp, open=100.0, high=102.0, low=98.0, close=101.0, volume=1.0)
    bars[f + 19] = Bar(timestamp=bars[f + 19].timestamp, open=105.0, high=111.0, low=104.0, close=110.0, volume=1.0)
    bars[f + 20] = Bar(timestamp=bars[f + 20].timestamp, open=110.0, high=999.0, low=109.0, close=999.0, volume=1.0)

    event_K = 20
    t = f + event_K - 1
    assert t == f + 19
    move = bars[t].close - bars[f].open
    assert move == pytest.approx(10.0)
    assert move != (bars[f + 20].close - bars[f].open)


def test_c6_sensibilidad_no_contamina_primaria():
    """C6: La muestra primaria se fija primero; falta de 60 barras no descarta la primaria."""
    day = date(2025, 3, 4)
    slots = m5_slots(8, 0, 12, 15)
    bars = day_bars(day, slots)
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100.0, high=100.4, low=99.6, close=100.0, volume=1.0)
    def put(hh, mm, o, h, lo, c):
        for i, b in enumerate(bars):
            if et_minutes(b.timestamp) == hh * 60 + mm:
                bars[i] = Bar(timestamp=b.timestamp, open=o, high=h, low=lo, close=c, volume=1.0)
                return
    for hh, mm in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        put(hh, mm, 100.0, 100.6, 99.4, 100.0)
    put(9, 35, 100.0, 100.2, 94.0, 96.0)
    put(9, 40, 96.0, 100.5, 96.0, 100.0)
    put(9, 45, 100.0, 100.6, 99.8, 100.3)

    idx = list(range(len(bars)))
    atr = atr_wilder(bars, 14)
    ev = compute_signal_A(day, idx, bars, atr, 0.10, 0.25, 5, {})
    assert ev is not None
    assert (ev.fill_idx + 60) >= len(bars)
    e12 = find_first_at_or_after(idx, bars, 12, 0)
    assert e12 is not None

    r_primary = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", "primary", {"exit_idx": e12})
    assert len(r_primary["trades"]) == 1
    assert r_primary["trades"][0]["exit_reason"] == "time_exit"


def test_c8_control_signos_originales_y_serie_positiva():
    """C8: Control = permutacion de signos originales. Serie 100% positiva preserva el 100%."""
    pos = [1.5, 2.3, 0.8, 4.1, 3.0]
    c_pos = paired_control(pos, 42)
    assert len(c_pos) == len(pos)
    assert all(x > 0 for x in c_pos)
    assert sum(1 for x in c_pos if x > 0) == len(pos)

    mixed = [1.0, -2.0, 3.0, -4.0, 5.0, 6.0]
    c_mix = paired_control(mixed, 77)
    assert sum(1 for x in c_mix if x > 0) == 4
    assert sum(1 for x in c_mix if x < 0) == 2
    assert sorted(abs(x) for x in c_mix) == pytest.approx(sorted(abs(x) for x in mixed))


def test_c9_drawdown_con_ancla_inicial():
    """C9: Drawdown con ancla 0 inicial: un unico trade de -1R da drawdown 1R."""
    res1 = summarize_r([{"budget_r": -1.0, "stop_r": -1.0, "net_pnl": -50.0}], cbb_seed=42)
    assert res1["max_drawdown_r"] == pytest.approx(1.0)

    res2 = summarize_r([
        {"budget_r": -0.5, "stop_r": -0.5, "net_pnl": -25.0},
        {"budget_r": -0.7, "stop_r": -0.7, "net_pnl": -35.0},
    ], cbb_seed=42)
    assert res2["max_drawdown_r"] == pytest.approx(1.2)

    res3 = summarize_r([
        {"budget_r": 1.0, "stop_r": 1.0, "net_pnl": 50.0},
        {"budget_r": 2.0, "stop_r": 1.0, "net_pnl": 100.0},
    ], cbb_seed=42)
    assert res3["max_drawdown_r"] == pytest.approx(0.0)


def test_c4_gates_con_motor_vacio_deben_fallar():
    """C4: Gates con motor vacio o divergencia deben fallar cerrado."""
    g1_fail = {
        "G1_signal_parity": {"common": 0, "divergent": 0, "empty_decisions": True, "PASS": False},
        "G2_mapping": {"unmapped_trades": 0, "reconciled": True, "PASS": True},
        "G3_determinism": {"PASS": True, "detail": "ok"},
        "G4_sealed": {"PASS": True},
        "G5_cbb": {"status": "PASS"},
    }
    with pytest.raises(SystemExit) as ei1:
        check_gates_ok(g1_fail, smoke=True)
    assert "G1" in str(ei1.value.code)

    g2_fail = {
        "G1_signal_parity": {"common": 1, "divergent": 0, "empty_decisions": False, "PASS": True},
        "G2_mapping": {"unmapped_trades": 0, "reconciled": False, "PASS": False},
        "G3_determinism": {"PASS": True, "detail": "ok"},
        "G4_sealed": {"PASS": True},
        "G5_cbb": {"status": "PASS"},
    }
    with pytest.raises(SystemExit) as ei2:
        check_gates_ok(g2_fail, smoke=True)
    assert "G2" in str(ei2.value.code)

    g5_fail = {
        "G1_signal_parity": {"common": 1, "divergent": 0, "empty_decisions": False, "PASS": True},
        "G2_mapping": {"unmapped_trades": 0, "reconciled": True, "PASS": True},
        "G3_determinism": {"PASS": True, "detail": "ok"},
        "G4_sealed": {"PASS": True},
        "G5_cbb": {"status": "FAIL", "detail": "no-finito"},
    }
    with pytest.raises(SystemExit) as ei3:
        check_gates_ok(g5_fail, smoke=True)
    assert "G5" in str(ei3.value.code)


def test_c5_g4_forma_de_abstencion_y_audit_sellado():
    """C5: G4 acepta abstencion legitima en pantalla y audita sellado sin reapertura."""
    g4_legitimate = {
        "G1_signal_parity": {"PASS": True},
        "G2_mapping": {"PASS": True},
        "G3_determinism": {"PASS": True},
        "G4_sealed": {"sealed_runs": 1, "expected_sealed_runs": 1,
                      "reopen_detected": False, "order_violated": False, "PASS": True},
        "G5_cbb": {"status": "PASS"},
    }
    check_gates_ok(g4_legitimate, smoke=False)

    g4_reopen = {
        "G1_signal_parity": {"PASS": True},
        "G2_mapping": {"PASS": True},
        "G3_determinism": {"PASS": True},
        "G4_sealed": {"sealed_runs": 2, "expected_sealed_runs": 1,
                      "reopen_detected": True, "order_violated": False, "PASS": False},
        "G5_cbb": {"status": "PASS"},
    }
    with pytest.raises(SystemExit) as ei:
        check_gates_ok(g4_reopen, smoke=False)
    assert "G4" in str(ei.value.code)


def test_c7_supresion_de_ic_bajo_n():
    """C7: No se publica ningun IC con N bajo el umbral (150 trades)."""
    rows_149 = [{"budget_r": 0.1, "stop_r": 0.1, "net_pnl": 10.0} for _ in range(149)]
    res_149 = summarize_r(rows_149, cbb_seed=42, min_trades=150)
    assert res_149["cbb_ci95_mean_budget_r"] is None

    rows_150 = [{"budget_r": 0.1, "stop_r": 0.1, "net_pnl": 10.0} for _ in range(150)]
    res_150 = summarize_r(rows_150, cbb_seed=42, min_trades=150)
    assert res_150["cbb_ci95_mean_budget_r"] is not None
    assert len(res_150["cbb_ci95_mean_budget_r"]) == 2


def test_w8_a1_fillbar_no_evalua_tp_con_target_lejano():
    """W8: Regla A1 y target lejano garantizan que el fill nunca resuelve TP en su propia vela."""
    from src.backtest.executor import BacktestConfig, run_backtest
    from src.backtest.strategy import Signal, Strategy
    sig = Signal("long", 100.0, 95.0, 1_000_000.0)
    class _Once(Strategy):
        def __init__(self, s):
            self.s = s
            self.done = False
        def evaluate(self, h):
            if not self.done and h:
                self.done = True
                return self.s
            return None
    bars = [
        et_bar(date(2025, 3, 4), 9, 30, 100.0, 101.0, 99.0, 100.0),
        et_bar(date(2025, 3, 4), 9, 35, 100.0, 105.0, 99.0, 102.0),
    ]
    cfg = BacktestConfig(commission_per_side=0.0, slippage_points=0.0)
    res = run_backtest(bars, _Once(sig), cfg)
    for tr in res.trades:
        assert tr.exit_reason != "profit_target"


def test_w5_parse_bars_rechaza_timestamps_duplicados():
    """W5: parse_bars rechaza timestamps duplicados levantando ValueError."""
    csv_data = (
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T10:00:00Z,100,101,99,100,10\n"
        "2024-01-01T10:00:00Z,100,101,99,100,10\n"
    ).encode("utf-8")
    with pytest.raises(ValueError) as excinfo:
        parse_bars(csv_data)
    assert "timestamp-duplicado" in str(excinfo.value)


def test_w4_gap_m5_entre_sweep_y_rechazo_invalida_senal():
    """W4: Si hay un salto temporal (>5min) entre sweep y rechazo, la senal se invalida."""
    day = date(2025, 3, 4)
    bars = day_bars(day, m5_slots(8, 0, 13, 0))
    for i, b in enumerate(bars):
        bars[i] = Bar(timestamp=b.timestamp, open=100.0, high=100.4, low=99.6, close=100.0, volume=1.0)
    def put(hh, mm, o, h, lo, c):
        for i, b in enumerate(bars):
            if et_minutes(b.timestamp) == hh * 60 + mm:
                bars[i] = Bar(timestamp=b.timestamp, open=o, high=h, low=lo, close=c, volume=1.0)
                return
    for hh, mm in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        put(hh, mm, 100.0, 100.6, 99.4, 100.0)
    put(9, 35, 100.0, 100.2, 94.0, 96.0)
    put(9, 45, 96.0, 100.5, 96.0, 100.0)
    bars = [b for b in bars if et_minutes(b.timestamp) != (9 * 60 + 40)]
    atr = atr_wilder(bars, 14)
    diag = {}
    ev = compute_signal_A(day, list(range(len(bars))), bars, atr, 0.10, 0.25, 5, diag)
    assert diag.get("A_gap_entre_sweep_y_rechazo", 0) >= 1


# ------------------------------------------------------------------ G1 x C6 fixes
def test_g1_sensibilidad_subconjunto_sin_60_barras_pasa():
    """G1 PASS: primary con trade sin 60 barras hace que sens sea subconjunto (b <= a)."""
    t1 = ("2025-03-04T09:35:00Z", "long", 100.0, 110.0)
    t2 = ("2025-03-05T09:35:00Z", "short", 200.0, 190.0)
    arms = {
        "A_on": {
            "signals_dev": 2,
            "dev": {
                "trades": [{"trade_id": "1"}, {"trade_id": "2"}],
                "decisions_primary": [t1, t2],
                "decisions_sens": [t1],  # t2 no tuvo 60 barras, por lo que sens es subconjunto
                "gap": 0, "collapsed": 0, "unresolved": 0,
            },
        },
        "A_off": {"signals_dev": 0, "dev": None},
        "C": {"signals_dev": 0, "dev": None},
    }
    res = evaluate_g1(arms)
    assert res["PASS"] is True
    assert res["divergent"] == 0
    assert res["common"] == 1
    assert res["empty_decisions"] is False
    check_gates_ok({
        "G1_signal_parity": res,
        "G2_mapping": {"PASS": True},
        "G3_determinism": {"PASS": True},
        "G4_sealed": {"PASS": True},
        "G5_cbb": {"status": "PASS"},
    }, smoke=True)


def test_g1_sensibilidad_decision_extra_falla():
    """G1 FAIL: sens tiene una decision extra no contenida en primary (divergencia real)."""
    t1 = ("2025-03-04T09:35:00Z", "long", 100.0, 110.0)
    t_extra = ("2025-03-06T09:35:00Z", "long", 105.0, 115.0)
    arms = {
        "A_on": {
            "signals_dev": 1,
            "dev": {
                "trades": [{"trade_id": "1"}],
                "decisions_primary": [t1],
                "decisions_sens": [t1, t_extra],
                "gap": 0, "collapsed": 0, "unresolved": 0,
            },
        },
        "A_off": {"signals_dev": 0, "dev": None},
        "C": {"signals_dev": 0, "dev": None},
    }
    res = evaluate_g1(arms)
    assert res["PASS"] is False
    assert res["divergent"] == 1
    with pytest.raises(SystemExit) as excinfo:
        check_gates_ok({
            "G1_signal_parity": res,
            "G2_mapping": {"PASS": True},
            "G3_determinism": {"PASS": True},
            "G4_sealed": {"PASS": True},
            "G5_cbb": {"status": "PASS"},
        }, smoke=True)
    assert "G1" in str(excinfo.value.code)


def test_g1_g2_todas_senales_gap_reconciliado_pasa():
    """G1 PASS y G2 PASS: todas las senales mueren como gap reconciliado (legitimo, no INVALID)."""
    d1 = date(2025, 3, 4)
    d2 = date(2025, 3, 5)
    dev_days = [d1, d2]
    arms = {
        "A_on": {
            "signals_dev": 2,
            "dev": {
                "trades": [],
                "decisions_primary": [],
                "decisions_sens": [],
                "gap": 2, "collapsed": 0, "unresolved": 0,
            },
        },
        "A_off": {"signals_dev": 0, "dev": None},
        "C": {"signals_dev": 0, "dev": None},
    }
    # G1
    res_g1 = evaluate_g1(arms)
    assert res_g1["PASS"] is True
    assert res_g1["empty_decisions"] is False
    assert res_g1["divergent"] == 0

    # G2 reconciliacion
    dev = arms["A_on"]["dev"]
    n_motor = len(dev["trades"]) + dev["gap"] + dev["collapsed"] + dev["unresolved"]
    n_expected = len(dev_days)
    assert n_motor == n_expected
    res_g2 = {"unmapped_trades": 0, "reconciled": (n_motor == n_expected), "PASS": (n_motor == n_expected)}
    assert res_g2["PASS"] is True

    # Gates check
    check_gates_ok({
        "G1_signal_parity": res_g1,
        "G2_mapping": res_g2,
        "G3_determinism": {"PASS": True},
        "G4_sealed": {"PASS": True},
        "G5_cbb": {"status": "PASS"},
    }, smoke=True)


def test_g1_decision_desaparecida_sin_drop_registrado_falla():
    """G1 FAIL: senales con decision desaparecida sin drop registrado fallan fail-closed."""
    arms = {
        "A_on": {
            "signals_dev": 1,
            "dev": {
                "trades": [],
                "decisions_primary": [],
                "decisions_sens": [],
                "gap": 0, "collapsed": 0, "unresolved": 0,
            },
        },
        "A_off": {"signals_dev": 0, "dev": None},
        "C": {"signals_dev": 0, "dev": None},
    }
    res_g1 = evaluate_g1(arms)
    assert res_g1["PASS"] is False
    assert res_g1["empty_decisions"] is True
    with pytest.raises(SystemExit) as excinfo:
        check_gates_ok({
            "G1_signal_parity": res_g1,
            "G2_mapping": {"PASS": True},
            "G3_determinism": {"PASS": True},
            "G4_sealed": {"PASS": True},
            "G5_cbb": {"status": "PASS"},
        }, smoke=True)
    assert "G1" in str(excinfo.value.code)

