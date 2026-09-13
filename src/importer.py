"""P1 end-to-end importer: CSV TSFM -> conversion -> audit -> persistence -> reimport -> metrics -> bootstrap.

Acceptance criterion A1: complete flow with synthetic fixtures.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.tsfm_adapter import (
    ConversionConfig,
    ConversionResult,
    convert_trades,
)
from src.types import Trade


# ---------------------------------------------------------------------------
# Row-level audit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RowAudit:
    """One row's audit trail through the import pipeline."""

    row_index: int
    source_hash: str
    namespace: str
    status: str  # accepted | rejected | deduplicated
    trade_id: str | None
    error_code: str | None = None
    error_message: str | None = None
    warning_code: str | None = None


@dataclass
class ImportAudit:
    """Full audit trail for one import run."""

    source_path: str
    source_sha256: str
    total_rows: int
    accepted: int
    rejected: int
    deduplicated: int
    rows: tuple[RowAudit, ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    config_summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# File fingerprinting
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _row_hash(row: dict[str, Any]) -> str:
    """Deterministic hash of a TSFM row for dedup tracking."""
    canonical = json.dumps(
        {k: row[k] for k in sorted(row.keys())},
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_tsfm_csv(path: Path) -> list[dict[str, Any]]:
    """Read a TSFM-format CSV file into a list of dicts."""
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            for key in ("side", "entry", "stop", "exit_price", "pnl_net"):
                if key in row and row[key] != "":
                    try:
                        row[key] = float(row[key])
                    except (ValueError, TypeError):
                        pass
            rows.append(row)
    return rows


def write_fars_csv(trades: tuple[Trade, ...], path: Path) -> None:
    """Write FARS Trade objects to CSV for ingestion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "r_result", "trade_id", "timestamp", "asset",
        "direction", "entry_price", "stop_price",
        "exit_price", "strategy",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "r_result": t.r_result,
                "trade_id": t.trade_id,
                "timestamp": t.timestamp.isoformat() if t.timestamp else "",
                "asset": t.asset,
                "direction": t.direction or "",
                "entry_price": t.entry_price if t.entry_price is not None else "",
                "stop_price": t.stop_price if t.stop_price is not None else "",
                "exit_price": t.exit_price if t.exit_price is not None else "",
                "strategy": t.strategy or "",
            })


# ---------------------------------------------------------------------------
# Import pipeline
# ---------------------------------------------------------------------------

def import_csv(
    source_path: Path,
    output_path: Path,
    *,
    config: ConversionConfig | None = None,
    outcomes_finalized: bool = True,
) -> ImportAudit:
    """Full import pipeline: read -> convert -> audit -> persist.

    Returns ImportAudit with per-row provenance.
    """
    config = config or ConversionConfig()

    # 1. Read source CSV
    rows = read_tsfm_csv(source_path)
    source_hash = _file_sha256(source_path)

    # 2. Convert with adapter
    result = convert_trades(rows, config=config)

    # 3. Build per-row audit trail
    row_audits = []
    seen_hashes: dict[str, int] = {}
    trade_idx = 0

    for idx, rec in enumerate(rows):
        rhash = _row_hash(rec)
        if rhash in seen_hashes:
            row_audits.append(RowAudit(
                row_index=idx, source_hash=rhash,
                namespace=config.namespace, status="deduplicated",
                trade_id=None, warning_code="DEDUPLICATED",
            ))
        elif any(
            e.record_index == idx for e in result.errors
        ):
            errs = [e for e in result.errors if e.record_index == idx]
            row_audits.append(RowAudit(
                row_index=idx, source_hash=rhash,
                namespace=config.namespace, status="rejected",
                trade_id=None,
                error_code=errs[0].code,
                error_message=errs[0].message,
            ))
        else:
            # Check if this index was accepted
            trade = result.trades[trade_idx] if trade_idx < len(result.trades) else None
            trade_idx += 1
            row_audits.append(RowAudit(
                row_index=idx, source_hash=rhash,
                namespace=config.namespace, status="accepted",
                trade_id=trade.trade_id if trade else None,
            ))
        seen_hashes[rhash] = idx

    # 4. Write audited FARS CSV
    write_fars_csv(result.trades, output_path)

    # 5. Build audit report
    n_accepted = sum(1 for r in row_audits if r.status == "accepted")
    n_rejected = sum(1 for r in row_audits if r.status == "rejected")
    n_deduped = sum(1 for r in row_audits if r.status == "deduplicated")

    return ImportAudit(
        source_path=str(source_path),
        source_sha256=source_hash,
        total_rows=len(rows),
        accepted=n_accepted,
        rejected=n_rejected,
        deduplicated=n_deduped,
        rows=tuple(row_audits),
        errors=tuple(e.__dict__ for e in result.errors),
        warnings=tuple(w.__dict__ for w in result.warnings),
        config_summary={
            "symbol": config.symbol,
            "pnl_units": config.pnl_units,
            "namespace": config.namespace,
        },
    )


# ---------------------------------------------------------------------------
# Reimport (dedup-aware)
# ---------------------------------------------------------------------------

def reimport_csv(
    source_path: Path,
    output_path: Path,
    existing_hashes: set[str] | None = None,
    *,
    config: ConversionConfig | None = None,
    outcomes_finalized: bool = True,
) -> ImportAudit:
    """Reimport with deduplication against previously imported hashes.

    existing_hashes: set of source_hash values from previous imports.
    Rows whose hash is already in existing_hashes are marked 'skipped'.
    """
    config = config or ConversionConfig()
    existing_hashes = existing_hashes or set()

    # Read and convert normally
    rows = read_tsfm_csv(source_path)
    source_hash = _file_sha256(source_path)

    result = convert_trades(rows, config=config)

    # Build audit, marking duplicates as skipped
    row_audits = []
    new_hashes: set[str] = set()

    for idx, rec in enumerate(rows):
        rhash = _row_hash(rec)
        if rhash in existing_hashes:
            row_audits.append(RowAudit(
                row_index=idx, source_hash=rhash,
                namespace=config.namespace, status="skipped",
                trade_id=None, warning_code="ALREADY_IMPORTED",
            ))
        elif rhash in new_hashes:
            row_audits.append(RowAudit(
                row_index=idx, source_hash=rhash,
                namespace=config.namespace, status="deduplicated",
                trade_id=None, warning_code="DEDUPLICATED",
            ))
        else:
            new_hashes.add(rhash)
            errs = [e for e in result.errors if e.record_index == idx]
            if errs:
                row_audits.append(RowAudit(
                    row_index=idx, source_hash=rhash,
                    namespace=config.namespace, status="rejected",
                    trade_id=None, error_code=errs[0].code,
                    error_message=errs[0].message,
                ))
            else:
                row_audits.append(RowAudit(
                    row_index=idx, source_hash=rhash,
                    namespace=config.namespace, status="accepted",
                    trade_id=None,
                ))

    n_skipped = sum(1 for r in row_audits if r.status == "skipped")
    n_accepted = sum(1 for r in row_audits if r.status == "accepted")
    n_rejected = sum(1 for r in row_audits if r.status == "rejected")
    n_deduped = sum(1 for r in row_audits if r.status == "deduplicated")

    # Only write newly accepted trades
    new_trades = []
    for audit in row_audits:
        if audit.status == "accepted":
            # Find the corresponding trade in result
            for t in result.trades:
                if t.trade_id == audit.trade_id:
                    new_trades.append(t)
                    break

    write_fars_csv(tuple(new_trades), output_path)

    return ImportAudit(
        source_path=str(source_path),
        source_sha256=source_hash,
        total_rows=len(rows),
        accepted=n_accepted,
        rejected=n_rejected,
        deduplicated=n_deduped,
        rows=tuple(row_audits),
        errors=tuple(e.__dict__ for e in result.errors),
        warnings=tuple(w.__dict__ for w in result.warnings),
        config_summary={
            "symbol": config.symbol,
            "pnl_units": config.pnl_units,
            "namespace": config.namespace,
            "skipped_existing": n_skipped,
        },
    )


# ---------------------------------------------------------------------------
# End-to-end: import -> ingest -> metrics -> bootstrap
# ---------------------------------------------------------------------------

def run_full_pipeline(
    source_csv: Path,
    output_dir: Path,
    *,
    config: ConversionConfig | None = None,
    master_seed: int = 42,
) -> dict[str, Any]:
    """Run the complete P1 pipeline: import -> ingest -> metrics -> bootstrap.

    Returns a dict with all results and audit information.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    fars_csv = output_dir / "fars_trades.csv"

    # 1. Import
    audit = import_csv(source_csv, fars_csv, config=config)

    # 2. Ingest into FARS
    from src.ingestion import load_trade_csv
    dataset = load_trade_csv(fars_csv, outcomes_finalized=True)

    # 3. Compute metrics
    from src.metrics import compute_metrics
    r_values = [t.r_result for t in dataset.trades]
    metrics = compute_metrics(r_values)

    # 4. Bootstrap if eligible
    bootstrap_result = None
    if dataset.capabilities.get("core_metrics") and dataset.capabilities["core_metrics"].available:
        from src.bootstrap import analyze_bootstrap
        bootstrap_result = analyze_bootstrap(dataset, master_seed=master_seed)

    return {
        "audit": {
            "source_path": audit.source_path,
            "source_sha256": audit.source_sha256,
            "total_rows": audit.total_rows,
            "accepted": audit.accepted,
            "rejected": audit.rejected,
            "deduplicated": audit.deduplicated,
        },
        "dataset": {
            "n_trades": len(dataset.trades),
            "core_available": dataset.capabilities.get("core_metrics", type("", (), {"available": False})()).available,
            "provenance_sha256": dataset.provenance.source_sha256,
        },
        "metrics": {
            "expectancy_r": metrics.expectancy_r,
            "win_rate": metrics.win_rate,
            "n_trades": metrics.n_trades,
            "std_r": metrics.std_r,
        },
        "bootstrap": {
            "eligible": bootstrap_result is not None,
            "state": bootstrap_result["eligibility"]["state"] if bootstrap_result else None,
        },
        "output_path": str(fars_csv),
    }


__all__ = [
    "RowAudit",
    "ImportAudit",
    "read_tsfm_csv",
    "write_fars_csv",
    "import_csv",
    "reimport_csv",
    "run_full_pipeline",
]
