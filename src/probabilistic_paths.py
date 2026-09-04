"""Probabilistic funded-account paths for FARS 1.2 Phase 11C.

This module deliberately does not reuse the legacy Phase 5 Monte Carlo engine.
Real audited trades may be resampled IID only after Phase 10A declares them
``iid_eligible``.  Dependent data use an exploratory circular block bootstrap
(CBB), while unsupported or structurally unstable data are refused.

All probabilities are conditional on the supplied historical dataset,
resampling route, monetary sizing and cost contracts, rule profile, schedule,
and RNG seed.  They are model estimates, not promises about future outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import NormalDist
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from src.funded_rules_v2 import (
    CAP_CLOSED_TRADE_EVENTS,
    AccountRuleInput,
    FundedAccountProfileV2,
    FundedAccountStateV2,
)
from src.ingestion import CanonicalTradeDataset


PATH_RESULT_SCHEMA_VERSION = "1.2-phase11c-path-result-v1"
PATH_ALGORITHM_VERSION = "fars-1.2-phase11c-v1"
BOOTSTRAP_RESULT_SCHEMA_VERSION = "fars-1.2-bootstrap-result-v1"
SUPPORTED_BOOTSTRAP_ALGORITHM_VERSION = "fars-1.2-phase10a-v6"
STATE_IID = "iid_eligible"
STATE_DEPENDENT = "dependent_resampling_candidate"
STATE_UNSUPPORTED = "unsupported_or_inconclusive"

ResamplingRoute = Literal["iid", "cbb"]
PathTerminal = Literal["pass", "breach", "max_trades_reached"]
SizingMode = Literal[
    "fixed_amount",
    "starting_balance_fraction",
    "current_balance_fraction",
]
DatasetRole = Literal["calibration", "validation", "final_oos"]


class PathAnalysisError(ValueError):
    """Raised when a Phase 11C analysis would violate its input contract."""


class PathAnalysisUnsupportedError(PathAnalysisError):
    """Raised when Phase 10A or the rule profile cannot support exact paths."""


def _finite_decimal(
    value: Decimal,
    name: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be strictly positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class RiskSizingPolicy:
    """Explicit conversion from one historical R outcome to money at risk."""

    mode: SizingMode
    value: Decimal
    currency: str
    minimum_amount: Decimal | None = None
    maximum_amount: Decimal | None = None

    def __post_init__(self) -> None:
        if self.mode not in (
            "fixed_amount",
            "starting_balance_fraction",
            "current_balance_fraction",
        ):
            raise ValueError("unsupported risk-sizing mode")
        _finite_decimal(self.value, "value", positive=True)
        if self.mode != "fixed_amount" and self.value > 1:
            raise ValueError("risk fractions must be in (0, 1]")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("risk-sizing currency must be explicit")
        object.__setattr__(self, "currency", self.currency.strip().upper())
        for name in ("minimum_amount", "maximum_amount"):
            amount = getattr(self, name)
            if amount is not None:
                _finite_decimal(amount, name, positive=True)
        if (
            self.minimum_amount is not None
            and self.maximum_amount is not None
            and self.minimum_amount > self.maximum_amount
        ):
            raise ValueError("minimum_amount cannot exceed maximum_amount")

    def amount_for(
        self,
        *,
        starting_balance: Decimal,
        current_balance: Decimal,
        quantum: Decimal,
        rounding: str,
    ) -> Decimal:
        if self.mode == "fixed_amount":
            amount = self.value
        elif self.mode == "starting_balance_fraction":
            amount = starting_balance * self.value
        else:
            amount = current_balance * self.value
        if self.minimum_amount is not None:
            amount = max(amount, self.minimum_amount)
        if self.maximum_amount is not None:
            amount = min(amount, self.maximum_amount)
        amount = amount.quantize(quantum, rounding=rounding)
        if amount <= 0:
            raise PathAnalysisError(
                "risk-sizing policy produced a non-positive monetary risk"
            )
        return amount


@dataclass(frozen=True)
class CostAssumptions:
    """Per-trade monetary costs; modeled slippage remains separately labeled."""

    currency: str
    commission_per_trade: Decimal = Decimal("0")
    exchange_fees_per_trade: Decimal = Decimal("0")
    other_fees_per_trade: Decimal = Decimal("0")
    modeled_slippage_per_trade: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("cost currency must be explicit")
        object.__setattr__(self, "currency", self.currency.strip().upper())
        for name in (
            "commission_per_trade",
            "exchange_fees_per_trade",
            "other_fees_per_trade",
            "modeled_slippage_per_trade",
        ):
            _finite_decimal(getattr(self, name), name, nonnegative=True)

    @property
    def total_per_trade(self) -> Decimal:
        return (
            self.commission_per_trade
            + self.exchange_fees_per_trade
            + self.other_fees_per_trade
            + self.modeled_slippage_per_trade
        )


@dataclass(frozen=True)
class PathSimulationConfig:
    """Fixed simulation, schedule, currency rounding, and data-split contract."""

    n_simulations: int
    max_trades: int
    seed: int
    start_at: datetime
    trades_per_day: int
    confidence_level: float = 0.95
    currency_quantum: Decimal = Decimal("0.01")
    rounding: str = "ROUND_HALF_EVEN"
    dataset_role: DatasetRole = "calibration"

    def __post_init__(self) -> None:
        for name in ("n_simulations", "max_trades", "trades_per_day"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.trades_per_day > 1_440:
            raise ValueError("trades_per_day cannot exceed one trade per minute")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not isinstance(self.start_at, datetime) or not _aware(self.start_at):
            raise ValueError("start_at must be timezone-aware")
        # A trades_per_day block lays trades one minute apart starting at
        # start_at. If that block crosses the calendar day, the simulated
        # trading day is silently inflated (trades meant for one day land on
        # two), which can make a path falsely pass when minimum_trading_days is
        # satisfied by the inflated count. Reject such schedules up front.
        if (
            self.start_at + timedelta(minutes=self.trades_per_day - 1)
        ).date() != self.start_at.date():
            raise ValueError(
                "trades_per_day schedule crosses the day boundary; adjust "
                "start_at (or trades_per_day) so one simulated day fits within "
                "a single calendar day"
            )
        if (
            isinstance(self.confidence_level, bool)
            or not isinstance(self.confidence_level, (int, float))
            or not math.isfinite(float(self.confidence_level))
            or not 0 < float(self.confidence_level) < 1
        ):
            raise ValueError("confidence_level must be finite and in (0, 1)")
        _finite_decimal(self.currency_quantum, "currency_quantum", positive=True)
        quantum_tuple = self.currency_quantum.normalize().as_tuple()
        if quantum_tuple.digits != (1,):
            raise ValueError("currency_quantum must be a positive power of ten")
        try:
            Decimal("1").quantize(self.currency_quantum, rounding=self.rounding)
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported Decimal rounding mode") from exc
        if self.dataset_role not in ("calibration", "validation", "final_oos"):
            raise ValueError("unsupported dataset_role")


@dataclass(frozen=True)
class ProbabilityEstimate:
    count: int
    total: int
    estimate: float
    confidence_interval: tuple[float, float]


@dataclass(frozen=True)
class DistributionSummary:
    count: int
    minimum: float | None
    median: float | None
    mean: float | None
    p95: float | None
    p99: float | None
    maximum: float | None


@dataclass(frozen=True)
class SimulatedPath:
    terminal: PathTerminal
    trades_executed: int
    trading_days: int
    final_balance: Decimal
    max_drawdown: Decimal
    max_loss_budget_consumption: Decimal
    max_losing_streak: int
    max_winning_streak: int
    best_day_consistency_ratio: Decimal | None
    breach_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbabilisticPathResult:
    route: ResamplingRoute
    block_length: int | None
    paths: tuple[SimulatedPath, ...]
    pass_probability: ProbabilityEstimate
    breach_probability: ProbabilityEstimate
    censoring_probability: ProbabilityEstimate
    trades_to_pass: DistributionSummary
    trading_days_to_pass: DistributionSummary
    max_drawdown: DistributionSummary
    loss_budget_consumption: DistributionSummary
    max_losing_streak: DistributionSummary
    max_winning_streak: DistributionSummary
    best_day_consistency: DistributionSummary
    terminal_counts: Mapping[str, int]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    provenance: Mapping[str, Any]
    schema_version: str = PATH_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(self.paths))
        object.__setattr__(self, "terminal_counts", _immutable_mapping(self.terminal_counts))
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "provenance", _immutable_mapping(self.provenance))


@dataclass(frozen=True)
class TradeLimitEstimate:
    estimand: str
    target_probability: float
    trades: int | None
    probability_at_limit: ProbabilityEstimate
    censored: bool


@dataclass(frozen=True)
class RiskSensitivityPoint:
    policy: RiskSizingPolicy
    result: ProbabilisticPathResult


@dataclass(frozen=True)
class RiskSensitivityResult:
    dataset_role: DatasetRole
    points: tuple[RiskSensitivityPoint, ...]
    selection_performed: bool = False
    assumptions: tuple[str, ...] = (
        "risk levels use common random numbers for paired sensitivity comparison",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "assumptions", tuple(self.assumptions))
        if self.selection_performed:
            raise ValueError("Phase 11C sensitivity reports the full curve only")


def _wilson(count: int, total: int, confidence_level: float) -> ProbabilityEstimate:
    p = count / total
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2)
    z2 = z * z
    denominator = 1 + z2 / total
    center = (p + z2 / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total))
    half /= denominator
    return ProbabilityEstimate(count, total, p, (max(0.0, center - half), min(1.0, center + half)))


def _summary(values: Sequence[int | float | Decimal]) -> DistributionSummary:
    if not values:
        return DistributionSummary(0, None, None, None, None, None, None)
    array = np.asarray([float(value) for value in values], dtype=np.float64)
    if not bool(np.all(np.isfinite(array))):
        raise FloatingPointError("distribution contains non-finite values")
    median, p95, p99 = np.quantile(array, [0.5, 0.95, 0.99], method="linear")
    return DistributionSummary(
        count=int(array.size),
        minimum=float(np.min(array)),
        median=float(median),
        mean=float(np.mean(array)),
        p95=float(p95),
        p99=float(p99),
        maximum=float(np.max(array)),
    )


def _validate_route(
    dataset: CanonicalTradeDataset,
    bootstrap_result: Mapping[str, Any],
) -> tuple[ResamplingRoute, int | None]:
    if not isinstance(bootstrap_result, Mapping):
        raise PathAnalysisError("bootstrap_result must be a mapping")
    if bootstrap_result.get("schema_version") != BOOTSTRAP_RESULT_SCHEMA_VERSION:
        raise PathAnalysisError("unsupported Phase 10A result schema")
    provenance = bootstrap_result.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("source_sha256") != (
        dataset.provenance.source_sha256
    ):
        raise PathAnalysisError("bootstrap result does not match the dataset fingerprint")
    if provenance.get("algorithm_version") != SUPPORTED_BOOTSTRAP_ALGORITHM_VERSION:
        raise PathAnalysisError("unsupported Phase 10A algorithm version")
    resolved_mapping = provenance.get("resolved_mapping")
    if not isinstance(resolved_mapping, Mapping) or dict(resolved_mapping) != dict(
        dataset.provenance.resolved_mapping
    ):
        raise PathAnalysisError("bootstrap result does not match the dataset mapping")
    eligibility = bootstrap_result.get("eligibility")
    if not isinstance(eligibility, Mapping):
        raise PathAnalysisError("bootstrap result lacks eligibility metadata")
    state = eligibility.get("state")
    if state == STATE_IID:
        return "iid", None
    if state == STATE_DEPENDENT:
        estimands = bootstrap_result.get("estimands")
        expectancy = estimands.get("expectancy") if isinstance(estimands, Mapping) else None
        block_metadata = (
            expectancy.get("block_length") if isinstance(expectancy, Mapping) else None
        )
        block = block_metadata.get("final") if isinstance(block_metadata, Mapping) else None
        if not isinstance(block, int) or isinstance(block, bool) or not 1 <= block <= len(
            dataset.trades
        ):
            raise PathAnalysisUnsupportedError(
                "dependent route lacks a valid expectancy-influence CBB block length"
            )
        return "cbb", block
    if state == STATE_UNSUPPORTED:
        reasons = eligibility.get("reasons", ())
        rendered = "; ".join(str(reason) for reason in reasons)
        raise PathAnalysisUnsupportedError(
            "Phase 10A does not authorize path resampling"
            + (f": {rendered}" if rendered else "")
        )
    raise PathAnalysisError(f"unknown Phase 10A eligibility state: {state!r}")


def _validate_inputs(
    dataset: CanonicalTradeDataset,
    profile: FundedAccountProfileV2,
    sizing: RiskSizingPolicy,
    costs: CostAssumptions,
    config: PathSimulationConfig,
) -> None:
    if not isinstance(dataset, CanonicalTradeDataset):
        raise TypeError("dataset must be CanonicalTradeDataset")
    dataset.require_capability("core_metrics")
    dataset.require_capability("temporal_analysis")
    if not dataset.trades:
        raise PathAnalysisUnsupportedError("historical dataset has no accepted trades")
    r_values = np.asarray([trade.r_result for trade in dataset.trades], dtype=np.float64)
    if not bool(np.all(np.isfinite(r_values))):
        raise PathAnalysisUnsupportedError("historical R outcomes must be finite")
    if not isinstance(profile, FundedAccountProfileV2):
        raise TypeError("profile must be FundedAccountProfileV2")
    if not profile.enabled or profile.readiness_reasons():
        raise PathAnalysisUnsupportedError(
            "funded-account profile must be enabled with fully resolved semantics"
        )
    if not isinstance(sizing, RiskSizingPolicy):
        raise TypeError("sizing must be RiskSizingPolicy")
    if not isinstance(costs, CostAssumptions):
        raise TypeError("costs must be CostAssumptions")
    if not isinstance(config, PathSimulationConfig):
        raise TypeError("config must be PathSimulationConfig")
    currencies = {profile.currency, sizing.currency, costs.currency}
    if len(currencies) != 1:
        raise PathAnalysisError(
            "profile, risk-sizing, and cost currencies must match exactly"
        )
    unsupported = profile.required_capabilities() - {CAP_CLOSED_TRADE_EVENTS}
    if unsupported:
        raise PathAnalysisUnsupportedError(
            "R-only paths cannot exactly generate required event capabilities: "
            + ", ".join(sorted(unsupported))
        )


def _draw_indices(
    route: ResamplingRoute,
    *,
    n_source: int,
    n_draws: int,
    block_length: int | None,
    seed: int,
    path_index: int,
) -> np.ndarray:
    generator = np.random.Generator(
        np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(path_index,)))
    )
    if route == "iid":
        return generator.integers(0, n_source, size=n_draws)
    assert block_length is not None
    n_blocks = math.ceil(n_draws / block_length)
    starts = generator.integers(0, n_source, size=n_blocks)
    offsets = np.arange(block_length, dtype=np.int64)
    return ((starts[:, None] + offsets) % n_source).reshape(-1)[:n_draws]


def _best_day_ratio(state: FundedAccountStateV2) -> Decimal | None:
    values = [value for _, value in sorted(state.daily_profit.items())]
    rule = state.profile.consistency
    if rule is not None and rule.window_sessions is not None:
        values = values[-rule.window_sessions :]
    best_positive = max((value for value in values if value > 0), default=Decimal(0))
    if rule is None or rule.denominator == "sum_positive_days":
        denominator = sum(
            (value for value in values if value > 0),
            start=Decimal(0),
        )
    elif rule.denominator == "net_evaluation_profit":
        denominator = sum(values, start=Decimal(0))
    else:
        return None
    if denominator <= 0:
        return None
    ratio = best_positive / denominator
    return rule.rounding.apply(ratio) if rule is not None and rule.rounding else ratio


def _loss_budget_consumption(state: FundedAccountStateV2) -> Decimal:
    threshold = state.maximum_loss_threshold
    if threshold is None:
        return Decimal(0)
    rule = state.profile.maximum_loss
    reference = state.balance if rule.reference == "balance" else state.equity
    anchor = (
        state.profile.starting_balance
        if rule.mode == "static"
        else state.high_watermark
    )
    distance = rule.distance.resolve(state.profile.starting_balance, anchor)
    remaining = reference - threshold
    return max(Decimal(0), Decimal(1) - remaining / distance)


def _simulate_one(
    r_values: tuple[Decimal, ...],
    indices: np.ndarray,
    *,
    profile: FundedAccountProfileV2,
    sizing: RiskSizingPolicy,
    costs: CostAssumptions,
    config: PathSimulationConfig,
    path_index: int,
) -> SimulatedPath:
    state = FundedAccountStateV2(
        profile,
        opened_at=config.start_at,
        data_capabilities=frozenset({CAP_CLOSED_TRADE_EVENTS}),
    )
    peak = profile.starting_balance
    max_drawdown = Decimal(0)
    max_consumption = Decimal(0)
    losing_streak = winning_streak = 0
    max_losing_streak = max_winning_streak = 0
    breach_rules: tuple[str, ...] = ()
    terminal: PathTerminal = "max_trades_reached"
    schedule_slot = 0

    for trade_number, source_index in enumerate(indices, start=1):
        day_index, minute_index = divmod(schedule_slot, config.trades_per_day)
        timestamp = config.start_at + timedelta(days=day_index, minutes=minute_index)
        risk = sizing.amount_for(
            starting_balance=profile.starting_balance,
            current_balance=state.balance,
            quantum=config.currency_quantum,
            rounding=config.rounding,
        )
        gross_pnl = (risk * r_values[int(source_index)]).quantize(
            config.currency_quantum,
            rounding=config.rounding,
        )
        net_pnl = (gross_pnl - costs.total_per_trade).quantize(
            config.currency_quantum,
            rounding=config.rounding,
        )
        new_balance = state.balance + net_pnl
        report = state.apply(
            AccountRuleInput(
                timestamp=timestamp,
                event_type="closed_trade",
                balance=new_balance,
                equity=new_balance,
                realized_pnl=net_pnl,
                trade_id=f"sim-{path_index}-trade-{trade_number}",
                metadata={
                    "source_index": int(source_index),
                    "source_r": str(r_values[int(source_index)]),
                    "risk_amount": str(risk),
                    "gross_pnl": str(gross_pnl),
                    "modeled_cost": str(costs.total_per_trade),
                },
            )
        )
        peak = max(peak, state.balance)
        max_drawdown = max(max_drawdown, peak - state.balance)
        max_consumption = max(max_consumption, _loss_budget_consumption(state))
        if net_pnl < 0:
            losing_streak += 1
            winning_streak = 0
        elif net_pnl > 0:
            winning_streak += 1
            losing_streak = 0
        else:
            losing_streak = winning_streak = 0
        max_losing_streak = max(max_losing_streak, losing_streak)
        max_winning_streak = max(max_winning_streak, winning_streak)

        breaches = tuple(event.rule for event in report.events if event.kind == "breach")
        if breaches:
            terminal = "breach"
            breach_rules = breaches
            break
        if report.pass_eligible:
            terminal = "pass"
            break
        schedule_slot += 1
        if any(event.kind == "soft_pause" for event in report.events):
            current_day = schedule_slot // config.trades_per_day
            if schedule_slot % config.trades_per_day:
                current_day += 1
            schedule_slot = current_day * config.trades_per_day

    return SimulatedPath(
        terminal=terminal,
        trades_executed=len(state.history),
        trading_days=len(state.trading_days),
        final_balance=state.balance,
        max_drawdown=max_drawdown,
        max_loss_budget_consumption=max_consumption,
        max_losing_streak=max_losing_streak,
        max_winning_streak=max_winning_streak,
        best_day_consistency_ratio=_best_day_ratio(state),
        breach_rules=breach_rules,
    )


def run_probabilistic_paths(
    dataset: CanonicalTradeDataset,
    bootstrap_result: Mapping[str, Any],
    profile: FundedAccountProfileV2,
    sizing: RiskSizingPolicy,
    costs: CostAssumptions,
    config: PathSimulationConfig,
) -> ProbabilisticPathResult:
    """Run conditional Phase 11C account paths under one fixed risk policy."""

    _validate_inputs(dataset, profile, sizing, costs, config)
    route, block_length = _validate_route(dataset, bootstrap_result)
    r_values = tuple(Decimal(str(trade.r_result)) for trade in dataset.trades)
    paths: list[SimulatedPath] = []
    for path_index in range(config.n_simulations):
        indices = _draw_indices(
            route,
            n_source=len(r_values),
            n_draws=config.max_trades,
            block_length=block_length,
            seed=config.seed,
            path_index=path_index,
        )
        paths.append(
            _simulate_one(
                r_values,
                indices,
                profile=profile,
                sizing=sizing,
                costs=costs,
                config=config,
                path_index=path_index,
            )
        )

    counts = {
        terminal: sum(path.terminal == terminal for path in paths)
        for terminal in ("pass", "breach", "max_trades_reached")
    }
    total = len(paths)
    passed = [path for path in paths if path.terminal == "pass"]
    ratios = [
        path.best_day_consistency_ratio
        for path in paths
        if path.best_day_consistency_ratio is not None
    ]
    assumptions = (
        f"{route} resampling authorized by Phase 10A eligibility",
        "historical R outcomes are conditionally representative under the selected route",
        "one fixed trades-per-day schedule is applied to every simulated path",
        "closed-trade balance equals equity; no intratrade excursion is reconstructed",
        "currency PnL equals rounded risk times R minus explicit per-trade costs",
        "Monte Carlo intervals quantify simulation error only",
    )
    limitations = (
        "ordinary empirical resampling cannot generate unseen tail outcomes",
        "future trading cadence, regime changes, and parameter uncertainty are not modeled",
        "CBB results are exploratory and assume plausible stationary short-memory dependence",
    ) if route == "cbb" else (
        "ordinary empirical resampling cannot generate unseen tail outcomes",
        "future trading cadence, regime changes, and parameter uncertainty are not modeled",
    )
    provenance = {
        "dataset_sha256": dataset.provenance.source_sha256,
        "bootstrap_schema_version": bootstrap_result["schema_version"],
        "bootstrap_algorithm_version": bootstrap_result["provenance"].get(
            "algorithm_version"
        ),
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "path_algorithm_version": PATH_ALGORITHM_VERSION,
        "rng": {
            "master_entropy": config.seed,
            "bit_generator": "PCG64",
            "path_derivation": "SeedSequence(master_entropy, spawn_key=(path_index,))",
        },
        "risk_sizing": {
            "mode": sizing.mode,
            "value": str(sizing.value),
            "currency": sizing.currency,
            "minimum_amount": (
                None if sizing.minimum_amount is None else str(sizing.minimum_amount)
            ),
            "maximum_amount": (
                None if sizing.maximum_amount is None else str(sizing.maximum_amount)
            ),
        },
        "costs": {
            "currency": costs.currency,
            "commission_per_trade": str(costs.commission_per_trade),
            "exchange_fees_per_trade": str(costs.exchange_fees_per_trade),
            "other_fees_per_trade": str(costs.other_fees_per_trade),
            "modeled_slippage_per_trade": str(costs.modeled_slippage_per_trade),
        },
        "simulation": {
            "n_simulations": config.n_simulations,
            "max_trades": config.max_trades,
            "trades_per_day": config.trades_per_day,
            "start_at": config.start_at.isoformat(),
            "confidence_level": config.confidence_level,
            "currency_quantum": str(config.currency_quantum),
            "rounding": config.rounding,
            "dataset_role": config.dataset_role,
        },
    }
    return ProbabilisticPathResult(
        route=route,
        block_length=block_length,
        paths=tuple(paths),
        pass_probability=_wilson(counts["pass"], total, config.confidence_level),
        breach_probability=_wilson(counts["breach"], total, config.confidence_level),
        censoring_probability=_wilson(
            counts["max_trades_reached"], total, config.confidence_level
        ),
        trades_to_pass=_summary([path.trades_executed for path in passed]),
        trading_days_to_pass=_summary([path.trading_days for path in passed]),
        max_drawdown=_summary([path.max_drawdown for path in paths]),
        loss_budget_consumption=_summary(
            [path.max_loss_budget_consumption for path in paths]
        ),
        max_losing_streak=_summary([path.max_losing_streak for path in paths]),
        max_winning_streak=_summary([path.max_winning_streak for path in paths]),
        best_day_consistency=_summary(ratios),
        terminal_counts=counts,
        assumptions=assumptions,
        limitations=limitations,
        provenance=provenance,
    )


def estimate_trades_for_pass_probability(
    result: ProbabilisticPathResult,
    target_probability: float,
    *,
    require_ci_lower_bound: bool = False,
) -> TradeLimitEstimate:
    """Estimate the first horizon where unconditional P(pass by h) reaches p."""

    if not isinstance(result, ProbabilisticPathResult):
        raise TypeError("result must be ProbabilisticPathResult")
    if (
        isinstance(target_probability, bool)
        or not isinstance(target_probability, (int, float))
        or not math.isfinite(float(target_probability))
        or not 0 < float(target_probability) < 1
    ):
        raise ValueError("target_probability must be finite and in (0, 1)")
    confidence = float(result.provenance["simulation"]["confidence_level"])
    total = len(result.paths)
    max_horizon = int(result.provenance["simulation"]["max_trades"])
    selected: tuple[int, ProbabilityEstimate] | None = None
    last = _wilson(0, total, confidence)
    for horizon in range(1, max_horizon + 1):
        count = sum(
            path.terminal == "pass" and path.trades_executed <= horizon
            for path in result.paths
        )
        estimate = _wilson(count, total, confidence)
        last = estimate
        criterion = (
            estimate.confidence_interval[0]
            if require_ci_lower_bound
            else estimate.estimate
        )
        if criterion >= target_probability:
            selected = (horizon, estimate)
            break
    if selected is None:
        return TradeLimitEstimate(
            estimand=(
                "minimum_h_with_wilson_lower_bound_p_pass_by_h_at_least_p"
                if require_ci_lower_bound
                else "minimum_h_with_estimated_p_pass_by_h_at_least_p"
            ),
            target_probability=float(target_probability),
            trades=None,
            probability_at_limit=last,
            censored=True,
        )
    return TradeLimitEstimate(
        estimand=(
            "minimum_h_with_wilson_lower_bound_p_pass_by_h_at_least_p"
            if require_ci_lower_bound
            else "minimum_h_with_estimated_p_pass_by_h_at_least_p"
        ),
        target_probability=float(target_probability),
        trades=selected[0],
        probability_at_limit=selected[1],
        censored=False,
    )


def run_risk_sensitivity(
    dataset: CanonicalTradeDataset,
    bootstrap_result: Mapping[str, Any],
    profile: FundedAccountProfileV2,
    risk_grid: Sequence[RiskSizingPolicy],
    costs: CostAssumptions,
    config: PathSimulationConfig,
) -> RiskSensitivityResult:
    """Report a predeclared full risk curve without choosing an optimum."""

    policies = tuple(risk_grid)
    if not policies:
        raise ValueError("risk_grid must contain at least one policy")
    if len(policies) != len(set(policies)):
        raise ValueError("risk_grid must not contain duplicate policies")
    if config.dataset_role != "calibration" and len(policies) > 1:
        raise PathAnalysisError(
            "validation and final OOS data cannot evaluate a multi-level tuning grid"
        )
    points = tuple(
        RiskSensitivityPoint(
            policy,
            run_probabilistic_paths(
                dataset,
                bootstrap_result,
                profile,
                policy,
                costs,
                replace(config),
            ),
        )
        for policy in policies
    )
    return RiskSensitivityResult(config.dataset_role, points)


__all__ = [
    "CostAssumptions",
    "DistributionSummary",
    "PATH_ALGORITHM_VERSION",
    "PATH_RESULT_SCHEMA_VERSION",
    "PathAnalysisError",
    "PathAnalysisUnsupportedError",
    "PathSimulationConfig",
    "ProbabilityEstimate",
    "ProbabilisticPathResult",
    "RiskSensitivityPoint",
    "RiskSensitivityResult",
    "RiskSizingPolicy",
    "SUPPORTED_BOOTSTRAP_ALGORITHM_VERSION",
    "SimulatedPath",
    "TradeLimitEstimate",
    "estimate_trades_for_pass_probability",
    "run_probabilistic_paths",
    "run_risk_sensitivity",
]
