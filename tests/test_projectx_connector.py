"""Deterministic tests for the read-only ProjectX boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.error import HTTPError

import pytest

from src.realtime.config import ProjectXCredentials
from src.realtime.connectors.projectx import (
    ProjectXAuthenticationError,
    ProjectXClient,
    ProjectXResponseError,
    UrllibJsonTransport,
)


class FakeTransport:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url, headers, payload, timeout):
        self.calls.append(
            {"url": url, "headers": dict(headers), "payload": dict(payload), "timeout": timeout}
        )
        return self.responses.pop(0)


def _credentials() -> ProjectXCredentials:
    return ProjectXCredentials("test-user", "super-secret", "TEST-CHALLENGE")


def _authenticated_client(*responses: Mapping[str, Any]):
    transport = FakeTransport(
        [{"success": True, "errorCode": 0, "token": "session-token"}, *responses]
    )
    client = ProjectXClient(_credentials(), transport=transport)
    client.authenticate()
    return client, transport


def test_credentials_repr_never_contains_api_key():
    assert "super-secret" not in repr(_credentials())


def test_authentication_sends_key_only_to_login_and_stores_token():
    client, transport = _authenticated_client()

    assert client.authenticated
    assert transport.calls[0]["url"].endswith("/api/Auth/loginKey")
    assert transport.calls[0]["payload"] == {
        "userName": "test-user",
        "apiKey": "super-secret",
    }
    assert "Authorization" not in transport.calls[0]["headers"]
    assert client.execution_allowed is False
    assert not hasattr(client, "place_order")


def test_data_request_requires_authentication():
    client = ProjectXClient(_credentials(), transport=FakeTransport([]))
    with pytest.raises(ProjectXAuthenticationError):
        client.list_accounts()


def test_validate_session_rotates_token_for_future_requests():
    client, transport = _authenticated_client(
        {"success": True, "errorCode": 0, "newToken": "rotated-token"},
        {"success": True, "errorCode": 0, "accounts": []},
    )
    client.validate_session()
    assert client.list_accounts() == ()
    assert transport.calls[-1]["headers"]["Authorization"] == "Bearer rotated-token"


def test_accounts_are_strictly_parsed_and_selected_by_exact_name():
    client, transport = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "accounts": [
                {
                    "id": 123456,
                    "name": "TEST-CHALLENGE",
                    "balance": 0,
                    "canTrade": True,
                    "isVisible": True,
                    "simulated": True,
                }
            ],
        }
    )

    accounts = client.list_accounts()
    selected = client.select_account(accounts)

    assert selected.account_id == 123456
    assert selected.balance == Decimal(0)
    assert selected.simulated is True
    assert transport.calls[1]["headers"]["Authorization"] == "Bearer session-token"
    assert transport.calls[1]["payload"] == {"onlyActiveAccounts": True}


def test_ambiguous_account_selection_fails_closed():
    client, _ = _authenticated_client()
    accounts = (
        type("Account", (), {"name": "A"})(),
        type("Account", (), {"name": "B"})(),
    )
    with pytest.raises(ProjectXResponseError, match="exactly one"):
        client.select_account(accounts, account_name="missing")


def test_contract_and_bar_values_use_decimal_not_binary_float():
    client, transport = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "contracts": [
                {
                    "id": "CON.F.US.MNQ.Z26",
                    "name": "MNQZ6",
                    "description": "Micro E-mini Nasdaq-100",
                    "tickSize": 0.25,
                    "tickValue": 0.5,
                    "activeContract": True,
                    "symbolId": "F.US.MNQ",
                }
            ],
        },
        {
            "success": True,
            "errorCode": 0,
            "bars": [
                {
                    "t": "2026-09-02T14:30:00Z",
                    "o": 23000.25,
                    "h": 23001.0,
                    "l": 22999.75,
                    "c": 23000.5,
                    "v": 12,
                }
            ],
        },
    )

    contract = client.search_contracts("MNQ")[0]
    bars = client.retrieve_bars(
        contract.contract_id,
        start=datetime(2026, 9, 2, 14, tzinfo=UTC),
        end=datetime(2026, 9, 2, 15, tzinfo=UTC),
        limit=60,
    )

    assert contract.tick_size == Decimal("0.25")
    assert contract.tick_value == Decimal("0.5")
    assert bars[0].close == Decimal("23000.5")
    assert transport.calls[-1]["payload"]["contractId"] == "CON.F.US.MNQ.Z26"
    assert transport.calls[-1]["payload"]["includePartialBar"] is False


def test_bar_request_rejects_naive_dates_and_excessive_limits():
    client, _ = _authenticated_client()
    aware = datetime(2026, 9, 2, tzinfo=UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        client.retrieve_bars(
            "CON.X", start=datetime(2026, 9, 1), end=aware, limit=1  # noqa: DTZ001
        )
    with pytest.raises(ValueError, match="between 1 and 20000"):
        client.retrieve_bars(
            "CON.X", start=aware, end=datetime(2026, 9, 3, tzinfo=UTC), limit=20001
        )


def test_provider_failure_does_not_expose_credentials():
    transport = FakeTransport(
        [{"success": False, "errorCode": 1, "errorMessage": "invalid credentials"}]
    )
    client = ProjectXClient(_credentials(), transport=transport)
    with pytest.raises(ProjectXAuthenticationError) as captured:
        client.authenticate()
    assert "super-secret" not in str(captured.value)


def test_missing_balance_is_not_invented_as_zero():
    client, _ = _authenticated_client(
        {
            "success": True,
            "errorCode": 0,
            "accounts": [
                {"id": 1, "name": "A", "canTrade": True, "isVisible": True}
            ],
        }
    )
    with pytest.raises(ProjectXResponseError, match="account.balance"):
        client.list_accounts()


def test_real_http_transport_serializes_json_without_logging_secret(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"success":true,"errorCode":0,"token":"jwt"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("src.realtime.connectors.projectx.urlopen", fake_urlopen)
    client = ProjectXClient(_credentials(), transport=UrllibJsonTransport())
    client.authenticate()

    decoded = json.loads(captured["body"])
    assert captured["url"] == "https://api.topstepx.com/api/Auth/loginKey"
    assert decoded == {"userName": "test-user", "apiKey": "super-secret"}
    assert captured["timeout"] == 10.0


def test_real_http_transport_maps_401_to_authentication_error(monkeypatch):
    def rejected(*args, **kwargs):
        raise HTTPError("https://api.topstepx.com", 401, "Unauthorized", {}, BytesIO())

    monkeypatch.setattr("src.realtime.connectors.projectx.urlopen", rejected)
    client = ProjectXClient(_credentials(), transport=UrllibJsonTransport())
    with pytest.raises(ProjectXAuthenticationError, match="HTTP 401"):
        client.authenticate()
