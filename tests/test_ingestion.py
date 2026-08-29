"""FARS 1.2 Phase 8A ingestion and audit tests."""

import hashlib
from pathlib import Path

import pytest

from src.engine import run_simulation
from src.ingestion import TradeDataError, load_trade_csv as _load_trade_csv
from src.metrics import compute_metrics
from src.types import FundedAccountRules


def _write_csv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "trades.csv"
    path.write_text(content, encoding="utf-8")
    return path


def load_trade_csv(path, **kwargs):
    """Load finalized test fixtures unless a test calls the public function directly."""
    return _load_trade_csv(path, outcomes_finalized=True, **kwargs)


def test_minimum_r_result_contract_and_unknown_metadata(tmp_path):
    path = _write_csv(tmp_path, "r_result,note\n1.5,kept\n-1,\n")

    dataset = load_trade_csv(path)

    assert [trade.r_result for trade in dataset.trades] == [1.5, -1.0]
    assert dataset.audit.total_rows == dataset.audit.accepted_rows == 2
    assert dataset.audit.rejected_rows == 0
    assert dataset.capabilities["core_metrics"].available
    assert not dataset.capabilities["temporal_analysis"].available
    assert not dataset.capabilities["daily_rule_simulation"].available
    assert dataset.trades[0].metadata["unknown_fields"] == {"note": "kept"}
    assert dataset.trades[1].metadata["unknown_fields"] == {"note": ""}
    assert all(trade.strategy == "" for trade in dataset.trades)
    assert sum(
        issue.code == "MISSING_TIMESTAMP_COLUMN" for issue in dataset.audit.issues
    ) == 1


def test_external_mapping_and_timezone_enable_daily_analysis(tmp_path):
    path = _write_csv(
        tmp_path,
        "Result,When,Ticket,Market\n"
        "1,2024-01-01T23:30:00+00:00,a,ES\n"
        "-0.5,2024-01-02T00:30:00+00:00,b,MNQ\n",
    )

    dataset = load_trade_csv(
        path,
        column_mapping={
            "Result": "r_result",
            "When": "timestamp",
            "Ticket": "trade_id",
            "Market": "asset",
        },
        analysis_timezone="America/New_York",
    )

    assert all(status.available for status in dataset.capabilities.values())
    assert [trade.date for trade in dataset.trades] == ["2024-01-01", "2024-01-01"]
    assert [trade.asset for trade in dataset.trades] == ["ES", "MNQ"]
    assert all(trade.metadata["unknown_fields"] == {} for trade in dataset.trades)
    provenance = dataset.provenance
    assert provenance.schema_version == "1.2-phase8a"
    assert provenance.source_path == str(path.resolve())
    assert provenance.source_size_bytes == path.stat().st_size
    assert provenance.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert provenance.resolved_mapping == {
        "Result": "r_result",
        "When": "timestamp",
        "Ticket": "trade_id",
        "Market": "asset",
    }
    assert provenance.analysis_timezone == "America/New_York"
    assert provenance.outcomes_finalized is True
    with pytest.raises(TypeError):
        provenance.resolved_mapping["Other"] = "asset"


def test_aware_timestamps_without_analysis_timezone_are_temporal_only(tmp_path):
    path = _write_csv(
        tmp_path,
        "r_result,timestamp\n1,2024-01-01T10:00:00Z\n",
    )

    dataset = load_trade_csv(path)

    assert dataset.capabilities["core_metrics"].available
    assert dataset.capabilities["temporal_analysis"].available
    assert not dataset.capabilities["daily_rule_simulation"].available
    assert dataset.trades[0].date == ""


@pytest.mark.parametrize("bad_r", ["", "nan", "inf", "not-a-number"])
def test_invalid_required_outcome_is_rejected_and_blocks_metrics(tmp_path, bad_r):
    path = _write_csv(tmp_path, f"r_result\n1\n{bad_r}\n")

    dataset = load_trade_csv(path)

    assert len(dataset.trades) == 1
    assert dataset.audit.rejected_rows == 1
    assert dataset.audit.has_errors
    assert not dataset.capabilities["core_metrics"].available
    with pytest.raises(TradeDataError, match="source rows were rejected"):
        dataset.require_capability("core_metrics")


def test_duplicate_nonempty_ids_are_retained_but_block_analysis(tmp_path):
    path = _write_csv(tmp_path, "r_result,trade_id\n1,same\n-1,same\n")

    dataset = load_trade_csv(path)

    assert len(dataset.trades) == 2
    assert dataset.audit.rejected_rows == 0
    assert any(issue.code == "DUPLICATE_TRADE_ID" for issue in dataset.audit.issues)
    assert not dataset.capabilities["core_metrics"].available


def test_missing_ids_do_not_infer_duplicates_from_equal_rows(tmp_path):
    path = _write_csv(tmp_path, "r_result,trade_id\n1,\n1,\n")

    dataset = load_trade_csv(path)

    assert dataset.capabilities["core_metrics"].available
    assert not any(issue.code == "DUPLICATE_TRADE_ID" for issue in dataset.audit.issues)


def test_invalid_optional_fields_do_not_block_core_metrics(tmp_path):
    path = _write_csv(
        tmp_path,
        "r_result,entry_price,direction,timestamp\n1,bad,sideways,not-a-date\n",
    )

    dataset = load_trade_csv(path)

    trade = dataset.trades[0]
    assert trade.entry_price is None
    assert trade.direction is None
    assert trade.timestamp is None
    assert trade.metadata["invalid_optional_fields"] == {
        "timestamp": "not-a-date",
        "direction": "sideways",
        "entry_price": "bad",
    }
    assert dataset.capabilities["core_metrics"].available
    assert not dataset.capabilities["temporal_analysis"].available


def test_naive_timestamp_does_not_receive_implicit_timezone(tmp_path):
    path = _write_csv(tmp_path, "r_result,timestamp\n1,2024-01-01T09:30:00\n")

    dataset = load_trade_csv(path, analysis_timezone="UTC")

    assert dataset.trades[0].timestamp.tzinfo is None
    assert dataset.trades[0].date == ""
    assert not dataset.capabilities["temporal_analysis"].available
    assert any(issue.code == "NAIVE_TIMESTAMP" for issue in dataset.audit.issues)


def test_non_chronological_input_is_not_sorted_and_blocks_temporal_use(tmp_path):
    path = _write_csv(
        tmp_path,
        "r_result,timestamp\n"
        "1,2024-01-02T09:30:00+00:00\n"
        "-1,2024-01-01T09:30:00+00:00\n",
    )

    dataset = load_trade_csv(path, analysis_timezone="UTC")

    assert [trade.r_result for trade in dataset.trades] == [1.0, -1.0]
    assert dataset.capabilities["core_metrics"].available
    assert not dataset.capabilities["temporal_analysis"].available
    assert not dataset.capabilities["daily_rule_simulation"].available
    assert any(
        issue.code == "NON_CHRONOLOGICAL_TIMESTAMP" for issue in dataset.audit.issues
    )


@pytest.mark.parametrize(
    "content,error",
    [
        ("", "no header"),
        ("r_result,r_result\n1,2\n", "duplicate headers"),
        (",value\n1,2\n", "headers must be non-empty"),
        ("value\n1\n", "required canonical field"),
    ],
)
def test_structural_csv_errors(tmp_path, content, error):
    path = _write_csv(tmp_path, content)
    with pytest.raises(TradeDataError, match=error):
        load_trade_csv(path)


def test_header_only_csv_returns_audited_unavailable_dataset(tmp_path):
    path = _write_csv(tmp_path, "r_result,timestamp\n")

    dataset = load_trade_csv(path)

    assert dataset.audit.total_rows == 0
    assert dataset.audit.has_errors
    assert any(issue.code == "NO_DATA_ROWS" for issue in dataset.audit.issues)
    assert not dataset.capabilities["core_metrics"].available


def test_mapping_missing_source_unsupported_target_and_collision(tmp_path):
    path = _write_csv(tmp_path, "a,b\n1,2\n")

    with pytest.raises(TradeDataError, match="missing"):
        load_trade_csv(path, column_mapping={"missing": "r_result"})
    with pytest.raises(TradeDataError, match="unsupported"):
        load_trade_csv(path, column_mapping={"a": "profit"})
    with pytest.raises(TradeDataError, match="multiple source"):
        load_trade_csv(
            path, column_mapping={"a": "r_result", "b": "r_result"}
        )


def test_explicit_alias_cannot_silently_override_canonical_header(tmp_path):
    path = _write_csv(tmp_path, "r_result,alias\n1,-99\n")

    with pytest.raises(TradeDataError, match="multiple source"):
        load_trade_csv(path, column_mapping={"alias": "r_result"})


def test_malformed_row_width_is_rejected_without_misalignment(tmp_path):
    path = _write_csv(tmp_path, "r_result,note\n1,ok\n-1,too,many\n")

    dataset = load_trade_csv(path)

    assert [trade.r_result for trade in dataset.trades] == [1.0]
    assert dataset.audit.rejected_rows == 1
    assert any(issue.code == "COLUMN_COUNT_MISMATCH" for issue in dataset.audit.issues)
    assert not dataset.capabilities["core_metrics"].available


def test_equal_timestamps_are_allowed_and_input_order_is_stable(tmp_path):
    path = _write_csv(
        tmp_path,
        "r_result,timestamp\n"
        "1,2024-01-01T09:30:00+00:00\n"
        "-1,2024-01-01T09:30:00+00:00\n",
    )

    dataset = load_trade_csv(path, analysis_timezone="UTC")

    assert dataset.capabilities["temporal_analysis"].available
    assert [trade.r_result for trade in dataset.trades] == [1.0, -1.0]


def test_utf8_bom_header_is_supported(tmp_path):
    path = tmp_path / "trades.csv"
    path.write_bytes("\ufeffr_result,asset\n1,ES\n".encode("utf-8"))

    dataset = load_trade_csv(path)

    assert dataset.trades[0].asset == "ES"


def test_unknown_capability_and_invalid_timezone_are_explicit(tmp_path):
    path = _write_csv(tmp_path, "r_result\n1\n")
    dataset = load_trade_csv(path)

    with pytest.raises(ValueError, match="unknown capability"):
        dataset.require_capability("bootstrap")
    with pytest.raises(TradeDataError, match="unknown IANA"):
        load_trade_csv(path, analysis_timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize("attestation", [None, False, 1, "yes"])
def test_final_outcome_attestation_is_explicit_and_strict(tmp_path, attestation):
    path = _write_csv(tmp_path, "r_result,status\n1,open\n")

    with pytest.raises(TradeDataError, match="outcomes_finalized=True"):
        _load_trade_csv(path, outcomes_finalized=attestation)


def test_loaded_trade_metadata_remains_mutable_by_core_contract(tmp_path):
    path = _write_csv(tmp_path, "r_result,note\n1,original\n")
    dataset = load_trade_csv(path)

    dataset.trades[0].metadata["runtime_annotation"] = "reviewed"

    assert dataset.trades[0].metadata["runtime_annotation"] == "reviewed"


def test_malformed_quoted_csv_raises_domain_error(tmp_path):
    path = _write_csv(tmp_path, 'r_result,note\n1,"unterminated\n')

    with pytest.raises(TradeDataError, match="malformed CSV"):
        load_trade_csv(path)


def test_invalid_encoding_raises_domain_error(tmp_path):
    path = tmp_path / "trades.csv"
    path.write_bytes(b"r_result,note\n1,\xff\n")

    with pytest.raises(TradeDataError, match="decode CSV"):
        load_trade_csv(path)


@pytest.mark.parametrize("delimiter", ['"', "\n", "\r", "", "::", None])
def test_invalid_delimiter_raises_domain_error(tmp_path, delimiter):
    path = _write_csv(tmp_path, "r_result\n1\n")

    with pytest.raises(TradeDataError, match="delimiter"):
        load_trade_csv(path, delimiter=delimiter)


def test_date_conversion_overflow_disables_only_daily_capability(tmp_path):
    path = _write_csv(
        tmp_path,
        "r_result,timestamp\n1,0001-01-01T00:00:00+14:00\n",
    )

    dataset = load_trade_csv(path, analysis_timezone="UTC")

    assert dataset.capabilities["core_metrics"].available
    assert dataset.capabilities["temporal_analysis"].available
    assert not dataset.capabilities["daily_rule_simulation"].available
    assert dataset.trades[0].date == ""
    assert any(issue.code == "DATE_CONVERSION_FAILED" for issue in dataset.audit.issues)


def test_canonical_dataset_integrates_with_metrics_and_daily_core(tmp_path):
    path = _write_csv(
        tmp_path,
        "r_result,timestamp\n"
        "2,2024-01-01T14:30:00+00:00\n"
        "-1,2024-01-02T14:30:00+00:00\n",
    )
    dataset = load_trade_csv(path, analysis_timezone="America/New_York")

    dataset.require_capability("core_metrics")
    metrics = compute_metrics([trade.r_result for trade in dataset.trades])
    assert metrics.n_trades == 2
    assert metrics.expectancy_r == pytest.approx(0.5)

    dataset.require_capability("daily_rule_simulation")
    result = run_simulation(
        list(dataset.trades),
        FundedAccountRules(
            initial_balance=100_000.0,
            profit_target_pct=0.50,
            max_drawdown_pct=0.50,
            daily_loss_limit_pct=0.50,
            risk_per_trade=0.01,
        ),
    )
    assert result.terminal_condition == "completed"
    assert result.trades_executed == 2
