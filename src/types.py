"""
FARS core data types.

Phase 1 defines the three foundational types used across all modules:
  - Trade: a single trade outcome in R-multiples
  - SyntheticConfig: configuration for synthetic trade generation
  - FundedAccountRules: funded-account constraints (used from Phase 3 onward)

Trade and FundedAccountRules are frozen (immutable) dataclasses.
SyntheticConfig is intentionally mutable to allow derived-seed construction.
"""

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Literal


@dataclass(frozen=True)
class Trade:
    """
    A single trade outcome.

    Frozen (immutable) except for the metadata dict, which is
    intentionally mutable for runtime annotations during analysis.

    The only field required by the simulation engine is r_result.
    All other fields exist for traceability when real data is available.

    r_result: outcome in R-multiples.
        +1R = gained the amount risked
        -1R = lost the full initial risk
        +2R = gained twice the risk
        -0.5R = partial loss

    date: "YYYY-MM-DD" string used for daily-loss tracking and
        trade-per-day grouping. Simple format avoids timezone friction.
    """

    r_result: float

    trade_id: str = ""
    timestamp: datetime | None = None
    date: str = ""
    asset: str = ""
    direction: Literal["long", "short"] | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    exit_price: float | None = None
    strategy: str = "default"
    metadata: dict = field(default_factory=dict)


@dataclass
class SyntheticConfig:
    """
    Configuration for synthetic trade generation.

    Data Generating Process (DGP) — see synthetic.py for full documentation:
      - Each trade is a Bernoulli trial for win/loss with probability win_rate.
      - Wins:  r_result ~ Lognormal(μ_win, σ_win), calibrated so that
        E[r_result] = avg_win_R and Std[r_result] = std_win_R.
        Always strictly positive by construction.
      - Losses: r_result ~ -Lognormal(μ_loss, σ_loss), calibrated so that
        E[|r_result|] = avg_loss_R and Std[|r_result|] = std_loss_R.
        Always strictly negative by construction.
      - Trades distributed uniformly across days.

    Key properties:
      - win_rate directly equals P(r_result > 0).
      - avg_win_R and avg_loss_R are the theoretical conditional means of
        their respective branches (no truncation distortion). Finite samples
        fluctuate around those configured expectations.

    ASSUMPTION: Trade outcomes are IID draws from a mixture of two
        lognormal distributions (or degenerate at mean when std=0).
    JUSTIFICATION: Synthetic data is for development and testing only.
        Lognormal captures the positive skew and fat right tail typical
        of winning trades, while keeping parameters directly interpretable.
    FAILURE MODE: Real trade sequences exhibit streaks, volatility
        clustering, and possibly different tail behavior than lognormal.
        P(PASS) estimates from MC on this data will not capture temporal
        dependence structures present in real trading.
    ROBUST ALTERNATIVE: When real data is available, use empirical
        distribution as default. If IID is rejected, use block
        bootstrap or Markov-chain resampling.
    """

    win_rate: float = 0.45
    avg_win_r: float = 2.0
    avg_loss_r: float = 1.0
    std_win_r: float = 0.5
    std_loss_r: float = 0.3
    n_trades: int = 100
    trades_per_day: int = 5
    seed: int | None = None

    # Future: distribution family and streak dependence
    distribution: Literal["lognormal"] = "lognormal"
    streak_factor: float = 0.0  # 0.0 = IID; future: > 0 = Markov dependence

    start_date: str = "2024-01-01"

    def __post_init__(self):
        if self.seed is not None and (
            not isinstance(self.seed, int)
            or isinstance(self.seed, bool)
            or self.seed < 0
        ):
            raise ValueError(
                f"seed must be a non-negative integer or None, got {self.seed!r}"
            )
        if not isinstance(self.start_date, str):
            raise ValueError(
                f"start_date must use YYYY-MM-DD format, got {self.start_date!r}"
            )
        try:
            normalized_start_date = datetime.strptime(
                self.start_date, "%Y-%m-%d"
            ).strftime("%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                f"start_date must use YYYY-MM-DD format, got {self.start_date!r}"
            ) from exc
        if normalized_start_date != self.start_date:
            raise ValueError(
                f"start_date must use zero-padded YYYY-MM-DD format, "
                f"got {self.start_date!r}"
            )
        if not isfinite(self.win_rate) or not 0 < self.win_rate < 1:
            raise ValueError(f"win_rate must be finite and in (0, 1), got {self.win_rate}")
        if not isfinite(self.avg_win_r) or self.avg_win_r <= 0:
            raise ValueError(f"avg_win_r must be finite and positive, got {self.avg_win_r}")
        if not isfinite(self.avg_loss_r) or self.avg_loss_r <= 0:
            raise ValueError(f"avg_loss_r must be finite and positive, got {self.avg_loss_r}")
        if not isfinite(self.std_win_r) or self.std_win_r < 0:
            raise ValueError(f"std_win_r must be finite and non-negative, got {self.std_win_r}")
        if not isfinite(self.std_loss_r) or self.std_loss_r < 0:
            raise ValueError(f"std_loss_r must be finite and non-negative, got {self.std_loss_r}")
        if self.n_trades <= 0 or not isinstance(self.n_trades, int) or isinstance(self.n_trades, bool):
            raise ValueError(
                f"n_trades must be a positive integer, got {self.n_trades!r}"
            )
        if self.trades_per_day <= 0 or not isinstance(self.trades_per_day, int) or isinstance(self.trades_per_day, bool):
            raise ValueError(
                f"trades_per_day must be a positive integer, got {self.trades_per_day!r}"
            )
        if not isfinite(self.streak_factor) or not 0.0 <= self.streak_factor <= 1.0:
            raise ValueError(
                f"streak_factor must be finite and in [0, 1], got {self.streak_factor}"
            )
        if self.distribution != "lognormal":
            raise ValueError(
                f"distribution must be 'lognormal' in v0.1, got {self.distribution!r}"
            )


@dataclass(frozen=True)
class FundedAccountRules:
    """
    Funded-account constraints. Immutable — rules don't change mid-simulation.

    Used from Phase 3 onward. Defined here in Phase 1 so the type is
    importable by downstream modules as they are built.

    daily_loss_base:
        "initial" — daily_loss_limit_pct is % of initial_balance (default, v0.1)
        "eod"     — daily_loss_limit_pct is % of previous day's closing equity

    drawdown_mode:
        "static"   — max_drawdown_pct is % of initial_balance (v0.1)
        "trailing" — max_drawdown_pct is % of peak equity (future)
    """

    initial_balance: float
    profit_target_pct: float
    max_drawdown_pct: float
    daily_loss_limit_pct: float
    risk_per_trade: float = 0.01  # default 1% risk per trade

    daily_loss_base: Literal["initial", "eod"] = "initial"
    drawdown_mode: Literal["static", "trailing"] = "static"
    max_trades: int | None = None

    def __post_init__(self):
        if not isfinite(self.initial_balance) or self.initial_balance <= 0:
            raise ValueError(
                f"initial_balance must be finite and positive, got {self.initial_balance}"
            )
        if not isfinite(self.profit_target_pct) or not 0 < self.profit_target_pct < 1:
            raise ValueError(
                f"profit_target_pct must be finite and in (0, 1), got {self.profit_target_pct}"
            )
        if not isfinite(self.max_drawdown_pct) or not 0 < self.max_drawdown_pct < 1:
            raise ValueError(
                f"max_drawdown_pct must be finite and in (0, 1), got {self.max_drawdown_pct}"
            )
        if not isfinite(self.daily_loss_limit_pct) or not 0 < self.daily_loss_limit_pct < 1:
            raise ValueError(
                f"daily_loss_limit_pct must be finite and in (0, 1), got {self.daily_loss_limit_pct}"
            )
        if not isfinite(self.risk_per_trade) or not 0 < self.risk_per_trade < 1:
            raise ValueError(
                f"risk_per_trade must be finite and in (0, 1), got {self.risk_per_trade}"
            )
        if self.max_trades is not None and (
            self.max_trades <= 0
            or not isinstance(self.max_trades, int)
            or isinstance(self.max_trades, bool)
        ):
            raise ValueError(
                f"max_trades must be a positive integer or None, got {self.max_trades!r}"
            )
        if self.daily_loss_base not in ("initial", "eod"):
            raise ValueError(
                f"daily_loss_base must be 'initial' or 'eod', got {self.daily_loss_base!r}"
            )
        if self.drawdown_mode not in ("static", "trailing"):
            raise ValueError(
                f"drawdown_mode must be 'static' or 'trailing', got {self.drawdown_mode!r}"
            )

        # Numeric safety: dollar_risk must be finite and positive.
        # Without this, initial_balance * risk_per_trade can underflow to
        # 0.0 (e.g. initial_balance=5e-324, risk_per_trade=0.01), which means
        # trades never move equity and the account "passes" instantly with
        # zero real gains.
        dollar_risk = self.risk_per_trade * self.initial_balance
        if not isfinite(dollar_risk) or dollar_risk <= 0.0:
            raise ValueError(
                f"risk_per_trade * initial_balance (dollar_risk) must be "
                f"finite and positive, got {dollar_risk!r}"
            )

        # Numeric safety: the profit target must be strictly above the balance.
        #
        # Computed as balance + balance*pct (NOT balance*(1+pct)) so it stays
        # consistent with the equity update balance + Σ(r·risk·balance). The
        # (1+pct) form rounds one ULP high (e.g. 100000*1.10 = 110000.00000000001)
        # and would make an exact-boundary +10R trade fall short. The `<= balance`
        # check rejects underflow (pct*balance → 0) and rounding-to-balance.
        target = self.initial_balance + self.initial_balance * self.profit_target_pct
        if not isfinite(target) or target <= self.initial_balance:
            raise ValueError(
                f"profit target must be strictly greater than initial_balance, "
                f"got target={target!r} vs balance={self.initial_balance!r}"
            )

        # Numeric safety: the drawdown and daily-loss DOLLAR thresholds must be
        # strictly below their reference. At construction the reference is the
        # initial balance (peak_equity and start_of_day_equity both start at
        # balance). If balance*pct absorbs into balance (pct below ~machine
        # epsilon, e.g. 5e-324), a zero-loss trade (equity == balance) would be
        # flagged as a violation because the threshold rounds back to balance.
        if (
            self.initial_balance - self.initial_balance * self.max_drawdown_pct
            >= self.initial_balance
        ):
            raise ValueError(
                f"max_drawdown_pct too small: balance - balance*pct rounds to "
                f"balance, got {self.max_drawdown_pct!r}"
            )
        if (
            self.initial_balance - self.initial_balance * self.daily_loss_limit_pct
            >= self.initial_balance
        ):
            raise ValueError(
                f"daily_loss_limit_pct too small: balance - balance*pct rounds to "
                f"balance, got {self.daily_loss_limit_pct!r}"
            )
