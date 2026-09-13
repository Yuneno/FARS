#!/usr/bin/env python
"""P7 BreakoutStrategy infrastructure benchmark - reproducible.

Reads the MNQ OHLCV CSVs directly from the Databento ZIP (streaming, no full
extraction), applies the canonical MNQ cut (timestamp >= 2019-05-06) and the
JobSpec warmup hold-out, then runs the BreakoutStrategy benchmark through
:func:`src.parallel.run_parallel`. One JSON artefact records configuration,
strategy parameters, dataset hash, effective range, bar counts, and load-time
vs execution-time separately.

MYM is deliberately NOT run: its provenance (Micro Dow vs the E-mini Dow ``YM``
raw feeds) is not yet confirmed, so it is excluded from valid results.

Usage (from the repo root, with the project venv):
    E:/FARS-LAB/.venv-fars/Scripts/python.exe \
        lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_p7.py \
        --zip E:/FARS-LAB/databento.zip \
        --out-dir lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_p7_out \
        --reps 3 --workers 6
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io as _io
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.backtest.history import Bar  # noqa: E402
from src.backtest.markets import get_market_spec  # noqa: E402
from src.parallel import (  # noqa: E402
    JobSpec,
    filter_job_bars,
    run_parallel,
)

CNQ_START = "2019-05-06"
CANONICAL_REF = "docs/refactor/canonical-dataset.md (MNQ post-launch cutoff 2019-05-06)"
CANONICAL_MIN_TIMESTAMP = "2019-05-06T00:00:00+00:00"
WARMUP_BARS = 20
MASTER_SEED = 42
STRATEGY_ID = "breakout"
PROFILE = "default"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_csv_member(zip_path: Path, member: str) -> tuple[list[dict], float]:
    """Stream one CSV member from the ZIP into a list of bar dicts."""
    started = time.perf_counter()
    rows: list[dict] = []
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as raw:
            reader = csv.DictReader(_io.TextIOWrapper(raw, encoding="utf-8"))
            for row in reader:
                rows.append({
                    "timestamp": row["timestamp"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
    return rows, time.perf_counter() - started


def effective_meta(bars_data: list[dict], *, range_start: str, warmup: int) -> dict:
    """Replicate the runner's canonical filter to verify range/warmup."""
    after_range = filter_job_bars(bars_data, range_start=range_start, range_end="", warmup_bars=0)
    evaluated = filter_job_bars(
        bars_data, range_start=range_start, range_end="", warmup_bars=warmup
    )
    return {
        "loaded": len(bars_data),
        "after_range": len(after_range),
        "after_warmup_evaluated": len(evaluated),
        "warmup_held_out": max(0, len(after_range) - len(evaluated)),
        "range_first": after_range[0].timestamp.isoformat() if after_range else None,
        "range_last": after_range[-1].timestamp.isoformat() if after_range else None,
        "evaluated_first": evaluated[0].timestamp.isoformat() if evaluated else None,
        "evaluated_last": evaluated[-1].timestamp.isoformat() if evaluated else None,
    }


def run(jobs: list[JobSpec], bars_by_job: dict[str, list[dict]], requested_workers: int):
    started = time.perf_counter()
    manifest = run_parallel(jobs, bars_by_job, max_workers=requested_workers)
    return {
        "wall_seconds": time.perf_counter() - started,
        "requested_workers": requested_workers,
        "effective_workers": manifest.n_workers,
        "sequential_fallback": requested_workers > 1 and manifest.n_workers == 1,
        "n_done": manifest.n_done,
        "n_errors": manifest.n_errors,
        "total_manifest_wall": manifest.total_wall_seconds,
        "manifest_hash": manifest.manifest_hash,
        "results": [
            {
                "job_id": r.job_id,
                "status": r.status,
                "n_trades": r.n_trades,
                "net_pnl": round(r.net_pnl, 4),
                "win_rate": round(r.win_rate, 6),
                "equity_curve_points": r.equity_curve_points,
                "job_wall_seconds": round(r.wall_seconds, 6),
                "worker_pid": r.worker_pid,
                "error": r.error_message,
            }
            for r in manifest.results
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    zip_path = args.zip.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict = {
        "schema_version": "fars-p7-benchmark-v1",
        "generated_utc": datetime.now(UTC).isoformat(),
        "git_commit": (
            __import__("subprocess").run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(REPO_ROOT), check=True, capture_output=True, text=True,
            ).stdout.strip()
        ),
        "are_previous_results_invalidated": True,
        "previous_results_ref": "lab_artifacts/CODEX_OMNIROUTE_RUN/benchmark_final.json (INVALID)",
        "strategy": {
            "class": "BreakoutStrategy",
            "lookback": 20,
            "stop_atr_mult": 1.5,
            "target_atr_mult": 3.0,
            "min_atr": 0.25,
            "provisional_placeholder": True,
            "not_smc_fvg_emas_crt_merged": True,
        },
        "canonical_cut": {
            "mnq_min_timestamp": CANONICAL_MIN_TIMESTAMP,
            "range_start_field": CNQ_START,
            "reference": CANONICAL_REF,
        },
        "warmup_bars": WARMUP_BARS,
        "master_seed": MASTER_SEED,
        "excluded_markets": {
            "MYM": "provenance unclarified (Micro Dow vs E-mini Dow YM raw feed); friction unvalidated"
        },
    }

    print("hashing zip ...")
    payload["dataset"] = {
        "path": str(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path),
    }

    jobs_spec = [("MNQ", "H4"), ("MNQ", "D1")]
    payload["jobs"] = {}

    for symbol, timeframe in jobs_spec:
        member = f"databento/{symbol}_{timeframe}.csv"
        label = f"{symbol}_{timeframe}"
        print(f"loading {member} ...")
        bars, load_seconds = load_csv_member(zip_path, member)
        market = get_market_spec(symbol)
        cfg = {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_config_id": STRATEGY_ID,
            "execution_profile": PROFILE,
            "warmup_bars": WARMUP_BARS,
            "master_seed": MASTER_SEED,
            "range_start": CNQ_START,
        }
        spec = JobSpec(
            dataset_fingerprint=payload["dataset"]["sha256"],
            symbol=symbol,
            timeframe=timeframe,
            range_start=CNQ_START,
            range_end="",
            warmup_bars=WARMUP_BARS,
            strategy_config_id=STRATEGY_ID,
            execution_profile=PROFILE,
            master_seed=MASTER_SEED,
        )
        meta = effective_meta(bars, range_start=CNQ_START, warmup=WARMUP_BARS)
        bars_by_job = {spec.job_id: bars}

        reps = []
        semantic = {"n_trades": None, "net_pnl": None}
        for rep in range(1, args.reps + 1):
            rep_res = run([spec], bars_by_job, requested_workers=args.workers)
            rep_res["rep_index"] = rep
            reps.append(rep_res)
            res = rep_res["results"][0]
            if res["status"] == "done":
                if semantic["n_trades"] is None:
                    semantic["n_trades"] = res["n_trades"]
                    semantic["net_pnl"] = res["net_pnl"]
                else:
                    semantic["deterministic_n_trades"] = (
                        semantic["n_trades"] == res["n_trades"]
                    )
                    semantic["deterministic_net_pnl"] = (
                        abs(semantic["net_pnl"] - res["net_pnl"]) < 1e-6
                    )

        config_from_scheme = __import__("src.parallel", fromlist=["build_market_config"]).build_market_config(symbol)
        payload["jobs"][label] = {
            "config": cfg,
            "job_id": spec.job_id,
            "market_spec": market.to_dict(),
            "market_config": asdict(config_from_scheme[0]),
            "cost_scheme": config_from_scheme[1],
            "load_seconds": round(load_seconds, 6),
            "effective_range": meta,
            "reps": reps,
            "determinism": semantic,
        }

    payload["validity"] = {
        "mnq_canonical_cut_applied": True,
        "per_market_config_applied": True,
        "mym_excluded": True,
        "results_valid": True,
        "completed_p7": False,
    }
    payload["pending"] = [
        "multiprocess validation: spawn falls back to sequential in this sandbox",
        "MYM provenance clarification before inclusion",
        "non-provisional strategy (SMC-FVG/EMAS/CRT on branch 50d0efc, never merged) excluded from this task",
    ]

    out_file = out_dir / "benchmark_p7.json"
    with out_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print("wrote", out_file)
    print(json.dumps(payload["jobs"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
