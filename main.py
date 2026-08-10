"""
FARS — Funded Account Risk System
=================================
Phase 1 entry point: project structure, configuration, synthetic data.

Usage:
    python main.py              # run demo
    python -m pytest tests/ -v  # run test suite
"""

from src.synthetic import generate_trades
from src.types import SyntheticConfig


def main():
    config = SyntheticConfig(
        seed=42,
        n_trades=20,
        win_rate=0.45,
        avg_win_r=2.0,
        avg_loss_r=1.0,
    )
    trades = generate_trades(config)

    print(f"FARS v0.1 — Phase 1: Synthetic Trade Generator\n")
    print(f"Config: {config}\n")
    print(f"Generated {len(trades)} trades:\n")

    wins = [t for t in trades if t.r_result > 0]
    losses = [t for t in trades if t.r_result < 0]

    for t in trades:
        tag = "WIN " if t.r_result > 0 else "LOSS"
        print(f"  [{tag}] {t.trade_id}  {t.date}  {t.r_result:+.4f}R")

    print(f"\nSummary: {len(wins)} wins, {len(losses)} losses")
    print(
        f"Empirical win rate: {len(wins) / len(trades):.1%}"
    )


if __name__ == "__main__":
    main()
