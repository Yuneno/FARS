"""Precomputación y serialización de la caché causal de zonas con ATR point-in-time.

Permite que las 24+ celdas del protocolo M11 ejecuten en segundos en lugar de horas,
preservando al 100% la causalidad point-in-time y los resultados bit a bit.
"""

from __future__ import annotations

import gzip
import os
import pickle
import sys
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import CALIBRATION_BARS_COUNT, M5_MEMBER, ZIP_PATH, load_canonical_m5
from src.backtest.history import Bar
from src.hypothesis_registry import WalkForwardPlan
from src.zones.engine import ZoneEngine

M11_DIR = Path(__file__).resolve().parent
CACHE_DIR = M11_DIR / "cache"


def _precompute_single_fold(
    fold_id: int,
    bars_slice: list[Bar],
    symbol: str = "MNQ",
    timeframe: str = "5m",
) -> Path:
    """Precalcula la caché para un solo fold y la guarda en disco comprimida."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"fold_{fold_id}.pkl.gz"
    if cache_file.exists():
        return cache_file

    engine = ZoneEngine(
        symbol=symbol,
        timeframe=timeframe,
        session_pools=True,
    )
    ctx_cache: dict[datetime, dict[str, Any]] = {}
    anchor_cache: dict[datetime, dict[str, Any]] = {}
    growing: list[Bar] = []
    tr_list: list[float] = []

    for b in bars_slice:
        prev_close = growing[-1].close if growing else b.close
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        tr_list.append(tr)
        atr_14 = sum(tr_list[-14:]) / 14.0 if len(tr_list) >= 14 else None

        growing.append(b)
        engine.update(growing)
        ctx_cache[b.timestamp] = engine.context(price=b.close, atr=atr_14)

        meta: dict[str, Any] = {}
        builder = getattr(engine, "_session_builder", None)
        if builder is not None:
            for kind in (
                "prev_day_high",
                "prev_day_low",
                "d20_high",
                "d20_low",
                "overnight_high",
                "overnight_low",
            ):
                z = builder.get_current_zone(kind)
                if z is not None:
                    meta[kind] = {
                        "zone_id": z.zone_id,
                        "pattern_time": z.pattern_time,
                        "available_at": z.available_at,
                        "midpoint": z.midpoint,
                        "upper": z.upper,
                        "lower": z.lower,
                        "state": z.state,
                    }
        anchor_cache[b.timestamp] = meta

    payload = {
        "fold_id": fold_id,
        "ctx_cache": ctx_cache,
        "anchor_cache": anchor_cache,
    }
    with gzip.open(cache_file, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)

    return cache_file


def _worker_task(args: tuple[int, list[Bar], str, str]) -> tuple[int, str]:
    fold_id, bars_slice, symbol, timeframe = args
    t0 = datetime.now()
    path = _precompute_single_fold(fold_id, bars_slice, symbol, timeframe)
    elapsed = (datetime.now() - t0).total_seconds()
    return fold_id, f"Fold {fold_id} listo en {elapsed:.1f}s ({path.name})"


def ensure_zone_cache(
    bars: list[Bar],
    plan: WalkForwardPlan,
    symbol: str = "MNQ",
    timeframe: str = "5m",
    workers: int = 4,
) -> dict[int, tuple[dict[datetime, dict[str, Any]], dict[datetime, dict[str, Any]]]]:
    """Garantiza la existencia de la caché de zonas para todos los folds."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tasks = []
    needed_fold_ids = []

    for fold in plan.folds:
        cache_file = CACHE_DIR / f"fold_{fold.fold_id}.pkl.gz"
        if not cache_file.exists():
            test_cal = bars[max(0, fold.test_start_idx - CALIBRATION_BARS_COUNT) : fold.test_start_idx]
            test_bars = bars[fold.test_start_idx : fold.test_end_idx]
            full_slice = test_cal + test_bars
            tasks.append((fold.fold_id, full_slice, symbol, timeframe))
            needed_fold_ids.append(fold.fold_id)

    if tasks:
        print(f"[{datetime.now().isoformat()}] Precalculando caché de zonas en paralelo ({len(tasks)} folds, workers={workers})...", flush=True)
        num_workers = min(workers, len(tasks), os.cpu_count() or 4)
        with Pool(processes=num_workers) as pool:
            for fold_id, msg in pool.imap_unordered(_worker_task, tasks):
                print(f"  [{datetime.now().isoformat()}] {msg}", flush=True)

    # Cargar caches en memoria
    loaded = {}
    for fold in plan.folds:
        cache_file = CACHE_DIR / f"fold_{fold.fold_id}.pkl.gz"
        with gzip.open(cache_file, "rb") as f:
            data = pickle.load(f)
            loaded[fold.fold_id] = (data["ctx_cache"], data["anchor_cache"])

    return loaded


if __name__ == "__main__":
    bars, fingerprint = load_canonical_m5(ZIP_PATH, M5_MEMBER)
    plan = WalkForwardPlan.create_calendar_rolling(
        bars,
        train_months=36,
        test_months=6,
        step_months=6,
        purge_gap_bars=0,
        warmup_bars=CALIBRATION_BARS_COUNT,
    )
    ensure_zone_cache(bars, plan, workers=4)
    print("Precomputación completa de los 8 folds.")

