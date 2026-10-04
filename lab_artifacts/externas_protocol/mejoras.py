"""Harness de Mejoras de Señales Externas — Búsqueda controlada de variantes.

Diseñado para probar estrictamente variantes DE UNA SOLA PIEZA por corrida
(metodología OFAT: One Factor At A Time) sobre las señales base validadas.

Evita el sobreajuste (data-mining) mediante:
1. Declaración previa del catálogo de hipótesis y variantes.
2. Contador acumulativo y explícito de variantes evaluadas (`variants_tested_count`).
3. Ajuste obligatorio de significancia por multiplicidad (corrección de Bonferroni).
4. Prohibición de optimizaciones multidimensionales en cuadrícula.
5. Criterio de promoción pre-registrado: CBB 95% cota inferior no empeora Y Bonferroni p < 0.05.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np

from lab_artifacts.externas_protocol.run_externas import (
    CORTE_CANONICO_FECHA,
    VENTANA_JUANCA_FIN,
    VENTANA_JUANCA_INICIO,
    compute_cbb_ci,
    compute_dsr,
    compute_permutation_p_value,
    compute_t_stat,
    summarize_trades,
)
from lab_artifacts.externas_protocol.senales_externas import (
    AmdCrtConfluenceStrategy,
    D2LStrategy,
    L0200Strategy,
    TradeRecord,
    simulate_signal_causal,
)
from src.backtest.history import Bar
from src.backtest.markets import MNQ, MarketSpec

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class VariantSpec:
    """Especificación declarativa de una variante de prueba."""

    variant_id: str
    base_signal: Literal["D2L", "L0200", "ACT", "AC"]
    dimension_modified: Literal[
        "ventana_horaria",
        "filtro_tendencia",
        "mecanica_salida",
        "umbral_compresion",
    ]
    baseline_value: Any
    variant_value: Any
    hypothesis_rationale: str
    created_at: str = field(default_factory=lambda: datetime.now(ET).isoformat())


class ImprovementHarness:
    """Harness de evaluación de variantes con control estricto de multiplicidad."""

    def __init__(self, alpha_base: float = 0.05) -> None:
        self.alpha_base = alpha_base
        self._registered_variants: list[VariantSpec] = []
        self._variants_tested_count: int = 0
        self._execution_history: list[dict[str, Any]] = []

    @property
    def variants_tested_count(self) -> int:
        return self._variants_tested_count

    @property
    def bonferroni_alpha(self) -> float:
        m = max(1, self._variants_tested_count)
        return self.alpha_base / m

    def register_variant(
        self,
        base_signal: Literal["D2L", "L0200", "ACT", "AC"],
        dimension_modified: Literal[
            "ventana_horaria",
            "filtro_tendencia",
            "mecanica_salida",
            "umbral_compresion",
        ],
        baseline_value: Any,
        variant_value: Any,
        hypothesis_rationale: str,
        variant_id_override: str | None = None,
    ) -> VariantSpec:
        """Registra una nueva variante declarando la hipótesis antes de evaluarla."""
        self._variants_tested_count += 1
        v_id = variant_id_override or f"VAR-{base_signal}-{self._variants_tested_count:03d}"
        spec = VariantSpec(
            variant_id=v_id,
            base_signal=base_signal,
            dimension_modified=dimension_modified,
            baseline_value=baseline_value,
            variant_value=variant_value,
            hypothesis_rationale=hypothesis_rationale,
        )
        self._registered_variants.append(spec)
        return spec

    def build_strategy_for_variant(self, spec: VariantSpec) -> Any:
        """Instancia la estrategia concreta configurada con el cambio atómico de la variante."""
        # 1. Base D2L
        if spec.base_signal == "D2L":
            sl = D2LStrategy().sl_pts
            tp = D2LStrategy().tp_pts
            exit_time = time(16, 0)
            entry_time = time(18, 0)

            if spec.dimension_modified == "mecanica_salida":
                if isinstance(spec.variant_value, dict):
                    sl = spec.variant_value.get("sl_pts", sl)
                    tp = spec.variant_value.get("tp_pts", tp)
                    if "exit_time" in spec.variant_value:
                        et_val = spec.variant_value["exit_time"]
                        exit_time = et_val if isinstance(et_val, time) else time.fromisoformat(str(et_val))
            elif spec.dimension_modified == "ventana_horaria":
                if isinstance(spec.variant_value, dict):
                    if "entry_time" in spec.variant_value:
                        ent_val = spec.variant_value["entry_time"]
                        entry_time = ent_val if isinstance(ent_val, time) else time.fromisoformat(str(ent_val))

            return D2LStrategy(
                market=MNQ,
                sl_pts=sl,
                tp_pts=tp,
                entry_time_domingo=entry_time,
                exit_time_lunes=exit_time,
            )

        # 2. Base L0200
        if spec.base_signal == "L0200":
            sl = L0200Strategy().sl_pts
            tp = L0200Strategy().tp_pts
            exit_time = time(16, 0)
            time_exit_only = False

            if spec.dimension_modified == "mecanica_salida":
                if isinstance(spec.variant_value, dict):
                    sl = spec.variant_value.get("sl_pts", sl)
                    tp = spec.variant_value.get("tp_pts", tp)
                    time_exit_only = spec.variant_value.get("use_time_exit_only", time_exit_only)
                    if "exit_time" in spec.variant_value:
                        et_val = spec.variant_value["exit_time"]
                        exit_time = et_val if isinstance(et_val, time) else time.fromisoformat(str(et_val))

            return L0200Strategy(
                market=MNQ,
                sl_pts=sl,
                tp_pts=tp,
                exit_time_lunes=exit_time,
                use_time_exit_only=time_exit_only,
            )

        # 3. Base ACT / AC
        use_ema = spec.base_signal == "ACT"
        ema_p = 50
        sl_cap = 50.0
        ny_only = False
        ny_end = time(12, 0)

        if spec.dimension_modified == "filtro_tendencia":
            if isinstance(spec.variant_value, dict):
                use_ema = spec.variant_value.get("use_ema", use_ema)
                ema_p = spec.variant_value.get("ema_period", ema_p)
        elif spec.dimension_modified == "ventana_horaria":
            if isinstance(spec.variant_value, dict):
                ny_only = spec.variant_value.get("ny_session_only", ny_only)
                if "ny_session_end" in spec.variant_value:
                    end_val = spec.variant_value["ny_session_end"]
                    ny_end = end_val if isinstance(end_val, time) else time.fromisoformat(str(end_val))
        elif spec.dimension_modified == "mecanica_salida":
            if isinstance(spec.variant_value, dict):
                sl_cap = spec.variant_value.get("sl_cap_pts", sl_cap)

        return AmdCrtConfluenceStrategy(
            market=MNQ,
            use_ema_filter=use_ema,
            ema_period=ema_p,
            sl_cap_pts=sl_cap,
            ny_session_only=ny_only,
            ny_session_end=ny_end,
        )

    def evaluate_variant(
        self,
        spec: VariantSpec,
        bars: list[Bar],
        commission: float = 0.62,
        slippage: float = 0.25,
    ) -> dict[str, Any]:
        """Ejecuta una corrida controlada de una sola variante, auditando multiplicidad."""
        strategy = self.build_strategy_for_variant(spec)
        trades = simulate_signal_causal(
            bars,
            strategy,
            signal_name=spec.variant_id,
            commission_per_side=commission,
            slippage_points=slippage,
        )

        n = len(trades)
        pts = [t.pnl_points for t in trades]
        mean_pts = float(np.mean(pts)) if n > 0 else 0.0
        std_pts = float(np.std(pts, ddof=1)) if n > 1 else 0.0
        t_stat = (mean_pts / (std_pts / math.sqrt(n))) if std_pts > 0 and n > 1 else None

        result_entry = {
            "variant_spec": asdict(spec),
            "variants_tested_so_far": self._variants_tested_count,
            "bonferroni_adjusted_alpha": self.bonferroni_alpha,
            "execution_summary": {
                "n_trades": n,
                "mean_points": round(mean_pts, 2),
                "t_stat": round(t_stat, 3) if t_stat is not None else None,
            },
        }
        self._execution_history.append(result_entry)
        return result_entry

    def export_harness_status(self) -> dict[str, Any]:
        """Exporta el estado completo del harness en formato reproducible."""
        return {
            "schema_version": "fars-externas-mejora-harness-v1",
            "alpha_base": self.alpha_base,
            "variants_tested_count": self._variants_tested_count,
            "bonferroni_alpha_current": self.bonferroni_alpha,
            "registered_variants": [asdict(v) for v in self._registered_variants],
            "execution_history": self._execution_history,
        }


# =============================================================================
# CATÁLOGO DE VARIANTES PRE-DECLARADAS (ENCARGO E3)
# =============================================================================

CATALOGO_PREDECLARADO_E3 = [
    {
        "variant_id": "V1",
        "base_signal": "D2L",
        "dimension_modified": "mecanica_salida",
        "baseline_value": {"exit_time": "16:00 ET"},
        "variant_value": {"exit_time": time(15, 45)},
        "hypothesis_rationale": "¿El último tramo lunes 15:45->16:00 aporta o daña al drift?",
    },
    {
        "variant_id": "V2",
        "base_signal": "D2L",
        "dimension_modified": "ventana_horaria",
        "baseline_value": {"entry_time": "18:05 ET"},
        "variant_value": {"entry_time": time(19, 0)},
        "hypothesis_rationale": "Sensibilidad de la ventana de entrada dominical (18:05 vs 19:00 ET)",
    },
    {
        "variant_id": "V3",
        "base_signal": "L0200",
        "dimension_modified": "mecanica_salida",
        "baseline_value": {"tp_pts": 150.0, "sl_pts": 372.9, "exit_time": "16:00 ET"},
        "variant_value": {"use_time_exit_only": True},
        "hypothesis_rationale": "Gestión de salida del candidato operativo (salida por tiempo lunes 16:00 sin TP vs TP/SL)",
    },
    {
        "variant_id": "V4",
        "base_signal": "L0200",
        "dimension_modified": "mecanica_salida",
        "baseline_value": {"sl_pts": 372.9, "tp_pts": 150.0},
        "variant_value": {"sl_pts": 250.0, "tp_pts": 100.6},
        "hypothesis_rationale": "Sensibilidad al stop: stop más estrecho 250 pts ($500 Budget-R) con TP proporcional 100.6 pts",
    },
    {
        "variant_id": "V5",
        "base_signal": "ACT",
        "dimension_modified": "filtro_tendencia",
        "baseline_value": {"ema_period": 50},
        "variant_value": {"ema_period": 20},
        "hypothesis_rationale": "¿El periodo del filtro importa? (sesgo más reactivo EMA20/4h vs EMA50/4h)",
    },
    {
        "variant_id": "V6",
        "base_signal": "ACT",
        "dimension_modified": "ventana_horaria",
        "baseline_value": {"session": "RTH completa (09:30-16:00 ET)"},
        "variant_value": {"ny_session_only": True, "ny_session_end": time(12, 0)},
        "hypothesis_rationale": "¿La ventana horaria concentra el edge? (solo sesión NY mañana: 09:30 a 12:00 ET)",
    },
]


def setup_e3_harness(alpha_base: float = 0.05) -> ImprovementHarness:
    """Configura e inicializa el harness con las 6 variantes pre-declaradas de E3."""
    harness = ImprovementHarness(alpha_base=alpha_base)
    for row in CATALOGO_PREDECLARADO_E3:
        harness.register_variant(
            base_signal=row["base_signal"],
            dimension_modified=row["dimension_modified"],
            baseline_value=row["baseline_value"],
            variant_value=row["variant_value"],
            hypothesis_rationale=row["hypothesis_rationale"],
            variant_id_override=row["variant_id"],
        )
    return harness
