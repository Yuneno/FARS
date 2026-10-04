#!/usr/bin/env python3
"""
test_paper_bot.py — pruebas de los controles del bot en paper trading.

  P1 stop obligatorio: sin stop_price el intent nunca se crea.
  P2 kill-switch: SystemEvent(system_halted) hace denegar todo después.
  P3 límite de pérdida diaria: equity bajo el inicio de día > límite → denega.
  P4 máximo de operaciones: trades_applied >= max_trades → denega.
  P5 drawdown trailing sin peak_equity → denega (fail-closed).
  P6 tamaño de posición: respeta risk_per_trade y max_risk_dollars_per_order.
"""
from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, r"E:\FARS-LAB\FARS\lab_artifacts\orb_protocol")

import paper_bot as pb  # noqa: E402
from src.realtime.events import AccountSnapshot, Signal, SystemEvent  # noqa: E402
from src.realtime.risk import AccountAwareRiskEngine  # noqa: E402
from src.types import FundedAccountRules  # noqa: E402

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 10, 0, tzinfo=ET)


class Clock:
    def now(self):
        return T0


def rules(**kw):
    base = dict(initial_balance=100_000.0, profit_target_pct=0.05,
                max_drawdown_pct=0.08, daily_loss_limit_pct=0.02,
                risk_per_trade=0.01, daily_loss_base="initial",
                drawdown_mode="trailing", max_trades=10,
                daily_loss_limit_usd=2_000.0, max_drawdown_usd=8_000.0,
                max_risk_dollars_per_order=1_000.0)
    base.update(kw)
    return FundedAccountRules(**base)


def engine(r, equity=100_000.0, peak=100_000.0, trades=0, sod_equity=100_000.0):
    e = AccountAwareRiskEngine(r, Clock(), source="test")
    # primer snapshot = apertura del día (ancla de la pérdida diaria)
    e.observe(AccountSnapshot(event_id="a0", source="test", timestamp=T0,
                              sequence=0, origin="replay", balance=sod_equity,
                              equity=sod_equity, peak_equity=max(peak or sod_equity, sod_equity),
                              trades_applied=trades))
    if equity != sod_equity or peak is None:
        e.observe(AccountSnapshot(event_id="a1", source="test", timestamp=T0,
                                  sequence=1, origin="replay", balance=equity,
                                  equity=equity, peak_equity=peak,
                                  trades_applied=trades))
    return e


def sig(action="LONG"):
    return Signal(event_id="s1", source="test", timestamp=T0, sequence=2,
                  symbol="MNQ", action=action, origin="replay")


def test_p1_stop_obligatorio():
    g = pb.RiskGuard(rules(), pb.StepClock(T0))
    try:
        g.authorize(T0, "long", "MNQ", None)
    except RuntimeError:
        return
    raise AssertionError("sin stop el intent debe bloquearse en origen")


def test_p2_kill_switch_halted():
    e = engine(rules())
    e.observe(SystemEvent(event_id="h1", source="test", timestamp=T0, sequence=9,
                          kind="system_halted", detail="kill", origin="replay"))
    d = e.evaluate(sig())
    assert not d.approved, f"tras halt debe denegar, aprueba con {d.reason}"


def test_p3_perdida_diaria():
    r = rules(daily_loss_limit_usd=1_000.0)
    e = engine(r, equity=98_000.0, peak=100_000.0, sod_equity=100_000.0)
    d = e.evaluate(sig())
    assert not d.approved, f"pérdida diaria debe denegar, obtuvo {d.reason}"


def test_p4_max_trades():
    e = engine(rules(max_trades=5), trades=5)
    d = e.evaluate(sig())
    assert not d.approved, f"máximo de operaciones debe denegar, obtuvo {d.reason}"


def test_p5_drawdown_trailing_sin_peak():
    e = engine(rules(drawdown_mode="trailing"), peak=None)
    d = e.evaluate(sig())
    assert not d.approved, f"trailing sin peak debe denegar (fail-closed), obtuvo {d.reason}"


def test_p6_sizing():
    r = rules(risk_per_trade=0.01, max_risk_dollars_per_order=1_000.0)
    n = pb.position_size(100_000.0, r)
    # 1% de 100k = 1.000 USD de riesgo; tope 1.000; 100 pts * $2/pt = 200 USD/contrato
    assert n == 5, f"esperaba 5 contratos, obtuve {n}"
    n2 = pb.position_size(10_000.0, r)   # 1% = 100 USD -> 0 contratos
    assert n2 == 0, f"con riesgo insuficiente debe ser 0, obtuvo {n2}"


if __name__ == "__main__":
    fails = 0
    for name in sorted(n for n in globals() if n.startswith("test_p")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'TODAS VERDES' if fails == 0 else f'{fails} FALLANDO'}")
    sys.exit(1 if fails else 0)
