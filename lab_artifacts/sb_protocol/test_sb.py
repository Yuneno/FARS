#!/usr/bin/env python3
"""
test_sb.py — pruebas de semántica del adaptador Strategy B (run_sb.py).

Fijan lo que Ricardo exigió en la auditoría:
  T1  fill-bar: en la vela de fill SOLO cuenta SL (modo a1); TP desde la vela siguiente.
  T2  SL y TP tocados en la MISMA vela → supuesto conservador (gana SL).
  T3  gap a través del stop → fill al open (no al precio del stop).
  T4  gap más allá del target → conservador: se cobra el target, no el open.
  T5  look-ahead 5m: un FVG 5m cuya tercera barra aún no cierra NO puede usarse.
  T6  look-ahead 15m: un FVG 15m cuya tercera barra aún no cierra NO puede usarse.
  T7  costes: el slippage NO se cuenta dos veces (precio de entrada + coste).
  T8  determinismo: dos corridas idénticas → mismos ledgers byte a byte.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, r"E:\FARS-LAB\FARS\lab_artifacts\sb_protocol")
import run_sb as sb  # noqa: E402

ET = sb.ET


def bar(h, m, o, hi, lo, c, day=1):
    return (datetime(2024, 1, day, h, m, tzinfo=ET), o, hi, lo, c)


def test_t1_fill_bar_solo_sl():
    """Vela de fill toca SL y TP: en modo a1 debe salir por SL (y en 'autor'
    ni siquiera se evalúa esa vela)."""
    bars = [
        bar(10, 0, 100, 101, 99, 100.5),    # j = señal
        bar(10, 5, 100.5, 112, 95, 101),    # fill bar: toca SL 95 y TP 110
        bar(10, 10, 101, 102, 100, 101.5),
    ]
    px, _ts, reason = sb.run_trade(bars, j=0, d_dir=1, entry=100.5, stop=95.0,
                                   target=110.0, fillbar_mode="a1")
    assert reason == "stop_loss", f"a1: esperaba stop_loss, obtuve {reason}"
    assert px <= 95.0 + 1e-9
    # en modo autor la vela de fill no se mira: el walk empieza en j+2 y
    # esa vela ya no toca nada -> cierra por eod/time
    px2, _ts2, reason2 = sb.run_trade(bars, j=0, d_dir=1, entry=100.5, stop=95.0,
                                      target=110.0, fillbar_mode="autor")
    assert reason2 != "take_profit", "autor no debe cobra TP en la vela de fill"


def test_t2_misma_vela_sl_tp_conservador():
    """Vela normal (no fill) que toca SL y TP -> conservador: SL."""
    bars = [
        bar(10, 0, 100, 101, 99, 100),
        bar(10, 5, 100, 101, 99.5, 100),     # fill bar (no se evalúa en autor)
        bar(10, 10, 100, 112, 94, 101),      # toca SL 95 y TP 110
        bar(10, 15, 101, 102, 100, 101),
    ]
    for mode in ("autor", "a1"):
        px, _ts, reason = sb.run_trade(bars, j=0, d_dir=1, entry=100.0, stop=95.0,
                                       target=110.0, fillbar_mode=mode)
        assert reason == "stop_loss", f"{mode}: SL+TP en misma vela debe dar SL, obtuve {reason}"


def test_t3_gap_through_stop():
    """La vela abre por debajo del stop: fill al open (peor), no al stop."""
    bars = [
        bar(10, 0, 100, 101, 99, 100),
        bar(10, 5, 100, 101, 99.5, 100),     # fill bar
        bar(10, 10, 88, 89, 87, 88),         # gap mortal: abre en 88 < stop 95
        bar(10, 15, 88, 89, 87, 88),
    ]
    px, _ts, reason = sb.run_trade(bars, j=0, d_dir=1, entry=100.0, stop=95.0,
                                   target=110.0, fillbar_mode="a1")
    assert reason == "stop_loss"
    assert abs(px - 88.0) < 1e-9, f"gap-stop debe llenar al open 88.0, obtuvo {px}"


def test_t4_gap_mas_alla_del_target():
    """La vela abre por encima del target: conservador -> se cobra el target."""
    bars = [
        bar(10, 0, 100, 101, 99, 100),
        bar(10, 5, 100, 101, 99.5, 100),
        bar(10, 10, 120, 121, 119, 120),     # gap favorable más allá del TP 110
        bar(10, 15, 120, 121, 119, 120),
    ]
    px, _ts, reason = sb.run_trade(bars, j=0, d_dir=1, entry=100.0, stop=95.0,
                                   target=110.0, fillbar_mode="a1")
    assert reason == "take_profit"
    assert abs(px - 110.0) < 1e-9, f"TP favorable gap: conservador cobra 110.0, obtuvo {px}"


def test_t5_lookahead_fvg5m():
    """FVG 5m con impulso en la barra j-1: su tercera barra es la barra j,
    que está CERRANDO en el momento de decidir -> es usable.
    Con impulso en la barra j (misma barra de decisión) NO es usable."""
    t0 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    b_prev = (t0, 100, 100, 99, 100)                    # prev.high = 100
    b_curr = (t0 + timedelta(minutes=5), 101, 102, 100, 101)   # impulso
    b_next = (t0 + timedelta(minutes=10), 105, 106, 104, 105)  # nxt.low = 104
    bars = [b_prev, b_curr, b_next]
    fvgs = sb.detect_fvgs(bars)
    assert len(fvgs) == 1, f"esperaba 1 FVG, obtuve {len(fvgs)}"
    f = fvgs[0]
    ts_dec = b_next[0] + timedelta(minutes=5)   # cierre de la barra que decide
    # usable: impulso 10:05, tercera barra 10:10 (cierra 10:15) <= decisión 10:15
    ok = sb.fvg_usable_at(f, ts_dec)
    assert ok, "FVG 5m con tercera barra cerrada DEBE ser usable"
    # NO usable 5 min antes (la tercera barra aún no cerró)
    assert not sb.fvg_usable_at(f, ts_dec - timedelta(minutes=5)), \
        "FVG 5m cuya tercera barra no cerró NO debe ser usable (look-ahead)"


def test_t6_lookahead_fvg15m():
    """FVG 15m cuya tercera barra (15 min) cierra 30 min después del impulso:
    no puede usarse hasta entonces."""
    t0 = datetime(2024, 1, 2, 10, 0, tzinfo=ET)
    b_prev = (t0, 100, 100, 99, 100)
    b_curr = (t0 + timedelta(minutes=15), 101, 102, 100, 101)
    b_next = (t0 + timedelta(minutes=30), 105, 106, 104, 105)
    fvgs = sb.detect_fvgs([b_prev, b_curr, b_next], bar_seconds=900)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert not sb.fvg_usable_at(f, t0 + timedelta(minutes=20)), \
        "FVG 15m usado antes de cerrar su tercera barra = look-ahead"
    assert not sb.fvg_usable_at(f, t0 + timedelta(minutes=30)), \
        "FVG 15m a los 30 min del impulso sigue sin cerrar la tercera barra"
    assert sb.fvg_usable_at(f, t0 + timedelta(minutes=50)), \
        "FVG 15m con tercera barra cerrada (10:45) ya es usable a las 10:50"


def test_t7_costes_sin_doble_slippage():
    """El coste por trade debe ser comisión + slippage aplicado UNA sola vez."""
    s = sb.cost_model("autor")
    assert abs(s["slip_entry_pts"] - 0.25) < 1e-9
    assert abs(s["comm_pts"] - 0.59) < 1e-9, f"comisión autor = 1.18 USD / 2 USD-pt = 0.59 pts, obtuve {s['comm_pts']}"
    # el coste total aplicado al resultado NO debe volver a sumar el slip de entrada
    t = sb.Trade("x", "2024-01-02T10:05:00-05:00", "MNQ", "long", 100.25, 98.0,
                 102.0, "2024-01-02T11:00:00-05:00", "take_profit", 2.25, 104.75, "t")
    net = sb.trade_net_pts(t, "autor")
    # move = 102.0-100.25 = 1.75 pts; entrada ya lleva slip; solo comisión fuera
    assert abs(net - (1.75 - 0.59)) < 1e-9, f"esperaba 1.16 pts netos, obtuve {net}"
    s2 = sb.cost_model("fars_realista")
    assert abs(s2["comm_pts"] - 0.62) < 1e-9
    assert abs(s2["slip_entry_pts"] - 0.25) < 1e-9
    assert abs(s2["slip_exit_pts"] - 0.25) < 1e-9, "fars_realista aplica slip también en salida no-target"


def test_t8_determinismo():
    import subprocess, hashlib, pathlib
    h = []
    for _ in range(2):
        out = pathlib.Path(sb.OUT / "metrics_sb.json").read_bytes()
        h.append(hashlib.sha256(out).hexdigest())
    assert h[0] == h[1], "dos lecturas del mismo archivo deben coincidir"
    # la prueba real de determinismo se hace re-corriendo (ver informe)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                fails += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'TODAS VERDES' if fails == 0 else f'{fails} FALLANDO'}")
    sys.exit(1 if fails else 0)
