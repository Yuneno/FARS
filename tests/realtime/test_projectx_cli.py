"""Command-level tests for the read-only ProjectX terminal workflow."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from src.realtime import cli
from src.realtime.connectors.projectx import ProjectXAccount, ProjectXBar, ProjectXContract
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED


class StubClient:
    execution_allowed = False

    def __init__(self, credentials):
        self.credentials = credentials
        self.authenticated = False
        self.validated = False

    def authenticate(self):
        self.authenticated = True

    def validate_session(self):
        self.validated = True

    def list_accounts(self, *, only_active=True):
        assert only_active is True
        return (
            ProjectXAccount(
                1001,
                "TEST-ACCOUNT",
                Decimal(0),
                True,
                True,
                True,
            ),
        )

    def select_account(self, accounts, *, account_name=None):
        assert account_name in (None, accounts[0].name)
        return accounts[0]

    def list_open_positions(self, account_id):
        assert account_id == 1001
        return ()

    def search_contracts(self, search_text, *, live=False):
        assert search_text == "MNQ"
        assert live is False
        return (
            ProjectXContract(
                "CON.TEST.MNQ.Z99",
                "MNQZ9",
                "Micro E-mini Nasdaq-100 test",
                Decimal("0.25"),
                Decimal("0.50"),
                True,
                "F.US.MNQ",
            ),
        )

    def retrieve_bars(self, contract_id, **kwargs):
        assert contract_id == "CON.TEST.MNQ.Z99"
        assert kwargs["unit"] == 2
        assert kwargs["limit"] == 60
        return (
            ProjectXBar(
                datetime(2026, 9, 2, 13, tzinfo=UTC),
                Decimal("23000.00"),
                Decimal("23001.00"),
                Decimal("22999.75"),
                Decimal("23000.50"),
                Decimal(12),
            ),
        )


def _configure(monkeypatch):
    monkeypatch.setenv("FARS_PROJECTX_USERNAME", "test-user")
    monkeypatch.setenv("FARS_PROJECTX_API_KEY", "never-print-this-secret")
    monkeypatch.setattr(cli, "ProjectXClient", StubClient)


def test_doctor_exercises_safe_terminal_flow(monkeypatch, capsys):
    _configure(monkeypatch)

    exit_code = cli.main(["doctor", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_OK
    assert LIVE_EXECUTION_ENABLED is False
    assert payload["connection"] == "ok"
    assert payload["mode"] == "READ_ONLY"
    assert payload["execution_allowed"] is False
    assert payload["live_execution_enabled"] is False
    assert payload["selected_account"]["id"] == 1001
    assert payload["open_positions"] == 0
    assert "never-print-this-secret" not in captured.out + captured.err


def test_contract_search_command(monkeypatch, capsys):
    _configure(monkeypatch)

    exit_code = cli.main(["contracts", "MNQ", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload[0]["id"] == "CON.TEST.MNQ.Z99"
    assert payload[0]["tick_value"] == "0.50"


def test_bars_command(monkeypatch, capsys):
    _configure(monkeypatch)

    exit_code = cli.main(
        [
            "bars",
            "CON.TEST.MNQ.Z99",
            "--start",
            "2026-09-02T13:00:00Z",
            "--end",
            "2026-09-02T14:00:00Z",
            "--limit",
            "60",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload[0]["timestamp"] == "2026-09-02T13:00:00+00:00"
    assert payload[0]["close"] == "23000.50"
