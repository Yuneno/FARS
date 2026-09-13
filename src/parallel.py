"""Deterministic parallel runner for FARS backtest jobs (P2).

Wraps the sequential backtest runner with ProcessPoolExecutor (spawn context).
Each job is a self-contained unit of work with deterministic seeds and identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

from src.types import FundedAccountRules

from src.backtest.markets import get_market_spec


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------

_SEED_SCHEMA_VERSION = "1.0"
MAX_WORKERS = 6

# Cost schemes produced by :func:`build_market_config`. "market_friction"
# derives commission from MarketSpec.friction_points with the canonical
# round-trip aggregation (commission = friction / 2 per side, zero
# slippage); "explicit_friction" is an operator-supplied scenario;
# "provisional" is the legacy MVP scenario used only for markets whose
# friction is unvalidated. Costs are never silent.
COST_SCHEME_FRICTION = "market_friction"
COST_SCHEME_EXPLICIT = "explicit_friction"
COST_SCHEME_PROVISIONAL = "provisional"


def derive_job_seed(master_seed: int, job_id: str) -> int:
    """Derive a deterministic job_seed from master_seed and job_id.

    job_seed = first 8 bytes of SHA-256(seed_schema_version, master_seed, job_id),
    interpreted as unsigned big-endian.
    """
    canonical = json.dumps(
        {"v": _SEED_SCHEMA_VERSION, "seed": master_seed, "job_id": job_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


# ---------------------------------------------------------------------------
# Job specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobSpec:
    """Immutable specification for a single parallelizable backtest job."""

    dataset_fingerprint: str
    symbol: str
    timeframe: str
    range_start: str
    range_end: str
    warmup_bars: int
    strategy_config_id: str
    execution_profile: str
    friction_pts: float | None = None
    fold_id: str | None = None
    replicate_id: str | None = None
    master_seed: int = 42
    output_dir: str = ""

    @property
    def job_id(self) -> str:
        """Deterministic job_id from canonical semantic identity."""
        obj = {
            "dataset_fp": self.dataset_fingerprint,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "range": [self.range_start, self.range_end],
            "warmup": self.warmup_bars,
            "strategy": self.strategy_config_id,
            "profile": self.execution_profile,
        }
        if self.fold_id is not None:
            obj["fold"] = self.fold_id
        if self.replicate_id is not None:
            obj["replicate"] = self.replicate_id
        if self.friction_pts is not None:
            obj["friction"] = self.friction_pts
        raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @property
    def job_seed(self) -> int:
        return derive_job_seed(self.master_seed, self.job_id)


# ---------------------------------------------------------------------------
# Job result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobResult:
    """Typed result for a single completed job."""

    job_id: str
    status: Literal["done", "error", "timeout"]
    n_trades: int = 0
    net_pnl: float = 0.0
    win_rate: float = 0.0
    equity_curve_points: int = 0
    wall_seconds: float = 0.0
    error_message: str | None = None
    worker_pid: int | None = None


# ---------------------------------------------------------------------------
# Worker function (module-level for pickling)
# ---------------------------------------------------------------------------


def _limit_threads():
    """Limit BLAS/OpenMP/NumExpr to 1 thread per worker before imports."""
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        os.environ[var] = "1"


def filter_job_bars(
    bars_data: list[dict[str, Any]],
    *,
    range_start: str = "",
    range_end: str = "",
    warmup_bars: int = 0,
) -> "list":
    """Apply a job's range and warmup filters in a verifiable, fixed order.

    Order is canonical: (1) full range filter (>= range_start, <= range_end),
    then (2) hold out the first ``warmup_bars`` bars of the filtered range so
    they cannot produce evaluated trades. Range-first / warmup-second guarantees
    the warmup hold-out is relative to the effective evaluation window and never
    silently consumes bars outside it.
    """
    from datetime import datetime, timezone as _tz
    from src.backtest.history import Bar

    bars = [
        Bar(
            timestamp=datetime.fromisoformat(bd["timestamp"]),
            open=bd["open"],
            high=bd["high"],
            low=bd["low"],
            close=bd["close"],
            volume=bd.get("volume", 0),
        )
        for bd in bars_data
    ]
    tzinfo = bars[0].timestamp.tzinfo if bars else None
    if range_start and bars:
        rs = datetime.fromisoformat(range_start)
        if rs.tzinfo is None and tzinfo is not None:
            rs = rs.replace(tzinfo=_tz.utc)
        bars = [b for b in bars if b.timestamp >= rs]
    if range_end and bars:
        re_ = datetime.fromisoformat(range_end)
        if re_.tzinfo is None and tzinfo is not None:
            re_ = re_.replace(tzinfo=_tz.utc)
        bars = [b for b in bars if b.timestamp <= re_]
    if warmup_bars > 0:
        cut = min(warmup_bars, len(bars))
        bars = bars[cut:]
    return bars


def build_market_config(
    symbol: str,
    *,
    initial_balance: float = 50_000.0,
    risk_per_trade: float = 0.01,
    friction_pts: float | None = None,
    fixed_quantity: int | None = None,
) -> "tuple":
    """Build a per-market executor config, reusing :class:`MarketSpec`.

    dollar_per_point, tick_size, and quantity sizing come from the market spec;
    costs follow the canonical round-trip friction aggregation (commission =
    friction_per_point / 2 per side, zero slippage) so costs are never
    double-counted. Markets whose friction is unvalidated
    (``MarketSpec.friction_points is None``) require an explicit ``friction_pts``;
    otherwise they fall back to the PROVISIONAL legacy cost scenario and the
    returned cost scheme is ``provisional`` (never a silent zero-cost run).
    """
    from src.backtest.executor import BacktestConfig

    market = get_market_spec(symbol)
    dollar_per_point = market.dollar_per_point
    friction = friction_pts if friction_pts is not None else market.friction_points
    if friction is not None and friction >= 0:
        commission = friction * dollar_per_point / 2.0
        slippage = 0.0
        scheme = COST_SCHEME_FRICTION if friction_pts is None else COST_SCHEME_EXPLICIT
    else:
        commission = 0.62
        slippage = 0.25
        scheme = COST_SCHEME_PROVISIONAL
    return (
        BacktestConfig(
            initial_balance=initial_balance,
            risk_per_trade=risk_per_trade,
            dollar_per_point=dollar_per_point,
            tick_size=market.tick_size,
            commission_per_side=commission,
            slippage_points=slippage,
            fixed_quantity=fixed_quantity,
        ),
        scheme,
    )


def _run_single_job(
    spec_dict: dict[str, Any],
    bars_data: list[dict[str, Any]],
    rules_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    _limit_threads()
    t0 = time.perf_counter()
    SUPPORTED_STRATEGIES = {"breakout_v1", "breakout"}
    SUPPORTED_PROFILES = {"legacy", "default"}
    try:
        from src.backtest.executor import run_backtest
        from src.backtest.strategy import BreakoutStrategy
        spec = JobSpec(**spec_dict)
        if spec.strategy_config_id not in SUPPORTED_STRATEGIES:
            elapsed = time.perf_counter() - t0
            return {"job_id": spec.job_id, "status": "error", "n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "equity_curve_points": 0, "wall_seconds": elapsed, "error_message": f"unsupported strategy: {spec.strategy_config_id!r}", "worker_pid": os.getpid()}
        if spec.execution_profile not in SUPPORTED_PROFILES:
            elapsed = time.perf_counter() - t0
            return {"job_id": spec.job_id, "status": "error", "n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "equity_curve_points": 0, "wall_seconds": elapsed, "error_message": f"unsupported profile: {spec.execution_profile!r}", "worker_pid": os.getpid()}
        bars = filter_job_bars(
            bars_data,
            range_start=spec.range_start,
            range_end=spec.range_end,
            warmup_bars=spec.warmup_bars,
        )
        if len(bars) < 2:
            elapsed = time.perf_counter() - t0
            return {"job_id": spec.job_id, "status": "done", "n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "equity_curve_points": 0, "wall_seconds": elapsed, "error_message": None, "worker_pid": os.getpid()}
        rules = rules_dict or {}
        config, _cost_scheme = build_market_config(
            spec.symbol,
            initial_balance=rules.get("initial_balance", 50000),
            risk_per_trade=rules.get("risk_per_trade", 0.01),
            friction_pts=spec.friction_pts,
        )
        result = run_backtest(bars, BreakoutStrategy(), config)
        elapsed = time.perf_counter() - t0
        return {"job_id": spec.job_id, "status": "done", "n_trades": result.n_trades, "net_pnl": result.net_pnl, "win_rate": result.win_rate, "equity_curve_points": len(result.equity_curve), "wall_seconds": elapsed, "error_message": None, "worker_pid": os.getpid()}
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {"job_id": spec_dict.get("job_id", "unknown"), "status": "error", "n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "equity_curve_points": 0, "wall_seconds": elapsed, "error_message": str(exc), "worker_pid": os.getpid()}


@dataclass(frozen=True)
class RunManifest:
    """Summary of a parallel run for auditability."""

    master_seed: int
    n_workers: int
    n_jobs: int
    n_done: int
    n_errors: int
    total_wall_seconds: float
    results: tuple[JobResult, ...]
    manifest_hash: str = ""

    def __post_init__(self):
        if not self.manifest_hash:
            canonical = json.dumps(
                [asdict(r) for r in self.results],
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            h = hashlib.sha256(canonical).hexdigest()
            object.__setattr__(self, "manifest_hash", h)


def _run_sequential(
    jobs: list[JobSpec],
    bars_by_job: dict[str, list[dict[str, Any]]],
    rules_dict: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    """Run jobs sequentially in the current process (fallback for sandboxed envs)."""
    results = {}
    for spec in jobs:
        bars = bars_by_job.get(spec.job_id, [])
        res = _run_single_job(asdict(spec), bars, rules_dict)
        results[spec.job_id] = res
    return results


def _run_concurrent(
    jobs: list[JobSpec],
    bars_by_job: dict[str, list[dict[str, Any]]],
    effective_workers: int,
    rules_dict: dict[str, Any] | None,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    """Run jobs using ProcessPoolExecutor with spawn context."""
    import multiprocessing as _mp
    results: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(
        max_workers=effective_workers,
        mp_context=_mp.get_context("spawn"),
    ) as executor:
        future_to_job = {}
        for spec in jobs:
            bars = bars_by_job.get(spec.job_id, [])
            future = executor.submit(_run_single_job, asdict(spec), bars, rules_dict)
            future_to_job[future] = spec
        for future in as_completed(future_to_job, timeout=timeout_seconds):
            spec = future_to_job[future]
            try:
                results[spec.job_id] = future.result(timeout=timeout_seconds)
            except Exception as exc:
                results[spec.job_id] = {
                    "job_id": spec.job_id, "status": "error",
                    "n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
                    "equity_curve_points": 0, "wall_seconds": 0.0,
                    "error_message": str(exc), "worker_pid": None,
                }
    return results


def run_parallel(
    jobs: list[JobSpec],
    bars_by_job: dict[str, list[dict[str, Any]]],
    *,
    max_workers: int = 1,
    timeout_seconds: float = 600.0,
    rules: FundedAccountRules | None = None,
) -> RunManifest:
    """Run multiple jobs in parallel (or sequentially in sandboxed envs).

    Parameters
    ----------
    jobs : list[JobSpec]
        Jobs to execute. max_workers is clamped to len(jobs).
    bars_by_job : dict[str, list[dict[str, Any]]]
        Serialized bars keyed by job_id.
    max_workers : int
        Number of worker processes (clamped to min(max_workers, len(jobs))).
    timeout_seconds : float
        Per-job timeout.
    rules : FundedAccountRules, optional
        Account rules for each job.

    Returns
    -------
    RunManifest
        Deterministically ordered results (by job_id).
    """
    if not isinstance(max_workers, int) or max_workers < 1:
        raise ValueError(f"max_workers must be a positive integer, got {max_workers}")
    if not jobs:
        return RunManifest(
            master_seed=0, n_workers=0, n_jobs=0, n_done=0, n_errors=0,
            total_wall_seconds=0.0, results=(),
        )

    effective_workers = max(1, min(max_workers, 6, len(jobs)))
    rules_dict = asdict(rules) if rules else None
    t0 = time.perf_counter()

    # Try multiprocessing; fall back to sequential on sandbox/permission errors
    try:
        results = _run_concurrent(jobs, bars_by_job, effective_workers, rules_dict, timeout_seconds)
    except (PermissionError, OSError):
        effective_workers = 1
        results = _run_sequential(jobs, bars_by_job, rules_dict, timeout_seconds)

    total_wall = time.perf_counter() - t0

    # Build ordered results (deterministic: sorted by job_id)
    ordered = []
    for spec in sorted(jobs, key=lambda j: j.job_id):
        res = results.get(spec.job_id, {
            "job_id": spec.job_id, "status": "error",
            "n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
            "equity_curve_points": 0, "wall_seconds": 0.0,
            "error_message": "job not submitted", "worker_pid": None,
        })
        ordered.append(JobResult(
            job_id=res["job_id"], status=res["status"],
            n_trades=res["n_trades"], net_pnl=res["net_pnl"],
            win_rate=res["win_rate"],
            equity_curve_points=res["equity_curve_points"],
            wall_seconds=res["wall_seconds"],
            error_message=res.get("error_message"),
            worker_pid=res.get("worker_pid"),
        ))

    n_done = sum(1 for r in ordered if r.status == "done")
    n_errors = sum(1 for r in ordered if r.status == "error")

    return RunManifest(
        master_seed=jobs[0].master_seed if jobs else 0,
        n_workers=effective_workers,
        n_jobs=len(jobs),
        n_done=n_done,
        n_errors=n_errors,
        total_wall_seconds=total_wall,
        results=tuple(ordered),
    )


__all__ = [
    "COST_SCHEME_EXPLICIT",
    "COST_SCHEME_FRICTION",
    "COST_SCHEME_PROVISIONAL",
    "JobSpec",
    "JobResult",
    "RunManifest",
    "build_market_config",
    "derive_job_seed",
    "filter_job_bars",
    "run_parallel",
]
