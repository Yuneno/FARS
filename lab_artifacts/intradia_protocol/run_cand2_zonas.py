#!/usr/bin/env python3
"""MIRA 2 / Candidata 2 — harness intradia para zona_rebote con flat diario.

Lee el preregistro congelado lab_artifacts/intradia_protocol/PREREGISTRO_INTRADIA_V2.md
(§"Candidata 2") y ejecuta la senal de rebote de zona activa con gestion
stop-borde-lejano / target-pool-de-sesion bajo flat diario (toda posicion se
cierra a las 15:10 CT; nada cruza de sesion).

SIN tocar src/: el flat por sesion se implementa como corrida por-dia con warmup
(slice = 500 barras previas + barras de la sesion hasta las 15:10 CT inclusive),
cada slice corrido con run_backtest + end_of_data_policy="close". Las zonas y el
ATR ven el warmup (identicos a corrida continua) pero el wrapper suprime senales
en el.

Estructura copiada de run_cand1_emas.py (mira 1); cambia SOLO la capa de senal
(EmasStrategy -> ZonasReboteStrategy) mas la atribucion por tipo de zona y el
log de corrida. La R por trade sale del executor sin recalculo
(budget-R = net_pnl / (risk_per_trade * initial_balance)).

Yardstick: lab_artifacts/orb_protocol/bootstrap_camino.py con la convencion de
lab_artifacts/orb_protocol/t1_orb_topstep_50k.py (bloque `correr` copiado).

NO corre nada al importar. Solo escribe bajo lab_artifacts/intradia_protocol/.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import math
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ORB_DIR = REPO_ROOT / "lab_artifacts" / "orb_protocol"
if str(ORB_DIR) not in sys.path:
    sys.path.insert(0, str(ORB_DIR))

from lab_artifacts.run_c1_walkforward import (  # noqa: E402
    CALIBRATION_BARS_COUNT,
    ZIP_PATH,
    CircularBlockBootstrap,
    compute_file_sha256,
    run_bootstrap_ci,
)
from src.backtest.emas import ATR_PERIOD, emas_config  # noqa: E402
from src.backtest.executor import run_backtest  # noqa: E402
from src.backtest.history import Bar  # noqa: E402
from src.backtest.markets import MNQ  # noqa: E402
from src.backtest.strategy import Signal  # noqa: E402
from src.funded_profiles import topstep_50k_profile  # noqa: E402
from src.hypothesis_registry import WalkForwardPlan  # noqa: E402
from src.session_calendar import session_date  # noqa: E402
from src.zones.engine import ZoneEngine  # noqa: E402

try:
    from src.bootstrap import optimal_block_length  # noqa: E402
except ImportError:  # fallback: misma funcion que usa bootstrap_camino.py
    from arch.bootstrap import optimal_block_length  # noqa: E402

HERE = Path(__file__).resolve().parent
PREREGISTRO_PATH = HERE / "PREREGISTRO_INTRADIA_V2.md"
PREREGISTRO_SHA256 = "82330fcdec4cfc9ae1e96e4ea4f0d32c62367bd8000c4d77dd50aa68441f9e1a"

MNQ_MEMBER = "databento/MNQ_M5.csv"
BAR_SECONDS = 300
DEV_FOLDS = 6  # fold_id 0..5; fold_id 6..7 = validacion SELLADA
WARMUP_BARS = CALIBRATION_BARS_COUNT  # 500, identico a H1/C1/mira 1

# ── parametros congelados de la Candidata 2 (§"Candidata 2" del preregistro) ──
ATR_K = 0.5  # stop = borde lejano ∓ 0.5 * ATR(14)
TARGET_R_MIN = 1.0  # ventana del pool: 1R <= d <= 5R (bordes inclusivos)
TARGET_R_MAX = 5.0
MIN_HISTORY_BARS = ATR_PERIOD + 1  # 15: 14 true ranges para el primer ATR(14)
# Pools candidatos: solo los 4 anclajes literales del preregistro
# (PDH/PDL del dia previo + ONH/ONL del dia actual). D20 queda fuera.
POOL_ANCHORS = frozenset({
    "prev_day_high", "prev_day_low", "overnight_high", "overnight_low",
})

CT = ZoneInfo("America/Chicago")
FLAT_HOUR_CT = 15
FLAT_MINUTE_CT = 10

MASTER_SEED = 20260928
B_REPLICATES = 2000

# Yardstick (convencion T1): 1 contrato MNQ, 1R == PTO USD.
PTO = 200.0  # stop_dollars MNQ = 100 pts * $2/pto (identico a T1)
K_CONTRACTS = 1
NO_DLL = 25_000.0  # "sin DLL": 0.5*balance, nunca bindea (convencion T1)

# Pines identicos a run_cand1_emas.py (brazo h1_targetpuro_f0 + flat 15:10 CT).
PINS = {
    "initial_balance": 50_000.0,
    "risk_per_trade": 0.01,
    "dollar_per_point": MNQ.dollar_per_point,
    "tick_size": MNQ.tick_size,
    "max_contracts": 1_000_000,
    "commission_per_side": 0.62,
    "slippage_points": 0.25,
    "time_exit_slippage_points": 0.25,
    "end_of_data_slippage_points": 0.25,
    "profit_target_pct": 0.06,
    "max_drawdown_pct": 0.06,
    "daily_loss_limit_pct": 0.03,
    "max_trades": None,
    "max_bars_held": 1_000_000_000,
    "max_hold_minutes": None,
    "fixed_quantity": None,
    "bar_interval_seconds": BAR_SECONDS,
    "pending_limit_entry": False,
    "pending_order_wait_bars": 0,
    "cooldown_bars": 0,
    "time_exit_mode": "market",
    "session_date_for_ledger": False,
    "end_of_data_policy": "close",
    "move_stop_to_break_even": False,  # sin BE: ruta legacy del executor
}
PARTIAL_F = 0.0  # target de pool (sin parciales), via niveles absolutos

TICK_POINTS = 0.25  # MNQ: 1 tick = 0.25 pts
SLIP_LEVELS_TICKS = (0, 1, 2)  # base, +1 tick/lado, +2 ticks/lado


def _slip_suffix(extra_ticks: int) -> str:
    return "base" if extra_ticks == 0 else f"slip+{extra_ticks}ticks"


# ── carga de datos copiada tal cual de run_h1.py:95-120 (via mira 1) ──────────
def load_market_bars_from_zip(zip_path: Path, member_name: str) -> tuple[list[Bar], str, str, str]:
    """Lee barras M5 por streaming desde el zip canonico, calcula sha256 y filtra >= 2019-05-06."""
    hasher = hashlib.sha256()
    bars: list[Bar] = []
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member_name) as raw:
            content_bytes = raw.read()
            hasher.update(content_bytes)
            reader = csv.DictReader(io.StringIO(content_bytes.decode("utf-8")))
            for row in reader:
                ts_str = row["timestamp"]
                if ts_str >= "2019-05-06":
                    bars.append(
                        Bar(
                            timestamp=datetime.fromisoformat(ts_str.replace("Z", "+00:00")),
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 0)),
                        )
                    )
    file_sha = hasher.hexdigest()
    first_ts = bars[0].timestamp.isoformat() if bars else "N/A"
    last_ts = bars[-1].timestamp.isoformat() if bars else "N/A"
    return bars, file_sha, first_ts, last_ts


# ── capa de senal: ZonasReboteStrategy (§"Candidata 2" del preregistro) ────────
@dataclass(frozen=True)
class Cand2Decision:
    """Snapshot observacional de una senal aceptada (una por zona como maximo)."""

    timestamp: datetime  # cierre de la barra senal
    direction: str  # "long" | "short"
    zone_id: str
    zone_type: str
    pool_anchor: str  # anchor_type del pool usado como target
    pool_price: float
    entry_ref: float  # close de la barra senal (proxy del fill a open siguiente)
    stop: float
    target: float
    risk_points: float
    target_r_multiple: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction,
            "zone_id": self.zone_id,
            "zone_type": self.zone_type,
            "pool_anchor": self.pool_anchor,
            "pool_price": self.pool_price,
            "entry_ref": self.entry_ref,
            "stop": self.stop,
            "target": self.target,
            "risk_points": self.risk_points,
            "target_r_multiple": round(self.target_r_multiple, 4),
        }


class ZonasReboteStrategy:
    """Rebote de zona activa con confirmacion de cierre (Candidata 2).

    Estado incremental: ZoneEngine(symbol=MNQ, timeframe=M5, session_pools=True,
    defaults del motor) alimentado barra a barra + ATR(14) SMA de true ranges
    (misma formula que EmasStrategy._append en src/backtest/emas.py: ATR_PERIOD
    importado de ahi; al motor NO se le pasa atr_series, ver CAND2_DISENO.md).

    Regla por barra cerrada (orden determinista del motor: available_at,
    zone_id; primera zona que califica):
    - demanda (direction == "long"): toque (low <= z.upper) + cierre por encima
      del midpoint (close > midpoint) -> LONG.
    - oferta (direction == "short"): toque (high >= z.lower) + cierre por debajo
      del midpoint (close < midpoint) -> SHORT.
    - stop absoluto: borde lejano ∓ 0.5*ATR (long: lower - k*ATR).
    - target absoluto: midpoint del pool de sesion (anclajes PDH/PDL/ONH/ONL)
      mas cercano en direccion del trade con 1R <= d <= 5R; sin pool valido no
      hay senal. Sin re-entrada por zona (used_zone_ids, marcado al emitir).
    - Signal a mercado con stop_target_as_points=False; el executor rellena a
      open de la siguiente barra y garantiza max 1 posicion abierta.
    """

    def __init__(self, *, market: Any = MNQ) -> None:
        self.market = market
        self.decisions: list[Cand2Decision] = []
        self._engine = ZoneEngine(
            symbol=market.symbol,
            timeframe="M5",
            tick_size=market.tick_size,
            session_pools=True,
        )
        self._seen = 0
        self._last_ts: datetime | None = None
        self._true_ranges: list[float] = []
        self._used_zone_ids: set[str] = set()

    def _sync(self, history: Any) -> bool:
        if self._seen and (
            len(history) < self._seen
            or history[self._seen - 1].timestamp != self._last_ts
        ):
            raise ValueError("history must be an append-only chronological sequence")
        changed = self._seen < len(history)
        if changed:
            for idx in range(self._seen, len(history)):
                bar = history[idx]
                prev_close = history[idx - 1].close if idx > 0 else bar.close
                self._true_ranges.append(max(
                    bar.high - bar.low,
                    abs(bar.high - prev_close),
                    abs(bar.low - prev_close),
                ))
            self._engine.update(history)
            self._seen = len(history)
            self._last_ts = history[-1].timestamp
        return changed

    def observe(self, history: Any) -> None:
        self._sync(history)

    def _atr(self) -> float | None:
        if len(self._true_ranges) < ATR_PERIOD:
            return None
        atr = sum(self._true_ranges[-ATR_PERIOD:]) / ATR_PERIOD
        if not math.isfinite(atr) or atr <= 0:
            return None
        return atr

    def _nearest_pool(
        self, entry_ref: float, risk: float, want_long: bool, ts: datetime
    ) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        best_d = math.inf
        for z in self._engine.active_zones():
            if z.zone_type != "session_level":
                continue
            if z.metadata.get("anchor_type") not in POOL_ANCHORS:
                continue
            if z.available_at > ts:
                continue
            mid = z.midpoint
            if want_long and not (mid > entry_ref):
                continue
            if not want_long and not (mid < entry_ref):
                continue
            d = abs(mid - entry_ref)
            r = d / risk
            if TARGET_R_MIN <= r <= TARGET_R_MAX and d < best_d:
                best = (str(z.metadata.get("anchor_type")), mid)
                best_d = d
        return best

    def evaluate(self, history: Any) -> Signal | None:
        if not self._sync(history) or not history:
            return None
        if len(history) < MIN_HISTORY_BARS:
            return None
        bar = history[-1]
        atr = self._atr()
        if atr is None:
            return None
        ts = bar.timestamp
        for z in self._engine.active_zones():
            if z.available_at > ts:
                continue
            if z.zone_id in self._used_zone_ids:
                continue
            if z.direction == "long":
                if not (bar.low <= z.upper and bar.close > z.midpoint):
                    continue
                stop = z.lower - ATR_K * atr
                want_long = True
            elif z.direction == "short":
                if not (bar.high >= z.lower and bar.close < z.midpoint):
                    continue
                stop = z.upper + ATR_K * atr
                want_long = False
            else:  # direction "neutral" (p. ej. FVG sin sesgo): sin mapeo a LONG/SHORT
                continue
            entry_ref = bar.close
            risk = abs(entry_ref - stop)
            if not math.isfinite(risk) or risk <= 0:
                continue
            pool = self._nearest_pool(entry_ref, risk, want_long, ts)
            if pool is None:
                return None  # sin pool valido no se opera (sin fallback a 2a zona)
            anchor, mid = pool
            self._used_zone_ids.add(z.zone_id)
            self.decisions.append(Cand2Decision(
                timestamp=ts,
                direction="long" if want_long else "short",
                zone_id=z.zone_id,
                zone_type=z.zone_type,
                pool_anchor=anchor,
                pool_price=mid,
                entry_ref=entry_ref,
                stop=stop,
                target=mid,
                risk_points=risk,
                target_r_multiple=abs(mid - entry_ref) / risk,
            ))
            return Signal(
                direction="long" if want_long else "short",
                entry=entry_ref,
                stop=stop,
                target=mid,
                stop_target_as_points=False,
            )
        return None


class DayGatedStrategy:
    """Wrapper que suprime senales durante el warmup sin alterar indicadores.

    Delega todo en una ZonasReboteStrategy interna. Si la ultima barra cerrada
    es anterior a day_start (warmup), avanza el estado interno con observe()
    (motor de zonas + ATR identicos a corrida continua) y devuelve None.
    A partir de day_start delega en evaluate().
    """

    def __init__(self, inner: ZonasReboteStrategy, day_start: datetime) -> None:
        self._inner = inner
        self._day_start = day_start
        self.market = inner.market

    @property
    def decisions(self):  # type: ignore[no-untyped-def]
        return self._inner.decisions

    def evaluate(self, history):  # type: ignore[no-untyped-def]
        if not history:
            return None
        if history[-1].timestamp < self._day_start:
            self._inner.observe(history)
            return None
        return self._inner.evaluate(history)


def build_config(slip_extra_ticks: int):
    """Config identica al brazo h1_targetpuro_f0 + slip extra por lado."""
    extra_pts = slip_extra_ticks * TICK_POINTS
    overrides = dict(PINS)
    overrides["slippage_points"] = PINS["slippage_points"] + extra_pts
    overrides["time_exit_slippage_points"] = PINS["time_exit_slippage_points"] + extra_pts
    overrides["end_of_data_slippage_points"] = PINS["end_of_data_slippage_points"] + extra_pts
    return emas_config(
        market=MNQ,
        f=PARTIAL_F,
        discrete_partial_contracts=False,
        **overrides,
    )


# ── serializacion copiada tal cual de run_h1.py:123-146 (via mira 1) ─────────
def serialize_trade(t: Any, trade_id: str, fold_id: int, sess: str) -> dict[str, Any]:
    row = {
        "trade_id": trade_id,
        "fold_id": fold_id,
        "session_date": sess,
        "direction": t.direction,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
        "quantity": t.quantity,
        "gross_pnl": round(t.gross_pnl, 4),
        "commission": round(t.commission, 4),
        "slippage_cost": round(t.slippage_cost, 4),
        "net_pnl": round(t.net_pnl, 4),
        "r_result": round(t.r_result, 6),
        "effective_r": round(t.effective_r, 6),
        "exit_reason": t.exit_reason,
    }
    return row


def compute_trades_sha256(trades: list[dict[str, Any]]) -> str:
    serialized = json.dumps(trades, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_drawdown_r(r_results: list[float]) -> float:
    if not r_results:
        return 0.0
    equity = np.cumsum(r_results)
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    return float(np.max(dd)) if len(dd) > 0 else 0.0


def pf_of(pnl: list[float]) -> float:
    wins = [p for p in pnl if p > 0]
    losses = [-p for p in pnl if p < 0]
    return (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (999.0 if wins else 0.0)


def ci_or_fail(values: list[float], label: str):
    """CBB obligatorio (misma regla G5 de H1): None con n>=5 => abort."""
    if CircularBlockBootstrap is None:
        raise SystemExit("FAIL: arch.bootstrap no disponible — CBB obligatorio.")
    lo, hi = run_bootstrap_ci(values, seed=42)
    if lo is None:
        if len(values) < 5:
            return None
        raise SystemExit(f"FAIL: CBB devolvió None para {label!r} con n={len(values)}")
    return (lo, hi)


def flat_cutoff_instant(sess: Any) -> datetime:
    """Instante 15:10 CT de la fecha de sesion S (DST via zoneinfo)."""
    return datetime(sess.year, sess.month, sess.day, FLAT_HOUR_CT, FLAT_MINUTE_CT, tzinfo=CT)


def run_day_slice(
    bars: list[Bar],
    i0: int,
    i1: int,
    cutoff: datetime,
    cfg: Any,
) -> tuple[Any, int, int, list[Cand2Decision]]:
    """Corre un dia: warmup 500 barras previas + barras [i0, i1) con ts <= cutoff."""
    warm = bars[max(0, i0 - WARMUP_BARS) : i0]
    day = [b for b in bars[i0:i1] if b.timestamp <= cutoff]
    assert day, "dia sin barras hasta el corte (el llamador debe filtrarlo)"
    day_start = day[0].timestamp
    strat = DayGatedStrategy(
        ZonasReboteStrategy(market=MNQ),
        day_start=day_start,
    )
    res = run_backtest(warm + day, strat, cfg)
    return res, len(warm), len(day), list(strat.decisions)


def build_signal_attribution(
    decisions: list[dict[str, Any]],
    dev_fold_ids: list[int],
) -> dict[str, Any]:
    """Desglose de senales aceptadas por tipo de zona y pool (solo folds dev).

    Fuente: decisiones aceptadas por la estrategia (no trades ejecutados; ver
    CAND2_DISENO.md A12 sobre la posible divergencia por rechazos del executor).
    """
    indev = [d for d in decisions if d["fold_id"] in dev_fold_ids]
    border = [d for d in decisions if d["fold_id"] is None]

    def counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        c: dict[str, int] = {}
        for d in rows:
            c[d[key]] = c.get(d[key], 0) + 1
        return dict(sorted(c.items()))

    by_fold: dict[str, Any] = {}
    for fid in sorted({d["fold_id"] for d in indev}):
        frows = [d for d in indev if d["fold_id"] == fid]
        by_fold[str(fid)] = {
            "n_signals": len(frows),
            "by_zone_type": counts(frows, "zone_type"),
            "by_direction": counts(frows, "direction"),
        }
    return {
        "n_accepted_signals": len(indev),
        "n_signals_border_fold": len(border),
        "by_zone_type": counts(indev, "zone_type"),
        "by_pool_anchor": counts(indev, "pool_anchor"),
        "by_direction": counts(indev, "direction"),
        "by_fold": by_fold,
    }


def run_ledger(
    bars: list[Bar],
    plan: WalkForwardPlan,
    cfg: Any,
    dev_intervals: list[tuple[int, int]],
    sealed_intervals: list[tuple[int, int]],
    max_days: int | None = None,
) -> dict[str, Any]:
    """Corrida por-dia sobre dias que tocan ventanas dev sin tocar selladas.

    Asigna fold por indice de la barra de entrada (misma particion que H1).
    max_days recorta a los primeros N dias corridos (solo smoke tecnico).
    Devuelve filas del ledger + contadores de higiene + decisiones de senal.
    """
    ts_index = {b.timestamp: i for i, b in enumerate(bars)}

    def in_any(idx: int, intervals: list[tuple[int, int]]) -> int | None:
        for f, (a, z) in enumerate(intervals):
            if a <= idx < z:
                return f
        return None

    # Agrupar indices por fecha de sesion (convencion del motor).
    order: list[Any] = []
    members: dict[Any, list[int]] = {}
    for i, b in enumerate(bars):
        s = session_date(b.timestamp)
        if s not in members:
            members[s] = []
            order.append(s)
        members[s].append(i)

    rows: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    n_days_run = 0
    n_days_skipped_sealed = 0
    n_days_out_of_scope = 0
    n_days_empty_to_cutoff = 0
    n_trades_dropped_out_of_window = 0
    n_eod_flat = 0
    gap_rejections = 0
    seq = 0

    for sess in order:
        if max_days is not None and n_days_run >= max_days:
            break
        idxs = members[sess]
        touches_dev = any(in_any(i, dev_intervals) is not None for i in idxs)
        touches_sealed = any(in_any(i, sealed_intervals) is not None for i in idxs)
        if touches_sealed:
            n_days_skipped_sealed += 1
            continue
        if not touches_dev:
            n_days_out_of_scope += 1
            continue
        cutoff = flat_cutoff_instant(sess)
        day_in = [i for i in idxs if bars[i].timestamp <= cutoff]
        if not day_in:
            n_days_empty_to_cutoff += 1
            continue
        i0, i1 = idxs[0], idxs[-1] + 1
        res, _, _, slice_decisions = run_day_slice(bars, i0, i1, cutoff, cfg)
        gap_rejections += res.gap_rejections
        assert res.unresolved_positions == 0, (
            f"posicion sin resolver con end_of_data_policy=close (sesion {sess})"
        )
        n_days_run += 1
        for d in slice_decisions:
            sig_idx = ts_index.get(d.timestamp)
            fold_rel = in_any(sig_idx, dev_intervals) if sig_idx is not None else None
            fold_id = plan.folds[fold_rel].fold_id if fold_rel is not None else None
            if fold_id is not None:
                assert fold_id < DEV_FOLDS, f"G4: fold {fold_id} sellado tocado"
            row = d.to_dict()
            row["fold_id"] = fold_id
            decisions.append(row)
        for t in res.trades:
            entry_idx = ts_index.get(t.entry_time)
            fold_rel = in_any(entry_idx, dev_intervals) if entry_idx is not None else None
            if fold_rel is None:
                # Entrada en borde fuera de ventanas dev (p.ej. warmup/train):
                # se dropea y se cuenta (documentado en CAND1_DISENO.md).
                n_trades_dropped_out_of_window += 1
                continue
            fold_id = plan.folds[fold_rel].fold_id
            assert fold_id < DEV_FOLDS, f"G4: fold {fold_id} sellado tocado"
            seq += 1
            reason = t.exit_reason
            raw_reason = reason
            if reason == "end_of_data":
                reason = "eod_flat"  # cierre forzado 15:10 CT
                n_eod_flat += 1
            row = serialize_trade(t, f"cand2-f{fold_id}-{seq:05d}", fold_id, sess.isoformat())
            row["exit_reason"] = reason
            row["executor_exit_reason"] = raw_reason
            rows.append(row)

    rows.sort(key=lambda r: (r["entry_time"], r["exit_time"], r["direction"], r["trade_id"]))
    decisions.sort(key=lambda d: (d["timestamp"], d["direction"], d["zone_id"]))
    attribution = build_signal_attribution(
        decisions, [f.fold_id for f in plan.folds[:DEV_FOLDS]])
    return {
        "rows": rows,
        "decisions": decisions,
        "signal_attribution": attribution,
        "n_days_run": n_days_run,
        "n_days_skipped_sealed": n_days_skipped_sealed,
        "n_days_out_of_scope": n_days_out_of_scope,
        "n_days_empty_to_cutoff": n_days_empty_to_cutoff,
        "n_trades_dropped_out_of_window": n_trades_dropped_out_of_window,
        "n_eod_flat": n_eod_flat,
        "gap_rejections": gap_rejections,
    }


LEDGER_CSV_COLS = [
    "trade_id", "fold_id", "session_date", "direction",
    "entry_time", "exit_time", "entry_price", "exit_price",
    "stop_price", "target_price", "quantity", "gross_pnl", "commission",
    "slippage_cost", "net_pnl", "r_result", "effective_r",
    "exit_reason", "executor_exit_reason",
]


def build_metrics(
    slip_extra_ticks: int,
    led: dict[str, Any],
    plan: WalkForwardPlan,
    bars: list[Bar],
    fingerprint: str,
    prereg_hash: str,
    cfg: Any,
    seeds: dict[str, int],
    smoke: bool,
) -> dict[str, Any]:
    rows = led["rows"]
    all_r = [r["r_result"] for r in rows]
    all_stop_r = [r["effective_r"] for r in rows]
    qty_hist: dict[int, int] = {}
    for r in rows:
        qty_hist[r["quantity"]] = qty_hist.get(r["quantity"], 0) + 1

    fold_records = []
    for fold in plan.folds[:DEV_FOLDS]:
        frows = [r for r in rows if r["fold_id"] == fold.fold_id]
        fr = [r["r_result"] for r in frows]
        fs = [r["effective_r"] for r in frows]
        fold_records.append({
            "fold_id": fold.fold_id,
            "test_start": fold.test_start,
            "test_end": fold.test_end,
            "test_bars": fold.test_end_idx - fold.test_start_idx,
            "n_trades": len(frows),
            "mean_er": round(float(np.mean(fr)), 4) if fr else 0.0,
            "mean_stop_r": round(float(np.mean(fs)), 4) if fs else 0.0,
            "profit_factor": round(min(pf_of([r["net_pnl"] for r in frows]), 999.0), 4),
            "net_pnl": round(sum(r["net_pnl"] for r in frows), 2),
            "win_rate": round(
                sum(1 for r in frows if r["net_pnl"] > 0) / len(frows), 4) if frows else 0.0,
            "trades": frows,
        })

    ci = ci_or_fail(all_r, f"cand2/slip+{slip_extra_ticks}/mean_er")
    positive_folds = sum(1 for f in fold_records if f["mean_er"] > 0)
    return {
        "metadata": {
            "experiment": "CAND2_ZONA_REBOTE",
            "arm_id": "cand2_zona_rebote_flat1510",
            "scenario": "realista",
            "slip_extra_ticks_per_side": slip_extra_ticks,
            "slip_extra_points_per_side": slip_extra_ticks * TICK_POINTS,
            "end_of_data_policy": "close",
            "zones": {
                "engine": "ZoneEngine(symbol=MNQ, timeframe=M5, session_pools=True, "
                          "defaults: pivot 3/3, min_gap_pct 0.001, liq 2 toques, "
                          "tol 4 ticks/0.10 ATR, lookback 4000, sin max_age, d20 20)",
                "entry_universe": "TODAS las zonas activas no-terminales con "
                                  "available_at <= ts y direction long/short",
                "entry_long": "low <= upper y close > midpoint",
                "entry_short": "high >= lower y close < midpoint",
                "stop": "borde lejano -/+ 0.5*ATR(14) SMA (stop_target_as_points=False)",
                "target": "midpoint del pool PDH/PDL/ONH/ONL mas cercano en "
                          "direccion con 1R <= d <= 5R; sin pool no hay senal",
                "atr": f"SMA({ATR_PERIOD}) de true ranges (formula de "
                       "EmasStrategy._append, src/backtest/emas.py)",
                "no_reentry": "used_zone_ids por slice diario (marcado al emitir)",
            },
            "flat_ct": f"{FLAT_HOUR_CT:02d}:{FLAT_MINUTE_CT:02d}",
            "warmup_bars": WARMUP_BARS,
            "pinned_config": dataclasses.asdict(cfg),
            "dev_folds": DEV_FOLDS,
            "preregistro_frozen": str(PREREGISTRO_PATH),
            "preregistro_sha256": prereg_hash,
            "dataset_sha256": fingerprint,
            "seeds": seeds,
            "smoke_mode": smoke,
            "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "dataset": {
            "archive": str(ZIP_PATH),
            "member": MNQ_MEMBER,
            "bars_count": len(bars),
            "first": bars[0].timestamp.isoformat() if bars else "N/A",
            "last": bars[-1].timestamp.isoformat() if bars else "N/A",
            "sha256": fingerprint,
        },
        "aggregate": {
            "n_trades": len(rows),
            "gate_n100_pass": len(rows) >= 100,
            "mean_er": round(float(np.mean(all_r)), 4) if all_r else 0.0,
            "mean_stop_r": round(float(np.mean(all_stop_r)), 4) if all_r else 0.0,
            "profit_factor": round(min(pf_of([r["net_pnl"] for r in rows]), 999.0), 4),
            "win_rate": round(
                (sum(1 for r in rows if r["net_pnl"] > 0) / len(rows)) if rows else 0.0, 4),
            "net_r": round(float(np.sum(all_r)), 4) if all_r else 0.0,
            "net_pnl": round(float(np.sum([r["net_pnl"] for r in rows])), 2) if rows else 0.0,
            "max_drawdown_r": round(compute_drawdown_r(all_r), 4),
            "positive_folds": positive_folds,
            "total_folds": len(fold_records),
            "cbb_bootstrap_ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
            "trades_sha256": compute_trades_sha256(rows),
            "n_eod_flat": led["n_eod_flat"],
            "n_days_run": led["n_days_run"],
            "n_days_skipped_sealed": led["n_days_skipped_sealed"],
            "n_days_out_of_scope": led["n_days_out_of_scope"],
            "n_days_empty_to_cutoff": led["n_days_empty_to_cutoff"],
            "n_trades_dropped_out_of_window": led["n_trades_dropped_out_of_window"],
            "gap_rejections": led["gap_rejections"],
            "qty_distribution": {str(k): v for k, v in sorted(qty_hist.items())},
            "attribution": {
                "total_slippage_cost": round(sum(r["slippage_cost"] for r in rows), 2),
                "total_commission": round(sum(r["commission"] for r in rows), 2),
            },
        },
        "signal_attribution": led["signal_attribution"],
        "folds": fold_records,
    }


def write_ledger_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_CSV_COLS)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r[c] for c in LEDGER_CSV_COLS})


# ── yardstick (plantilla: t1_orb_topstep_50k.py, bloque `correr`) ─────────────
def derive_seeds() -> dict[str, int]:
    """Semillas hijas reproducibles desde SeedSequence(20260928).

    seed_mbb ocupa el slot 0 (identico a T1: spawn(4)[0]); el slot 1 se reserva
    (slot iid de T1, sin uso aqui); slots 2 y 3 para los dos controles.
    """
    children = np.random.SeedSequence(MASTER_SEED).spawn(4)
    return {
        "master": MASTER_SEED,
        "seed_mbb": int(children[0].generate_state(1)[0]),
        "seed_reserved_t1_iid_slot": int(children[1].generate_state(1)[0]),
        "seed_perm": int(children[2].generate_state(1)[0]),
        "seed_centered": int(children[3].generate_state(1)[0]),
    }


def reg_equals(reg: dict[str, float]) -> bool:
    return (reg["start"] == 50_000.0 and reg["target"] == 3_000.0
            and reg["mll"] == 2_000.0 and reg["lock"] == 50_000.0)


def check_topstep_profile() -> dict[str, float]:
    p = topstep_50k_profile()
    reg = dict(start=float(p.starting_balance),
               target=float(p.profit_target.target.value),
               mll=float(p.maximum_loss.distance.value),
               lock=float(p.maximum_loss.threshold_ceiling))
    assert reg_equals(reg), f"constantes del perfil no cuadran con T5: {reg}"
    return reg


def correr(
    nombre: str, r_in: np.ndarray, d_in: np.ndarray, block: int, b_reps: int,
    seed_mbb: int, reg: dict[str, float], consist: float, min_dias: int,
    dll: float, cap_usd: float, out: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bloque `correr` copiado de t1_orb_topstep_50k.py (solo parametrizado)."""
    import bootstrap_camino as bc  # noqa: E402  (lazy: el ledger solo no lo necesita)

    orig = bc.simulate_funded_trajectory

    def patched(resample, timeline, _cons=consist, _md=min_dias, _dll=dll, **kw):
        kw["initial_balance"] = reg["start"]
        kw["profit_target_usd"] = reg["target"]
        kw["max_dd_limit_usd"] = reg["mll"]
        kw["dd_floor_ceiling"] = reg["lock"]            # lock en starting balance (T5)
        kw["daily_loss_limit_usd"] = _dll
        kw["consistency_max_day_ratio"] = _cons          # 0.55 oficial / 0.50 conservador
        kw["min_trading_days"] = _md                     # 2 oficial
        kw["trailing_update_cadence"] = "end_of_day"     # F1 Fix A: ratchet EOD (spec Topstep)
        kw["consistency_day_consolidation"] = "day_close"  # F1 Fix A: mejor dia al cierre
        kw["risk_pct"] = 2.0 * cap_usd / reg["start"]
        kw["max_risk_dollars_order"] = cap_usd
        out_r = orig(resample, timeline, **kw)
        n_cons = min(out_r["consumed_opportunities"], len(timeline))
        out_r["_meses"] = ((timeline[n_cons - 1] - timeline[0]).total_seconds() / 86400 / 30.44
                           if n_cons > 0 else 0.0)
        return out_r

    bc.simulate_funded_trajectory = patched
    try:
        _, _, acts, _ = bc.run_unwrapped_mbb_paired(
            r_in, block, b_reps, seed_mbb, dates=d_in, profit_target_usd=reg["target"])
    finally:
        bc.simulate_funded_trajectory = orig

    cnt = Counter(a["terminal_condition"] for a in acts)
    pp = cnt.get("target", 0) / b_reps
    quemar = (cnt.get("breach_trailing", 0) + cnt.get("breach_daily", 0)) / b_reps
    m_p = np.array([a["_meses"] for a in acts if a["terminal_condition"] == "target"])
    m_f = np.array([a["_meses"] for a in acts if a["terminal_condition"] != "target"])
    fila = dict(nombre=nombre, pp=pp, quemar=quemar, fracaso=1.0 - pp, cnt=dict(cnt),
                med_p=float(np.median(m_p)) if m_p.size else float("nan"),
                med_f=float(np.median(m_f)) if m_f.size else float("nan"),
                med_all=float(np.median([a["_meses"] for a in acts])))
    out.append(f"  {nombre:42s} | P(pasar)={pp*100:5.1f}% | P(quemar)={quemar*100:5.1f}% | "
               f"mediana si pasa {fila['med_p']:.2f}m | si muere {fila['med_f']:.2f}m | "
               f"estados {dict(cnt)}")
    return fila, acts


def paired_delta(
    acts_cand: list[dict[str, Any]], acts_ctrl: list[dict[str, Any]]
) -> dict[str, Any]:
    """Delta P(pase) control − candidato sobre replicas pareadas (mismo seed/bloque/n).

    delta_b = pass_ctrl_b − pass_cand_b en {−1, 0, 1}; IC95 normal sobre las B
    diferencias pareadas.
    """
    assert len(acts_cand) == len(acts_ctrl) and len(acts_cand) > 0
    pc = np.array([1.0 if a["terminal_condition"] == "target" else 0.0 for a in acts_cand])
    px = np.array([1.0 if a["terminal_condition"] == "target" else 0.0 for a in acts_ctrl])
    d = px - pc
    mean = float(d.mean())
    sd = float(d.std(ddof=1)) if len(d) > 1 else 0.0
    half = 1.96 * sd / (len(d) ** 0.5) if len(d) > 1 else 0.0
    return {
        "delta_mean": round(mean, 4),
        "ci95": [round(mean - half, 4), round(mean + half, 4)],
        "ci95_excludes_zero": (mean - half) > 0 or (mean + half) < 0,
        "delta_gt_zero_excl_zero": mean > 0 and (mean - half) > 0,
        "n_replicas": len(d),
        "n_both_pass": int(((pc == 1) & (px == 1)).sum()),
        "n_cand_only": int(((pc == 1) & (px == 0)).sum()),
        "n_ctrl_only": int(((pc == 0) & (px == 1)).sum()),
        "n_neither": int(((pc == 0) & (px == 0)).sum()),
    }


def run_yardstick(
    rows: list[dict[str, Any]],
    seeds: dict[str, int],
    b_reps: int,
    out: list[str],
    attribution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Escenarios A/B/C + controles de permutacion + deltas pareados."""
    import math

    reg = check_topstep_profile()
    cap_usd = PTO * K_CONTRACTS

    recs = sorted(
        ((r["exit_time"], r["r_result"], r["session_date"]) for r in rows),
        key=lambda z: (z[0], z[2]),
    )
    r_led = np.array([z[1] for z in recs], dtype=np.float64)
    # Fechas = session_date del ledger (convencion del motor), no fecha UTC del exit:
    # la consolidacion day_close debe agrupar por dia de trading.
    d_led = np.array([z[2] for z in recs])
    if attribution is not None:
        out.append(f"  senales aceptadas (folds dev)={attribution['n_accepted_signals']} "
                   f"por tipo={attribution['by_zone_type']} "
                   f"pools={attribution['by_pool_anchor']} "
                   f"borde_sin_fold={attribution['n_signals_border_fold']}")
    if len(r_led) < 5:
        out.append(f"  MUESTRA INSUFICIENTE para yardstick: n={len(r_led)} (min 5).")
        return {"insufficient_trades": int(len(r_led)), "B": b_reps,
                "scenarios": {}, "deltas": {}}

    block_opt = optimal_block_length(r_led)
    block = max(1, min(int(math.ceil(float(block_opt.loc[0, "circular"]))), len(r_led)))
    seed_mbb = seeds["seed_mbb"]
    out.append(f"  MBB bloque={block} (optimal circular, ceil) | seed_mbb={seed_mbb} | "
               f"seed_perm={seeds['seed_perm']} | seed_centered={seeds['seed_centered']} | B={b_reps}")
    out.append(f"  Perfil topstep_50k_profile: start=${reg['start']:,.0f} target=${reg['target']:,.0f} "
               f"MLL=${reg['mll']:,.0f} lock=${reg['lock']:,.0f} | 1 contrato (1R == ${PTO:,.0f})")

    # Controles con COLA ACOTADA (preregistro): misma distribucion, sin estructura.
    r_perm = np.random.default_rng(seeds["seed_perm"]).permutation(r_led)
    r_centered = np.random.default_rng(seeds["seed_centered"]).permutation(r_led - r_led.mean())
    out.append(f"  control permutacion: media={r_perm.mean():+.4f} (igual que candidata) | "
               f"control centrado: media={r_centered.mean():+.4f}")

    scenarios = {"A": (0.55, 2, NO_DLL), "B": (0.50, 2, NO_DLL), "C": (0.50, 2, 1_000.0)}
    names = {"A": "A · Base oficial (cons 0.55, min2)",
             "B": "B · Conservador (cons 0.50, min2)",
             "C": "C · Cons+DLL $1,000 terminal (cota)"}
    filas: dict[str, Any] = {}
    acts: dict[str, list[dict[str, Any]]] = {}
    out.append("══ ESCENARIOS PREDECLARADOS (1 contrato, flat 15:10 CT) ══")
    for tag, (cons, md, dll) in scenarios.items():
        filas[tag], acts[tag] = correr(names[tag], r_led, d_led, block, b_reps,
                                       seed_mbb, reg, cons, md, dll, cap_usd, out)

    out.append("══ CONTROLES (mismo motor, mismas reglas; fechas fijas) ══")
    for tag in ("A", "B"):
        cons, md, dll = scenarios[tag]
        filas[f"ctrl_perm_{tag}"], acts[f"ctrl_perm_{tag}"] = correr(
            f"CTRL permutacion · cons {cons:.2f}", r_perm, d_led, block, b_reps,
            seed_mbb, reg, cons, md, dll, cap_usd, out)
        filas[f"ctrl_cent_{tag}"], acts[f"ctrl_cent_{tag}"] = correr(
            f"CTRL centrado · cons {cons:.2f}", r_centered, d_led, block, b_reps,
            seed_mbb, reg, cons, md, dll, cap_usd, out)

    out.append("══ DELTAS PAREADOS (control − candidato; B replicas comunes) ══")
    deltas: dict[str, Any] = {}
    for tag in ("A", "B"):
        for ctrl in ("perm", "cent"):
            key = f"{tag}_{ctrl}"
            deltas[key] = paired_delta(acts[tag], acts[f"ctrl_{ctrl}_{tag}"])
            dd = deltas[key]
            out.append(f"  Escenario {tag} vs CTRL {ctrl}: "
                       f"Δ={dd['delta_mean']*100:+.1f} pp IC95=[{dd['ci95'][0]*100:+.1f},"
                       f"{dd['ci95'][1]*100:+.1f}] pp (n={dd['n_replicas']})")
    return {
        "reg": reg, "cap_usd": cap_usd, "contracts": K_CONTRACTS,
        "mbb_block": block, "B": b_reps,
        "scenarios": filas, "deltas": deltas,
    }


def evaluate_conditions(summaries: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Lectura operativa del criterio de EXITO del preregistro (la etiqueta la pone Hermes).

    CONDICION A: P(pase)_cand − P(pase)_ctrl_perm > 0 con IC95 pareado sin cruzar
    cero, en base, escenario A. (Delta almacenado como control − candidato: A se
    cumple <=> delta_mean < 0 con IC95 sin cruzar cero.)
    CONDICION B: lo mismo con slippage +1 tick por lado.
    Gate n>=100: si n<100 el veredicto es ABSTENCION por muestra.
    """
    out: dict[str, Any] = {"gate_n100": {}, "condicion_A": None, "condicion_B": None}
    for extra in sorted(summaries):
        n = summaries[extra]["metrics"]["aggregate"]["n_trades"]
        out["gate_n100"][str(extra)] = {"n_trades": n, "pass": n >= 100}
    if out["gate_n100"]["0"]["pass"]:
        d = summaries[0]["yardstick"]["deltas"]["A_perm"]
        out["condicion_A"] = {
            "delta_control_menos_candidato": d["delta_mean"],
            "ci95": d["ci95"],
            "cumple": bool(d["delta_mean"] < 0 and d["ci95"][1] < 0),
        }
    if out["gate_n100"]["1"]["pass"]:
        d = summaries[1]["yardstick"]["deltas"]["A_perm"]
        out["condicion_B"] = {
            "delta_control_menos_candidato": d["delta_mean"],
            "ci95": d["ci95"],
            "cumple": bool(d["delta_mean"] < 0 and d["ci95"][1] < 0),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="MIRA 2 / CAND2: zona_rebote flat 15:10 CT")
    parser.add_argument("--smoke", action="store_true",
                        help="prueba tecnica: 10 dias, slip base, B=200 (NO es resultado)")
    parser.add_argument("--slip-extra", type=int, choices=[0, 1, 2], default=None,
                        help="corre un solo nivel de slippage extra (default: 0,1,2)")
    parser.add_argument("--no-yardstick", action="store_true",
                        help="omite el yardstick (solo ledger + metricas)")
    args = parser.parse_args()
    smoke = args.smoke

    log_lines: list[str] = []

    def emit(msg: str = "") -> None:
        print(msg, flush=True)
        log_lines.append(msg)

    if not PREREGISTRO_PATH.exists():
        raise SystemExit(f"PREREGISTRO CONGELADO NO ENCONTRADO: {PREREGISTRO_PATH}.")
    prereg_hash = compute_file_sha256(PREREGISTRO_PATH)
    if prereg_hash != PREREGISTRO_SHA256:
        raise SystemExit(
            f"PREREGISTRO MODIFICADO (sha {prereg_hash[:16]}... != congelado "
            f"{PREREGISTRO_SHA256[:16]}...). Sin preregistro congelado no hay corrida.")
    emit(f"Preregistro congelado verificado (SHA256: {prereg_hash})")
    if CircularBlockBootstrap is None:
        raise SystemExit("FAIL: arch.bootstrap no disponible — CBB obligatorio.")

    slip_levels = [args.slip_extra] if args.slip_extra is not None else list(SLIP_LEVELS_TICKS)
    if smoke:
        slip_levels = [0]
    b_reps = 200 if smoke else B_REPLICATES
    suffix = "_SMOKE" if smoke else ""
    if smoke:
        emit("MODO SMOKE: 10 dias, slip base, B=200 — los numeros NO son resultado.")

    emit("Cargando MNQ M5 desde databento.zip...")
    bars, fingerprint, first_ts, last_ts = load_market_bars_from_zip(ZIP_PATH, MNQ_MEMBER)
    emit(f"  {len(bars)} barras | {first_ts} -> {last_ts} | sha {fingerprint[:16]}...")
    for prev, curr in zip(bars, bars[1:]):
        if curr.timestamp < prev.timestamp:
            raise SystemExit("FAIL: barras no cronologicas.")

    plan = WalkForwardPlan.create_calendar_rolling(
        bars, train_months=36, test_months=6, step_months=6,
        purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT,
    )
    emit(f"Plan C1: {plan.n_folds} folds creados")
    if plan.n_folds != 8:
        raise SystemExit(f"El plan C1 debe crear exactamente 8 folds (creo {plan.n_folds}).")
    dev_intervals = [(f.test_start_idx, f.test_end_idx) for f in plan.folds[:DEV_FOLDS]]
    sealed_intervals = [(f.test_start_idx, f.test_end_idx) for f in plan.folds[DEV_FOLDS:]]
    for f in plan.folds[:DEV_FOLDS]:
        assert f.fold_id < DEV_FOLDS, f"G4: fold {f.fold_id} fuera del tramo"

    seeds = derive_seeds()
    summaries: dict[int, dict[str, Any]] = {}
    for extra in slip_levels:
        tag = _slip_suffix(extra)
        emit(f"\n=== CAND2 | slip +{extra} ticks/lado ===")
        cfg = build_config(extra)
        led = run_ledger(bars, plan, cfg, dev_intervals, sealed_intervals,
                         max_days=10 if smoke else None)
        emit(f"  dias corridos={led['n_days_run']} trades={len(led['rows'])} "
             f"senales={led['signal_attribution']['n_accepted_signals']} "
             f"por_tipo={led['signal_attribution']['by_zone_type']} "
             f"eod_flat={led['n_eod_flat']} sellados_saltados={led['n_days_skipped_sealed']} "
             f"fuera_de_ventana={led['n_days_out_of_scope']} "
             f"dropeados_borde={led['n_trades_dropped_out_of_window']}")
        metrics = build_metrics(extra, led, plan, bars, fingerprint,
                                prereg_hash, cfg, seeds, smoke)
        write_ledger_csv(led["rows"], HERE / f"cand2_ledger_{tag}{suffix}.csv")
        (HERE / f"cand2_metrics_{tag}{suffix}.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        emit(f"  Escrito cand2_ledger_{tag}{suffix}.csv + cand2_metrics_{tag}{suffix}.json")

        yard: dict[str, Any] | None = None
        if not args.no_yardstick:
            out: list[str] = []
            out.append("=" * 78)
            out.append(f"CAND2 — zona_rebote flat 15:10 CT · yardstick Topstep 50K · {tag}{suffix}")
            out.append("=" * 78)
            yard = run_yardstick(led["rows"], seeds, b_reps, out,
                                 led["signal_attribution"])
            (HERE / f"cand2_yardstick_{tag}{suffix}.json").write_text(
                json.dumps(yard, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            (HERE / f"cand2_yardstick_{tag}{suffix}.txt").write_text(
                "\n".join(out) + "\n", encoding="utf-8")
            for line in out:
                emit(line)
        summaries[extra] = {"metrics": metrics, "yardstick": yard}

    conditions = None
    if not args.no_yardstick and not smoke and set(summaries) >= {0, 1} and \
            all(summaries[e]["yardstick"] is not None for e in (0, 1)):
        conditions = evaluate_conditions(summaries)
        emit("\n══ LECTURA OPERATIVA (etiqueta final: Hermes) ══")
        emit(json.dumps(conditions, indent=2, ensure_ascii=False))

    emit("\n=== CAND2 COMPLETADO ===")
    (HERE / f"CAND2_RUN_LOG{suffix}.txt").write_text(
        "\n".join(log_lines) + "\n", encoding="utf-8")

    manifest = {
        "metadata": {
            "title": "Manifest CAND2 zona_rebote intradia",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "runtime": sys.version,
            "preregistro_sha256": prereg_hash,
            "smoke_mode": smoke,
        },
        "dataset": {"archive": str(ZIP_PATH), "member": MNQ_MEMBER, "bars_count": len(bars),
                    "first": first_ts, "last": last_ts, "sha256": fingerprint},
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36, "test_months": 6,
                              "step_months": 6, "purge_gap_bars": 0,
                              "warmup_bars": CALIBRATION_BARS_COUNT,
                              "n_folds": plan.n_folds, "dev_folds_used": DEV_FOLDS,
                              "validation_folds_sealed": [6, 7]},
        "arm": {"strategy": "ZonasReboteStrategy(ZoneEngine MNQ/M5 session_pools=True, "
                            "ATR(14) SMA, k=0.5, pools PDH/PDL/ONH/ONL 1R-5R)",
                "executor": "legacy absolute (sin BE)",
                "flat_ct": "15:10", "warmup_bars": WARMUP_BARS,
                "slip_levels_ticks": slip_levels},
        "pins_base": PINS,
        "seeds": seeds,
        "yardstick": {"engine": "bootstrap_camino", "B": b_reps,
                      "trailing_update_cadence": "end_of_day",
                      "consistency_day_consolidation": "day_close",
                      "profile": "topstep_50k", "contracts": K_CONTRACTS,
                      "risk_usd_per_R": PTO},
        "conditions": conditions,
        "artifacts_sha256": {p.name: compute_file_sha256(p) for p in sorted(
            q for q in list(HERE.glob("cand2_*")) + list(HERE.glob("CAND2_RUN_LOG*.txt"))
            if (suffix and suffix in q.name) or (not suffix and "_SMOKE" not in q.name))},
    }
    (HERE / f"cand2_manifest{suffix}.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Log + manifest escritos: CAND2_RUN_LOG{suffix}.txt, "
          f"cand2_manifest{suffix}.json", flush=True)


if __name__ == "__main__":
    main()
