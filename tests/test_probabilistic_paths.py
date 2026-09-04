"""Deterministic acceptance tests for Phase 11C probabilistic paths."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.bootstrap import analyze_bootstrap
from src.funded_profiles import rapid_25k_profile
from src.funded_rules_v2 import (
    ConsistencyRule,
    DailyLossRule,
    FundedAccountProfileV2,
    MaximumLossRule,
    OperationalRules,
    ProfitTargetRule,
    RoundingPolicy,
    RuleAmount,
    RuleSource,
)
from src.ingestion import load_trade_csv
from src.probabilistic_paths import (
    BOOTSTRAP_RESULT_SCHEMA_VERSION,
    CostAssumptions,
    PathAnalysisError,
    PathAnalysisUnsupportedError,
    PathSimulationConfig,
    RiskSizingPolicy,
    estimate_trades_for_pass_probability,
    run_probabilistic_paths,
    run_risk_sensitivity,
)


UTC = timezone.utc
SOURCE = RuleSource("https://example.com/rules/v1", date(2026, 8, 30))


def _dataset(tmp_path, values):
    rows = ["timestamp,r_result"]
    start = datetime(2024, 1, 1, 12, tzinfo=UTC)
    for index, value in enumerate(values):
        timestamp = (start + timedelta(hours=index)).isoformat()
        rows.append(f"{timestamp},{value}")
    path = tmp_path / "trades.csv"
    path.write_text("\n".join(rows), encoding="utf-8")
    return load_trade_csv(path, outcomes_finalized=True)


def _bootstrap(dataset, state="iid_eligible", *, block_length=None, reasons=()):
    block = None if block_length is None else {"final": block_length}
    return {
        "schema_version": BOOTSTRAP_RESULT_SCHEMA_VERSION,
        "eligibility": {"state": state, "reasons": list(reasons)},
        "estimands": {"expectancy": {"block_length": block}},
        "provenance": {
            "source_sha256": dataset.provenance.source_sha256,
            "algorithm_version": "fars-1.2-phase10a-v6",
            "resolved_mapping": dict(dataset.provenance.resolved_mapping),
        },
    }


def _profile(**changes):
    values = {
        "profile_id": "test-25k",
        "profile_version": "1",
        "provider": "Test Provider",
        "currency": "USD",
        "starting_balance": Decimal("25000"),
        "profit_target": ProfitTargetRule(
            RuleAmount("absolute", Decimal("100"))
        ),
        "maximum_loss": MaximumLossRule(
            RuleAmount("absolute", Decimal("100")),
            mode="static",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
        ),
        "session_timezone": "UTC",
        "session_boundary": time(0),
        "operational": OperationalRules(news_trading_allowed=True),
        "rule_sources": (SOURCE,),
    }
    values.update(changes)
    return FundedAccountProfileV2(**values)


def _config(**changes):
    values = {
        "n_simulations": 32,
        "max_trades": 10,
        "seed": 17,
        "start_at": datetime(2026, 9, 1, 12, tzinfo=UTC),
        "trades_per_day": 1,
    }
    values.update(changes)
    return PathSimulationConfig(**values)


def _sizing(amount="100"):
    return RiskSizingPolicy("fixed_amount", Decimal(amount), "USD")


def _costs(**changes):
    return CostAssumptions("USD", **changes)


def test_known_positive_process_always_passes_on_first_trade(tmp_path):
    dataset = _dataset(tmp_path, [1, 2, 1, 2])
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset),
        _profile(),
        _sizing(),
        _costs(),
        _config(),
    )

    assert result.route == "iid"
    assert result.terminal_counts == {
        "pass": 32,
        "breach": 0,
        "max_trades_reached": 0,
    }
    assert result.pass_probability.estimate == 1.0
    assert result.trades_to_pass.minimum == result.trades_to_pass.maximum == 1.0
    assert sum(
        estimate.estimate
        for estimate in (
            result.pass_probability,
            result.breach_probability,
            result.censoring_probability,
        )
    ) == 1.0


def test_accepts_an_actual_matching_phase10a_iid_result(tmp_path):
    values = np.random.default_rng(7).normal(size=120)
    dataset = _dataset(tmp_path, values)
    bootstrap = analyze_bootstrap(dataset, master_seed=41, B=2000)

    result = run_probabilistic_paths(
        dataset,
        bootstrap,
        _profile(),
        _sizing(),
        _costs(),
        _config(n_simulations=5),
    )

    assert bootstrap["eligibility"]["state"] == "iid_eligible"
    assert result.route == "iid"
    assert result.provenance["bootstrap_algorithm_version"] == (
        "fars-1.2-phase10a-v6"
    )


def test_known_negative_process_always_breaches_on_first_trade(tmp_path):
    dataset = _dataset(tmp_path, [-1, -2, -1, -2])
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset),
        _profile(),
        _sizing(),
        _costs(),
        _config(),
    )

    assert result.breach_probability.estimate == 1.0
    assert all(path.breach_rules == ("maximum_loss",) for path in result.paths)
    assert result.max_drawdown.minimum == Decimal("100")
    assert result.loss_budget_consumption.minimum == Decimal("1")


def test_paths_are_bit_reproducible_and_prefix_stable(tmp_path):
    dataset = _dataset(tmp_path, [-1, 2, -0.5, 1.5])
    args = (
        dataset,
        _bootstrap(dataset),
        _profile(
            profit_target=ProfitTargetRule(RuleAmount("absolute", Decimal("500"))),
            maximum_loss=MaximumLossRule(
                RuleAmount("absolute", Decimal("500")),
                mode="static",
                reference="balance",
                update_cadence="closed_trade",
                monitoring_cadence="closed_trade",
                breach_boundary="<=",
            ),
        ),
        _sizing(),
        _costs(),
    )
    short = run_probabilistic_paths(*args, _config(n_simulations=8))
    long = run_probabilistic_paths(*args, _config(n_simulations=20))
    repeated = run_probabilistic_paths(*args, _config(n_simulations=8))

    assert short.paths == repeated.paths
    assert short.paths == long.paths[:8]
    assert short.provenance["rng"]["master_entropy"] == 17


def test_cbb_route_uses_expectancy_block_length_and_is_reproducible(tmp_path):
    dataset = _dataset(tmp_path, [2, -1])
    profile = _profile(
        profit_target=ProfitTargetRule(RuleAmount("absolute", Decimal("1000"))),
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("100")),
            mode="trailing",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
            threshold_ceiling=Decimal("25100"),
        ),
    )
    config = _config(n_simulations=1, max_trades=2, seed=1, trades_per_day=2)
    first = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset, "dependent_resampling_candidate", block_length=2),
        profile,
        _sizing(),
        _costs(),
        config,
    )
    second = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset, "dependent_resampling_candidate", block_length=2),
        profile,
        _sizing(),
        _costs(),
        config,
    )

    assert first.route == "cbb"
    assert first.block_length == 2
    assert first.paths == second.paths
    assert first.paths[0].terminal == "breach"
    assert first.paths[0].final_balance == Decimal("25100.00")
    assert "stationary short-memory" in " ".join(first.limitations)


def test_costs_consistency_and_minimum_days_are_applied_along_path(tmp_path):
    dataset = _dataset(tmp_path, [1, 1])
    consistency = ConsistencyRule(
        maximum_ratio=Decimal("0.50"),
        denominator="sum_positive_days",
        rounding=RoundingPolicy(4),
        window_sessions=None,
        acceptance_boundary="<=",
        action="block_pass",
        non_positive_behavior="block_pass",
    )
    profile = _profile(
        profit_target=ProfitTargetRule(RuleAmount("absolute", Decimal("180"))),
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="static",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
        ),
        minimum_trading_days=2,
        consistency=consistency,
    )
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset),
        profile,
        _sizing(),
        _costs(commission_per_trade=Decimal("10")),
        _config(n_simulations=4, max_trades=3),
    )

    assert result.pass_probability.estimate == 1.0
    assert result.trades_to_pass.minimum == 2.0
    assert result.trading_days_to_pass.minimum == 2.0
    assert result.best_day_consistency.minimum == 0.5
    assert all(path.final_balance == Decimal("25180.00") for path in result.paths)


def test_soft_daily_pause_moves_next_trade_to_next_session(tmp_path):
    dataset = _dataset(tmp_path, [-1, 2])
    profile = _profile(
        profit_target=ProfitTargetRule(RuleAmount("absolute", Decimal("100"))),
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("1000")),
            mode="static",
            reference="balance",
            update_cadence="closed_trade",
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
        ),
        daily_loss=DailyLossRule(
            RuleAmount("absolute", Decimal("100")),
            reference="balance",
            reset_timezone="UTC",
            session_boundary=time(0),
            monitoring_cadence="closed_trade",
            breach_boundary="<=",
            action="soft_pause",
        ),
    )
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset, "dependent_resampling_candidate", block_length=2),
        profile,
        _sizing(),
        _costs(),
        _config(n_simulations=1, max_trades=2, seed=1, trades_per_day=5),
    )

    assert result.paths[0].terminal == "pass"
    assert result.paths[0].trading_days == 2


@pytest.mark.parametrize(
    ("state", "reasons"),
    [
        ("unsupported_or_inconclusive", ("insufficient_sample",)),
        ("unsupported_or_inconclusive", ("structural_change_evidence",)),
    ],
)
def test_unsupported_and_structural_change_routes_are_refused(
    tmp_path, state, reasons
):
    dataset = _dataset(tmp_path, [-1, 1])

    with pytest.raises(PathAnalysisUnsupportedError, match=reasons[0]):
        run_probabilistic_paths(
            dataset,
            _bootstrap(dataset, state, reasons=reasons),
            _profile(),
            _sizing(),
            _costs(),
            _config(),
        )


def test_bootstrap_fingerprint_must_match_dataset(tmp_path):
    dataset = _dataset(tmp_path, [-1, 1])
    bootstrap = _bootstrap(dataset)
    bootstrap["provenance"]["source_sha256"] = "0" * 64

    with pytest.raises(PathAnalysisError, match="fingerprint"):
        run_probabilistic_paths(
            dataset,
            bootstrap,
            _profile(),
            _sizing(),
            _costs(),
            _config(),
        )


def test_bootstrap_algorithm_and_mapping_must_match(tmp_path):
    dataset = _dataset(tmp_path, [-1, 1])
    old_algorithm = _bootstrap(dataset)
    old_algorithm["provenance"]["algorithm_version"] = "fars-1.2-phase10a-v5"

    with pytest.raises(PathAnalysisError, match="algorithm version"):
        run_probabilistic_paths(
            dataset,
            old_algorithm,
            _profile(),
            _sizing(),
            _costs(),
            _config(),
        )

    wrong_mapping = _bootstrap(dataset)
    wrong_mapping["provenance"]["resolved_mapping"] = {"other": "r_result"}
    with pytest.raises(PathAnalysisError, match="dataset mapping"):
        run_probabilistic_paths(
            dataset,
            wrong_mapping,
            _profile(),
            _sizing(),
            _costs(),
            _config(),
        )


def test_disabled_profile_and_missing_intraday_paths_fail_closed(tmp_path):
    dataset = _dataset(tmp_path, [-1, 1])
    bootstrap = _bootstrap(dataset)

    with pytest.raises(PathAnalysisUnsupportedError, match="enabled"):
        run_probabilistic_paths(
            dataset,
            bootstrap,
            rapid_25k_profile(),
            _sizing(),
            _costs(),
            _config(),
        )

    intraday = _profile(
        maximum_loss=MaximumLossRule(
            RuleAmount("absolute", Decimal("100")),
            mode="trailing",
            reference="equity",
            update_cadence="intraday_event",
            monitoring_cadence="intraday_event",
            breach_boundary="<=",
        )
    )
    with pytest.raises(PathAnalysisUnsupportedError, match="intraday_events"):
        run_probabilistic_paths(
            dataset,
            bootstrap,
            intraday,
            _sizing(),
            _costs(),
            _config(),
        )


def test_trade_limit_names_estimand_and_reports_horizon_censoring(tmp_path):
    dataset = _dataset(tmp_path, [1, 2])
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset),
        _profile(),
        _sizing(),
        _costs(),
        _config(n_simulations=20),
    )

    limit = estimate_trades_for_pass_probability(result, 0.90)
    conservative = estimate_trades_for_pass_probability(
        result, 0.90, require_ci_lower_bound=True
    )
    assert limit.estimand == "minimum_h_with_estimated_p_pass_by_h_at_least_p"
    assert limit.trades == 1
    assert not limit.censored
    assert conservative.trades is None
    assert conservative.censored


def test_risk_grid_reports_full_curve_and_cannot_tune_on_holdout(tmp_path):
    dataset = _dataset(tmp_path, [-1, 2])
    grid = (_sizing("50"), _sizing("100"))
    calibration = run_risk_sensitivity(
        dataset,
        _bootstrap(dataset),
        _profile(),
        grid,
        _costs(),
        _config(n_simulations=10),
    )

    assert len(calibration.points) == 2
    assert not calibration.selection_performed
    assert [point.policy.value for point in calibration.points] == [
        Decimal("50"),
        Decimal("100"),
    ]
    assert all(
        point.result.provenance["rng"]["master_entropy"] == 17
        for point in calibration.points
    )

    with pytest.raises(PathAnalysisError, match="cannot evaluate"):
        run_risk_sensitivity(
            dataset,
            _bootstrap(dataset),
            _profile(),
            grid,
            _costs(),
            replace(_config(), dataset_role="validation"),
        )


def test_currency_mismatch_is_refused(tmp_path):
    dataset = _dataset(tmp_path, [-1, 1])

    with pytest.raises(PathAnalysisError, match="currencies"):
        run_probabilistic_paths(
            dataset,
            _bootstrap(dataset),
            _profile(),
            RiskSizingPolicy("fixed_amount", Decimal("100"), "EUR"),
            _costs(),
            _config(),
        )


def test_currency_quantum_must_be_a_power_of_ten():
    with pytest.raises(ValueError, match="power of ten"):
        _config(currency_quantum=Decimal("0.03"))


def test_run_rejects_schedule_that_crosses_the_day_boundary(tmp_path):
    # UTC session with a midnight boundary: two trades from 23:59 cross into the
    # next session, silently inflating trading_days and enabling a false pass
    # when minimum_trading_days is satisfied by the inflated count.
    dataset = _dataset(tmp_path, [1, 1])
    with pytest.raises(PathAnalysisError, match="session boundary"):
        run_probabilistic_paths(
            dataset,
            _bootstrap(dataset),
            _profile(),
            _sizing(),
            _costs(),
            _config(
                start_at=datetime(2026, 9, 1, 23, 59, tzinfo=UTC),
                trades_per_day=2,
                max_trades=2,
            ),
        )


def test_run_rejects_schedule_that_straddles_a_nonmidnight_session(tmp_path):
    # A funding-account session boundary at 17:00 is not a calendar-day check:
    # a two-trade block starting at 16:59 falls on both sides of the session and
    # must be rejected (the simpler calendar-day guard would miss this).
    dataset = _dataset(tmp_path, [1, 1])
    profile = _profile(session_boundary=time(17))
    with pytest.raises(PathAnalysisError, match="session boundary"):
        run_probabilistic_paths(
            dataset,
            _bootstrap(dataset),
            profile,
            _sizing(),
            _costs(),
            _config(
                start_at=datetime(2026, 9, 1, 16, 59, tzinfo=UTC),
                trades_per_day=2,
                max_trades=2,
            ),
        )


def test_config_accepts_near_boundary_schedule_that_stays_within_the_day():
    # Single trade at 23:59 is a valid schedule (no crossing).
    config = PathSimulationConfig(
        n_simulations=1,
        max_trades=2,
        seed=0,
        start_at=datetime(2026, 9, 1, 23, 59, tzinfo=UTC),
        trades_per_day=1,
    )
    assert config.trades_per_day == 1
    # A multi-trade block well inside the day is valid.
    _config(trades_per_day=5)


def test_path_result_provenance_nested_mapping_is_read_only(tmp_path):
    dataset = _dataset(tmp_path, [1, 2, 1, 2])
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset),
        _profile(),
        _sizing(),
        _costs(),
        _config(n_simulations=2, max_trades=2),
    )
    # The top-level provenance is already a read-only mapping, but the nested
    # dicts ("rng", "risk_sizing", ...) must be read-only too: a frozen result
    # must not be mutable through its provenance, and estimate_* must never
    # observe a mutated provenance.
    with pytest.raises(TypeError):
        result.provenance["rng"]["master_entropy"] = 999
    assert result.provenance["rng"]["master_entropy"] == 17


def test_path_result_rejects_non_mapping_provenance(tmp_path):
    dataset = _dataset(tmp_path, [1, 2, 1, 2])
    result = run_probabilistic_paths(
        dataset,
        _bootstrap(dataset),
        _profile(),
        _sizing(),
        _costs(),
        _config(n_simulations=2, max_trades=2),
    )
    with pytest.raises(TypeError):
        replace(result, provenance=42)
