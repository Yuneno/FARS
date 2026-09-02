"""RT-0 event contract tests."""

from datetime import datetime, timezone

import pytest

from src.realtime.events import (
    ORIGIN_REPLAY,
    SIGNAL_LONG,
    AccountSnapshot,
    Bar,
    ExecutionReport,
    MarketTick,
    MarketTrade,
    OrderIntent,
    Quote,
    RiskDecision,
    Signal,
    SystemEvent,
    identity_key,
    is_authorized,
    stream_key,
)
from src.types import Trade

TS = datetime(2024, 1, 2, 15, 30, tzinfo=timezone.utc)


def _tick(**overrides):
    values = {
        "event_id": "t-1",
        "source": "replay",
        "timestamp": TS,
        "sequence": 1,
        "symbol": "MNQ",
        "price": 18000.25,
        "volume": 1.0,
    }
    values.update(overrides)
    return MarketTick(**values)


def test_tick_requires_aware_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        _tick(timestamp=datetime(2024, 1, 2, 15, 30))


def test_empty_event_id_and_source_rejected():
    with pytest.raises(ValueError, match="event_id"):
        _tick(event_id="")
    with pytest.raises(ValueError, match="event_id"):
        _tick(event_id="   ")
    with pytest.raises(ValueError, match="source"):
        _tick(source="")


def test_invalid_symbol_is_empty_not_unfamiliar():
    with pytest.raises(ValueError, match="symbol"):
        _tick(symbol="")
    event = _tick(symbol="UNKNOWN_XYZ")
    assert event.symbol == "UNKNOWN_XYZ"


def test_negative_or_nonfinite_price_and_volume_rejected():
    with pytest.raises(ValueError, match="price"):
        _tick(price=-1.0)
    with pytest.raises(ValueError, match="price"):
        _tick(price=0.0)
    with pytest.raises(ValueError, match="price"):
        _tick(price=float("nan"))
    with pytest.raises(ValueError, match="volume"):
        _tick(volume=-0.01)
    with pytest.raises(ValueError, match="volume"):
        _tick(volume=float("inf"))


def test_sequence_rejects_bool_and_negative():
    with pytest.raises(ValueError, match="sequence"):
        _tick(sequence=True)
    with pytest.raises(ValueError, match="sequence"):
        _tick(sequence=-1)


def test_events_are_immutable():
    event = _tick()
    with pytest.raises(AttributeError):
        event.price = 1.0  # type: ignore[misc]


def test_live_and_replay_share_the_same_contract():
    live = _tick(origin="live", event_id="a")
    replay = _tick(origin=ORIGIN_REPLAY, event_id="b")
    assert type(live) is type(replay)
    assert live.origin == "live"
    assert replay.origin == "replay"


def test_invalid_origin_rejected():
    with pytest.raises(ValueError, match="origin"):
        _tick(origin="paper")


def test_identity_and_stream_keys():
    event = _tick(event_id="abc", source="feed-a", sequence=9)
    assert identity_key(event) == ("feed-a", "abc")
    assert stream_key(event) == ("feed-a", 9)


def test_market_trade_is_not_core_trade():
    print_event = MarketTrade(
        event_id="p1",
        source="replay",
        timestamp=TS,
        sequence=1,
        symbol="ES",
        price=5000.0,
        size=2.0,
    )
    core = Trade(r_result=1.0, trade_id="p1")
    assert type(print_event) is not type(core)
    assert not hasattr(print_event, "r_result")


def test_quote_allows_crossed_market():
    quote = Quote(
        event_id="q1",
        source="replay",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        bid_price=10.0,
        ask_price=9.5,
        bid_size=1.0,
        ask_size=1.0,
    )
    assert quote.bid_price > quote.ask_price


def test_bar_rejects_inconsistent_ohlc():
    kwargs = dict(
        event_id="b1",
        source="replay",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        interval="1m",
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=3.0,
    )
    Bar(**kwargs)
    with pytest.raises(ValueError, match="high"):
        Bar(**{**kwargs, "high": 9.5})


def test_account_snapshot_freezes_positions_and_optional_money():
    snap = AccountSnapshot(
        event_id="s1",
        source="broker",
        timestamp=TS,
        sequence=4,
        positions=[("MNQ", 1)],
        working_orders=[],
        equity=25000.0,
        broker_timestamp=TS,
    )
    assert snap.positions == (("MNQ", 1),)
    assert snap.working_orders == ()
    with pytest.raises(AttributeError):
        snap.equity = 1.0  # type: ignore[misc]
    with pytest.raises(ValueError, match="timezone-aware"):
        AccountSnapshot(
            event_id="s2",
            source="broker",
            timestamp=TS,
            sequence=5,
            last_sync=datetime(2024, 1, 2),
        )
    for invalid in (-1, 1.5, True):
        with pytest.raises(ValueError, match="trades_applied"):
            AccountSnapshot(
                event_id=f"invalid-{invalid}",
                source="broker",
                timestamp=TS,
                sequence=6,
                trades_applied=invalid,  # type: ignore[arg-type]
            )


def test_signal_is_not_an_order_and_rejects_invalid_action():
    signal = Signal(
        event_id="sig1",
        source="strategy",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        action=SIGNAL_LONG,
    )
    assert not hasattr(signal, "risk_decision_id")
    with pytest.raises(ValueError, match="action"):
        Signal(
            event_id="sig2",
            source="strategy",
            timestamp=TS,
            sequence=2,
            symbol="MNQ",
            action="BUY",
        )


def test_risk_decision_requires_explicit_bool_approval():
    ok = RiskDecision(
        event_id="rd1",
        source="risk",
        timestamp=TS,
        sequence=1,
        signal_id="sig1",
        signal_source="strategy",
        approved=True,
        reason="WITHIN_LIMITS",
    )
    assert ok.approved is True
    with pytest.raises(ValueError, match="explicit bool"):
        RiskDecision(
            event_id="rd2",
            source="risk",
            timestamp=TS,
            sequence=2,
            signal_id="sig1",
            signal_source="strategy",
            approved=1,  # type: ignore[arg-type]
            reason="NOPE",
        )
    with pytest.raises(ValueError, match="reason"):
        RiskDecision(
            event_id="rd3",
            source="risk",
            timestamp=TS,
            sequence=3,
            signal_id="sig1",
            signal_source="strategy",
            approved=False,
            reason="",
        )


def test_is_authorized_is_fail_closed():
    denied = RiskDecision(
        event_id="rd4",
        source="risk",
        timestamp=TS,
        sequence=4,
        signal_id="sig1",
        signal_source="strategy",
        approved=False,
        reason="DRAWDOWN_BUFFER_TOO_LOW",
    )
    approved = RiskDecision(
        event_id="rd5",
        source="risk",
        timestamp=TS,
        sequence=5,
        signal_id="sig1",
        signal_source="strategy",
        approved=True,
        reason="OK",
    )
    assert is_authorized(None) is False
    assert is_authorized("True") is False
    assert is_authorized(denied) is False
    assert is_authorized(approved) is True
    assert is_authorized(approved) is (approved.approved is True)


def test_order_intent_and_execution_report_link_ids():
    intent = OrderIntent(
        event_id="oi1",
        source="risk-gate",
        timestamp=TS,
        sequence=1,
        symbol="MNQ",
        action=SIGNAL_LONG,
        risk_decision_id="rd5",
    )
    report = ExecutionReport(
        event_id="er1",
        source="paper",
        timestamp=TS,
        sequence=1,
        order_intent_id=intent.event_id,
        status="accepted",
    )
    assert report.order_intent_id == "oi1"
    with pytest.raises(ValueError, match="status"):
        ExecutionReport(
            event_id="er2",
            source="paper",
            timestamp=TS,
            sequence=2,
            order_intent_id="oi1",
            status="done",
        )


def test_system_event_kinds():
    halted = SystemEvent(
        event_id="sys1",
        source="runtime",
        timestamp=TS,
        sequence=1,
        kind="system_halted",
        detail="gap",
    )
    assert halted.kind == "system_halted"
    with pytest.raises(ValueError, match="kind"):
        SystemEvent(
            event_id="sys2",
            source="runtime",
            timestamp=TS,
            sequence=2,
            kind="all_good",
        )
