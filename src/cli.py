"""Phase 9A command-line interface for FARS.

Exposes the capabilities approved by the Phase 9A CLI contract: CSV audit
(Phase 8A) and core descriptive metrics. Bootstrap, Monte Carlo, and
optimization are not exposed here; Phase 10A requires a separate CLI contract.

Exit codes:
    0  operation completed successfully
    1  unexpected internal error
    2  invalid CLI arguments
    3  file, encoding, mapping, or CSV structurally invalid
    4  audit completed, but the data block the requested analysis
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Sequence

from .ingestion import (
    CanonicalTradeDataset,
    TradeDataError,
    load_trade_csv,
    validate_csv_delimiter,
)
from .metrics import Metrics, compute_metrics

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_STRUCTURAL = 3
EXIT_BLOCKED = 4

_METRIC_FIELDS = (
    "n_trades",
    "win_rate",
    "avg_win_r",
    "avg_loss_r",
    "expectancy_r",
    "std_r",
    "skewness",
    "kurtosis",
    "max_drawdown_r",
    "max_losing_streak",
)


def _delimiter(value: str) -> str:
    """Adapt the shared ingestion validator to argparse's type contract."""
    try:
        return validate_csv_delimiter(value)
    except TradeDataError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fars",
        description=(
            "FARS Phase 9A CLI: audit external trade CSVs and compute core "
            "descriptive metrics. No resampling or optimization is performed."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("audit", "load a trade CSV and report validation, capabilities, provenance"),
        ("metrics", "audit the CSV and compute core metrics if core_metrics is available"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("csv", help="path to the external trade CSV file")
        sub.add_argument(
            "--outcomes-finalized",
            action="store_true",
            required=True,
            help="attest that every r_result is a closed, final outcome (required)",
        )
        sub.add_argument(
            "--map",
            dest="mapping",
            action="append",
            default=[],
            metavar="SOURCE=CANONICAL",
            help="map a source column to a canonical field; repeatable",
        )
        sub.add_argument(
            "--timezone",
            default=None,
            metavar="IANA_NAME",
            help="explicit analysis timezone (e.g. America/New_York)",
        )
        sub.add_argument(
            "--delimiter",
            default=",",
            type=_delimiter,
            help="CSV delimiter, exactly one character (default ',')",
        )
        sub.add_argument(
            "--encoding", default="utf-8-sig", help="CSV encoding (default utf-8-sig)"
        )
        sub.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="output format (default text)",
        )
    return parser


def _parse_mapping(pairs: list[str], parser: argparse.ArgumentParser) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            parser.error(f"--map expects SOURCE=CANONICAL, got {pair!r}")
        source, _, canonical = pair.partition("=")
        if not source or not canonical:
            parser.error(f"--map expects non-empty SOURCE=CANONICAL, got {pair!r}")
        if source in mapping:
            parser.error(f"--map source column repeated: {source!r}")
        mapping[source] = canonical
    return mapping


def _finite_or_none(value: Any) -> Any:
    """Return None for non-finite floats so JSON never contains NaN."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _audit_payload(dataset: CanonicalTradeDataset) -> dict[str, Any]:
    report = dataset.audit
    provenance = dataset.provenance
    return {
        "source": dataset.source,
        "rows": {
            "total": report.total_rows,
            "accepted": report.accepted_rows,
            "rejected": report.rejected_rows,
        },
        "issue_counts": {
            "errors": report.error_count,
            "warnings": report.warning_count,
        },
        "issues": [
            {
                "severity": issue.severity,
                "code": issue.code,
                "message": issue.message,
                "row": issue.row_number,
                "field": issue.field,
            }
            for issue in report.issues
        ],
        "capabilities": {
            name: {
                "available": status.available,
                "reasons": list(status.reasons),
            }
            for name, status in dataset.capabilities.items()
        },
        "provenance": {
            "schema_version": provenance.schema_version,
            "source_path": provenance.source_path,
            "source_sha256": provenance.source_sha256,
            "source_size_bytes": provenance.source_size_bytes,
            "resolved_mapping": dict(provenance.resolved_mapping),
            "outcomes_finalized": provenance.outcomes_finalized,
            "analysis_timezone": provenance.analysis_timezone,
            "delimiter": provenance.delimiter,
            "encoding": provenance.encoding,
        },
    }


def _metrics_payload(metrics: Metrics) -> dict[str, Any]:
    return {name: _finite_or_none(getattr(metrics, name)) for name in _METRIC_FIELDS}


def _print_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, indent=2, allow_nan=False)
    sys.stdout.write("\n")


def _print_audit_text(dataset: CanonicalTradeDataset) -> None:
    report = dataset.audit
    provenance = dataset.provenance
    print(f"Source: {dataset.source}")
    print(
        f"Rows: total={report.total_rows} accepted={report.accepted_rows} "
        f"rejected={report.rejected_rows}"
    )
    print(f"Issues: {report.error_count} error(s), {report.warning_count} warning(s)")
    for issue in report.issues:
        location = ""
        if issue.row_number is not None:
            location += f" row={issue.row_number}"
        if issue.field is not None:
            location += f" field={issue.field}"
        print(f"  [{issue.severity}] {issue.code}{location}: {issue.message}")
    print("Capabilities:")
    for name, status in dataset.capabilities.items():
        if status.available:
            print(f"  {name}: available")
        else:
            print(f"  {name}: unavailable ({'; '.join(status.reasons)})")
    print("Provenance:")
    print(f"  schema_version: {provenance.schema_version}")
    print(f"  source_path: {provenance.source_path}")
    print(f"  source_sha256: {provenance.source_sha256}")
    print(f"  source_size_bytes: {provenance.source_size_bytes}")
    print(f"  resolved_mapping: {dict(provenance.resolved_mapping)}")
    print(f"  outcomes_finalized: {provenance.outcomes_finalized}")
    print(f"  analysis_timezone: {provenance.analysis_timezone}")
    print(f"  delimiter: {provenance.delimiter!r}")
    print(f"  encoding: {provenance.encoding}")


def _print_metrics_text(metrics: Metrics) -> None:
    labels = {
        "n_trades": "Trades",
        "win_rate": "Win rate",
        "avg_win_r": "Avg win (R)",
        "avg_loss_r": "Avg loss (R)",
        "expectancy_r": "Expectancy (R)",
        "std_r": "Std (R)",
        "skewness": "Skewness",
        "kurtosis": "Kurtosis",
        "max_drawdown_r": "Max drawdown (R)",
        "max_losing_streak": "Max losing streak",
    }
    for name in _METRIC_FIELDS:
        value = _finite_or_none(getattr(metrics, name))
        rendered = "n/a (statistically undefined)" if value is None else str(value)
        print(f"{labels[name]}: {rendered}")


def _emit(dataset: CanonicalTradeDataset, fmt: str) -> None:
    if fmt == "json":
        _print_json(_audit_payload(dataset))
    else:
        _print_audit_text(dataset)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    mapping = _parse_mapping(args.mapping, parser)

    try:
        dataset = load_trade_csv(
            args.csv,
            outcomes_finalized=args.outcomes_finalized,
            column_mapping=mapping or None,
            analysis_timezone=args.timezone,
            delimiter=args.delimiter,
            encoding=args.encoding,
        )
    except TradeDataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_STRUCTURAL
    except Exception as exc:  # noqa: BLE001 - unexpected internal failure
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    if args.command == "audit":
        _emit(dataset, args.format)
        return EXIT_OK

    capability = dataset.capabilities["core_metrics"]
    if not capability.available:
        _emit(dataset, args.format)
        print(
            "error: core_metrics is unavailable: " + "; ".join(capability.reasons),
            file=sys.stderr,
        )
        return EXIT_BLOCKED

    r_results = [trade.r_result for trade in dataset.trades]
    try:
        metrics = compute_metrics(r_results)
    except ValueError as exc:
        # Inputs are finite (validated at load), so this is derived overflow:
        # the data block the requested analysis. Emit the audit first so the
        # result stays connected to its data and provenance.
        _emit(dataset, args.format)
        print(f"error: cannot compute metrics for this dataset: {exc}", file=sys.stderr)
        return EXIT_BLOCKED

    if args.format == "json":
        payload = _audit_payload(dataset)
        payload["metrics"] = _metrics_payload(metrics)
        _print_json(payload)
    else:
        _print_audit_text(dataset)
        print("Metrics:")
        _print_metrics_text(metrics)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
