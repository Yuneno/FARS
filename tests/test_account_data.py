"""Deterministic acceptance tests for the Phase 11A monetary contract."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.account_data import (
    ACCOUNT_TRADE_SCHEMA_VERSION,
    R_DERIVATION_FORMULA,
    R_DERIVATION_SOURCE_FIELDS,
    R_DERIVATION_VERSION,
    AccountEquityEvent,
    AccountTradeDataError,
    CanonicalAccountTrade,
    RValueProvenance,
    load_account_trade_csv,
)
from src.types import Trade


HEADER = (
    "trade_id,opened_at,closed_at,instrument,contract_multiplier,direction,quantity,"
    "entry_price,exit_price,currency,gross_pnl,commission,exchange_fees,"
    "other_fees,net_pnl,initial_risk_amount,r_result,strategy,note\n"
)


def _write(tmp_path: Path, rows: str, *, header: str = HEADER) -> Path:
    path = tmp_path / "account.csv"
    path.write_text(header + rows, encoding="utf-8")
    return path


def _load(path: Path, **kwargs):
    return load_account_trade_csv(
        path,
        row_semantics="completed_round_trip",
        **kwargs,
    )


def _complete_row(
    *,
    trade_id: str = "T-1",
    opened_at: str = "2026-08-01T09:30:00-04:00",
    closed_at: str = "2026-08-01T10:00:00-04:00",
    currency: str = "USD",
    gross: str = "110.00",
    commission: str = "5.00",
    exchange_fees: str = "3.00",
    other_fees: str = "2.00",
    net: str = "100.00",
    risk: str = "50.00",
    r_result: str = "2",
) -> str:
    return (
        f"{trade_id},{opened_at},{closed_at},MNQ,2,long,2,"
        f"20000.25,20010.25,{currency},{gross},{commission},{exchange_fees},"
        f"{other_fees},{net},{risk},{r_result},baseline,kept\n"
    )


def test_complete_round_trip_is_exact_traceable_and_capability_ready(tmp_path):
    path = _write(tmp_path, _complete_row())
    source_before = path.read_bytes()

    dataset = _load(path)

    assert path.read_bytes() == source_before
    assert dataset.audit.total_rows == dataset.audit.accepted_rows == 1
    assert dataset.audit.rejected_rows == 0
    trade = dataset.trades[0]
    assert isinstance(trade, CanonicalAccountTrade)
    assert not isinstance(trade, Trade)
    assert trade.currency == "USD"
    assert trade.contract_multiplier == Decimal("2")
    assert trade.quantity == Decimal("2")
    assert trade.gross_pnl == Decimal("110.00")
    assert trade.net_pnl == Decimal("100.00")
    assert trade.cost_reconciliation == "verified"
    assert trade.cost_reconciliation_tolerance == Decimal("0")
    assert trade.r_result == Decimal("2")
    assert trade.r_provenance is not None
    assert trade.r_provenance.origin == "supplied"
    assert trade.metadata["unknown_fields"] == {"note": "kept"}
    assert trade.fingerprint.source_row_number == 2
    assert len(trade.fingerprint.raw_row_sha256) == 64
    assert len(trade.fingerprint.normalized_record_sha256) == 64
    assert dataset.provenance.schema_version == ACCOUNT_TRADE_SCHEMA_VERSION
    assert dataset.provenance.source_sha256 == hashlib.sha256(source_before).hexdigest()
    assert dataset.provenance.row_semantics == "completed_round_trip"
    assert all(
        dataset.capabilities[name].available
        for name in (
            "account_pnl",
            "cost_reconciliation",
            "r_multiple_analysis",
            "closed_trade_replay",
        )
    )
    assert not dataset.capabilities["intraday_rule_replay"].available


def test_fingerprints_are_reproducible_and_change_with_content(tmp_path):
    path = _write(tmp_path, _complete_row())
    first = _load(path).trades[0].fingerprint
    second = _load(path).trades[0].fingerprint
    assert first == second

    path.write_text(HEADER + _complete_row(net="99.00", r_result="1.98"), encoding="utf-8")
    changed = _load(path, currency_tolerance="1").trades[0].fingerprint
    assert changed.raw_row_sha256 != first.raw_row_sha256
    assert changed.normalized_record_sha256 != first.normalized_record_sha256


def test_r_is_derived_only_from_reconciled_net_and_positive_initial_risk(tmp_path):
    path = _write(tmp_path, _complete_row(r_result=""))

    trade = _load(path).trades[0]

    assert trade.r_result == Decimal("2")
    assert trade.r_provenance is not None
    assert trade.r_provenance.origin == "derived"
    assert trade.r_provenance.source_fields == R_DERIVATION_SOURCE_FIELDS
    assert trade.r_provenance.formula == R_DERIVATION_FORMULA
    assert trade.r_provenance.method_version == R_DERIVATION_VERSION


def test_public_constructor_rejects_forged_derived_r_and_stale_fingerprint(tmp_path):
    trade = _load(_write(tmp_path, _complete_row())).trades[0]
    derived = RValueProvenance(
        "derived",
        R_DERIVATION_SOURCE_FIELDS,
        R_DERIVATION_FORMULA,
        R_DERIVATION_VERSION,
    )

    with pytest.raises(ValueError, match="does not equal"):
        replace(trade, r_result=Decimal("999"), r_provenance=derived)
    with pytest.raises(ValueError, match="fingerprint does not match"):
        replace(trade, strategy="changed-after-normalization")


def test_loss_sign_convention_subtracts_costs_and_derives_negative_r(tmp_path):
    path = _write(
        tmp_path,
        _complete_row(
            gross="-90",
            commission="5",
            exchange_fees="3",
            other_fees="2",
            net="-100",
            risk="50",
            r_result="",
        ),
    )

    trade = _load(path).trades[0]

    assert trade.cost_reconciliation == "verified"
    assert trade.net_pnl == Decimal("-100")
    assert trade.r_result == Decimal("-2")


@pytest.mark.parametrize("risk", ["", "0", "-50", "nan"])
def test_missing_or_invalid_initial_risk_never_invents_r(tmp_path, risk):
    path = _write(tmp_path, _complete_row(risk=risk, r_result=""))

    dataset = _load(path)

    assert dataset.trades[0].r_result is None
    assert dataset.trades[0].r_provenance is None
    assert not dataset.capabilities["r_multiple_analysis"].available
    assert dataset.capabilities["account_pnl"].available


def test_incomplete_costs_keep_supplied_net_and_r_but_degrade_reconciliation(tmp_path):
    path = _write(
        tmp_path,
        _complete_row(commission="", exchange_fees="", other_fees=""),
    )

    dataset = _load(path)

    assert dataset.trades[0].cost_reconciliation == "not_evaluable"
    assert dataset.capabilities["account_pnl"].available
    assert dataset.capabilities["r_multiple_analysis"].available
    assert not dataset.capabilities["cost_reconciliation"].available


def test_incomplete_costs_do_not_authorize_r_derivation(tmp_path):
    path = _write(
        tmp_path,
        _complete_row(commission="", exchange_fees="", other_fees="", r_result=""),
    )

    dataset = _load(path)

    assert dataset.trades[0].r_result is None
    assert not dataset.capabilities["r_multiple_analysis"].available


def test_cost_identity_uses_explicit_decimal_tolerance(tmp_path):
    path = _write(tmp_path, _complete_row(net="100.01", r_result=""))

    exact = _load(path)
    tolerant = _load(path, currency_tolerance=Decimal("0.01"))

    assert exact.trades[0].cost_reconciliation == "failed"
    assert exact.trades[0].r_result is None
    assert not exact.capabilities["account_pnl"].available
    assert any(
        issue.code == "NET_PNL_RECONCILIATION_FAILED"
        for issue in exact.audit.issues
    )
    assert tolerant.trades[0].cost_reconciliation == "verified"
    assert tolerant.trades[0].cost_reconciliation_tolerance == Decimal("0.01")
    assert tolerant.trades[0].r_result == Decimal("2.0002")
    assert tolerant.provenance.currency_tolerance == Decimal("0.01")

    with pytest.raises(ValueError, match="does not match"):
        replace(exact.trades[0], cost_reconciliation="verified")


def test_negative_cost_is_not_silently_treated_as_standard_magnitude(tmp_path):
    path = _write(tmp_path, _complete_row(commission="-5"))

    dataset = _load(path)
    trade = dataset.trades[0]

    assert trade.commission is None
    assert trade.cost_reconciliation == "not_evaluable"
    assert trade.metadata["invalid_optional_fields"]["commission"] == "-5"
    assert any(
        issue.code == "INVALID_OPTIONAL_MONETARY_VALUE"
        and issue.field == "commission"
        for issue in dataset.audit.issues
    )


def test_supplied_and_derived_r_are_retained_but_not_mixed_automatically(tmp_path):
    rows = _complete_row(trade_id="T-1", r_result="2") + _complete_row(
        trade_id="T-2",
        opened_at="2026-08-01T10:30:00-04:00",
        closed_at="2026-08-01T11:00:00-04:00",
        r_result="",
    )
    dataset = _load(_write(tmp_path, rows))

    assert [trade.r_provenance.origin for trade in dataset.trades] == [
        "supplied",
        "derived",
    ]
    assert not dataset.capabilities["r_multiple_analysis"].available
    with pytest.raises(AccountTradeDataError, match="cannot be mixed"):
        dataset.require_capability("r_multiple_analysis")


def test_multiple_currencies_block_only_currency_aggregation(tmp_path):
    rows = _complete_row(trade_id="T-1", currency="usd") + _complete_row(
        trade_id="T-2",
        opened_at="2026-08-01T10:30:00-04:00",
        closed_at="2026-08-01T11:00:00-04:00",
        currency="EUR",
    )
    dataset = _load(_write(tmp_path, rows))

    assert [trade.currency for trade in dataset.trades] == ["USD", "EUR"]
    assert not dataset.capabilities["account_pnl"].available
    assert dataset.capabilities["r_multiple_analysis"].available


def test_missing_optional_fields_retain_row_and_degrade_capabilities(tmp_path):
    header = "currency,net_pnl,note\n"
    dataset = _load(_write(tmp_path, "USD,25,kept\n", header=header))

    assert dataset.audit.accepted_rows == 1
    trade = dataset.trades[0]
    assert trade.net_pnl == Decimal("25")
    assert trade.r_result is None
    assert trade.trade_id == ""
    assert trade.closed_at is None
    assert trade.metadata["unknown_fields"] == {"note": "kept"}
    assert dataset.capabilities["account_pnl"].available
    assert not dataset.capabilities["r_multiple_analysis"].available
    assert not dataset.capabilities["closed_trade_replay"].available


def test_duplicate_ids_are_retained_for_audit_and_block_automatic_use(tmp_path):
    rows = _complete_row(trade_id="same") + _complete_row(
        trade_id="same",
        opened_at="2026-08-01T10:30:00-04:00",
        closed_at="2026-08-01T11:00:00-04:00",
    )
    dataset = _load(_write(tmp_path, rows))

    assert len(dataset.trades) == 2
    assert any(
        issue.code == "DUPLICATE_ACCOUNT_TRADE_ID"
        for issue in dataset.audit.issues
    )
    assert not dataset.capabilities["account_pnl"].available
    assert not dataset.capabilities["closed_trade_replay"].available


def test_naive_and_non_chronological_timestamps_never_get_sorted_or_assumed(tmp_path):
    rows = _complete_row(
        trade_id="T-1",
        opened_at="2026-08-02T09:30:00",
        closed_at="2026-08-02T10:00:00+00:00",
    ) + _complete_row(
        trade_id="T-2",
        opened_at="2026-08-01T09:30:00+00:00",
        closed_at="2026-08-01T10:00:00+00:00",
    )
    dataset = _load(_write(tmp_path, rows))

    assert dataset.trades[0].opened_at is None
    assert [trade.trade_id for trade in dataset.trades] == ["T-1", "T-2"]
    assert not dataset.capabilities["closed_trade_replay"].available
    assert any(
        issue.code == "INVALID_ACCOUNT_TIMESTAMP" for issue in dataset.audit.issues
    )
    assert any(
        issue.code == "NON_CHRONOLOGICAL_ACCOUNT_TRADE"
        for issue in dataset.audit.issues
    )


def test_open_after_close_is_disclosed_and_blocks_replay(tmp_path):
    path = _write(
        tmp_path,
        _complete_row(
            opened_at="2026-08-01T11:00:00+00:00",
            closed_at="2026-08-01T10:00:00+00:00",
        ),
    )
    dataset = _load(path)

    assert dataset.trades[0].opened_at is None
    assert not dataset.capabilities["closed_trade_replay"].available
    assert any(
        issue.code == "ACCOUNT_TRADE_TIME_REVERSED"
        for issue in dataset.audit.issues
    )


@pytest.mark.parametrize(
    "row_semantics",
    ["fill", "order", "partial_fill", "reversal", "average_price"],
)
def test_unapproved_fill_and_position_semantics_are_refused(tmp_path, row_semantics):
    path = _write(tmp_path, _complete_row())
    with pytest.raises(AccountTradeDataError, match="reconstruction is unsupported"):
        load_account_trade_csv(path, row_semantics=row_semantics)


def test_currency_is_structurally_required_and_missing_values_are_rejected(tmp_path):
    no_currency_header = "trade_id,net_pnl\n"
    with pytest.raises(AccountTradeDataError, match="required canonical field 'currency'"):
        _load(_write(tmp_path, "T-1,10\n", header=no_currency_header))

    dataset = _load(_write(tmp_path, _complete_row(currency="")))
    assert dataset.audit.rejected_rows == 1
    assert dataset.trades == ()
    assert any(issue.code == "MISSING_CURRENCY" for issue in dataset.audit.issues)


def test_structural_mapping_width_and_delimiter_errors_are_domain_errors(tmp_path):
    path = _write(tmp_path, _complete_row())
    with pytest.raises(AccountTradeDataError, match="multiple source columns"):
        _load(
            path,
            column_mapping={"trade_id": "currency"},
        )
    with pytest.raises(AccountTradeDataError, match="delimiter"):
        _load(path, delimiter="\n")

    malformed = _write(tmp_path, "USD,10,extra\n", header="currency,net_pnl\n")
    dataset = _load(malformed)
    assert dataset.audit.rejected_rows == 1
    assert any(
        issue.code == "ACCOUNT_COLUMN_COUNT_MISMATCH"
        for issue in dataset.audit.issues
    )


def test_mapping_is_one_to_one_and_unknown_provider_fields_are_preserved(tmp_path):
    header = "Ticket,Ccy,Net,ProviderOnly\n"
    path = _write(tmp_path, "A,USD,25,raw\n", header=header)
    dataset = _load(
        path,
        column_mapping={
            "Ticket": "trade_id",
            "Ccy": "currency",
            "Net": "net_pnl",
        },
    )

    assert dataset.trades[0].trade_id == "A"
    assert dataset.trades[0].metadata["unknown_fields"] == {
        "ProviderOnly": "raw"
    }
    assert dataset.provenance.resolved_mapping == {
        "Ticket": "trade_id",
        "Ccy": "currency",
        "Net": "net_pnl",
    }
    with pytest.raises(TypeError):
        dataset.provenance.resolved_mapping["Other"] = "strategy"


@pytest.mark.parametrize(
    "header",
    [
        "password",
        "api_key",
        "session-token",
        "AccessToken",
        "client_secret",
        "auth_token",
        "bearer_token",
        "id_token",
        "private_key",
        "credentials",
    ],
)
def test_credentials_are_refused_instead_of_preserved_as_metadata(tmp_path, header):
    path = _write(
        tmp_path,
        f"USD,10,sensitive\n",
        header=f"currency,net_pnl,{header}\n",
    )
    with pytest.raises(AccountTradeDataError, match="prohibited credential"):
        _load(path)


@pytest.mark.parametrize("bad", ["nan", "inf", "not-a-number"])
def test_nonfinite_optional_values_are_omitted_without_decimal_crashes(tmp_path, bad):
    path = _write(tmp_path, _complete_row(commission=bad))
    dataset = _load(path)
    assert dataset.trades[0].commission is None
    assert dataset.audit.warning_count >= 1


def test_account_equity_event_is_separate_immutable_and_timezone_aware():
    event = AccountEquityEvent(
        event_id="snapshot-1",
        source="manual_fixture",
        timestamp=datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc),
        currency="usd",
        equity=Decimal("25123.45"),
        balance=Decimal("25050.00"),
        metadata={"provider_sequence": "19"},
    )

    assert event.currency == "USD"
    assert event.equity == Decimal("25123.45")
    with pytest.raises(TypeError):
        event.metadata["provider_sequence"] = "20"
    with pytest.raises(ValueError, match="timezone-aware"):
        AccountEquityEvent(
            event_id="snapshot-2",
            source="manual_fixture",
            timestamp=datetime(2026, 8, 1, 14, 30),
            currency="USD",
            equity=Decimal("25000"),
        )
    with pytest.raises(ValueError, match="finite Decimal"):
        AccountEquityEvent(
            event_id="snapshot-3",
            source="manual_fixture",
            timestamp=datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc),
            currency="USD",
            equity=None,  # type: ignore[arg-type]
        )


def test_intraday_replay_fails_closed_without_equity_events(tmp_path):
    dataset = _load(_write(tmp_path, _complete_row()))
    with pytest.raises(AccountTradeDataError, match="intraday equity"):
        dataset.require_capability("intraday_rule_replay")


@pytest.mark.parametrize("tolerance", [0.01, float("nan"), "-0.01", "nan"])
def test_currency_tolerance_rejects_binary_or_invalid_values(tmp_path, tolerance):
    path = _write(tmp_path, _complete_row())
    with pytest.raises(AccountTradeDataError, match="currency_tolerance"):
        _load(path, currency_tolerance=tolerance)


def test_account_equity_event_nested_metadata_is_read_only():
    # The top-level metadata mapping is read-only, but the nested dicts must be
    # too: a frozen event must not be mutable through its metadata (the
    # shallow _immutable_mapping left nested provider metadata writable).
    event = AccountEquityEvent(
        event_id="snapshot-nested",
        source="manual_fixture",
        timestamp=datetime(2026, 8, 1, 14, 30, tzinfo=timezone.utc),
        currency="USD",
        equity=Decimal("25123.45"),
        metadata={"provider": {"sequence": 17}},
    )
    with pytest.raises(TypeError):
        event.metadata["provider"]["sequence"] = 999
    assert event.metadata["provider"]["sequence"] == 17
