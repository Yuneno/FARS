"""Read-only two-hour Market Hub capture. Not RT-9 and not live execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit, urlunsplit

from src.realtime.bus import AsyncIOEventBus
from src.realtime.clock import FrozenClock, SystemClock
from src.realtime.config import ProjectXConfigurationError, load_projectx_credentials
from src.realtime.connectors.projectx import (
    ProjectXClient,
    ProjectXError,
    ProjectXResponseError,
)
from src.realtime.connectors.projectx_signalr import (
    DEFAULT_MARKET_HUB,
    RECORD_SEPARATOR,
    HubSocket,
    StreamSequencer,
    handshake_frames,
    map_hub_message,
    market_hub_socket_url,
    negotiate_market_hub,
    parse_signalr_frame,
    ping_frame,
    redact_secrets,
    select_mnq_contract,
    split_signalr_frames,
    subscribe_frames,
    system_event,
)
from src.realtime.events import (
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_HALTED,
    CanonicalEvent,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.ordering import OrderingClass, SequenceTracker, requires_halt
from src.realtime.recorder import FileEventRecorder, reconstruct_events
from src.realtime.replay import ReplayEngine

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_CONFIGURATION = 2
EXIT_PROVIDER = 3
PING_INTERVAL_SECONDS = 15.0
RECV_TIMEOUT_SECONDS = 5.0
RECONNECT_CAP_SECONDS = 15.0


class _Stop(Exception):
    """Internal loop stop."""


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fars-projectx-listen",
        description="Read-only ProjectX Market Hub capture for FARS.",
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--hours", type=_positive_int, default=None)
    parser.add_argument("--seconds", type=_positive_int, default=None)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--symbol", default="MNQ")
    parser.add_argument("--hub-url", default=DEFAULT_MARKET_HUB)
    parser.add_argument("--timeout", type=float, default=10.0)
    return parser


def _duration_seconds(args: argparse.Namespace) -> int:
    if args.seconds is not None and args.hours is not None:
        raise ProjectXConfigurationError("pass --hours or --seconds, not both")
    if args.seconds is not None:
        return args.seconds
    if args.hours is not None:
        return args.hours * 3600
    raise ProjectXConfigurationError("pass --hours or --seconds")


class _WebSocketHub:
    """Adapter so websocket-client matches HubSocket without a FARS dependency."""

    def __init__(self, sock: Any) -> None:
        self._sock = sock

    def send(self, data: str) -> None:
        self._sock.send(data)

    def recv(self) -> str:
        payload = self._sock.recv()
        if isinstance(payload, bytes):
            return payload.decode("utf-8")
        return str(payload)

    def close(self) -> None:
        self._sock.close()


def _open_websocket(url: str, headers: Mapping[str, str], timeout: float) -> HubSocket:
    try:
        import websocket
    except ImportError as exc:  # pragma: no cover - machine-specific
        raise ProjectXError(
            "Market Hub listen needs package 'websocket' on this machine; "
            "not adding it as a FARS dependency"
        ) from exc
    header = [f"{key}: {value}" for key, value in headers.items()]
    sock = websocket.create_connection(url, header=header, timeout=timeout)
    sock.settimeout(RECV_TIMEOUT_SECONDS)
    return _WebSocketHub(sock)


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _log(handle: TextIO, message: str, *secrets: str) -> None:
    stamp = datetime.now(UTC).isoformat()
    handle.write(redact_secrets(f"{stamp} {message}\n", *secrets))
    handle.flush()


class _CaptureState:
    def __init__(self) -> None:
        self.duplicates = 0
        self.gaps = 0
        self.late = 0
        self.conflicts = 0
        self.skipped = 0
        self.recorded = 0
        self.disconnects = 0
        self.reconnects = 0
        self.parse_errors = 0
        self.last_skip = ""
        self.stop = False
        self.halted = False


def _record_event(
    event: CanonicalEvent,
    *,
    recorder: FileEventRecorder,
    tracker: SequenceTracker,
    stats: _CaptureState,
) -> None:
    kind = tracker.classify(event)
    if kind is OrderingClass.DUPLICATE:
        stats.duplicates += 1
        return
    if requires_halt(kind):
        stats.conflicts += 1
        stats.halted = True
        raise _Stop()
    if kind is OrderingClass.SEQUENCE_GAP:
        stats.gaps += 1
    elif kind is OrderingClass.LATE:
        stats.late += 1
    recorder.record(event)
    stats.recorded += 1


def _connect_hub(
    token: str,
    *,
    hub_url: str,
    timeout: float,
    socket_factory,
) -> tuple[HubSocket, str]:
    negotiate = negotiate_market_hub(token, hub_url=hub_url, timeout=timeout)
    socket_url = market_hub_socket_url(hub_url, negotiate, token)
    headers = {"Authorization": f"Bearer {token}"}
    socket = socket_factory(socket_url, headers, timeout)
    return socket, socket_url


def _handshake_and_subscribe(socket: HubSocket, contract_id: str) -> str:
    socket.send(handshake_frames())
    buffer = ""
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        chunk = socket.recv()
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        buffer += chunk
        frames, buffer = split_signalr_frames(buffer)
        leftover: list[str] = []
        subscribed = False
        for frame in frames:
            if subscribed:
                leftover.append(frame)
                continue
            message = parse_signalr_frame(frame)
            if message.get("error"):
                raise ProjectXResponseError("Market Hub handshake failed")
            if message.get("type") is None and "error" not in message:
                quotes, trades = subscribe_frames(contract_id)
                socket.send(quotes)
                socket.send(trades)
                subscribed = True
        if subscribed:
            rest = RECORD_SEPARATOR.join(leftover)
            if leftover:
                rest += RECORD_SEPARATOR
            return rest + buffer
    raise ProjectXError("Market Hub handshake timed out")


def _drain_socket(
    socket: HubSocket,
    buffer: str,
    *,
    contract_id: str,
    sequencer: StreamSequencer,
    recorder: FileEventRecorder,
    tracker: SequenceTracker,
    stats: _CaptureState,
) -> str:
    chunk = socket.recv()
    if isinstance(chunk, bytes):
        chunk = chunk.decode("utf-8")
    buffer += chunk
    frames, buffer = split_signalr_frames(buffer)
    for frame in frames:
        try:
            message = parse_signalr_frame(frame)
        except ProjectXResponseError:
            stats.parse_errors += 1
            continue
        msg_type = message.get("type")
        if msg_type == 3 and message.get("error"):
            stats.skipped += 1
            stats.last_skip = (
                f"completion_error invocation={message.get('invocationId')!r}"
            )
            continue
        if msg_type == 6:
            socket.send(ping_frame())
            continue
        if msg_type == 7:
            raise ConnectionError("Market Hub closed")
        events = map_hub_message(
            message, contract_id=contract_id, sequencer=sequencer
        )
        if not events and msg_type in {1, 2}:
            stats.skipped += 1
            keys: list[str] = []
            arguments = message.get("arguments")
            if isinstance(arguments, list) and arguments and isinstance(arguments[0], dict):
                keys = sorted(str(key) for key in arguments[0])
            stats.last_skip = f"target={message.get('target')!r} keys={keys}"
        for event in events:
            _record_event(event, recorder=recorder, tracker=tracker, stats=stats)
    return buffer


def capture_until(
    *,
    token: str,
    contract_id: str,
    recorder: FileEventRecorder,
    deadline_monotonic: float,
    hub_url: str,
    timeout: float,
    log: TextIO,
    socket_factory=_open_websocket,
    clock=None,
    stats: _CaptureState | None = None,
) -> _CaptureState:
    clock = clock or SystemClock()
    sequencer = StreamSequencer(contract_id)
    tracker = SequenceTracker()
    if stats is None:
        stats = _CaptureState()
    secrets = (token,)
    backoff = 1.0
    connected = False

    def emit_system(kind: str, detail: str) -> None:
        event = system_event(
            sequencer,
            kind=kind,
            clock_now=clock.now(),
            detail=detail,
            symbol=contract_id,
        )
        _record_event(event, recorder=recorder, tracker=tracker, stats=stats)

    while time.monotonic() < deadline_monotonic and not stats.stop and not stats.halted:
        socket = None
        try:
            socket, socket_url = _connect_hub(
                token,
                hub_url=hub_url,
                timeout=timeout,
                socket_factory=socket_factory,
            )
            _log(log, f"hub socket {_safe_url(socket_url)}", *secrets)
            leftover = _handshake_and_subscribe(socket, contract_id)
            if connected:
                stats.reconnects += 1
                emit_system(SYSTEM_CONNECTOR_RECONNECTED, "market hub reconnected")
            connected = True
            backoff = 1.0
            buffer = leftover
            last_ping = time.monotonic()
            while time.monotonic() < deadline_monotonic and not stats.stop:
                try:
                    buffer = _drain_socket(
                        socket,
                        buffer,
                        contract_id=contract_id,
                        sequencer=sequencer,
                        recorder=recorder,
                        tracker=tracker,
                        stats=stats,
                    )
                except TimeoutError:
                    pass
                except Exception as exc:
                    if "timed out" in str(exc).lower() or exc.__class__.__name__.endswith(
                        "TimeoutException"
                    ):
                        pass
                    else:
                        raise
                if time.monotonic() - last_ping >= PING_INTERVAL_SECONDS:
                    socket.send(ping_frame())
                    last_ping = time.monotonic()
                    if stats.last_skip:
                        _log(log, f"hub skip {stats.last_skip}", *secrets)
                    _log(
                        log,
                        f"hub heartbeat recorded={stats.recorded} skipped={stats.skipped}",
                        *secrets,
                    )
        except _Stop:
            emit_system(SYSTEM_HALTED, "sequence conflict")
            break
        except Exception as exc:  # noqa: BLE001  # reconnect any hub/transport failure
            _log(log, f"hub disconnect: {type(exc).__name__}", *secrets)
            stats.disconnects += 1
            if connected:
                try:
                    emit_system(SYSTEM_CONNECTOR_DISCONNECTED, type(exc).__name__)
                except _Stop:
                    break
            connected = False
            if time.monotonic() >= deadline_monotonic or stats.stop:
                break
            time.sleep(min(backoff, RECONNECT_CAP_SECONDS))
            backoff = min(backoff * 2.0, RECONNECT_CAP_SECONDS)
        finally:
            if socket is not None:
                try:
                    socket.close()
                except OSError:
                    pass
    return stats


async def _verify_replay(journal: Path) -> dict[str, Any]:
    events = reconstruct_events(journal)
    if not events:
        return {
            "ok": True,
            "recorded": 0,
            "replayed": 0,
            "delivered": 0,
            "duplicates": 0,
            "gaps": 0,
            "late": 0,
            "conflicts": 0,
            "note": "empty journal",
        }
    bus = AsyncIOEventBus()
    delivered: list[CanonicalEvent] = []

    def _sub(event: CanonicalEvent) -> None:
        delivered.append(event)

    bus.subscribe(_sub)
    await bus.start()
    try:
        replayed = await ReplayEngine(
            journal, bus=bus, clock=FrozenClock(events[0].timestamp)
        ).play()
        await bus.wait_idle()
    finally:
        await bus.shutdown()
    return {
        "ok": replayed == len(events) and tuple(delivered) == events,
        "recorded": len(events),
        "replayed": replayed,
        "delivered": len(delivered),
        "duplicates": bus.duplicates,
        "gaps": bus.gaps,
        "late": bus.late,
        "conflicts": bus.conflicts,
    }


def run_listen(
    args: argparse.Namespace,
    *,
    socket_factory=_open_websocket,
    client_factory: Any = ProjectXClient,
    log: TextIO | None = None,
) -> int:
    if LIVE_EXECUTION_ENABLED is not False:
        raise ProjectXError("live execution is locked; listen stays read-only")
    duration = _duration_seconds(args)
    journal = Path(args.journal)
    meta_path = Path(args.meta)
    report_path = Path(args.report)
    log_handle = log or sys.stdout
    start = datetime.now(UTC)
    end = start + timedelta(seconds=duration)
    credentials = load_projectx_credentials(args.env_file)
    client = client_factory(credentials)
    if getattr(client, "execution_allowed", False) is not False:
        raise ProjectXError("listen refuses a client that allows execution")
    client.authenticate()
    client.validate_session()
    token = client.session_token()
    if args.symbol.strip().upper() != "MNQ":
        raise ProjectXConfigurationError("this capture is pinned to MNQ")
    contracts = client.search_contracts(args.symbol.strip(), live=False)
    contract = select_mnq_contract(contracts)
    meta = {
        "pid": os.getpid(),
        "mode": "READ_ONLY",
        "live_execution_enabled": False,
        "rt9": False,
        "user_hub": False,
        "market_hub": args.hub_url,
        "symbol": "MNQ",
        "contract_id": contract.contract_id,
        "contract_name": contract.name,
        "started_utc": start.isoformat(),
        "expected_end_utc": end.isoformat(),
        "duration_seconds": duration,
        "journal": str(journal),
        "equity": None,
        "stop": f"kill -TERM {os.getpid()}",
    }
    _write_json(meta_path, meta)
    _log(log_handle, f"listen start pid={os.getpid()} contract={contract.contract_id}", token)
    recorder = FileEventRecorder(journal)
    journal.touch(exist_ok=True)
    stats = _CaptureState()

    def _handle_stop(_signum, _frame) -> None:
        stats.stop = True

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    try:
        capture_until(
            token=token,
            contract_id=contract.contract_id,
            recorder=recorder,
            deadline_monotonic=time.monotonic() + duration,
            hub_url=args.hub_url,
            timeout=args.timeout,
            log=log_handle,
            socket_factory=socket_factory,
            stats=stats,
        )
    finally:
        finished = datetime.now(UTC)
        replay = asyncio.run(_verify_replay(journal))
        report = {
            "pid": os.getpid(),
            "started_utc": start.isoformat(),
            "finished_utc": finished.isoformat(),
            "expected_end_utc": end.isoformat(),
            "contract_id": contract.contract_id,
            "journal": str(journal),
            "recorded": stats.recorded,
            "duplicates": stats.duplicates,
            "gaps": stats.gaps,
            "late": stats.late,
            "conflicts": stats.conflicts,
            "skipped": stats.skipped,
            "last_skip": stats.last_skip,
            "disconnects": stats.disconnects,
            "reconnects": stats.reconnects,
            "parse_errors": stats.parse_errors,
            "halted": stats.halted,
            "replay": replay,
            "user_hub": False,
            "live_execution_enabled": False,
            "equity": None,
        }
        _write_json(report_path, report)
        _log(log_handle, f"listen end recorded={stats.recorded} replay_ok={replay.get('ok')}", token)
    if stats.halted or replay.get("ok") is not True:
        return EXIT_PROVIDER
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        return run_listen(args)
    except ProjectXConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except ProjectXError as exc:
        print(f"provider error: {exc}", file=sys.stderr)
        return EXIT_PROVIDER
    except Exception as exc:  # noqa: BLE001
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
