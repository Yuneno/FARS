"""Bridge desacoplado entre ZoneEngine y el protocolo de estrategias de backtest (Z5).

Proporciona:
- ZoneContextProvider: alimenta ZoneEngine incrementalmente con barras cerradas
  y expone el contexto de zonas de forma causal.
- ZoneFilter: especificación declarativa de predicados sobre el contexto.
- ZoneDecisionRecord: snapshot auditable de cada decisión tomada o rechazada.
- ZoneFilteredStrategy: decorador universal para cualquier Strategy que filtra
  señales point-in-time sin alterar su ejecución ni violar el baseline.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from src.backtest.history import Bar
from src.backtest.strategy import Signal, Strategy
from src.zones.engine import ZoneEngine


@dataclass(frozen=True)
class ZoneDecisionRecord:
    """Registro inmutable de una decisión de filtrado evaluada en una barra."""

    timestamp: datetime
    price: float
    direction: Literal["long", "short"]
    verdict: bool  # True = aceptada, False = rechazada por filtro
    filter_name: str
    context: dict[str, Any]
    anchor_meta: dict[str, Any] = field(default_factory=dict)
    signal: Signal | None = None


class ZoneContextProvider:
    """Proveedor causal de contexto de zonas para estrategias en backtest.

    Envuelve un ZoneEngine, garantizando actualización incremental append-only
    (a lo sumo una vez por barra) y acceso point-in-time a las features de zona.
    """

    def __init__(
        self,
        engine: ZoneEngine | None = None,
        symbol: str = "MNQ",
        timeframe: str = "5m",
        session_pools: bool = True,
        session_tick_tolerance: int = 4,
        session_atr_tolerance: float = 0.10,
        d20_sessions: int = 20,
        cache: dict[datetime, dict[str, Any]] | None = None,
        anchor_cache: dict[datetime, dict[str, Any]] | None = None,
        **engine_kwargs: Any,
    ) -> None:
        self._cache = cache
        self._anchor_cache = anchor_cache
        self._last_seen_bar_ts: datetime | None = None

        if engine is not None:
            self.engine = engine
            self.symbol = engine.symbol
            self.timeframe = engine.timeframe
            self.session_pools = engine.session_pools
        else:
            self.symbol = symbol
            self.timeframe = timeframe
            self.session_pools = session_pools
            self.engine = ZoneEngine(
                symbol=symbol,
                timeframe=timeframe,
                session_pools=session_pools,
                session_tick_tolerance=session_tick_tolerance,
                session_atr_tolerance=session_atr_tolerance,
                d20_sessions=d20_sessions,
                **engine_kwargs,
            )

        self._last_seen_len: int = 0
        self._update_calls_count: int = 0
        self._bars_processed_count: int = 0

    @property
    def update_calls_count(self) -> int:
        return self._update_calls_count

    @property
    def bars_processed_count(self) -> int:
        return self._bars_processed_count

    def update(self, history: Sequence[Bar]) -> None:
        """Actualiza el motor incrementalmente con barras cerradas."""
        self._update_calls_count += 1
        n = len(history)
        if n < self._last_seen_len:
            raise ValueError(
                f"Contrato append-only violado: history encogió ({n} < {self._last_seen_len})"
            )
        if n == self._last_seen_len:
            # Misma longitud de historia: ya procesada
            return

        new_count = n - self._last_seen_len
        if self._cache is None:
            self.engine.update(history)
        self._bars_processed_count += new_count
        self._last_seen_len = n
        if history:
            self._last_seen_bar_ts = history[-1].timestamp

    def context(self, price: float, atr: float | None = None) -> dict[str, Any]:
        """Obtiene el diccionario de contexto point-in-time del motor."""
        if self._cache is not None and self._last_seen_bar_ts in self._cache:
            return self._cache[self._last_seen_bar_ts]
        return self.engine.context(price=price, atr=atr)

    def context_at_decision(self, bar: Bar, atr: float | None = None) -> dict[str, Any]:
        """Obtiene el contexto evaluado al precio de cierre de la barra de decisión."""
        if self._cache is not None and bar.timestamp in self._cache:
            return self._cache[bar.timestamp]
        return self.context(price=bar.close, atr=atr)

    def get_anchor_metadata(self) -> dict[str, Any]:
        """Retorna metadatos de disponibilidad causal de anclajes de sesión."""
        if self._anchor_cache is not None and self._last_seen_bar_ts in self._anchor_cache:
            return self._anchor_cache[self._last_seen_bar_ts]

        meta: dict[str, Any] = {}
        builder = getattr(self.engine, "_session_builder", None)
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
        return meta


def precompute_fold_cache(
    bars: Sequence[Bar],
    symbol: str = "MNQ",
    timeframe: str = "5m",
    session_pools: bool = True,
    session_tick_tolerance: int = 4,
    session_atr_tolerance: float = 0.10,
    d20_sessions: int = 20,
) -> tuple[dict[datetime, dict[str, Any]], dict[datetime, dict[str, Any]]]:
    """Pre-calcula el contexto y metadatos causales de zonas barra a barra para una secuencia.

    Acelera múltiples corridas sobre el mismo conjunto de barras sin alterar
    en un solo bit la estricta causalidad point-in-time.
    """
    engine = ZoneEngine(
        symbol=symbol,
        timeframe=timeframe,
        session_pools=session_pools,
        session_tick_tolerance=session_tick_tolerance,
        session_atr_tolerance=session_atr_tolerance,
        d20_sessions=d20_sessions,
    )
    ctx_cache: dict[datetime, dict[str, Any]] = {}
    anchor_cache: dict[datetime, dict[str, Any]] = {}
    growing: list[Bar] = []

    for b in bars:
        growing.append(b)
        engine.update(growing)
        ctx_cache[b.timestamp] = engine.context(price=b.close)
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

    return ctx_cache, anchor_cache


PredicateFn = Callable[[dict[str, Any], Signal], bool]


@dataclass(frozen=True)
class ZoneFilter:
    """Predicado declarativo nombrado sobre el contexto de zonas y la señal."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)
    predicate: PredicateFn = field(default=lambda ctx, sig: True)


# ---------------------------------------------------------------------------
# Catálogo normativo de filtros de zona
# ---------------------------------------------------------------------------


def make_always_true_filter() -> ZoneFilter:
    """Filtro trivial (siempre True): garantía anti-fraude bit a bit vs baseline."""
    return ZoneFilter(name="always_true", params={}, predicate=lambda ctx, sig: True)


def make_inside_pdr_filter() -> ZoneFilter:
    """Filtra para permitir entradas solo cuando el precio está dentro del rango del día anterior."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        val = ctx.get("inside_prev_day_range")
        return bool(val is True)

    return ZoneFilter(name="inside_prev_day_range", params={}, predicate=_pred)


def make_pdr_position_filter(threshold: float = 0.5) -> ZoneFilter:
    """Filtro de régimen premium/discount: Longs en discount (< threshold), Shorts en premium (>= threshold)."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        pos = ctx.get("prev_day_range_position")
        if pos is None:
            return False
        if sig.direction == "long":
            return bool(pos < threshold)
        return bool(pos >= threshold)

    return ZoneFilter(
        name="prev_day_range_position",
        params={"threshold": threshold},
        predicate=_pred,
    )


def make_dist_onl_atr_filter(max_atr: float = 1.0) -> ZoneFilter:
    """Longs solo cuando la distancia a Overnight Low es <= max_atr."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction != "long":
            return True
        d = ctx.get("distance_to_overnight_low_atr")
        return bool(d is not None and d <= max_atr)

    return ZoneFilter(
        name="distance_to_overnight_low_atr",
        params={"max_atr": max_atr, "direction": "long"},
        predicate=_pred,
    )


def make_dist_onh_atr_filter(max_atr: float = 1.0) -> ZoneFilter:
    """Shorts solo cuando la distancia a Overnight High es <= max_atr."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction != "short":
            return True
        d = ctx.get("distance_to_overnight_high_atr")
        return bool(d is not None and d <= max_atr)

    return ZoneFilter(
        name="distance_to_overnight_high_atr",
        params={"max_atr": max_atr, "direction": "short"},
        predicate=_pred,
    )


def make_on_swept_pdl_filter() -> ZoneFilter:
    """Longs solo si en overnight se barrió el Prev Day Low."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction != "long":
            return True
        val = ctx.get("overnight_swept_prev_day_low")
        return bool(val is True)

    return ZoneFilter(
        name="overnight_swept_prev_day_low",
        params={"direction": "long"},
        predicate=_pred,
    )


def make_on_swept_pdh_filter() -> ZoneFilter:
    """Shorts solo si en overnight se barrió el Prev Day High."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction != "short":
            return True
        val = ctx.get("overnight_swept_prev_day_high")
        return bool(val is True)

    return ZoneFilter(
        name="overnight_swept_prev_day_high",
        params={"direction": "short"},
        predicate=_pred,
    )


def make_dist_sellside_liq_filter(max_atr: float = 1.0) -> ZoneFilter:
    """Longs solo cuando la distancia a sellside liquidity es <= max_atr."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction != "long":
            return True
        d = ctx.get("distance_to_sellside_liquidity_atr")
        return bool(d is not None and d <= max_atr)

    return ZoneFilter(
        name="distance_to_sellside_liquidity_atr",
        params={"max_atr": max_atr, "direction": "long"},
        predicate=_pred,
    )


def make_dist_buyside_liq_filter(max_atr: float = 1.0) -> ZoneFilter:
    """Shorts solo cuando la distancia a buyside liquidity es <= max_atr."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction != "short":
            return True
        d = ctx.get("distance_to_buyside_liquidity_atr")
        return bool(d is not None and d <= max_atr)

    return ZoneFilter(
        name="distance_to_buyside_liquidity_atr",
        params={"max_atr": max_atr, "direction": "short"},
        predicate=_pred,
    )


def make_inside_fvg_filter() -> ZoneFilter:
    """Entrada dentro de FVG alineado por dirección."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if sig.direction == "long":
            return bool(ctx.get("inside_bullish_fvg") is True)
        return bool(ctx.get("inside_bearish_fvg") is True)

    return ZoneFilter(name="inside_fvg", params={}, predicate=_pred)


def make_liquidity_swept_filter() -> ZoneFilter:
    """Entrada requiere liquidity_swept == True."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        return bool(ctx.get("liquidity_swept") is True)

    return ZoneFilter(name="liquidity_swept", params={}, predicate=_pred)


def make_fvg_liquidity_overlap_filter() -> ZoneFilter:
    """Entrada requiere confluencia geométrica FVG y Liquidez (fvg_liquidity_overlap == True)."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        return bool(ctx.get("fvg_liquidity_overlap") is True)

    return ZoneFilter(name="fvg_liquidity_overlap", params={}, predicate=_pred)


def make_combo_pdr_and_on_sweep_filter() -> ZoneFilter:
    """Combinación: inside_prev_day_range Y overnight_swept alineado."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        if not ctx.get("inside_prev_day_range"):
            return False
        if sig.direction == "long":
            return bool(ctx.get("overnight_swept_prev_day_low") is True)
        return bool(ctx.get("overnight_swept_prev_day_high") is True)

    return ZoneFilter(name="combo_pdr_and_on_sweep", params={}, predicate=_pred)


def make_combo_fvg_and_liquidity_filter() -> ZoneFilter:
    """Combinación: inside_fvg Y liquidity_swept."""
    def _pred(ctx: dict[str, Any], sig: Signal) -> bool:
        inside = (
            ctx.get("inside_bullish_fvg") if sig.direction == "long" else ctx.get("inside_bearish_fvg")
        )
        swept = ctx.get("liquidity_swept")
        return bool(inside and swept)

    return ZoneFilter(name="combo_fvg_and_liquidity", params={}, predicate=_pred)


def build_filter(name: str, params: dict[str, Any] | None = None) -> ZoneFilter:
    """Factory de filtros nombrados según el catálogo preregistrado."""
    p = params or {}
    registry: dict[str, Callable[[], ZoneFilter]] = {
        "always_true": make_always_true_filter,
        "inside_prev_day_range": make_inside_pdr_filter,
        "prev_day_range_position": lambda: make_pdr_position_filter(p.get("threshold", 0.5)),
        "distance_to_overnight_low_atr": lambda: make_dist_onl_atr_filter(p.get("max_atr", 1.0)),
        "distance_to_overnight_high_atr": lambda: make_dist_onh_atr_filter(p.get("max_atr", 1.0)),
        "overnight_swept_prev_day_low": make_on_swept_pdl_filter,
        "overnight_swept_prev_day_high": make_on_swept_pdh_filter,
        "distance_to_sellside_liquidity_atr": lambda: make_dist_sellside_liq_filter(p.get("max_atr", 1.0)),
        "distance_to_buyside_liquidity_atr": lambda: make_dist_buyside_liq_filter(p.get("max_atr", 1.0)),
        "inside_fvg": make_inside_fvg_filter,
        "liquidity_swept": make_liquidity_swept_filter,
        "fvg_liquidity_overlap": make_fvg_liquidity_overlap_filter,
        "combo_pdr_and_on_sweep": make_combo_pdr_and_on_sweep_filter,
        "combo_fvg_and_liquidity": make_combo_fvg_and_liquidity_filter,
    }
    if name not in registry:
        raise ValueError(f"Filtro de zona desconocido: {name!r}. Disponibles: {list(registry.keys())}")
    return registry[name]()


class ZoneFilteredStrategy:
    """Decorador de estrategia que aplica un filtro de zona causal (Z5).

    Cumple con el protocolo Strategy (evaluate(history) -> Signal | None).
    Delega de forma transparente cualquier método duck-typed hacia la estrategia interna
    (p. ej. note_trade, decisions, observe_closed_bar, set_execution_state).
    """

    def __init__(
        self,
        inner: Strategy,
        provider: ZoneContextProvider,
        filter_spec: ZoneFilter | None = None,
        log: list[ZoneDecisionRecord] | None = None,
    ) -> None:
        self.inner = inner
        self.provider = provider
        self.filter_spec = filter_spec
        self.log: list[ZoneDecisionRecord] = log if log is not None else []

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None

        # 1. Actualiza el provider con la última barra cerrada (una sola vez)
        self.provider.update(history)

        # 2. Solicita la señal a la estrategia interna
        signal = self.inner.evaluate(history)
        if signal is None:
            return None

        # 3. Evalúa el filtro sobre el contexto de la barra de decisión
        decision_bar = history[-1]
        ctx = self.provider.context_at_decision(decision_bar)

        passed = True
        if self.filter_spec is not None:
            passed = bool(self.filter_spec.predicate(ctx, signal))

        # 4. Registra la decisión en el log de auditoría
        anchor_meta = self.provider.get_anchor_metadata()
        record = ZoneDecisionRecord(
            timestamp=decision_bar.timestamp,
            price=decision_bar.close,
            direction=signal.direction,
            verdict=passed,
            filter_name=self.filter_spec.name if self.filter_spec else "always_true",
            context=dict(ctx),
            anchor_meta=anchor_meta,
            signal=signal,
        )
        self.log.append(record)

        # 5. Emite la señal intacta si pasó el filtro; None si fue rechazada
        if not passed:
            return None
        return signal

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier atributo o método no implementado hacia la estrategia interna."""
        return getattr(self.inner, name)


__all__ = [
    "PredicateFn",
    "ZoneContextProvider",
    "ZoneDecisionRecord",
    "ZoneFilter",
    "ZoneFilteredStrategy",
    "build_filter",
    "precompute_fold_cache",
    "make_always_true_filter",
    "make_combo_fvg_and_liquidity_filter",
    "make_combo_pdr_and_on_sweep_filter",
    "make_dist_buyside_liq_filter",
    "make_dist_onh_atr_filter",
    "make_dist_onl_atr_filter",
    "make_dist_sellside_liq_filter",
    "make_fvg_liquidity_overlap_filter",
    "make_inside_fvg_filter",
    "make_inside_pdr_filter",
    "make_liquidity_swept_filter",
    "make_on_swept_pdh_filter",
    "make_on_swept_pdl_filter",
    "make_pdr_position_filter",
]
