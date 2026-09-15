"""Single entry-point funded account engine for FARS (Block D2).

Consumes:
- FundedAccountProfileV2 (from src.funded_profiles)
- FundedAccountStateV2 (from src.funded_rules_v2)
- Historical or resampled trades (with causal MAE from src.backtest.mae)

Implements:
- Single source of truth: delegates trailing, HWM, floor ceilings, and breach detection
  strictly to FundedAccountStateV2 without duplicating rules.
- Sizing with safety buffer fraction (risk <= 0.75 * (equity - floor)).
- Blocked state detection when not even 1 contract fits within the risk buffer.
- Exact intraday trailing evaluation using causal MAE events vs closed-trade evaluation.
- Monte Carlo simulation of challenge paths with deterministic seeds.
- Multi-account portfolio simulation with explicit correlation accounting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

import numpy as np

from src.backtest.executor import ExecutedTrade
from src.backtest.history import Bar
from src.backtest.mae import TradeMaeResult, compute_trade_mae
from src.funded_rules_v2 import (
    AccountRuleInput,
    FundedAccountProfileV2,
    FundedAccountStateV2,
)


@dataclass(frozen=True)
class AccountEngineConfig:
    """Execution parameters for the account engine."""

    risk_pct: float  # e.g. 0.001946 (0.1946% per trade)
    trailing_mode: str = "closed_trade"  # "closed_trade" or "intraday_event"
    dd_buffer_safety_frac: float = 0.75  # from Kai spec §6 / challenge_sim.py:111-119
    dollar_per_point: float = 2.0  # MNQ dollar per point
    horizon_calendar_days: int = 30  # configurable (30d default vs 90d research)
    commission_per_contract_rt: float = 1.00  # $1.00 RT executable in Kai spec §7.1
    slippage_points: float = 0.25  # 1 tick per market fill
    instrument_type: str = "micro"  # "micro" (MNQ) or "mini" (NQ)


@dataclass(frozen=True)
class TradeRunRecord:
    """Detailed record of one trade execution in the account state."""

    trade_index: int
    trade_id: str
    entry_time: datetime
    exit_time: datetime
    direction: str
    entry_price: float
    exit_price: float
    stop_points: float
    quantity: int
    gross_pnl: float
    net_pnl: float
    balance_after: float
    equity_after: float
    floor_after: float
    hwm_after: float
    intraday_mae_points: float
    intraday_equity_min: float
    event_status: str


@dataclass(frozen=True)
class SingleAccountRunResult:
    """Outcome of running a sequence of trades through a funded account."""

    account_id: str
    profile_id: str
    starting_balance: float
    final_balance: float
    final_equity: float
    high_watermark: float
    final_floor: float
    status: str  # "passed", "blown", "blocked", "timeout"
    termination_reason: str
    trades_executed: int
    days_to_outcome: float
    trading_days_count: int
    max_drawdown_dollars: float
    max_drawdown_pct: float
    records: tuple[TradeRunRecord, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def blown(self) -> bool:
        return self.status == "blown"

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


def run_account_simulation(
    profile: FundedAccountProfileV2,
    trades: Sequence[ExecutedTrade],
    config: AccountEngineConfig,
    *,
    account_id: str = "acc-001",
    opened_at: datetime | None = None,
    precomputed_maes: Mapping[str, TradeMaeResult] | None = None,
    m1_bars_by_date: Mapping[str, list[Bar]] | None = None,
) -> SingleAccountRunResult:
    """Simulate a trade sequence through FundedAccountStateV2 with rigorous rule evaluation.

    Delegates all threshold tracking, trailing, and breach logic to FundedAccountStateV2.
    """
    if not trades:
        return SingleAccountRunResult(
            account_id=account_id,
            profile_id=profile.profile_id,
            starting_balance=float(profile.starting_balance),
            final_balance=float(profile.starting_balance),
            final_equity=float(profile.starting_balance),
            high_watermark=float(profile.starting_balance),
            final_floor=float(profile.starting_balance - profile.maximum_loss.distance.value),
            status="timeout",
            termination_reason="no_trades_provided",
            trades_executed=0,
            days_to_outcome=0.0,
            trading_days_count=0,
            max_drawdown_dollars=0.0,
            max_drawdown_pct=0.0,
        )

    # Ensure profile cadence matches config
    required_cadence = "intraday_event" if config.trailing_mode == "intraday_event" else "closed_trade"
    if profile.maximum_loss.monitoring_cadence != required_cadence:
        # Re-derive profile with appropriate cadence if needed
        from src.funded_profiles import apex_profile
        # derive size from profile_id (e.g. apex-25k-evaluation)
        size_key = profile.profile_id.split("-")[1]
        profile = apex_profile(size_key, cadence=required_cadence)

    sim_start = opened_at or trades[0].entry_time
    if sim_start.tzinfo is None:
        sim_start = sim_start.replace(tzinfo=timezone.utc)

    state = FundedAccountStateV2(
        profile=profile,
        opened_at=sim_start,
        data_capabilities=profile.required_capabilities(),
    )

    records: list[TradeRunRecord] = []
    status = "timeout"
    termination_reason = "horizon_exhausted"
    horizon_cutoff = sim_start + timedelta(days=config.horizon_calendar_days)

    peak_balance = float(profile.starting_balance)
    max_dd_dollars = 0.0

    dollar_per_point = Decimal(str(config.dollar_per_point))
    starting_balance = profile.starting_balance
    safety_frac = Decimal(str(config.dd_buffer_safety_frac))
    nominal_risk_usd = Decimal(str(config.risk_pct)) * starting_balance

    max_contracts_limit = (
        profile.operational.maximum_micros
        if config.instrument_type == "micro"
        else profile.operational.maximum_minis
    ) or 100

    for idx, trade in enumerate(trades):
        # 1. Horizon check
        t_entry = trade.entry_time if trade.entry_time.tzinfo else trade.entry_time.replace(tzinfo=timezone.utc)
        t_exit = trade.exit_time if trade.exit_time.tzinfo else trade.exit_time.replace(tzinfo=timezone.utc)

        if t_entry > horizon_cutoff:
            status = "timeout"
            termination_reason = f"trade_entry_exceeded_horizon_{config.horizon_calendar_days}d"
            break

        # 2. Sizing calculation with safety buffer cap
        current_balance = state.balance
        current_floor = state.maximum_loss_threshold or (starting_balance - profile.maximum_loss.distance.value)
        buffer = current_balance - current_floor

        if buffer <= 0:
            status = "blown"
            termination_reason = "equity_at_or_below_floor_pre_trade"
            break

        cap = safety_frac * buffer
        stop_points = abs(trade.entry_price - trade.stop_price)
        if stop_points <= 0.0:
            stop_points = 10.0  # Fallback 10 points

        unit_risk_usd = Decimal(str(round(stop_points * config.dollar_per_point, 4)))
        qty_max_buffer = int(cap // unit_risk_usd)

        # Risk-blocked condition: if not even 1 contract fits into the safety buffer
        if qty_max_buffer < 1:
            status = "blocked"
            termination_reason = "risk-blocked:dd-buffer"
            records.append(
                TradeRunRecord(
                    trade_index=idx,
                    trade_id=trade.trade_id,
                    entry_time=t_entry,
                    exit_time=t_exit,
                    direction=trade.direction,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    stop_points=stop_points,
                    quantity=0,
                    gross_pnl=0.0,
                    net_pnl=0.0,
                    balance_after=float(current_balance),
                    equity_after=float(state.equity),
                    floor_after=float(current_floor),
                    hwm_after=float(state.high_watermark),
                    intraday_mae_points=0.0,
                    intraday_equity_min=float(state.equity),
                    event_status="blocked",
                )
            )
            break

        # Desired quantity and capped quantity
        want_qty = max(1, int(round(float(nominal_risk_usd / unit_risk_usd))))
        executed_qty = max(1, min(want_qty, qty_max_buffer, max_contracts_limit))

        # 3. MAE lookup or computation (Block D3)
        mae_points = 0.0
        mae_ts = None
        if config.trailing_mode == "intraday_event":
            if precomputed_maes and trade.trade_id in precomputed_maes:
                mae_res = precomputed_maes[trade.trade_id]
                mae_points = mae_res.mae_points
                mae_ts = mae_res.mae_timestamp
            else:
                date_key = t_entry.strftime("%Y-%m-%d")
                m1_bars = m1_bars_by_date.get(date_key) if m1_bars_by_date else None
                mae_res = compute_trade_mae(trade, m1_bars=m1_bars, dollar_per_point=config.dollar_per_point)
                mae_points = mae_res.mae_points
                mae_ts = mae_res.mae_timestamp

            if mae_ts is None:
                mae_ts = t_entry
            elif mae_ts.tzinfo is None:
                mae_ts = mae_ts.replace(tzinfo=timezone.utc)
            mae_ts = min(max(t_entry, mae_ts), t_exit)

            # Synthesize intraday equity dip
            mae_dollars = Decimal(str(round(mae_points * config.dollar_per_point * executed_qty, 2)))
            intraday_equity = current_balance - mae_dollars

            # Apply intraday event to state
            intra_input = AccountRuleInput(
                timestamp=mae_ts,
                event_type="intraday",
                balance=current_balance,
                equity=intraday_equity,
                has_open_positions=True,
            )
            state.apply(intra_input)

            if state.terminal_breach:
                status = "blown"
                termination_reason = "maximum_loss_intraday_breach"
                records.append(
                    TradeRunRecord(
                        trade_index=idx,
                        trade_id=trade.trade_id,
                        entry_time=t_entry,
                        exit_time=t_exit,
                        direction=trade.direction,
                        entry_price=trade.entry_price,
                        exit_price=trade.exit_price,
                        stop_points=stop_points,
                        quantity=executed_qty,
                        gross_pnl=0.0,
                        net_pnl=float(-mae_dollars),
                        balance_after=float(current_balance),
                        equity_after=float(intraday_equity),
                        floor_after=float(state.maximum_loss_threshold or current_floor),
                        hwm_after=float(state.high_watermark),
                        intraday_mae_points=mae_points,
                        intraday_equity_min=float(intraday_equity),
                        event_status="intraday_breach",
                    )
                )
                break
        else:
            intraday_equity = current_balance

        # 4. Closed trade execution
        # Reconcile PnL per contract from ExecutedTrade
        trade_base_qty = trade.quantity if trade.quantity > 0 else 1
        net_per_contract = trade.net_pnl / trade_base_qty
        gross_per_contract = trade.gross_pnl / trade_base_qty

        realized_net = Decimal(str(round(net_per_contract * executed_qty, 2)))
        realized_gross = Decimal(str(round(gross_per_contract * executed_qty, 2)))
        new_balance = current_balance + realized_net

        trade_input = AccountRuleInput(
            timestamp=t_exit,
            event_type="closed_trade",
            balance=new_balance,
            equity=new_balance,
            realized_pnl=realized_net,
            trade_id=f"{trade.trade_id}-{idx}",
            trade_opened_at=t_entry,
            mini_contracts=0,
            micro_contracts=executed_qty,
        )
        report = state.apply(trade_input)

        # Track peak and drawdown
        bal_float = float(new_balance)
        peak_balance = max(peak_balance, bal_float)
        current_dd = peak_balance - bal_float
        max_dd_dollars = max(max_dd_dollars, current_dd)

        event_kind = report.primary_event.kind
        records.append(
            TradeRunRecord(
                trade_index=idx,
                trade_id=trade.trade_id,
                entry_time=t_entry,
                exit_time=t_exit,
                direction=trade.direction,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                stop_points=stop_points,
                quantity=executed_qty,
                gross_pnl=float(realized_gross),
                net_pnl=float(realized_net),
                balance_after=bal_float,
                equity_after=bal_float,
                floor_after=float(state.maximum_loss_threshold or 0),
                hwm_after=float(state.high_watermark),
                intraday_mae_points=mae_points,
                intraday_equity_min=float(intraday_equity),
                event_status=event_kind,
            )
        )

        if state.terminal_breach:
            status = "blown"
            termination_reason = "maximum_loss_closed_breach"
            break
        elif report.pass_eligible:
            status = "passed"
            termination_reason = "profit_target_reached"
            break

    # Calculate days to outcome
    first_ts = records[0].entry_time if records else sim_start
    last_ts = records[-1].exit_time if records else sim_start
    days_to_outcome = max(0.1, (last_ts - first_ts).total_seconds() / 86400.0)

    max_dd_pct = (max_dd_dollars / peak_balance) * 100.0 if peak_balance > 0 else 0.0

    return SingleAccountRunResult(
        account_id=account_id,
        profile_id=profile.profile_id,
        starting_balance=float(starting_balance),
        final_balance=float(state.balance),
        final_equity=float(state.equity),
        high_watermark=float(state.high_watermark),
        final_floor=float(state.maximum_loss_threshold or (starting_balance - profile.maximum_loss.distance.value)),
        status=status,
        termination_reason=termination_reason,
        trades_executed=len(records),
        days_to_outcome=round(days_to_outcome, 2),
        trading_days_count=len(state.trading_days),
        max_drawdown_dollars=round(max_dd_dollars, 2),
        max_drawdown_pct=round(max_dd_pct, 4),
        records=tuple(records),
    )


@dataclass(frozen=True)
class AccountMonteCarloSummary:
    """Statistical summary across N simulated paths for a funded account."""

    account_size: str
    risk_pct: float
    trailing_mode: str
    n_simulations: int
    horizon_trades: int
    pass_rate: float
    blown_rate: float
    blocked_rate: float
    timeout_rate: float
    median_days_to_pass: float | None
    p25_days_to_pass: float | None
    p75_days_to_pass: float | None
    median_max_drawdown_pct: float
    p95_max_drawdown_pct: float
    simulations_passed: int
    simulations_blown: int
    simulations_blocked: int
    simulations_timeout: int


def run_account_monte_carlo(
    profile: FundedAccountProfileV2,
    trades: Sequence[ExecutedTrade],
    config: AccountEngineConfig,
    *,
    n_simulations: int = 1000,
    seed: int = 20260729,
    horizon_trades: int | None = None,
    precomputed_maes: Mapping[str, TradeMaeResult] | None = None,
) -> AccountMonteCarloSummary:
    """Run Monte Carlo bootstrap paths (IID sampling of audited trades) through the account engine.

    Uses deterministic NumPy generator with the specified seed.
    """
    if not trades:
        raise ValueError("Cannot run Monte Carlo on empty trades sequence")

    # Compute trading frequency from historical trade sequence
    span_days = max(1.0, (trades[-1].exit_time - trades[0].entry_time).total_seconds() / 86400.0)
    trades_per_day = len(trades) / span_days

    if horizon_trades is None:
        horizon_trades = max(1, round(trades_per_day * config.horizon_calendar_days))

    rng = np.random.default_rng(seed)
    n_trades_source = len(trades)

    passed_count = 0
    blown_count = 0
    blocked_count = 0
    timeout_count = 0

    pass_days: list[float] = []
    max_dds_pct: list[float] = []

    for sim_idx in range(n_simulations):
        # Sample with replacement
        sampled_indices = rng.integers(0, n_trades_source, size=horizon_trades)
        sampled_trades = [trades[i] for i in sampled_indices]

        # Reset timestamps to synthesize a continuous chronological series
        sim_opened_at = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
        synthetic_trades = []
        cur_time = sim_opened_at

        for orig_t in sampled_trades:
            # Advance timestamp by the trade's duration or interval
            duration = max(timedelta(minutes=5), orig_t.exit_time - orig_t.entry_time)
            cur_time += timedelta(minutes=15)
            t_enter = cur_time
            t_exit = t_enter + duration
            cur_time = t_exit

            # Copy trade with synthetic timestamps
            synthetic_trades.append(
                ExecutedTrade(
                    trade_id=f"mc-{sim_idx}-{orig_t.trade_id}",
                    direction=orig_t.direction,
                    entry_time=t_enter,
                    exit_time=t_exit,
                    entry_price=orig_t.entry_price,
                    exit_price=orig_t.exit_price,
                    stop_price=orig_t.stop_price,
                    target_price=orig_t.target_price,
                    quantity=orig_t.quantity,
                    gross_pnl=orig_t.gross_pnl,
                    commission=orig_t.commission,
                    net_pnl=orig_t.net_pnl,
                    r_result=orig_t.r_result,
                    exit_reason=orig_t.exit_reason,
                    budgeted_risk_dollars=orig_t.budgeted_risk_dollars,
                    effective_risk_dollars=orig_t.effective_risk_dollars,
                )
            )

        # Build trade_id mapping for precomputed MAEs if available
        sim_mae_map: dict[str, TradeMaeResult] = {}
        if precomputed_maes:
            for synth_t, orig_t in zip(synthetic_trades, sampled_trades):
                if orig_t.trade_id in precomputed_maes:
                    orig_mae = precomputed_maes[orig_t.trade_id]
                    sim_mae_map[synth_t.trade_id] = TradeMaeResult(
                        trade_id=synth_t.trade_id,
                        mae_points=orig_mae.mae_points,
                        mae_dollars_per_contract=orig_mae.mae_dollars_per_contract,
                        mae_timestamp=min(synth_t.entry_time + timedelta(seconds=1), synth_t.exit_time),
                        mfe_points=orig_mae.mfe_points,
                        mfe_timestamp=synth_t.exit_time,
                        m1_bars_evaluated=orig_mae.m1_bars_evaluated,
                        resolution_mode=orig_mae.resolution_mode,
                    )

        res = run_account_simulation(
            profile=profile,
            trades=synthetic_trades,
            config=config,
            account_id=f"mc-{sim_idx}",
            opened_at=sim_opened_at,
            precomputed_maes=sim_mae_map if sim_mae_map else precomputed_maes,
        )

        max_dds_pct.append(res.max_drawdown_pct)
        if res.status == "passed":
            passed_count += 1
            pass_days.append(res.days_to_outcome)
        elif res.status == "blown":
            blown_count += 1
        elif res.status == "blocked":
            blocked_count += 1
        else:
            timeout_count += 1

    pass_rate = round(passed_count / n_simulations, 4)
    blown_rate = round(blown_count / n_simulations, 4)
    blocked_rate = round(blocked_count / n_simulations, 4)
    timeout_rate = round(timeout_count / n_simulations, 4)

    med_days = float(np.median(pass_days)) if pass_days else None
    p25_days = float(np.percentile(pass_days, 25)) if pass_days else None
    p75_days = float(np.percentile(pass_days, 75)) if pass_days else None

    med_dd = float(np.median(max_dds_pct)) if max_dds_pct else 0.0
    p95_dd = float(np.percentile(max_dds_pct, 95)) if max_dds_pct else 0.0

    return AccountMonteCarloSummary(
        account_size=profile.profile_id.split("-")[1],
        risk_pct=config.risk_pct,
        trailing_mode=config.trailing_mode,
        n_simulations=n_simulations,
        horizon_trades=horizon_trades,
        pass_rate=pass_rate,
        blown_rate=blown_rate,
        blocked_rate=blocked_rate,
        timeout_rate=timeout_rate,
        median_days_to_pass=round(med_days, 2) if med_days is not None else None,
        p25_days_to_pass=round(p25_days, 2) if p25_days is not None else None,
        p75_days_to_pass=round(p75_days, 2) if p75_days is not None else None,
        median_max_drawdown_pct=round(med_dd, 2),
        p95_max_drawdown_pct=round(p95_dd, 2),
        simulations_passed=passed_count,
        simulations_blown=blown_count,
        simulations_blocked=blocked_count,
        simulations_timeout=timeout_count,
    )


@dataclass(frozen=True)
class MultiAccountPortfolioResult:
    """Outcome of running N simultaneous accounts over the same signal stream."""

    accounts: tuple[SingleAccountRunResult, ...]
    total_accounts: int
    passed_count: int
    blown_count: int
    blocked_count: int
    timeout_count: int
    simultaneous_quema_risk_warning: str
    correlation_coefficient: float


def run_multi_account_portfolio(
    profiles: Sequence[FundedAccountProfileV2],
    trades: Sequence[ExecutedTrade],
    configs: Mapping[str, AccountEngineConfig],
    *,
    precomputed_maes: Mapping[str, TradeMaeResult] | None = None,
) -> MultiAccountPortfolioResult:
    """Run N simultaneous funded accounts over the same signal stream (Block D5).

    Explicitly models correlation: all accounts trade the exact same signals.
    """
    results: list[SingleAccountRunResult] = []

    for idx, profile in enumerate(profiles):
        acc_id = f"port-{profile.profile_id}-{idx + 1}"
        cfg = configs.get(profile.profile_id) or configs.get(profile.profile_id.split("-")[1])
        if cfg is None:
            # default fallback
            cfg = AccountEngineConfig(risk_pct=0.001946)

        res = run_account_simulation(
            profile=profile,
            trades=trades,
            config=cfg,
            account_id=acc_id,
            precomputed_maes=precomputed_maes,
        )
        results.append(res)

    passed = sum(1 for r in results if r.passed)
    blown = sum(1 for r in results if r.blown)
    blocked = sum(1 for r in results if r.blocked)
    timeout = sum(1 for r in results if r.status == "timeout")

    warning = (
        "ADVERTENCIA DE CORRELACION UNITARIA: Las cuentas que operan la misma senal "
        "en el mismo mercado tienen correlacion de quema ~ 1.0. La diversificacion de cuentas "
        "sin diversificacion de senales NO reduce el riesgo de quema conjunta; si una quema por "
        "drawdown en la senal, todas las de igual o menor colchon queman simultaneamente."
    )

    return MultiAccountPortfolioResult(
        accounts=tuple(results),
        total_accounts=len(results),
        passed_count=passed,
        blown_count=blown,
        blocked_count=blocked,
        timeout_count=timeout,
        simultaneous_quema_risk_warning=warning,
        correlation_coefficient=1.0,  # Same signal -> identical operational correlation
    )


__all__ = [
    "AccountEngineConfig",
    "TradeRunRecord",
    "SingleAccountRunResult",
    "AccountMonteCarloSummary",
    "MultiAccountPortfolioResult",
    "run_account_simulation",
    "run_account_monte_carlo",
    "run_multi_account_portfolio",
]
