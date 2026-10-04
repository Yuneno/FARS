#!/usr/bin/env python3
"""Runner W4 — candidata B: Wednesday Weekly Cycle (medicion de la hipotesis).

Ejecuta EXACTAMENTE el preregistro congelado
``lab_artifacts/w4_protocol/preregistro.json`` (sha256 fail-closed al arrancar).

Fork del subconjunto auditado de ``lab_artifacts/n1_protocol/run_n1.py``:
verificacion sha fail-closed del preregistro + pins, parseo/dedup de barras
desde el zip canonico, escenarios de coste (realista / sensibilidad),
``cbb_ci``, ``paired_control``, resumen R, semantica de tramos sellados
(dev = folds 0..5; sellado = folds 6..7 + cola 6 meses, UNA sola apertura),
``mirror_valid_levels``, gates G1-G5, ``manifest.json`` / ``INFORME.md`` /
status. PROHIBIDO importar senales, event-study, vol-regimen o fixtures de N1:
este archivo es autocontenido (solo LEE el motor canonico de ``src/``).

Deltas explicitos respecto a ``run_n1.py`` (lista completa en INFORME.md):
ver ``render_informe`` / seccion "Deltas respecto a run_n1.py".

Semantica W4 (pins del preregistro):
post-fix fill-bar (fills next-open + slip + tick); regla A1 solo SL en vela
de fill; gap-stop al open; gap-entry -> rechazo con PRIORIDAD (el gap-stop
solo aplica a posiciones ya abiertas). Senal D1 lunes/martes por fechas
exactas; sesiones RTH completas de 78 slots [09:30,16:00) ET con basis
identico; D1 exacto (igualdad no cuenta; doble sweep / sin cierre interior /
sin barrido -> NO_SIGNAL). Reloj: senal en barra 09:25 ET (su close = entry
estimado), fill en open de la barra 09:30 ET, bar_interval=300. Stop =
extremo del barrido del martes (margen 0), redondeo a tick con polaridad
estricta; colapso -> NO_SIGNAL. Target = 1.5R absoluto desde entry estimado
(Alt B); drift R nominal vs efectivo medido POR TRADE (distribucion, sin
claims escalares). Salida temporal: primera barra >= 16:00 ET del mismo dia
(390 min), exit_idx fijado y pre-chequeado ANTES de correr; sin esa barra ->
trade NO corrido, contador ``unresolved`` (sin fill inventado, sin
reclasificar como NO_SIGNAL). Pre-chequeo mirror: fill que cruza STOP ->
``gap_reject``; fill que cruza TARGET -> ``gap_reject_beyond_target``.
Cantidad fija 1 contrato; headline = stop-R; budget-R secundario;
risk_dollars + elegibilidad Practice <= $200 solo telemetria.

PROHIBIDO tocar src/: este archivo solo LEE el motor canonico.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import zipfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
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
from src.backtest.markets import MES, MNQ, MYM  # noqa: E402
from src.backtest.strategy import Signal  # noqa: E402
from src.hypothesis_registry import WalkForwardPlan  # noqa: E402
from src.zones.wednesday_rth import RTH_SESSION_BASIS  # noqa: E402

W4_DIR = Path(__file__).resolve().parent
PREREG_PATH = W4_DIR / "preregistro.json"
PREREG_SHA256 = "a80b5746c8073d8a314629c12b52669d4caebcefa5146f4501a8f3cebaa60dd4"

ET = ZoneInfo("America/New_York")
BAR_SECONDS = 300

# Mercados del bloque: MNQ confirmatorio; MES/MYM exploratorios.
# MGC EXCLUIDO por pin (sesion 08:20-13:30 ET incompatible con salida 16:00,
# tick 0.1, adapter W2 exclusivamente MNQ RTH): pasarlo por CLI se rechaza.
MARKET_SPECS = {"MNQ": MNQ, "MES": MES, "MYM": MYM}
ALLOWED_MARKETS = ("MNQ", "MES", "MYM")
MARKET_FILES = {
    "MNQ": "databento/MNQ_M5.csv",
    "MES": "databento/MES_M5.csv",
    "MYM": "databento/MYM_M5.csv",
}

# Grilla RTH canonica: 78 slots exactos [09:30,16:00) ET en minutos locales.
RTH_OPEN_MIN = 9 * 60 + 30
RTH_CLOSE_MIN = 16 * 60
RTH_SLOT_MINUTES = frozenset(RTH_OPEN_MIN + 5 * k for k in range(78))
LAST_RTH_MIN = 15 * 60 + 55
SIGNAL_MIN = 9 * 60 + 25  # barra 09:25-09:30 ET (su close = entry estimado)
FILL_MIN = 9 * 60 + 30  # fill al open de la barra 09:30 ET
EXIT_MIN = 16 * 60  # salida temporal: primera barra >= 16:00 ET

# Basis de sesion RTH identico exigido en lunes y martes: alias contrastado
# contra el canonico de W2 (src/zones/wednesday_rth.py). Si el canonico cambia,
# la importacion lo refleja y el chequeo de process_market lo declara.
W4_RTH_BASIS = RTH_SESSION_BASIS

TARGET_FAR_PTS = 1_000_000.0  # comparador temporal puro: sin target 1.5R

# Descripcion literal del comparador temporal (pin congelado): conserva el stop
# estructural y solo elimina el target; la salida temporal es la unica salida
# por diseño salvo stop. Debe aparecer literal en INFORME y artefactos (W5).
TEMPORAL_NOTE = ("sin target 1.5R; conserva stop estructural; salida temporal única "
                 "(sub-análisis, sin estatus de test)")

# Sellado persistente entre ejecuciones (C2): se crea al abrir el sellado y
# bloquea reaperturas posteriores (fail-closed).
SEALED_LOCK_NAME = "w4_sealed.lock"

COST_SCENARIOS = {
    "realista": {"commission_per_side": 0.62, "slip_pts": 0.25},
    "sensibilidad": {"commission_per_side_pts": 1.0, "slip_pts": 0.0},
}


# ---------------------------------------------------------------------------
# Preregistro congelado (fail-closed) + pins
# ---------------------------------------------------------------------------
def verify_preregistro(path: Path = PREREG_PATH, expected_sha: str = PREREG_SHA256) -> dict[str, Any]:
    """Verifica sha256 del preregistro y lo devuelve parseado.

    Falla cerrado (SystemExit codigo 2) si falta o no coincide. Nada se
    ejecuta sin el preregistro exacto.
    """
    if not path.exists():
        raise SystemExit(
            f"W4 STATUS: INVALID gate=SHA detail=preregistro-ausente({path})"
        )
    digest = compute_file_sha256(path)
    if digest.lower() != expected_sha.lower():
        raise SystemExit(
            "W4 STATUS: INVALID gate=SHA detail=preregistro-mismatch"
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_signal_fill_clock(clock: str) -> tuple[int, int, int]:
    """Parsea el pin 'signal 09:25 ET / fill 09:30 ET / 300 s' a
    (signal_min, fill_min, bar_seconds). Sin parseo => ValueError (INVALID)."""
    m = re.search(
        r"signal\s+(\d{1,2}):(\d{2}).*?fill\s+(\d{1,2}):(\d{2}).*?(\d+)\s*s",
        clock,
    )
    if not m:
        raise ValueError(f"reloj-ilegible:{clock!r}")
    sh, sm, fh, fm, sec = (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           int(m.group(4)), int(m.group(5)))
    return sh * 60 + sm, fh * 60 + fm, sec


def extract_pins(prereg: dict[str, Any]) -> dict[str, Any]:
    """Lee los pins del preregistro VERIFICADO. Clave ausente => INVALID.

    S1: los pins se CONSUMEN en la logica (target_R, quantity, reloj
    signal/fill, session_slots); si un pin falta o no coincide con lo esperado
    => INVALID fail-closed.
    """
    try:
        pins = prereg["pins_summary"]
        seeds = prereg["seeds"]
        cal = prereg["calendar_and_microstructure"]
        ticks = cal["ticks"]
        out = {
            "target_R": float(pins["target_R"]),
            "quantity": int(pins["quantity"]),
            "headline_R": str(pins["headline_R"]),
            "signal_fill_clock": str(pins["signal_fill_clock"]),
            "session_slots": int(pins["session_slots"]),
            "n_min": int(pins["n_min"]),
            "cbb_seed": int(seeds["cbb_seed"]),
            "control_seed": int(seeds["control_seed"]),
            "ticks": {m: float(ticks[m]) for m in ALLOWED_MARKETS},
        }
        out["signal_min"], out["fill_min"], out["bar_seconds"] = parse_signal_fill_clock(
            out["signal_fill_clock"]
        )
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise SystemExit(
            f"W4 STATUS: INVALID gate=PINS detail=pin-ausente-o-ilegible({exc})"
        )
    # Chequeo de coherencia fail-closed con lo que el codigo implementa.
    if not (
        out["target_R"] == 1.5
        and out["quantity"] == 1
        and out["session_slots"] == 78
        and out["n_min"] == 100
        and out["signal_min"] == SIGNAL_MIN
        and out["fill_min"] == FILL_MIN
        and out["bar_seconds"] == BAR_SECONDS
    ):
        raise SystemExit(
            "W4 STATUS: INVALID gate=PINS detail=pin-fuera-de-rango-implementado"
        )
    for m in ALLOWED_MARKETS:
        if out["ticks"][m] != MARKET_SPECS[m].tick_size:
            raise SystemExit(
                f"W4 STATUS: INVALID gate=PINS detail=tick-no-canonico({m})"
            )
    return out


def emit_invalid(gate: str, detail: str) -> NoReturn:
    raise SystemExit(f"W4 STATUS: INVALID gate={gate} detail={detail}")


def validate_markets(raw: str) -> list[str]:
    """Valida --markets. MGC (excluido por pin) y desconocidos => ValueError.

    El llamante (main) lo convierte en INVALID gate=MGC / gate=MARKET.
    """
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not syms:
        raise ValueError("mercados-vacios")
    for s in syms:
        if s == "MGC":
            raise ValueError(
                "MGC-excluido-por-pin(sesion-0820-1330-incompatible-con-salida-1600)"
            )
        if s not in ALLOWED_MARKETS:
            raise ValueError(f"mercado-desconocido:{s}")
    return list(dict.fromkeys(syms))


def parse_roll_dates(spec: str, allowed: list[str]) -> dict[str, set[date]]:
    """Parsea --roll-dates 'MNQ=YYYY-MM-DD,MES=YYYY-MM-DD'. Sin entradas en
    fecha de roll (miercoles excluido; roll en lunes/martes solo se cuenta en
    diag). Vacio = sin rolls (limitacion declarada en INFORME). Fecha sin
    mercado aplica a todos.
    """
    out: dict[str, set[date]] = {m: set() for m in allowed}
    if not spec.strip():
        return out
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            m, d = item.split("=", 1)
            m = m.strip().upper()
            d = d.strip()
            if m == "MGC" or (m not in ALLOWED_MARKETS and m not in MARKET_SPECS):
                raise ValueError(f"mercado-desconocido-en-roll-dates:{m}")
            out.setdefault(m, set()).add(date.fromisoformat(d))
        else:
            dd = date.fromisoformat(item)
            for m in allowed:
                out[m].add(dd)
    return out


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
    """Parsea el miembro canonico con rechazo de timestamps duplicados.

    Rango: todo lo disponible del canonico 2019-2025 (sin corte inferior;
    el plan C1 y los folds disciplinan los tramos).
    """
    bars: list[Bar] = []
    seen_ts: set[datetime] = set()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    for row in reader:
        ts = row["timestamp"].replace("Z", "+00:00")
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
# Utilidades ET / calendario / sesiones RTH
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


def find_first_at_or_after(day_idx: list[int], bars: list[Bar], h: int, m: int) -> int | None:
    target = h * 60 + m
    for i in day_idx:
        if et_minutes(bars[i].timestamp) >= target:
            return i
    return None


def session_summary(
    day_idx: list[int], bars: list[Bar], n_slots: int = 78
) -> dict[str, Any] | None:
    """Resumen RTH de una sesion de referencia (lunes/martes).

    Exige timestamps alineados a la rejilla de 300 s en minutos exactos
    (:00, :05, ...) con segundos y microsegundos a cero, y ``n_slots`` slots
    UNICOS sin sobrescritura silenciosa (duplicado o desplazamiento, p. ej.
    30 s, => sesion incompleta => None). high/low sobre los slots; close =
    close de la barra 15:55. Grilla incompleta o distinta -> None (el llamante
    cuenta NO_SIGNAL fail-closed). Transporta ``basis`` para contraste con el
    canonico W2 (RTH_SESSION_BASIS).
    """
    expected = frozenset(RTH_OPEN_MIN + 5 * k for k in range(n_slots))
    slot: dict[int, int] = {}
    for i in day_idx:
        e = et_of(bars[i].timestamp)
        m = e.hour * 60 + e.minute
        if not (RTH_OPEN_MIN <= m < RTH_CLOSE_MIN):
            continue
        if e.second != 0 or e.microsecond != 0:
            return None
        if m in slot:
            return None
        slot[m] = i
    if set(slot.keys()) != expected:
        return None
    hi = max(bars[i].high for i in slot.values())
    lo = min(bars[i].low for i in slot.values())
    close = bars[slot[LAST_RTH_MIN]].close
    return {"high": hi, "low": lo, "close": close, "n": n_slots,
            "basis": W4_RTH_BASIS}


# ---------------------------------------------------------------------------
# Regla D1 (candidata B) — funcion pura, testeable
# ---------------------------------------------------------------------------
def decide_D1(
    mon_h: float,
    mon_l: float,
    tue_h: float,
    tue_l: float,
    tue_c: float,
    require_inside_close: bool = True,
) -> tuple[str | None, str]:
    """Devuelve (lado | None, motivo).

    LONG: tue_low < mon_low AND mon_low < tue_close < mon_high.
    SHORT: tue_high > mon_high AND mon_low < tue_close < mon_high.
    Igualdad (==) no cuenta como barrido ni como cierre interior. Doble
    sweep -> NO_SIGNAL. Sin cierre interior (con require_inside_close=ON) ->
    NO_SIGNAL. Sin barrido -> NO_SIGNAL. Rango cero (high == low) en
    cualquiera de las dos sesiones -> NO_SIGNAL explicito (sin R degenerado:
    con igualdad estricta un martes plano jamas puede barrer y cerrar dentro
    a la vez, pero se cuenta aparte para diagnostico).
    """
    if tue_h == tue_l:
        return None, "rango_cero_martes"
    if mon_h == mon_l:
        return None, "rango_cero_lunes"
    sweep_low = tue_l < mon_l
    sweep_high = tue_h > mon_h
    if sweep_low and sweep_high:
        return None, "doble_sweep"
    if not sweep_low and not sweep_high:
        return None, "sin_barrido"
    if require_inside_close and not (mon_l < tue_c < mon_h):
        return None, "sin_cierre_interior"
    if sweep_low and not sweep_high:
        return "long", "ok"
    if sweep_high and not sweep_low:
        return "short", "ok"
    return None, "ambigua"


# ---------------------------------------------------------------------------
# Senales semanales W4
# ---------------------------------------------------------------------------
def atr_wilder_14(bars: list[Bar], signal_idx: int, period: int = 14) -> float | None:
    """ATR Wilder de ``period`` sobre M5 calculado SOLO con barras anteriores
    a la senal (telemetria del preregistro, nunca filtro). Semilla Wilder =
    media simple de los primeros ``period`` TR; luego recurrencia:
    atr_t = (atr_{t-1} * (period - 1) + TR_t) / period.
    Sin ``period + 1`` barras previas => None declarado (sin inventar historia).
    """
    if signal_idx < period + 1:
        return None
    prev_close = bars[0].close
    tr_sum = 0.0
    for i in range(1, period + 1):
        b = bars[i]
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        tr_sum += tr
        prev_close = b.close
    atr = tr_sum / period
    for i in range(period + 1, signal_idx):
        b = bars[i]
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        atr = (atr * (period - 1) + tr) / period
        prev_close = b.close
    return float(atr)


def precompute_wilder_atr(bars: list[Bar], period: int = 14) -> list[float | None]:
    """Precalcula ATR Wilder para todas las barras en una sola pasada O(N).

    out[k] es el ATR Wilder calculado con las barras estrictamente anteriores
    a la barra k (es decir, bars[:k]).
    """
    n = len(bars)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    prev_close = bars[0].close
    tr_sum = 0.0
    for i in range(1, period + 1):
        b = bars[i]
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        tr_sum += tr
        prev_close = b.close
    atr = tr_sum / period
    for i in range(period + 1, n):
        out[i] = atr
        b = bars[i]
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        atr = (atr * (period - 1) + tr) / period
        prev_close = b.close
    return out


@dataclass(frozen=True)
class SignalEvent:
    week_key: tuple[int, int]  # (iso_year, iso_week); max 1 entrada/semana
    wed: date
    mon: date
    tue: date
    side: str  # 'long' | 'short'
    signal_idx: int  # barra 09:25 ET (su close = entry estimado)
    fill_idx: int  # barra 09:30 ET (fill a su open)
    mon_h: float
    mon_l: float
    tue_h: float
    tue_l: float
    tue_c: float
    entry_est: float
    stop_level: float
    target_level: float
    risk_nominal_pts: float
    basis: str = W4_RTH_BASIS
    extra: str = ""
    atr14: float | None = None  # telemetria ATR Wilder 14 pre-senal (W4)


def signal_tuple(ev: SignalEvent) -> tuple[tuple[int, int], str, float, float]:
    """Tupla de senal a nivel de compute_signal_W4 para G1 (C4): identica por
    construccion entre escenarios de coste; divergencia = bug real."""
    return (ev.week_key, ev.side, round(ev.stop_level, 6),
            round(ev.target_level, 6))


def compute_signal_W4(
    wed: date,
    groups: dict[date, list[int]],
    bars: list[Bar],
    tick: float,
    diag: dict[str, int] | None,
    emitted: set[tuple[int, int]],
    require_inside_close: bool = True,
    pins: dict[str, Any] | None = None,
    atr_series: list[float | None] | None = None,
) -> SignalEvent | None:
    """Senal semanal candidata B para un miercoles dado (fecha exacta).

    Lunes/martes = sesiones RTH completas (78 slots, basis identico y
    contrastado con el canonico W2) en las fechas exactas wed-2d / wed-1d de
    la misma semana; si falta una o esta rota -> NO_SIGNAL fail-closed
    (festivos: sin parches de sustitucion). Reloj: senal en barra 09:25 ET,
    fill en open de la barra 09:30 ET (contiguas, 300 s); sin esas barras ->
    NO_SIGNAL fail-closed (sin fill inventado). Stop = extremo del barrido
    del martes (margen 0), redondeo a tick con polaridad estricta
    stop<entry<target (long); colapso -> NO_SIGNAL. Target = target_R absoluto
    desde entry estimado (Alt B, pin consumido). Dedup (iso_year, iso_week):
    maximo 1 entrada/semana (interanual incluido). Con ``pins`` (ruta del
    runner) el target_R, el reloj signal/fill y los slots se consumen del
    preregistro verificado (S1); sin pins rigen las constantes del modulo.
    """

    def bump(k: str) -> None:
        if diag is not None:
            diag[k] = diag.get(k, 0) + 1

    target_R = float(pins["target_R"]) if pins is not None else 1.5
    sig_min = int(pins["signal_min"]) if pins is not None else SIGNAL_MIN
    fill_min = int(pins["fill_min"]) if pins is not None else FILL_MIN
    bar_sec = int(pins["bar_seconds"]) if pins is not None else BAR_SECONDS
    n_slots = int(pins["session_slots"]) if pins is not None else 78

    if wed.weekday() != 2:
        bump("semana_miercoles_no_miercoles")
        return None
    mon = wed - timedelta(days=2)
    tue = wed - timedelta(days=1)
    if mon.weekday() != 0 or tue.weekday() != 1:
        bump("semana_fechas_no_exactas")
        return None
    week_key = (wed.isocalendar()[0], wed.isocalendar()[1])
    if week_key in emitted:
        bump("dedup_iso_duplicada")
        return None
    mon_idx = groups.get(mon)
    if mon_idx is None:
        bump("falta_lunes")
        return None
    tue_idx = groups.get(tue)
    if tue_idx is None:
        bump("falta_martes")
        return None
    mon_s = session_summary(mon_idx, bars, n_slots)
    if mon_s is None:
        bump("lunes_incompleto")
        return None
    tue_s = session_summary(tue_idx, bars, n_slots)
    if tue_s is None:
        bump("martes_incompleto")
        return None
    if mon_s["basis"] != W4_RTH_BASIS or tue_s["basis"] != W4_RTH_BASIS:
        bump("basis_no_canonico")
        return None
    side, reason = decide_D1(
        mon_s["high"], mon_s["low"],
        tue_s["high"], tue_s["low"], tue_s["close"],
        require_inside_close,
    )
    if side is None:
        bump(f"no_signal_{reason}")
        return None

    wed_idx = groups.get(wed)
    if wed_idx is None:
        bump("miercoles_sin_datos")
        return None
    min_to_global: dict[int, int] = {}
    for i in wed_idx:
        min_to_global.setdefault(et_minutes(bars[i].timestamp), i)
    sgi = min_to_global.get(sig_min)
    fii = min_to_global.get(fill_min)
    if sgi is None:
        bump("reloj_sin_barra_0925")
        return None
    if fii is None:
        bump("reloj_sin_barra_0930")
        return None
    if fii != sgi + 1 or int((bars[fii].timestamp - bars[sgi].timestamp).total_seconds()) != bar_sec:
        bump("reloj_fill_no_contiguo")
        return None

    entry_est = bars[sgi].close
    stop_raw = tue_s["low"] if side == "long" else tue_s["high"]
    risk_raw = abs(entry_est - stop_raw)
    target_raw = (entry_est + target_R * risk_raw if side == "long"
                  else entry_est - target_R * risk_raw)
    stop = round_tick(stop_raw, tick)
    target = round_tick(target_raw, tick)
    if side == "long":
        ok = stop < entry_est < target
    else:
        ok = stop > entry_est > target
    if not ok:
        bump("colapso_tick")
        return None
    risk_nominal = abs(entry_est - stop)
    if not math.isfinite(risk_nominal) or risk_nominal <= 0:
        bump("colapso_tick")
        return None
    emitted.add(week_key)
    bump(f"senal_{side}")
    return SignalEvent(
        week_key=week_key, wed=wed, mon=mon, tue=tue, side=side,
        signal_idx=sgi, fill_idx=fii,
        mon_h=mon_s["high"], mon_l=mon_s["low"],
        tue_h=tue_s["high"], tue_l=tue_s["low"], tue_c=tue_s["close"],
        entry_est=entry_est, stop_level=stop, target_level=target,
        risk_nominal_pts=risk_nominal,
        extra="inside_close=ON" if require_inside_close else "inside_close=OFF",
        atr14=atr_series[sgi] if (atr_series is not None and sgi < len(atr_series)) else atr_wilder_14(bars, sgi),
    )


def flip_event(ev: SignalEvent, tick: float, diag: dict[str, int] | None = None,
               target_R: float = 1.5) -> SignalEvent | None:
    """Ablacion primaria B_flip: misma D1, lado opuesto al emitido.

    Riesgo nominal espejado alrededor del entry estimado (misma |R| por
    construccion), target target_R (pin) al lado opuesto. Mismos signal/fill
    idx y timestamps -> apareada trade a trade. Colapso tras redondeo -> None.
    """
    side = "short" if ev.side == "long" else "long"
    risk = abs(ev.entry_est - ev.stop_level)
    stop_raw = ev.entry_est + (ev.entry_est - ev.stop_level)
    target_raw = (ev.entry_est - target_R * risk if side == "short"
                  else ev.entry_est + target_R * risk)
    stop = round_tick(stop_raw, tick)
    target = round_tick(target_raw, tick)
    valid = (stop < ev.entry_est < target) if side == "long" else (stop > ev.entry_est > target)
    if not valid:
        if diag is not None:
            diag["flip_colapso_tick"] = diag.get("flip_colapso_tick", 0) + 1
        return None
    return replace(ev, side=side, stop_level=stop, target_level=target,
                   risk_nominal_pts=abs(ev.entry_est - stop), extra="flip-apareada")


# ---------------------------------------------------------------------------
# Niveles / estrategia de un disparo para el motor canonico
# ---------------------------------------------------------------------------
class W4OneShot:
    """Strategy de un solo disparo: emite la senal precomputada cuando el
    historial termina en la barra de senal (09:25 ET); el motor hace fill al
    siguiente open 09:30 ET (con slip+tick), chequea SL en la vela de fill
    (A1), gap-stop al open y rechazo gap-entry si falta la barra inmediata."""

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
    prioridad) | 'gap_reject_beyond_target' (fill mas alla del target: evita
    TP instantaneo espurio) | 'collapsed' (niveles colapsados tras redondeo).
    """
    entry = round_tick(fill_open + slip if side == "long" else fill_open - slip, tick)
    s = round_tick(stop, tick)
    t = round_tick(target, tick)
    if side == "long":
        if not s < t:
            return ("collapsed", entry, s, t)
        if entry <= s:
            return ("gap_reject", entry, s, t)
        if entry >= t:
            return ("gap_reject_beyond_target", entry, s, t)
        return ("ok", entry, s, t)
    if not s > t:
        return ("collapsed", entry, s, t)
    if entry >= s:
        return ("gap_reject", entry, s, t)
    if entry <= t:
        return ("gap_reject_beyond_target", entry, s, t)
    return ("ok", entry, s, t)


# ---------------------------------------------------------------------------
# Corrida por senal semanal con el motor canonico
# ---------------------------------------------------------------------------
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


def r_efectivo_target(entry_fill: float, stop: float, target: float) -> float | None:
    """R efectivo del target absoluto (W4): |target - entry_fill| /
    |entry_fill - stop| junto a R_nominal. Distinto de la variacion de la
    distancia al stop (drift_rel)."""
    denom = abs(entry_fill - stop)
    if not math.isfinite(denom) or denom <= 0:
        return None
    return abs(target - entry_fill) / denom


def run_signal_day(
    bars: list[Bar],
    ev: SignalEvent,
    market: Any,
    scenario: str,
    exit_idx: int,
    temporal: bool = False,
    quantity: int = 1,
    target_R: float = 1.5,
) -> dict[str, Any]:
    """Corre UNA senal semanal en el motor canonico.

    Salida primaria: max_hold_minutes hasta la primera barra >= 16:00 ET del
    mismo miercoles (salida market a su open, antes de H/L/C de esa barra).
    ``temporal=True``: comparador sin target 1.5R (target lejano inalcanzable;
    conserva el stop estructural; salida temporal unica).
    Cantidad fija (pin ``quantity``). bar_interval=300 (reloj senal
    09:25/fill 09:30). Devuelve trades serializados (con drift R nominal vs
    efectivo + R_efectivo_target + atr14) + contadores + decisiones a nivel de
    senal (C4).

    C1 (regla A1 solo SL en vela de fill, por composicion sin tocar src/):
    el motor corre con target lejano (resuelve con fidelidad fill, SL,
    gap-stop al open y salida temporal; NUNCA cierra por TP) y el target real
    se re-arma manualmente DESDE LA SIGUIENTE VELA al fill: t_target = primer
    k con fill_idx < k <= exit_idx cuyo rango toque el target. Composicion
    exacta con prioridad SL como en el motor: sin t_target, o salida del
    motor en/antes de t_target => trade del motor; salida posterior =>
    take_profit en t_target a precio target (tick, sin slippage, como el
    motor), comision 2 lados.
    """
    tick = market.tick_size
    slip = scenario_slip(scenario, tick)
    comm = scenario_comm(scenario, market.dollar_per_point)
    dpp = market.dollar_per_point

    target_px = round_tick(ev.target_level, tick)
    far = TARGET_FAR_PTS if ev.side == "long" else -TARGET_FAR_PTS
    # Pre-chequeo mirror con el target REAL (M1: evita TP instantaneo espurio
    # por gap mas alla del target; con slip de escenario). El comparador
    # temporal usa el lejano (sin target por diseño).
    precheck_target = far if temporal else ev.target_level
    fill_bar = bars[ev.fill_idx]
    cls, _entry, _s, _t = mirror_valid_levels(
        ev.side, fill_bar.open, slip, tick, ev.stop_level, precheck_target
    )
    if cls != "ok":
        return {"trades": [],
                "gap_reject": 1 if cls == "gap_reject" else 0,
                "gap_beyond_target": 1 if cls == "gap_reject_beyond_target" else 0,
                "collapsed": 1 if cls == "collapsed" else 0,
                "unresolved": 0, "decisions": [signal_tuple(ev)], "precheck": cls}

    # El motor siempre corre con target lejano (A1 para la primaria; semantica
    # declarada para el comparador temporal). El TP real se re-arma debajo.
    motor_target = far
    sig_key = bars[ev.signal_idx].timestamp.isoformat()
    strat = W4OneShot({sig_key: (ev.side, ev.stop_level, motor_target, ev.entry_est)},
                      market)
    hold_min = (bars[exit_idx].timestamp - fill_bar.timestamp).total_seconds() / 60.0
    cfg = BacktestConfig(
        dollar_per_point=dpp, tick_size=tick,
        commission_per_side=comm, slippage_points=slip,
        time_exit_slippage_points=slip, end_of_data_slippage_points=0.0,
        bar_interval_seconds=BAR_SECONDS, max_bars_held=10**9,
        max_hold_minutes=hold_min, time_exit_mode="market",
        end_of_data_policy="unresolved", fixed_quantity=quantity,
    )
    sl = bars[ev.fill_idx: exit_idx + 1]
    cal = bars[: ev.fill_idx]
    res = run_backtest(sl, strat, cfg, calibration_bars=cal)
    dollar_risk = cfg.risk_per_trade * cfg.initial_balance

    composed: list[dict[str, Any]] = []
    if not temporal and res.trades:
        ts_to_idx = {b.timestamp: n for n, b in enumerate(bars)}
        for tr in res.trades:
            exit_k = ts_to_idx.get(tr.exit_time)
            t_target: int | None = None
            for k in range(ev.fill_idx + 1, exit_idx + 1):
                bk = bars[k]
                touch = (target_px <= bk.high if ev.side == "long"
                         else target_px >= bk.low)
                if touch:
                    t_target = k
                    break
            if t_target is None or exit_k is None or exit_k <= t_target:
                composed.append(None)  # marca: conservar trade del motor
            else:
                move = (target_px - tr.entry_price if ev.side == "long"
                        else tr.entry_price - target_px)
                gross = move * dpp * tr.quantity
                commission = comm * 2 * tr.quantity
                net = gross - commission
                stop_dist = abs(tr.entry_price - tr.stop_price)
                stop_risk = stop_dist * dpp * tr.quantity
                composed.append({
                    "exit_time": bars[t_target].timestamp,
                    "exit_price": target_px,
                    "exit_reason": "take_profit",
                    "gross_pnl": gross, "commission": commission,
                    "net_pnl": net,
                    "r_result": net / dollar_risk if dollar_risk > 0 else 0.0,
                    "budgeted_r": net / dollar_risk if dollar_risk > 0 else 0.0,
                    "effective_r": net / stop_risk if stop_risk > 0 else 0.0,
                })
    rows = []
    for n, tr in enumerate(res.trades):
        ov = composed[n] if (composed and composed[n] is not None) else None
        exit_time = ov["exit_time"] if ov else tr.exit_time
        exit_price = ov["exit_price"] if ov else tr.exit_price
        exit_reason = ov["exit_reason"] if ov else tr.exit_reason
        gross_pnl = ov["gross_pnl"] if ov else tr.gross_pnl
        commission_v = ov["commission"] if ov else tr.commission
        net_pnl = ov["net_pnl"] if ov else tr.net_pnl
        r_result = ov["r_result"] if ov else tr.r_result
        budgeted_r = ov["budgeted_r"] if ov else tr.budgeted_r
        effective_r = ov["effective_r"] if ov else tr.effective_r
        risk_eff = abs(tr.entry_price - tr.stop_price)
        drift = ((risk_eff - ev.risk_nominal_pts) / ev.risk_nominal_pts
                 if ev.risk_nominal_pts > 0 else 0.0)
        risk_dollars = float(abs(tr.entry_price - tr.stop_price) * dpp * tr.quantity)
        r_eff_t = r_efectivo_target(tr.entry_price, tr.stop_price, target_px)
        rows.append({
            "trade_id": tr.trade_id,
            "week": f"{ev.week_key[0]}-W{ev.week_key[1]:02d}",
            "day": ev.wed.isoformat(),
            "direction": tr.direction,
            "signal_time": bars[ev.signal_idx].timestamp.isoformat(),
            "entry_time": tr.entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "entry_price": tr.entry_price, "exit_price": exit_price,
            "stop_price": tr.stop_price, "target_price": target_px,
            "quantity": tr.quantity, "gross_pnl": round(gross_pnl, 4),
            "commission": round(commission_v, 4),
            "net_pnl": round(net_pnl, 4),
            "r_result": round(r_result, 6),
            "budget_r": round(budgeted_r, 6),
            "stop_r": round(effective_r, 6),
            "risk_dollars": round(risk_dollars, 2),
            "practice_eligible_le_200": bool(risk_dollars <= 200.0),
            "r_nominal": target_R,
            "R_efectivo_target": round(r_eff_t, 6) if r_eff_t is not None else None,
            "atr14": round(ev.atr14, 6) if ev.atr14 is not None else None,
            "risk_nominal_pts": round(ev.risk_nominal_pts, 6),
            "risk_efectivo_pts": round(risk_eff, 6),
            "drift_rel": round(drift, 6),
            "exit_reason": exit_reason,
            "temporal": bool(temporal),
        })
    return {"trades": rows, "gap_reject": res.gap_rejections,
            "gap_beyond_target": 0, "collapsed": 0,
            "unresolved": res.unresolved_positions,
            "decisions": [signal_tuple(ev)], "precheck": "ok"}


def trades_sha(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Estadistica (CBB causal + control pareado + resumen stop-R)
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


def drift_stats(vals: list[float]) -> dict[str, Any]:
    """Distribucion del drift (R nominal vs efectivo) por trade. Sin claims
    escalares: se reporta la distribucion completa."""
    if not vals:
        return {"n": 0, "mean": 0.0, "p5": None, "p95": None,
                "min": None, "max": None}
    a = np.array(vals, dtype=float)
    return {
        "n": len(vals),
        "mean": round(float(np.mean(a)), 6),
        "p5": round(float(np.percentile(a, 5)), 6),
        "p95": round(float(np.percentile(a, 95)), 6),
        "min": round(float(np.min(a)), 6),
        "max": round(float(np.max(a)), 6),
    }


def summarize_w4(rows: list[dict[str, Any]], cbb_seed: int, min_trades: int = 100) -> dict[str, Any]:
    """Resumen headline stop-R (primario) + budget-R (secundario).

    IC95 solo si n >= min_trades; por debajo -> ABSTENCION (CI None).
    """
    s = [row["stop_r"] for row in rows]
    b = [row["budget_r"] for row in rows]
    pnl = [row["net_pnl"] for row in rows]
    n = len(rows)
    slo, shi = cbb_ci(s, cbb_seed) if n >= min_trades else (None, None)
    blo, bhi = cbb_ci(b, cbb_seed) if n >= min_trades else (None, None)
    wins = sum(1 for v in pnl if v > 0)
    gross_w = sum(v for v in pnl if v > 0)
    gross_l = sum(-v for v in pnl if v < 0)
    pf = (gross_w / gross_l) if gross_l > 0 else (999.0 if gross_w > 0 else 0.0)
    if s:
        eq = np.concatenate(([0.0], np.cumsum(np.array(s, dtype=float))))
        peak = np.maximum.accumulate(eq)
        dd = float(np.max(peak - eq))
    else:
        dd = 0.0
    over = sum(1 for row in rows if row["risk_dollars"] > 200.0)
    r_eff_t = [row["R_efectivo_target"] for row in rows
               if row.get("R_efectivo_target") is not None]
    return {
        "n_trades": n,
        "mean_stop_r": round(float(np.mean(s)), 4) if s else 0.0,
        "cbb_ci95_mean_stop_r": [round(slo, 4), round(shi, 4)] if slo is not None else None,
        "mean_budget_r": round(float(np.mean(b)), 4) if b else 0.0,
        "cbb_ci95_mean_budget_r": [round(blo, 4), round(bhi, 4)] if blo is not None else None,
        "net_pnl": round(float(np.sum(pnl)), 2) if pnl else 0.0,
        "win_rate": round(wins / n, 4) if n else 0.0,
        "profit_factor": round(min(pf, 999.0), 4),
        "max_drawdown_stop_r": round(dd, 4),
        "drift_rel": drift_stats([row["drift_rel"] for row in rows]),
        "R_efectivo_target": drift_stats(r_eff_t),
        "practice_telemetry": {
            "n_over_200": over, "n_total": n,
            "frac_eligible": round((n - over) / n, 4) if n else 1.0,
            "note": "telemetria-contable-solo-telemetria-nunca-filtro",
        },
        "trades_sha256": trades_sha(rows),
    }


# ---------------------------------------------------------------------------
# Gates G1-G5 (fail-closed) + procesamiento por mercado
# ---------------------------------------------------------------------------
def check_gates_ok(gates: dict[str, Any], smoke: bool) -> None:
    """G1-G4 fail-closed (+ G5 fail-fast solo series explicitas del preregistro).
    FAIL en smoke sigue siendo FAIL (sin excepcion)."""
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
    """G1: paridad de senal A NIVEL DE SENAL (C4).

    Compara las tuplas (week_key, side, stop, target) que emite
    ``compute_signal_W4`` (identicas por construccion entre escenarios de
    coste; los costes solo gobiernan fill/salida). Divergencia = bug real.
    Las diferencias post-prechequeo (gap/collapsed/unresolved) NO son
    divergencia: se concilian por G2 como drops.
    """
    g1_div = 0
    g1_common = 0
    g1_empty = False
    for arm, bundle in arms.items():
        dev = bundle.get("dev") if isinstance(bundle, dict) else None
        if dev is None:
            continue
        a = set(dev.get("decisions_primary", []))
        b = set(dev.get("decisions_sens", []))
        signals = bundle.get("signals_dev", 0)
        if signals > 0 and len(a) == 0:
            g1_empty = True
        g1_common += len(a & b)
        g1_div += len(b - a) + len(a - b)
    return {"common": g1_common, "divergent": g1_div,
            "empty_decisions": g1_empty, "PASS": (g1_div == 0) and not g1_empty,
            "cobertura": ("paridad-a-nivel-de-senal (week_key,side,stop,target) "
                          "realista-vs-sensibilidad por brazo; drops post-prechequeo "
                          "excluidos (los concilia G2)")}


def check_flip_paired(trades_on: list[dict[str, Any]],
                      trades_flip: list[dict[str, Any]]) -> tuple[bool, str]:
    """Invariante B_flip apareada (C3): mismo N y mismos timestamps de
    entrada. Devuelve (ok, detalle)."""
    e_on = sorted(t["entry_time"] for t in trades_on)
    e_flip = sorted(t["entry_time"] for t in trades_flip)
    if len(e_on) != len(e_flip):
        return False, f"N_on={len(e_on)}-vs-N_flip={len(e_flip)}"
    if e_on != e_flip:
        return False, "timestamps-de-entrada-divergen"
    return True, f"apareada-N={len(e_on)}"


def check_arm_mapping(trades: list[dict[str, Any]], sigmap: dict[date, SignalEvent],
                      scope_days: list[date], bars: list[Bar],
                      n_expected: int, drops: int = 0) -> tuple[int, bool]:
    """Reconciliacion de un brazo (G2): cada trade mapea a su senal (mismo
    fill-timestamp y lado) y outputs (trades + drops gap/collapsed/unresolved)
    == senales esperadas. Devuelve (no_mapeados, reconciliado)."""
    sigfill = {bars[ev.fill_idx].timestamp.isoformat(): ev.side
               for d, ev in sigmap.items() if d in scope_days and d in sigmap}
    unmapped = sum(1 for tr in trades
                   if sigfill.get(tr["entry_time"]) != tr["direction"])
    reconciled = (unmapped == 0) and (len(trades) + drops == n_expected)
    return unmapped, reconciled


def evaluate_g2(
    arms: dict[str, Any],
    sig_maps: dict[str, dict[date, SignalEvent]],
    bars: list[Bar],
    dev_days: list[date],
    sealed_days: list[date],
    with_tail: bool,
) -> dict[str, Any]:
    """G2: mapeo trade->senal y reconciliacion por brazo x segmento x escenario.

    Concilia por brazo x segmento (dev/tail) x escenario (realista/sensibilidad):
    mapeo trade->senal y reconciliacion:
    trades + gap + collapsed + unresolved == senales elegibles.
    """
    unmapped = 0
    reconciled = True
    parts: dict[str, Any] = {}
    scopes = [("dev", dev_days, "dev")]
    if with_tail:
        scopes.append(("tail", sealed_days, "tail"))
    for arm in ("B_on", "B_flip", "temporal", "OFF"):
        sm = sig_maps.get(arm, {})
        for scope, sdays, key in scopes:
            bundle = arms.get(arm, {}).get(key)
            if bundle is None:
                parts[f"{arm}/{scope}"] = "sellado-sin-correr-no-cubierto"
                continue
            scope_days = [d for d in sdays if d in sm]
            for sc in ("realista", "sensibilidad"):
                if sc == "realista":
                    trades = bundle.get("trades", [])
                    drops = (bundle.get("gap_reject", 0)
                             + bundle.get("gap_beyond_target", 0)
                             + bundle.get("collapsed", 0)
                             + bundle.get("unresolved", 0))
                    n_exp = bundle.get("n_eligible", {}).get(
                        "realista", bundle.get("n_expected", len(scope_days)))
                else:
                    trades = bundle.get("trades_sens", [])
                    drops = (bundle.get("gap_reject_sens", 0)
                             + bundle.get("gap_beyond_target_sens", 0)
                             + bundle.get("collapsed_sens", 0)
                             + bundle.get("unresolved_sens", 0))
                    n_exp = bundle.get("n_eligible", {}).get(
                        "sensibilidad", bundle.get("n_expected", len(scope_days)))

                u, r = check_arm_mapping(trades, sm, scope_days, bars, n_exp, drops)
                unmapped += u
                reconciled = reconciled and r
                parts[f"{arm}/{scope}/{sc}"] = (
                    f"n={len(trades)}-drops={drops}-esperados={n_exp}-{'ok' if r else 'FALLO'}"
                )
    tail_cov = ("dev+sellado-4-brazos-realista-y-sensibilidad" if with_tail
                else "solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final)")
    return {
        "unmapped_trades": unmapped,
        "reconciled": reconciled,
        "PASS": (unmapped == 0) and reconciled,
        "parts": parts,
        "cobertura": (f"mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles "
                      f"en {tail_cov} por brazo x segmento x escenario; pares no apareados (C3) "
                      f"cuentan como excluidos en diag, no como drops"),
    }


def decide_sealed(dev_n: int, ci_low: float | None, g1: bool, g2: bool,
                  g3: bool, n_min: int) -> tuple[bool, str]:
    """Puerta del sellado (C2): abrir (folds 6-7 + cola) SOLO si en DEV
    n >= n_min Y IC95 CBB del brazo primario con low > 0 Y gates G1-G3 en
    verde. Si no => (False, motivo) y el llamante declara ABSTENCION con
    tail=None."""
    if dev_n < n_min:
        return False, f"dev-n-{dev_n}-lt-{n_min}"
    if ci_low is None or not math.isfinite(ci_low) or ci_low <= 0:
        return False, f"IC95-low-no-positivo({ci_low})"
    if not (g1 and g2 and g3):
        return False, f"gates-no-verdes(G1={g1},G2={g2},G3={g3})"
    return True, "dev-pasa-abrir-sellado"


def write_sealed_lock(lock_path: Path, prereg_sha: str,
                      arms: list[str], markets: list[str]) -> None:
    """Crea el lock de apertura del sellado con creacion exclusiva real (persistente).

    Usa os.open con O_CREAT | O_EXCL | O_WRONLY para asegurar creacion atomica.
    Lanza FileExistsError si el lock ya existe.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(lock_path), flags, 0o644)
    try:
        payload = json.dumps({
            "prereg_sha256": prereg_sha,
            "opened_at_utc": datetime.now(timezone.utc).isoformat(),
            "arms": arms,
            "markets": markets,
        }, indent=2, ensure_ascii=False) + "\n"
        with open(fd, "w", encoding="utf-8", closefd=True) as fh:
            fh.write(payload)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def fold_of_day(day: date, folds: Any) -> int | None:
    for f in folds:
        s = date.fromisoformat(f.test_start)
        e = date.fromisoformat(f.test_end)
        if s <= day < e:
            return f.fold_id
    return None


def pair_precheck_ok(ev: SignalEvent, bars: list[Bar], scenario: str,
                     tick: float) -> bool:
    """Pre-chequeo mirror de UN brazo con el slip del escenario (C3): la
    elegibilidad del par exige que AMBOS brazos pasen."""
    slip = scenario_slip(scenario, tick)
    cls, _e, _s, _t = mirror_valid_levels(
        ev.side, bars[ev.fill_idx].open, slip, tick,
        ev.stop_level, ev.target_level)
    return cls == "ok"


def pair_eligible_days(days: list[date], sig_on: dict[date, SignalEvent],
                       sig_flip: dict[date, SignalEvent], bars: list[Bar],
                       scenario: str, tick: float,
                       diag: dict[str, int] | None, tag: str) -> list[date]:
    """Conjunto de dias donde AMBOS brazos pasan el prechequeo (C3). Los
    pares no apareados quedan fuera completos (nunca 1 vs 0)."""
    out: list[date] = []
    for d in sorted(days):
        ev = sig_on.get(d)
        fl = sig_flip.get(d)
        if ev is None or fl is None:
            continue
        if pair_precheck_ok(ev, bars, scenario, tick) and pair_precheck_ok(
                fl, bars, scenario, tick):
            out.append(d)
        elif diag is not None:
            diag[f"{tag}_par_excluido_{scenario}"] = (
                diag.get(f"{tag}_par_excluido_{scenario}", 0) + 1)
    return out


def process_market(
    symbol: str,
    bars: list[Bar],
    pins: dict[str, Any],
    plan: WalkForwardPlan | None,
    roll_dates: set[date],
    smoke: bool,
    enforce_g5: bool = True,
    allow_sealed: bool | None = None,
    allow_sealed_reason: str = "",
    sealed_already_open: bool = False,
    lock_path: Path | None = None,
    prereg_sha: str = "",
    honor_preexisting_lock: bool = True,
) -> dict[str, Any]:
    """Pipeline completo de un mercado. Devuelve el contenido de metrics.

    B_on (primaria H_B) + B_flip (ablacion apareada trade a trade sobre el
    conjunto de dias elegibles por AMBOS brazos, C3) + comparador temporal
    puro (sub-analisis: sin target 1.5R; conserva stop estructural; salida
    temporal unica) + require_inside_close=OFF (sub-analisis). G5 fail-fast
    SOLO sobre stop_R_dev_<MKT> y stop_R_tail_<MKT>; con enforce_g5=False
    (exploratorios MES/MYM) se calcula pero NO aborta.
    El sellado (folds 6..7 + cola) se abre SOLO si dev pasa en el mercado
    confirmatorio MNQ (n >= n_min, IC95 CBB de B_on con low > 0, G1-G3 verdes);
    si no => ABSTENCION con tail=None (C2). ``allow_sealed`` (veredicto MNQ desde main)
    gobierna la apertura; sin MNQ en la corrida => allow_sealed=False para todos.
    ``sealed_already_open`` (lock preexistente) => tail sin correr,
    fail-closed. Con ``lock_path`` se crea el lock al abrir con creacion exclusiva.
    ``honor_preexisting_lock=False`` (ruta multi-mercado de ``main``): el
    lock creado por un mercado ANTERIOR en la MISMA corrida no bloquea a
    los siguientes; el fail-closed entre corridas lo asegura ``main`` al
    calcular ``sealed_already_open`` antes del primer mercado.
    """
    if W4_RTH_BASIS != RTH_SESSION_BASIS:
        emit_invalid("BASIS", "W4_RTH_BASIS-no-contrastado-con-canonico-W2")
    market = MARKET_SPECS[symbol]
    tick = market.tick_size
    cbb_seed = pins["cbb_seed"]
    control_seed = pins["control_seed"]
    n_min = pins["n_min"]
    target_R = float(pins["target_R"])
    quantity = int(pins["quantity"])

    groups = group_by_et_date(bars)
    sessions = sorted(groups.keys())
    diag: dict[str, int] = {}
    folds = plan.folds if plan is not None else []
    if plan is not None and plan.n_folds != 8 and not smoke:
        emit_invalid("G4", f"plan-C1-debe-tener-8-folds(hay-{plan.n_folds})")

    if (honor_preexisting_lock and lock_path is not None
            and lock_path.exists()):
        sealed_already_open = True

    tail_start: date | None = None
    if not smoke:
        max_day = sessions[-1]
        y, m = max_day.year, max_day.month - 5
        while m <= 0:
            m += 12
            y -= 1
        tail_start = date(y, m, 1)

    # Tramos sellados: dev = folds 0..5; sellado = folds 6..7 + cola 6 meses.
    in_scope: dict[date, str] = {}
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

    # --- senales semanales (causales por barra; el fold solo disciplina el reporte) ---
    sigOn: dict[date, SignalEvent] = {}
    sigFlip: dict[date, SignalEvent] = {}
    sigOff: dict[date, SignalEvent] = {}
    emitted_on: set[tuple[int, int]] = set()
    emitted_off: set[tuple[int, int]] = set()
    atr_series = precompute_wilder_atr(bars, period=14)
    for wed in sessions:
        if wed.weekday() != 2:
            continue
        mon = wed - timedelta(days=2)
        tue = wed - timedelta(days=1)
        # W3 (literal al pin): el pin excluye ENTRADAS en fecha de roll (el
        # miercoles). Roll en lunes/martes se cuenta (diag, visible y
        # cuantificado) pero NO filtra.
        if wed in roll_dates:
            diag["semanas_roll_excluidas"] = diag.get("semanas_roll_excluidas", 0) + 1
            continue
        if mon in roll_dates or tue in roll_dates:
            diag["semana_con_roll_en_lunes_o_martes"] = (
                diag.get("semana_con_roll_en_lunes_o_martes", 0) + 1)
        ev = compute_signal_W4(wed, groups, bars, tick, diag, emitted_on, True,
                               pins=pins, atr_series=atr_series)
        if ev is not None:
            sigOn[wed] = ev
            fl = flip_event(ev, tick, diag, target_R)
            if fl is not None:
                sigFlip[wed] = fl
        off = compute_signal_W4(wed, groups, bars, tick, diag, emitted_off, False,
                                pins=pins, atr_series=atr_series)
        if off is not None:
            sigOff[wed] = off

    # --- corridas de motor por senal (exit 16:00 fijado y pre-chequeado ANTES) ---
    def run_bucket(days: list[date], sigmap: dict[date, SignalEvent],
                   tag: str, temporal: bool = False,
                   pair_maps: dict[str, set[date]] | None = None,
                   count_diag: bool = True) -> dict[str, Any]:
        """Corre un brazo sobre ``days`` en ambos escenarios.

        Decisiones a nivel de senal (C4): se registran para TODOS los dias de
        entrada en ambos escenarios (identicas por construccion). Con
        ``pair_maps`` (C3, brazos apareados) el motor corre SOLO sobre los
        dias elegibles por AMBOS brazos en cada escenario; los pares
        excluidos se cuentan en diag y G2 concilia contra los elegibles.
        """
        agg: dict[str, Any] = {"trades": [], "trades_sens": [],
                               "gap_reject": 0, "gap_beyond_target": 0,
                               "collapsed": 0, "unresolved": 0,
                               "gap_reject_sens": 0, "gap_beyond_target_sens": 0,
                               "collapsed_sens": 0, "unresolved_sens": 0,
                               "decisions_primary": [], "decisions_sens": [],
                               "n_attempted": 0, "n_eligible": {}}
        def bumpd(k: str) -> None:
            if count_diag:
                diag[k] = diag.get(k, 0) + 1

        per_fold: dict[int, list[dict[str, Any]]] = {}
        present = [d for d in sorted(days) if d in sigmap]
        agg["n_attempted"] = len(present)
        for scenario in ("realista", "sensibilidad"):
            if pair_maps is not None:
                elig = [d for d in present if d in pair_maps[scenario]]
            else:
                elig = list(present)
            agg["n_eligible"][scenario] = len(elig)
            # Decisiones a nivel de senal: TODOS los dias de entrada (C4), con
            # construccion independiente por escenario.
            for d in present:
                (agg["decisions_primary"] if scenario == "realista"
                 else agg["decisions_sens"]).append(signal_tuple(sigmap[d]))
            for d in elig:
                ev = sigmap[d]
                day_idx = groups[d]
                e16 = find_first_at_or_after(day_idx, bars, 16, 0)
                if e16 is None or e16 <= ev.fill_idx:
                    key = "unresolved" if scenario == "realista" else "unresolved_sens"
                    agg[key] += 1
                    if scenario == "realista":
                        bumpd(f"{tag}_sin_barra_1600")
                    continue
                if et_of(bars[e16].timestamp).date() != d:
                    key = "unresolved" if scenario == "realista" else "unresolved_sens"
                    agg[key] += 1
                    if scenario == "realista":
                        bumpd(f"{tag}_salida_fuera_del_dia")
                    continue
                r = run_signal_day(bars, ev, market, scenario, e16, temporal,
                                   quantity, target_R)
                if scenario == "realista":
                    agg["trades"].extend(r["trades"])
                    agg["gap_reject"] += r["gap_reject"]
                    agg["gap_beyond_target"] += r["gap_beyond_target"]
                    agg["collapsed"] += r["collapsed"]
                    agg["unresolved"] += r["unresolved"]
                    fid = fold_of_day(d, folds) if folds else None
                    if fid is not None:
                        per_fold.setdefault(fid, []).extend(r["trades"])
                else:
                    agg["trades_sens"].extend(r["trades"])
                    agg["gap_reject_sens"] += r["gap_reject"]
                    agg["gap_beyond_target_sens"] += r["gap_beyond_target"]
                    agg["collapsed_sens"] += r["collapsed"]
                    agg["unresolved_sens"] += r["unresolved"]
        # G2 concilia la via realista contra los dias que el motor intento.
        agg["n_expected"] = agg["n_eligible"].get("realista", agg["n_attempted"])
        agg["per_fold"] = per_fold
        return agg

    dev_days = [d for d in sessions if in_scope[d] == "dev"]
    sealed_days = [d for d in sessions if in_scope[d] == "sellado"]

    # --- C3: conjunto de dias elegibles donde AMBOS brazos pasan el ---
    # --- prechequeo (por escenario, con su slip); los pares no apareados ---
    # --- quedan fuera completos antes de correr el motor. ---
    pair_dev = {
        sc: set(pair_eligible_days(
            [d for d in dev_days if d in sigOn], sigOn, sigFlip,
            bars, sc, tick, diag, "Bpar_dev"))
        for sc in ("realista", "sensibilidad")
    }

    arms: dict[str, Any] = {}
    arms["B_on"] = {
        "dev": run_bucket([d for d in dev_days if d in sigOn], sigOn, "Bon",
                          False, pair_dev),
        "signals_dev": sum(1 for d in dev_days if d in sigOn),
    }
    arms["B_flip"] = {
        "dev": run_bucket([d for d in dev_days if d in sigFlip], sigFlip, "Bflip",
                          False, pair_dev),
        "signals_dev": sum(1 for d in dev_days if d in sigFlip),
    }
    arms["temporal"] = {
        "dev": run_bucket([d for d in dev_days if d in sigOn], sigOn, "temporal", True),
        "signals_dev": sum(1 for d in dev_days if d in sigOn),
    }
    arms["OFF"] = {
        "dev": run_bucket([d for d in dev_days if d in sigOff], sigOff, "OFF"),
        "signals_dev": sum(1 for d in dev_days if d in sigOff),
    }

    sealed_audit: list[dict[str, Any]] = []
    for arm in ("B_on", "B_flip", "temporal", "OFF"):
        sealed_audit.append({"action": "open_dev", "arm": arm, "days": len(dev_days)})
    dev_finished = True

    # --- G1: paridad de senal a nivel de senal (C4) ---
    g1_eval = evaluate_g1(arms)

    # --- C3: invariante flip apareada en dev (realista y sensibilidad) ---
    for _sc, _tk, _fk in (("realista", "trades", "trades"),
                          ("sensibilidad", "trades_sens", "trades_sens")):
        _ok, _det = check_flip_paired(arms["B_on"]["dev"][_tk],
                                      arms["B_flip"]["dev"][_fk])
        if not _ok:
            emit_invalid("G1", f"flip-no-apareada(dev-{_sc}-{_det})")

    sig_maps = {"B_on": sigOn, "B_flip": sigFlip, "temporal": sigOn, "OFF": sigOff}
    g2dev = evaluate_g2(arms, sig_maps, bars, dev_days, sealed_days, with_tail=False)

    # --- G3: determinismo de generacion de senales + recorridas (W1) ---
    def _evkey(ev: SignalEvent) -> tuple[Any, ...]:
        return (ev.week_key, ev.side, round(ev.stop_level, 6),
                round(ev.target_level, 6), round(ev.entry_est, 6),
                ev.signal_idx, ev.fill_idx,
                round(ev.atr14, 6) if ev.atr14 is not None else None)

    g3_pass = True
    g3_detail = "n/a"
    any_signals = any(arms[a]["signals_dev"] > 0
                      for a in ("B_on", "B_flip", "temporal", "OFF"))
    if any_signals:
        regen_ok = True
        for req, sm in ((True, sigOn), (False, sigOff)):
            emitted2: set[tuple[int, int]] = set()
            for wed in sessions:
                if wed.weekday() != 2:
                    continue
                if wed in roll_dates:
                    continue
                ev2 = compute_signal_W4(wed, groups, bars, tick, {}, emitted2,
                                        req, pins=pins, atr_series=atr_series)
                orig = sm.get(wed)
                if (ev2 is None) != (orig is None):
                    regen_ok = False
                    break
                if ev2 is not None and _evkey(ev2) != _evkey(orig):
                    regen_ok = False
                    break
            if not regen_ok:
                break
        if regen_ok:
            for wed, ev in sigOn.items():
                fl2 = flip_event(ev, tick, None, target_R)
                origf = sigFlip.get(wed)
                if ((fl2 is None) != (origf is None)
                        or (fl2 is not None and _evkey(fl2) != _evkey(origf))):
                    regen_ok = False
                    break
        if not regen_ok:
            g3_pass = False
            g3_detail = "mismatch-regeneracion-senales"
        else:
            g3_detail = "regen-senales-ok"
            for arm, sm, tmp, pmap in (
                    ("B_on", sigOn, False, pair_dev),
                    ("B_flip", sigFlip, False, pair_dev),
                    ("temporal", sigOn, True, None),
                    ("OFF", sigOff, False, None)):
                devb = arms[arm]["dev"]
                re = run_bucket([d for d in dev_days if d in sm], sm,
                                arm + "_g3", tmp, pmap, count_diag=False)
                if (trades_sha(re["trades"]) != trades_sha(devb["trades"])
                        or trades_sha(re["trades_sens"]) != trades_sha(
                            devb["trades_sens"])):
                    g3_pass = False
                    g3_detail = f"mismatch-motor-{arm}"
                    break
                # G3 verifica tambien el mapeo del escenario de sensibilidad
                sigfill_g3 = {bars[ev.fill_idx].timestamp.isoformat(): ev.side
                              for d, ev in sm.items() if d in dev_days}
                u_sens = sum(1 for tr in devb.get("trades_sens", [])
                             if sigfill_g3.get(tr["entry_time"]) != tr["direction"])
                if u_sens > 0:
                    g3_pass = False
                    g3_detail = f"mapeo-invalido-sensibilidad-{arm}"
                    break
            if g3_pass:
                g3_detail = ("regen-senales+recorrida-motor-realista-y-"
                             "sensibilidad-dev-4-brazos+mapeo-sensibilidad-ok")
    else:
        g3_detail = "sin-senales-nada-que-recorrer"

    # --- C2: puerta del sellado (solo si dev pasa en mercado confirmatorio MNQ) ---
    dev_n = len(arms["B_on"]["dev"]["trades"])
    _s_vals = [t["stop_r"] for t in arms["B_on"]["dev"]["trades"]]
    _ci_low = cbb_ci(_s_vals, cbb_seed)[0] if len(_s_vals) >= n_min else None
    dev_pasa, motivo_dev = decide_sealed(
        dev_n, _ci_low, g1_eval["PASS"], g2dev["PASS"], g3_pass, n_min)

    # El gate del sellado se decide SOLO sobre el dev de MNQ (confirmatorio).
    # Si MNQ no esta en la corrida -> allow_sealed = False para todos.
    if symbol == "MNQ":
        gate_open = dev_pasa if allow_sealed is None else bool(allow_sealed)
    else:
        if allow_sealed is None:
            gate_open = False
            allow_sealed = False
            if not allow_sealed_reason:
                allow_sealed_reason = "sin-MNQ-no-hay-gate-confirmatorio"
        else:
            gate_open = bool(allow_sealed)

    abstain_n = (not smoke) and dev_n < n_min

    sealed_runs = 0
    reopen_detected = False
    order_violated = False
    motivo_sellado = ""
    pair_tail: dict[str, set[date]] = {"realista": set(), "sensibilidad": set()}
    open_tail = (not smoke) and (not sealed_already_open) and gate_open and (not abstain_n)
    if smoke:
        motivo_sellado = "smoke-sin-sellado"
    elif sealed_already_open:
        motivo_sellado = "sellado_ya_abierto-lock-preexistente-tail-sin-correr"
    elif abstain_n:
        motivo_sellado = f"dev-n-{dev_n}-lt-{n_min}-ABSTENCION"
    elif not gate_open:
        if allow_sealed_reason:
            motivo_sellado = allow_sealed_reason
        elif allow_sealed is None:
            motivo_sellado = f"ABSTENCION-sellado-bloqueado-{motivo_dev}"
        else:
            motivo_sellado = "ABSTENCION-sellado-bloqueado-veredicto-MNQ-no-pasa"
    else:
        motivo_sellado = "dev-pasa-abrir-sellado"

    if open_tail:
        if lock_path is not None:
            try:
                write_sealed_lock(lock_path, prereg_sha or PREREG_SHA256,
                                  ["B_on", "B_flip", "temporal", "OFF"], [symbol])
            except FileExistsError:
                if honor_preexisting_lock:
                    sealed_already_open = True
                    open_tail = False
                    motivo_sellado = "sellado_ya_abierto-lock-preexistente-tail-sin-correr"

    if open_tail:
        pair_tail = {
            sc: set(pair_eligible_days(
                [d for d in sealed_days if d in sigOn], sigOn, sigFlip,
                bars, sc, tick, diag, "Bpar_tail"))
            for sc in ("realista", "sensibilidad")
        }
        for arm, sm, tmp, pmap in (("B_on", sigOn, False, pair_tail),
                                   ("B_flip", sigFlip, False, pair_tail),
                                   ("temporal", sigOn, True, None),
                                   ("OFF", sigOff, False, None)):
            if not dev_finished:
                order_violated = True
            if any(a.get("action") == "open_sealed" and a.get("arm") == arm
                   for a in sealed_audit):
                reopen_detected = True
            tb = run_bucket([d for d in sealed_days if d in sm], sm,
                            arm + "sealed", tmp, pmap)
            sealed_runs += 1
            arms[arm]["tail"] = tb
            arms[arm]["signals_tail"] = sum(1 for d in sealed_days if d in sm)
            sealed_audit.append({"action": "open_sealed", "arm": arm,
                                 "days": len(sealed_days)})
    else:
        for arm in ("B_on", "B_flip", "temporal", "OFF"):
            arms[arm]["tail"] = None
            arms[arm]["signals_tail"] = 0
            sealed_audit.append({"action": f"skip_sealed_{motivo_sellado}",
                                 "arm": arm})

    # --- C3: invariante flip apareada en tail (si corrio) ---
    if open_tail:
        for _sc, _tk in (("realista", "trades"), ("sensibilidad", "trades_sens")):
            _ok, _det = check_flip_paired(arms["B_on"]["tail"][_tk],
                                          arms["B_flip"]["tail"][_tk])
            if not _ok:
                emit_invalid("G1", f"flip-no-apareada(tail-{_sc}-{_det})")

    tail_block = {"tail_start": tail_start.isoformat() if tail_start else None,
                  "sealed_runs": sealed_runs, "audit": sealed_audit}

    # --- G2 final: dev + sellado (W1) ---
    g2_final = evaluate_g2(arms, sig_maps, bars, dev_days, sealed_days, with_tail=open_tail)

    # --- G4: verificacion de sellado real ---
    active_arms = 4
    expected_sealed_runs = active_arms if open_tail else 0
    g4_pass = ((sealed_runs == expected_sealed_runs)
               and not reopen_detected and not order_violated)

    # --- G5: fail-fast SOLO sobre stop_R_dev_<MKT> y stop_R_tail_<MKT> ---
    g5_series: list[tuple[str, list[float]]] = [
        (f"stop_R_dev_{symbol}", [t["stop_r"] for t in arms["B_on"]["dev"]["trades"]]),
    ]
    if arms["B_on"].get("tail") is not None:
        g5_series.append(
            (f"stop_R_tail_{symbol}", [t["stop_r"] for t in arms["B_on"]["tail"]["trades"]]))
    g5_status = "PASS"
    g5_detail = "ok"
    for name, s_vals in g5_series:
        if any(not math.isfinite(x) for x in s_vals):
            g5_status = "FAIL"
            g5_detail = f"no-finito-en-{name}"
            break
        if len(s_vals) >= 5:
            lo, hi = cbb_ci(s_vals, cbb_seed)
            if lo is None or hi is None or not math.isfinite(lo) or not math.isfinite(hi):
                g5_status = "FAIL"
                g5_detail = f"cbb-fallo-en-{name}"
                break
    if not enforce_g5 and g5_status == "FAIL":
        g5_status = "EXCLUIDO-del-fail-fast"
        g5_detail = f"exploratorio-excluido-del-fail-fast({g5_detail})"

    gates = {
        "G1_signal_parity": g1_eval,
        "G2_mapping": g2_final,
        "G2_dev": g2dev,
        "G3_determinism": {"PASS": bool(g3_pass), "detail": g3_detail,
                           "cobertura": ("regeneracion-completa-de-senales "
                                         "(compute_signal_W4+flip, dev+sellado) + "
                                         "recorrida-motor-realista-y-sensibilidad-dev-"
                                         "4-brazos; sellado-motor-no-recorrido (ver nota)")},
        "G4_sealed": {"sealed_runs": sealed_runs,
                      "expected_sealed_runs": expected_sealed_runs,
                      "reopen_detected": reopen_detected,
                      "order_violated": order_violated,
                      "audit": sealed_audit,
                      "sellado_motivo": motivo_sellado,
                      "PASS": g4_pass},
        "G5_cbb": {"status": g5_status, "detail": g5_detail,
                   "series": [n for n, _ in g5_series],
                   "note": "fail-fast-solo-sobre-stop_R_dev_y_tail (comparador/exploratorios/sensibilidad/drift excluidos)"},
    }
    if smoke:
        gates["G4_sealed"]["PASS"] = True
        gates["G4_sealed"]["note"] = "smoke-sin-folds"
    check_gates_ok(gates, smoke)

    # --- resumenes: primaria + flip apareado + control + comparadores ---
    out: dict[str, Any] = {
        "market": symbol,
        "market_role": "confirmatorio" if symbol == "MNQ" else "exploratorio",
        "diag": diag,
        "gates": gates,
        "tail": tail_block,
        "signals_dev": {a: arms[a]["signals_dev"] for a in ("B_on", "B_flip", "temporal", "OFF")},
        "signals_tail": {a: arms[a].get("signals_tail", 0) for a in ("B_on", "B_flip", "temporal", "OFF")},
    }
    bdev = arms["B_on"]["dev"]
    summ = summarize_w4(bdev["trades"], cbb_seed, n_min)
    summ_sens = summarize_w4(bdev.get("trades_sens", []), cbb_seed, n_min)
    r_on = [t["stop_r"] for t in bdev["trades"]]
    ctrl_on = paired_control(r_on, control_seed)
    clo, chi = cbb_ci(ctrl_on, cbb_seed) if len(ctrl_on) >= n_min else (None, None)

    # Flip apareado trade a trade (misma D1, lado opuesto, mismos timestamps).
    fdev = arms["B_flip"]["dev"]
    on_by_entry = {t["entry_time"]: t["stop_r"] for t in bdev["trades"]}
    off_by_entry = {t["entry_time"]: t["stop_r"] for t in fdev["trades"]}
    common_flip = sorted(set(on_by_entry) & set(off_by_entry))
    flip_deltas = [float(on_by_entry[e]) - float(off_by_entry[e]) for e in common_flip]
    dlo, dhi = cbb_ci(flip_deltas, cbb_seed) if len(flip_deltas) >= n_min else (None, None)

    if smoke:
        status_on = "SMOKE"
    elif abstain_n or not dev_pasa:
        status_on = "ABSTENCION"
    else:
        status_on = "OK"
    out["sellado"] = {
        "abierto": bool(open_tail),
        "motivo": motivo_sellado,
        "motivo_dev": motivo_dev,
        "dev_pasa": bool(dev_pasa),
        "dev_n": dev_n,
        "dev_ci_low": _ci_low,
        "sellado_ya_abierto": bool(sealed_already_open and not smoke),
    }
    out["B_on"] = {
        "status": status_on,
        "signals_dev": arms["B_on"]["signals_dev"],
        "signals_tail": arms["B_on"].get("signals_tail", 0),
        "realista": summ,
        "sensibilidad": summ_sens,
        "gap_reject": bdev["gap_reject"],
        "gap_reject_beyond_target": bdev["gap_beyond_target"],
        "gap_reject_sens": bdev["gap_reject_sens"],
        "gap_reject_beyond_target_sens": bdev["gap_beyond_target_sens"],
        "collapsed": bdev["collapsed"],
        "collapsed_sens": bdev.get("collapsed_sens", 0),
        "unresolved": bdev["unresolved"],
        "unresolved_sens": bdev["unresolved_sens"],
        "folds": {str(k): summarize_w4(v, cbb_seed, n_min) for k, v in bdev["per_fold"].items()},
        "tail": summarize_w4(arms["B_on"]["tail"]["trades"], cbb_seed, n_min)
        if arms["B_on"].get("tail") else None,
        "flip_apareado": {
            "n_common_trades": len(common_flip),
            "n_on": len(bdev["trades"]), "n_flip": len(fdev["trades"]),
            "mean_on_minus_flip": round(float(np.mean(flip_deltas)), 4) if flip_deltas else 0.0,
            "cbb_ci95": [round(dlo, 4), round(dhi, 4)] if dlo is not None else None,
        },
        "control_pareado": {
            "n": len(ctrl_on),
            "mean": round(float(np.mean(ctrl_on)), 4) if ctrl_on else 0.0,
            "cbb_ci95": [round(clo, 4), round(chi, 4)] if clo is not None else None,
            "seed": control_seed,
        },
    }
    fsumm = summarize_w4(fdev["trades"], cbb_seed, n_min)
    out["B_flip"] = {
        "status": status_on,
        "signals_dev": arms["B_flip"]["signals_dev"],
        "signals_tail": arms["B_flip"].get("signals_tail", 0),
        "realista": fsumm,
        "sensibilidad": summarize_w4(fdev.get("trades_sens", []), cbb_seed, n_min),
        "gap_reject": fdev["gap_reject"],
        "gap_reject_beyond_target": fdev["gap_beyond_target"],
        "gap_reject_sens": fdev.get("gap_reject_sens", 0),
        "gap_reject_beyond_target_sens": fdev.get("gap_beyond_target_sens", 0),
        "collapsed": fdev["collapsed"],
        "collapsed_sens": fdev.get("collapsed_sens", 0),
        "unresolved": fdev["unresolved"],
        "unresolved_sens": fdev.get("unresolved_sens", 0),
        "folds": {str(k): summarize_w4(v, cbb_seed, n_min) for k, v in fdev["per_fold"].items()},
        "tail": summarize_w4(arms["B_flip"]["tail"]["trades"], cbb_seed, n_min)
        if arms["B_flip"].get("tail") else None,
    }
    # Comparador temporal puro (sub-analisis declarado, SIN estatus de test).
    tdev = arms["temporal"]["dev"]
    out["comparador_temporal_puro"] = {
        "status": status_on if status_on in ("SMOKE", "ABSTENCION") else "declarado",
        "note": TEMPORAL_NOTE,
        "signals_dev": arms["temporal"]["signals_dev"],
        "realista": summarize_w4(tdev["trades"], cbb_seed, n_min),
        "sensibilidad": summarize_w4(tdev.get("trades_sens", []), cbb_seed, n_min),
        "unresolved": tdev["unresolved"],
        "tail": summarize_w4(arms["temporal"]["tail"]["trades"], cbb_seed, n_min)
        if arms["temporal"].get("tail") else None,
    }
    # require_inside_close=OFF (sub-analisis declarado, SIN estatus de test).
    odev = arms["OFF"]["dev"]
    on_entry = {t["entry_time"]: t["stop_r"] for t in bdev["trades"]}
    off_entry = {t["entry_time"]: t["stop_r"] for t in odev["trades"]}
    common_off = sorted(set(on_entry) & set(off_entry))
    off_deltas = [float(on_entry[e]) - float(off_entry[e]) for e in common_off]
    olo, ohi = cbb_ci(off_deltas, cbb_seed) if len(off_deltas) >= n_min else (None, None)
    out["inside_close_OFF"] = {
        "status": status_on if status_on in ("SMOKE", "ABSTENCION") else "declarado",
        "note": "barrido-sin-exigir-cierre-interior (sub-analisis, sin estatus de test)",
        "signals_dev": arms["OFF"]["signals_dev"],
        "realista": summarize_w4(odev["trades"], cbb_seed, n_min),
        "unresolved": odev["unresolved"],
        "dias_comunes": {
            "n_common_trades": len(common_off),
            "n_on": len(bdev["trades"]), "n_off": len(odev["trades"]),
            "mean_on_minus_off": round(float(np.mean(off_deltas)), 4) if off_deltas else 0.0,
            "cbb_ci95": [round(olo, 4), round(ohi, 4)] if olo is not None else None,
        },
        "tail": summarize_w4(arms["OFF"]["tail"]["trades"], cbb_seed, n_min)
        if arms["OFF"].get("tail") else None,
    }
    # Filas por trade (drift) para auditoria.
    out["trades_B_on_dev"] = bdev["trades"]
    return out


# ---------------------------------------------------------------------------
# Fixture sintetico determinista para smoke (NO son datos de mercado)
# ---------------------------------------------------------------------------
def _smoke_bar(day: date, hh: int, mm: int, o: float, h: float, lo: float, c: float) -> Bar:
    ts = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).astimezone(timezone.utc)
    return Bar(timestamp=ts, open=o, high=h, low=lo, close=c, volume=100.0)


def build_smoke_bars() -> list[Bar]:
    """Semana sintetica lunes/martes/miercoles en memoria (precios redondos).

    Lunes: rango [100, 110], close 105. Martes: barre el low (99) y cierra
    dentro (104) -> LONG. Miercoles: senal 09:25 (close 103), fill 09:30
    (open 103), stop 99, target 109 (1.5R), deriva alcista que toca target,
    salida 16:00 presente. Una sola senal; G1-G5 en verde; sin datos reales.
    """
    mon = date(2025, 3, 3)
    tue = date(2025, 3, 4)
    wed = date(2025, 3, 5)
    bars: list[Bar] = []

    def slots(h0: int, m0: int, h1: int, m1: int) -> list[tuple[int, int]]:
        out, h, m = [], h0, m0
        while (h, m) <= (h1, m1):
            out.append((h, m))
            m += 5
            if m >= 60:
                m, h = 0, h + 1
        return out

    for m in slots(9, 30, 15, 55):
        bars.append(_smoke_bar(mon, *m, 105.0, 105.5, 104.5, 105.0))
    # Lunes: high 110 (10:00), low 100 (11:00), close 15:55 = 105.
    bars = [b if not (et_of(b.timestamp).date() == mon and et_minutes(b.timestamp) == 600)
            else _smoke_bar(mon, 10, 0, 105.0, 110.0, 104.5, 105.0) for b in bars]
    bars = [b if not (et_of(b.timestamp).date() == mon and et_minutes(b.timestamp) == 660)
            else _smoke_bar(mon, 11, 0, 105.0, 105.5, 100.0, 105.0) for b in bars]

    for m in slots(9, 30, 15, 55):
        bars.append(_smoke_bar(tue, *m, 105.0, 105.5, 104.5, 105.0))
    # Martes: sweep del low a 99 (10:00), high 106 (< 110), close 15:55 = 104.
    bars = [b if not (et_of(b.timestamp).date() == tue and et_minutes(b.timestamp) == 600)
            else _smoke_bar(tue, 10, 0, 105.0, 106.0, 99.0, 105.0) for b in bars]
    bars = [b if not (et_of(b.timestamp).date() == tue and et_minutes(b.timestamp) == 955)
            else _smoke_bar(tue, 15, 55, 104.5, 105.5, 103.5, 104.0) for b in bars]

    # Miercoles 09:25..16:00: senal/fill + deriva alcista hasta el target 109.
    px = 103.0
    for m in slots(9, 25, 16, 0):
        if m == (9, 25):
            bars.append(_smoke_bar(wed, *m, 103.0, 103.5, 102.5, 103.0))
        elif m == (9, 30):
            bars.append(_smoke_bar(wed, *m, 103.0, 103.5, 102.5, 103.2))
        else:
            px = min(px + 0.15, 112.0)
            o = px - 0.15
            bars.append(_smoke_bar(wed, *m, o, px + 0.3, o - 0.1, px))
    bars.sort(key=lambda b: b.timestamp)
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
    L.append(f"# INFORME W4 — candidata B Wednesday Weekly Cycle — status {status}")
    L.append("")
    L.append(f"Preregistro congelado sha256: `{prereg_sha}`")
    if smoke:
        L.append("")
        L.append("> SMOKE TECNICO con fixture sintetico: los numeros NO son resultado. "
                 "Prohibido reportar --smoke como resultado.")
    L.append("")
    L.append("## Parametros (pins del preregistro)")
    L.append("")
    L.append(f"- target 1.5R absoluto desde entry estimado (Alt B); drift R nominal vs efectivo por trade")
    L.append(f"- salida primaria: primera barra >= 16:00 ET del mismo dia (390 min)")
    L.append(f"- reloj: {pins['signal_fill_clock']}; sesiones: {pins['session_slots']} slots [09:30,16:00) ET, basis identico")
    L.append("- costes realista: 0,62 USD/side + 0,25 pt; sensibilidad: 2,0 pts RT exactos (1,0 pt/side comision, slip 0)")
    L.append(f"- seeds: cbb={pins['cbb_seed']}, control={pins['control_seed']}")
    L.append(f"- n_min: {pins['n_min']} trades por mercado (dev < 100 -> ABSTENCION, no se abre el sellado)")
    L.append("- stop: extremo del barrido del martes (margen 0), tick por mercado, polaridad estricta")
    L.append("- sizing: cantidad fija 1 contrato; headline stop-R (primario), budget-R (secundario)")
    L.append("- G5 fail-fast SOLO sobre stop_R_dev_MNQ y stop_R_tail_MNQ")
    L.append("")
    L.append("## Metricas")
    L.append("")
    for sym, m in market_results.items():
        role = m.get("market_role", "confirmatorio" if sym == "MNQ" else "exploratorio")
        if m.get("status") == "exploratorio-no-disponible":
            L.append(f"- {sym} ({role}): exploratorio-no-disponible (sin dataset, no inventado)")
            continue
        for arm in ("B_on", "B_flip"):
            c = m.get(arm, {})
            st = c.get("status", "?")
            r = c.get("realista", {})
            n_t = r.get("n_trades", 0)
            ci_val = r.get("cbb_ci95_mean_stop_r")
            ci_str = "ABSTENCION" if (ci_val is None or n_t < pins["n_min"]) else str(ci_val)
            L.append(f"- {sym} ({role})/{arm}: status={st} signals_dev={c.get('signals_dev', 0)} n={n_t} "
                     f"E[stop-R]={r.get('mean_stop_r', 0):+.4f} CI95={ci_str} "
                     f"E[budget-R]={r.get('mean_budget_r', 0):+.4f} "
                     f"gaps={c.get('gap_reject', 0)} beyond={c.get('gap_reject_beyond_target', 0)} "
                     f"unres={c.get('unresolved', 0)}")
        b = m.get("B_on", {})
        fl = b.get("flip_apareado", {})
        fl_ci = fl.get("cbb_ci95")
        fl_ci_str = "ABSTENCION" if (fl_ci is None or fl.get("n_common_trades", 0) < pins["n_min"]) else str(fl_ci)
        L.append(f"  - flip apareado: n_comun={fl.get('n_common_trades')} "
                 f"E[on-flip]={fl.get('mean_on_minus_flip', 0.0):+.4f} CI95={fl_ci_str}")
        ct = b.get("control_pareado", {})
        ct_ci = ct.get("cbb_ci95")
        ct_ci_str = "ABSTENCION" if (ct_ci is None or ct.get("n", 0) < pins["n_min"]) else str(ct_ci)
        L.append(f"  - control pareado: n={ct.get('n')} mean={ct.get('mean', 0.0):+.4f} CI95={ct_ci_str}")
        tc = m.get("comparador_temporal_puro", {}).get("realista", {})
        L.append(f"  - comparador temporal puro (declarado): n={tc.get('n_trades', 0)} "
                 f"E[stop-R]={tc.get('mean_stop_r', 0.0):+.4f} ({TEMPORAL_NOTE})")
        oc = m.get("inside_close_OFF", {})
        dd = oc.get("dias_comunes", {})
        L.append(f"  - inside_close=OFF (declarado): signals_dev={oc.get('signals_dev', 0)} "
                 f"n={oc.get('realista', {}).get('n_trades', 0)} dias_comunes_n={dd.get('n_common_trades')} "
                 f"E[on-off]={dd.get('mean_on_minus_off', 0.0):+.4f} (sin estatus de test)")
        dr = b.get("realista", {}).get("drift_rel", {})
        L.append(f"  - drift (variacion distancia al stop): n={dr.get('n')} mean={dr.get('mean')} "
                 f"p5={dr.get('p5')} p95={dr.get('p95')} min={dr.get('min')} max={dr.get('max')}")
        re_ = b.get("realista", {}).get("R_efectivo_target", {})
        L.append(f"  - R_efectivo_target=|target-entry_fill|/|entry_fill-stop| "
                 f"(R_nominal=1.5): n={re_.get('n')} mean={re_.get('mean')} "
                 f"p5={re_.get('p5')} p95={re_.get('p95')} min={re_.get('min')} max={re_.get('max')}")
        sl = m.get("sellado", {})
        L.append(f"  - sellado: abierto={sl.get('abierto')} motivo={sl.get('motivo')} "
                 f"dev_pasa={sl.get('dev_pasa')} ya_abierto={sl.get('sellado_ya_abierto')}")
    L.append("")
    L.append("## Deltas respecto a run_n1.py (fork, importacion prohibida)")
    L.append("")
    for dlt in [
        "1. Senal: N1 intradia (sweep 9:00-9:30 + rechazo / OR-breakout 9:30-10:00 con k_d*ATR) -> "
        "W4 semanal D1 lunes/martes (barrido + cierre interior estrictos; igualdad no cuenta; doble "
        "sweep / sin cierre interior / sin barrido -> NO_SIGNAL). Sin ATR, sin k_d, sin k_stop, sin percentil de vol.",
        "2. Sesiones por FECHAS EXACTAS lun/mar de la misma semana + completitud 78 slots [09:30,16:00) ET "
        "con basis identico; dedup (iso_year, iso_week) max 1 entrada/semana (interanual incluido).",
        "3. Reloj senal 09:25 ET (close = entry estimado) / fill 09:30 ET open, bar_interval=300 "
        "(vs reloj intradia N1).",
        "4. Stop estructural = extremo del barrido del martes, margen 0 (vs stop N1 con k_stop*ATR); "
        "target 1.5R ABSOLUTO desde entry estimado (Alt B) con drift R nominal vs efectivo medido POR TRADE "
        "(media/p5/p95/min/max, sin claims escalares).",
        "5. Salida primaria primera barra >= 16:00 ET del mismo dia (390 min, vs 12:00 ET en N1); "
        "sensibilidad = SOLO escenario de costes 2.0 pts RT (sin variante de 60 barras ni event-study K=20).",
        "6. mirror_valid_levels extendido: fill que cruza el TARGET -> gap_reject_beyond_target "
        "(evita TP instantaneo espurio), con contador diag propio.",
        "7. Sizing cantidad fija 1 contrato (vs riesgo 1% sobre 50k en N1); headline stop-R primario + "
        "budget-R secundario; risk_dollars + elegibilidad Practice <= $200 solo telemetria.",
        "8. Brazos B_on (H_B) + B_flip (misma D1, lado opuesto, apareada trade a trade) + comparador "
        "temporal puro + require_inside_close=OFF como sub-analisis declarados SIN estatus de test "
        "(vs A_on/A_off/C + event-study + regresion en N1).",
        "9. n_min=100 trades por mercado (vs 150/100 en N1); dev < 100 -> ABSTENCION y NO se abre el sellado; "
        "G5 fail-fast SOLO sobre stop_R_dev_MNQ y stop_R_tail_MNQ (comparador/exploratorios/sensibilidad/drift excluidos).",
        "10. Mercados --markets MNQ,MES,MYM por defecto (cada uno con su tick/dollar/coste); MGC EXCLUIDO por pin "
        "y rechazado por CLI (gate=MGC); rolls POR MERCADO via --roll-dates (declarados en manifest).",
        "11. Sin imports de run_n1 (fork standalone autocontenido); artefactos metrics_<MKT>_W4.json / w4_status.json.",
        "12. Regla A1 (solo SL en vela de fill) por composicion sin tocar src/: el motor corre "
        "con target lejano (fill, SL, gap-stop y salida temporal fieles; nunca TP) y el target "
        "real se re-arma desde la siguiente vela (prioridad SL; TP a precio target sin slippage).",
        "13. B_flip apareada de verdad (C3): ambos brazos corren SOLO sobre dias elegibles por "
        "los dos (invariante N_on==N_flip y mismos timestamps, gate=G1); G1 a nivel de senal "
        "(C4); sellado solo si dev pasa con lock persistente w4_sealed.lock (C2); slots 78 "
        "exactos con segundos a cero y basis canonico W2 (W2); rolls solo el miercoles (W3); "
        "R_efectivo_target + atr14 por trade (W4); comparador temporal con redaccion literal (W5).",
    ]:
        L.append(f"- {dlt}")
    L.append("")
    L.append("## Anomalias observadas")
    L.append("")
    for sym, m in market_results.items():
        if "diag" not in m:
            continue
        L.append(f"- {sym}: " + json.dumps(m["diag"], ensure_ascii=False))
    L.append("")
    L.append("## Cobertura de gates (W1: lo no cubierto se declara, sin PASS no acreditado)")
    L.append("")
    for sym, m in market_results.items():
        if "gates" not in m:
            continue
        g = m["gates"]
        L.append(f"- {sym} G1: {g['G1_signal_parity'].get('cobertura', 'n/d')}")
        L.append(f"- {sym} G2: {g['G2_mapping'].get('cobertura', 'n/d')}")
        L.append(f"- {sym} G3: {g['G3_determinism'].get('cobertura', 'n/d')}")
        L.append(f"- {sym} G2-dev: {g.get('G2_dev', {}).get('cobertura', 'n/d')}")
    L.append("")
    L.append("## Limitaciones declaradas")
    L.append("")
    L.append("- Rolls: lista por mercado via --roll-dates; SOLO excluye entradas en fecha de roll "
             "del miercoles (pin literal); roll en lunes/martes se cuenta en diag "
             "'semana_con_roll_en_lunes_o_martes' sin filtrar; lista vacia = limitacion declarada "
             "(sin calendario ex-ante).")
    L.append("- Festivos/early-closes: sin calendario ex-ante; se detectan post-hoc (lunes/martes incompleto -> "
             "NO_SIGNAL; falta barra 16:00 -> unresolved).")
    L.append("- MGC excluido por pin (sesion 08:20-13:30 ET incompatible con salida 16:00 ET).")
    L.append("- Parser rechaza timestamps duplicados; agrupacion por fecha ET.")
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
    L.append("- Conclusion cuantitativa: DIFERIDA a Hermes (multiplicidad >130 comparaciones; sin alfa pre-fijado).")
    L.append("")
    return "\n".join(L)


def maybe_write_sealed_lock(lock_file: Path, prereg_sha: str,
                            markets: list[str], opened_any: bool) -> bool:
    """Crea el lock del sellado SOLO si algun mercado lo abrio y no existe
    (persistente entre ejecuciones, fail-closed). Devuelve si quedo creado."""
    if not opened_any:
        return False
    try:
        write_sealed_lock(lock_file, prereg_sha,
                          ["B_on", "B_flip", "temporal", "OFF"], markets)
        return True
    except FileExistsError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Runner W4 candidata B (preregistro congelado)")
    ap.add_argument("--smoke", action="store_true",
                    help="semana sintetica en memoria; numeros NO son resultado")
    ap.add_argument("--markets", default="MNQ,MES,MYM",
                    help="mercados separados por coma (defecto MNQ,MES,MYM; MGC rechazado)")
    ap.add_argument("--roll-dates", default="",
                    help="rolls por mercado 'MNQ=YYYY-MM-DD,MES=YYYY-MM-DD' (sin entradas)")
    ap.add_argument("--outdir", default=str(W4_DIR))
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    prereg = verify_preregistro()
    prereg_sha = compute_file_sha256(PREREG_PATH).lower()
    print(f"Preregistro congelado verificado (SHA256: {prereg_sha})", flush=True)
    pins = extract_pins(prereg)
    try:
        markets = validate_markets(args.markets)
    except ValueError as exc:
        msg = str(exc)
        emit_invalid("MGC" if "MGC" in msg else "MARKET", msg)
    try:
        rolls = parse_roll_dates(args.roll_dates, markets)
    except ValueError as exc:
        emit_invalid("ROLL", str(exc))

    market_results: dict[str, Any] = {}
    datasets_meta: dict[str, Any] = {}
    raw: dict[str, tuple[bytes, str]] = {}

    if not args.smoke:
        try:
            zip_sha = compute_file_sha256(ZIP_PATH)
        except FileNotFoundError:
            emit_invalid("DATA", f"zip-canonico-ausente({ZIP_PATH})")
        print(f"[{ZIP_PATH.name}] sha256 registrado antes de leer: {zip_sha[:16]}...", flush=True)
        for sym in markets:
            member = MARKET_FILES[sym]
            try:
                content, sha = read_member_bytes(ZIP_PATH, member)
            except FileNotFoundError:
                if sym in ("MES", "MYM"):
                    market_results[sym] = {"status": "exploratorio-no-disponible",
                                           "market_role": "exploratorio"}
                    datasets_meta[sym] = {"status": "exploratorio-no-disponible",
                                          "member": member}
                    print(f"[{sym}] exploratorio-no-disponible (sin dataset)", flush=True)
                    continue
                emit_invalid("DATA", f"MNQ-canonico-ausente({member})")
            raw[sym] = (content, sha)
            datasets_meta[sym] = {"member": member, "sha256": sha}
            print(f"[{sym}] sha256 registrado antes de leer: {sha[:16]}...", flush=True)
    else:
        for sym in markets:
            datasets_meta[sym] = {"source": "sintetico-smoke", "sha256": "n/a-smoke"}
        zip_sha = "n/a-smoke"

    initial_manifest = {
        "metadata": {"title": "Manifest W4", "status": "PROCESSING",
                     "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                     "runtime": sys.version,
                     "preregistro_sha256": prereg_sha,
                     "smoke_mode": args.smoke},
        "datasets": datasets_meta,
        "zip_sha256": zip_sha,
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36,
                              "test_months": 6, "step_months": 6,
                              "purge_gap_bars": 0,
                              "warmup_bars": CALIBRATION_BARS_COUNT,
                              "dev_folds": "0..5",
                              "sealed_folds": "6..7",
                              "final_sealed": "ultimos-6-meses-1-corrida-al-final"},
        "aclaraciones_implementacion": [
            "Lectura canonica de tramos sellados: dev = folds 0..5; sellado = folds 6..7 + tramo final 6 meses, abierto UNA vez al final."
        ],
        "pins": pins,
        "seeds": {"cbb_seed": pins["cbb_seed"], "control_seed": pins["control_seed"]},
        "roll_dates": {m: sorted(d.isoformat() for d in rolls.get(m, set())) or "no-provistas"
                       for m in markets},
        "cost_scenarios": COST_SCENARIOS,
        "artifacts_sha256": {},
    }
    write_json(outdir / "manifest.json", initial_manifest)

    # C2: lock persistente del sellado (solo corrida real, nunca smoke).
    lock_file = W4_DIR / SEALED_LOCK_NAME
    sealed_already_open = (not args.smoke) and lock_file.exists()
    if sealed_already_open:
        print(f"[sellado] lock preexistente {lock_file.name}: tail sin correr "
              f"(fail-closed, sellado_ya_abierto).", flush=True)

    try:
        if args.smoke:
            print("MODO SMOKE: semana sintetica — los numeros NO son resultado.", flush=True)
            for sym in markets:
                bars = build_smoke_bars()
                market_results[sym] = process_market(
                    sym, bars, pins, None, set(), True, enforce_g5=(sym == "MNQ"))
                datasets_meta[sym]["n_bars"] = len(bars)
            status = "SMOKE"
        else:
            # MNQ primero: su veredicto dev gobierna la apertura del sellado
            # de todos los mercados (C2, brazo primario confirmatorio).
            ordered = (["MNQ"] if "MNQ" in raw else []) + [
                s for s in raw if s != "MNQ"]
            mnq_in_run = "MNQ" in ordered
            if not mnq_in_run:
                allow_sealed: bool | None = False
                allow_sealed_reason: str = "sin-MNQ-no-hay-gate-confirmatorio"
                print(f"[sellado] MNQ no esta en la corrida: sellado bloqueado para todos los mercados ({allow_sealed_reason}).", flush=True)
            else:
                allow_sealed: bool | None = None
                allow_sealed_reason: str = ""

            for sym in ordered:
                content, sha = raw[sym]
                bars = parse_bars(content)
                plan = WalkForwardPlan.create_calendar_rolling(
                    bars, train_months=36, test_months=6, step_months=6,
                    purge_gap_bars=0, warmup_bars=CALIBRATION_BARS_COUNT)
                print(f"[{sym}] {len(bars)} barras | plan C1: {plan.n_folds} folds", flush=True)
                market_results[sym] = process_market(
                    sym, bars, pins, plan, rolls.get(sym, set()), False,
                    enforce_g5=(sym == "MNQ"),
                    allow_sealed=allow_sealed,
                    allow_sealed_reason=allow_sealed_reason,
                    sealed_already_open=sealed_already_open,
                    lock_path=lock_file,
                    prereg_sha=prereg_sha,
                    honor_preexisting_lock=False)
                if sym == "MNQ":
                    allow_sealed = bool(
                        market_results[sym].get("sellado", {}).get("dev_pasa", False))
                    allow_sealed_reason = ("dev-pasa-abrir-sellado" if allow_sealed
                                           else "veredicto-MNQ-no-pasa")
                    print(f"[sellado] veredicto MNQ dev_pasa={allow_sealed}.", flush=True)
                datasets_meta[sym].update({
                    "n_bars": len(bars),
                    "first": bars[0].timestamp.isoformat() if bars else None,
                    "last": bars[-1].timestamp.isoformat() if bars else None,
                })
            bon = [m.get("B_on", {}).get("status", "") for m in market_results.values()
                   if isinstance(m.get("B_on"), dict)]
            status = "ABSTENCION" if any(s == "ABSTENCION" for s in bon) else "OK"
            opened_any = any(bool(m.get("sellado", {}).get("abierto", False))
                             for m in market_results.values()
                             if isinstance(m, dict))
            if maybe_write_sealed_lock(lock_file, prereg_sha, ordered, opened_any):
                print(f"[sellado] lock creado: {lock_file.name}.", flush=True)
    except SystemExit as exc:
        initial_manifest["metadata"]["status"] = f"ABORTED({exc.code})"
        initial_manifest["metadata"]["aborted_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(outdir / "manifest.json", initial_manifest)
        raise

    # artefactos
    for sym, m in market_results.items():
        if m.get("status") == "exploratorio-no-disponible":
            continue
        write_json(outdir / f"metrics_{sym}_W4.json", {
            "metadata": {"market": sym, "status": status,
                         "market_role": m.get("market_role", "confirmatorio" if sym == "MNQ" else "exploratorio"),
                         "preregistro_sha256": prereg_sha,
                         "dataset_sha256": datasets_meta.get(sym, {}).get("sha256"),
                         "smoke_mode": args.smoke,
                         "ran_at_utc": datetime.now(timezone.utc).isoformat()},
            **m,
        })
        print(f"Escrito metrics_{sym}_W4.json", flush=True)

    artifacts = {p.name: compute_file_sha256(p) for p in sorted(outdir.glob("metrics_*.json"))}
    manifest = {
        "metadata": {"title": "Manifest W4", "status": status,
                     "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                     "runtime": sys.version,
                     "preregistro_sha256": prereg_sha,
                     "smoke_mode": args.smoke},
        "datasets": datasets_meta,
        "zip_sha256": zip_sha,
        "walk_forward_plan": {"type": "calendar_rolling", "train_months": 36,
                              "test_months": 6, "step_months": 6,
                              "purge_gap_bars": 0,
                              "warmup_bars": CALIBRATION_BARS_COUNT,
                              "dev_folds": "0..5",
                              "sealed_folds": "6..7",
                              "final_sealed": "ultimos-6-meses-1-corrida-al-final"},
        "aclaraciones_implementacion": [
            "Lectura canonica de tramos sellados: dev = folds 0..5; sellado = folds 6..7 + tramo final 6 meses, abierto UNA vez al final."
        ],
        "pins": pins,
        "seeds": {"cbb_seed": pins["cbb_seed"], "control_seed": pins["control_seed"]},
        "roll_dates": {m: sorted(d.isoformat() for d in rolls.get(m, set())) or "no-provistas"
                       for m in markets},
        "cost_scenarios": COST_SCENARIOS,
        "artifacts_sha256": artifacts,
    }
    write_json(outdir / "manifest.json", manifest)
    (outdir / "INFORME.md").write_text(
        render_informe(status, market_results, pins, prereg_sha, args.smoke),
        encoding="utf-8")
    sellado_decl = {
        sym: (m.get("sellado", {}) if isinstance(m, dict) else {})
        for sym in markets if isinstance(market_results.get(sym), dict)
        and "sellado" in market_results.get(sym, {})
    }
    write_json(outdir / "w4_status.json", {
        "status": status,
        "sellado": sellado_decl,
        "sellado_ya_abierto": bool(sealed_already_open),
        "preregistro_sha256": prereg_sha,
    })
    print(f"W4 STATUS: {status}", flush=True)


if __name__ == "__main__":
    main()
