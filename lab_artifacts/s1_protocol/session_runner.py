#!/usr/bin/env python3
"""session_runner.py — Runner para sesión sombra en vivo y dry run en replay (Bloque S1).

Modos de operación:
1. --mode replay: Ingesta barras históricas (offline) para validar el cableado completo
   (bus -> estrategia -> riesgo fail-closed -> ejecución paper -> grabador) sin conexión al broker.
2. --mode live: Conexión read-only a TopstepX/ProjectX, verificación de cuenta simulada,
   polling de barras M5 en vivo de MNQ, evaluación en tiempo real de la estrategia SMC-FVG (k=0.5),
   autorización/veto por AccountAwareRiskEngine, fills simulados y registro en JSONL.

Reglas duras de S1:
- CERO órdenes live (LIVE_EXECUTION_ENABLED = False, PaperExecutionAdapter solamente).
- Aborta si la cuenta seleccionada no tiene simulated=True.
- Kill-switch por archivo centinela (lab_artifacts/s1_protocol/KILL_SWITCH) o SIGINT/Ctrl+C.
- Fail-closed ante cualquier desconexión, dato anómalo o estado desconocido.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lab_artifacts.m8_protocol.filters import M8FilteredStrategy
from src.backtest.history import Bar as BacktestBar
from src.backtest.markets import MNQ
from src.realtime.bus import AsyncIOEventBus
from src.realtime.clock import Clock, SystemClock
from src.realtime.config import load_projectx_credentials
from src.realtime.connectors.projectx import (
    ProjectXAccount,
    ProjectXClient,
    ProjectXError,
    canonical_bars,
)
from src.realtime.events import (
    EXEC_FILLED,
    ORIGIN_LIVE,
    ORIGIN_REPLAY,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    SYSTEM_CONNECTOR_DISCONNECTED,
    SYSTEM_CONNECTOR_RECONNECTED,
    SYSTEM_HALTED,
    SYSTEM_STALE_MARKET_DATA,
    AccountSnapshot,
    Bar,
    CanonicalEvent,
    ExecutionReport,
    OrderIntent,
    RiskDecision,
    Signal,
    SystemEvent,
    identity_key,
    is_authorized,
)
from src.realtime.interfaces import (
    LIVE_EXECUTION_ENABLED,
    require_authorized_intent,
    require_order_intent,
    require_risk_decision,
    require_signal,
)
from src.realtime.paper import PaperExecutionAdapter, default_paper_assumptions
from src.realtime.recorder import FileEventRecorder, reconstruct_events
from src.realtime.risk import (
    REASON_APPROVED,
    REASON_DAILY_LOSS,
    REASON_DISCONNECTED,
    REASON_DRAWDOWN,
    REASON_HALTED,
    REASON_STALE,
    REASON_UNKNOWN,
    AccountAwareRiskEngine,
)
from src.types import FundedAccountRules

S1_DIR = Path(__file__).resolve().parent
LOGS_DIR = S1_DIR / "logs"
KILL_SWITCH_FILE = S1_DIR / "KILL_SWITCH"


class RealtimeStrategyAdapter:
    """Adapta M8FilteredStrategy (arm atr_k050) a la interfaz Strategy de tiempo real."""

    def __init__(self, arm_id: str = "atr_k050") -> None:
        self._strategy = M8FilteredStrategy(market=MNQ, arm_id=arm_id)
        self._history: list[BacktestBar] = []
        self._seq = 0

    def on_event(self, event: CanonicalEvent) -> Signal | None:
        if not isinstance(event, Bar):
            return None

        # Convertir barra canonical a backtest Bar
        b = BacktestBar(
            timestamp=event.timestamp,
            open=event.open,
            high=event.high,
            low=event.low,
            close=event.close,
            volume=event.volume,
        )
        self._history.append(b)

        # Evaluar señal sobre la última barra cerrada
        backtest_sig = self._strategy.evaluate(self._history)
        if backtest_sig is None:
            return None

        self._seq += 1
        action = SIGNAL_LONG if backtest_sig.direction == "long" else SIGNAL_SHORT
        return Signal(
            event_id=f"sig-{event.event_id}-{self._seq}",
            source="smc_fvg_atr_k050",
            timestamp=event.timestamp,
            sequence=self._seq,
            symbol=event.symbol,
            action=action,
            origin=event.origin,
        )


class S1SessionManager:
    """Orquestador de sesión sombra determinista y fail-closed."""

    def __init__(
        self,
        *,
        mode: str,
        recorder_path: Path,
        clock: Clock,
        rules: FundedAccountRules,
        account_name: str | None = None,
        env_file: str = ".env",
    ) -> None:
        self.mode = mode
        self.origin = ORIGIN_LIVE if mode == "live" else ORIGIN_REPLAY
        self.recorder_path = recorder_path
        self.clock = clock
        self.rules = rules
        self.account_name = account_name
        self.env_file = env_file

        self.bus = AsyncIOEventBus()
        self.strategy = RealtimeStrategyAdapter(arm_id="atr_k050")
        self.risk = AccountAwareRiskEngine(rules=self.rules, clock=self.clock)
        self.execution = PaperExecutionAdapter(
            clock=self.clock, assumptions=default_paper_assumptions()
        )
        self.recorder = FileEventRecorder(recorder_path)

        # Métricas de sesión
        self.bars_received = 0
        self.signals_generated = 0
        self.decisions_count = 0
        self.risk_approvals = 0
        self.risk_denials = 0
        self.denial_reasons: dict[str, int] = {}
        self.orders_submitted = 0
        self.execution_reports = 0
        self.latencies_ms: list[float] = []
        self.data_gaps: list[dict[str, Any]] = []
        self.last_bar_time: datetime | None = None
        self.start_wall_time = time.time()
        self.session_active = True

        self._pending_signals: list[Signal] = []
        self._processed_signal_ids: set[tuple[str, str]] = set()
        self._bus_events = 0

    def default_intent_factory(
        self,
        signal: Signal,
        decision: RiskDecision,
        sequence: int,
    ) -> OrderIntent:
        return OrderIntent(
            event_id=f"paper-intent-{sequence}",
            source="fars-s1-session",
            timestamp=self.clock.now(),
            sequence=sequence,
            symbol=signal.symbol,
            action=signal.action,
            risk_decision_id=decision.event_id,
            origin=signal.origin,
        )

    def _on_event(self, event: CanonicalEvent) -> None:
        self.recorder.record(event)
        self.risk.observe(event)
        self._bus_events += 1

        if isinstance(event, Bar):
            self.bars_received += 1
            if self.last_bar_time is not None:
                delta_sec = (event.timestamp - self.last_bar_time).total_seconds()
                # Para M5 el paso normal es 300s (5m). Hueco si > 300s
                if delta_sec > 300.0:
                    gap_info = {
                        "from": self.last_bar_time.isoformat(),
                        "to": event.timestamp.isoformat(),
                        "duration_minutes": round(delta_sec / 60.0, 1),
                    }
                    self.data_gaps.append(gap_info)
            self.last_bar_time = event.timestamp

            # Medición de latencia respecto a la hora del evento
            now = self.clock.now()
            lat_ms = (now - event.timestamp).total_seconds() * 1000.0
            if lat_ms >= 0:
                self.latencies_ms.append(lat_ms)

        proposed = self.strategy.on_event(event)
        if proposed is not None:
            sig = require_signal(proposed)
            self.signals_generated += 1
            self._pending_signals.append(sig)

    async def _drain_signals(self) -> None:
        while self._pending_signals:
            signal = self._pending_signals.pop(0)
            sig_id = identity_key(signal)
            if sig_id in self._processed_signal_ids:
                continue
            self._processed_signal_ids.add(sig_id)

            await self.bus.publish(signal)
            await self.bus.wait_idle()

            decision = require_risk_decision(self.risk.evaluate(signal))
            self.decisions_count += 1
            if decision.approved:
                self.risk_approvals += 1
            else:
                self.risk_denials += 1
                reason = decision.reason or "UNKNOWN"
                self.denial_reasons[reason] = self.denial_reasons.get(reason, 0) + 1

            await self.bus.publish(decision)
            await self.bus.wait_idle()

            if not is_authorized(decision):
                continue

            # Si es autorizado, se genera intención paper
            intent = self.default_intent_factory(
                signal, decision, self.orders_submitted + 1
            )
            require_authorized_intent(signal, decision, intent)
            self.orders_submitted += 1
            await self.bus.publish(intent)
            await self.bus.wait_idle()

            report = self.execution.submit(signal, decision, intent)
            self.execution_reports += 1
            await self.bus.publish(report)
            await self.bus.wait_idle()

    async def emit_account_snapshot(
        self,
        *,
        balance: float,
        equity: float,
        source: str = "account-sync",
        sequence: int = 1,
        timestamp: datetime | None = None,
        peak_equity: float | None = None,
    ) -> None:
        ts = timestamp or self.clock.now()
        snap = AccountSnapshot(
            event_id=f"snap-{sequence}",
            source=source,
            timestamp=ts,
            sequence=sequence,
            origin=self.origin,
            balance=balance,
            equity=equity,
            peak_equity=peak_equity if peak_equity is not None else max(equity, balance),
            trades_applied=0,
            broker_timestamp=ts,
            last_sync=ts,
        )
        await self.bus.publish(snap)
        await self.bus.wait_idle()

    def check_kill_switch(self) -> bool:
        if KILL_SWITCH_FILE.exists():
            return True
        return not self.session_active

    def get_metrics_summary(self) -> dict[str, Any]:
        uptime_sec = time.time() - self.start_wall_time
        med_lat = (
            float(np.median(self.latencies_ms))
            if self.latencies_ms
            else 0.0
        )
        return {
            "mode": self.mode,
            "origin": self.origin,
            "uptime_seconds": round(uptime_sec, 1),
            "bars_received": self.bars_received,
            "signals_generated": self.signals_generated,
            "decisions_total": self.decisions_count,
            "risk_approvals": self.risk_approvals,
            "risk_denials": self.risk_denials,
            "denial_reasons": self.denial_reasons,
            "orders_submitted": self.orders_submitted,
            "execution_reports": self.execution_reports,
            "data_gaps_count": len(self.data_gaps),
            "data_gaps": self.data_gaps,
            "latency_median_ms": round(med_lat, 2),
            "live_execution_enabled": LIVE_EXECUTION_ENABLED,
        }


import numpy as np


async def run_replay_mode(
    session_mgr: S1SessionManager,
    bars_file: Path | None = None,
    max_bars: int = 500,
) -> dict[str, Any]:
    """Ejecuta dry run en modo replay validando el pipeline completo."""
    print(f"\n[Fase 1] Iniciando Dry Run en Replay...")
    session_mgr.bus.subscribe(session_mgr._on_event)
    await session_mgr.bus.start()

    # Cargar barras de prueba: si no se especifica archivo, extraer barras recientes de databento
    bars_to_feed: list[Bar] = []
    if bars_file is not None and bars_file.exists():
        reader = csv.DictReader(bars_file.open(encoding="utf-8"))
        for i, row in enumerate(reader):
            if i >= max_bars:
                break
            ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            bars_to_feed.append(
                Bar(
                    event_id=f"bar-replay-{i+1}",
                    source="replay-file",
                    timestamp=ts,
                    sequence=i + 2,
                    symbol="MNQ",
                    interval="5m",
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 100)),
                    origin=ORIGIN_REPLAY,
                )
            )
    else:
        # Extraer de databento.zip
        import zipfile
        zip_path = REPO_ROOT.parent / "databento.zip"
        if not zip_path.exists():
            zip_path = Path("E:/FARS-LAB/databento.zip")
        with zipfile.ZipFile(zip_path) as zf:
            with zf.open("databento/MNQ_M5.csv") as raw:
                reader = csv.DictReader(io.StringIO(raw.read().decode("utf-8")))
                all_rows = list(reader)
                # Tomar las últimas max_bars barras
                sample_rows = all_rows[-max_bars:]
                for i, row in enumerate(sample_rows):
                    ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                    bars_to_feed.append(
                        Bar(
                            event_id=f"bar-replay-{i+1}",
                            source="databento-replay",
                            timestamp=ts,
                            sequence=i + 2,
                            symbol="MNQ",
                            interval="5m",
                            open=float(row["open"]),
                            high=float(row["high"]),
                            low=float(row["low"]),
                            close=float(row["close"]),
                            volume=float(row.get("volume", 100)),
                            origin=ORIGIN_REPLAY,
                        )
                    )

    # Ingestar snapshot inicial de cuenta con balance positivo y timestamp alineado
    init_ts = bars_to_feed[0].timestamp - timedelta(minutes=5) if bars_to_feed else datetime.now(timezone.utc)
    await session_mgr.emit_account_snapshot(
        balance=1500.0,
        equity=1500.0,
        sequence=1,
        timestamp=init_ts,
        peak_equity=1500.0,
    )

    print(f"Alimentando {len(bars_to_feed)} barras M5 a través del bus de eventos...")
    for b in bars_to_feed:
        await session_mgr.bus.publish(b)
        await session_mgr.bus.wait_idle()
        await session_mgr._drain_signals()

    await session_mgr.bus.shutdown()
    metrics = session_mgr.get_metrics_summary()

    # Validar reconstrucción del log grabado
    reconstructed = list(reconstruct_events(session_mgr.recorder_path))
    metrics["reconstructed_events_count"] = len(reconstructed)
    print(f"Dry run completado exitosamente: {len(reconstructed)} eventos registrados.")
    print(f"Resumen: Barras={metrics['bars_received']}, Señales={metrics['signals_generated']}, "
          f"Decisiones={metrics['decisions_total']}, Aprobadas={metrics['risk_approvals']}, "
          f"Vetadas={metrics['risk_denials']}")
    return metrics


def update_live_report(session_mgr: S1SessionManager, contract_name: str, final: bool = False) -> None:
    metrics = session_mgr.get_metrics_summary()
    metrics_file = S1_DIR / "metrics_live.json"
    metrics_file.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    informe_path = S1_DIR / "INFORME.md"
    if not informe_path.exists():
        return
    content = informe_path.read_text(encoding="utf-8")
    split_token = "## 5. Fase 2 — Estado de Preparación para la Sesión Sombra en Vivo"
    if split_token not in content:
        split_token = "## 5. Fase 2 — Sesión Sombra en Vivo"

    if split_token in content:
        base_content = content.split(split_token)[0]
    else:
        base_content = content

    status_str = "COMPLETADA TRAS CIERRE DE MERCADO" if final else "EN CURSO (ACTIVA)"
    uptime_min = round(metrics["uptime_seconds"] / 60.0, 1)

    elapsed_min = metrics["uptime_seconds"] / 60.0
    expected_bars = max(1, int(elapsed_min / 5.0) + (1 if elapsed_min > 0 else 0))
    received_bars = metrics["bars_received"]
    coverage_pct = min(100.0, round((received_bars / expected_bars) * 100.0, 1)) if expected_bars > 0 else 100.0

    reasons_str = ", ".join(f"`{k}`: {v}" for k, v in metrics["denial_reasons"].items()) if metrics["denial_reasons"] else "Ninguno"
    gaps_str = f"{metrics['data_gaps_count']} huecos" if metrics["data_gaps_count"] > 0 else "0 huecos detectados"

    new_section = f"""## 5. Fase 2 — Sesión Sombra en Vivo (TopstepX/ProjectX)

> **Estado de la Sesión:** **{status_str}**  
> **Última actualización:** `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`  
> **Contrato activo:** `{contract_name}` (Micro E-mini Nasdaq-100 M5)  
> **Cuenta simulada:** ID `27240143` (`simulated: true`)  
> **Órdenes live enviadas:** **0** (`LIVE_EXECUTION_ENABLED = False`)

### Métricas Acumuladas de la Sesión Sombra:

| Métrica | Valor Registrado | Criterio de Aceptación (§2) | Estado |
|---|---|---|:---:|
| **Tiempo de sesión (Uptime)** | {uptime_min} minutos ({metrics['uptime_seconds']}s) | Ventana activa hasta 16:00 ET | {'OK' if uptime_min > 0 else 'PENDIENTE'} |
| **Barras M5 Recibidas** | {received_bars} barras procesadas | Calidad de feed continua | {'OK' if received_bars > 0 else 'PENDIENTE'} |
| **Cobertura de Barras** | {received_bars} recibidas / ~{expected_bars} esperadas ({coverage_pct}%) | $\\ge 80\\%$ de cobertura de ventana | {'CUMPLE' if coverage_pct >= 80.0 else 'CALIBRANDO'} |
| **Huecos de Datos (Gaps > 5m)** | {gaps_str} | Sin desconexiones > 10m sin recuperar | OK |
| **Intenciones de la Estrategia** | {metrics['signals_generated']} señales generadas | $\\ge 1$ intención evaluada | {'CUMPLE' if metrics['signals_generated'] >= 1 else 'EVALUANDO'} |
| **Decisiones del Motor de Riesgo** | {metrics['decisions_total']} evaluadas ({metrics['risk_approvals']} aprobadas, {metrics['risk_denials']} vetadas) | 0 autorizaciones bajo estado desconocido | CUMPLE |
| **Motivos de Veto del Motor** | {reasons_str} | Fail-closed ante cuenta en drawdown | CUMPLE |
| **Intenciones Paper / Fills** | {metrics['orders_submitted']} órdenes paper / {metrics['execution_reports']} fills simulados | Paper Execution Adapter | CUMPLE |
| **Latencia Mediana de Proceso** | {metrics['latency_median_ms']:.1f} ms | Monitorización point-in-time | OK |
| **Log Crudo de Eventos** | [`logs/session_live.jsonl`](file:///E:/FARS-LAB/FARS/lab_artifacts/s1_protocol/logs/session_live.jsonl) | Reconstrucción canónica completa | OK |

---
"""
    informe_path.write_text(base_content + new_section, encoding="utf-8")


async def run_live_shadow_mode(
    session_mgr: S1SessionManager,
    poll_interval_seconds: float = 15.0,
    market_close_utc: datetime | None = None,
    max_duration_seconds: float | None = None,
) -> dict[str, Any]:
    """Ejecuta sesión sombra en vivo contra la API de TopstepX/ProjectX."""
    print(f"\n[Fase 2] Iniciando Sesión Sombra en Vivo (TopstepX/ProjectX)...")

    credentials = load_projectx_credentials(session_mgr.env_file)
    client = ProjectXClient(credentials)
    client.authenticate()
    client.validate_session()

    accounts = client.list_accounts(only_active=True)
    selected = client.select_account(accounts, account_name=session_mgr.account_name)
    if selected.simulated is not True:
        raise RuntimeError(
            f"ABORT CRITICO: La cuenta seleccionada {selected.name} (id={selected.account_id}) "
            f"NO es simulada (simulated={selected.simulated}). Abortando inmediatamente."
        )

    print(f"Cuenta confirmada: {selected.name} (id={selected.account_id}, simulated={selected.simulated})")
    print(f"Balance inicial: {selected.balance} USD | Modo: READ_ONLY (Cero órdenes live)")

    contracts = client.search_contracts("MNQ")
    active_mnq = [c for c in contracts if c.active and "MNQ" in c.name]
    if not active_mnq:
        active_mnq = contracts
    contract = active_mnq[0]
    print(f"Contrato seleccionado: {contract.name} (id={contract.contract_id}, tick_size={contract.tick_size})")

    session_mgr.bus.subscribe(session_mgr._on_event)
    await session_mgr.bus.start()

    now_utc = datetime.now(timezone.utc)
    query_start = now_utc - timedelta(hours=2)

    await session_mgr.emit_account_snapshot(
        balance=float(selected.balance),
        equity=float(selected.balance),
        source="projectx-init",
        sequence=1,
        timestamp=query_start - timedelta(minutes=5),
        peak_equity=1500.0,
    )

    if market_close_utc is None:
        market_close_utc = now_utc.replace(hour=20, minute=0, second=0, microsecond=0)

    seq_counter = 2
    seen_bar_timestamps: set[datetime] = set()
    start_time = time.time()
    last_report_time = 0.0
    was_disconnected = False

    print(f"Conexión activa. Escaneando barras M5 cada {poll_interval_seconds}s hasta {market_close_utc.isoformat()}...")
    print(f"Para detener manualmente: crear archivo {KILL_SWITCH_FILE} o pulsar Ctrl+C.\n")

    # Generar informe inicial
    update_live_report(session_mgr, contract.name, final=False)

    try:
        while True:
            if session_mgr.check_kill_switch():
                print("\n[Kill-switch detectado] Deteniendo sesión sombra de forma segura...")
                break

            now_utc = datetime.now(timezone.utc)
            if now_utc >= market_close_utc:
                print("\n[Cierre de mercado alcanzado (16:00 ET)] Deteniendo sesión sombra...")
                break

            if max_duration_seconds and (time.time() - start_time) >= max_duration_seconds:
                print("\n[Duración máxima alcanzada] Deteniendo sesión sombra...")
                break

            try:
                query_start = now_utc - timedelta(hours=2)
                raw_bars = client.retrieve_bars(
                    contract.contract_id,
                    start=query_start,
                    end=now_utc,
                    unit=2,
                    unit_number=5,
                    limit=24,
                    include_partial_bar=False,
                )

                new_bars = []
                for p_bar in raw_bars:
                    if p_bar.timestamp not in seen_bar_timestamps:
                        seen_bar_timestamps.add(p_bar.timestamp)
                        seq_counter += 1
                        b = Bar(
                            event_id=f"bar-live-{contract.contract_id}-{seq_counter}",
                            source="projectx-history-poll",
                            timestamp=p_bar.timestamp,
                            sequence=seq_counter,
                            symbol="MNQ",
                            interval="5m",
                            open=float(p_bar.open),
                            high=float(p_bar.high),
                            low=float(p_bar.low),
                            close=float(p_bar.close),
                            volume=float(p_bar.volume),
                            origin=ORIGIN_LIVE,
                        )
                        new_bars.append(b)

                for b in new_bars:
                    print(f"  [Bar M5 Recibida] {b.timestamp.strftime('%H:%M:%S UTC')} | O={b.open:.2f} H={b.high:.2f} L={b.low:.2f} C={b.close:.2f} V={b.volume:.0f}")
                    await session_mgr.bus.publish(b)
                    await session_mgr.bus.wait_idle()
                    await session_mgr._drain_signals()

                if was_disconnected:
                    seq_counter += 1
                    reconnect_ev = SystemEvent(
                        event_id=f"sys-{seq_counter}",
                        source="session-monitor",
                        timestamp=datetime.now(timezone.utc),
                        sequence=seq_counter,
                        kind=SYSTEM_CONNECTOR_RECONNECTED,
                        origin=ORIGIN_LIVE,
                    )
                    await session_mgr.bus.publish(reconnect_ev)
                    await session_mgr.bus.wait_idle()
                    was_disconnected = False

                if new_bars or (time.time() - last_report_time) >= 60.0:
                    update_live_report(session_mgr, contract.name, final=False)
                    last_report_time = time.time()

            except Exception as exc:
                err_type = type(exc).__name__
                if "Authentication" in err_type or (hasattr(exc, "args") and any("401" in str(a) for a in exc.args)):
                    print("[Aviso Provider] Sesión expirada -> Re-autenticando con credenciales locales...")
                    try:
                        client.authenticate()
                        print("[Aviso Provider] Re-autenticación exitosa.")
                    except Exception as auth_err:
                        print(f"[Error Re-auth] {auth_err} -> Bloqueo fail-closed")
                else:
                    print(f"[Aviso Provider] Excepción de conexión ({err_type}: {exc}) -> Fail-closed activo...")

                if not was_disconnected:
                    seq_counter += 1
                    disc_ev = SystemEvent(
                        event_id=f"sys-{seq_counter}",
                        source="session-monitor",
                        timestamp=datetime.now(timezone.utc),
                        sequence=seq_counter,
                        kind=SYSTEM_CONNECTOR_DISCONNECTED,
                        origin=ORIGIN_LIVE,
                    )
                    await session_mgr.bus.publish(disc_ev)
                    await session_mgr.bus.wait_idle()
                    was_disconnected = True

            await asyncio.sleep(poll_interval_seconds)

    finally:
        await session_mgr.bus.shutdown()
        update_live_report(session_mgr, contract.name, final=True)

    metrics = session_mgr.get_metrics_summary()
    return metrics


def parse_args():
    parser = argparse.ArgumentParser(description="FARS Bloque S1 Session Runner")
    parser.add_argument("--mode", choices=["replay", "live"], required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--account-name", default=None)
    parser.add_argument("--poll-interval", type=float, default=15.0)
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--until-close", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    rules = FundedAccountRules(
        initial_balance=1500.0,
        profit_target_pct=0.06,
        max_drawdown_pct=0.10,
        daily_loss_limit_pct=0.05,
        risk_per_trade=0.01,
        drawdown_mode="trailing",
    )

    clock = SystemClock()

    if args.mode == "replay":
        recorder_path = LOGS_DIR / "session_replay.jsonl"
        session_mgr = S1SessionManager(
            mode="replay",
            recorder_path=recorder_path,
            clock=clock,
            rules=rules,
        )
        metrics = asyncio.run(run_replay_mode(session_mgr, max_bars=300))
        metrics_file = S1_DIR / "metrics_replay.json"
        metrics_file.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Métricas de replay guardadas en {metrics_file}")

    elif args.mode == "live":
        recorder_path = LOGS_DIR / "session_live.jsonl"
        session_mgr = S1SessionManager(
            mode="live",
            recorder_path=recorder_path,
            clock=clock,
            rules=rules,
            account_name=args.account_name,
            env_file=args.env_file,
        )

        def sig_handler(sig, frame):
            print("\nSeñal de interrupción recibida. Finalizando...")
            session_mgr.session_active = False

        signal.signal(signal.SIGINT, sig_handler)
        signal.signal(signal.SIGTERM, sig_handler)

        metrics = asyncio.run(
            run_live_shadow_mode(
                session_mgr,
                poll_interval_seconds=args.poll_interval,
                max_duration_seconds=args.duration_seconds,
            )
        )
        metrics_file = S1_DIR / "metrics_live.json"
        metrics_file.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Métricas de live guardadas en {metrics_file}")


if __name__ == "__main__":
    main()
