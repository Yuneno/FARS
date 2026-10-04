#!/usr/bin/env python3
"""
separar_senales.py — separa señales (S), aceptadas (A) y rechazadas (R) por el RiskEngine.

Calcula métricas FARS (E[R], win rate, DD, racha perdedora) sobre:
  (i)  el ledger bruto (ledger_orb_fars_stop.csv, 2.444 señales)
  (ii) la secuencia riesgo-permitida autorizada por AccountAwareRiskEngine y pre-filtro mensual.

Reporta la diferencia entre ambos y desglosa los motivos de rechazo.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"E:\FARS-LAB\FARS")
sys.path.insert(0, str(HERE))

from src.ingestion import load_trade_csv  # noqa: E402
from src.metrics import compute_metrics  # noqa: E402
from src.realtime.events import AccountSnapshot, Signal  # noqa: E402
from src.realtime.risk import AccountAwareRiskEngine  # noqa: E402
from src.types import FundedAccountRules, Trade  # noqa: E402
import paper_bot as pb  # noqa: E402

CT = ZoneInfo("America/Chicago")


def default_rules(max_trades: int | None = None) -> FundedAccountRules:
    return FundedAccountRules(
        initial_balance=100_000.0,
        profit_target_pct=0.05,
        max_drawdown_pct=0.08,
        daily_loss_limit_pct=0.02,
        risk_per_trade=0.01,
        daily_loss_base="initial",
        drawdown_mode="trailing",
        max_trades=max_trades,
        daily_loss_limit_usd=2_000.0,
        max_drawdown_usd=8_000.0,
        max_risk_dollars_per_order=1_000.0,
    )


def get_trade_exit_time(trade: Trade) -> datetime:
    """Determina el timestamp de salida efectivo para atribuir el PnL al momento real (Fix H6).

    Usa el exit_time real del ledger. No aplica fallback a 14:30 salvo cuando la
    operación realmente cerró a fin de sesión (exit_reason EOD).
    """
    if hasattr(trade, "metadata") and isinstance(trade.metadata, dict):
        unknown = trade.metadata.get("unknown_fields", {})
        raw_exit = unknown.get("exit_time")
        if raw_exit:
            try:
                ts = pd.Timestamp(raw_exit)
                if ts.tzinfo is None:
                    ts = ts.tz_localize(trade.timestamp.tzinfo if trade.timestamp else CT)
                return ts.to_pydatetime()
            except Exception:
                pass
    if hasattr(trade, "exit_time") and getattr(trade, "exit_time"):
        return getattr(trade, "exit_time")

    # Salida a fin de sesión (14:30 CT) ÚNICAMENTE si la operación realmente cerró por fin de sesión (EOD)
    unknown = trade.metadata.get("unknown_fields", {}) if hasattr(trade, "metadata") and isinstance(trade.metadata, dict) else {}
    exit_reason = str(unknown.get("exit_reason", "")).lower()
    if "eod" in exit_reason:
        entry_ts = trade.timestamp or datetime(2010, 6, 1, 10, 0, tzinfo=CT)
        return entry_ts.replace(hour=14, minute=30, second=0, microsecond=0)

    # Si no tiene exit_time exportado ni indicación EOD, usar el propio timestamp del trade
    if trade.timestamp:
        return trade.timestamp
    return datetime(2010, 6, 1, 14, 30, tzinfo=CT)


def run_separation(csv_path: Path, max_trades_month: int = 42,
                   rules: FundedAccountRules | None = None,
                   export_accepted_path: Path | None = None) -> dict:
    if rules is None:
        rules = default_rules()

    dataset = load_trade_csv(
        csv_path,
        outcomes_finalized=True,
        analysis_timezone="America/New_York",
    )
    all_signals = list(dataset.trades)
    n_signals = len(all_signals)

    if n_signals == 0:
        raise ValueError("El ledger no contiene operaciones")

    # Iniciar motor y estado
    first_ts = all_signals[0].timestamp or datetime(2010, 6, 1, 10, 0, tzinfo=CT)
    clock = pb.StepClock(first_ts)
    engine = AccountAwareRiskEngine(rules, clock, source="separar-senales")

    equity = rules.initial_balance
    peak_equity = equity
    realized = 0.0
    trades_applied = 0
    seq = 0

    accepted_trades: list[Trade] = []
    rejected_trades: list[dict] = []

    current_month: str | None = None
    trades_this_month = 0
    pending_exits: list[tuple[datetime, float]] = []

    def flush_pending_exits(up_to_ts: datetime | None = None) -> None:
        """Aplica el PnL en el momento real de salida (Fix H6)."""
        nonlocal equity, peak_equity, realized, seq
        pending_exits.sort(key=lambda x: x[0])
        while pending_exits and (up_to_ts is None or pending_exits[0][0] <= up_to_ts):
            exit_ts, exit_pnl = pending_exits.pop(0)
            seq += 1
            clock.set(exit_ts)
            realized += exit_pnl
            equity += exit_pnl
            peak_equity = max(peak_equity, equity)
            engine.observe(AccountSnapshot(
                event_id=f"exit-snap-{seq}",
                source="separar-senales",
                timestamp=exit_ts,
                sequence=seq,
                origin="replay",
                balance=equity,
                equity=equity,
                peak_equity=peak_equity,
                realized_pnl=realized,
                trades_applied=trades_applied,
            ))

    for t in all_signals:
        seq += 1
        ts = t.timestamp or clock.now()

        # Atribuir primero cualquier PnL de salidas previas antes de evaluar la nueva señal (Fix H6)
        flush_pending_exits(up_to_ts=ts)

        clock.set(ts)

        # Pre-filtro mensual en capa de estrategia (Fix H5: cupo mensual unificado con OrbSignalGenerator)
        month = f"{ts.year:04d}-{ts.month:02d}"
        if month != current_month:
            current_month = month
            trades_this_month = 0

        # Verificar cupo mensual
        if max_trades_month is not None and trades_this_month >= max_trades_month:
            rejected_trades.append({
                "trade_id": t.trade_id,
                "timestamp": ts,
                "reason": "STRATEGY_MONTHLY_LIMIT",
                "r_result": t.r_result,
            })
            continue

        # La estrategia consume su cupo al evaluar la señal generada (Fix H5)
        trades_this_month += 1

        # Snapshot de la cuenta al motor (al momento de la señal)
        engine.observe(AccountSnapshot(
            event_id=f"snap-{seq}",
            source="separar-senales",
            timestamp=ts,
            sequence=seq,
            origin="replay",
            balance=equity,
            equity=equity,
            peak_equity=peak_equity,
            realized_pnl=realized,
            trades_applied=trades_applied,
        ))

        # Evaluación de riesgo
        action = t.direction.upper() if t.direction else "LONG"
        sig = Signal(
            event_id=f"sig-{seq}",
            source="separar-senales",
            timestamp=ts,
            sequence=seq,
            symbol=t.asset or "MNQ",
            action=action,
            origin="replay",
        )
        decision = engine.evaluate(sig)

        if decision.approved:
            size = pb.position_size(equity, rules)
            if size <= 0:
                rejected_trades.append({
                    "trade_id": t.trade_id,
                    "timestamp": ts,
                    "reason": "SIZING_ZERO",
                    "r_result": t.r_result,
                })
                continue

            # Orden aceptada
            trades_applied += 1
            accepted_trades.append(t)

            # Reconstruir camino de equity en el TIMESTAMP DE SALIDA (Fix H6)
            pnl = t.r_result * (pb.STOP_PTS * pb.POINT_VALUE) * size
            exit_ts = get_trade_exit_time(t)
            pending_exits.append((exit_ts, pnl))
        else:
            rejected_trades.append({
                "trade_id": t.trade_id,
                "timestamp": ts,
                "reason": decision.reason,
                "r_result": t.r_result,
            })

    # Liquidar cualquier salida remanente al final del histórico (Fix H6)
    flush_pending_exits(up_to_ts=None)

    # Verificación de consistencia S = A + R
    assert len(all_signals) == len(accepted_trades) + len(rejected_trades), (
        f"Inconsistencia: S ({len(all_signals)}) != A ({len(accepted_trades)}) + R ({len(rejected_trades)})"
    )

    # Exportar ledger aceptado si se solicitó (soporte para H8)
    if export_accepted_path is not None:
        rows = []
        for t in accepted_trades:
            rows.append({
                "trade_id": t.trade_id,
                "timestamp": t.timestamp.isoformat() if t.timestamp else "",
                "asset": t.asset or "MNQ",
                "direction": t.direction or "",
                "entry_price": t.entry_price,
                "stop_price": t.stop_price,
                "exit_price": t.exit_price,
                "r_result": t.r_result,
                "strategy": t.strategy,
                "exit_time": get_trade_exit_time(t).isoformat(),
            })
        export_df = pd.DataFrame(rows)
        export_path = Path(export_accepted_path)
        export_df.to_csv(export_path, index=False)

    # Cálculo de métricas FARS
    metrics_gross = compute_metrics([t.r_result for t in all_signals])
    metrics_allowed = (
        compute_metrics([t.r_result for t in accepted_trades])
        if accepted_trades else None
    )

    reasons_count = Counter(r["reason"] for r in rejected_trades)

    return {
        "n_signals": len(all_signals),
        "n_accepted": len(accepted_trades),
        "n_rejected": len(rejected_trades),
        "reasons": dict(reasons_count),
        "metrics_gross": metrics_gross,
        "metrics_allowed": metrics_allowed,
        "final_equity": round(equity, 2),
        "peak_equity": round(peak_equity, 2),
        "realized_pnl": round(realized, 2),
        "accepted_trades": accepted_trades,
        "rejected_trades": rejected_trades,
        "export_accepted_path": str(export_accepted_path) if export_accepted_path else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Separar señales S/A/R y comparar métricas FARS")
    ap.add_argument("--csv", type=Path, default=HERE / "ledger_orb_fars_stop.csv")
    ap.add_argument("--max-month", type=int, default=42)
    ap.add_argument("--export-accepted", type=Path, default=HERE / "ledger_orb_fars_accepted.csv",
                    help="Ruta donde exportar el ledger de órdenes aceptadas (para bootstrap_camino.py)")
    args = ap.parse_args()

    print(f"Cargando {args.csv.name} y simulando autorizaciones FARS...")
    out = run_separation(args.csv, max_trades_month=args.max_month,
                         export_accepted_path=args.export_accepted)

    mg = out["metrics_gross"]
    ma = out["metrics_allowed"]

    print("\n" + "═" * 70)
    print("SEPARACIÓN DE SEÑALES (S = A + R)")
    print("═" * 70)
    print(f"  Total señales generadas (S) : {out['n_signals']:>6}")
    print(f"  Órdenes aceptadas       (A) : {out['n_accepted']:>6} ({out['n_accepted']/out['n_signals']*100:.1f}%)")
    print(f"  Órdenes rechazadas      (R) : {out['n_rejected']:>6} ({out['n_rejected']/out['n_signals']*100:.1f}%)")
    print("\nDesglose de motivos de rechazo:")
    for reason, cnt in sorted(out["reasons"].items(), key=lambda x: -x[1]):
        print(f"    - {reason:<26}: {cnt:>5} ({cnt/out['n_rejected']*100:.1f}%)")

    print("\n" + "═" * 70)
    print(f"{'Métrica FARS':<26} {'(i) Ledger Bruto':>18} {'(ii) Riesgo-Permitido':>22}")
    print("─" * 70)
    if ma is not None:
        print(f"{'Operaciones (N)':<26} {mg.n_trades:>18d} {ma.n_trades:>22d}")
        print(f"{'E[R] (Esperanza R)':<26} {mg.expectancy_r:>18.4f} {ma.expectancy_r:>22.4f}")
        print(f"{'Win Rate (%)':<26} {mg.win_rate*100:>17.2f}% {ma.win_rate*100:>21.2f}%")
        print(f"{'Avg Win R':<26} {mg.avg_win_r:>18.4f} {ma.avg_win_r:>22.4f}")
        print(f"{'Avg Loss R':<26} {mg.avg_loss_r:>18.4f} {ma.avg_loss_r:>22.4f}")
        print(f"{'Std R':<26} {mg.std_r:>18.4f} {ma.std_r:>22.4f}")
        print(f"{'Max Drawdown (R)':<26} {mg.max_drawdown_r:>18.2f} {ma.max_drawdown_r:>22.2f}")
        print(f"{'Max Losing Streak':<26} {mg.max_losing_streak:>18d} {ma.max_losing_streak:>22d}")
    print("═" * 70)

    if ma is not None:
        delta_er = ma.expectancy_r - mg.expectancy_r
        delta_wr = (ma.win_rate - mg.win_rate) * 100
        delta_dd = ma.max_drawdown_r - mg.max_drawdown_r
        print("\nImpacto de la capa de riesgo:")
        print(f"  Δ E[R]            : {delta_er:+.4f} R")
        print(f"  Δ Win Rate        : {delta_wr:+.2f} pp")
        print(f"  Δ Max Drawdown    : {delta_dd:+.2f} R")
        print(f"  Equity final      : ${out['final_equity']:,.2f} (Peak: ${out['peak_equity']:,.2f})")
        if abs(delta_er) > 0.02 or ma.expectancy_r <= 0:
            print("  ADVERTENCIA: La capa de riesgo altera materialmente la expectativa de la estrategia.")
        else:
            print("  CONCLUSIÓN: La capa de riesgo preserva la distribución de expectativa con filtrado controlado.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
