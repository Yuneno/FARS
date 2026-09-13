import time as _t
"""P1 end-to-end integration tests: CSV -> conversion -> audit -> persist -> reimport -> metrics -> bootstrap."""
import csv
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from src.importer import (
    read_tsfm_csv, write_fars_csv, import_csv, reimport_csv,
    run_full_pipeline, _row_hash, _file_sha256,
)
from src.tsfm_adapter import ConversionConfig

# Override tmp_path to use workspace-local directory (sandbox blocks system temp)
_WORKSPACE_TEMP = Path(__file__).resolve().parent.parent / "_test_tmp"

@pytest.fixture
def tmp_path():
    import shutil
    d = _WORKSPACE_TEMP / f"run_{int(_t.time_ns()) & 0xFFFFFF:06x}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)



# ---------------------------------------------------------------------------
# Synthetic fixture generator
# ---------------------------------------------------------------------------

def _write_tsfm_csv(path: Path, records: list[dict]) -> Path:
    """Write TSFM-format CSV with synthetic trade records."""
    headers = ["t", "strategy", "side", "entry", "stop", "exit_price", "pnl_net", "outcome", "asset", "trade_id"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)
    return path


def _make_records(n: int = 10, *, include_errors: bool = False,
                  include_duplicates: bool = False,
                  symbol: str = "MNQ") -> list[dict]:
    """Generate n synthetic TSFM trade records."""
    import random
    rng = random.Random(42)
    records = []
    for i in range(n):
        side = 1 if rng.random() > 0.4 else -1
        entry = 15000 + rng.uniform(-50, 50)
        risk = rng.uniform(10, 30)
        stop = entry - risk if side == 1 else entry + risk
        pnl = risk * rng.uniform(-1.5, 3)
        records.append({
            "t": f"2024-06-0{i % 9 + 1}T{8 + i % 8:02d}:{i % 4 * 15:02d}:00+00:00",
            "strategy": "breakout_v1",
            "side": side,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "exit_price": round(entry + pnl * 0.3, 2),
            "pnl_net": round(pnl, 2),
            "outcome": "closed",
            "asset": symbol,
            "trade_id": f"SYN-{i:03d}",
        })
    if include_duplicates:
        records.append(dict(records[0]))  # exact copy
    if include_errors:
        records.append({
            "t": "2024-06-01T10:00:00+00:00",
            "strategy": "test",
            "side": 1.5,
            "entry": 100,
            "stop": 95,
            "exit_price": 110,
            "pnl_net": 10,
            "outcome": "closed",
            "asset": "MNQ",
            "trade_id": "ERR-001",
        })
    return records


# ===========================================================================
# 1. Read/write CSV round-trip
# ===========================================================================

class TestCsvIO:
    def test_read_tsfm_csv(self, tmp_path):
        records = _make_records(5)
        path = _write_tsfm_csv(tmp_path / "trades.csv", records)
        rows = read_tsfm_csv(path)
        assert len(rows) == 5
        assert rows[0]["side"] == 1 or rows[0]["side"] == -1

    def test_write_fars_csv_roundtrip(self, tmp_path):
        from src.tsfm_adapter import convert_trades
        records = _make_records(5)
        result = convert_trades(records, config=ConversionConfig(symbol="MNQ"))
        csv_path = tmp_path / "fars.csv"
        write_fars_csv(result.trades, csv_path)
        rows = read_tsfm_csv(csv_path)
        assert len(rows) == result.accepted

    def test_file_sha256_deterministic(self, tmp_path):
        path = _write_tsfm_csv(tmp_path / "trades.csv", _make_records(3))
        h1 = _file_sha256(path)
        h2 = _file_sha256(path)
        assert h1 == h2
        assert len(h1) == 64


# ===========================================================================
# 2. Row hash determinism
# ===========================================================================

class TestRowHash:
    def test_deterministic(self):
        rec = {"t": "2024-01-01", "side": 1, "entry": 100}
        h1 = _row_hash(rec)
        h2 = _row_hash(rec)
        assert h1 == h2

    def test_different_content_different_hash(self):
        r1 = {"t": "2024-01-01", "side": 1}
        r2 = {"t": "2024-01-02", "side": 1}
        assert _row_hash(r1) != _row_hash(r2)


# ===========================================================================
# 3. Import pipeline
# ===========================================================================

class TestImportPipeline:
    def test_basic_import(self, tmp_path):
        records = _make_records(8)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst = tmp_path / "out" / "fars.csv"
        audit = import_csv(src, dst, config=ConversionConfig(symbol="MNQ"))
        assert audit.accepted == 8
        assert audit.rejected == 0
        assert dst.exists()

    def test_import_with_errors(self, tmp_path):
        records = _make_records(5, include_errors=True)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst = tmp_path / "out" / "fars.csv"
        audit = import_csv(src, dst, config=ConversionConfig(symbol="MNQ"))
        assert audit.accepted == 5
        assert audit.rejected >= 1

    def test_import_with_duplicates(self, tmp_path):
        records = _make_records(5, include_duplicates=True)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst = _write_tsfm_csv(tmp_path / "out.csv", records)  # pre-create
        audit = import_csv(src, dst, config=ConversionConfig(symbol="MNQ"))
        assert audit.deduplicated >= 1

    def test_audit_per_row(self, tmp_path):
        records = _make_records(3)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst = tmp_path / "out" / "fars.csv"
        audit = import_csv(src, dst, config=ConversionConfig(symbol="MNQ"))
        assert len(audit.rows) == 3
        for row in audit.rows:
            assert row.source_hash
            assert row.namespace == "tsfm"

    def test_audit_source_hash(self, tmp_path):
        records = _make_records(3)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst = tmp_path / "out" / "fars.csv"
        audit = import_csv(src, dst, config=ConversionConfig(symbol="MNQ"))
        assert audit.source_sha256 == _file_sha256(src)

    def test_invariant_accepted_rejected_dedup(self, tmp_path):
        records = _make_records(5, include_errors=True, include_duplicates=True)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst = _tmp_path_out(tmp_path) if False else tmp_path / "out" / "fars.csv"
        audit = import_csv(src, dst, config=ConversionConfig(symbol="MNQ"))
        assert audit.accepted + audit.rejected + audit.deduplicated == audit.total_rows


def _tmp_path_out(tmp_path):
    return tmp_path / "out" / "fars.csv"


# ===========================================================================
# 4. Reimport deduplication
# ===========================================================================

class TestReimport:
    def test_second_import_skips_existing(self, tmp_path):
        records = _make_records(5)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst1 = tmp_path / "run1" / "fars.csv"
        audit1 = import_csv(src, dst1, config=ConversionConfig(symbol="MNQ"))
        assert audit1.accepted == 5

        # Collect hashes from first import
        hashes = {r.source_hash for r in audit1.rows if r.status == "accepted"}

        # Second import of same data
        dst2 = tmp_path / "run2" / "fars.csv"
        audit2 = reimport_csv(src, dst2, existing_hashes=hashes, config=ConversionConfig(symbol="MNQ"))
        assert audit2.accepted == 0  # all skipped

    def test_partial_reimport(self, tmp_path):
        records = _make_records(5)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        dst1 = tmp_path / "run1" / "fars.csv"
        audit1 = import_csv(src, dst1, config=ConversionConfig(symbol="MNQ"))

        # Only mark first 3 as imported
        hashes = {r.source_hash for r in audit1.rows[:3] if r.status == "accepted"}

        dst2 = tmp_path / "run2" / "fars.csv"
        audit2 = reimport_csv(src, dst2, existing_hashes=hashes, config=ConversionConfig(symbol="MNQ"))
        assert audit2.accepted + sum(1 for r in audit2.rows if r.status == "skipped") == 5


# ===========================================================================
# 5. Full pipeline: import -> ingest -> metrics -> bootstrap
# ===========================================================================

class TestFullPipeline:
    def test_complete_flow(self, tmp_path):
        records = _make_records(30)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        out_dir = tmp_path / "pipeline"

        result = run_full_pipeline(src, out_dir, config=ConversionConfig(symbol="MNQ"))

        assert result["audit"]["accepted"] == 30
        assert result["audit"]["rejected"] == 0
        assert result["dataset"]["n_trades"] == 30
        assert result["dataset"]["core_available"] is True
        assert result["metrics"]["n_trades"] == 30
        assert result["metrics"]["win_rate"] >= 0
        assert result["bootstrap"]["eligible"] is True
        assert result["bootstrap"]["state"] in {"iid_eligible", "dependent_resampling_candidate", "unsupported_or_inconclusive"}

    def test_pipeline_deterministic(self, tmp_path):
        records = _make_records(15)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)

        r1 = run_full_pipeline(src, tmp_path / "run1", config=ConversionConfig(symbol="MNQ"))
        r2 = run_full_pipeline(src, tmp_path / "run2", config=ConversionConfig(symbol="MNQ"))

        assert r1["metrics"]["expectancy_r"] == pytest.approx(r2["metrics"]["expectancy_r"])
        assert r1["metrics"]["win_rate"] == pytest.approx(r2["metrics"]["win_rate"])

    def test_pipeline_with_errors(self, tmp_path):
        records = _make_records(10, include_errors=True)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        out_dir = tmp_path / "pipeline"

        result = run_full_pipeline(src, out_dir, config=ConversionConfig(symbol="MNQ"))
        assert result["audit"]["accepted"] >= 10
        assert result["audit"]["rejected"] >= 1

    def test_output_csv_exists(self, tmp_path):
        records = _make_records(5)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        out_dir = tmp_path / "pipeline"
        result = run_full_pipeline(src, out_dir, config=ConversionConfig(symbol="MNQ"))
        assert Path(result["output_path"]).exists()

    def test_metrics_structure(self, tmp_path):
        records = _make_records(20)
        src = _write_tsfm_csv(tmp_path / "src.csv", records)
        result = run_full_pipeline(src, tmp_path / "run", config=ConversionConfig(symbol="MNQ"))
        m = result["metrics"]
        assert "expectancy_r" in m
        assert "win_rate" in m
        assert "n_trades" in m
        assert "std_r" in m
