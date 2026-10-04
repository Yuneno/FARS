#!/usr/bin/env python3
"""
paper_bot.py — bot ORB operando en PAPER TRADING sobre la capa realtime de FARS.

COMPONENTES SEPARADOS (requisito de Ricardo):
  estrategia   = reglas ORB de `nq-intraday-breakout` (MIT), reimplementadas aquí
                 como generador de señales de SOLO lectura de barras pasadas
  riesgo       = FARS `AccountAwareRiskEngine` — veto absoluto antes de órdenes
  ejecución    = FARS `PaperExecutionAdapter` — NUNCA live (el propio adapter
                 se niega a correr si live está habilitado)
  FARS Core    = solo analítico (ledger → métricas)

# CONTROLES:
#   · límites diarios   : FundedAccountRules.daily_loss_limit_usd / daily_profit_target_usd
#   · drawdown máx      : max_drawdown_pct / max_drawdown_usd (static o trailing)
#   · tamaño posición   : risk_per_trade * equity / (stop_pts * point_value)
#   · stop OBLIGATORIO  : sin stop_price el intent se rechaza ANTES de enviar
#   · apagado seguro    : kill-switch por archivo + SystemEvent(kind="halt");
#                         el risk engine pasa a denegar todo
#
# CONVENCIÓN CAUSAL Y STOP EFECTIVO (Fix B1):
#   - Decisión al CIERRE de la barra completada (t) donde se produce el breakout.
#   - Ejecución simulada al OPEN de la barra SIGUIENTE (t+1).
#   - Prohibido look-ahead: ningún dato de la barra t+1 (high/low/close) se usa para la entrada.
#   - Stop obligatorio anclado al PRECIO EFECTIVO DE ENTRADA: entrada ± 100 puntos.
#   - Techo mensual: gestionado en estrategia (OrbSignalGenerator); engine con max_trades=None (Fix B2).
#   - Salida temporal: simétrica EOD para largos y cortos a TRADE_END (Fix H9).
#
# SIN credenciales, SIN órdenes con dinero real, SIN conexión a broker.
#
# Uso:
#     python paper_bot.py --replay-mes 3      # reproduce N meses de barras
#     python paper_bot.py --kill              # activa el kill-switch
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, r"E:\FARS-LAB\ext_review2\nq-intraday-breakout\src")

from src.realtime.events import (  # noqa: E402
    AccountSnapshot, OrderIntent, Signal, SystemEvent,
)
from src.realtime.paper import PaperAssumptions, PaperExecutionAdapter  # noqa: E402
from src.realtime.risk import AccountAwareRiskEngine  # noqa: E402
from src.types import FundedAccountRules  # noqa: E402

CT = ZoneInfo("America/Chicago")
KILL_FILE = HERE / "KILL_SWITCH"

# ── Reglas de la estrategia (nq-intraday-breakout, Config del autor) ───────
WINDOW_START = dtime(8, 30)
WINDOW_END = dtime(10, 0)      # exclusivo
TRADE_START = dtime(10, 0)
TRADE_END = dtime(14, 30)
STOP_PTS = 100.0               # $2,000 / $20-pt NQ  →  100 puntos
TARGET_PTS = 200.0
POINT_VALUE = 2.0              # MNQ $2/punto
MAX_TRADES_DAY = 2
MAX_TRADES_MONTH = 42
COMMISSION_PER_SIDE = 0.62      # MNQ realista
SLIPPAGE_TICKS_PER_SIDE = 1.5
TICK_VALUE = 0.50               # 0.25 pt * $2/pt = $0.50
ROUND_TURN_COST = 2 * COMMISSION_PER_SIDE + 2 * SLIPPAGE_TICKS_PER_SIDE * TICK_VALUE  # 2.74 USD
ENTRY_MODE = "stop"            # convención conservadora (la corregida del autor)


def compute_trade_pnl(direction: str, entry_price: float, exit_price: float, contracts: int) -> tuple[float, float]:
    """Calcula (pnl_usd, r_result) de un trade con costes MNQ realistas."""
    sign = 1.0 if direction.lower() == "long" else -1.0
    gross_pts = sign * (exit_price - entry_price)
    cost_pts = ROUND_TURN_COST / POINT_VALUE
    r = (gross_pts - cost_pts) / STOP_PTS
    pnl = (gross_pts * POINT_VALUE - ROUND_TURN_COST) * contracts
    return pnl, r


def _utc_day(value: datetime) -> date:
    return value.astimezone(timezone.utc).date()


class StepClock:
    """Clock del replay: avanza con las barras. tz-aware siempre."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        self._now = t


class OrbSignalGenerator:
    """Genera señales ORB. Lee SOLO barras ya cerradas (sin look-ahead)."""

    def __init__(self, max_trades_day: int = MAX_TRADES_DAY,
                 max_trades_month: int = MAX_TRADES_MONTH) -> None:
        self.win_high: float | None = None
        self.win_low: float | None = None
        self.day: str | None = None
        self.month: str | None = None
        self.trades_today = 0
        self.trades_this_month = 0
        self.max_trades_day = max_trades_day
        self.max_trades_month = max_trades_month

    @staticmethod
    def effective_stop(action: str, entry_price: float) -> float:
        """Calcula el stop loss anclado al precio efectivo de entrada (STOP_PTS de distancia)."""
        sign = 1.0 if action == "long" else -1.0
        return round(entry_price - sign * STOP_PTS, 4)

    def on_bar(self, ts: datetime, o: float, h: float, lo: float,
               c: float) -> tuple[str, float] | None:
        """Devuelve (accion, stop_indicativo) o None al cierre de una barra completada.
        
        La entrada efectiva se ejecuta en la barra siguiente, anclando el stop
        definitivo a: entry_price ± STOP_PTS via self.effective_stop().
        """
        day = ts.date().isoformat()
        if day != self.day:
            self.day = day
            self.win_high = self.win_low = None
            self.trades_today = 0

        month = f"{ts.year:04d}-{ts.month:02d}"
        if month != self.month:
            self.month = month
            self.trades_this_month = 0

        t = ts.time()
        if WINDOW_START <= t < WINDOW_END:
            self.win_high = h if self.win_high is None else max(self.win_high, h)
            self.win_low = lo if self.win_low is None else min(self.win_low, lo)
            return None
        if (self.win_high is None or self.trades_today >= self.max_trades_day
                or self.trades_this_month >= self.max_trades_month
                or not (TRADE_START <= t <= TRADE_END)):
            return None
        if h > self.win_high:
            self.trades_today += 1
            self.trades_this_month += 1
            return ("long", round(c - STOP_PTS, 4))
        if lo < self.win_low:
            self.trades_today += 1
            self.trades_this_month += 1
            return ("short", round(c + STOP_PTS, 4))
        return None


class RiskGuard:
    """Capa de riesgo FARS + stop obligatorio + kill-switch."""

    def __init__(self, rules: FundedAccountRules, clock: StepClock) -> None:
        self.engine = AccountAwareRiskEngine(rules, clock, source="orb-paper-risk")
        self.rules = rules
        self.clock = clock
        self.seq = 0
        self.last_equity = rules.initial_balance
        self.last_peak = rules.initial_balance
        self.last_realized = 0.0
        self.trades_applied = 0
        self.snapshot(
            clock.now(),
            equity=rules.initial_balance,
            peak_equity=rules.initial_balance,
            realized=0.0,
            trades_applied=0,
        )

    def _env(self, ts: datetime, prefix: str) -> dict:
        self.seq += 1
        return {"event_id": f"{prefix}-{self.seq:06d}", "source": "orb-paper-bot",
                "timestamp": ts, "sequence": self.seq, "origin": "replay"}

    def killed(self) -> bool:
        return KILL_FILE.exists()

    def halt(self, ts: datetime, detail: str) -> SystemEvent:
        ev = SystemEvent(**self._env(ts, "halt"), kind="system_halted", detail=detail)
        self.engine.observe(ev)
        return ev

    def snapshot(self, ts: datetime, equity: float, peak_equity: float,
                 realized: float, trades_applied: int) -> AccountSnapshot:
        self.last_equity = equity
        self.last_peak = peak_equity
        self.last_realized = realized
        self.trades_applied = trades_applied
        ev = AccountSnapshot(**self._env(ts, "acct"), balance=equity,
                             equity=equity, peak_equity=peak_equity,
                             realized_pnl=realized,
                             trades_applied=trades_applied)
        self.engine.observe(ev)
        return ev

    def authorize(self, ts: datetime, action: str, symbol: str,
                  stop_price: float) -> tuple[Signal, object, OrderIntent | None]:
        """Signal → RiskDecision → OrderIntent. El stop es OBLIGATORIO."""
        if stop_price is None:
            raise RuntimeError("stop obligatorio ausente: intent bloqueado en origen")
        if self.engine._snapshot is None:
            self.snapshot(ts, self.rules.initial_balance, self.rules.initial_balance, 0.0, 0)
        elif (
            self.engine._start_of_day_date is not None
            and _utc_day(ts) > self.engine._start_of_day_date
            and ts >= self.engine._snapshot.timestamp
        ):
            self.snapshot(ts, self.last_equity, self.last_peak, self.last_realized, self.trades_applied)
        sig = Signal(**self._env(ts, "sig"), symbol=symbol,
                     action={"long": "LONG", "short": "SHORT", "flat": "FLAT"}[action])
        decision = self.engine.evaluate(sig)
        if not decision.approved:
            return sig, decision, None
        intent = OrderIntent(**self._env(ts, "ord"), symbol=symbol, action=sig.action,
                             risk_decision_id=decision.event_id, size=1,
                             stop_price=stop_price)
        return sig, decision, intent


def position_size(equity: float, rules: FundedAccountRules) -> int:
    """Tamaño de posición: riesgo por trade sobre el riesgo del stop."""
    risk_usd = rules.risk_per_trade * equity
    if rules.max_risk_dollars_per_order:
        risk_usd = min(risk_usd, rules.max_risk_dollars_per_order)
    per_contract = STOP_PTS * POINT_VALUE
    return max(0, int(risk_usd / per_contract))


def run_replay(months: int, rules: FundedAccountRules) -> dict:
    from nq_breakout.data import load_nq_1min, resample_5min

    DATA = Path("E:/FARS-LAB/ext_review2/data_adapted/mnq_1min_multicharts.csv.gz")
    print("Cargando barras …")
    df = resample_5min(load_nq_1min(DATA))
    end = df["date"].max()
    start = (end - pd.DateOffset(months=months)).to_pydatetime()
    sub = df[df["date"] >= start]
    print(f"  replay {sub['date'].iloc[0]} → {sub['date'].iloc[-1]}  ({len(sub):,} barras)")

    clock = StepClock(pd.Timestamp(start).tz_localize(CT).to_pydatetime())
    guard = RiskGuard(rules, clock)
    paper = PaperExecutionAdapter(clock, PaperAssumptions(
        latency=pd.Timedelta(seconds=0).to_pytimedelta(),
        slippage="unmodeled: se declara, no se simula (paper)",
        commissions="unmodeled: 0.62 USD/side MNQ se cobra fuera del paper",
        partial_fills="not_simulated: un intent = un report",
        rejections="stop obligatorio faltante o riesgo denegado",
        fill_policy="full",
    ))
    strat = OrbSignalGenerator()

    equity = rules.initial_balance
    peak_equity = equity
    realized = 0.0
    trades_applied = 0
    journal: list[dict] = []
    halted = False
    open_trade: dict | None = None
    pending_signal: tuple[str, datetime] | None = None
    signals_count = 0

    for row in sub.itertuples(index=False):
        ts = pd.Timestamp(row.date)
        if ts.tzinfo is None:
            ts = ts.tz_localize(CT)
        ts = ts.to_pydatetime()
        clock.set(ts)

        if guard.killed() and not halted:
            ev = guard.halt(ts, "kill-switch detectado: apagado seguro")
            halted = True
            journal.append({"t": ts.isoformat(), "event": "HALT", "detail": ev.detail})
            print(f"  [{ts}] HALT por kill-switch")
            break
        if halted:
            break

        # 1. Revisar salidas de posición abierta
        if open_trade is not None and ts > open_trade["entry_time"]:
            exit_price = None
            exit_reason = None
            direction = open_trade["action"]
            stop = open_trade["stop"]
            target = open_trade["target"]
            if direction == "long":
                if row.open <= stop:
                    exit_price, exit_reason = float(row.open), "stop"
                elif row.low <= stop:
                    exit_price, exit_reason = stop, "stop"
                elif row.open >= target:
                    exit_price, exit_reason = float(row.open), "target"
                elif row.high >= target:
                    exit_price, exit_reason = target, "target"
            else:
                if row.open >= stop:
                    exit_price, exit_reason = float(row.open), "stop"
                elif row.high >= stop:
                    exit_price, exit_reason = stop, "stop"
                elif row.open <= target:
                    exit_price, exit_reason = float(row.open), "target"
                elif row.low <= target:
                    exit_price, exit_reason = target, "target"

            t_now = ts.time()
            # Fix H9: Salida simétrica al fin de sesión para AMBOS lados
            if exit_price is None and t_now >= TRADE_END:
                exit_price, exit_reason = float(row.close), f"eod_{direction}"

            if exit_price is not None:
                pnl, r_mult = compute_trade_pnl(direction, open_trade["entry_price"],
                                                exit_price, open_trade["size"])
                realized += pnl
                equity += pnl
                peak_equity = max(peak_equity, equity)
                journal.append({
                    "t": ts.isoformat(),
                    "event": "TRADE_EXIT",
                    "action": direction,
                    "entry_price": open_trade["entry_price"],
                    "exit_price": exit_price,
                    "reason": exit_reason,
                    "pnl": round(pnl, 2),
                    "r_result": round(r_mult, 4),
                    "equity": round(equity, 2),
                    "peak_equity": round(peak_equity, 2),
                })
                open_trade = None

        guard.snapshot(ts, equity, peak_equity, realized, trades_applied)

        # 2. Fix B1: Ejecutar la señal pendiente al OPEN de la barra actual (sin look-ahead)
        if pending_signal is not None and open_trade is None:
            action, sig_ts = pending_signal
            pending_signal = None

            entry_price = float(row.open)
            sign = 1.0 if action == "long" else -1.0
            stop_price = strat.effective_stop(action, entry_price)
            size = position_size(equity, rules)
            if size <= 0:
                journal.append({
                    "t": ts.isoformat(),
                    "event": "SKIP_SIZING",
                    "detail": "tamaño de posición 0 por límite de riesgo",
                })
            else:
                signal, decision, intent = guard.authorize(ts, action, "MNQ", stop_price)
                entry = {
                    "t": ts.isoformat(),
                    "action": action,
                    "entry_price": entry_price,
                    "stop": stop_price,
                    "size": size,
                    "risk_decision": decision.approved,
                    "reason": decision.reason,
                    "decision_bar_time": sig_ts.isoformat(),
                }
                if intent is not None:
                    intent = OrderIntent(
                        **{**intent.__dict__, "size": size, "entry_price": entry_price}
                    )
                    report = paper.submit(signal, decision, intent)
                    entry["execution"] = getattr(report, "status", "unknown")
                    if getattr(report, "status", "") in ("filled", "accepted"):
                        trades_applied += 1
                        open_trade = {
                            "action": action,
                            "entry_time": ts,
                            "entry_price": entry_price,
                            "stop": stop_price,
                            "target": round(entry_price + sign * TARGET_PTS, 4),
                            "size": size,
                        }
                journal.append(entry)
                print(
                    f"  [{ts:%Y-%m-%d %H:%M}] {action:5} entry={entry_price:9.2f} stop={stop_price:9.2f} "
                    f"size={size} risk={'OK' if decision.approved else 'DENEGADO'} ({decision.reason})"
                )

        # 3. Fix B1: Al CIERRE de la barra actual, evaluar si se genera señal para la SIGUIENTE barra
        if open_trade is None and pending_signal is None:
            sig = strat.on_bar(ts, row.open, row.high, row.low, row.close)
            if sig is not None:
                pending_signal = (sig[0], ts)
                signals_count += 1

    if open_trade is not None:
        last_close = float(sub["close"].iloc[-1])
        pnl, r_mult = compute_trade_pnl(open_trade["action"], open_trade["entry_price"],
                                        last_close, open_trade["size"])
        realized += pnl
        equity += pnl
        peak_equity = max(peak_equity, equity)
        journal.append({
            "t": ts.isoformat(), "event": "FORCE_CLOSE",
            "action": open_trade["action"], "entry_price": open_trade["entry_price"],
            "exit_price": last_close, "reason": "replay_end", "pnl": round(pnl, 2),
            "r_result": round(r_mult, 4), "equity": round(equity, 2),
            "peak_equity": round(peak_equity, 2),
        })
        open_trade = None

    out = {"rules": {k: v for k, v in asdict(rules).items()},
           "bars_replayed": int(len(sub)),
           "signals": signals_count,
           "trades_accepted": trades_applied,
           "final_equity": round(equity, 2),
           "peak_equity": round(peak_equity, 2),
           "realized_pnl": round(realized, 2),
           "halted": halted,
           "journal_tail": journal[-25:],
           "live_execution_enabled": False}
    (HERE / "paper_run.json").write_text(json.dumps(out, indent=2, default=str),
                                         encoding="utf-8")
    print(f"\nResumen: {len(journal)} eventos, {trades_applied} órdenes aceptadas, "
          f"equity=${equity:,.2f}, peak=${peak_equity:,.2f}, halted={halted} → paper_run.json")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Bot ORB en paper trading (FARS realtime)")
    ap.add_argument("--replay-mes", type=int, default=3)
    ap.add_argument("--kill", action="store_true", help="activa el kill-switch")
    ap.add_argument("--unkill", action="store_true", help="retira el kill-switch")
    args = ap.parse_args()

    if args.kill:
        KILL_FILE.write_text(datetime.now().isoformat(), encoding="utf-8")
        print(f"KILL-SWITCH ACTIVADO ({KILL_FILE})")
        return 0
    if args.unkill:
        KILL_FILE.unlink(missing_ok=True)
        print("kill-switch retirado")
        return 0

    rules = FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.08,
        daily_loss_limit_pct=0.02,
        risk_per_trade=0.01,
        daily_loss_base="initial",
        drawdown_mode="trailing",
        max_trades=None,                      # Fix B2: sin techo global en engine; cupo mensual (42) en estrategia
        daily_loss_limit_usd=2_000.0,
        max_drawdown_usd=8_000.0,
        max_risk_dollars_per_order=1_000.0,
    )
    run_replay(args.replay_mes, rules)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
