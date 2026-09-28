"""RT-9 read-path retry hardening: deterministic offline tests (mock transport).

Covers the acceptance criteria without network access:
1. Read timeout -> retry -> success (final result arrives).
2. Persistent read timeouts -> error after exactly 3 attempts (no infinite loop).
3. Write-path timeout -> immediate error, zero retries.
4. Business/HTTP rejection on a read path -> never retried.
5. Default read timeout is 30 s; live execution stays locked.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import time

import pytest

from src.realtime.connectors.projectx import (
    JsonTransport,
    ProjectXClient,
    ProjectXCredentials,
    ProjectXError,
    ProjectXResponseError,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED
from src.realtime.orders.practice_client import (
    DEFAULT_PRACTICE_TIMEOUT,
    READ_RETRY_BACKOFF_SECONDS,
    READ_RETRY_MAX_ATTEMPTS,
    PracticeOrderClient,
)

TEST_ACCOUNT_NAME = "PRAC-V2-673085-85699223"
TEST_ACCOUNT_ID = 27765990


class ScriptedTransport(JsonTransport):
    """Mock transport: raises queued failures in order, then returns canned payloads."""

    def __init__(
        self,
        canned: dict[str, Any],
        failures: list[BaseException] | None = None,
    ) -> None:
        self.canned = canned
        self.failures = list(failures or [])
        self.calls: list[str] = []
        self.timeouts_seen: list[float] = []

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Mapping[str, Any]:
        endpoint = url.split("api.topstepx.com")[-1] if "api.topstepx.com" in url else url
        self.calls.append(endpoint)
        self.timeouts_seen.append(timeout)
        if self.failures:
            raise self.failures.pop(0)
        return self.canned[endpoint]


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff sleeps without waiting."""
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    return sleeps


def _make_client(transport: JsonTransport) -> PracticeOrderClient:
    return PracticeOrderClient(
        token_provider="test-token",
        transport=transport,
        practice_execution_enabled=True,
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )


def _read(client: PracticeOrderClient, path: str) -> Any:
    if path == "/api/Position/searchOpen":
        return client.search_open_positions(TEST_ACCOUNT_ID, account_name=TEST_ACCOUNT_NAME)
    return client.search_open_orders(TEST_ACCOUNT_ID, account_name=TEST_ACCOUNT_NAME)


@pytest.mark.parametrize("path", ["/api/Position/searchOpen", "/api/Order/searchOpen"])
def test_read_timeout_retries_then_succeeds(path: str, no_sleep: list[float]) -> None:
    transport = ScriptedTransport(
        canned={path: []},
        failures=[TimeoutError("The read operation timed out")],
    )
    result = _read(_make_client(transport), path)
    assert result == []
    assert transport.calls == [path, path]
    assert no_sleep == [READ_RETRY_BACKOFF_SECONDS[0]]
    assert all(t == DEFAULT_PRACTICE_TIMEOUT for t in transport.timeouts_seen)


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("The read operation timed out"),
        ProjectXError("ProjectX request failed or timed out"),
    ],
    ids=["timeout-error", "projectx-timeout"],
)
def test_read_retry_exhausts_after_three_attempts(
    failure: BaseException, no_sleep: list[float]
) -> None:
    transport = ScriptedTransport(
        canned={},
        failures=[failure, failure, failure],
    )
    with pytest.raises(type(failure)):
        _read(_make_client(transport), "/api/Order/searchOpen")
    assert transport.calls == ["/api/Order/searchOpen"] * READ_RETRY_MAX_ATTEMPTS
    assert READ_RETRY_MAX_ATTEMPTS == 3
    assert no_sleep == list(READ_RETRY_BACKOFF_SECONDS)
    assert list(READ_RETRY_BACKOFF_SECONDS) == [2.0, 4.0]


@pytest.mark.parametrize("op", ["cancel", "close"])
def test_write_timeout_never_retried(op: str, no_sleep: list[float]) -> None:
    transport = ScriptedTransport(
        canned={},
        failures=[TimeoutError("The read operation timed out")],
    )
    client = _make_client(transport)
    with pytest.raises(TimeoutError):
        if op == "cancel":
            client.cancel_order(
                account_id=TEST_ACCOUNT_ID, order_id=123, account_name=TEST_ACCOUNT_NAME
            )
        else:
            client.flatten_contract(
                TEST_ACCOUNT_ID, "CON_MNQ_202612", account_name=TEST_ACCOUNT_NAME
            )
    assert len(transport.calls) == 1
    assert no_sleep == []


@pytest.mark.parametrize(
    "error",
    [
        ProjectXError("ProjectX HTTP error 400"),
        ProjectXResponseError("ProjectX error 0: order rejected"),
    ],
    ids=["http-400", "business-rejection"],
)
def test_read_business_rejection_not_retried(error: ProjectXError, no_sleep: list[float]) -> None:
    transport = ScriptedTransport(canned={}, failures=[error])
    with pytest.raises(ProjectXError):
        _read(_make_client(transport), "/api/Order/searchOpen")
    assert len(transport.calls) == 1
    assert no_sleep == []


def test_default_timeout_is_thirty_seconds_and_live_locked() -> None:
    assert LIVE_EXECUTION_ENABLED is False
    assert DEFAULT_PRACTICE_TIMEOUT == 30.0
    client = PracticeOrderClient(
        token_provider="test-token",
        transport=ScriptedTransport(canned={}),
        account_allowlist=(TEST_ACCOUNT_NAME, TEST_ACCOUNT_ID),
    )
    assert client._timeout == 30.0
    px_client = ProjectXClient(
        ProjectXCredentials(username="u", api_key="k"),
        transport=ScriptedTransport(canned={}),
    )
    assert px_client._timeout == 30.0
