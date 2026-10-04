#!/usr/bin/env python3
"""
run_sb.py — Strategy B (nq-strategy-b-bot) medido desde cero sobre MNQ de FARS.

PROVENENCIA DE LAS REGLAS (clean-room, NO es el código del autor):
  Repo: github.com/prashanthaitha24/nq-strategy-b-bot @ bc199e7 (MIT)
  Reglas transcritas de `analysis/ict_fvg_backtest.py:210-350` (run_strategy_b)
  y constantes de `analysis/smc_breakout_backtest.py:41-45`.
  El backtest original NO corre (faltan `signals/fvg.py` y
  `analysis/atb_refinement_backtest.py`; `load_data()` busca otro dataset),
  por eso las reglas se reimplementan de forma independiente.

CORRECCIONES DE AUDITORÍA (Hermes 2026-09-28, documentadas en INFORME_SB.md):
  B1 look-ahead FVG 15m: el autor activa un FVG cuando `f.time < ts`, pero un
     FVG 15m solo se conoce al cerrar su TERCERA barra (+30 min). Usarlo antes
     = mirar el futuro hasta 25 min. Ahora `fvg_usable_at()` exige
     `f.time + 2*bar_len <= decision_ts`.
  B2 gap-stop: el autor llena el stop a precio exacto aunque la vela abra a
     través de él. Ahora gap-stop llena al open (conservador).
  B3 doble slippage: el slip ya va en el precio de entrada y además se
     restaba como coste. Ahora el coste es comisión (+ slip de salida solo en
     el escenario FARS).
  B4 FVGs entre sesiones: el slice 06:00-16:00 crea "FVGs" sobre el gap
     nocturno. Ahora se exige misma sesión (mismo día ET).
  B5 SL y TP en la misma vela: supuesto conservador (gana SL). Ya estaba, ahora
     está fijado por test.

SEPARACIÓN (decisión de Ricardo 2026-09-28):
  estrategia = reglas del autor · adaptación = este archivo · FARS Core = solo
  se invoca después, con el CSV canónico del ledger. Sin tocar src/ ni specs.

SALIDAS (en este directorio):
  ledger_sb_<tag>.csv · metrics_sb.json · manifest_sb.json · INFORME_SB.md
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import zipfile
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ZIP_PATH = Path("E:/FARS-LAB/databento.zip")
MEMBER = "databento/MNQ_M5.csv"
OUT = HERE
SIGNAL_BAR_SECONDS = 300

# ── Constantes de la estrategia (transcritas del repo del autor) ────────────
MIN_FVG_PTS = 3.0            # ict_fvg_backtest.py:40
ENTRY_CUTOFF = dtime(12, 0)  # :39
HARD_EXIT = dtime(15, 45)    # smc_breakout_backtest.py:45
TICK = 0.25                  # :41
TICK_VAL = 0.50              # :42 (MNQ 0.25 pt = $0.50 -> $2/pt)
COMM_AUTOR_USD = 1.18        # :43
SLIP_AUTOR = TICK            # :44 (1 tick, solo entrada)
STOP_BUFFER_PTS = 2.0        # ict_fvg_backtest.py:291
TARGET_R = 2.0               # :299
FVG15_BUFFER_PTS = 5.0       # :263
MAX_TRADES_DAY = 1           # :228
FVG5_MAX_AGE_BARS = 24       # :254
FVG15_MAX_AGE_BARS = 96      # :247
FVG5_WINDOW = (dtime(6, 0), dtime(16, 0))   # :518
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
DOLLAR_PER_POINT = 2.0       # MNQ

COMM_FARS_USD_PER_SIDE = 0.62
SLIP_FARS_PTS = 0.25

_FVG_TIMES: dict[int, list[float]] = {}


# ── Costes ─────────────────────────────────────────────────────────────────
def cost_model(scenario: str) -> dict:
    if scenario == "autor":
        return {
            "comm_pts": round(COMM_AUTOR_USD / DOLLAR_PER_POINT, 6),   # 0.59
            "slip_entry_pts": SLIP_AUTOR,      # ya va en el precio de entrada
            "slip_exit_pts": 0.0,
        }
    if scenario == "fars_realista":
        return {
            "comm_pts": round(COMM_FARS_USD_PER_SIDE * 2 / DOLLAR_PER_POINT, 6),  # 0.62
            "slip_entry_pts": SLIP_FARS_PTS,
            "slip_exit_pts": SLIP_FARS_PTS,    # salidas no-target
        }
    raise ValueError(scenario)


def trade_net_pts(t: "Trade", scenario: str) -> float:
    """PnL neto en puntos MNQ, ya SIN contar dos veces el slippage de entrada
    (el precio de entrada ya lo trae). El slip de salida solo aplica a
    salidas que no son target."""
    cfg = cost_model(scenario)
    d = 1 if t.direction == "long" else -1
    move = (t.exit_price - t.entry_price) * d
    exit_cost = 0.0 if t.exit_reason == "take_profit" else cfg["slip_exit_pts"]
    return move - cfg["comm_pts"] - exit_cost


# ── Carga de datos (sha antes de leer) ─────────────────────────────────────
def load_bars() -> tuple[list, str]:
    with zipfile.ZipFile(ZIP_PATH) as zf:
        raw = zf.read(MEMBER)
    sha = hashlib.sha256(raw).hexdigest()
    rows = []
    reader = csv.reader(io.StringIO(raw.decode("utf-8")))
    header = next(reader)
    idx = {name.lower().strip(): i for i, name in enumerate(header)}
    it = iter(reader)
    for r in it:
        if not r:
            continue
        col = idx.get("timestamp", 0)
        ts = datetime.fromisoformat(r[col].strip())
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        ts = ts.astimezone(ET)
        rows.append((ts, float(r[idx["open"]]), float(r[idx["high"]]),
                     float(r[idx["low"]]), float(r[idx["close"]])))
    rows.sort(key=lambda b: b[0])
    return rows, sha


# ── FVGs ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FVG:
    type: str
    top: float
    bottom: float
    mid: float
    time: datetime
    gap: float
    bar_seconds: int

    @property
    def known_at(self) -> datetime:
        """Momento en que el FVG es conocible: al cerrar su TERCERA barra."""
        return self.time + timedelta(seconds=2 * self.bar_seconds)


def fvg_usable_at(f: FVG, decision_ts: datetime) -> bool:
    """Un FVG solo puede usarse cuando su tercera barra ya cerró."""
    return f.known_at <= decision_ts


def detect_fvgs(bars: list, min_gap: float = MIN_FVG_PTS,
                bar_seconds: int = 300, same_session_only: bool = True) -> list[FVG]:
    fvgs: list[FVG] = []
    for i in range(1, len(bars) - 1):
        prev, curr, nxt = bars[i - 1], bars[i], bars[i + 1]
        if same_session_only and not (prev[0].date() == curr[0].date() == nxt[0].date()):
            continue
        gt, gb = nxt[3], prev[2]
        if gt > gb and (gt - gb) >= min_gap:
            fvgs.append(FVG("bull", gt, gb, (gt + gb) / 2, curr[0], gt - gb, bar_seconds))
        gt2, gb2 = prev[3], nxt[2]
        if gt2 > gb2 and (gt2 - gb2) >= min_gap:
            fvgs.append(FVG("bear", gt2, gb2, (gt2 + gb2) / 2, curr[0], gt2 - gb2, bar_seconds))
    return fvgs


def active_fvgs_at(fvg_list: list[FVG], ts_bar: datetime, price: float,
                   max_age_bars: int) -> list[FVG]:
    """FVGs usables en el momento de decidir (cierre de la barra ts_bar),
    con la mitación simplificada del autor (price vs mid) y la edad del autor."""
    if not fvg_list:
        return []
    decision_ts = ts_bar + timedelta(seconds=SIGNAL_BAR_SECONDS)
    key = id(fvg_list)
    times = _FVG_TIMES.get(key)
    if times is None:
        times = [f.time.timestamp() for f in fvg_list]
        _FVG_TIMES[key] = times
    cutoff = ts_bar - timedelta(seconds=max_age_bars * (fvg_list[0].bar_seconds))
    lo = bisect_left(times, cutoff.timestamp())
    out = []
    for f in fvg_list[lo:]:
        if f.time >= ts_bar:
            break
        if not fvg_usable_at(f, decision_ts):
            continue
        if f.type == "bull" and price < f.mid:
            continue
        if f.type == "bear" and price > f.mid:
            continue
        out.append(f)
    return out


# ── Sesión ─────────────────────────────────────────────────────────────────
def resample_15min(bars: list) -> list:
    buckets: dict[datetime, list] = {}
    for b in bars:
        t = b[0]
        if not (dtime(9, 30) <= t.time() <= dtime(16, 0)):
            continue
        key = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
        buckets.setdefault(key, []).append(b)
    return [(k, g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4])
            for k, g in sorted(buckets.items())]


# ── Motor de un trade ──────────────────────────────────────────────────────
@dataclass
class Trade:
    trade_id: str
    timestamp: str
    asset: str
    direction: str
    entry_price: float
    stop_price: float
    exit_price: float
    exit_time: str
    exit_reason: str
    risk_pts: float
    target_price: float
    strategy: str


def _stop_fill(direction: int, stop: float, bar_open: float) -> float:
    """Gap-stop al open (conservador): si la vela abre a través del stop,
    se llena al open, no al precio del stop."""
    return min(stop, bar_open) if direction == 1 else max(stop, bar_open)


def run_trade(bars: list, j: int, d_dir: int, entry: float, stop: float,
              target: float, fillbar_mode: str) -> tuple[float, str, str]:
    """(exit_px, exit_time_iso, reason).

    fillbar_mode 'autor' : ni SL ni TP en la vela de fill (walk desde j+2)
    fillbar_mode 'a1'    : en la vela de fill SOLO SL; TP desde la vela siguiente
    Misma vela con SL y TP: conservador, gana SL.
    """
    n = len(bars)
    start = j + 1 if fillbar_mode == "a1" else j + 2
    for k in range(start, n):
        b = bars[k]
        if b[0].time() >= HARD_EXIT:
            return float(b[1]), b[0].isoformat(), "time_exit_1545"
        h2, l2, o2 = b[2], b[3], b[1]
        fill_bar = (k == j + 1)
        if d_dir == 1:
            if l2 <= stop:
                return float(_stop_fill(1, stop, o2)), b[0].isoformat(), "stop_loss"
            if (not fill_bar) and h2 >= target:
                return float(target), b[0].isoformat(), "take_profit"
        else:
            if h2 >= stop:
                return float(_stop_fill(-1, stop, o2)), b[0].isoformat(), "stop_loss"
            if (not fill_bar) and l2 <= target:
                return float(target), b[0].isoformat(), "take_profit"
    last = bars[n - 1]
    return float(last[4]), last[0].isoformat(), "eod_close"


# ── Señal Strategy B ──────────────────────────────────────────────────────
def scan(trades: list, bars_rth_by_day: dict, fvgs_5: list, fvgs_15: list,
         only_long: bool, fillbar_mode: str, slip: float, tag: str,
         diag: dict) -> None:
    n_id = 0
    for trade_date in sorted(bars_rth_by_day):
        day_bars = bars_rth_by_day[trade_date]
        day_trades = 0
        for j, bar in enumerate(day_bars):
            ts = bar[0]
            if ts.time() >= ENTRY_CUTOFF:
                break
            if day_trades >= MAX_TRADES_DAY:
                break
            lo, hi, cl = bar[3], bar[2], bar[4]
            active_15 = active_fvgs_at(fvgs_15, ts, cl, FVG15_MAX_AGE_BARS)
            if not active_15:
                continue
            active_5 = active_fvgs_at(fvgs_5, ts, cl, FVG5_MAX_AGE_BARS)
            sig = None
            for f5 in active_5:
                if f5.type == "bull" and lo <= f5.top and lo >= f5.bottom and cl > f5.top:
                    for f15 in active_15:
                        if (f15.type == "bull" and f5.bottom >= f15.bottom
                                and f5.top <= f15.top + FVG15_BUFFER_PTS):
                            sig = ("long", f5)
                            break
                elif f5.type == "bear" and hi >= f5.bottom and hi <= f5.top and cl < f5.bottom:
                    for f15 in active_15:
                        if (f15.type == "bear" and f5.top <= f15.top
                                and f5.bottom >= f15.bottom - FVG15_BUFFER_PTS):
                            sig = ("short", f5)
                            break
                if sig:
                    break
            if sig is None:
                continue
            if only_long and sig[0] == "short":
                continue
            if j + 1 >= len(day_bars):
                diag[f"{tag}_sin_barra_entrada"] = diag.get(f"{tag}_sin_barra_entrada", 0) + 1
                continue
            entry_bar = day_bars[j + 1]
            if entry_bar[0].time() >= HARD_EXIT:
                continue
            d_dir = 1 if sig[0] == "long" else -1
            f5 = sig[1]
            entry = float(entry_bar[1]) + slip * d_dir
            if d_dir == 1:
                stop = round(f5.bottom - STOP_BUFFER_PTS, 2)
                risk = entry - stop
            else:
                stop = round(f5.top + STOP_BUFFER_PTS, 2)
                risk = stop - entry
            if risk <= 0:
                diag[f"{tag}_riesgo_no_positivo"] = diag.get(f"{tag}_riesgo_no_positivo", 0) + 1
                continue
            target = round(entry + d_dir * TARGET_R * risk, 2)
            exit_px, exit_ts, reason = run_trade(day_bars, j, d_dir, entry, stop,
                                                 target, fillbar_mode)
            n_id += 1
            trades.append(Trade(
                trade_id=f"{tag}-{trade_date.isoformat()}-{n_id:04d}",
                timestamp=entry_bar[0].isoformat(),
                asset="MNQ",
                direction="long" if d_dir == 1 else "short",
                entry_price=round(entry, 2),
                stop_price=round(stop, 2),
                exit_price=round(exit_px, 2),
                exit_time=exit_ts,
                exit_reason=reason,
                risk_pts=round(risk, 4),
                target_price=round(target, 2),
                strategy=f"strategy_b_nqbot:{tag}",
            ))
            day_trades += 1
            diag[f"{tag}_salidas_{reason}"] = diag.get(f"{tag}_salidas_{reason}", 0) + 1


# ── Métricas ───────────────────────────────────────────────────────────────
def summarize(trades: list, scenario: str) -> dict:
    """E[R] = media de r_result; r_result = (PnL neto en puntos) / riesgo en
    puntos, con el riesgo = distancia entrada→stop al fill. DD = máximo
    drawdown de la curva acumulada de PnL neto en USD (y en R)."""
    if not trades:
        return {"n": 0}
    rs, pnls = [], []
    for t in trades:
        net = trade_net_pts(t, scenario)
        rs.append(net / t.risk_pts)
        pnls.append(net * DOLLAR_PER_POINT)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    cum = peak = dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    eq_r = peak_r = dd_r = 0.0
    for r in rs:
        eq_r += r
        peak_r = max(peak_r, eq_r)
        dd_r = max(dd_r, peak_r - eq_r)
    n = len(rs)
    mean = sum(rs) / n
    var = sum((x - mean) ** 2 for x in rs) / (n - 1) if n > 1 else 0.0
    by_year: dict[str, dict] = {}
    for t, r, p in zip(trades, rs, pnls):
        b = by_year.setdefault(t.timestamp[:4], {"n": 0, "net_usd": 0.0, "net_r": 0.0, "wr": 0})
        b["n"] += 1
        b["net_usd"] += p
        b["net_r"] += r
        b["wr"] += 1 if p > 0 else 0
    for b in by_year.values():
        b["net_usd"] = round(b["net_usd"], 2)
        b["net_r"] = round(b["net_r"], 4)
        b["win_rate"] = round(b["wr"] / b["n"], 4)
        del b["wr"]
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "expectancy_R": round(mean, 4),
        "std_R": round(math.sqrt(var), 4),
        "net_usd": round(sum(pnls), 2),
        "profit_factor": round(gw / gl, 4) if gl > 0 else None,
        "max_dd_usd": round(dd, 2),
        "max_dd_R": round(dd_r, 4),
        "avg_win_R": round(sum(r for r in rs if r > 0) / max(1, sum(1 for r in rs if r > 0)), 4),
        "avg_loss_R": round(sum(r for r in rs if r <= 0) / max(1, sum(1 for r in rs if r <= 0)), 4),
        "max_losing_streak": _max_streak(pnls, win=False),
        "by_year": by_year,
    }


def _max_streak(pnls: list, win: bool) -> int:
    best = cur = 0
    for p in pnls:
        hit = (p > 0) if win else (p <= 0)
        cur = cur + 1 if hit else 0
        best = max(best, cur)
    return best


def write_ledger(trades: list, scenario: str, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["trade_id", "timestamp", "asset", "direction",
                    "entry_price", "stop_price", "exit_price", "r_result",
                    "strategy"])
        for t in trades:
            w.writerow([t.trade_id, t.timestamp, t.asset, t.direction,
                        t.entry_price, t.stop_price, t.exit_price,
                        round(trade_net_pts(t, scenario) / t.risk_pts, 6),
                        t.strategy])


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    print("Cargando MNQ M5 del zip canónico …")
    bars, dataset_sha = load_bars()
    print(f"  {len(bars):,} barras | {bars[0][0]} → {bars[-1][0]}")
    print(f"  dataset sha256 = {dataset_sha}")

    win_lo, win_hi = FVG5_WINDOW
    bars_fvg5 = [b for b in bars if win_lo <= b[0].time() <= win_hi]
    rth = [b for b in bars if dtime(9, 30) <= b[0].time() <= dtime(16, 0)]
    bars_by_day: dict[date, list] = {}
    for b in rth:
        bars_by_day.setdefault(b[0].date(), []).append(b)

    print("Detectando FVGs (5m y 15m, misma sesión) …")
    fvgs_5 = detect_fvgs(bars_fvg5, bar_seconds=300)
    fvgs_15 = detect_fvgs(resample_15min(bars), bar_seconds=900)
    print(f"  5m: {len(fvgs_5):,} | 15m: {len(fvgs_15):,}")

    diag: dict = {}
    results = {}
    for scen in ("autor", "fars_realista"):
        cfg = cost_model(scen)
        for only_long, dir_tag in ((True, "long_only"), (False, "both")):
            for fb in ("autor", "a1"):
                tag = f"{scen}_{dir_tag}_{fb}"
                trades: list[Trade] = []
                scan(trades, bars_by_day, fvgs_5, fvgs_15, only_long, fb,
                     cfg["slip_entry_pts"], tag, diag)
                s = summarize(trades, scen)
                results[tag] = s
                write_ledger(trades, scen, OUT / f"ledger_sb_{tag}.csv")
                print(f"  [{tag}] n={s['n']} E[R]={s.get('expectancy_R')} "
                      f"WR={s.get('win_rate')} net=${s.get('net_usd')} DD=${s.get('max_dd_usd')}")

    (OUT / "metrics_sb.json").write_text(
        json.dumps({"results": results, "diag": diag}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    (OUT / "manifest_sb.json").write_text(json.dumps({
        "dataset_member": MEMBER,
        "dataset_sha256": dataset_sha,
        "bars_total": len(bars),
        "source_repo": "github.com/prashanthaitha24/nq-strategy-b-bot",
        "source_commit": "bc199e7",
        "license": "MIT",
        "rules_transcribed_from": "analysis/ict_fvg_backtest.py:210-350 (run_strategy_b)",
        "constants": {"MIN_FVG_PTS": MIN_FVG_PTS, "ENTRY_CUTOFF": str(ENTRY_CUTOFF),
                      "HARD_EXIT": str(HARD_EXIT), "STOP_BUFFER_PTS": STOP_BUFFER_PTS,
                      "TARGET_R": TARGET_R, "FVG15_BUFFER_PTS": FVG15_BUFFER_PTS,
                      "MAX_TRADES_DAY": MAX_TRADES_DAY,
                      "FVG5_MAX_AGE_BARS": FVG5_MAX_AGE_BARS,
                      "FVG15_MAX_AGE_BARS": FVG15_MAX_AGE_BARS},
        "audit_fixes": ["B1 look-ahead FVG15 (known_at)", "B2 gap-stop al open",
                        "B3 coste sin doble slippage", "B4 FVG solo misma sesión",
                        "B5 SL>TP conservador en misma vela"],
        "scenarios": {k: cost_model(k) for k in ("autor", "fars_realista")},
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Escrito metrics_sb.json, manifest_sb.json y ledgers.")


if __name__ == "__main__":
    main()
