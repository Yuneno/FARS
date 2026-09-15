"""Tests de la capa de politicas de apuesta (E4)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.account_engine import AccountEngineConfig, run_account_simulation
from src.account_policy import SizingPolicy, SizingPolicyConfig
from src.backtest.executor import ExecutedTrade
from src.funded_profiles import apex_25k_profile


def _risk(policy, balance="25000", floor="23500", base="500"):
    p = SizingPolicy(policy)
    return p.risk_usd(
        balance=Decimal(balance), floor=Decimal(floor), base_risk_usd=Decimal(base)
    )


def test_fixed_passthrough():
    assert _risk(SizingPolicyConfig(kind="fixed")) == Decimal("500")


def test_buffer_prop_proportional_within_band():
    cfg = SizingPolicyConfig(kind="buffer_prop", param=0.10)
    # colchon = 25000 - 23500 = 1500 -> 10% = 150 (dentro de [25, 375])
    assert _risk(cfg) == Decimal("150")


def test_buffer_prop_clamped_low_and_high():
    cfg = SizingPolicyConfig(kind="buffer_prop", param=0.10)
    # colchon = 100 -> 10% = 10 -> piso 25
    assert _risk(cfg, balance="25000", floor="24900") == Decimal("25")
    cfg_k1 = SizingPolicyConfig(kind="buffer_prop", param=1.0)
    # colchon = 1500 -> 1500 -> techo 375
    assert _risk(cfg_k1) == Decimal("375")


def test_streak_halves_after_two_losses_and_resets():
    cfg = SizingPolicyConfig(kind="fixed", streak_threshold=2, streak_factor=0.5)
    p = SizingPolicy(cfg)
    base = dict(balance=Decimal("25000"), floor=Decimal("23500"), base_risk_usd=Decimal("500"))
    assert p.risk_usd(**base) == Decimal("500")
    p.note_result(Decimal("-100")); p.note_result(Decimal("-100"))
    assert p.risk_usd(**base) == Decimal("250")
    p.note_result(Decimal("50"))
    assert p.risk_usd(**base) == Decimal("500")


def test_buffer_prop_with_streak_composes():
    cfg = SizingPolicyConfig(kind="buffer_prop", param=0.10, streak_threshold=2, streak_factor=0.5)
    p = SizingPolicy(cfg)
    base = dict(balance=Decimal("25000"), floor=Decimal("23500"), base_risk_usd=Decimal("500"))
    assert p.risk_usd(**base) == Decimal("150")
    p.note_result(Decimal("-1")); p.note_result(Decimal("-1"))
    assert p.risk_usd(**base) == Decimal("75")


@pytest.mark.parametrize(
    "kwargs,msg",
    [
        ({"kind": "weird"}, "kind"),
        ({"kind": "buffer_prop", "param": 0}, "param"),
        ({"floor_frac": 0.05, "cap_frac": 0.02}, "floor_frac"),
        ({"streak_threshold": 2, "streak_factor": 1.2}, "streak_factor"),
        ({"streak_threshold": 2, "streak_factor": 1.0}, "reduce"),
    ],
)
def test_policy_config_validation(kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        SizingPolicyConfig(**kwargs)


def _make_trade(trade_id, t0, net_pnl, budgeted_risk_dollars=100.0):
    return ExecutedTrade(
        trade_id=trade_id,
        direction="long",
        entry_time=t0,
        exit_time=t0 + timedelta(minutes=10),
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


def test_engine_streak_policy_halves_third_trade_quantity():
    """E4 end-to-end: tras 2 perdidas seguidas, el motor dimensiona a la mitad."""
    profile = apex_25k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    trades = [
        _make_trade("t1", t0, net_pnl=-50.0),
        _make_trade("t2", t0 + timedelta(hours=1), net_pnl=-50.0),
        _make_trade("t3", t0 + timedelta(hours=2), net_pnl=150.0),
    ]
    cfg = AccountEngineConfig(
        risk_pct=0.02, trailing_mode="closed_trade",
        policy=SizingPolicyConfig(kind="fixed", streak_threshold=2, streak_factor=0.5),
    )
    res = run_account_simulation(profile, trades, cfg)
    # unidad de riesgo 100: qty completa = 500/100 = 5; con racha = 250/100 = 2
    assert res.trades_executed == 3
    assert res.records[0].quantity == 5
    assert res.records[1].quantity == 5
    assert res.records[2].quantity == 2


def test_engine_without_policy_matches_legacy_sizing():
    """Sin politica (None), el motor dimensiona igual que antes (regresion E4)."""
    profile = apex_25k_profile()
    t0 = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    trades = [
        _make_trade("t1", t0, net_pnl=-50.0),
        _make_trade("t2", t0 + timedelta(hours=1), net_pnl=-50.0),
        _make_trade("t3", t0 + timedelta(hours=2), net_pnl=150.0),
    ]
    cfg = AccountEngineConfig(risk_pct=0.02, trailing_mode="closed_trade")
    res = run_account_simulation(profile, trades, cfg)
    assert res.trades_executed == 3
    assert [r.quantity for r in res.records] == [5, 5, 5]
