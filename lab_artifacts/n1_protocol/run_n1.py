#!/usr/bin/env python3
"""Runner N1 — sweep-reversal NY 9:00-9:30 (A) y OR-breakout x regime (C).

Ejecuta EXACTAMENTE el preregistro congelado
``lab_artifacts/n1_protocol/preregistro.json`` (sha256 fail-closed al arrancar).
Patron de arquitectura: ``lab_artifacts/m9_protocol/run_m9.py`` (senales +
``run_backtest`` del motor canonico con plan C1) y ``lab_artifacts/h1_protocol/
run_h1.py`` (gates + CBB + manifest). Las reglas salen del preregistro, no de
esos runners.

Semantica post-fix: fills next-open + slip + tick; regla A1 (la vela de fill
SI se chequea contra SL); gap-stop al open; gap-entry -> rechazo con
PRIORIDAD (el gap-stop solo aplica a posiciones ya abiertas). Costes:
realista 0.62 USD/side + 0.25 pt; sensibilidad 2.0 pts RT (canonico M9:
2.0 USD/side + 1 tick). Headline: budget-R + stop-R (r_result y effective_r
del motor). Salida primaria: primera barra >= 12:00 ET; sensibilidad: 60
barras. Sin targets R como primaria (target lejano inalcanzable).

PROHIBIDO tocar src/: este archivo solo LEE el motor canonico.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.run_c1_walkforward import (  # noqa: E402
    CALIBRATION_BARS_COUNT,
    ZIP_PATH,
    compute_file_sha256,
    run_bootstrap_ci,
)
from src.backtest.executor import BacktestConfig, run_backtest  # noqa: E402
from src.backtest.history import Bar  # noqa: E402
from src.backtest.markets import MES, MGC, MNQ, MYM  # noqa: E402
from src.backtest.strategy import Signal  # noqa: E402
from src.hypothesis_registry import WalkForwardPlan  # noqa: E402

N1_DIR = Path(__file__).resolve().parent
PREREG_PATH = N1_DIR / "preregistro.json"
PREREG_SHA256 = "f9a1a419fe96d46f072db89c97190f6cff0620cf12985c88d911b3dc98babd16"

ET = ZoneInfo("America/New_York")
BAR_SECONDS = 300

MARKET_SPECS = {"MNQ": MNQ, "MES": MES, "MYM": MYM, "MGC": MGC}
MARKET_FILES = {
    "MNQ": "databento/MNQ_M5.csv",
    "MES": "databento/MES_M5.csv",
    "MYM": "databento/MYM_M5.csv",
    "MGC": "databento/MGC_M5.csv",
}
EXPLORATORY = ("MES", "MYM", "MGC")

# TARGET_FAR_PTS: 1_000_000.0 pts. El preregistro define salida temporal (>=12:00 ET) o stop,
# sin target R fijo. Se usa un target astronomicameente lejano para no interferir.
# En el motor canonico con ordenes a mercado al next-open (pending_limit_entry=False),
# la regla A1 y el nivel inalcanzable garantizan que nunca se evalua ni ejecuta TP en la vela
# de fill, previniendo falsos positivos de TP.
TARGET_FAR_PTS = 1_000_000.0  # sin target R fijo: salida temporal o stop
FILTER_START = "2019-05-06"  # mismo corte canonico que M9/M8


# ---------------------------------------------------------------------------
# Preregistro congelado (fail-closed)
# ---------------------------------------------------------------------------
def verify_preregistro(path: Path = PREREG_PATH, expected_sha: str = PREREG_SHA256) -> dict[str, Any]:
    """Verifica sha256 del preregistro y lo devuelve parseado.

    Falla cerrado (SystemExit codigo 2) si falta o no coincide. Nada se
    ejecuta sin el preregistro exacto.
    """
    if not path.exists():
        raise SystemExit(
            f"N1 STATUS: INVALID gate=SHA detail=preregistro-ausente({path})"
        )
    digest = compute_file_sha256(path)
    if digest.lower() != expected_sha.lower():
        raise SystemExit(
            "N1 STATUS: INVALID gate=SHA detail=preregistro-mismatch"
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def extract_pins(prereg: dict[str, Any]) -> dict[str, Any]:
    """Lee los pins del preregistro VERIFICADO. Clave ausente => INVALID."""
    try:
        pins = prereg["pins_summary"]
        seeds = prereg["seeds"]
        cal = prereg["calendar_and_microstructure"]
        return {
            "k_d": float(pins["k_d"]),
            "k_stop": float(pins["k_stop"]),
            "atr_period": 14,
            "vol_window": 20,
            "vol_pct_window": 120,
            "exit_sens_bars": int(pins["exit_sensitivity_bars"]),
            "event_K": int(pins["event_study_K"]),
            "cbb_seed": int(seeds["cbb_seed"]),
            "control_seed": int(seeds["control_seed"]),
            "min_bars_range": int(cal["min_bars_range"]),
            "n_min_trades": 150,
            "n_min_events": 100,
        }
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise SystemExit(
            f"N1 STATUS: INVALID gate=PINS detail=pin-ausente-o-ilegible({exc})"
        )


# ---------------------------------------------------------------------------
# Datos: sha ANTES de la primera lectura, luego parseo
# ---------------------------------------------------------------------------
def read_member_bytes(zip_path: Path, member: str) -> tuple[bytes, str]:
    with zipfile.ZipFile(zip_path) as zf:
        try:
            content = zf.read(member)
        except KeyError:
            raise FileNotFoundError(f"member-ausente:{member}")
    return content, hashlib.sha256(content).hexdigest()


def parse_bars(content: bytes) -> list[Bar]:
    bars: list[Bar] = []
    seen_ts: set[datetime] = set()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for row in reader:
        ts_str = row["timestamp"]
        if ts_str >= FILTER_START:
            ts = ts_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt in seen_ts:
                raise ValueError(f"timestamp-duplicado-en-parser:{dt.isoformat()}")
            seen_ts.add(dt)
            bars.append(
                Bar(
                    timestamp=dt,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                )
            )
    bars.sort(key=lambda b: b.timestamp)
    return bars


# ---------------------------------------------------------------------------
# Utilidades ET / calendario
# ---------------------------------------------------------------------------
def et_of(ts: datetime) -> datetime:
    return ts.astimezone(ET)


def et_minutes(ts: datetime) -> int:
    e = et_of(ts)
    return e.hour * 60 + e.minute


def group_by_et_date(bars: list[Bar]) -> dict[date, list[int]]:
    groups: dict[date, list[int]] = {}
    for i, b in enumerate(bars):
        d = et_of(b.timestamp).date()
        groups.setdefault(d, []).append(i)
    return groups


def rth_count(day_idx: list[int], bars: list[Bar]) -> int:
    n = 0
    for i in day_idx:
        m = et_minutes(bars[i].timestamp)
        if 9 * 60 + 30 <= m < 16 * 60:
            n += 1
    return n


# ---------------------------------------------------------------------------
# ATR(14) Wilder causal sobre M5 cerradas
# ---------------------------------------------------------------------------
def true_range(h: float, l: float, pc: float) -> float:
    return max(h - l, abs(h - pc), abs(l - pc))


def atr_wilder(bars: list[Bar], period: int = 14) -> list[float]:
    """ATR Wilder alineado por indice; NaN donde i < period (no hay dato)."""
    out = [math.nan] * len(bars)
    if len(bars) < period + 1:
        return out
    trs = [0.0] * len(bars)
    for i in range(1, len(bars)):
        trs[i] = true_range(bars[i].high, bars[i].low, bars[i - 1].close)
    seed = sum(trs[1 : period + 1]) / period
    out[period] = seed
    for i in range(period + 1, len(bars)):
        out[i] = (out[i - 1] * (period - 1) + trs[i]) / period
    return out


# ---------------------------------------------------------------------------
# Rangos / sweep / rechazo (funciones puras, testeables)
# ---------------------------------------------------------------------------
def window_range(
    day_idx: list[int], bars: list[Bar], lo_min: int, hi_min: int, min_bars: int
) -> tuple[float, float, int] | None:
    """high/low de barras con ET en [lo_min, hi_min). None si < min_bars."""
    hi = -math.inf
    lo = math.inf
    n = 0
    for i in day_idx:
        m = et_minutes(bars[i].timestamp)
        if lo_min <= m < hi_min:
            hi = max(hi, bars[i].high)
            lo = min(lo, bars[i].low)
            n += 1
    if n < min_bars:
        return None
    return (hi, lo, n)


def detect_sweep(close: float, rh: float, rl: float, atr: float, k_d: float) -> str | None:
    """'up' si el cierre excede el maximo en >= k_d*ATR; 'down' simetrico."""
    if not math.isfinite(atr) or atr <= 0:
        return None
    if close >= rh + k_d * atr:
        return "up"
    if close <= rl - k_d * atr:
        return "down"
    return None


def is_rejection(close: float, rh: float, rl: float) -> bool:
    return rl <= close <= rh


def find_first_at_or_after(day_idx: list[int], bars: list[Bar], h: int, m: int) -> int | None:
    target = h * 60 + m
    for i in day_idx:
        if et_minutes(bars[i].timestamp) >= target:
            return i
    return None


# ---------------------------------------------------------------------------
# Volatilidad realizada 20 sesiones + percentil 120d (estrictamente causal)
# ---------------------------------------------------------------------------
def intraday_log_returns(day_idx: list[int], bars: list[Bar]) -> list[float]:
    rets: list[float] = []
    for k in range(1, len(day_idx)):
        i, j = day_idx[k - 1], day_idx[k]
        if j != i + 1:
            continue  # hueco de datos: no mezclar sesiones ni saltos
        c0, c1 = bars[i].close, bars[j].close
        if c0 > 0 and c1 > 0:
            rets.append(math.log(c1 / c0))
    return rets


def realized_vol_prior(
    sessions: list[date],
    rets_by_day: dict[date, list[float]],
    day: date,
    window: int,
) -> float:
    """std (ddof=1) de retornos M5 pooleados de las `window` sesiones previas
    con datos, estrictamente < day. NaN si < 2 retornos."""
    pos = sessions.index(day)
    pool: list[float] = []
    used = 0
    k = pos - 1
    while k >= 0 and used < window:
        pool.extend(rets_by_day.get(sessions[k], []))
        used += 1
        k -= 1
    if len(pool) < 2:
        return math.nan
    return float(np.std(np.array(pool, dtype=float), ddof=1))


def vol_asof_all(
    sessions: list[date],
    rets_by_day: dict[date, list[float]],
    window: int,
) -> dict[date, float]:
    """Vol realizada trailing por sesion (una sola pasada; cada valor usa
    solo sesiones estrictamente anteriores)."""
    idx = {d: p for p, d in enumerate(sessions)}
    return {d: realized_vol_prior(sessions, rets_by_day, d, window) for d in sessions
            if idx[d] >= 1}


def vol_percentile(
    sessions: list[date],
    rets_by_day: dict[date, list[float]],
    day: date,
    window: int,
    ref_window: int,
    cache: dict[date, float] | None = None,
) -> float:
    """Percentil de la vol actual entre las vols trailing de las ultimas
    `ref_window` sesiones. Cada vol de referencia se computa con datos
    estrictamente anteriores a su propia sesion (sin look-ahead).
    NaN si faltan referencias."""
    if cache is None:
        cache = vol_asof_all(sessions, rets_by_day, window)
    cur = cache.get(day, math.nan)
    if not math.isfinite(cur):
        return math.nan
    pos = sessions.index(day)
    refs: list[float] = []
    k = pos - 1
    while k >= 0 and len(refs) < ref_window:
        v = cache.get(sessions[k], math.nan)
        if math.isfinite(v):
            refs.append(v)
        k -= 1
    if len(refs) < ref_window:
        return math.nan
    return float(sum(1 for v in refs if v <= cur) / len(refs))


# ---------------------------------------------------------------------------
# Senales
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalEvent:
    day: date
    kind: str  # 'A' | 'C'
    side: str  # 'long' | 'short'
    signal_idx: int
    fill_idx: int
    stop_level: float
    target_level: float
    ref_close: float
    atr: float
    extra: str = ""


def compute_signal_A(
    day: date,
    day_idx: list[int],
    bars: list[Bar],
    atr: list[float],
    k_d: float,
    k_stop: float,
    min_bars: int,
    diag: dict[str, int] | None = None,
) -> SignalEvent | None:
    """Sweep-reversal 9:00-9:30: primer sweep+rechazo valido del dia.

    Rango half-open [9:00,9:30) ET, min `min_bars` barras. Barrido: cierre que
    excede el extremo en >= k_d*ATR. Rechazo: la barra SIGUIENTE cierra de
    vuelta dentro del rango, con timestamp < 11:00 ET. Entrada al siguiente
    open, lado contrario al barrido. Maximo una entrada por dia (primer
    patron valido; tras un sweep sin rechazo se sigue buscando).
    """
    def bump(k: str) -> None:
        if diag is not None:
            diag[k] = diag.get(k, 0) + 1

    wr = window_range(day_idx, bars, 9 * 60, 9 * 60 + 30, min_bars)
    if wr is None:
        bump("A_rango_no_operable")
        return None
    rh, rl, _ = wr
    pos_in_day = {g: p for p, g in enumerate(day_idx)}
    k = 0
    while k < len(day_idx):
        i = day_idx[k]
        m = et_minutes(bars[i].timestamp)
        if m < 9 * 60 + 30:
            k += 1
            continue
        if m >= 11 * 60:
            break
        sw = detect_sweep(bars[i].close, rh, rl, atr[i], k_d)
        if sw is None:
            k += 1
            continue
        # barra siguiente dentro del mismo dia
        if pos_in_day[i] + 1 >= len(day_idx):
            bump("A_sin_barra_rechazo")
            return None
        j = day_idx[pos_in_day[i] + 1]
        if j != i + 1 or int((bars[j].timestamp - bars[i].timestamp).total_seconds()) != BAR_SECONDS:
            # hueco de datos: falta barra M5 inmediata entre sweep y rechazo
            bump("A_gap_entre_sweep_y_rechazo")
            k += 1
            continue
        close_j_min = et_minutes(bars[j].timestamp) + (BAR_SECONDS // 60)
        if close_j_min >= 11 * 60 or not is_rejection(bars[j].close, rh, rl):
            bump("A_sweep_sin_rechazo")
            k += 1
            continue
        if pos_in_day[j] + 1 >= len(day_idx):
            bump("A_sin_barra_fill")
            return None
        f = day_idx[pos_in_day[j] + 1]
        if f != j + 1 or int((bars[f].timestamp - bars[j].timestamp).total_seconds()) != BAR_SECONDS:
            bump("A_gap_entre_rechazo_y_fill")
            k = pos_in_day[j] + 1
            continue
        a = atr[j]
        if not math.isfinite(a) or a <= 0:
            bump("A_atr_no_disponible")
            return None
        if sw == "up":
            side = "short"
            stop = bars[i].high + k_stop * a
        else:
            side = "long"
            stop = bars[i].low - k_stop * a
        bump("A_senal")
        return SignalEvent(
            day=day, kind="A", side=side, signal_idx=j, fill_idx=f,
            stop_level=stop, target_level=bars[j].close + (TARGET_FAR_PTS if side == "long" else -TARGET_FAR_PTS),
            ref_close=bars[j].close, atr=a, extra=f"sweep-{sw}",
        )
    bump("A_sin_sweep")
    return None


def compute_signal_C(
    day: date,
    day_idx: list[int],
    bars: list[Bar],
    atr: list[float],
    k_stop: float,
    min_bars: int,
    percentile: float,
    diag: dict[str, int] | None = None,
) -> SignalEvent | None:
    """Breakout del rango [9:30,10:00): primer cierre fuera del rango con
    timestamp en [10:00,11:00) ET. Entrada al siguiente open, lado del
    breakout. Stop = k_stop*ATR anclado al cierre de la barra de breakout
    (el preregistro no fija ancla: gap declarado). Una entrada por dia."""
    def bump(k: str) -> None:
        if diag is not None:
            diag[k] = diag.get(k, 0) + 1

    wr = window_range(day_idx, bars, 9 * 60 + 30, 10 * 60, min_bars)
    if wr is None:
        bump("C_rango_no_operable")
        return None
    rh, rl, _ = wr
    pos_in_day = {g: p for p, g in enumerate(day_idx)}
    for k in range(len(day_idx)):
        i = day_idx[k]
        m = et_minutes(bars[i].timestamp)
        if m < 10 * 60:
            continue
        if m >= 11 * 60:
            break
        c = bars[i].close
        if c > rh:
            side = "long"
        elif c < rl:
            side = "short"
        else:
            continue
        if pos_in_day[i] + 1 >= len(day_idx):
            bump("C_sin_barra_fill")
            return None
        f = day_idx[pos_in_day[i] + 1]
        if f != i + 1:
            bump("C_gap_entre_breakout_y_fill")
            continue
        a = atr[i]
        if not math.isfinite(a) or a <= 0:
            bump("C_atr_no_disponible")
            return None
        dist = k_stop * a
        stop = c - dist if side == "long" else c + dist
        bump("C_senal")
        return SignalEvent(
            day=day, kind="C", side=side, signal_idx=i, fill_idx=f,
            stop_level=stop, target_level=c + (TARGET_FAR_PTS if side == "long" else -TARGET_FAR_PTS),
            ref_close=c, atr=a, extra=f"pct={percentile:.4f}" if math.isfinite(percentile) else "pct=nan",
        )
    bump("C_sin_breakout")
    return None


# ---------------------------------------------------------------------------
# Estrategia de un disparo para el motor canonico
# ---------------------------------------------------------------------------
class N1OneShot:
    """Strategy de un solo disparo: emite la senal precomputada cuando el
    historial termina en la barra de senal; el motor hace fill al siguiente
    open (con slip+tick), chequea SL en la vela de fill (A1), gap-stop al
    open y rechazo gap-entry si falta la barra inmediata."""

    def __init__(self, signals: dict[str, tuple[str, float, float, float]], market: Any):
        # signal_bar_iso -> (side, stop_level, target_level, ref_close)
        self._signals = signals
        self.market = market
        self.decisions: list[tuple[str, str, float, float]] = []

    def evaluate(self, history: Any) -> Signal | None:
        if not history:
            return None
        key = history[-1].timestamp.isoformat()
        hit = self._signals.get(key)
        if hit is None:
            return None
        side, stop, target, ref = hit
        self.decisions.append((key, side, stop, target))
        return Signal(direction=side, entry=ref, stop=stop, target=target,
                       stop_target_as_points=False)


def round_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(price / tick) * tick


def mirror_valid_levels(side: str, fill_open: float, slip: float, tick: float,
                        stop: float, target: float) -> tuple[str, float, float, float]:
    """Replica la resolucion del motor para clasificar la entrada ANTES de
    correr: 'ok' | 'gap_reject' (fill mas alla del stop: gap de entrada con
    prioridad) | 'collapsed' (niveles colapsados tras redondeo)."""
    entry = round_tick(fill_open + slip if side == "long" else fill_open - slip, tick)
    s = round_tick(stop, tick)
    t = round_tick(target, tick)
    if side == "long":
        valid = s < entry < t
        beyond = entry <= s
    else:
        valid = s > entry > t
        beyond = entry >= s
    if valid:
        return ("ok", entry, s, t)
    if beyond:
        return ("gap_reject", entry, s, t)
    return ("collapsed", entry, s, t)


# ---------------------------------------------------------------------------
# Corrida por dia con el motor canonico
# ---------------------------------------------------------------------------
# W1: coste sensibilidad = 2,0 pts RT exactos (1,0 pt/side comision, slip 0)
COST_SCENARIOS = {
    "realista": {"commission_per_side": 0.62, "slip_pts": 0.25},
    "sensibilidad": {"commission_per_side_pts": 1.0, "slip_pts": 0.0},
}


def scenario_slip(scenario: str, tick: float) -> float:
    cfg = COST_SCENARIOS[scenario]
    if "slip_pts" in cfg:
        return float(cfg["slip_pts"])
    return float(cfg["slip_ticks"]) * tick


def scenario_comm(scenario: str, dollar_per_point: float) -> float:
    cfg = COST_SCENARIOS[scenario]
    if "commission_per_side_pts" in cfg:
        return float(cfg["commission_per_side_pts"]) * dollar_per_point
    return float(cfg["commission_per_side"])


def run_signal_day(
    bars: list[Bar],
    ev: SignalEvent,
    market: Any,
    scenario: str,
    exit_mode: str,
    exit_info: dict[str, Any],
) -> dict[str, Any]:
    """Corre UN dia-senal en el motor canonico.

    exit_mode 'primary': max_hold_minutes hasta la primera barra >= 12:00 ET
      (salida market a su open, antes de H/L/C de esa barra).
    exit_mode 'sens60': max_bars_held=60 (salida al close de la barra 60).
    Devuelve trades serializados + contadores + decisiones logueadas.
    """
    tick = market.tick_size
    slip = scenario_slip(scenario, tick)
    comm = scenario_comm(scenario, market.dollar_per_point)

    fill_bar = bars[ev.fill_idx]
    cls, _entry, _s, _t = mirror_valid_levels(
        ev.side, fill_bar.open, slip, tick, ev.stop_level, ev.target_level
    )
    if cls != "ok":
        return {"trades": [], "gap_rejections": 1 if cls == "gap_reject" else 0,
                "collapsed": 1 if cls == "collapsed" else 0,
                "unresolved": 0, "decisions": [], "precheck": cls}

    sig_key = bars[ev.signal_idx].timestamp.isoformat()
    strat = N1OneShot({sig_key: (ev.side, ev.stop_level, ev.target_level, ev.ref_close)},
                      market)
    if exit_mode == "primary":
        exit_idx = exit_info["exit_idx"]
        hold_min = (bars[exit_idx].timestamp - fill_bar.timestamp).total_seconds() / 60.0
        cfg = BacktestConfig(
            dollar_per_point=market.dollar_per_point, tick_size=tick,
            commission_per_side=comm, slippage_points=slip,
            time_exit_slippage_points=slip, end_of_data_slippage_points=0.0,
            bar_interval_seconds=BAR_SECONDS, max_bars_held=10**9,
            max_hold_minutes=hold_min, time_exit_mode="market",
            end_of_data_policy="unresolved",
        )
        end_idx = exit_idx
    else:
        cfg = BacktestConfig(
            dollar_per_point=market.dollar_per_point, tick_size=tick,
            commission_per_side=comm, slippage_points=slip,
            time_exit_slippage_points=slip, end_of_data_slippage_points=0.0,
            bar_interval_seconds=BAR_SECONDS, max_bars_held=exit_info["sens_bars"],
            max_hold_minutes=None, time_exit_mode="market",
            end_of_data_policy="unresolved",
        )
        end_idx = exit_info["sens_end_idx"]

    sl = bars[ev.fill_idx : end_idx + 1]
    cal = bars[: ev.fill_idx]
    res = run_backtest(sl, strat, cfg, calibration_bars=cal)
    rows = []
    for tr in res.trades:
        rows.append({
            "trade_id": tr.trade_id, "day": ev.day.isoformat(), "kind": ev.kind,
            "direction": tr.direction,
            "signal_time": bars[ev.signal_idx].timestamp.isoformat(),
            "entry_time": tr.entry_time.isoformat(),
            "exit_time": tr.exit_time.isoformat(),
            "entry_price": tr.entry_price, "exit_price": tr.exit_price,
            "stop_price": tr.stop_price, "target_price": tr.target_price,
            "quantity": tr.quantity, "gross_pnl": round(tr.gross_pnl, 4),
            "commission": round(tr.commission, 4),
            "net_pnl": round(tr.net_pnl, 4),
            "r_result": round(tr.r_result, 6),
            "budget_r": round(tr.r_result, 6),
            "stop_r": round(tr.effective_r, 6),
            "exit_reason": tr.exit_reason,
        })
    return {"trades": rows, "gap_rejections": res.gap_rejections,
            "collapsed": 0, "unresolved": res.unresolved_positions,
            "decisions": list(strat.decisions), "precheck": "ok"}


def trades_sha(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Estadistica (CBB causal + control pareado + regresion)
# ---------------------------------------------------------------------------
def cbb_ci(values: list[float], seed: int) -> tuple[float | None, float | None]:
    lo, hi = run_bootstrap_ci(values, seed=seed)
    if lo is None or hi is None:
        return None, None
    return float(lo), float(hi)


def paired_control(r_values: list[float], seed: int) -> list[float]:
    """Control aleatorio pareado: mismos N, misma |R|, permutacion de
    signos originales con semilla fija (preserva el multiset de signos)."""
    if not r_values:
        return []
    magnitudes = [abs(x) for x in r_values]
    original_signs = [1.0 if x >= 0 else -1.0 for x in r_values]
    rng = np.random.RandomState(seed)
    permuted_signs = rng.permutation(original_signs)
    return [float(s * m) for s, m in zip(permuted_signs, magnitudes)]


def ols_beta(x: list[float], y: list[float]) -> dict[str, Any]:
    """OLS y = a + b*x. p_one_sided para H: b > 0 (t de Student; n>=3)."""
    n = len(x)
    xa = np.array(x, dtype=float)
    ya = np.array(y, dtype=float)
    xm, ym = float(xa.mean()), float(ya.mean())
    sxx = float(((xa - xm) ** 2).sum())
    if n < 3 or sxx <= 0:
        return {"n": n, "beta": None, "se": None, "t": None, "p_one_sided": None,
                "alpha": None}
    beta = float(((xa - xm) * (ya - ym)).sum() / sxx)
    alpha = float(ym - beta * xm)
    resid = ya - (alpha + beta * xa)
    mse = float((resid ** 2).sum() / (n - 2))
    se = float(math.sqrt(mse / sxx)) if mse > 0 else 0.0
    if se <= 0:
        return {"n": n, "beta": beta, "se": 0.0, "t": None,
                "p_one_sided": 0.0 if beta > 0 else 1.0, "alpha": alpha}
    t = beta / se
    try:
        from scipy.stats import t as t_dist
        p = float(t_dist.sf(t, n - 2))
    except ImportError:
        p = float(0.5 * math.erfc(t / math.sqrt(2)))
    return {"n": n, "beta": beta, "se": se, "t": float(t),
            "p_one_sided": p, "alpha": alpha}


def summarize_r(rows: list[dict[str, Any]], cbb_seed: int, min_trades: int = 150) -> dict[str, Any]:
    r = [row["budget_r"] for row in rows]
    s = [row["stop_r"] for row in rows]
    pnl = [row["net_pnl"] for row in rows]
    n = len(rows)
    lo, hi = cbb_ci(r, cbb_seed) if n >= min_trades else (None, None)
    wins = sum(1 for v in pnl if v > 0)
    gross_w = sum(v for v in pnl if v > 0)
    gross_l = sum(-v for v in pnl if v < 0)
    pf = (gross_w / gross_l) if gross_l > 0 else (999.0 if gross_w > 0 else 0.0)
    if r:
        eq = np.concatenate(([0.0], np.cumsum(np.array(r, dtype=float))))
        peak = np.maximum.accumulate(eq)
        dd = float(np.max(peak - eq))
    else:
        dd = 0.0
    return {
        "n_trades": n,
        "mean_budget_r": round(float(np.mean(r)), 4) if r else 0.0,
        "mean_stop_r": round(float(np.mean(s)), 4) if s else 0.0,
        "net_budget_r": round(float(np.sum(r)), 4) if r else 0.0,
        "net_pnl": round(float(np.sum(pnl)), 2) if pnl else 0.0,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "profit_factor": round(min(pf, 999.0), 4),
        "max_drawdown_r": round(dd, 4),
        "cbb_ci95_mean_budget_r": [round(lo, 4), round(hi, 4)] if lo is not None else None,
        "trades_sha256": trades_sha(rows),
    }


# ---------------------------------------------------------------------------
# Procesamiento por mercado
# ---------------------------------------------------------------------------
def emit_invalid(gate: str, detail: str) -> NoReturn:
    raise SystemExit(f"N1 STATUS: INVALID gate={gate} detail={detail}")


def check_gates_ok(gates: dict[str, Any], smoke: bool) -> None:
    """G1-G5 fail-closed. FAIL en smoke sigue siendo FAIL (sin excepcion)."""
    g = gates
    if not g["G1_signal_parity"]["PASS"]:
        emit_invalid("G1", "divergencia-senal-o-conjunto-vacio")
    if not g["G2_mapping"]["PASS"]:
        emit_invalid("G2", "desconciliacion-senales-outputs")
    if not g["G3_determinism"]["PASS"]:
        emit_invalid("G3", "determinismo-violado-en-recorrida")
    if not g["G4_sealed"]["PASS"]:
        emit_invalid("G4", "tramo-reutilizado-o-reapertura-o-orden-violado")
    g5 = g["G5_cbb"]["status"]
    if g5 == "FAIL":
        emit_invalid("G5", g["G5_cbb"].get("detail", "cbb-fallo-o-no-finito"))


def evaluate_g1(arms: dict[str, Any]) -> dict[str, Any]:
    """G1: paridad de senal (sens subconjunto de primary) y no vacio reconciliado (C4).
    - sens DEBE ser subconjunto de primary (b <= a).
    - g1_div mide |b - a| (elementos en sens no contenidos en primary).
    - g1_empty: vacio con signals_dev > 0 solo es FAIL si los drops
      no quedan reconciliados por G2 (gap + collapsed + unresolved == signals).
    """
    g1_div = 0
    g1_common = 0
    g1_empty = False
    for arm in ("A_on", "A_off", "C"):
        dev = arms.get(arm, {}).get("dev") if isinstance(arms.get(arm), dict) else None
        if dev is None:
            continue
        a = {(t, s, round(p, 6), round(q, 6)) for (t, s, p, q) in dev.get("decisions_primary", [])}
        b = {(t, s, round(p, 6), round(q, 6)) for (t, s, p, q) in dev.get("decisions_sens", [])}
        signals = arms[arm].get("signals_dev", 0)
        drops = dev.get("gap", 0) + dev.get("collapsed", 0) + dev.get("unresolved", 0)
        if signals > 0 and len(a) == 0 and drops != signals:
            g1_empty = True
        g1_common += len(a & b)
        g1_div += len(b - a)
    g1_pass = (g1_div == 0) and not g1_empty
    return {
        "common": g1_common,
        "divergent": g1_div,
        "empty_decisions": g1_empty,
        "PASS": g1_pass,
    }


def fold_of_day(day: date, folds: Any) -> int | None:
    for f in folds:
        s = date.fromisoformat(f.test_start)
        e = date.fromisoformat(f.test_end)
        if s <= day < e:
            return f.fold_id
    return None


def process_market(
    symbol: str,
    bars: list[Bar],
    pins: dict[str, Any],
    plan: WalkForwardPlan | None,
    roll_dates: set[date],
    smoke: bool,
) -> dict[str, Any]:
    """Pipeline completo de un mercado. Devuelve el contenido de metrics."""
    market = MARKET_SPECS[symbol]
    k_d = pins["k_d"]
    k_stop = pins["k_stop"]
    min_bars = pins["min_bars_range"]
    cbb_seed = pins["cbb_seed"]
    control_seed = pins["control_seed"]
    sens_bars = pins["exit_sens_bars"]
    event_K = pins["event_K"]

    groups = group_by_et_date(bars)
    sessions = sorted(groups.keys())
    atr = atr_wilder(bars, 14)
    rets_by_day = {d: intraday_log_returns(groups[d], bars) for d in sessions}
    vol_cache = vol_asof_all(sessions, rets_by_day, pins["vol_window"])

    diag: dict[str, int] = {}
    folds = plan.folds if plan is not None else []
    if plan is not None and plan.n_folds != 8 and not smoke:
        emit_invalid("G4", f"plan-C1-debe-tener-8-folds(hay-{plan.n_folds})")

    tail_start: date | None = None
    if not smoke:
        max_day = sessions[-1]
        y, m = max_day.year, max_day.month - 5
        while m <= 0:
            m += 12
            y -= 1
        tail_start = date(y, m, 1)

    # C3: separacion fija dev = folds 0..5; sellado = folds 6..7 + tramo final 6 meses
    in_scope: dict[date, str] = {}  # day -> 'dev' | 'sellado' | 'fuera'
    for d in sessions:
        if smoke:
            in_scope[d] = "dev"
        else:
            fid = fold_of_day(d, folds)
            if fid is not None and fid <= 5:
                in_scope[d] = "dev"
            elif fid is not None and fid in (6, 7):
                in_scope[d] = "sellado"
            elif tail_start is not None and d >= tail_start:
                in_scope[d] = "sellado"
            else:
                in_scope[d] = "fuera"

    # --- senales (causales por barra; el fold solo disciplina el reporte) ---
    sigA: dict[date, SignalEvent] = {}
    sigA_off: dict[date, SignalEvent] = {}
    sigC: dict[date, SignalEvent] = {}
    pctC: dict[date, float] = {}
    half_excl = 0
    roll_excl = 0
    for d in sessions:
        day_idx = groups[d]
        if not smoke and rth_count(day_idx, bars) < 78:
            half_excl += 1
            diag["dias_media_sesion_excluidos"] = half_excl
            continue
        if d in roll_dates:
            roll_excl += 1
            continue
        ev = compute_signal_A(d, day_idx, bars, atr, k_d, k_stop, min_bars, diag)
        if ev is not None:
            sigA[d] = ev
        ev_off = compute_signal_A(d, day_idx, bars, atr, 0.0, k_stop, min_bars, diag)
        if ev_off is not None:
            sigA_off[d] = ev_off
        pct = vol_percentile(sessions, rets_by_day, d, pins["vol_window"],
                             pins["vol_pct_window"], vol_cache)
        if smoke and not math.isfinite(pct):
            pct = 0.75  # smoke: sin 120d de historia; valor fijo declarado
            diag["C_vol_smoke_fallback"] = diag.get("C_vol_smoke_fallback", 0) + 1
        pctC[d] = pct
        if not math.isfinite(pct):
            diag["C_vol_warmup_skip"] = diag.get("C_vol_warmup_skip", 0) + 1
            continue
        evc = compute_signal_C(d, day_idx, bars, atr, k_stop, min_bars, pct, diag)
        if evc is not None:
            sigC[d] = evc
    diag["dias_roll_excluidos"] = roll_excl

    # --- event-study A stage 1 (C2: exactamente 20 barras f..f+19, C3: solo dev) ---
    events: list[float] = []
    for d, ev in sorted(sigA.items()):
        if in_scope[d] != "dev":
            continue
        f = ev.fill_idx
        t = f + event_K - 1
        if t >= len(bars):
            diag["A_evento_sin_K_barras"] = diag.get("A_evento_sin_K_barras", 0) + 1
            continue
        move = bars[t].close - bars[f].open
        events.append(move if ev.side == "long" else -move)
    # C7: no publicar IC si n < 100 eventos
    ev_lo, ev_hi = cbb_ci(events, cbb_seed) if len(events) >= pins["n_min_events"] else (None, None)
    event_study = {
        "K_barras": event_K, "n_events": len(events),
        "mean_signed_pts": round(float(np.mean(events)), 4) if events else 0.0,
        "cbb_ci95": [round(ev_lo, 4), round(ev_hi, 4)] if ev_lo is not None else None,
    }
    stage1_pass = (
        len(events) >= pins["n_min_events"]
        and ev_lo is not None and ev_lo > 0
    )
    if len(events) < pins["n_min_events"]:
        stage1_note = "ABSTENCION-stage1(n<100)"
    elif ev_lo is None:
        stage1_note = "ABSTENCION-stage1(CBB-indefinido)"
    elif ev_lo <= 0:
        stage1_note = "ABSTENCION-stage1(CI-no->0)"
    else:
        stage1_note = "PASS-a-stage2"
    event_study["gate"] = stage1_note

    # --- corridas de motor por dia-senal (C6: primaria fija, no contaminada por 60b) ---
    def run_bucket(days: list[date], sigmap: dict[date, SignalEvent],
                   tag: str) -> dict[str, Any]:
        agg = {"trades": [], "gap": 0, "collapsed": 0, "unresolved": 0,
               "decisions_primary": [], "decisions_sens": []}
        per_fold: dict[int, list[dict[str, Any]]] = {}
        for d in sorted(days):
            ev = sigmap[d]
            day_idx = groups[d]
            e12 = find_first_at_or_after(day_idx, bars, 12, 0)
            if e12 is None or e12 <= ev.fill_idx:
                agg["unresolved"] += 1
                diag[f"{tag}_sin_barra_1200"] = diag.get(f"{tag}_sin_barra_1200", 0) + 1
                continue
            last_exit_bar_date = et_of(bars[e12].timestamp).date()
            if last_exit_bar_date != d:
                agg["unresolved"] += 1
                diag[f"{tag}_salida_fuera_del_dia"] = diag.get(f"{tag}_salida_fuera_del_dia", 0) + 1
                continue
            info_primary = {"exit_idx": e12}

            # Primaria se corre primero y queda fijada
            for scenario in ("realista", "sensibilidad"):
                r = run_signal_day(bars, ev, market, scenario, "primary", info_primary)
                if scenario == "realista":
                    agg["trades"].extend(r["trades"])
                    agg["gap"] += r["gap_rejections"]
                    agg["collapsed"] += r["collapsed"]
                    agg["unresolved"] += r["unresolved"]
                    agg["decisions_primary"].extend(r["decisions"])
                    fid = fold_of_day(d, folds) if folds else None
                    if fid is not None:
                        per_fold.setdefault(fid, []).extend(r["trades"])
                else:
                    agg.setdefault("trades_sens", []).extend(r["trades"])
                    agg.setdefault("gap_sens", 0)
                    agg["gap_sens"] += r["gap_rejections"]
                    agg.setdefault("unresolved_sens", 0)
                    agg["unresolved_sens"] += r["unresolved"]
                    agg["decisions_sens"].extend(r["decisions"])

            # C6: sensibilidad 60 barras SOLO sobre las que disponen de 60 barras
            sens_end = ev.fill_idx + sens_bars
            if sens_end < len(bars):
                info_sens = {"sens_end_idx": sens_end, "sens_bars": sens_bars}
                for scenario in ("realista", "sensibilidad"):
                    r = run_signal_day(bars, ev, market, scenario, "sens60", info_sens)
                    if scenario == "realista":
                        agg.setdefault("trades_60", []).extend(r["trades"])
                    else:
                        agg.setdefault("trades_60_sens", []).extend(r["trades"])
            else:
                diag[f"{tag}_sin_60_barras"] = diag.get(f"{tag}_sin_60_barras", 0) + 1
        agg["per_fold"] = per_fold
        return agg

    arms: dict[str, Any] = {}
    dev_days = [d for d in sessions if in_scope[d] == "dev"]
    sealed_days = [d for d in sessions if in_scope[d] == "sellado"]
    run_A = stage1_pass or smoke

    sealed_audit: list[dict[str, Any]] = []
    reopen_detected = False
    order_violated = False

    if run_A:
        arms["A_on"] = {
            "dev": run_bucket([d for d in dev_days if d in sigA], sigA, "A"),
            "signals_dev": sum(1 for d in dev_days if d in sigA),
        }
        sealed_audit.append({"action": "open_dev", "arm": "A_on", "days": len(dev_days)})
        arms["A_off"] = {
            "dev": run_bucket([d for d in dev_days if d in sigA_off], sigA_off, "Aoff"),
            "signals_dev": sum(1 for d in dev_days if d in sigA_off),
        }
        sealed_audit.append({"action": "open_dev", "arm": "A_off", "days": len(dev_days)})
    else:
        # C3 & C5: si A falla stage1 en dev -> abstencion y NO se abre el sellado
        arms["A_on"] = {"dev": None, "signals_dev": sum(1 for d in dev_days if d in sigA),
                        "skipped": stage1_note}
        arms["A_off"] = {"dev": None, "signals_dev": 0, "skipped": stage1_note}
        sealed_audit.append({"action": "skip_dev_stage1_fail", "arm": "A_on"})
        sealed_audit.append({"action": "skip_dev_stage1_fail", "arm": "A_off"})

    arms["C"] = {
        "dev": run_bucket([d for d in dev_days if d in sigC], sigC, "C"),
        "signals_dev": sum(1 for d in dev_days if d in sigC),
    }
    sealed_audit.append({"action": "open_dev", "arm": "C", "days": len(dev_days)})
    dev_finished = True

    # C5: verificacion de sellado real (tramo sellado UNA vez al final)
    sealed_runs = 0
    if not smoke:
        for arm, sm in (("A_on", sigA), ("A_off", sigA_off), ("C", sigC)):
            if arms[arm].get("dev") is None:
                arms[arm]["tail"] = None
                sealed_audit.append({"action": "skip_sealed_abstained", "arm": arm})
                continue
            if not dev_finished:
                order_violated = True
            if any(a.get("action") == "open_sealed" and a.get("arm") == arm for a in sealed_audit):
                reopen_detected = True
            tb = run_bucket([d for d in sealed_days if d in sm], sm, arm + "sealed")
            sealed_runs += 1
            arms[arm]["tail"] = tb
            arms[arm]["signals_tail"] = sum(1 for d in sealed_days if d in sm)
            sealed_audit.append({"action": "open_sealed", "arm": arm, "days": len(sealed_days)})
    else:
        for arm in ("A_on", "A_off", "C"):
            arms[arm]["tail"] = None

    tail_block = {"tail_start": tail_start.isoformat() if tail_start else None,
                  "sealed_runs": sealed_runs, "audit": sealed_audit}

    # --- G1: paridad de senal y no vacio si hay senales (C4) ---
    g1_eval = evaluate_g1(arms)

    # --- G2: mapeo y reconciliacion exacta outputs == senales (C4) ---
    unmapped = 0
    reconciled = True
    for arm, sm in (("A_on", sigA), ("A_off", sigA_off), ("C", sigC)):
        dev = arms[arm].get("dev")
        if dev is None:
            continue
        sigfill = {bars[ev.fill_idx].timestamp.isoformat(): ev.side for d, ev in sm.items()
                   if d in dev_days and d in sm}
        for tr in dev["trades"]:
            if sigfill.get(tr["entry_time"]) != tr["direction"]:
                unmapped += 1
        n_motor = len(dev["trades"]) + dev["gap"] + dev["collapsed"] + dev["unresolved"]
        n_expected = len([d for d in dev_days if d in sm])
        if n_motor != n_expected:
            reconciled = False
    g2_pass = (unmapped == 0) and reconciled

    # --- G3: determinismo completo de todos los candidatos (C4) ---
    g3_pass = True
    g3_detail = "n/a"
    has_trades = False
    for arm, sm in (("A_on", sigA), ("A_off", sigA_off), ("C", sigC)):
        dev = arms[arm].get("dev")
        if dev is None or not dev["trades"]:
            continue
        has_trades = True
        orig_sha = trades_sha(dev["trades"])
        re_rows: list[dict[str, Any]] = []
        for d in sorted(dev_days):
            if d not in sm:
                continue
            ev = sm[d]
            day_idx = groups[d]
            e12 = find_first_at_or_after(day_idx, bars, 12, 0)
            if e12 is None or e12 <= ev.fill_idx:
                continue
            if et_of(bars[e12].timestamp).date() != d:
                continue
            r = run_signal_day(bars, ev, market, "realista", "primary", {"exit_idx": e12})
            re_rows.extend(r["trades"])
        if trades_sha(re_rows) != orig_sha:
            g3_pass = False
            g3_detail = f"mismatch-en-{arm}"
            break
    if g3_pass and has_trades:
        g3_detail = "corrida-completa-todos-los-candidatos"

    # --- G4: verificacion de sellado real (C5) ---
    active_arms = sum(1 for arm in ("A_on", "A_off", "C") if arms[arm].get("dev") is not None)
    expected_sealed_runs = 0 if smoke else active_arms
    g4_pass = (
        (sealed_runs == expected_sealed_runs)
        and not reopen_detected
        and not order_violated
    )

    # --- G5: CBB sobre pantalla + tail + control + ablacion + variante pct>50 (C4) ---
    cbb_status = "PASS"
    g5_fail_reason = ""
    series_to_check: list[tuple[str, list[float]]] = [("pantalla", events)]
    for arm in ("A_on", "A_off", "C"):
        dev = arms[arm].get("dev")
        if dev is not None:
            for k in ("trades", "trades_sens", "trades_60"):
                series_to_check.append((f"{arm}_dev_{k}", [t["budget_r"] for t in dev.get(k, [])]))
        tail = arms[arm].get("tail")
        if tail is not None:
            series_to_check.append((f"{arm}_tail_trades", [t["budget_r"] for t in tail.get("trades", [])]))

    if arms["A_on"].get("dev") is not None:
        r_a = [t["budget_r"] for t in arms["A_on"]["dev"]["trades"]]
        ctrl_a = paired_control(r_a, control_seed)
        series_to_check.append(("ctrl_A", ctrl_a))
        off = arms["A_off"].get("dev")
        if off:
            on_by_day = {}
            for t in arms["A_on"]["dev"]["trades"]:
                on_by_day.setdefault(t["day"], []).append(t["budget_r"])
            off_by_day = {}
            for t in off["trades"]:
                off_by_day.setdefault(t["day"], []).append(t["budget_r"])
            common = sorted(set(on_by_day) & set(off_by_day))
            deltas = [float(np.mean(on_by_day[d])) - float(np.mean(off_by_day[d])) for d in common]
            series_to_check.append(("ablacion_deltas", deltas))
    else:
        ctrl_a = []
        deltas = []

    if arms["C"].get("dev") is not None:
        r_c = [t["budget_r"] for t in arms["C"]["dev"]["trades"]]
        ctrl_c = paired_control(r_c, control_seed)
        series_to_check.append(("ctrl_C", ctrl_c))
        xs, ys = [], []
        for t in arms["C"]["dev"]["trades"]:
            d = date.fromisoformat(t["day"])
            if d in pctC and math.isfinite(pctC[d]):
                xs.append(pctC[d])
                ys.append(t["budget_r"])
        gt50 = [y for x, y in zip(xs, ys) if x > 0.50]
        series_to_check.append(("variante_pct_gt_50", gt50))
    else:
        ctrl_c = []
        gt50 = []

    for name, s_vals in series_to_check:
        if any(not math.isfinite(x) for x in s_vals):
            cbb_status = "FAIL"
            g5_fail_reason = f"no-finito-en-{name}"
            break
        if len(s_vals) >= 5:
            lo, hi = cbb_ci(s_vals, cbb_seed)
            if lo is None or hi is None or not math.isfinite(lo) or not math.isfinite(hi):
                cbb_status = "FAIL"
                g5_fail_reason = f"cbb-fallo-en-{name}"
                break

    gates = {
        "G1_signal_parity": g1_eval,
        "G2_mapping": {"unmapped_trades": unmapped, "reconciled": reconciled,
                       "PASS": g2_pass},
        "G3_determinism": {"PASS": bool(g3_pass), "detail": g3_detail},
        "G4_sealed": {"sealed_runs": sealed_runs,
                      "expected_sealed_runs": expected_sealed_runs,
                      "reopen_detected": reopen_detected,
                      "order_violated": order_violated,
                      "audit": sealed_audit,
                      "PASS": g4_pass},
        "G5_cbb": {"status": cbb_status, "detail": g5_fail_reason or "ok"},
    }
    if smoke:
        gates["G4_sealed"]["PASS"] = True
        gates["G4_sealed"]["note"] = "smoke-sin-folds"
    check_gates_ok(gates, smoke)

    # --- resumenes + control + regresion + ablacion (C7: supresion de IC si n < n_min) ---
    out: dict[str, Any] = {
        "market": symbol,
        "market_role": "confirmatorio" if symbol == "MNQ" else "exploratorio",
        "diag": diag,
        "gates": gates,
        "tail": tail_block,
    }
    n_min = pins["n_min_trades"]
    # A
    adev = arms["A_on"].get("dev")
    if adev is not None:
        summ = summarize_r(adev["trades"], cbb_seed, n_min)
        summ_sens = summarize_r(adev.get("trades_sens", []), cbb_seed, n_min)
        summ_60 = summarize_r(adev.get("trades_60", []), cbb_seed, n_min)
        clo, chi = cbb_ci(ctrl_a, cbb_seed) if len(ctrl_a) >= n_min else (None, None)
        off = arms["A_off"]["dev"]
        summ_off = summarize_r(off["trades"], cbb_seed, n_min) if off else {"n_trades": 0}
        dlo, dhi = cbb_ci(deltas, cbb_seed) if len(deltas) >= n_min else (None, None)
        n_a = summ["n_trades"]
        status_a = "OK" if (smoke or n_a >= n_min) else "ABSTENCION"
        out["A"] = {
            "status": "SMOKE" if smoke else status_a,
            "signals_dev": arms["A_on"]["signals_dev"],
            "realista": summ, "sensibilidad": summ_sens, "sens60": summ_60,
            "ablacion_off": summ_off,
            "ablacion_delta_on_minus_off": {
                "n_common_days": len(common),
                "mean": round(float(np.mean(deltas)), 4) if deltas else 0.0,
                "cbb_ci95": [round(dlo, 4), round(dhi, 4)] if dlo is not None else None,
            },
            "control_pareado": {
                "n": len(ctrl_a),
                "mean": round(float(np.mean(ctrl_a)), 4) if ctrl_a else 0.0,
                "cbb_ci95": [round(clo, 4), round(chi, 4)] if clo is not None else None,
                "seed": control_seed,
            },
            "event_study_stage1": event_study,
            "gap_rejections": adev["gap"], "collapsed": adev["collapsed"],
            "unresolved": adev["unresolved"],
            "folds": {str(k): summarize_r(v, cbb_seed, n_min) for k, v in adev["per_fold"].items()},
            "tail": summarize_r(arms["A_on"]["tail"]["trades"], cbb_seed, n_min)
            if arms["A_on"].get("tail") else None,
        }
    else:
        out["A"] = {"status": "ABSTENCION", "reason": arms["A_on"]["skipped"],
                    "signals_dev": arms["A_on"]["signals_dev"],
                    "event_study_stage1": event_study}
    # C
    cdev = arms["C"]["dev"]
    summ_c = summarize_r(cdev["trades"], cbb_seed, n_min)
    summ_c_sens = summarize_r(cdev.get("trades_sens", []), cbb_seed, n_min)
    summ_c_60 = summarize_r(cdev.get("trades_60", []), cbb_seed, n_min)
    xs, ys = [], []
    for t in cdev["trades"]:
        d = date.fromisoformat(t["day"])
        if d in pctC and math.isfinite(pctC[d]):
            xs.append(pctC[d])
            ys.append(t["budget_r"])
    reg = ols_beta(xs, ys)
    glo, ghi = cbb_ci(gt50, cbb_seed) if len(gt50) >= n_min else (None, None)
    cclo, cchi = cbb_ci(ctrl_c, cbb_seed) if len(ctrl_c) >= n_min else (None, None)
    n_c = summ_c["n_trades"]
    status_c = "OK" if (smoke or n_c >= n_min) else "ABSTENCION"
    out["C"] = {
        "status": "SMOKE" if smoke else status_c,
        "signals_dev": arms["C"]["signals_dev"],
        "realista": summ_c, "sensibilidad": summ_c_sens, "sens60": summ_c_60,
        "regresion_interaccion": reg,
        "variante_pct_gt_50": {
            "n_trades": len(gt50),
            "mean_budget_r": round(float(np.mean(gt50)), 4) if gt50 else 0.0,
            "cbb_ci95": [round(glo, 4), round(ghi, 4)] if glo is not None else None,
        },
        "control_pareado": {
            "n": len(ctrl_c),
            "mean": round(float(np.mean(ctrl_c)), 4) if ctrl_c else 0.0,
            "cbb_ci95": [round(cclo, 4), round(cchi, 4)] if cclo is not None else None,
            "seed": control_seed,
        },
        "gap_rejections": cdev["gap"], "collapsed": cdev["collapsed"],
        "unresolved": cdev["unresolved"],
        "folds": {str(k): summarize_r(v, cbb_seed, n_min) for k, v in cdev["per_fold"].items()},
        "tail": summarize_r(arms["C"]["tail"]["trades"], cbb_seed, n_min)
        if arms["C"].get("tail") else None,
    }
    return out


# ---------------------------------------------------------------------------
# Fixture sintetico determinista para smoke (NO son datos de mercado)
# ---------------------------------------------------------------------------
def build_smoke_bars() -> list[Bar]:
    """6 dias ET con patrones plantados: A-long, A-short, C-long, C-short,
    dia sin patron y dia de media sesion. Semilla fija, sin red."""
    rng = np.random.RandomState(7)
    bars: list[Bar] = []
    base_days = [date(2025, 3, 3 + i) for i in range(6)]  # lun-sab; sab=media sesion
    for di, d in enumerate(base_days):
        price = 100.0  # reset diario: continuidad intradia, sin saltos overnight
        # dia completo: 8:00-13:05 ET (62 slots); media sesion: solo 9:30-10:30
        slots = []
        h, m = 8, 0
        while (h, m) <= (13, 5):
            slots.append((h, m))
            m += 5
            if m >= 60:
                m = 0
                h += 1
        if di == 5:
            slots = [(hh, mm) for (hh, mm) in slots if (hh, mm) >= (9, 30) and (hh, mm) < (10, 30)]
        for (hh, mm) in slots:
            ts = datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).astimezone(timezone.utc)
            drift = float(rng.normal(0, 1.2))
            o = price
            c = o + drift
            hi = max(o, c) + abs(float(rng.normal(0, 0.5)))
            lo = min(o, c) - abs(float(rng.normal(0, 0.5)))
            bars.append(Bar(timestamp=ts, open=o, high=hi, low=lo, close=c,
                            volume=100.0))
            price = c
    # --- plantar patrones (indices relativos al dia) ---
    groups = group_by_et_date(bars)
    days = sorted(groups.keys())

    def set_bar(day_i: int, hh: int, mm: int, o: float, h: float, lo: float, c: float) -> int:
        for gi in groups[days[day_i]]:
            if et_minutes(bars[gi].timestamp) == hh * 60 + mm:
                b = bars[gi]
                bars[gi] = Bar(timestamp=b.timestamp, open=o, high=h, low=lo,
                               close=c, volume=b.volume)
                return gi
        raise AssertionError(f"slot {hh}:{mm} no hallado dia {day_i}")

    # Dia 0: rango 9:00-9:30 plano en 100; sweep DOWN a las 9:35 (close 96),
    # rechazo 9:40 (close 100 dentro); fill 9:45 -> LONG.
    for (hh, mm) in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        set_bar(0, hh, mm, 100.0, 100.6, 99.4, 100.0)
    set_bar(0, 9, 35, 100.0, 100.2, 95.5, 96.0)
    set_bar(0, 9, 40, 96.0, 100.5, 96.0, 100.0)
    set_bar(0, 9, 45, 100.0, 101.0, 99.5, 100.5)
    # Dia 1: sweep UP 9:35 (close 104), rechazo 9:40 (close 100) -> SHORT.
    for (hh, mm) in [(9, 0), (9, 5), (9, 10), (9, 15), (9, 20), (9, 25), (9, 30)]:
        set_bar(1, hh, mm, 100.0, 100.6, 99.4, 100.0)
    set_bar(1, 9, 35, 100.0, 104.5, 99.8, 104.0)
    set_bar(1, 9, 40, 104.0, 104.0, 99.5, 100.0)
    set_bar(1, 9, 45, 100.0, 100.5, 99.0, 99.5)
    # Dia 2: rango OR 9:30-10:00 plano en 100; breakout UP 10:05 (close 103) -> LONG C.
    for (hh, mm) in [(9, 30), (9, 35), (9, 40), (9, 45), (9, 50), (9, 55), (10, 0)]:
        set_bar(2, hh, mm, 100.0, 100.5, 99.5, 100.0)
    set_bar(2, 10, 5, 100.0, 103.5, 100.0, 103.0)
    set_bar(2, 10, 10, 103.0, 104.0, 102.5, 103.5)
    # Dia 3: breakout DOWN 10:05 (close 97) -> SHORT C.
    for (hh, mm) in [(9, 30), (9, 35), (9, 40), (9, 45), (9, 50), (9, 55), (10, 0)]:
        set_bar(3, hh, mm, 100.0, 100.5, 99.5, 100.0)
    set_bar(3, 10, 5, 100.0, 100.0, 96.5, 97.0)
    set_bar(3, 10, 10, 97.0, 97.5, 96.0, 96.5)
    # Dia 4: sin patron plantado (ruido; puede o no dar senal, es solo relleno).
    # Dia 5: media sesion (solo 9:30-10:30; en corrida real lo excluye el calendario).
    return bars


# ---------------------------------------------------------------------------
# Artefactos: metrics + manifest + INFORME + status
# ---------------------------------------------------------------------------
def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def render_informe(status: str, market_results: dict[str, Any], pins: dict[str, Any],
                   prereg_sha: str, smoke: bool) -> str:
    L: list[str] = []
    L.append(f"# INFORME N1 — status {status}")
    L.append("")
    L.append(f"Preregistro congelado sha256: `{prereg_sha}`")
    if smoke:
        L.append("")
        L.append("> SMOKE TECNICO con fixture sintetico: los numeros NO son resultado.")
    L.append("")
    L.append("## Parametros")
    L.append("")
    L.append(f"- k_d={pins['k_d']}, k_stop={pins['k_stop']}, ATR(14) Wilder M5")
    L.append(f"- salida primaria: primera barra >= 12:00 ET; sensibilidad: {pins['exit_sens_bars']} barras")
    L.append("- costes realista: 0,62 USD/side + 0,25 pt; sensibilidad: 2,0 pts RT exactos (1,0 pt/side comision, slip 0)")
    L.append(f"- seeds: cbb={pins['cbb_seed']}, control={pins['control_seed']}")
    L.append(f"- N_min: {pins['n_min_trades']} trades (MNQ) / {pins['n_min_events']} eventos stage1")
    L.append("- stop A: extremo de la barra de barrido (high/low) +- k_stop*ATR; stop C: cierre breakout -+ k_stop*ATR (ancla C: gap declarado)")
    L.append("- sizing: riesgo 1% sobre 50k (motor canonico, max_contracts por defecto=10)")
    L.append("")
    L.append("## Metricas")
    L.append("")
    for sym, m in market_results.items():
        role = m.get("market_role", "confirmatorio" if sym == "MNQ" else "exploratorio")
        if m.get("status") == "exploratorio-no-disponible":
            L.append(f"- {sym} ({role}): exploratorio-no-disponible (sin dataset, no inventado)")
            continue
        for cand in ("A", "C"):
            c = m.get(cand, {})
            st = c.get("status", "?")
            r = c.get("realista", {})
            n_t = r.get("n_trades", 0)
            ci_val = r.get("cbb_ci95_mean_budget_r")
            ci_str = "ABSTENCION" if (ci_val is None or n_t < pins["n_min_trades"]) else str(ci_val)
            L.append(f"- {sym} ({role})/{cand}: status={st} n={n_t} "
                     f"E[budget-R]={r.get('mean_budget_r', 0):+.4f} "
                     f"E[stop-R]={r.get('mean_stop_r', 0):+.4f} "
                     f"CI95={ci_str} "
                     f"gaps={c.get('gap_rejections', 0)} unres={c.get('unresolved', 0)}")
            if cand == "A" and "event_study_stage1" in c:
                e = c["event_study_stage1"]
                e_ci = e.get("cbb_ci95")
                e_ci_str = "ABSTENCION" if (e_ci is None or e.get("n_events", 0) < pins["n_min_events"]) else str(e_ci)
                L.append(f"  - stage1 event-study K={e['K_barras']}: n={e['n_events']} "
                         f"mean={e['mean_signed_pts']:+.4f}pts CI95={e_ci_str} gate={e['gate']}")
            if cand == "C" and "regresion_interaccion" in c:
                rg = c["regresion_interaccion"]
                L.append(f"  - regresion beta={rg.get('beta')} se={rg.get('se')} "
                         f"p_one_sided={rg.get('p_one_sided')} n={rg.get('n')}")
                v = c.get("variante_pct_gt_50", {})
                v_ci = v.get("cbb_ci95")
                v_ci_str = "ABSTENCION" if (v_ci is None or v.get("n_trades", 0) < pins["n_min_trades"]) else str(v_ci)
                L.append(f"  - variante pct>50: n={v.get('n_trades')} E[R]={v.get('mean_budget_r', 0.0):+.4f} "
                         f"CI95={v_ci_str}")
    L.append("")
    L.append("## Anomalias observadas")
    L.append("")
    for sym, m in market_results.items():
        if "diag" not in m:
            continue
        L.append(f"- {sym}: " + json.dumps(m["diag"], ensure_ascii=False))
    L.append("")
    L.append("## Limitaciones declaradas")
    L.append("")
    L.append("- W1: coste sensibilidad = 2,0 pts RT exactos (1,0 pt/side comision, slip 0).")
    L.append("- W2: lista de rolls unica para todos los mercados (proxy tercer viernes); roll real por contrato pendiente.")
    L.append("- W5: agrupacion por fecha ET en vez de sesion de exchange (limitacion declarada; parser rechaza timestamps duplicados).")
    L.append("- W6: SE de regresion OLS clasicos (sin HAC); la afirmacion confirmatoria usa CBB de la media, no el p-value de beta.")
    L.append("")
    L.append("## Confianza en el calculo")
    L.append("")
    for sym, m in market_results.items():
        if "gates" not in m:
            continue
        g = m["gates"]
        L.append(f"- {sym}: G1={'PASS' if g['G1_signal_parity']['PASS'] else 'FAIL'} "
                 f"G2={'PASS' if g['G2_mapping']['PASS'] else 'FAIL'} "
                 f"G3={'PASS' if g['G3_determinism']['PASS'] else 'FAIL'} "
                 f"G4={'PASS' if g['G4_sealed']['PASS'] else 'FAIL'} "
                 f"G5={g['G5_cbb']['status']}")
    L.append("- Conclusion cuantitativa: DIFERIDA a Hermes (multiplicidad >120 comparaciones; el preregistro no fija alfa).")
    L.append("- Gaps declarados: ancla del stop C; sensibilidad 2pt como canonico M9; roll-dates por CLI; MGC bajo regla 78 barras.")
    L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="Runner N1 (preregistro congelado)")
    ap.add_argument("--smoke", action="store_true",
                    help="fixture sintetico; numeros NO son resultado")
    ap.add_argument("--markets", default="MNQ",
                    help="mercados separados por coma (defecto MNQ confirmatorio)")
    ap.add_argument("--roll-dates", default="",
                    help="fechas de roll YYYY-MM-DD separadas por coma (sin entradas)")
    ap.add_argument("--outdir", default=str(N1_DIR))
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    prereg = verify_preregistro()
    prereg_sha = compute_file_sha256(PREREG_PATH).lower()
    print(f"Preregistro congelado verificado (SHA256: {prereg_sha})", flush=True)
    pins = extract_pins(prereg)
    roll_dates = {date.fromisoformat(d.strip()) for d in args.roll_dates.split(",") if d.strip()}

    market_results: dict[str, Any] = {}
    datasets_meta: dict[str, Any] = {}
    raw: dict[str, tuple[bytes, str]] = {}

    if not args.smoke:
        target = [m.strip().upper() for m in args.markets.split(",") if m.strip()]
        for sym in target:
            member = MARKET_FILES[sym]
            try:
                content, sha = read_member_bytes(ZIP_PATH, member)
            except FileNotFoundError:
                if sym in EXPLORATORY:
                    market_results[sym] = {"status": "exploratorio-no-disponible",
                                           "market_role": "exploratorio"}
                    datasets_meta[sym] = {"status": "exploratorio-no-disponible",
                                          "member": member}
                    print(f"[{sym}] exploratorio-no-disponible (sin dataset)", flush=True)
                    continue
                emit_invalid("DATA", f"MNQ-canonico-ausente({member})")
            raw[sym] = (content, sha)
            datasets_meta[sym] = {
                "member": member, "sha256": sha,
            }
            print(f"[{sym}] sha256 registrado antes de leer: {sha[:16]}...", flush=True)
    else:
        datasets_meta["MNQ"] = {"source": "sintetico-smoke", "sha256": "n/a-smoke"}

    # W3: persistir sha del preregistro + sha de datasets en manifest.json ANTES de procesar
    initial_manifest = {
        "metadata": {"title": "Manifest N1", "status": "PROCESSING",
                     "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                     "runtime": sys.version,
                     "preregistro_sha256": prereg_sha,
                     "smoke_mode": args.smoke},
        "datasets": datasets_meta,
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36,
                              "test_months": 6, "step_months": 6,
                              "purge_gap_bars": 0,
                              "warmup_bars": CALIBRATION_BARS_COUNT,
                              "dev_folds": "0..5",
                              "sealed_folds": "6..7",
                              "final_sealed": "ultimos-6-meses-1-corrida-al-final"},
        "aclaraciones_implementacion": [
            "Lectura canonica de tramos sellados (coherente con protocolo H1): dev = folds 0..5; sellado = folds 6..7 + tramo final 6 meses, abierto UNA vez al final."
        ],
        "pins": pins,
        "seeds": {"cbb_seed": pins["cbb_seed"], "control_seed": pins["control_seed"]},
        "roll_dates": sorted(d.isoformat() for d in roll_dates) or "no-provistas",
        "cost_scenarios": COST_SCENARIOS,
        "artifacts_sha256": {},
    }
    write_json(outdir / "manifest.json", initial_manifest)

    try:
        if args.smoke:
            print("MODO SMOKE: fixture sintetico — los numeros NO son resultado.", flush=True)
            bars = build_smoke_bars()
            market_results["MNQ"] = process_market("MNQ", bars, pins, None, set(), True)
            datasets_meta["MNQ"]["n_bars"] = len(bars)
            status = "SMOKE"
        else:
            for sym, (content, sha) in raw.items():
                bars = parse_bars(content)
                plan = WalkForwardPlan.create_calendar_rolling(
                    bars, train_months=36, test_months=6, step_months=6,
                    purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT)
                print(f"[{sym}] {len(bars)} barras | plan C1: {plan.n_folds} folds", flush=True)
                market_results[sym] = process_market(sym, bars, pins, plan, roll_dates, False)
                datasets_meta[sym].update({
                    "n_bars": len(bars),
                    "first": bars[0].timestamp.isoformat() if bars else None,
                    "last": bars[-1].timestamp.isoformat() if bars else None,
                    "filter_start": FILTER_START,
                })
            statuses = [m.get("A", {}).get("status", "") for m in market_results.values()
                        if isinstance(m.get("A"), dict)]
            statuses += [m.get("C", {}).get("status", "") for m in market_results.values()
                         if isinstance(m.get("C"), dict)]
            status = "ABSTENCION" if any(s == "ABSTENCION" for s in statuses) else "OK"
    except SystemExit as exc:
        initial_manifest["metadata"]["status"] = f"ABORTED({exc.code})"
        initial_manifest["metadata"]["aborted_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(outdir / "manifest.json", initial_manifest)
        raise

    # artefactos
    for sym, m in market_results.items():
        if m.get("status") == "exploratorio-no-disponible":
            continue
        write_json(outdir / f"metrics_{sym}_N1.json", {
            "metadata": {"market": sym, "status": status,
                         "market_role": m.get("market_role", "confirmatorio" if sym == "MNQ" else "exploratorio"),
                         "preregistro_sha256": prereg_sha,
                         "dataset_sha256": datasets_meta.get(sym, {}).get("sha256"),
                         "smoke_mode": args.smoke,
                         "ran_at_utc": datetime.now(timezone.utc).isoformat()},
            **m,
        })
        print(f"Escrito metrics_{sym}_N1.json", flush=True)

    artifacts = {p.name: compute_file_sha256(p) for p in sorted(outdir.glob("metrics_*.json"))}
    manifest = {
        "metadata": {"title": "Manifest N1", "status": status,
                     "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                     "runtime": sys.version,
                     "preregistro_sha256": prereg_sha,
                     "smoke_mode": args.smoke},
        "datasets": datasets_meta,
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36,
                              "test_months": 6, "step_months": 6,
                              "purge_gap_bars": 0,
                              "warmup_bars": CALIBRATION_BARS_COUNT,
                              "dev_folds": "0..5",
                              "sealed_folds": "6..7",
                              "final_sealed": "ultimos-6-meses-1-corrida-al-final"},
        "aclaraciones_implementacion": [
            "Lectura canonica de tramos sellados (coherente con protocolo H1): dev = folds 0..5; sellado = folds 6..7 + tramo final 6 meses, abierto UNA vez al final."
        ],
        "pins": pins,
        "seeds": {"cbb_seed": pins["cbb_seed"], "control_seed": pins["control_seed"]},
        "roll_dates": sorted(d.isoformat() for d in roll_dates) or "no-provistas",
        "cost_scenarios": COST_SCENARIOS,
        "artifacts_sha256": artifacts,
    }
    write_json(outdir / "manifest.json", manifest)
    (outdir / "INFORME.md").write_text(
        render_informe(status, market_results, pins, prereg_sha, args.smoke),
        encoding="utf-8")
    write_json(outdir / "n1_status.json", {"status": status})
    print(f"N1 STATUS: {status}", flush=True)


if __name__ == "__main__":
    main()
