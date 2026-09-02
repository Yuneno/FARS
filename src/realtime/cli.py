"""Terminal entry point for the read-only ProjectX gateway."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from .config import ProjectXConfigurationError, load_projectx_credentials
from .connectors.projectx import ProjectXClient, ProjectXError

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_CONFIGURATION = 2
EXIT_PROVIDER = 3


def _aware_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use an ISO-8601 datetime with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fars-projectx",
        description="Read-only ProjectX/TopstepX connection for FARS RT-1.",
    )
    parser.add_argument("--env-file", default=".env", help="local credential file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="authenticate and verify the active account without trading"
    )
    doctor.add_argument("--account-name", default=None)
    doctor.add_argument("--json", action="store_true")

    contracts = subparsers.add_parser("contracts", help="search available contracts")
    contracts.add_argument("query", help="contract search text, for example MNQ")
    contracts.add_argument("--live", action="store_true", help="use a live data subscription")
    contracts.add_argument("--json", action="store_true")

    bars = subparsers.add_parser("bars", help="retrieve historical OHLCV bars")
    bars.add_argument("contract_id")
    bars.add_argument("--start", required=True, type=_aware_datetime)
    bars.add_argument("--end", required=True, type=_aware_datetime)
    bars.add_argument("--unit", type=int, choices=range(1, 7), default=2)
    bars.add_argument("--unit-number", type=int, default=1)
    bars.add_argument("--limit", type=int, default=1000)
    bars.add_argument("--include-partial-bar", action="store_true")
    bars.add_argument("--live", action="store_true", help="use a live data subscription")
    bars.add_argument("--json", action="store_true")
    return parser


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, default=_json_value, indent=2, sort_keys=True))
        return
    if isinstance(payload, list):
        for row in payload:
            print(" | ".join(f"{key}={value}" for key, value in row.items()))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def _account_payload(account: Any) -> dict[str, Any]:
    return {
        "id": account.account_id,
        "name": account.name,
        "balance": account.balance,
        "can_trade": account.can_trade,
        "is_visible": account.is_visible,
        "simulated": account.simulated,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        credentials = load_projectx_credentials(args.env_file)
        client = ProjectXClient(credentials)
        client.authenticate()

        if args.command == "doctor":
            client.validate_session()
            accounts = client.list_accounts(only_active=True)
            selected = client.select_account(accounts, account_name=args.account_name)
            positions = client.list_open_positions(selected.account_id)
            payload = {
                "connection": "ok",
                "mode": "READ_ONLY",
                "execution_allowed": client.execution_allowed,
                "active_account_count": len(accounts),
                "selected_account": _account_payload(selected),
                "open_positions": len(positions),
            }
            _emit(payload, as_json=args.json)
        elif args.command == "contracts":
            rows = [
                {
                    "id": contract.contract_id,
                    "name": contract.name,
                    "description": contract.description,
                    "tick_size": contract.tick_size,
                    "tick_value": contract.tick_value,
                    "active": contract.active,
                }
                for contract in client.search_contracts(args.query, live=args.live)
            ]
            _emit(rows, as_json=args.json)
        elif args.command == "bars":
            rows = [
                {
                    "timestamp": bar.timestamp,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in client.retrieve_bars(
                    args.contract_id,
                    start=args.start,
                    end=args.end,
                    unit=args.unit,
                    unit_number=args.unit_number,
                    limit=args.limit,
                    include_partial_bar=args.include_partial_bar,
                    live=args.live,
                )
            ]
            _emit(rows, as_json=args.json)
        return EXIT_OK
    except ProjectXConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except ProjectXError as exc:
        print(f"provider error: {exc}", file=sys.stderr)
        return EXIT_PROVIDER
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - CLI safety boundary
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
