"""Canonical read-only events at the provider boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_finite(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal")


@dataclass(frozen=True)
class AccountSnapshot:
    """One account state returned by ProjectX."""

    account_id: int
    name: str
    balance: Decimal
    can_trade: bool
    is_visible: bool
    simulated: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.account_id, bool) or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if not self.name.strip():
            raise ValueError("account name must not be empty")
        _require_finite(self.balance, "balance")


@dataclass(frozen=True)
class Contract:
    """Tradable futures contract metadata."""

    contract_id: str
    name: str
    description: str
    tick_size: Decimal
    tick_value: Decimal
    active: bool
    symbol_id: str

    def __post_init__(self) -> None:
        if not self.contract_id.strip() or not self.name.strip() or not self.symbol_id.strip():
            raise ValueError("contract id, name, and symbol id must not be empty")
        _require_finite(self.tick_size, "tick_size")
        _require_finite(self.tick_value, "tick_value")
        if self.tick_size <= 0 or self.tick_value <= 0:
            raise ValueError("tick_size and tick_value must be positive")


@dataclass(frozen=True)
class Bar:
    """OHLCV bar returned by the ProjectX history endpoint."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "timestamp")
        for name in ("open", "high", "low", "close", "volume"):
            _require_finite(getattr(self, name), name)
        if self.volume < 0:
            raise ValueError("volume must not be negative")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is below another OHLC value")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is above another OHLC value")


@dataclass(frozen=True)
class OpenPosition:
    """Current provider position. Position type 1 is long and 2 is short."""

    position_id: int
    account_id: int
    contract_id: str
    created_at: datetime
    position_type: int
    size: int
    average_price: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")
        _require_finite(self.average_price, "average_price")
        if self.position_id <= 0 or self.account_id <= 0:
            raise ValueError("position_id and account_id must be positive")
        if self.position_type not in (1, 2):
            raise ValueError("position_type must be 1 (long) or 2 (short)")
        if not self.contract_id.strip():
            raise ValueError("contract_id must not be empty")
        if self.size <= 0:
            raise ValueError("position size must be positive")


@dataclass(frozen=True)
class ProviderTrade:
    """Raw ProjectX fill/trade record.

    A null ``profit_and_loss`` is a half turn.  These records must not be
    relabeled as FARS completed round trips without a separate aggregator.
    """

    trade_id: int
    account_id: int
    contract_id: str
    created_at: datetime
    price: Decimal
    profit_and_loss: Decimal | None
    fees: Decimal
    side: int
    size: int
    voided: bool
    order_id: int

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "created_at")
        for name in ("price", "fees"):
            _require_finite(getattr(self, name), name)
        if self.profit_and_loss is not None:
            _require_finite(self.profit_and_loss, "profit_and_loss")
        if self.trade_id <= 0 or self.account_id <= 0 or self.order_id <= 0:
            raise ValueError("trade, account, and order ids must be positive")
        if not self.contract_id.strip():
            raise ValueError("contract_id must not be empty")
        if self.side not in (0, 1):
            raise ValueError("trade side must be 0 (bid) or 1 (ask)")
        if self.size <= 0:
            raise ValueError("trade size must be positive")
