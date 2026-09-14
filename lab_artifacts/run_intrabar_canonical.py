#!/usr/bin/env python3
"""Canonical M1 intrabar ambiguity benchmark and audit (T1 & T2).

Evaluates:
- CRT-TBS (Champion: H4 bias, H1 CRT, M5 confirmation, fixed_rr=2.0)
- ORB (09:30-10:00 NY range, opposite stop, 2R target, max_hold=192 bars)

Under canonical MNQ M5 (518,237 bars) with time_exit_mode="market".

Architecture: Two-pass streaming approach to minimize memory and time:
- Pass 1 (Conservative): run backtest without M1; detect bars where both SL and TP
  are touched in the same M5 candle; resolve to stop_loss (conservative bound).
- Pass 2 (M1-resolved): selectively stream from E:/FARS-LAB/databento.zip (member
  databento/MNQ_M1.csv, timestamp >= 2019-05-06) ONLY the M1 bars corresponding to
  the ambiguous M5 timestamps. Re-run backtest with m1_bars.

Outputs:
- lab_artifacts/intrabar_canonical_audit.json
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import time
import tracemalloc
import zipfile
from datetime import UTC, datetime

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.crt_tbs import CrtTbsConfig, CrtTbsStrategy, crt_tbs_config
from src.backtest.executor import BacktestConfig, BacktestResult, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MNQ
from src.backtest.orb import OrbConfig, OrbStrategy, orb_config


def load_canonical_m5(zip_path: Path, member: str = "databento/MNQ_M5.csv") -> list[Bar]:
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str >= "2019-05-06":
                    bars.append(
                        Bar(
                            timestamp=datetime.fromisoformat(ts_str),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0)),
                        )
                    )
    return bars


def load_selective_m1(
    zip_path: Path,
    target_m5_timestamps: set[datetime],
    member: str = "databento/MNQ_M1.csv",
) -> list[Bar]:
    """Stream through M1 CSV and extract ONLY bars matching target M5 intervals."""
    if not target_m5_timestamps:
        return []

    target_dates = {ts.strftime("%Y-%m-%d") for ts in target_m5_timestamps}
    m1_bars: list[Bar] = []

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str[:10] in target_dates:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    m5_minute = (dt.minute // 5) * 5
                    m5_dt = dt.replace(minute=m5_minute, second=0, microsecond=0)
                    if m5_dt in target_m5_timestamps:
                        m1_bars.append(
                            Bar(
                                timestamp=dt,
                                open=float(row["open"]),
                                high=float(row["high"]),
                                low=float(row["low"]),
                                close=float(row["close"]),
                                volume=float(row.get("volume", 0)),
                            )
                        )
    return m1_bars


def build_strategy_and_config(strat_id: str) -> tuple[object, BacktestConfig]:
    if strat_id == "crt_tbs_champion":
        strat_cfg = CrtTbsConfig(
            require_4h_bias=True,
            require_half_zone=False,
            target_mode="fixed_rr",
            fixed_rr=2.0,
        )
        strategy = CrtTbsStrategy(strat_cfg)
        cfg = crt_tbs_config(
            market=MNQ,
            commission_per_side=2.0,
            slippage_points=0.0,
            time_exit_mode="market",
        )
        return strategy, cfg
    elif strat_id == "orb":
        strat_cfg = OrbConfig(
            or_minutes=30,
            stop_mode="opposite",
            rr=2.0,
            use_bias=False,
            fade=False,
            end_hour=16,
            max_hold_bars=192,
            time_exit_mode="market",
        )
        strategy = OrbStrategy(strat_cfg)
        cfg = orb_config(
            market=MNQ,
            commission_per_side=2.0,
            slippage_points=0.0,
            max_bars_held=192,
            time_exit_mode="market",
        )
        return strategy, cfg
    else:
        raise ValueError(f"Unknown strategy: {strat_id}")


def extract_metrics(res: BacktestResult) -> dict:
    time_exits = [t for t in res.trades if t.exit_reason == "time_exit"]
    return {
        "n_trades": res.n_trades,
        "n_time_exits": len(time_exits),
        "win_rate": round(res.win_rate, 4),
        "profit_factor": (
            round(res.profit_factor, 4) if res.profit_factor != float("inf") else "inf"
        ),
        "net_pnl": round(res.net_pnl, 2),
        "expectancy": round(res.expectancy, 2),
        "max_drawdown_pct": round(res.max_drawdown_pct, 4),
        "total_commission": round(res.total_commission, 2),
    }


def main():
    tracemalloc.start()
    t_start = time.perf_counter()

    zip_path = Path("E:/FARS-LAB/databento.zip")
    print(f"Loading canonical M5 bars from {zip_path}...")
    t0 = time.perf_counter()
    bars = load_canonical_m5(zip_path)
    t_load_m5 = time.perf_counter() - t0
    print(f"Loaded {len(bars)} M5 bars in {t_load_m5:.2f}s")

    strategies = [
        ("CRT-TBS (Champion)", "crt_tbs_champion"),
        ("ORB (Experimental)", "orb"),
    ]

    results_data = {
        "metadata": {
            "dataset_m5": "databento/MNQ_M5.csv (timestamp >= 2019-05-06)",
            "dataset_m1": "databento/MNQ_M1.csv (timestamp >= 2019-05-06)",
            "total_m5_bars": len(bars),
            "generated_at": datetime.now(UTC).isoformat(),
            "time_exit_mode": "market",
            "friction": "$4.00 RT commission, 0.0 slippage",
            "approach": "two_pass_selective_m1_streaming",
            "conservative_bound_definition": (
                "When SL and TP fall within the same M5 bar, resolve to stop_loss "
                "if M1 data is absent or if both levels remain touched in the same M1 candle."
            ),
        },
        "benchmarks": {},
    }

    all_ambiguous_timestamps: set[datetime] = set()
    pass1_results: dict[str, tuple[BacktestResult, set[datetime]]] = {}

    print("\n" + "=" * 80)
    print("PASS 1: Evaluating strategies under conservative bound (without M1)...")
    print("=" * 80)
    for label, strat_id in strategies:
        strat, cfg = build_strategy_and_config(strat_id)
        t_strat = time.perf_counter()
        res = run_backtest(bars, strat, cfg)
        elapsed = time.perf_counter() - t_strat
        amb_ts = set(res.intrabar_audit.ambiguous_timestamps)
        all_ambiguous_timestamps.update(amb_ts)
        pass1_results[strat_id] = (res, amb_ts)
        print(
            f"  {label:<22} | Trades: {res.n_trades:>5} | Net PnL: ${res.net_pnl:>10,.2f} | "
            f"Ambiguous bars: {res.intrabar_audit.ambiguous_bars_count:>3} | Elapsed: {elapsed:.2f}s"
        )

    print(f"\nTotal unique ambiguous M5 timestamps across all strategies: {len(all_ambiguous_timestamps)}")
    for ts in sorted(all_ambiguous_timestamps):
        print(f"  - {ts.isoformat()}")

    print("\n" + "=" * 80)
    print("STREAMING: Loading selective M1 bars for ambiguous intervals only...")
    print("=" * 80)
    t_m1 = time.perf_counter()
    selective_m1_bars = load_selective_m1(zip_path, all_ambiguous_timestamps)
    elapsed_m1 = time.perf_counter() - t_m1
    print(f"Loaded {len(selective_m1_bars)} constituent M1 bars in {elapsed_m1:.2f}s")
    for b in selective_m1_bars:
        print(f"  M1 bar: {b.timestamp.isoformat()} | O: {b.open} | H: {b.high} | L: {b.low} | C: {b.close}")

    print("\n" + "=" * 80)
    print("PASS 2: Re-evaluating strategies with M1 resolution...")
    print("=" * 80)
    for label, strat_id in strategies:
        strat, cfg = build_strategy_and_config(strat_id)
        res_pass1, amb_ts = pass1_results[strat_id]
        
        t_strat = time.perf_counter()
        res_m1 = run_backtest(bars, strat, cfg, m1_bars=selective_m1_bars)
        elapsed = time.perf_counter() - t_strat

        audit = res_m1.intrabar_audit
        audit_dict = {
            "total_bars_evaluated": audit.total_bars_evaluated,
            "bars_in_position": audit.bars_in_position,
            "ambiguous_bars_count": audit.ambiguous_bars_count,
            "resolved_by_m1_stop": audit.resolved_by_m1_stop,
            "resolved_by_m1_target": audit.resolved_by_m1_target,
            "m1_residual_ambiguity": audit.m1_residual_ambiguity,
            "no_m1_data_fallback": audit.no_m1_data_fallback,
            "ambiguous_pct_of_all_bars": round(audit.ambiguous_pct_of_all_bars, 6),
            "ambiguous_pct_of_bars_in_position": round(audit.ambiguous_pct_of_bars_in_position, 6),
            "ambiguous_bars_pct": round(audit.ambiguous_bars_pct, 6),
            "resolution_rate_pct": round(audit.resolution_rate_pct, 2),
            "ambiguous_timestamps": [ts.isoformat() for ts in audit.ambiguous_timestamps],
        }

        m_pass1 = extract_metrics(res_pass1)
        m_m1 = extract_metrics(res_m1)

        results_data["benchmarks"][strat_id] = {
            "strategy_label": label,
            "intrabar_audit": audit_dict,
            "metrics_conservative_stop": m_pass1,
            "metrics_m1_resolved": m_m1,
            "delta": {
                "net_pnl": round(m_m1["net_pnl"] - m_pass1["net_pnl"], 2),
                "profit_factor": (
                    round(m_m1["profit_factor"] - m_pass1["profit_factor"], 4)
                    if isinstance(m_m1["profit_factor"], (int, float))
                    and isinstance(m_pass1["profit_factor"], (int, float))
                    else 0.0
                ),
                "win_rate": round(m_m1["win_rate"] - m_pass1["win_rate"], 4),
            },
        }

        print(
            f"  {label:<22} | M1 Resolved Trades: {res_m1.n_trades:>5} | Net PnL: ${res_m1.net_pnl:>10,.2f} | "
            f"Res Rate: {audit.resolution_rate_pct:.1f}% | Elapsed: {elapsed:.2f}s"
        )

    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    total_time = time.perf_counter() - t_start

    cost_metrics = {
        "total_elapsed_seconds": round(total_time, 2),
        "peak_ram_mb": round(peak_mem / (1024 * 1024), 2),
        "current_ram_mb": round(current_mem / (1024 * 1024), 2),
        "m1_streaming_seconds": round(elapsed_m1, 2),
        "m1_bars_extracted": len(selective_m1_bars),
    }
    results_data["performance_and_cost"] = cost_metrics

    out_json = REPO_ROOT / "lab_artifacts" / "intrabar_canonical_audit.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results_data, f, indent=2)
    print(f"\nSaved canonical audit JSON to {out_json}")

    print("\n" + "=" * 90)
    print("RESUMEN DE AUDITORIA INTRABARRA CANONICA (M5 + M1):")
    print("=" * 90)
    for strat_id, entry in results_data["benchmarks"].items():
        lbl = entry["strategy_label"]
        aud = entry["intrabar_audit"]
        m_c = entry["metrics_conservative_stop"]
        m_m = entry["metrics_m1_resolved"]
        d = entry["delta"]
        print(f"\nEstrategia: {lbl}")
        print(f"  Total Barras Evaluadas:  {aud['total_bars_evaluated']:,}")
        print(f"  Barras con Posicion:     {aud['bars_in_position']:,}")
        print(f"  Barras Ambiguas:         {aud['ambiguous_bars_count']}")
        print(f"  Tasa (sobre total):      {aud['ambiguous_pct_of_all_bars']}%")
        print(f"  Tasa (sobre posicion):   {aud['ambiguous_pct_of_bars_in_position']}%")
        print(f"  Resueltas M1 -> Stop:    {aud['resolved_by_m1_stop']}")
        print(f"  Resueltas M1 -> Target:  {aud['resolved_by_m1_target']}")
        print(f"  Ambiguedad Residual M1:  {aud['m1_residual_ambiguity']}")
        print(f"  Fallback sin M1:         {aud['no_m1_data_fallback']}")
        print(f"  Tasa de Resolucion:      {aud['resolution_rate_pct']}%")
        print(f"  Metricas Comparativas:")
        print(f"    - Cota Conservadora (SL): PnL=${m_c['net_pnl']:,.2f} | PF={m_c['profit_factor']} | WR={m_c['win_rate']*100:.2f}%")
        print(f"    - Resuelta por M1:        PnL=${m_m['net_pnl']:,.2f} | PF={m_m['profit_factor']} | WR={m_m['win_rate']*100:.2f}%")
        print(f"    - Delta Atribuido:        PnL=+${d['net_pnl']:,.2f} | PF={d['profit_factor']:+.4f} | WR={d['win_rate']*100:+.2f}%")

    print("\nCoste de Ejecucion:")
    print(f"  Tiempo Total: {cost_metrics['total_elapsed_seconds']}s")
    print(f"  Pico de RAM:  {cost_metrics['peak_ram_mb']} MB")
    print(f"  Streaming M1: {cost_metrics['m1_streaming_seconds']}s (extrajo {cost_metrics['m1_bars_extracted']} barras M1 de 2,589,531)")
    print("=" * 90)


if __name__ == "__main__":
    main()
