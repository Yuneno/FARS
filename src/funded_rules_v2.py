"""Generic funded-account rule engine for FARS 1.2 Phase 11B.

The legacy :class:`src.types.FundedAccountRules` and :class:`src.account.AccountState`
remain unchanged.  This module models monetary rules with :class:`~decimal.Decimal`,
keeps immutable provider profiles separate from mutable replay state, and fails
closed when the supplied event stream cannot evaluate a rule exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


AmountUnit = Literal["absolute", "percentage"]
PercentageBase = Literal["starting_balance", "reference_value"]
AccountReference = Literal["balance", "equity"]
ComparisonBoundary = Literal[">=", ">", "<=", "<"]
LossBoundary = Literal["<=", "<"]
UpdateCadence = Literal["closed_trade", "end_of_day", "intraday_event"]
LossAction = Literal["breach", "soft_pause"]
ConsistencyAction = Literal["breach", "block_pass"]
ConsistencyDenominator = Literal["sum_positive_days", "net_evaluation_profit"]
NonPositiveBehavior = Literal["not_evaluable", "block_pass", "breach"]
EventType = Literal["closed_trade", "end_of_day", "intraday"]
EvaluationKind = Literal[
    "breach",
    "soft_pause",
    "consistency_block",
    "pass_blocked",
    "not_evaluable",
    "pass_eligible",
    "in_progress",
]

CAP_CLOSED_TRADE_EVENTS = "closed_trade_events"
CAP_END_OF_DAY_EVENTS = "end_of_day_events"
CAP_INTRADAY_EVENTS = "intraday_events"
CAP_POSITION_SIZES = "position_sizes"
CAP_OPEN_POSITION_STATUS = "open_position_status"
CAP_NEWS_FLAGS = "news_flags"
CAP_TRADE_OPEN_TIMESTAMPS = "trade_open_timestamps"

RULE_ENGINE_SCHEMA_VERSION = "1.2-phase11b-funded-rules-v2"

_SUPPORTED_CAPABILITIES = frozenset(
    {
        CAP_CLOSED_TRADE_EVENTS,
        CAP_END_OF_DAY_EVENTS,
        CAP_INTRADAY_EVENTS,
        CAP_POSITION_SIZES,
        CAP_TRADE_OPEN_TIMESTAMPS,
        CAP_OPEN_POSITION_STATUS,
        CAP_NEWS_FLAGS,
    }
)
_ROUNDING_MODES = frozenset(
    {
        "ROUND_CEILING",
        "ROUND_DOWN",
        "ROUND_FLOOR",
        "ROUND_HALF_DOWN",
        "ROUND_HALF_EVEN",
        "ROUND_HALF_UP",
        "ROUND_UP",
        "ROUND_05UP",
    }
)
_EVENT_PRIORITY = {
    "breach": 0,
    "soft_pause": 1,
    "consistency_block": 2,
    "pass_blocked": 2,
    "not_evaluable": 3,
    "pass_eligible": 4,
    "in_progress": 5,
}
_RULE_PRIORITY = {
    "profile": 0,
    "maximum_loss": 10,
    "daily_loss": 20,
    "position_size": 30,
    "permitted_session": 31,
    "forced_close": 32,
    "news": 33,
    "inactivity": 34,
    "consistency": 40,
    "profit_target": 50,
}


class FundedRuleError(ValueError):
    """Raised when a v2 profile or replay event is unsafe to interpret."""


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _finite_decimal(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be strictly positive")


def _positive_int(value: int | None, name: str) -> None:
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer or None")


def _timezone(value: str | None, name: str) -> ZoneInfo | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty IANA timezone or None")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"{name} is not an available IANA timezone: {value!r}") from exc


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


from src.session_calendar import session_date


def _session_date(
    timestamp: datetime,
    timezone_name: str,
    boundary: time,
    skip_weekends: bool = False,
) -> date:
    d = session_date(
        timestamp,
        tz=timezone_name,
        reset_hour=boundary,
        skip_weekends=skip_weekends,
    )
    assert d is not None
    return d


def _cadence_capability(cadence: UpdateCadence) -> str:
    return {
        "closed_trade": CAP_CLOSED_TRADE_EVENTS,
        "end_of_day": CAP_END_OF_DAY_EVENTS,
        "intraday_event": CAP_INTRADAY_EVENTS,
    }[cadence]


def _cadence_matches(cadence: UpdateCadence, event_type: EventType) -> bool:
    if cadence == "closed_trade":
        return event_type == "closed_trade"
    if cadence == "end_of_day":
        return event_type == "end_of_day"
    return True


@dataclass(frozen=True)
class RuleAmount:
    """An absolute currency amount or an explicitly based percentage."""

    unit: AmountUnit
    value: Decimal
    percentage_base: PercentageBase = "starting_balance"

    def __post_init__(self) -> None:
        if self.unit not in ("absolute", "percentage"):
            raise ValueError("unit must be 'absolute' or 'percentage'")
        _finite_decimal(self.value, "value", positive=True)
        if self.percentage_base not in ("starting_balance", "reference_value"):
            raise ValueError("unsupported percentage_base")
        if self.unit == "absolute" and self.percentage_base != "starting_balance":
            raise ValueError("absolute amounts cannot claim a percentage base")

    def resolve(self, starting_balance: Decimal, reference_value: Decimal) -> Decimal:
        """Return the exact Decimal distance represented by this rule."""

        if self.unit == "absolute":
            return self.value
        base = (
            starting_balance
            if self.percentage_base == "starting_balance"
            else reference_value
        )
        return base * self.value


@dataclass(frozen=True)
class ProfitTargetRule:
    target: RuleAmount
    reference: AccountReference = "balance"
    boundary: Literal[">=", ">"] = ">="

    def __post_init__(self) -> None:
        if self.reference not in ("balance", "equity"):
            raise ValueError("profit target reference must be balance or equity")
        if self.boundary not in (">=", ">"):
            raise ValueError("profit target boundary must be '>=' or '>'")
        if self.target.unit == "percentage" and (
            self.target.percentage_base != "starting_balance"
        ):
            raise ValueError("profit-target percentages must use starting_balance")


@dataclass(frozen=True)
class MaximumLossRule:
    distance: RuleAmount
    mode: Literal["static", "trailing"]
    reference: AccountReference
    update_cadence: UpdateCadence | None
    monitoring_cadence: UpdateCadence | None
    breach_boundary: LossBoundary | None
    threshold_ceiling: Decimal | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("static", "trailing"):
            raise ValueError("maximum-loss mode must be static or trailing")
        if self.reference not in ("balance", "equity"):
            raise ValueError("maximum-loss reference must be balance or equity")
        if self.update_cadence not in (
            None,
            "closed_trade",
            "end_of_day",
            "intraday_event",
        ):
            raise ValueError("unsupported maximum-loss update cadence")
        if self.monitoring_cadence not in (
            None,
            "closed_trade",
            "end_of_day",
            "intraday_event",
        ):
            raise ValueError("unsupported maximum-loss monitoring cadence")
        if self.breach_boundary not in (None, "<=", "<"):
            raise ValueError("maximum-loss boundary must be '<=', '<', or None")
        if self.threshold_ceiling is not None:
            _finite_decimal(self.threshold_ceiling, "threshold_ceiling")
        if self.mode == "static" and self.threshold_ceiling is not None:
            raise ValueError("a static maximum-loss threshold cannot trail to a ceiling")
        if self.distance.unit == "percentage" and self.distance.value >= 1:
            raise ValueError("maximum-loss percentage must be in (0, 1)")


@dataclass(frozen=True)
class DailyLossRule:
    distance: RuleAmount
    reference: AccountReference
    reset_timezone: str
    session_boundary: time
    monitoring_cadence: UpdateCadence
    breach_boundary: LossBoundary
    action: LossAction

    def __post_init__(self) -> None:
        if self.reference not in ("balance", "equity"):
            raise ValueError("daily-loss reference must be balance or equity")
        _timezone(self.reset_timezone, "reset_timezone")
        if not isinstance(self.session_boundary, time):
            raise ValueError("session_boundary must be datetime.time")
        if self.session_boundary.tzinfo is not None:
            raise ValueError("session_boundary must be local wall time without tzinfo")
        if self.monitoring_cadence not in (
            "closed_trade",
            "end_of_day",
            "intraday_event",
        ):
            raise ValueError("unsupported daily-loss monitoring cadence")
        if self.breach_boundary not in ("<=", "<"):
            raise ValueError("daily-loss boundary must be '<=' or '<'")
        if self.action not in ("breach", "soft_pause"):
            raise ValueError("daily-loss action must be breach or soft_pause")
        if self.distance.unit == "percentage" and self.distance.value >= 1:
            raise ValueError("daily-loss percentage must be in (0, 1)")


@dataclass(frozen=True)
class RoundingPolicy:
    decimal_places: int
    mode: str = "ROUND_HALF_EVEN"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.decimal_places, int)
            or isinstance(self.decimal_places, bool)
            or self.decimal_places < 0
        ):
            raise ValueError("decimal_places must be a non-negative integer")
        if self.mode not in _ROUNDING_MODES:
            raise ValueError(f"unsupported Decimal rounding mode {self.mode!r}")

    def apply(self, value: Decimal) -> Decimal:
        quantum = Decimal(1).scaleb(-self.decimal_places)
        return value.quantize(quantum, rounding=self.mode)


@dataclass(frozen=True)
class ConsistencyRule:
    maximum_ratio: Decimal
    denominator: ConsistencyDenominator | None
    rounding: RoundingPolicy | None
    window_sessions: int | None
    acceptance_boundary: Literal["<=", "<"]
    action: ConsistencyAction
    non_positive_behavior: NonPositiveBehavior

    def __post_init__(self) -> None:
        _finite_decimal(self.maximum_ratio, "maximum_ratio", positive=True)
        if self.maximum_ratio > 1:
            raise ValueError("maximum_ratio must be in (0, 1]")
        if self.denominator not in (
            None,
            "sum_positive_days",
            "net_evaluation_profit",
        ):
            raise ValueError("unsupported consistency denominator")
        _positive_int(self.window_sessions, "window_sessions")
        if self.acceptance_boundary not in ("<=", "<"):
            raise ValueError("consistency boundary must be '<=' or '<'")
        if self.action not in ("breach", "block_pass"):
            raise ValueError("consistency action must be breach or block_pass")
        if self.non_positive_behavior not in (
            "not_evaluable",
            "block_pass",
            "breach",
        ):
            raise ValueError("unsupported non-positive consistency behavior")


@dataclass(frozen=True)
class OperationalRules:
    maximum_minis: int | None = None
    maximum_micros: int | None = None
    micros_per_mini: Decimal | None = None
    permitted_session_start: time | None = None
    permitted_session_end: time | None = None
    forced_close_time: time | None = None
    news_trading_allowed: bool | None = None
    inactivity_calendar_days: int | None = None
    advisory_constraints: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _positive_int(self.maximum_minis, "maximum_minis")
        _positive_int(self.maximum_micros, "maximum_micros")
        if self.micros_per_mini is not None:
            _finite_decimal(self.micros_per_mini, "micros_per_mini", positive=True)
        for name in (
            "permitted_session_start",
            "permitted_session_end",
            "forced_close_time",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, time) or value.tzinfo is not None
            ):
                raise ValueError(f"{name} must be local wall time or None")
        if (self.permitted_session_start is None) != (
            self.permitted_session_end is None
        ):
            raise ValueError("permitted session start and end must be present together")
        if self.news_trading_allowed not in (None, True, False):
            raise ValueError("news_trading_allowed must be bool or None")
        _positive_int(self.inactivity_calendar_days, "inactivity_calendar_days")
        if not isinstance(self.advisory_constraints, Mapping):
            raise ValueError("advisory_constraints must be a mapping")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.advisory_constraints.items()
        ):
            raise ValueError("advisory constraint keys and values must be strings")
        object.__setattr__(
            self,
            "advisory_constraints",
            _immutable_mapping(self.advisory_constraints),
        )


@dataclass(frozen=True)
class RuleSource:
    url: str
    retrieved_on: date

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.startswith("https://"):
            raise ValueError("rule-source URL must use https")
        if not isinstance(self.retrieved_on, date) or isinstance(
            self.retrieved_on, datetime
        ):
            raise ValueError("retrieved_on must be datetime.date")


@dataclass(frozen=True)
class FundedAccountProfileV2:
    """Immutable, versioned rule profile; no mutable account state lives here."""

    profile_id: str
    profile_version: str
    provider: str
    currency: str
    starting_balance: Decimal
    profit_target: ProfitTargetRule
    maximum_loss: MaximumLossRule
    session_timezone: str | None
    session_boundary: time | None
    minimum_trading_days: int = 0
    daily_loss: DailyLossRule | None = None
    consistency: ConsistencyRule | None = None
    operational: OperationalRules = field(default_factory=OperationalRules)
    rule_sources: tuple[RuleSource, ...] = ()
    unresolved_assumptions: tuple[str, ...] = ()
    enabled: bool = True
    schema_version: str = RULE_ENGINE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("profile_id", "profile_version", "provider"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("currency must be explicit")
        object.__setattr__(self, "currency", self.currency.strip().upper())
        _finite_decimal(self.starting_balance, "starting_balance", positive=True)
        timezone = _timezone(self.session_timezone, "session_timezone")
        if self.session_boundary is not None and (
            not isinstance(self.session_boundary, time)
            or self.session_boundary.tzinfo is not None
        ):
            raise ValueError("session_boundary must be local wall time or None")
        if (timezone is None) != (self.session_boundary is None):
            raise ValueError("session timezone and boundary must be present together")
        if (
            not isinstance(self.minimum_trading_days, int)
            or isinstance(self.minimum_trading_days, bool)
            or self.minimum_trading_days < 0
        ):
            raise ValueError("minimum_trading_days must be a non-negative integer")
        object.__setattr__(self, "rule_sources", tuple(self.rule_sources))
        if any(not isinstance(item, RuleSource) for item in self.rule_sources):
            raise ValueError("rule_sources must contain RuleSource values")
        object.__setattr__(
            self,
            "unresolved_assumptions",
            tuple(self.unresolved_assumptions),
        )
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.unresolved_assumptions
        ):
            raise ValueError("unresolved assumptions must be non-empty strings")
        if self.schema_version != RULE_ENGINE_SCHEMA_VERSION:
            raise ValueError("unsupported funded-rule schema version")
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be bool")
        if not isinstance(self.profit_target, ProfitTargetRule):
            raise ValueError("profit_target must be ProfitTargetRule")
        if not isinstance(self.maximum_loss, MaximumLossRule):
            raise ValueError("maximum_loss must be MaximumLossRule")
        if self.daily_loss is not None and not isinstance(
            self.daily_loss, DailyLossRule
        ):
            raise ValueError("daily_loss must be DailyLossRule or None")
        if self.consistency is not None and not isinstance(
            self.consistency, ConsistencyRule
        ):
            raise ValueError("consistency must be ConsistencyRule or None")
        if not isinstance(self.operational, OperationalRules):
            raise ValueError("operational must be OperationalRules")
        readiness = self.readiness_reasons()
        if self.enabled and readiness:
            raise ValueError(
                "enabled profile has unresolved rule semantics: " + "; ".join(readiness)
            )

    def readiness_reasons(self) -> tuple[str, ...]:
        reasons = list(self.unresolved_assumptions)
        if not self.rule_sources:
            reasons.append("at least one dated rule source is required")
        if self.session_timezone is None or self.session_boundary is None:
            reasons.append("session timezone and boundary are unresolved")
        if self.maximum_loss.update_cadence is None:
            reasons.append("maximum-loss update cadence is unresolved")
        if self.maximum_loss.monitoring_cadence is None:
            reasons.append("maximum-loss monitoring cadence is unresolved")
        if self.maximum_loss.breach_boundary is None:
            reasons.append("maximum-loss breach boundary is unresolved")
        if self.consistency is not None:
            if self.consistency.denominator is None:
                reasons.append("consistency denominator is unresolved")
            if self.consistency.rounding is None:
                reasons.append("consistency rounding policy is unresolved")
        if self.operational.news_trading_allowed is None:
            reasons.append("news trading permission is unresolved")
        return tuple(dict.fromkeys(reasons))

    def required_capabilities(self) -> frozenset[str]:
        required = {CAP_CLOSED_TRADE_EVENTS}
        if self.maximum_loss.update_cadence is not None:
            required.add(_cadence_capability(self.maximum_loss.update_cadence))
        if self.maximum_loss.monitoring_cadence is not None:
            required.add(_cadence_capability(self.maximum_loss.monitoring_cadence))
        if self.daily_loss is not None:
            required.add(_cadence_capability(self.daily_loss.monitoring_cadence))
        if self.consistency is not None:
            required.add(CAP_CLOSED_TRADE_EVENTS)
        if (
            self.operational.maximum_minis is not None
            or self.operational.maximum_micros is not None
        ):
            required.add(CAP_POSITION_SIZES)
        if self.operational.forced_close_time is not None:
            required.add(CAP_OPEN_POSITION_STATUS)
        if self.operational.permitted_session_start is not None:
            required.add(CAP_TRADE_OPEN_TIMESTAMPS)
        if self.operational.news_trading_allowed is False:
            required.add(CAP_NEWS_FLAGS)
        return frozenset(required)


@dataclass(frozen=True)
class AccountRuleInput:
    """One ordered monetary observation consumed by the v2 engine."""

    timestamp: datetime
    event_type: EventType
    balance: Decimal
    equity: Decimal
    realized_pnl: Decimal | None = None
    trade_id: str = ""
    trade_opened_at: datetime | None = None
    mini_contracts: int | None = None
    micro_contracts: int | None = None
    has_open_positions: bool | None = None
    is_news_window: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime) or not _aware(self.timestamp):
            raise ValueError("timestamp must be timezone-aware")
        if self.event_type not in ("closed_trade", "end_of_day", "intraday"):
            raise ValueError("unsupported account-rule input event type")
        _finite_decimal(self.balance, "balance")
        _finite_decimal(self.equity, "equity")
        if self.realized_pnl is not None:
            _finite_decimal(self.realized_pnl, "realized_pnl")
        if self.event_type == "closed_trade" and self.realized_pnl is None:
            raise ValueError("closed_trade inputs require realized_pnl")
        if self.event_type != "closed_trade" and self.realized_pnl not in (
            None,
            Decimal("0"),
        ):
            raise ValueError("only closed_trade inputs may change realized balance")
        if self.trade_opened_at is not None and (
            not isinstance(self.trade_opened_at, datetime)
            or not _aware(self.trade_opened_at)
            or self.trade_opened_at > self.timestamp
        ):
            raise ValueError(
                "trade_opened_at must be timezone-aware and not after timestamp"
            )
        if self.event_type != "closed_trade" and self.trade_opened_at is not None:
            raise ValueError("only closed_trade inputs may have trade_opened_at")
        if not isinstance(self.trade_id, str):
            raise ValueError("trade_id must be a string")
        if self.event_type == "closed_trade" and not self.trade_id.strip():
            raise ValueError("closed_trade inputs require a non-empty trade_id")
        for name in ("mini_contracts", "micro_contracts"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.has_open_positions not in (None, True, False):
            raise ValueError("has_open_positions must be bool or None")
        if self.is_news_window not in (None, True, False):
            raise ValueError("is_news_window must be bool or None")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


@dataclass(frozen=True)
class RuleEvaluationEvent:
    kind: EvaluationKind
    rule: str
    timestamp: datetime
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in _EVENT_PRIORITY:
            raise ValueError("unsupported evaluation event kind")
        if not isinstance(self.rule, str) or not self.rule:
            raise ValueError("rule must be non-empty")
        if not isinstance(self.timestamp, datetime) or not _aware(self.timestamp):
            raise ValueError("evaluation timestamp must be timezone-aware")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("evaluation message must be non-empty")
        object.__setattr__(self, "details", _immutable_mapping(self.details))


@dataclass(frozen=True)
class RuleEvaluationReport:
    timestamp: datetime
    events: tuple[RuleEvaluationEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        if not isinstance(self.timestamp, datetime) or not _aware(self.timestamp):
            raise ValueError("report timestamp must be timezone-aware")
        if not self.events:
            raise ValueError("evaluation report requires at least one event")
        if any(event.timestamp != self.timestamp for event in self.events):
            raise ValueError("all report events must share the report timestamp")

    @property
    def primary_event(self) -> RuleEvaluationEvent:
        return self.events[0]

    @property
    def pass_eligible(self) -> bool:
        return any(event.kind == "pass_eligible" for event in self.events)


def _compare(value: Decimal, boundary: ComparisonBoundary, threshold: Decimal) -> bool:
    return {
        ">=": value >= threshold,
        ">": value > threshold,
        "<=": value <= threshold,
        "<": value < threshold,
    }[boundary]


def _within_session(current: time, start: time, end: time) -> bool:
    if start < end:
        return start <= current < end
    return current >= start or current < end


@dataclass
class FundedAccountStateV2:
    """Mutable deterministic replay state for one immutable v2 profile."""

    profile: FundedAccountProfileV2
    opened_at: datetime
    data_capabilities: frozenset[str]

    balance: Decimal = field(init=False)
    equity: Decimal = field(init=False)
    high_watermark: Decimal = field(init=False)
    maximum_loss_threshold: Decimal | None = field(init=False, default=None)
    session_start_balance: Decimal = field(init=False)
    session_start_equity: Decimal = field(init=False)
    current_session: date | None = field(init=False, default=None)
    soft_paused: bool = field(init=False, default=False)
    terminal_breach: bool = field(init=False, default=False)

    _last_timestamp: datetime | None = field(init=False, default=None)
    _daily_session: date | None = field(init=False, default=None)
    _daily_start_balance: Decimal = field(init=False)
    _daily_start_equity: Decimal = field(init=False)
    _trading_days: set[date] = field(init=False, default_factory=set)
    _trade_ids: set[str] = field(init=False, default_factory=set)
    _daily_profit: dict[date, Decimal] = field(init=False, default_factory=dict)
    _last_activity_date: date = field(init=False)
    _history: list[RuleEvaluationReport] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, FundedAccountProfileV2):
            raise ValueError("profile must be FundedAccountProfileV2")
        if not isinstance(self.opened_at, datetime) or not _aware(self.opened_at):
            raise ValueError("opened_at must be timezone-aware")
        capabilities = frozenset(self.data_capabilities)
        if any(not isinstance(item, str) for item in capabilities):
            raise ValueError("data capabilities must be strings")
        unsupported = capabilities - _SUPPORTED_CAPABILITIES
        if unsupported:
            raise ValueError(f"unsupported data capabilities: {sorted(unsupported)!r}")
        self.data_capabilities = capabilities
        self.balance = self.profile.starting_balance
        self.equity = self.profile.starting_balance
        self.high_watermark = self.profile.starting_balance
        self.session_start_balance = self.profile.starting_balance
        self.session_start_equity = self.profile.starting_balance
        self._daily_start_balance = self.profile.starting_balance
        self._daily_start_equity = self.profile.starting_balance
        timezone_name = self.profile.session_timezone or "UTC"
        self._last_activity_date = self.opened_at.astimezone(
            ZoneInfo(timezone_name)
        ).date()
        self.maximum_loss_threshold = self._maximum_loss_threshold(
            self.high_watermark
        )

    def _maximum_loss_threshold(self, high_watermark: Decimal) -> Decimal:
        rule = self.profile.maximum_loss
        anchor = (
            self.profile.starting_balance
            if rule.mode == "static"
            else high_watermark
        )
        distance = rule.distance.resolve(self.profile.starting_balance, anchor)
        threshold = anchor - distance
        if rule.threshold_ceiling is not None:
            threshold = min(threshold, rule.threshold_ceiling)
        return threshold

    def _event(
        self,
        kind: EvaluationKind,
        rule: str,
        source: AccountRuleInput,
        message: str,
        **details: Any,
    ) -> RuleEvaluationEvent:
        return RuleEvaluationEvent(kind, rule, source.timestamp, message, details)

    def _consistency_result(
        self,
        rule: ConsistencyRule,
        daily_profit: Mapping[date, Decimal],
    ) -> tuple[Decimal | None, Decimal | None, str | None]:
        values = list(daily_profit.values())
        if rule.window_sessions is not None:
            values = values[-rule.window_sessions :]
        best_positive = max((value for value in values if value > 0), default=Decimal(0))
        if rule.denominator == "sum_positive_days":
            denominator = sum(
                (value for value in values if value > 0),
                start=Decimal(0),
            )
        elif rule.denominator == "net_evaluation_profit":
            denominator = sum(values, start=Decimal(0))
        else:
            return None, None, "consistency denominator is unresolved"
        if denominator <= 0:
            return best_positive, denominator, "consistency denominator is non-positive"
        ratio = best_positive / denominator
        if rule.rounding is None:
            return ratio, denominator, "consistency rounding policy is unresolved"
        return rule.rounding.apply(ratio), denominator, None

    def apply(self, source: AccountRuleInput) -> RuleEvaluationReport:
        """Validate and atomically apply one ordered observation."""

        if self.terminal_breach:
            raise FundedRuleError("cannot apply events after a terminal breach")
        if source.timestamp < self.opened_at:
            raise FundedRuleError("account-rule input predates account opening")
        if (
            source.trade_opened_at is not None
            and source.trade_opened_at < self.opened_at
        ):
            raise FundedRuleError("trade opening predates account opening")
        if source.event_type == "closed_trade" and source.trade_id in self._trade_ids:
            raise FundedRuleError(f"duplicate closed trade_id {source.trade_id!r}")
        if self._last_timestamp is not None and source.timestamp < self._last_timestamp:
            raise FundedRuleError("account-rule inputs must be chronological")

        if not self.profile.enabled:
            report = RuleEvaluationReport(
                source.timestamp,
                (
                    self._event(
                        "not_evaluable",
                        "profile",
                        source,
                        "profile is provisional or disabled",
                        reasons=self.profile.readiness_reasons(),
                    ),
                ),
            )
            self._history.append(report)
            return report

        expected_balance = self.balance
        if source.event_type == "closed_trade":
            assert source.realized_pnl is not None
            expected_balance += source.realized_pnl
        if source.balance != expected_balance:
            raise FundedRuleError(
                "balance does not reconcile with the prior balance and realized_pnl"
            )

        profile = self.profile
        timezone_name = profile.session_timezone or "UTC"
        session_boundary = profile.session_boundary or time(0)
        session = _session_date(source.timestamp, timezone_name, session_boundary)
        new_session = self.current_session is not None and session != self.current_session
        session_start_balance = (
            self.balance if new_session else self.session_start_balance
        )
        session_start_equity = self.equity if new_session else self.session_start_equity
        soft_paused = self.soft_paused

        trading_days = set(self._trading_days)
        trade_ids = set(self._trade_ids)
        daily_profit = dict(self._daily_profit)
        if source.event_type == "closed_trade":
            trading_days.add(session)
            trade_ids.add(source.trade_id)
            assert source.realized_pnl is not None
            daily_profit[session] = daily_profit.get(session, Decimal(0)) + source.realized_pnl

        events: list[RuleEvaluationEvent] = []
        missing_capabilities = profile.required_capabilities() - self.data_capabilities
        for capability in sorted(missing_capabilities):
            events.append(
                self._event(
                    "not_evaluable",
                    "profile",
                    source,
                    f"required data capability {capability!r} is absent",
                    capability=capability,
                )
            )

        maximum_rule = profile.maximum_loss
        high_watermark = self.high_watermark
        if maximum_rule.mode == "static":
            high_watermark = profile.starting_balance
        elif maximum_rule.update_cadence is not None and _cadence_matches(
            maximum_rule.update_cadence, source.event_type
        ):
            observed = (
                source.balance
                if maximum_rule.reference == "balance"
                else source.equity
            )
            high_watermark = max(high_watermark, observed)
        maximum_threshold = self._maximum_loss_threshold(high_watermark)
        maximum_capability_missing = (
            maximum_rule.update_cadence is None
            or _cadence_capability(maximum_rule.update_cadence)
            not in self.data_capabilities
            or maximum_rule.monitoring_cadence is None
            or _cadence_capability(maximum_rule.monitoring_cadence)
            not in self.data_capabilities
        )
        maximum_check_due = bool(
            maximum_rule.monitoring_cadence is not None
            and _cadence_matches(
                maximum_rule.monitoring_cadence,
                source.event_type,
            )
        )
        if (
            maximum_rule.breach_boundary is not None
            and not maximum_capability_missing
            and maximum_check_due
        ):
            current = (
                source.balance
                if maximum_rule.reference == "balance"
                else source.equity
            )
            if _compare(current, maximum_rule.breach_boundary, maximum_threshold):
                events.append(
                    self._event(
                        "breach",
                        "maximum_loss",
                        source,
                        "maximum-loss threshold breached",
                        current=current,
                        threshold=maximum_threshold,
                        boundary=maximum_rule.breach_boundary,
                    )
                )

        daily_rule = profile.daily_loss
        daily_check_due = daily_rule is None
        daily_session = self._daily_session
        daily_start_balance = self._daily_start_balance
        daily_start_equity = self._daily_start_equity
        if daily_rule is not None:
            daily_session = _session_date(
                source.timestamp,
                daily_rule.reset_timezone,
                daily_rule.session_boundary,
            )
            if self._daily_session is not None and daily_session != self._daily_session:
                daily_start_balance = self.balance
                daily_start_equity = self.equity
                soft_paused = False
            daily_reference = (
                daily_start_balance
                if daily_rule.reference == "balance"
                else daily_start_equity
            )
            daily_current = (
                source.balance
                if daily_rule.reference == "balance"
                else source.equity
            )
            daily_distance = daily_rule.distance.resolve(
                profile.starting_balance,
                daily_reference,
            )
            daily_threshold = daily_reference - daily_distance
            daily_capability = _cadence_capability(daily_rule.monitoring_cadence)
            daily_check_due = _cadence_matches(
                daily_rule.monitoring_cadence,
                source.event_type,
            )
            if (
                daily_capability in self.data_capabilities
                and daily_check_due
                and _compare(
                    daily_current,
                    daily_rule.breach_boundary,
                    daily_threshold,
                )
            ):
                kind: EvaluationKind = (
                    "breach" if daily_rule.action == "breach" else "soft_pause"
                )
                events.append(
                    self._event(
                        kind,
                        "daily_loss",
                        source,
                        "daily-loss threshold reached",
                        current=daily_current,
                        threshold=daily_threshold,
                        boundary=daily_rule.breach_boundary,
                    )
                )
                if kind == "soft_pause":
                    soft_paused = True
            elif soft_paused:
                events.append(
                    self._event(
                        "soft_pause",
                        "daily_loss",
                        source,
                        "daily soft pause remains active until the next reset",
                    )
                )

        operational = profile.operational
        if source.event_type == "closed_trade":
            if (
                operational.maximum_minis is not None
                or operational.maximum_micros is not None
            ) and CAP_POSITION_SIZES in self.data_capabilities:
                if source.mini_contracts is None or source.micro_contracts is None:
                    events.append(
                        self._event(
                            "not_evaluable",
                            "position_size",
                            source,
                            "position-size fields are missing from a closed trade",
                        )
                    )
                else:
                    minis = Decimal(source.mini_contracts)
                    micros = Decimal(source.micro_contracts)
                    mixed = minis > 0 and micros > 0
                    if mixed and operational.micros_per_mini is None:
                        events.append(
                            self._event(
                                "not_evaluable",
                                "position_size",
                                source,
                                "mixed minis and micros lack an equivalence rule",
                            )
                        )
                    else:
                        too_large = False
                        if operational.micros_per_mini is None:
                            too_large = bool(
                                (
                                    operational.maximum_minis is not None
                                    and minis > operational.maximum_minis
                                )
                                or (
                                    operational.maximum_micros is not None
                                    and micros > operational.maximum_micros
                                )
                            )
                        else:
                            equivalent_minis = (
                                minis + micros / operational.micros_per_mini
                            )
                            equivalent_micros = (
                                micros + minis * operational.micros_per_mini
                            )
                            too_large = bool(
                                (
                                    operational.maximum_minis is not None
                                    and equivalent_minis > operational.maximum_minis
                                )
                                or (
                                    operational.maximum_micros is not None
                                    and equivalent_micros > operational.maximum_micros
                                )
                            )
                        if too_large:
                            events.append(
                                self._event(
                                    "breach",
                                    "position_size",
                                    source,
                                    "maximum contract size exceeded",
                                    mini_contracts=source.mini_contracts,
                                    micro_contracts=source.micro_contracts,
                                )
                            )

            if operational.permitted_session_start is not None:
                assert operational.permitted_session_end is not None
                if (
                    CAP_TRADE_OPEN_TIMESTAMPS in self.data_capabilities
                    and source.trade_opened_at is None
                ):
                    events.append(
                        self._event(
                            "not_evaluable",
                            "permitted_session",
                            source,
                            "trade opening timestamp is missing",
                        )
                    )
                elif source.trade_opened_at is not None:
                    opened_wall_time = (
                        source.trade_opened_at.astimezone(ZoneInfo(timezone_name))
                        .time()
                        .replace(tzinfo=None)
                    )
                    if not _within_session(
                        opened_wall_time,
                        operational.permitted_session_start,
                        operational.permitted_session_end,
                    ):
                        events.append(
                            self._event(
                                "breach",
                                "permitted_session",
                                source,
                                "trade opened outside the permitted session",
                                local_time=opened_wall_time.isoformat(),
                            )
                        )
            if operational.news_trading_allowed is False:
                if (
                    CAP_NEWS_FLAGS in self.data_capabilities
                    and source.is_news_window is None
                ):
                    events.append(
                        self._event(
                            "not_evaluable",
                            "news",
                            source,
                            "news-window status is missing from a closed trade",
                        )
                    )
                elif source.is_news_window:
                    events.append(
                        self._event(
                            "breach",
                            "news",
                            source,
                            "trade occurred during a restricted news window",
                        )
                    )

        if operational.forced_close_time is not None:
            local_wall_time = (
                source.timestamp.astimezone(ZoneInfo(timezone_name))
                .time()
                .replace(tzinfo=None)
            )
            if (
                CAP_OPEN_POSITION_STATUS in self.data_capabilities
                and source.has_open_positions is None
            ):
                events.append(
                    self._event(
                        "not_evaluable",
                        "forced_close",
                        source,
                        "open-position status is missing",
                    )
                )
            elif (
                source.has_open_positions
                and local_wall_time >= operational.forced_close_time
            ):
                events.append(
                    self._event(
                        "breach",
                        "forced_close",
                        source,
                        "position remained open at or after forced-close time",
                        local_time=local_wall_time.isoformat(),
                    )
                )

        event_local_date = source.timestamp.astimezone(ZoneInfo(timezone_name)).date()
        if operational.inactivity_calendar_days is not None:
            inactive_days = (event_local_date - self._last_activity_date).days
            if inactive_days >= operational.inactivity_calendar_days:
                events.append(
                    self._event(
                        "breach",
                        "inactivity",
                        source,
                        "inactivity window reached before this event",
                        inactive_days=inactive_days,
                    )
                )

        consistency_blocked = False
        consistency_rule = profile.consistency
        if consistency_rule is not None:
            ratio, denominator, consistency_error = self._consistency_result(
                consistency_rule,
                daily_profit,
            )
            if consistency_error is not None:
                if denominator is not None and denominator <= 0:
                    behavior = consistency_rule.non_positive_behavior
                    if behavior == "breach":
                        kind = "breach"
                    elif behavior == "block_pass":
                        kind = "consistency_block"
                        consistency_blocked = True
                    else:
                        kind = "not_evaluable"
                else:
                    kind = "not_evaluable"
                events.append(
                    self._event(
                        kind,
                        "consistency",
                        source,
                        consistency_error,
                        denominator=denominator,
                    )
                )
            else:
                assert ratio is not None
                within_limit = _compare(
                    ratio,
                    consistency_rule.acceptance_boundary,
                    consistency_rule.maximum_ratio,
                )
                if not within_limit:
                    kind = (
                        "breach"
                        if consistency_rule.action == "breach"
                        else "consistency_block"
                    )
                    consistency_blocked = kind == "consistency_block"
                    events.append(
                        self._event(
                            kind,
                            "consistency",
                            source,
                            "consistency ratio exceeds the permitted maximum",
                            ratio=ratio,
                            maximum_ratio=consistency_rule.maximum_ratio,
                            denominator=denominator,
                        )
                    )

        profit_rule = profile.profit_target
        profit_current = (
            source.balance
            if profit_rule.reference == "balance"
            else source.equity
        )
        target_amount = profit_rule.target.resolve(
            profile.starting_balance,
            profile.starting_balance,
        )
        target_level = profile.starting_balance + target_amount
        target_reached = _compare(
            profit_current,
            profit_rule.boundary,
            target_level,
        )
        if target_reached and len(trading_days) < profile.minimum_trading_days:
            events.append(
                self._event(
                    "pass_blocked",
                    "profit_target",
                    source,
                    "profit target reached but minimum trading days are incomplete",
                    trading_days=len(trading_days),
                    required=profile.minimum_trading_days,
                )
            )
        if target_reached and not maximum_check_due:
            events.append(
                self._event(
                    "pass_blocked",
                    "maximum_loss",
                    source,
                    "passing awaits the configured maximum-loss monitoring event",
                    monitoring_cadence=maximum_rule.monitoring_cadence,
                )
            )
        if target_reached and not daily_check_due:
            assert daily_rule is not None
            events.append(
                self._event(
                    "pass_blocked",
                    "daily_loss",
                    source,
                    "passing awaits the configured daily-loss monitoring event",
                    monitoring_cadence=daily_rule.monitoring_cadence,
                )
            )

        blocking_kinds = {
            "breach",
            "soft_pause",
            "consistency_block",
            "pass_blocked",
            "not_evaluable",
        }
        if (
            profile.enabled
            and target_reached
            and len(trading_days) >= profile.minimum_trading_days
            and not soft_paused
            and not consistency_blocked
            and not any(event.kind in blocking_kinds for event in events)
        ):
            events.append(
                self._event(
                    "pass_eligible",
                    "profit_target",
                    source,
                    "profit target and all pass conditions are satisfied",
                    current=profit_current,
                    target=target_level,
                    trading_days=len(trading_days),
                )
            )

        if not events:
            events.append(
                self._event(
                    "in_progress",
                    "profit_target",
                    source,
                    "profit target has not yet been reached",
                    current=profit_current,
                    target=target_level,
                )
            )

        events.sort(
            key=lambda item: (
                _EVENT_PRIORITY[item.kind],
                _RULE_PRIORITY.get(item.rule, 999),
                item.rule,
                item.message,
            )
        )
        report = RuleEvaluationReport(source.timestamp, tuple(events))

        self.balance = source.balance
        self.equity = source.equity
        self.high_watermark = high_watermark
        self.maximum_loss_threshold = maximum_threshold
        self.session_start_balance = session_start_balance
        self.session_start_equity = session_start_equity
        self.current_session = session
        self.soft_paused = soft_paused
        self._daily_session = daily_session
        self._daily_start_balance = daily_start_balance
        self._daily_start_equity = daily_start_equity
        self.terminal_breach = any(event.kind == "breach" for event in events)
        self._last_timestamp = source.timestamp
        self._trading_days = trading_days
        self._trade_ids = trade_ids
        self._daily_profit = daily_profit
        if source.event_type == "closed_trade":
            self._last_activity_date = event_local_date
        self._history.append(report)
        return report

    @property
    def trading_days(self) -> tuple[date, ...]:
        return tuple(sorted(self._trading_days))

    @property
    def daily_profit(self) -> Mapping[date, Decimal]:
        return MappingProxyType(dict(self._daily_profit))

    @property
    def history(self) -> tuple[RuleEvaluationReport, ...]:
        return tuple(self._history)


__all__ = [
    "AccountRuleInput",
    "CAP_CLOSED_TRADE_EVENTS",
    "CAP_END_OF_DAY_EVENTS",
    "CAP_INTRADAY_EVENTS",
    "CAP_NEWS_FLAGS",
    "CAP_OPEN_POSITION_STATUS",
    "CAP_POSITION_SIZES",
    "CAP_TRADE_OPEN_TIMESTAMPS",
    "ConsistencyRule",
    "DailyLossRule",
    "FundedAccountProfileV2",
    "FundedAccountStateV2",
    "FundedRuleError",
    "MaximumLossRule",
    "OperationalRules",
    "ProfitTargetRule",
    "RULE_ENGINE_SCHEMA_VERSION",
    "RoundingPolicy",
    "RuleAmount",
    "RuleEvaluationEvent",
    "RuleEvaluationReport",
    "RuleSource",
]
