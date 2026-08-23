"""
Synthetic trade data generator.

Produces Trade sequences from a configurable parametric model for
development, testing, and stress-scenario analysis.

Data Generating Process (DGP)
-----------------------------
For each of n_trades:

1. Draw u ~ Uniform(0, 1).
2. If u < win_rate → winning trade:
     r_result ~ Lognormal(μ_win, σ_win)
     Always positive by construction. μ and σ are calibrated so that
     E[r_result] = avg_win_R and Std[r_result] = std_win_R.
3. Otherwise → losing trade:
     r_result ~ -Lognormal(μ_loss, σ_loss)
     Always negative by construction. μ and σ are calibrated so that
     E[|r_result|] = avg_loss_R and Std[|r_result|] = std_loss_R.

4. Trades are assigned uniformly across days: trades_per_day trades
   per calendar day starting from start_date.

5. streak_factor (NOT IMPLEMENTED in v0.1):
     If > 0, introduces first-order Markov dependence on win/loss.
     Raises UserWarning if set to non-zero in v0.1.

6. distribution: only "lognormal" is supported in v0.1.
     Other values are rejected at construction.

Key design property:
  - win_rate directly controls P(r_result > 0) — it IS the fraction
    of positive trades in expectation, matching the parameter name exactly.
  - avg_win_R is the theoretical conditional mean of positive trades.
  - avg_loss_R is the theoretical conditional mean of |negative trades|.
    Finite generated samples generally do not match these means exactly.

Lognormal calibration:
  Given desired mean m > 0 and std s > 0:
    σ² = ln(1 + (s/m)²)
    μ  = ln(m) - σ²/2
  Then X = exp(N(μ, σ)) has E[X] = m, Std[X] = s.

Special case: when std = 0, the distribution is degenerate at mean
(i.e., all wins are exactly avg_win_R).

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

import uuid
import warnings
from datetime import datetime, timedelta

import numpy as np

from src.types import SyntheticConfig, Trade


def _lognormal_params(mean: float, std: float) -> tuple[float, float]:
    """
    Calibrate lognormal parameters (μ, σ) to achieve desired mean and std.

    For X ~ Lognormal(μ, σ):
        E[X]   = exp(μ + σ²/2)
        Var[X] = (exp(σ²) - 1) * exp(2μ + σ²)

    Solving for μ, σ given desired mean m and std s:
        σ² = ln(1 + (s/m)²)
        μ  = ln(m) - σ²/2

    Returns (μ, σ) for the underlying normal.
    """
    if std == 0.0:
        # Degenerate case: all values equal to mean
        return np.log(mean), 0.0

    cv2 = (std / mean) ** 2
    sigma_sq = np.log(1.0 + cv2)
    sigma = np.sqrt(sigma_sq)
    mu = np.log(mean) - sigma_sq / 2.0
    return mu, sigma


def _generate_r_results(config: SyntheticConfig) -> np.ndarray:
    """Generate only R outcomes, preserving the public generator's RNG stream."""
    # Warn about unimplemented features
    if config.streak_factor != 0.0:
        warnings.warn(
            "streak_factor is not implemented in v0.1. "
            "Trade outcomes will be IID regardless of streak_factor."
        )

    rng = np.random.default_rng(config.seed)

    # --- 1. Determine win/loss for each trade ---
    is_win = rng.random(config.n_trades) < config.win_rate
    n_wins = int(np.sum(is_win))
    n_losses = config.n_trades - n_wins

    # --- 2. Generate R outcomes ---
    r_results = np.zeros(config.n_trades, dtype=np.float64)

    if n_wins > 0:
        mu_win, sigma_win = _lognormal_params(config.avg_win_r, config.std_win_r)
        if sigma_win == 0.0:
            win_draws = np.full(n_wins, config.avg_win_r)
        else:
            win_draws = rng.lognormal(mean=mu_win, sigma=sigma_win, size=n_wins)
        r_results[is_win] = win_draws

    if n_losses > 0:
        mu_loss, sigma_loss = _lognormal_params(config.avg_loss_r, config.std_loss_r)
        if sigma_loss == 0.0:
            loss_draws = np.full(n_losses, -config.avg_loss_r)
        else:
            loss_draws = -rng.lognormal(mean=mu_loss, sigma=sigma_loss, size=n_losses)
        r_results[~is_win] = loss_draws

    return r_results


def generate_trades(config: SyntheticConfig) -> list[Trade]:
    """
    Generate a sequence of synthetic trades.

    Returns a list of Trade objects with r_result, date, and trade_id
    populated. The sequence length equals config.n_trades.

    Reproducibility: setting config.seed fixes the numpy RNG state
    before generation. Same seed + same config → identical sequence.

    The configured win_rate directly equals P(r_result > 0).
    avg_win_R and avg_loss_R are the theoretical conditional means of
    their respective branches (no truncation distortion).
    """
    r_results = _generate_r_results(config)

    # --- 3. Assign dates ---
    start = datetime.strptime(config.start_date, "%Y-%m-%d")
    dates = []
    for i in range(config.n_trades):
        day_offset = i // config.trades_per_day
        trade_date = start + timedelta(days=day_offset)
        dates.append(trade_date.strftime("%Y-%m-%d"))

    # --- 4. Build Trade objects ---
    batch_id = uuid.uuid4().hex[:8] if config.seed is None else str(config.seed)
    trades = []
    for i in range(config.n_trades):
        trade = Trade(
            r_result=float(r_results[i]),
            trade_id=f"synth-{batch_id}-{i:06d}",
            date=dates[i],
            timestamp=datetime.strptime(dates[i], "%Y-%m-%d"),
        )
        trades.append(trade)

    return trades


def generate_trade_sequences(
    config: SyntheticConfig,
    n_sequences: int = 1,
) -> list[list[Trade]]:
    """
    Generate multiple independent trade sequences.

    Each sequence uses a derived seed for reproducibility:
      - If config.seed is not None: seed_i = config.seed + i
      - If config.seed is None: seed_i = None (truly random per sequence)

    Useful for Monte Carlo where each simulation run needs its own
    independent trade sequence.
    """
    if n_sequences <= 0 or not isinstance(n_sequences, int) or isinstance(n_sequences, bool):
        raise ValueError(
            f"n_sequences must be a positive integer, got {n_sequences!r}"
        )

    sequences = []

    for i in range(n_sequences):
        if config.seed is not None:
            derived_seed = config.seed + i
        else:
            derived_seed = None

        seq_config = SyntheticConfig(
            win_rate=config.win_rate,
            avg_win_r=config.avg_win_r,
            avg_loss_r=config.avg_loss_r,
            std_win_r=config.std_win_r,
            std_loss_r=config.std_loss_r,
            n_trades=config.n_trades,
            trades_per_day=config.trades_per_day,
            seed=derived_seed,
            distribution=config.distribution,
            streak_factor=config.streak_factor,
            start_date=config.start_date,
        )
        sequences.append(generate_trades(seq_config))

    return sequences
