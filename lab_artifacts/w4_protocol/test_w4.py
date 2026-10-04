"""Tests deterministas del bloque W4 (sinteticos, sin red, sin datos reales).

Cubre logica D1/stop/target/salida y los minimos del encargo:
tick-collapse por mercado, fill mas alla del target, miercoles corto
truncado, MGC rechazado, fechas exactas + dedup ISO, rango cero y flip
apareado con mismo N.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.w4_protocol.run_w4 import (  # noqa: E402
    ET,
    MARKET_SPECS,
    PREREG_PATH,
    PREREG_SHA256,
    SignalEvent,
    atr_wilder_14,
    build_smoke_bars,
    check_flip_paired,
    check_gates_ok,
    compute_signal_W4,
    decide_D1,
    decide_sealed,
    drift_stats,
    et_minutes,
    evaluate_g1,
    evaluate_g2,
    extract_pins,
    find_first_at_or_after,
    flip_event,
    group_by_et_date,
    maybe_write_sealed_lock,
    mirror_valid_levels,
    paired_control,
    parse_bars,
    parse_roll_dates,
    precompute_wilder_atr,
    process_market,
    round_tick,
    run_signal_day,
    session_summary,
    signal_tuple,
    summarize_w4,
    validate_markets,
    verify_preregistro,
    write_sealed_lock,
)
from src.backtest.history import Bar  # noqa: E402


def et_bar(day: date, hh: int, mm: int, o: float = 100.0, h: float = 101.0,
           lo: float = 99.0, c: float = 100.0) -> Bar:
    ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).astimezone(timezone.utc)
    return Bar(timestamp=ts, open=o, high=h, low=lo, close=c, volume=50.0)


def rth_slots() -> list[tuple[int, int]]:
    out, h, m = [], 9, 30
    while (h, m) <= (15, 55):
        out.append((h, m))
        m += 5
        if m >= 60:
            m, h = 0, h + 1
    return out


def make_session(day: date, hi: float, lo: float, close_last: float) -> list[Bar]:
    """Sesion RTH completa de 78 slots con high/low/close controlados."""
    mid = (hi + lo) / 2.0
    bars = [et_bar(day, hh, mm, mid, mid, mid, mid) for (hh, mm) in rth_slots()]
    assert len(bars) == 78
    bars[6] = et_bar(day, 10, 0, mid, hi, lo, mid)  # barra de extremos
    bars[-1] = et_bar(day, 15, 55, mid, max(mid, close_last),
                      min(mid, close_last), close_last)
    return bars


def make_wednesday(day: date, signal_close: float, fill_open: float,
                   last_hhmm: tuple[int, int] = (16, 0), drift: float = 0.0) -> list[Bar]:
    """Miercoles 09:25..last con reloj 09:25/09:30 y deriva posterior."""
    bars = [et_bar(day, 9, 25, signal_close, signal_close, signal_close, signal_close)]
    bars.append(et_bar(day, 9, 30, fill_open, fill_open + 0.5, fill_open - 0.5,
                       fill_open + 0.2))
    px = fill_open + 0.2
    h, m = 9, 35
    while (h, m) <= last_hhmm:
        px += drift
        bars.append(et_bar(day, h, m, px - drift, px + 0.2, px - 0.2, px))
        m += 5
        if m >= 60:
            m, h = 0, h + 1
    return bars


def make_week(mon: date, mon_hi: float, mon_lo: float, mon_c: float,
              tue_hi: float, tue_lo: float, tue_c: float,
              sig_close: float, fill_open: float,
              last_hhmm: tuple[int, int] = (16, 0), drift: float = 0.0) -> list[Bar]:
    tue = mon + timedelta(days=1)
    wed = mon + timedelta(days=2)
    return (make_session(mon, mon_hi, mon_lo, mon_c)
            + make_session(tue, tue_hi, tue_lo, tue_c)
            + make_wednesday(wed, sig_close, fill_open, last_hhmm, drift))


def pins():
    return extract_pins(verify_preregistro())


LONG_WEEK = dict(mon_hi=110.0, mon_lo=100.0, mon_c=105.0,
                 tue_hi=106.0, tue_lo=99.0, tue_c=104.0,
                 sig_close=103.0, fill_open=103.0, drift=0.15)


def long_week_bars(mon: date = date(2025, 3, 3), **kw) -> list[Bar]:
    cfg = dict(LONG_WEEK)
    cfg.update(kw)
    return make_week(mon, **cfg)


class MockFold:
    def __init__(self, fold_id: int, start: str, end: str):
        self.fold_id = fold_id
        self.test_start = start
        self.test_end = end


class MockPlan:
    def __init__(self, folds: list[MockFold]):
        self.folds = folds
        self.n_folds = len(folds)


def make_mock_plan(start_year: int = 2020, end_year: int = 2026) -> MockPlan:
    folds = [
        MockFold(0, f"{start_year}-01-01", f"{end_year}-01-01"),
        MockFold(1, f"{end_year}-01-01", f"{end_year}-02-01"),
        MockFold(2, f"{end_year}-02-01", f"{end_year}-03-01"),
        MockFold(3, f"{end_year}-03-01", f"{end_year}-04-01"),
        MockFold(4, f"{end_year}-04-01", f"{end_year}-05-01"),
        MockFold(5, f"{end_year}-05-01", f"{end_year}-06-01"),
        MockFold(6, f"{end_year}-06-01", f"{end_year}-07-01"),
        MockFold(7, f"{end_year}-07-01", f"{end_year}-08-01"),
    ]
    return MockPlan(folds)


def make_multi_week_bars(n_weeks: int, start_mon: date = date(2021, 1, 4),
                         drift: float = 0.15) -> list[Bar]:
    bars: list[Bar] = []
    for k in range(n_weeks):
        mon = start_mon + timedelta(days=7 * k)
        bars.extend(long_week_bars(mon=mon, drift=drift))
    bars.sort(key=lambda b: b.timestamp)
    return bars


# ------------------------------------------------------------------ D1
def test_d1_long_limpio():
    side, reason = decide_D1(110.0, 100.0, 106.0, 99.0, 104.0)
    assert (side, reason) == ("long", "ok")


def test_d1_short_limpio():
    side, reason = decide_D1(110.0, 100.0, 111.0, 101.0, 106.0)
    assert (side, reason) == ("short", "ok")


def test_d1_doble_sweep_no_signal():
    assert decide_D1(110.0, 100.0, 111.0, 99.0, 105.0) == (None, "doble_sweep")


def test_d1_igualdad_no_es_barrido_ni_cierre():
    assert decide_D1(110.0, 100.0, 106.0, 100.0, 104.0)[0] is None  # tue_low == mon_low
    assert decide_D1(110.0, 100.0, 110.0, 101.0, 106.0)[0] is None  # tue_high == mon_high
    assert decide_D1(110.0, 100.0, 106.0, 99.0, 100.0)[0] is None  # tue_close == mon_low
    assert decide_D1(110.0, 100.0, 106.0, 99.0, 110.0)[0] is None  # tue_close == mon_high


def test_d1_sin_cierre_interior_y_sin_barrido():
    assert decide_D1(110.0, 100.0, 106.0, 99.0, 112.0) == (None, "sin_cierre_interior")
    assert decide_D1(110.0, 100.0, 106.0, 101.0, 105.0) == (None, "sin_barrido")


def test_d1_off_sin_cierre_interior_si_emite():
    side, _ = decide_D1(110.0, 100.0, 106.0, 99.0, 112.0, require_inside_close=False)
    assert side == "long"


def test_senal_semanal_long_y_stop_target():
    bars = long_week_bars()
    groups = group_by_et_date(bars)
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, {}, set())
    assert ev is not None and ev.side == "long"
    assert ev.stop_level == pytest.approx(99.0)
    assert ev.entry_est == pytest.approx(103.0)
    assert ev.target_level == pytest.approx(103.0 + 1.5 * 4.0)
    assert ev.risk_nominal_pts == pytest.approx(4.0)
    assert ev.week_key == (2025, 10)


def test_sesion_incompleta_no_signal():
    bars = long_week_bars()
    # martes con 77 slots (falta 15:55) -> incompleta
    bars = [b for b in bars
            if not (b.timestamp.astimezone(ET).date() == date(2025, 3, 4)
                    and et_minutes(b.timestamp) == 955)]
    groups = group_by_et_date(bars)
    diag: dict[str, int] = {}
    assert compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, diag, set()) is None
    assert diag.get("martes_incompleto") == 1


# ------------------------------------------------- minimos del encargo
def test_tick_collapse_por_mercado():
    # Barrido fino: mon_lo=103, tue_lo=102.9 (riesgo 0.1 < 1 tick) -> colapso.
    # MNQ tick 0.25 y MYM tick 1.0 -> NO_SIGNAL en ambos.
    bars = long_week_bars(mon_lo=103.0, mon_hi=110.0, mon_c=105.0,
                          tue_lo=102.9, tue_hi=106.0, tue_c=104.0,
                          sig_close=103.0, fill_open=103.0)
    groups = group_by_et_date(bars)
    diag: dict[str, int] = {}
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, diag, set())
    assert ev is None and diag.get("colapso_tick") == 1
    # MYM tick 1.0: riesgo 0.5 < 1 tick -> colapso -> NO_SIGNAL
    diag2: dict[str, int] = {}
    ev2 = compute_signal_W4(date(2025, 3, 5), groups, bars, 1.0, diag2, set())
    assert ev2 is None and diag2.get("colapso_tick") == 1


def test_fill_mas_alla_del_target():
    cls, _, _, _ = mirror_valid_levels("long", 120.0, 0.25, 0.25, stop=99.0, target=109.0)
    assert cls == "gap_reject_beyond_target"
    cls, _, _, _ = mirror_valid_levels("short", 90.0, 0.25, 0.25, stop=111.0, target=101.0)
    assert cls == "gap_reject_beyond_target"
    # a nivel motor: gap favorable extremo no corre, cuenta diag, sin trades
    bars = long_week_bars(fill_open=120.0)
    groups = group_by_et_date(bars)
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, {}, set())
    assert ev is not None
    day_idx = groups[date(2025, 3, 5)]
    e16 = find_first_at_or_after(day_idx, bars, 16, 0)
    assert e16 is not None
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", e16)
    assert r["precheck"] == "gap_reject_beyond_target"
    assert r["trades"] == [] and r["gap_beyond_target"] == 1


def test_gap_reject_stop_con_prioridad():
    cls, _, _, _ = mirror_valid_levels("long", 98.0, 0.25, 0.25, stop=99.0, target=109.0)
    assert cls == "gap_reject"
    cls, _, _, _ = mirror_valid_levels("short", 112.0, 0.25, 0.25, stop=111.0, target=101.0)
    assert cls == "gap_reject"


def test_miercoles_corto_truncado():
    # dataset hasta 15:30 -> unresolved, sin fill inventado, sin reclasificar
    bars = long_week_bars(last_hhmm=(15, 30))
    out = process_market("MNQ", bars, pins(), None, set(), True)
    b = out["B_on"]
    assert b["signals_dev"] == 1  # la senal existio
    assert b["realista"]["n_trades"] == 0
    assert b["unresolved"] == 1  # trade NO corrido, contador unresolved
    assert b["unresolved_sens"] == 1  # la sensibilidad tambien lo intenta
    # Diag cuenta la via realista (la que concilia G2); la via de
    # sensibilidad queda en el campo unresolved_sens (W1: G3 la recorre igual).
    assert out["diag"].get("Bon_sin_barra_1600") == 1
    g = out["gates"]
    assert g["G1_signal_parity"]["PASS"] and g["G2_mapping"]["PASS"]
    assert g["G3_determinism"]["PASS"] and g["G4_sealed"]["PASS"]


def test_mgc_rechazado():
    with pytest.raises(ValueError, match="MGC"):
        validate_markets("MGC")
    with pytest.raises(ValueError, match="MGC"):
        validate_markets("MNQ,MGC")
    with pytest.raises(ValueError, match="desconocido"):
        validate_markets("MNQ,XXX")
    assert validate_markets("MNQ,MES,MYM") == ["MNQ", "MES", "MYM"]
    assert "MGC" not in MARKET_SPECS


def test_exact_dates_y_dedup_iso():
    # lunes festivo (sin lunes) + martes que barre el viernes -> None
    tue = date(2025, 3, 11)
    wed = date(2025, 3, 12)
    fri_prev = date(2025, 3, 7)
    bars = (make_session(fri_prev, 110.0, 100.0, 105.0)  # viernes previo
            + make_session(tue, 106.0, 99.0, 104.0)  # martes barre al viernes
            + make_wednesday(wed, 103.0, 103.0))
    groups = group_by_et_date(bars)
    diag: dict[str, int] = {}
    assert compute_signal_W4(wed, groups, bars, 0.25, diag, set()) is None
    assert diag.get("falta_lunes") == 1
    # semana interanual 2025-W01 (lun 2024-12-30): exactamente 1 senal
    mon, tue2, wed2 = date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 1)
    assert wed2.isocalendar()[1] == 1 and wed2.weekday() == 2
    bars2 = (make_session(mon, 110.0, 100.0, 105.0)
             + make_session(tue2, 106.0, 99.0, 104.0)
             + make_wednesday(wed2, 103.0, 103.0))
    g2 = group_by_et_date(bars2)
    emitted: set[tuple[int, int]] = set()
    diag2: dict[str, int] = {}
    ev = compute_signal_W4(wed2, g2, bars2, 0.25, diag2, emitted)
    assert ev is not None and ev.week_key == (2025, 1)
    assert compute_signal_W4(wed2, g2, bars2, 0.25, diag2, emitted) is None
    assert diag2.get("dedup_iso_duplicada") == 1


def test_rango_cero():
    assert decide_D1(110.0, 100.0, 105.0, 105.0, 105.0) == (None, "rango_cero_martes")
    assert decide_D1(105.0, 105.0, 106.0, 104.0, 105.0) == (None, "rango_cero_lunes")
    # a nivel senal: martes plano no genera R degenerado
    bars = long_week_bars(tue_hi=104.0, tue_lo=104.0, tue_c=104.0)
    groups = group_by_et_date(bars)
    diag: dict[str, int] = {}
    assert compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, diag, set()) is None
    assert diag.get("no_signal_rango_cero_martes") == 1


def test_flip_apareado_mismo_n():
    short_cfg = dict(mon_hi=110.0, mon_lo=100.0, mon_c=105.0,
                     tue_hi=111.0, tue_lo=101.0, tue_c=106.0,
                     sig_close=107.0, fill_open=107.0, drift=-0.15)
    bars = long_week_bars(mon=date(2025, 3, 3)) + make_week(date(2025, 3, 10), **short_cfg)
    bars.sort(key=lambda b: b.timestamp)
    out = process_market("MNQ", bars, pins(), None, set(), True)
    bon = out["B_on"]["realista"]["n_trades"]
    bflip = out["B_flip"]["realista"]["n_trades"]
    assert bon == 2 and bflip == 2
    # mismos timestamps: n_comun == n_on == n_flip implica conjuntos identicos
    # (comun = interseccion de entry_time de ambos brazos)
    fl = out["B_on"]["flip_apareado"]
    assert fl["n_on"] == fl["n_flip"] == 2
    assert fl["n_common_trades"] == 2


# ------------------------------------------------- motor / drift / salida
def test_salida_1600_y_drift_registrado():
    bars = long_week_bars(drift=0.0)
    groups = group_by_et_date(bars)
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, {}, set())
    assert ev is not None
    e16 = find_first_at_or_after(groups[date(2025, 3, 5)], bars, 16, 0)
    assert e16 is not None
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", e16)
    assert len(r["trades"]) == 1
    tr = r["trades"][0]
    assert tr["quantity"] == 1
    assert tr["exit_reason"] == "time_exit"
    assert tr["risk_nominal_pts"] == pytest.approx(4.0)
    # entry fill 103.25 (open 103 + slip 0.25) -> riesgo efectivo 4.25
    assert tr["risk_efectivo_pts"] == pytest.approx(4.25)
    assert tr["drift_rel"] == pytest.approx(0.0625)
    assert tr["r_nominal"] == 1.5
    assert tr["practice_eligible_le_200"] is True


def test_cantidad_fija_un_contrato_y_headline_stop_r():
    out = process_market("MNQ", build_smoke_bars(), pins(), None, set(), True)
    rows = out["trades_B_on_dev"]
    assert len(rows) == 1 and rows[0]["quantity"] == 1
    assert "stop_r" in rows[0] and "budget_r" in rows[0]
    assert out["B_on"]["realista"]["cbb_ci95_mean_stop_r"] is None  # n < 100: ABSTENCION de IC


def test_drift_stats_distribucion():
    d = drift_stats([0.0625, -0.02, 0.10])
    assert d["n"] == 3 and d["min"] == pytest.approx(-0.02)
    assert d["max"] == pytest.approx(0.10) and d["p5"] is not None and d["p95"] is not None
    assert drift_stats([])["n"] == 0


def test_roll_dates_excluye_semana():
    bars = long_week_bars()
    rolls = parse_roll_dates("MNQ=2025-03-05", ["MNQ"])
    assert date(2025, 3, 5) in rolls["MNQ"]
    out = process_market("MNQ", bars, pins(), None, rolls["MNQ"], True)
    assert out["B_on"]["signals_dev"] == 0
    assert out["diag"].get("semanas_roll_excluidas") == 1


def test_summarize_suprime_ic_bajo_n():
    rows = [{"stop_r": 0.1, "budget_r": 0.1, "net_pnl": 10.0, "drift_rel": 0.0,
             "risk_dollars": 50.0} for _ in range(99)]
    res = summarize_w4(rows, cbb_seed=20260930, min_trades=100)
    assert res["cbb_ci95_mean_stop_r"] is None and res["cbb_ci95_mean_budget_r"] is None


def test_control_pareado_determinista():
    r = [0.5, -1.2, 0.8, 2.0]
    assert paired_control(r, 20261001) == paired_control(r, 20261001)
    c = paired_control(r, 20261001)
    assert sorted(abs(v) for v in c) == pytest.approx(sorted(abs(v) for v in r))


def test_sha_preregistro_ok_y_alterado(tmp_path):
    prereg = verify_preregistro()
    assert prereg["block_id"] == "W4"
    alter = tmp_path / "preregistro.json"
    raw = Path(PREREG_PATH).read_bytes().replace(b'"block_id": "W4"', b'"block_id": "X4"')
    assert raw != Path(PREREG_PATH).read_bytes()
    alter.write_bytes(raw)
    with pytest.raises(SystemExit) as ei:
        verify_preregistro(alter, PREREG_SHA256)
    assert "INVALID" in str(ei.value.code)
    with pytest.raises(SystemExit):
        verify_preregistro(tmp_path / "noexiste.json", PREREG_SHA256)


def test_parse_bars_rechaza_duplicados():
    csv_data = ("timestamp,open,high,low,close,volume\n"
                "2024-01-01T10:00:00Z,100,101,99,100,10\n"
                "2024-01-01T10:00:00Z,100,101,99,100,10\n").encode("utf-8")
    with pytest.raises(ValueError, match="timestamp-duplicado"):
        parse_bars(csv_data)


def test_smoke_gates_verdes_todos_los_mercados():
    for sym in ("MNQ", "MES", "MYM"):
        out = process_market(sym, build_smoke_bars(), pins(), None, set(), True,
                             enforce_g5=(sym == "MNQ"))
        g = out["gates"]
        assert g["G1_signal_parity"]["PASS"], sym
        assert g["G2_mapping"]["PASS"], sym
        assert g["G3_determinism"]["PASS"], sym
        assert g["G4_sealed"]["PASS"], sym
        assert g["G5_cbb"]["status"] in ("PASS", "EXCLUIDO-del-fail-fast"), sym
        assert out["B_on"]["realista"]["n_trades"] == 1, sym


def test_gates_fallan_cerrado():
    bad = {"G1_signal_parity": {"PASS": True}, "G2_mapping": {"PASS": False},
           "G3_determinism": {"PASS": True}, "G4_sealed": {"PASS": True},
           "G5_cbb": {"status": "PASS"}}
    with pytest.raises(SystemExit, match="G2"):
        check_gates_ok(bad, smoke=True)


def test_sin_imports_prohibidos_de_n1():
    src = Path(__file__).with_name("run_w4.py").read_text(encoding="utf-8")
    assert "from run_n1 import" not in src and "import run_n1" not in src


# ------------------------------------------------- regresion C1–C4 / W2-W3 / W6 / S1
def wednesday_con_fill_que_toca_target(day: date = date(2025, 3, 5)) -> list[Bar]:
    """Miercoles C1: vela de fill 09:30 que TOCA el target (high 109.5) sin
    tocar el SL (low 102.0); la 09:35 tambien toca (high 109.2)."""
    bars = [et_bar(day, 9, 25, 103.0, 103.0, 103.0, 103.0)]
    bars.append(et_bar(day, 9, 30, 103.0, 109.5, 102.0, 103.2))
    bars.append(et_bar(day, 9, 35, 103.0, 109.2, 102.5, 103.1))
    px = 103.1
    h, m = 9, 40
    while (h, m) <= (16, 0):
        bars.append(et_bar(day, h, m, px, px + 0.2, px - 0.2, px))
        m += 5
        if m >= 60:
            m, h = 0, h + 1
    return bars


def c1_week() -> tuple[list[Bar], dict[date, list[int]]]:
    mon, tue = date(2025, 3, 3), date(2025, 3, 4)
    bars = (make_session(mon, 110.0, 100.0, 105.0)
            + make_session(tue, 106.0, 99.0, 104.0)
            + wednesday_con_fill_que_toca_target())
    bars.sort(key=lambda b: b.timestamp)
    return bars, group_by_et_date(bars)


def test_c1_tp_no_cierra_en_vela_de_fill():
    # Regla A1 (solo SL en vela de fill, por composicion): la vela de fill
    # toca el target pero NO cierra ahi; el TP se cobra en la 09:35.
    # Con el codigo viejo (motor con target real) este trade cerraba por TP
    # en la propia vela de fill (exit 09:30), asi que este test fallaba.
    bars, groups = c1_week()
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, {}, set())
    assert ev is not None and ev.side == "long"
    e16 = find_first_at_or_after(groups[date(2025, 3, 5)], bars, 16, 0)
    assert e16 is not None
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", e16)
    assert len(r["trades"]) == 1
    tr = r["trades"][0]
    assert tr["exit_reason"] == "take_profit"
    assert tr["exit_price"] == pytest.approx(109.0)
    fill_ts = bars[ev.fill_idx].timestamp.isoformat()
    assert tr["entry_time"] == fill_ts  # el fill sigue siendo en 09:30
    assert tr["exit_time"] != fill_ts  # pero el TP NO es en la vela de fill
    next_ts = bars[ev.fill_idx + 1].timestamp.isoformat()
    assert tr["exit_time"] == next_ts  # TP en la vela siguiente


def test_c1_sl_antes_que_target_manda_motor():
    # Composicion exacta con prioridad SL: SL en 09:35 y target tocado
    # despues -> se conserva el trade del motor (stop_loss).
    mon, tue, wed = date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5)
    bars = (make_session(mon, 110.0, 100.0, 105.0)
            + make_session(tue, 106.0, 99.0, 104.0))
    bars.append(et_bar(wed, 9, 25, 103.0, 103.0, 103.0, 103.0))
    bars.append(et_bar(wed, 9, 30, 103.0, 103.5, 102.5, 103.2))
    bars.append(et_bar(wed, 9, 35, 103.0, 104.0, 98.5, 99.0))  # toca SL, no TP
    px = 99.0
    h, m = 9, 40
    while (h, m) <= (16, 0):
        px = min(px + 0.3, 112.0)
        bars.append(et_bar(wed, h, m, px - 0.3, px + 0.3, px - 0.3, px))
        m += 5
        if m >= 60:
            m, h = 0, h + 1
    bars.sort(key=lambda b: b.timestamp)
    groups = group_by_et_date(bars)
    ev = compute_signal_W4(wed, groups, bars, 0.25, {}, set())
    assert ev is not None
    e16 = find_first_at_or_after(groups[wed], bars, 16, 0)
    assert e16 is not None
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", e16)
    assert len(r["trades"]) == 1
    tr = r["trades"][0]
    assert tr["exit_reason"] == "stop_loss"
    assert tr["exit_time"] == bars[ev.fill_idx + 1].timestamp.isoformat()


def test_c2_sellado_bloqueado_con_ic95_negativo(tmp_path):
    # Puerta del sellado: solo abre con n >= n_min Y IC95 low > 0 Y G1-G3.
    ok, _ = decide_sealed(150, 0.05, True, True, True, 100)
    assert ok is True
    for dev_n, ci_low, g1, g2, g3 in (
        (150, -0.02, True, True, True),   # IC95 negativo -> NO abre
        (150, 0.0, True, True, True),     # low == 0 -> NO abre
        (150, None, True, True, True),    # sin IC -> NO abre
        (50, 0.05, True, True, True),     # n < n_min -> NO abre
        (150, 0.05, True, False, True),   # un gate en rojo -> NO abre
    ):
        pasa, motivo = decide_sealed(dev_n, ci_low, g1, g2, g3, 100)
        assert pasa is False and motivo
    # Sin apertura no hay lock: fail-closed persistente.
    lock = tmp_path / "w4_sealed.lock"
    assert maybe_write_sealed_lock(lock, "sha", ["MNQ"], opened_any=False) is False
    assert not lock.exists()

    # Ruta real sin atajos (smoke=False): dev con 105 trades perdedores (drift=-0.2),
    # n >= n_min (105 >= 100) e IC95 CBB negativo -> sellado bloqueado, tail None,
    # y lock NO creado en disco (fail-closed, ruta real de produccion).
    p = pins()
    plan = make_mock_plan(2020, 2026)
    losing_bars = make_multi_week_bars(105, start_mon=date(2021, 1, 4), drift=-0.2)
    lock_neg = tmp_path / "w4_sealed_neg.lock"
    out_neg = process_market("MNQ", losing_bars, p, plan, set(), smoke=False,
                             lock_path=lock_neg, prereg_sha="sha_neg")
    assert out_neg["B_on"]["realista"]["n_trades"] == 105
    assert out_neg["sellado"]["dev_pasa"] is False
    assert out_neg["sellado"]["dev_ci_low"] is not None and out_neg["sellado"]["dev_ci_low"] < 0
    assert out_neg["sellado"]["abierto"] is False
    assert out_neg["B_on"]["tail"] is None
    assert not lock_neg.exists()

    # Persistencia entre ejecuciones: lock preexistente creado con creacion exclusiva
    # -> NO reabrir, sellado_ya_abierto y tail sin correr (fail-closed).
    lock_exist = tmp_path / "w4_sealed_exist.lock"
    write_sealed_lock(lock_exist, "sha_exist", ["B_on"], ["MNQ"])
    out2 = process_market("MNQ", long_week_bars(), p, None, set(), False,
                          lock_path=lock_exist, prereg_sha="sha_exist")
    assert out2["sellado"]["sellado_ya_abierto"] is True
    assert out2["sellado"]["abierto"] is False
    assert out2["B_on"]["tail"] is None


def test_c3_flip_apareada_gap_excluye_par_completo():
    # Gap adversarial (entry_est 103, open 108): el flip (short, stop 107)
    # cae en gap_reject mientras el on pasaria -> el PAR completo queda
    # fuera (N_on == N_flip == 0 en ambos escenarios), nunca 1 vs 0.
    cls_on, _, _, _ = mirror_valid_levels("long", 108.0, 0.25, 0.25,
                                          stop=99.0, target=109.0)
    assert cls_on == "ok"
    cls_flip, _, _, _ = mirror_valid_levels("short", 108.0, 0.25, 0.25,
                                            stop=107.0, target=97.0)
    assert cls_flip == "gap_reject"
    out = process_market("MNQ", long_week_bars(fill_open=108.0),
                         pins(), None, set(), True)
    assert out["B_on"]["realista"]["n_trades"] == 0
    assert out["B_flip"]["realista"]["n_trades"] == 0
    assert out["B_on"]["sensibilidad"]["n_trades"] == 0
    assert out["B_flip"]["sensibilidad"]["n_trades"] == 0
    fl = out["B_on"]["flip_apareado"]
    assert fl["n_on"] == fl["n_flip"] == fl["n_common_trades"] == 0
    assert out["diag"].get("Bpar_dev_par_excluido_realista", 0) >= 1
    assert out["diag"].get("Bpar_dev_par_excluido_sensibilidad", 0) >= 1
    assert out["gates"]["G1_signal_parity"]["PASS"]
    assert out["gates"]["G2_mapping"]["PASS"]


def test_c4_g1_pasa_con_divergencia_solo_por_slip():
    # fill 108.8: el prechequeo realista (slip 0.25) cruza el target mientras
    # el de sensibilidad (slip 0) pasa -> divergencia post-prechequeo LEGITIMA
    # que G2 concilia como drops; G1 a nivel de senal sigue en PASS.
    # Con el codigo viejo (G1 post-prechequeo) este fixture daba INVALID.
    bars = long_week_bars(fill_open=108.8)
    groups = group_by_et_date(bars)
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, {}, set())
    assert ev is not None
    e16 = find_first_at_or_after(groups[date(2025, 3, 5)], bars, 16, 0)
    assert e16 is not None
    r_real = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", e16)
    assert r_real["trades"] == [] and r_real["precheck"] == "gap_reject_beyond_target"
    r_sens = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "sensibilidad", e16)
    assert len(r_sens["trades"]) == 1  # la via barata SI corre
    out = process_market("MNQ", bars, pins(), None, set(), True)
    g1 = out["gates"]["G1_signal_parity"]
    assert g1["PASS"] and g1["divergent"] == 0


def test_w2_barras_desplazadas_30s_sin_senal():
    # Rejilla de 300 s en minutos exactos: barras desplazadas 30 s ->
    # sesion incompleta -> NO_SIGNAL (sin sobrescritura silenciosa).
    bars = long_week_bars()
    shifted = []
    for b in bars:
        if b.timestamp.astimezone(ET).date() == date(2025, 3, 3):
            b = Bar(timestamp=b.timestamp + timedelta(seconds=30),
                    open=b.open, high=b.high, low=b.low, close=b.close,
                    volume=b.volume)
        shifted.append(b)
    shifted.sort(key=lambda b: b.timestamp)
    groups = group_by_et_date(shifted)
    diag: dict[str, int] = {}
    assert compute_signal_W4(date(2025, 3, 5), groups, shifted,
                             0.25, diag, set()) is None
    assert diag.get("lunes_incompleto") == 1


def test_w3_roll_solo_lunes_no_filtra_miercoles():
    # Pin literal: solo la entrada EN fecha de roll (el miercoles) se
    # excluye; roll en lunes/martes se cuenta sin filtrar.
    rolls = parse_roll_dates("MNQ=2025-03-03", ["MNQ"])
    out = process_market("MNQ", long_week_bars(), pins(), None,
                         rolls["MNQ"], True)
    assert out["B_on"]["signals_dev"] == 1  # la entrada NO se elimina
    assert out["diag"].get("semana_con_roll_en_lunes_o_martes") == 1
    assert out["diag"].get("semanas_roll_excluidas") is None


def test_w6b_salida_exacta_tiempo_y_precio():
    # La salida temporal exige exit_time y exit_price exactos: 16:00 ET con
    # precio = open de esa barra menos slippage, redondeado a tick.
    bars = long_week_bars(drift=0.0)
    groups = group_by_et_date(bars)
    ev = compute_signal_W4(date(2025, 3, 5), groups, bars, 0.25, {}, set())
    assert ev is not None
    e16 = find_first_at_or_after(groups[date(2025, 3, 5)], bars, 16, 0)
    assert e16 is not None
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "realista", e16)
    assert len(r["trades"]) == 1
    tr = r["trades"][0]
    assert tr["exit_reason"] == "time_exit"
    assert tr["exit_time"] == bars[e16].timestamp.isoformat()
    assert tr["exit_price"] == pytest.approx(
        round_tick(bars[e16].open - 0.25, 0.25))


def test_w6c_gates_negativos_una_regla():
    # Negativos que rompen UNA regla a la vez (no un PASS=False prefabricado).
    wk = (2025, 10)
    arms = {"B_on": {"dev": {
        "decisions_primary": [(wk, "long", 99.0, 109.0)],
        "decisions_sens": [(wk, "long", 99.0, 108.0)]},  # target mutado
        "signals_dev": 1}}
    g1 = evaluate_g1(arms)
    assert g1["PASS"] is False and g1["divergent"] >= 1
    ok, detail = check_flip_paired(
        [{"entry_time": "t1"}, {"entry_time": "t2"}],
        [{"entry_time": "t1"}])
    assert ok is False and "N_on=2-vs-N_flip=1" in detail


def test_s1_pins_faltantes_o_incoherentes_invalid():
    # Pins consumidos, no solo validados: ausente o incoherente -> INVALID.
    base = verify_preregistro()
    sin_target = json.loads(json.dumps(base))
    del sin_target["pins_summary"]["target_R"]
    with pytest.raises(SystemExit, match="INVALID"):
        extract_pins(sin_target)
    qty2 = json.loads(json.dumps(base))
    qty2["pins_summary"]["quantity"] = 2
    with pytest.raises(SystemExit, match="INVALID"):
        extract_pins(qty2)


def test_c2_main_pasa_lock_path_a_process_market():
    # Regresión del CRITICAL C2 (re-review Codex): la ruta real de main() debe
    # pasar lock_path a process_market para que el lock se cree AL ABRIR el
    # sellado (fail-closed), no solo al final de todos los mercados. Falla con
    # el bug presente: la llamada con sealed_already_open sin lock_path.
    import ast
    src = (Path(__file__).parent / "run_w4.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "process_market"]
    assert calls, "no hay llamadas a process_market"
    with_lock = [c for c in calls
                 if any(k.arg == "lock_path" for k in c.keywords)]
    assert with_lock, "ninguna llamada a process_market pasa lock_path"
    for c in calls:
        kws = {k.arg for k in c.keywords}
        if "sealed_already_open" in kws:
            assert "lock_path" in kws, (
                "la ruta real (sealed_already_open) no pasa lock_path: "
                "el sellado se abriría sin lock persistente (fail-open)")
            assert "honor_preexisting_lock" in kws, (
                "la ruta real no desactiva honor_preexisting_lock: el lock "
                "creado por un mercado anterior en la MISMA corrida "
                "bloquearía a los siguientes (regresión multimercado)")


def test_c2_lock_creado_en_esta_corrida_no_bloquea_siguientes():
    # Regresión multimercado del delta C2: el lock creado por MNQ al abrir su
    # sellado NO debe tratarse como preexistente para MES/MYM en la misma
    # corrida (main pasa honor_preexisting_lock=False). El fail-closed entre
    # corridas lo cubre sealed_already_open calculado al arrancar main.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "w4_sealed.lock"
        lock.write_text('{"prereg_sha256": "x"}', encoding="utf-8")
        out = process_market("MNQ", long_week_bars(), pins(), None, set(),
                             False, lock_path=lock, prereg_sha="x",
                             honor_preexisting_lock=False)
        assert out["sellado"]["sellado_ya_abierto"] is False
    # Y el comportamiento entre corridas sigue fail-closed: mismo lock, pero
    # con honor_preexisting_lock=True (default) => bloqueado.
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "w4_sealed.lock"
        lock.write_text('{"prereg_sha256": "x"}', encoding="utf-8")
        out2 = process_market("MNQ", long_week_bars(), pins(), None, set(),
                              False, lock_path=lock, prereg_sha="x")
        assert out2["sellado"]["sellado_ya_abierto"] is True
        assert out2["B_on"]["tail"] is None


def test_fix1a_corrida_sin_mnq_dev_mes_positivo_bloquea_sellado_y_lock(tmp_path):
    # Fix 1a (CRITICAL): corrida sin MNQ (solo MES) con dev MES positivo (105 trades ganadores):
    # allow_sealed=None para MES se resuelve a False con motivo 'sin-MNQ-no-hay-gate-confirmatorio',
    # ningun tail corre y ningun lock se crea.
    p = pins()
    plan = make_mock_plan(2020, 2026)
    winning_bars = make_multi_week_bars(105, start_mon=date(2021, 1, 4), drift=0.2)
    lock_mes = tmp_path / "w4_mes.lock"
    out_mes = process_market("MES", winning_bars, p, plan, set(), smoke=False,
                             allow_sealed=None, lock_path=lock_mes, prereg_sha="sha_mes")
    assert out_mes["B_on"]["realista"]["n_trades"] == 105
    assert out_mes["sellado"]["dev_pasa"] is True  # El dev de MES en si es positivo
    assert out_mes["sellado"]["abierto"] is False   # Pero el sellado NO se abre sin MNQ
    assert out_mes["sellado"]["motivo"] == "sin-MNQ-no-hay-gate-confirmatorio"
    assert out_mes["B_on"]["tail"] is None
    assert not lock_mes.exists()


def test_fix1_lock_creacion_exclusiva_real(tmp_path):
    # Fix 1: creacion exclusiva real (os.O_CREAT | os.O_EXCL | os.O_WRONLY).
    # Si ya existe -> FileExistsError; process_market lo captura y no reabre tail.
    lock = tmp_path / "w4_exclusive.lock"
    write_sealed_lock(lock, "sha_test", ["B_on", "B_flip"], ["MNQ"])
    assert lock.exists()
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["prereg_sha256"] == "sha_test"
    assert payload["markets"] == ["MNQ"]

    # Segundo intento sobre el mismo archivo -> FileExistsError atomico
    with pytest.raises(FileExistsError):
        write_sealed_lock(lock, "sha_test", ["B_on"], ["MNQ"])


def test_fix2_g2_falla_si_muta_direccion_sensibilidad():
    # Fix 2: G2 concilia trade->senal por escenario (realista y sensibilidad).
    # Mutar la direccion de un trade de sensibilidad a 'WRONG' debe hacer fallar G2 (INVALID).
    bars = long_week_bars()
    groups = group_by_et_date(bars)
    wed = date(2025, 3, 5)
    ev = compute_signal_W4(wed, groups, bars, 0.25, {}, set())
    assert ev is not None
    e16 = find_first_at_or_after(groups[wed], bars, 16, 0)
    assert e16 is not None
    r = run_signal_day(bars, ev, MARKET_SPECS["MNQ"], "sensibilidad", e16)
    assert len(r["trades"]) == 1
    tr_mutado = dict(r["trades"][0])
    tr_mutado["direction"] = "WRONG"

    arms = {
        "B_on": {
            "dev": {
                "trades": r["trades"],
                "trades_sens": [tr_mutado],
                "decisions_primary": [signal_tuple(ev)],
                "decisions_sens": [signal_tuple(ev)],
                "gap_reject": 0, "gap_beyond_target": 0,
                "gap_reject_sens": 0, "gap_beyond_target_sens": 0,
                "collapsed": 0, "collapsed_sens": 0,
                "unresolved": 0, "unresolved_sens": 0,
                "n_eligible": {"realista": 1, "sensibilidad": 1},
                "n_attempted": 1,
                "per_fold": {},
            },
            "signals_dev": 1,
        }
    }
    sigmaps = {"B_on": {wed: ev}}
    g2 = evaluate_g2(arms, sigmaps, bars, dev_days=[wed], sealed_days=[], with_tail=False)
    assert g2["PASS"] is False
    assert g2["unmapped_trades"] >= 1
    assert "FALLO" in g2["parts"]["B_on/dev/sensibilidad"]

    # check_gates_ok fail-closed -> SystemExit(INVALID gate=G2)
    gates = {
        "G1_signal_parity": {"PASS": True},
        "G2_mapping": g2,
        "G3_determinism": {"PASS": True},
        "G4_sealed": {"PASS": True},
        "G5_cbb": {"status": "PASS"},
    }
    with pytest.raises(SystemExit, match="INVALID gate=G2"):
        check_gates_ok(gates, smoke=False)


def test_fix3_atr_wilder_14_recurrencia_fixture_codex():
    # Fix 3: ATR Wilder 14 con recurrencia atr_t = (atr_{t-1} * 13 + TR_t) / 14.
    # Fixture exacto de Codex: semilla 14 (14 TRs de 14.0) y dos TRs cero ->
    # atr_15 = (14 * 13 + 0) / 14 = 13.0
    # atr_16 = (13 * 13 + 0) / 14 = 169 / 14 = 12.071428571428571...
    # (El promedio simple de los ultimos 14 TR daria 12.0; Wilder da 12.07142857).
    day = date(2025, 3, 3)
    # Barra 0 de base (close=100.0)
    bars = [et_bar(day, 9, 30, o=100.0, h=100.0, lo=100.0, c=100.0)]
    # Barras 1..14 (14 barras) con TR = 14.0 cada una (h=114, lo=100, c=100)
    for i in range(1, 15):
        bars.append(et_bar(day, 9, 30, o=100.0, h=114.0, lo=100.0, c=100.0))
    # Barras 15 y 16 (2 barras) con TR = 0.0 cada una (h=100, lo=100, c=100)
    for i in (15, 16):
        bars.append(et_bar(day, 9, 30, o=100.0, h=100.0, lo=100.0, c=100.0))
    # Barra 17: senal en signal_idx = 17
    bars.append(et_bar(day, 9, 30, o=100.0, h=100.0, lo=100.0, c=100.0))

    atr = atr_wilder_14(bars, signal_idx=17, period=14)
    assert atr is not None
    esperado = 169.0 / 14.0
    assert atr == pytest.approx(esperado, abs=1e-8)
    assert round(atr, 8) == 12.07142857

    # Coincidencia exacta con la serie precomputada
    series = precompute_wilder_atr(bars, period=14)
    assert series[17] == pytest.approx(esperado, abs=1e-8)

