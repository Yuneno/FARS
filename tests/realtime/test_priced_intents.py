"""Pruebas offline exhaustivas para el bridge de priced intents de MNQ.

Cubre todos los requerimientos obligatorios de FARS_GEMINI_ENCARGO_MNQ_PRICED_INTENT_BRIDGE.md:
1. Factory default priceless continúa siendo rechazado por PracticeExecutionAdapter (fallback y gateway).
2. Señal SMC-FVG sintética aceptada produce intent con entry/stop/target/dpp exactos y tick-validados.
3. Paridad causal sobre las mismas barras canónicas: backtest y adapter realtime emiten mismos datos.
4. LONG y SHORT con polaridad de brackets correcta.
5. Identidad, origin, symbol y action mismatch fallan cerrado.
6. Contexto faltante, duplicado conflictivo o reutilizado falla cerrado.
7. Tamaño >1 queda clamped a 1 micro en fallback y gateway double.
8. Riesgo >$200 a 1 micro es vetado por MAX_RISK_PER_ORDER sin llamar a place_order.
9. Riesgo <=$200 es aceptado en pre-check y el gateway double recibe brackets coherentes.
10. La ruta no abre red ni requiere credenciales (100% offline).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
import pytest

from src.backtest.history import Bar as BacktestBar
from src.backtest.markets import MNQ
from src.backtest.smc_fvg import SmcFvgStrategy
from src.backtest.strategy import Signal as BacktestSignal
from src.realtime.bus import AsyncIOEventBus
from src.realtime.clock import FrozenClock
from src.realtime.connector import ReplayMarketConnector
from src.realtime.connectors.projectx import JsonTransport
from src.realtime.events import (
    Bar as RealtimeBar,
    EXEC_ACCEPTED,
    EXEC_FILLED,
    EXEC_REJECTED,
    ORIGIN_LIVE,
    ORIGIN_REPLAY,
    OrderIntent,
    RiskDecision,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    Signal as RealtimeSignal,
)
from src.realtime.orders.practice_client import (
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_STATUS_WORKING,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_STOP,
    PracticeOrderClient,
)
from src.realtime.paper import default_paper_assumptions
from src.realtime.practice_adapter import PracticeExecutionAdapter
from src.realtime.priced_intents import (
    MNQ_DOLLARS_PER_POINT,
    MNQ_SYMBOL,
    MNQ_TICK_SIZE,
    MnqPricedIntentBridge,
    MnqPricedStrategyAdapter,
    PricingContext,
    round_mnq_prices_conservative,
)
from src.realtime.recorder import FileEventRecorder
from src.realtime.risk import AccountAwareRiskEngine, REASON_APPROVED
from src.realtime.session import PaperRealtimeSession, default_intent_factory
from src.types import FundedAccountRules

TEST_ACCOUNT_NAME = "PRAC-V2-673085-85699223"
TEST_ACCOUNT_ID = 27765990
TEST_TIME = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


class MockTransport(JsonTransport):
    """Transporte mock que registra llamadas a la API sin abrir conexiones de red."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.canned_order_id = 99991111

    def post(self, url: str, headers: Any, payload: Any, timeout: float) -> dict[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        self.calls.append((endpoint, dict(payload)))
        if endpoint == "/api/Order/place":
            return {"orderId": self.canned_order_id, "success": True, "errorMessage": None}
        return {"success": True}


def create_gateway_client(transport: MockTransport) -> PracticeOrderClient:
    return PracticeOrderClient(
        token_provider="offline-test-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )


def create_practice_adapter(
    clock: FrozenClock,
    order_client: PracticeOrderClient | None = None,
    max_risk: float = 200.0,
) -> PracticeExecutionAdapter:
    return PracticeExecutionAdapter(
        clock=clock,
        account_name=TEST_ACCOUNT_NAME,
        account_id=TEST_ACCOUNT_ID,
        contract_id=MNQ_SYMBOL,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
        order_client=order_client,
        max_risk_dollars_per_order=max_risk,
    )


def make_backtest_bar(idx: int, open_: float, high: float, low: float, close: float) -> BacktestBar:
    return BacktestBar(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * idx),
        open_,
        high,
        low,
        close,
        100.0,
    )


def make_realtime_bar(idx: int, open_: float, high: float, low: float, close: float) -> RealtimeBar:
    return RealtimeBar(
        event_id=f"bar-{idx}",
        source="feed",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * idx),
        sequence=idx + 1,
        symbol=MNQ_SYMBOL,
        interval="5m",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        origin=ORIGIN_LIVE,
    )


def bullish_fvg_bars() -> list[tuple[float, float, float, float]]:
    """Estructura alcista con FVG claro (swing_w=1, min_risk_pts=0.0)."""
    return [
        (100.0, 101.0, 95.0, 100.0),
        (100.0, 105.0, 90.0, 100.0),  # swing low at t=1
        (100.0, 110.0, 99.0, 105.0),  # swing high at t=2
        (105.0, 108.0, 101.0, 106.0),
        (112.0, 115.0, 111.0, 114.0),  # BOS + bullish FVG over high[t-2]=110.0
    ]


def bearish_fvg_bars() -> list[tuple[float, float, float, float]]:
    """Estructura bajista con FVG claro (swing_w=1, min_risk_pts=0.0)."""
    return [
        (100.0, 105.0, 99.0, 100.0),
        (100.0, 110.0, 95.0, 100.0),  # swing high at t=1
        (100.0, 101.0, 90.0, 95.0),   # swing low at t=2
        (95.0, 99.0, 92.0, 94.0),
        (88.0, 89.0, 85.0, 86.0),     # BOS + bearish FVG below low[t-2]=90.0
    ]


# ==============================================================================
# 1. Factory default priceless continúa siendo rechazado
# ==============================================================================

def test_default_factory_priceless_rejected_by_practice_adapter() -> None:
    clock = FrozenClock(TEST_TIME)
    sig = RealtimeSignal("sig-1", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-1", "risk", clock.now(), 1, "sig-1", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    intent = default_intent_factory(sig, dec, 1, clock)
    assert intent.entry_price is None
    assert intent.stop_price is None
    assert intent.target_price is None

    # Fallback simulation
    adapter_fb = create_practice_adapter(clock, order_client=None)
    rep_fb = adapter_fb.submit(sig, dec, intent)
    assert rep_fb.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in rep_fb.reason

    # Gateway double
    transport = MockTransport()
    client = create_gateway_client(transport)
    adapter_gw = create_practice_adapter(clock, order_client=client)
    rep_gw = adapter_gw.submit(sig, dec, intent)
    assert rep_gw.status == EXEC_REJECTED
    assert "MISSING_ENTRY_OR_STOP_PRICE" in rep_gw.reason
    assert len(transport.calls) == 0


# ==============================================================================
# 2. Señal SMC-FVG sintética aceptada produce intent con entry/stop/target/dpp
# ==============================================================================

def test_smc_fvg_synthetic_produces_exact_and_tick_validated_intent() -> None:
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    strat = SmcFvgStrategy(market=MNQ, swing_w=1, min_risk_pts=0.0)

    bars_raw = bullish_fvg_bars()
    b_bars = [make_backtest_bar(i, *b) for i, b in enumerate(bars_raw)]

    backtest_sig = strat.evaluate(b_bars)
    assert backtest_sig is not None
    assert backtest_sig.direction == "long"
    assert backtest_sig.entry == 111.0

    realtime_sig = RealtimeSignal("sig-smc-1", "smc_strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-smc-1", "risk", clock.now(), 1, "sig-smc-1", "smc_strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    ctx = bridge.register_context(realtime_sig, backtest_sig, size=1)
    intent = bridge.create_intent(realtime_sig, dec, 1, clock)

    assert intent.symbol == MNQ_SYMBOL
    assert intent.action == SIGNAL_LONG
    assert intent.risk_decision_id == "dec-smc-1"
    assert intent.dollars_per_point == 2.0
    assert intent.size == 1

    # Verificación de ticks múltiplos exactos de 0.25
    assert round(intent.entry_price % 0.25, 4) in (0.0, 0.25)
    assert round(intent.stop_price % 0.25, 4) in (0.0, 0.25)
    assert round(intent.target_price % 0.25, 4) in (0.0, 0.25)

    # Invariante conservadora: stop redondeado hacia abajo expande la distancia (nunca reduce riesgo)
    raw_risk = backtest_sig.entry - backtest_sig.stop
    rounded_risk = intent.entry_price - intent.stop_price
    assert rounded_risk >= raw_risk
    assert intent.stop_price < intent.entry_price < intent.target_price


# ==============================================================================
# 3. Paridad causal sobre las mismas barras canónicas
# ==============================================================================

def test_causal_parity_backtest_vs_realtime_adapter() -> None:
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    rt_adapter = MnqPricedStrategyAdapter(
        strategy=SmcFvgStrategy(market=MNQ, swing_w=1, min_risk_pts=0.0),
        bridge=bridge,
        source="test_smc",
    )
    bt_strat = SmcFvgStrategy(market=MNQ, swing_w=1, min_risk_pts=0.0)

    bars_raw = bullish_fvg_bars()
    bt_signals = []
    rt_signals = []

    for i, b in enumerate(bars_raw):
        b_bar = make_backtest_bar(i, *b)
        r_bar = make_realtime_bar(i, *b)

        # En backtest: evaluamos con historial hasta la barra actual i
        sig_bt = bt_strat.evaluate([make_backtest_bar(j, *bars_raw[j]) for j in range(i + 1)])
        if sig_bt is not None:
            bt_signals.append((b_bar.timestamp, sig_bt))

        # En realtime: alimentamos evento por evento
        sig_rt = rt_adapter.on_event(r_bar)
        if sig_rt is not None:
            rt_signals.append((r_bar.timestamp, sig_rt))

    assert len(bt_signals) == 1
    assert len(rt_signals) == 1

    bt_ts, bt_sig = bt_signals[0]
    rt_ts, rt_sig = rt_signals[0]

    # Mismo timestamp causal
    assert bt_ts == rt_ts
    assert bt_sig.direction == "long"
    assert rt_sig.action == SIGNAL_LONG

    # Verificar que el bridge tiene el contexto con precios correspondientes
    key = (rt_sig.source, rt_sig.event_id)
    ctx = bridge._contexts[key]
    assert ctx.raw_entry == bt_sig.entry
    assert ctx.raw_stop == bt_sig.stop
    assert ctx.raw_target == bt_sig.target


# ==============================================================================
# 4. LONG y SHORT
# ==============================================================================

def test_long_and_short_signals_with_correct_bracket_polarity() -> None:
    clock = FrozenClock(TEST_TIME)

    # Caso LONG
    bridge_l = MnqPricedIntentBridge()
    strat_l = SmcFvgStrategy(market=MNQ, swing_w=1, min_risk_pts=0.0)
    sig_l = strat_l.evaluate([make_backtest_bar(i, *b) for i, b in enumerate(bullish_fvg_bars())])
    assert sig_l is not None and sig_l.direction == "long"

    rsig_l = RealtimeSignal("s-l", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec_l = RiskDecision("d-l", "risk", clock.now(), 1, "s-l", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    bridge_l.register_context(rsig_l, sig_l)
    intent_l = bridge_l.create_intent(rsig_l, dec_l, 1, clock)

    assert intent_l.action == SIGNAL_LONG
    assert intent_l.stop_price < intent_l.entry_price < intent_l.target_price
    assert (intent_l.entry_price - intent_l.stop_price) >= (sig_l.entry - sig_l.stop)

    # Caso SHORT
    bridge_s = MnqPricedIntentBridge()
    strat_s = SmcFvgStrategy(market=MNQ, swing_w=1, min_risk_pts=0.0)
    sig_s = strat_s.evaluate([make_backtest_bar(i, *b) for i, b in enumerate(bearish_fvg_bars())])
    assert sig_s is not None and sig_s.direction == "short"

    rsig_s = RealtimeSignal("s-s", "strat", clock.now(), 2, MNQ_SYMBOL, SIGNAL_SHORT, origin=ORIGIN_LIVE)
    dec_s = RiskDecision("d-s", "risk", clock.now(), 2, "s-s", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    bridge_s.register_context(rsig_s, sig_s)
    intent_s = bridge_s.create_intent(rsig_s, dec_s, 2, clock)

    assert intent_s.action == SIGNAL_SHORT
    assert intent_s.target_price < intent_s.entry_price < intent_s.stop_price
    assert (intent_s.stop_price - intent_s.entry_price) >= (sig_s.stop - sig_s.entry)


# ==============================================================================
# 5. Identidad/origin/symbol/action mismatch falla cerrado
# ==============================================================================

def test_fail_closed_on_identity_origin_symbol_and_action_mismatch() -> None:
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    bt_sig = BacktestSignal("long", 15000.0, 14950.0, 15100.0)

    sig = RealtimeSignal("sig-m", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    bridge.register_context(sig, bt_sig)

    # 1. Decision signal_id no coincide
    dec_bad_id = RiskDecision("dec-1", "risk", clock.now(), 1, "different-sig", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    with pytest.raises(ValueError, match="references signal"):
        bridge.create_intent(sig, dec_bad_id, 1, clock)

    # 2. Origin mismatch
    dec_bad_orig = RiskDecision("dec-2", "risk", clock.now(), 1, "sig-m", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_REPLAY)
    with pytest.raises(ValueError, match="origin 'replay' does not match Signal origin 'live'"):
        bridge.create_intent(sig, dec_bad_orig, 1, clock)

    # 3. Symbol no es MNQ
    sig_mes = RealtimeSignal("sig-mes", "strat", clock.now(), 2, "MES", SIGNAL_LONG, origin=ORIGIN_LIVE)
    with pytest.raises(ValueError, match="Bridge accepts symbol 'MNQ' only"):
        bridge.register_context(sig_mes, bt_sig)

    # 4. Action mismatch entre backtest y realtime
    sig_short = RealtimeSignal("sig-s", "strat", clock.now(), 3, MNQ_SYMBOL, SIGNAL_SHORT, origin=ORIGIN_LIVE)
    with pytest.raises(ValueError, match="does not match backtest direction"):
        bridge.register_context(sig_short, bt_sig)

    # 5. Brackets inválidos en round_mnq_prices_conservative
    with pytest.raises(ValueError, match="Invalid LONG brackets"):
        round_mnq_prices_conservative(SIGNAL_LONG, 100.0, 105.0, 110.0)  # stop > entry

    # 6. Precios negativos o no finitos
    with pytest.raises(ValueError, match="finite positive number"):
        round_mnq_prices_conservative(SIGNAL_LONG, 100.0, -10.0, 110.0)


# ==============================================================================
# 6. Contexto faltante, duplicado conflictivo o reutilizado falla cerrado
# ==============================================================================

def test_fail_closed_on_missing_conflicting_or_reused_context() -> None:
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    bt_sig = BacktestSignal("long", 15000.0, 14950.0, 15100.0)
    sig = RealtimeSignal("sig-c", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-c", "risk", clock.now(), 1, "sig-c", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    # 1. Contexto faltante
    with pytest.raises(ValueError, match="Missing pricing context"):
        bridge.create_intent(sig, dec, 1, clock)

    # Registrar contexto inicial
    bridge.register_context(sig, bt_sig, size=1)

    # 2. Duplicado idéntico es idempotente y se tolera
    bridge.register_context(sig, bt_sig, size=1)

    # 3. Duplicado conflictivo (diferente tamaño o precios) falla
    bt_sig_diff = BacktestSignal("long", 15000.0, 14900.0, 15100.0)
    with pytest.raises(ValueError, match="Conflicting duplicate pricing context"):
        bridge.register_context(sig, bt_sig_diff, size=1)

    # 4. Consumir contexto
    intent = bridge.create_intent(sig, dec, 1, clock)
    assert intent is not None

    # 5. Reutilizar contexto ya consumido falla cerrado
    with pytest.raises(ValueError, match="already been consumed"):
        bridge.create_intent(sig, dec, 2, clock)


# ==============================================================================
# 7. Tamaño >1 queda en 1 micro en fallback y gateway double
# ==============================================================================

def test_size_greater_than_one_clamped_to_one_micro() -> None:
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    bt_sig = BacktestSignal("long", 15000.0, 14980.0, 15050.0)  # risk = 20 pts = $40/micro; $120 for 3 micros <= $200
    sig = RealtimeSignal("sig-sz", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-sz", "risk", clock.now(), 1, "sig-sz", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    # Tamaño solicitado 3
    bridge.register_context(sig, bt_sig, size=3)
    intent = bridge.create_intent(sig, dec, 1, clock)
    assert intent.size == 3

    # Fallback: el adapter clampa a 1 micro
    adapter_fb = create_practice_adapter(clock, order_client=None)
    rep_fb = adapter_fb.submit(sig, dec, intent)
    assert rep_fb.status == EXEC_FILLED
    assert "CLAMPED_TO_1_MICRO" in rep_fb.reason
    assert adapter_fb.open_positions[MNQ_SYMBOL] == 1

    # Gateway double: el adapter envía size=1 a place_order
    bridge_gw = MnqPricedIntentBridge()
    bridge_gw.register_context(sig, bt_sig, size=3)
    intent_gw = bridge_gw.create_intent(sig, dec, 2, clock)

    transport = MockTransport()
    client = create_gateway_client(transport)
    adapter_gw = create_practice_adapter(clock, order_client=client)
    rep_gw = adapter_gw.submit(sig, dec, intent_gw)
    assert rep_gw.status == EXEC_ACCEPTED
    assert len(transport.calls) == 1
    place_call = transport.calls[0]
    assert place_call[0] == "/api/Order/place"
    assert place_call[1]["size"] == 1  # Clamped to 1 micro


# ==============================================================================
# 8. Si 1 micro implica riesgo >$200, ambos caminos vetan con MAX_RISK_PER_ORDER
# ==============================================================================

def test_risk_exceeding_max_risk_vetoed_on_both_paths() -> None:
    clock = FrozenClock(TEST_TIME)
    # Stop a 110 puntos: riesgo en MNQ = 110 * 2.0 * 1 = $220 > $200
    bt_sig = BacktestSignal("long", 15000.0, 14890.0, 15150.0)

    # Fallback
    bridge_fb = MnqPricedIntentBridge()
    sig_fb = RealtimeSignal("sig-r-fb", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec_fb = RiskDecision("dec-r-fb", "risk", clock.now(), 1, "sig-r-fb", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    bridge_fb.register_context(sig_fb, bt_sig, size=1)
    intent_fb = bridge_fb.create_intent(sig_fb, dec_fb, 1, clock)

    adapter_fb = create_practice_adapter(clock, order_client=None, max_risk=200.0)
    rep_fb = adapter_fb.submit(sig_fb, dec_fb, intent_fb)
    assert rep_fb.status == EXEC_REJECTED
    assert "MAX_RISK_PER_ORDER" in rep_fb.reason
    assert "$220.00 (at 1 micro) exceeds limit $200.00" in rep_fb.reason
    assert adapter_fb.open_positions.get(MNQ_SYMBOL, 0) == 0

    # Gateway double
    bridge_gw = MnqPricedIntentBridge()
    sig_gw = RealtimeSignal("sig-r-gw", "strat", clock.now(), 2, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec_gw = RiskDecision("dec-r-gw", "risk", clock.now(), 2, "sig-r-gw", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    bridge_gw.register_context(sig_gw, bt_sig, size=1)
    intent_gw = bridge_gw.create_intent(sig_gw, dec_gw, 2, clock)

    transport = MockTransport()
    client = create_gateway_client(transport)
    adapter_gw = create_practice_adapter(clock, order_client=client, max_risk=200.0)
    rep_gw = adapter_gw.submit(sig_gw, dec_gw, intent_gw)
    assert rep_gw.status == EXEC_REJECTED
    assert "MAX_RISK_PER_ORDER" in rep_gw.reason
    # place_order NO debe haber sido llamado
    assert len(transport.calls) == 0


# ==============================================================================
# 9. Para riesgo <=$200, fallback y gateway toman la misma decisión y brackets coherentes
# ==============================================================================

def test_risk_within_limit_accepted_and_brackets_verified() -> None:
    clock = FrozenClock(TEST_TIME)
    # Stop a 50 puntos: riesgo en MNQ = 50 * 2.0 * 1 = $100 <= $200
    # Target a 75 puntos (1.5R)
    bt_sig = BacktestSignal("long", 15000.0, 14950.0, 15075.0)

    # Fallback
    bridge_fb = MnqPricedIntentBridge()
    sig_fb = RealtimeSignal("sig-ok-fb", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec_fb = RiskDecision("dec-ok-fb", "risk", clock.now(), 1, "sig-ok-fb", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    bridge_fb.register_context(sig_fb, bt_sig, size=1)
    intent_fb = bridge_fb.create_intent(sig_fb, dec_fb, 1, clock)

    adapter_fb = create_practice_adapter(clock, order_client=None, max_risk=200.0)
    rep_fb = adapter_fb.submit(sig_fb, dec_fb, intent_fb)
    assert rep_fb.status == EXEC_FILLED
    assert adapter_fb.open_positions[MNQ_SYMBOL] == 1

    # Gateway double
    bridge_gw = MnqPricedIntentBridge()
    sig_gw = RealtimeSignal("sig-ok-gw", "strat", clock.now(), 2, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec_gw = RiskDecision("dec-ok-gw", "risk", clock.now(), 2, "sig-ok-gw", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)
    bridge_gw.register_context(sig_gw, bt_sig, size=1)
    intent_gw = bridge_gw.create_intent(sig_gw, dec_gw, 2, clock)

    transport = MockTransport()
    client = create_gateway_client(transport)
    adapter_gw = create_practice_adapter(clock, order_client=client, max_risk=200.0)
    rep_gw = adapter_gw.submit(sig_gw, dec_gw, intent_gw)
    assert rep_gw.status == EXEC_ACCEPTED
    assert len(transport.calls) == 1

    payload = transport.calls[0][1]
    assert payload["side"] == ORDER_SIDE_BUY
    assert payload["size"] == 1
    assert payload["contractId"] == MNQ_SYMBOL
    assert payload["limitPrice"] == 15000.0
    # Stop bracket: 50 pts / 0.25 = 200 ticks
    assert payload["stopLossBracket"] == {"ticks": 200, "type": ORDER_TYPE_STOP}
    # Target bracket: 75 pts / 0.25 = 300 ticks
    assert payload["takeProfitBracket"] == {"ticks": 300, "type": ORDER_TYPE_LIMIT}


# ==============================================================================
# 10. La ruta no abre red ni requiere credenciales (100% offline)
# ==============================================================================

def test_offline_session_run_with_priced_bridge(tmp_path) -> None:
    """Verifica una sesión completa offline ejecutando con el bridge de priced intents."""
    import asyncio
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    strategy = MnqPricedStrategyAdapter(
        strategy=SmcFvgStrategy(market=MNQ, swing_w=1, min_risk_pts=0.0),
        bridge=bridge,
        source="priced_smc",
    )

    # Conector replay con snapshot inicial y barras sintéticas
    bars_raw = bullish_fvg_bars()
    payloads = [
        {
            "type": "snapshot",
            "event_id": "snap-1",
            "timestamp": TEST_TIME.isoformat(),
            "sequence": 1,
            "equity": 100_000.0,
            "balance": 100_000.0,
            "peak_equity": 100_000.0,
            "last_sync": TEST_TIME.isoformat(),
        }
    ]
    for i, b in enumerate(bars_raw):
        payloads.append(
            {
                "type": "bar",
                "event_id": f"bar-{i}",
                "timestamp": (TEST_TIME + timedelta(minutes=5 * i)).isoformat(),
                "sequence": i + 2,
                "symbol": MNQ_SYMBOL,
                "interval": "5m",
                "open": b[0],
                "high": b[1],
                "low": b[2],
                "close": b[3],
                "volume": 100.0,
            }
        )
    connector = ReplayMarketConnector(payloads, source="replay-feed", clock=clock)

    bus = AsyncIOEventBus()
    recorder = FileEventRecorder(tmp_path / "events.jsonl")
    rules = FundedAccountRules(
        initial_balance=100_000,
        profit_target_pct=0.10,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
    )
    risk = AccountAwareRiskEngine(clock=clock, rules=rules)
    execution = create_practice_adapter(clock, order_client=None, max_risk=200.0)

    session = PaperRealtimeSession(
        connector=connector,
        bus=bus,
        strategy=strategy,
        risk=risk,
        execution=execution,
        recorder=recorder,
        clock=clock,
        intent_factory=bridge,
    )

    result = asyncio.run(session.run())

    # La sesión procesó 1 snapshot + 5 barras, emitió señal, aprobó riesgo, generó intent y ejecutó fill
    assert result.metrics.connector_events == 6
    assert result.metrics.signals_generated == 1
    assert result.metrics.risk_approvals == 1
    assert result.metrics.orders_submitted == 1
    assert result.metrics.execution_reports == 1

    # Verificar que el OrderIntent generado tiene precios y dpp
    intent = result.intents[0]
    assert intent.entry_price == 111.0
    assert intent.stop_price < intent.entry_price < intent.target_price
    assert intent.dollars_per_point == 2.0

    # Verificar que el execution report fue exitoso
    report = result.reports[0]
    assert report.status == EXEC_FILLED
    assert execution.open_positions[MNQ_SYMBOL] == 1


# ==============================================================================
# 11. F2 Regresiones obligatorias de redondeo conservador de entry (LONG y SHORT)
# ==============================================================================

def test_conservative_entry_rounding_regressions_long_and_short() -> None:
    """Verifica los dos casos exactos encontrados por Muse donde el redondeo previo subestimaba el riesgo.

    1. LONG:
       - raw_entry=100.37, raw_stop=100.00, raw_target=101.00 (raw_risk = 0.37 pts).
       - Política anterior (nearest-tick): entry quedaba en 100.25 -> riesgo bajaba a 0.25 pts (< 0.37).
       - Política conservadora F2 (ceil): entry queda en 100.50 -> riesgo es 0.50 pts (>= 0.37).
    2. SHORT:
       - raw_target=99.00, raw_entry=99.63, raw_stop=100.00 (raw_risk = 0.37 pts).
       - Política anterior (nearest-tick): entry quedaba en 99.75 -> riesgo bajaba a 0.25 pts (< 0.37).
       - Política conservadora F2 (floor): entry queda en 99.50 -> riesgo es 0.50 pts (>= 0.37).
    """
    tick = 0.25

    # --- Caso 1: LONG exacto ---
    raw_entry_l, raw_stop_l, raw_target_l = 100.37, 100.00, 101.00
    raw_risk_l = raw_entry_l - raw_stop_l  # 0.37

    # Demostración del fallo con política anterior (nearest-tick para entry)
    old_entry_l = round(round(raw_entry_l / tick) * tick, 4)  # 100.25
    old_stop_l = round(math.floor(raw_stop_l / tick) * tick, 4)  # 100.00
    old_risk_l = old_entry_l - old_stop_l  # 0.25
    assert old_risk_l < raw_risk_l, "La política anterior subestimaba el riesgo de 0.37 a 0.25 pts"

    # Redondeo conservador F2
    entry_l, stop_l, target_l = round_mnq_prices_conservative(
        SIGNAL_LONG, raw_entry_l, raw_stop_l, raw_target_l, tick_size=tick
    )
    rounded_risk_l = entry_l - stop_l
    assert entry_l == 100.50
    assert stop_l == 100.00
    assert target_l == 101.00
    assert rounded_risk_l >= raw_risk_l, f"El riesgo redondeado ({rounded_risk_l}) debe ser >= crudo ({raw_risk_l})"
    assert stop_l < entry_l < target_l, "Polaridad LONG válida: stop < entry < target"

    # --- Caso 2: SHORT exacto ---
    raw_target_s, raw_entry_s, raw_stop_s = 99.00, 99.63, 100.00
    raw_risk_s = raw_stop_s - raw_entry_s  # 0.37

    # Demostración del fallo con política anterior (nearest-tick para entry)
    old_entry_s = round(round(raw_entry_s / tick) * tick, 4)  # 99.75
    old_stop_s = round(math.ceil(raw_stop_s / tick) * tick, 4)  # 100.00
    old_risk_s = old_stop_s - old_entry_s  # 0.25
    assert old_risk_s < raw_risk_s, "La política anterior subestimaba el riesgo de 0.37 a 0.25 pts"

    # Redondeo conservador F2
    entry_s, stop_s, target_s = round_mnq_prices_conservative(
        SIGNAL_SHORT, raw_entry_s, raw_stop_s, raw_target_s, tick_size=tick
    )
    rounded_risk_s = stop_s - entry_s
    assert entry_s == 99.50
    assert stop_s == 100.00
    assert target_s == 99.00
    assert rounded_risk_s >= raw_risk_s, f"El riesgo redondeado ({rounded_risk_s}) debe ser >= crudo ({raw_risk_s})"
    assert target_s < entry_s < stop_s, "Polaridad SHORT válida: target < entry < stop"


# ==============================================================================
# 12. F2 self._consumed.add(key) ocurre solo tras require_order_intent exitoso
# ==============================================================================

def test_consumed_only_after_require_order_intent_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica que el contexto no se marque como consumido si la validación falla."""
    import src.realtime.priced_intents as pi_mod
    clock = FrozenClock(TEST_TIME)
    bridge = MnqPricedIntentBridge()
    bt_sig = BacktestSignal("long", 100.0, 95.0, 105.0)
    sig = RealtimeSignal("sig-cons-test", "strat", clock.now(), 1, MNQ_SYMBOL, SIGNAL_LONG, origin=ORIGIN_LIVE)
    dec = RiskDecision("dec-cons-test", "risk", clock.now(), 1, "sig-cons-test", "strat", approved=True, reason=REASON_APPROVED, origin=ORIGIN_LIVE)

    key = (sig.source, sig.event_id)
    bridge.register_context(sig, bt_sig, size=1)

    # Simular fallo en require_order_intent
    def boom(intent: Any) -> Any:
        raise RuntimeError("Validation explosion")

    monkeypatch.setattr(pi_mod, "require_order_intent", boom)

    with pytest.raises(RuntimeError, match="Validation explosion"):
        bridge.create_intent(sig, dec, 1, clock)

    # El contexto NO debe haber sido consumido
    assert key not in bridge._consumed

    # Restaurar require_order_intent real y verificar que ahora sí se puede consumir
    monkeypatch.undo()
    intent = bridge.create_intent(sig, dec, 1, clock)
    assert intent is not None
    assert key in bridge._consumed

    # Un segundo intento ya debe fallar por haber sido consumido
    with pytest.raises(ValueError, match="already been consumed"):
        bridge.create_intent(sig, dec, 2, clock)
