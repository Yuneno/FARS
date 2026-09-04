"""Deterministic tests for the read-only ProjectX Market Hub mapper."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest

from src.realtime.clock import FrozenClock
from src.realtime.config import ProjectXConfigurationError
from src.realtime.connectors.projectx import (
    ProjectXAuthenticationError,
    ProjectXContract,
    ProjectXError,
    ProjectXResponseError,
)
from src.realtime.connectors.projectx_signalr import (
    DEFAULT_MARKET_HUB,
    RECORD_SEPARATOR,
    SUBSCRIBE_QUOTES,
    SUBSCRIBE_TRADES,
    StreamSequencer,
    encode_signalr,
    handshake_frames,
    map_hub_message,
    parse_signalr_frame,
    select_active_contract,
    select_mnq_contract,
    split_signalr_frames,
    subscribe_frames,
)
from src.realtime.events import MarketTick, MarketTrade, Quote
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.listen import _build_parser, _duration_seconds, capture_until, run_listen
from src.realtime.recorder import reconstruct_events


def _contract(contract_id: str, name: str, *, active: bool = True) -> ProjectXContract:
    return ProjectXContract(
        contract_id,
        name,
        "Micro E-mini Nasdaq-100",
        Decimal("0.25"),
        Decimal("0.50"),
        active,
        "F.US.MNQ",
    )


def test_live_execution_stays_locked():
    assert LIVE_EXECUTION_ENABLED is False


def test_signalr_frames_round_trip():
    encoded = encode_signalr({"protocol": "json", "version": 1})
    frames, rest = split_signalr_frames(encoded + "{\"type\":6}")
    assert frames == ('{"protocol":"json","version":1}',)
    assert rest == '{"type":6}'
    assert parse_signalr_frame(frames[0])["protocol"] == "json"


def test_select_mnq_rejects_ambiguous_and_inactive():
    with pytest.raises(ProjectXResponseError, match="no active MNQ"):
        select_mnq_contract((_contract("CON.TEST.MNQ.Z99", "MNQ", active=False),))
    with pytest.raises(ProjectXResponseError, match="ambiguous"):
        select_mnq_contract(
            (
                _contract("CON.TEST.MNQ.U99", "MNQU9"),
                _contract("CON.TEST.MNQ.Z99", "MNQZ9"),
            )
        )
    chosen = select_mnq_contract(
        (
            _contract("CON.TEST.NQ.Z99", "NQZ9"),
            _contract("CON.TEST.MNQ.Z99", "MNQ"),
        )
    )
    assert chosen.contract_id == "CON.TEST.MNQ.Z99"


def test_quote_and_trade_map_to_canonical_events():
    sequencer = StreamSequencer("CON.TEST.MNQ.Z99")
    quotes = map_hub_message(
        {
            "type": 1,
            "target": "GatewayQuote",
            "arguments": [
                {
                    "timestamp": "2026-09-03T21:00:00Z",
                    "bestBid": "23000.25",
                    "bestAsk": "23000.50",
                    "bestBidSize": "3",
                    "bestAskSize": "4",
                }
            ],
        },
        contract_id="CON.TEST.MNQ.Z99",
        sequencer=sequencer,
    )
    trades = map_hub_message(
        {
            "type": 1,
            "target": "GatewayTrade",
            "arguments": [
                {
                    "id": 88,
                    "timestamp": "2026-09-03T21:00:01Z",
                    "price": "23000.50",
                    "size": "2",
                }
            ],
        },
        contract_id="CON.TEST.MNQ.Z99",
        sequencer=sequencer,
    )
    assert len(quotes) == 1 and isinstance(quotes[0], Quote)
    assert quotes[0].sequence == 1
    assert quotes[0].source == "projectx/CON.TEST.MNQ.Z99/quote"
    assert quotes[0].bid_price == 23000.25
    assert quotes[0].origin == "live"
    assert len(trades) == 1 and isinstance(trades[0], MarketTrade)
    assert trades[0].sequence == 1
    assert trades[0].source == "projectx/CON.TEST.MNQ.Z99/trade"
    assert trades[0].size == 2.0


def test_incomplete_quote_is_skipped_not_filled():
    sequencer = StreamSequencer("CON.TEST.MNQ.Z99")
    events = map_hub_message(
        {
            "type": 1,
            "target": "GatewayQuote",
            "arguments": [{"timestamp": "2026-09-03T21:00:00Z", "bestBid": "1", "bestAsk": "2"}],
        },
        contract_id="CON.TEST.MNQ.Z99",
        sequencer=sequencer,
    )
    assert events == ()


def test_last_price_without_quote_becomes_tick_not_core_trade():
    sequencer = StreamSequencer("CON.TEST.MNQ.Z99")
    events = map_hub_message(
        {
            "type": 1,
            "target": "GatewayLast",
            "arguments": [
                {
                    "timestamp": "2026-09-03T21:00:00Z",
                    "lastPrice": "23000.25",
                    "volume": "10",
                }
            ],
        },
        contract_id="CON.TEST.MNQ.Z99",
        sequencer=sequencer,
    )
    assert len(events) == 1
    assert isinstance(events[0], MarketTick)
    assert events[0].volume == 10.0


def test_session_token_requires_auth():
    from src.realtime.config import ProjectXCredentials
    from src.realtime.connectors.projectx import ProjectXClient
    from tests.realtime.test_projectx_connector import FakeTransport

    client = ProjectXClient(
        ProjectXCredentials("test-user", "super-secret"),
        transport=FakeTransport([]),
    )
    with pytest.raises(ProjectXAuthenticationError):
        client.session_token()


class _FakeHub:
    def __init__(self, chunks: list[str], *, empty_error: BaseException | None = None) -> None:
        self.chunks = list(chunks)
        self.sent: list[str] = []
        self.closed = False
        self.empty_error = empty_error

    def send(self, data: str) -> None:
        self.sent.append(data)

    def recv(self) -> str:
        if not self.chunks:
            if self.empty_error is not None:
                raise self.empty_error
            raise TimeoutError()
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


def test_capture_records_quote_and_skips_duplicates(tmp_path: Path, monkeypatch):
    from src.realtime import listen as listen_mod
    from src.realtime.recorder import FileEventRecorder

    quote = {
        "type": 1,
        "target": "GatewayQuote",
        "arguments": [
            {
                "timestamp": "2026-09-03T21:00:00Z",
                "bestBid": "23000.25",
                "bestAsk": "23000.50",
                "bestBidSize": "1",
                "bestAskSize": "1",
            }
        ],
    }
    frame = json.dumps(quote, separators=(",", ":")) + RECORD_SEPARATOR
    handshake = handshake_frames()
    hub = _FakeHub(["{}" + RECORD_SEPARATOR, frame, frame])

    monkeypatch.setattr(
        listen_mod,
        "negotiate_market_hub",
        lambda token, hub_url, timeout: {"connectionToken": "conn-token"},
    )
    monkeypatch.setattr(
        listen_mod,
        "market_hub_socket_url",
        lambda hub_url, negotiate, token: "wss://example.test/hubs/market",
    )

    journal = tmp_path / "journal.jsonl"
    stats = capture_until(
        token="session-token",
        contract_id="CON.TEST.MNQ.Z99",
        recorder=FileEventRecorder(journal),
        deadline_monotonic=__import__("time").monotonic() + 0.4,
        hub_url="https://example.test/hubs/market",
        timeout=1.0,
        log=StringIO(),
        socket_factory=lambda url, headers, timeout: hub,
        clock=FrozenClock(datetime(2026, 9, 3, 21, tzinfo=UTC)),
    )
    events = reconstruct_events(journal)
    quotes = [event for event in events if isinstance(event, Quote)]
    assert len(quotes) == 1
    assert stats.duplicates == 1
    assert stats.conflicts == 0
    assert handshake in "".join(hub.sent)
    assert any(SUBSCRIBE in item for item in hub.sent for SUBSCRIBE in subscribe_frames("CON.TEST.MNQ.Z99"))
    assert "session-token" not in journal.read_text(encoding="utf-8")


class _StubClient:
    execution_allowed = False

    def __init__(self, credentials):
        self.credentials = credentials
        self._token = None

    def authenticate(self):
        self._token = "session-token"

    def validate_session(self):
        return None

    def session_token(self):
        return self._token

    def search_contracts(self, search_text, *, live=False):
        assert live is False
        token = search_text.strip().upper()
        return (_contract(f"CON.TEST.{token}.Z99", token),)


def test_run_listen_writes_meta_without_secrets(tmp_path: Path, monkeypatch):
    from src.realtime import listen as listen_mod
    from src.realtime.listen import _build_parser

    monkeypatch.setenv("FARS_PROJECTX_USERNAME", "test-user")
    monkeypatch.setenv("FARS_PROJECTX_API_KEY", "never-print-this-secret")
    monkeypatch.setattr(
        listen_mod,
        "capture_until",
        lambda **kwargs: listen_mod._CaptureState(),
    )
    args = _build_parser().parse_args(
        [
            "--seconds",
            "1",
            "--journal",
            str(tmp_path / "journal.jsonl"),
            "--meta",
            str(tmp_path / "meta.json"),
            "--report",
            str(tmp_path / "report.json"),
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    log = StringIO()
    code = run_listen(args, client_factory=_StubClient, log=log)
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    combined = log.getvalue() + json.dumps(meta) + json.dumps(report)
    assert code == 0
    assert meta["live_execution_enabled"] is False
    assert meta["user_hub"] is False
    assert meta["contract_id"] == "CON.TEST.MNQ.Z99"
    assert meta["equity"] is None
    assert report["replay"]["ok"] is True
    assert "never-print-this-secret" not in combined
    assert "session-token" not in combined


def test_console_script_is_registered():
    import tomllib

    data = tomllib.loads(
        Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text(encoding="utf-8")
    )
    assert data["project"]["scripts"]["fars-projectx-listen"] == "src.realtime.listen:main"
    assert data["project"]["scripts"]["fars-projectx"] == "src.realtime.cli:main"


def test_listen_parser_rejects_non_positive_duration():
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "--hours",
                "0",
                "--journal",
                "journal.jsonl",
                "--meta",
                "meta.json",
                "--report",
                "report.json",
            ]
        )
    assert exc.value.code == 2


def test_duration_rejects_hours_and_seconds_together():
    args = _build_parser().parse_args(
        [
            "--hours",
            "1",
            "--seconds",
            "1",
            "--journal",
            "journal.jsonl",
            "--meta",
            "meta.json",
            "--report",
            "report.json",
        ]
    )
    with pytest.raises(ProjectXConfigurationError, match="not both"):
        _duration_seconds(args)


def test_duration_requires_hours_or_seconds():
    args = _build_parser().parse_args(
        [
            "--journal",
            "journal.jsonl",
            "--meta",
            "meta.json",
            "--report",
            "report.json",
        ]
    )
    with pytest.raises(ProjectXConfigurationError, match="--hours or --seconds"):
        _duration_seconds(args)


def test_select_active_contract_picks_unique_symbol_and_rejects_empty():
    nq = select_active_contract((_contract("CON.TEST.NQ.Z99", "NQ"),), "NQ")
    assert nq.contract_id == "CON.TEST.NQ.Z99"
    with pytest.raises(ProjectXResponseError, match="non-empty"):
        select_active_contract((_contract("CON.TEST.MNQ.Z99", "MNQ"),), "  ")


def test_select_active_contract_rejects_nq_substring_of_mnq():
    mnq = ProjectXContract(
        "CON.F.US.MNQ.Z26",
        "MNQZ6",
        "Micro E-mini Nasdaq-100",
        Decimal("0.25"),
        Decimal("0.50"),
        True,
        "F.US.MNQ",
    )
    with pytest.raises(ProjectXResponseError, match="no active NQ"):
        select_active_contract((mnq,), "NQ")
    mes = ProjectXContract(
        "CON.F.US.MES.Z26",
        "MESZ6",
        "Micro E-mini S&P",
        Decimal("0.25"),
        Decimal("0.50"),
        True,
        "F.US.MES",
    )
    with pytest.raises(ProjectXResponseError, match="no active ES"):
        select_active_contract((mes,), "ES")


def test_run_listen_accepts_unique_nq(tmp_path: Path, monkeypatch):
    from src.realtime import listen as listen_mod

    monkeypatch.setenv("FARS_PROJECTX_USERNAME", "test-user")
    monkeypatch.setenv("FARS_PROJECTX_API_KEY", "never-print-this-secret")
    monkeypatch.setattr(
        listen_mod,
        "capture_until",
        lambda **kwargs: listen_mod._CaptureState(),
    )
    args = _build_parser().parse_args(
        [
            "--seconds",
            "1",
            "--symbol",
            "NQ",
            "--journal",
            str(tmp_path / "journal.jsonl"),
            "--meta",
            str(tmp_path / "meta.json"),
            "--report",
            str(tmp_path / "report.json"),
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    code = run_listen(args, client_factory=_StubClient, log=StringIO())
    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert code == 0
    assert meta["symbol"] == "NQ"
    assert meta["contract_id"] == "CON.TEST.NQ.Z99"
    assert meta["user_hub"] is False
    assert meta["live_execution_enabled"] is False


class _ExecutableStub(_StubClient):
    execution_allowed = True


def test_run_listen_refuses_execution_allowed_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FARS_PROJECTX_USERNAME", "test-user")
    monkeypatch.setenv("FARS_PROJECTX_API_KEY", "never-print-this-secret")
    args = _build_parser().parse_args(
        [
            "--seconds",
            "1",
            "--journal",
            str(tmp_path / "journal.jsonl"),
            "--meta",
            str(tmp_path / "meta.json"),
            "--report",
            str(tmp_path / "report.json"),
            "--env-file",
            str(tmp_path / "missing.env"),
        ]
    )
    with pytest.raises(ProjectXError, match="allows execution"):
        run_listen(args, client_factory=_ExecutableStub, log=StringIO())


def test_subscribe_frames_are_market_hub_only():
    quotes, trades = subscribe_frames("CON.TEST.MNQ.Z99")
    combined = quotes + trades
    assert SUBSCRIBE_QUOTES in combined
    assert SUBSCRIBE_TRADES in combined
    assert "User" not in combined
    assert "Order" not in combined
    assert DEFAULT_MARKET_HUB.endswith("/hubs/market")
    assert "user" not in DEFAULT_MARKET_HUB.lower()


def test_capture_reconnect_records_system_events(tmp_path: Path, monkeypatch):
    from src.realtime import listen as listen_mod
    from src.realtime.events import SYSTEM_CONNECTOR_DISCONNECTED, SYSTEM_CONNECTOR_RECONNECTED
    from src.realtime.recorder import FileEventRecorder

    quote = {
        "type": 1,
        "target": "GatewayQuote",
        "arguments": [
            {
                "timestamp": "2026-09-03T21:00:00Z",
                "bestBid": "23000.25",
                "bestAsk": "23000.50",
                "bestBidSize": "1",
                "bestAskSize": "1",
            }
        ],
    }
    frame = json.dumps(quote, separators=(",", ":")) + RECORD_SEPARATOR
    stats = listen_mod._CaptureState()

    class _StopAfterEmpty(_FakeHub):
        def recv(self) -> str:
            if not self.chunks:
                stats.stop = True
                raise TimeoutError()
            return super().recv()

    first = _FakeHub(["{}" + RECORD_SEPARATOR], empty_error=ConnectionError("hub dropped"))
    second = _StopAfterEmpty(["{}" + RECORD_SEPARATOR, frame])
    sockets = [first, second]

    monkeypatch.setattr(
        listen_mod,
        "negotiate_market_hub",
        lambda token, hub_url, timeout: {"connectionToken": "conn-token"},
    )
    monkeypatch.setattr(
        listen_mod,
        "market_hub_socket_url",
        lambda hub_url, negotiate, token: "wss://example.test/hubs/market",
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    journal = tmp_path / "journal.jsonl"
    capture_until(
        token="session-token",
        contract_id="CON.TEST.MNQ.Z99",
        recorder=FileEventRecorder(journal),
        deadline_monotonic=__import__("time").monotonic() + 5.0,
        hub_url="https://example.test/hubs/market",
        timeout=1.0,
        log=StringIO(),
        socket_factory=lambda url, headers, timeout: sockets.pop(0),
        clock=FrozenClock(datetime(2026, 9, 3, 21, tzinfo=UTC)),
        stats=stats,
    )
    events = reconstruct_events(journal)
    kinds = [event.kind for event in events if hasattr(event, "kind")]
    assert stats.disconnects == 1
    assert stats.reconnects == 1
    assert SYSTEM_CONNECTOR_DISCONNECTED in kinds
    assert SYSTEM_CONNECTOR_RECONNECTED in kinds
    assert any(isinstance(event, Quote) for event in events)
    assert "session-token" not in journal.read_text(encoding="utf-8")


def test_capture_identity_conflict_halts_and_journals_system_event(tmp_path: Path, monkeypatch):
    from src.realtime import listen as listen_mod
    from src.realtime.events import ORIGIN_LIVE, SYSTEM_HALTED
    from src.realtime.recorder import FileEventRecorder

    stamp = datetime(2026, 9, 3, 21, tzinfo=UTC)
    shared = {
        "source": "projectx/CON.TEST.MNQ.Z99/quote",
        "timestamp": stamp,
        "sequence": 1,
        "symbol": "CON.TEST.MNQ.Z99",
        "bid_size": 1.0,
        "ask_size": 1.0,
        "origin": ORIGIN_LIVE,
    }
    first = Quote(event_id="quote-a", bid_price=1.0, ask_price=2.0, **shared)
    second = Quote(event_id="quote-b", bid_price=1.25, ask_price=2.25, **shared)

    monkeypatch.setattr(
        listen_mod,
        "map_hub_message",
        lambda message, **kwargs: (first, second) if message.get("type") in {1, 2} else (),
    )
    monkeypatch.setattr(
        listen_mod,
        "negotiate_market_hub",
        lambda token, hub_url, timeout: {"connectionToken": "conn-token"},
    )
    monkeypatch.setattr(
        listen_mod,
        "market_hub_socket_url",
        lambda hub_url, negotiate, token: "wss://example.test/hubs/market",
    )

    frame = json.dumps({"type": 1, "target": "GatewayQuote", "arguments": [{}]}) + RECORD_SEPARATOR
    hub = _FakeHub(["{}" + RECORD_SEPARATOR, frame])
    journal = tmp_path / "journal.jsonl"
    stats = capture_until(
        token="session-token",
        contract_id="CON.TEST.MNQ.Z99",
        recorder=FileEventRecorder(journal),
        deadline_monotonic=__import__("time").monotonic() + 2.0,
        hub_url="https://example.test/hubs/market",
        timeout=1.0,
        log=StringIO(),
        socket_factory=lambda url, headers, timeout: hub,
        clock=FrozenClock(stamp),
    )
    events = reconstruct_events(journal)
    kinds = [event.kind for event in events if hasattr(event, "kind")]
    assert stats.halted is True
    assert stats.conflicts == 1
    assert SYSTEM_HALTED in kinds
    assert "session-token" not in journal.read_text(encoding="utf-8")
