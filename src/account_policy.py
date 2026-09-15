"""Capa de políticas de apuesta para el motor de cuentas (E4).

Las políticas deciden el riesgo nominal por operación a partir del estado de la
cuenta (balance, floor trailing) y del historial reciente (racha de pérdidas).
100% causales: solo ven información disponible ANTES del trade.

Políticas:
- ``fixed``:       riesgo base constante (comportamiento legacy).
- ``buffer_prop``: riesgo = clip(k * colchón, floor_frac*balance, cap_frac*balance).
                   El colchón es (balance − floor trailing): lejos del suelo se
                   arriesga más; cerca, menos.
- racha (streak):  composable sobre cualquier base — tras ``streak_threshold``
                   pérdidas seguidas, el riesgo se multiplica por
                   ``streak_factor`` (<1) hasta la siguiente ganancia.

Motivación medida (E4 exploratorio): buffer_prop k=0.10 da el mejor pase por
unidad de fracaso (3.02 vs 1.76 del fijo); el throttle por racha reduce la
quema ~20-30% sin tocar el pase. Nada de esto toca la señal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SizingPolicyConfig:
    """Configuración declarativa de una política de apuesta (E4)."""

    kind: str = "fixed"  # "fixed" | "buffer_prop"
    param: float = 0.0  # k para buffer_prop (fracción del colchón)
    floor_frac: float = 0.001  # riesgo mínimo, fracción del balance
    cap_frac: float = 0.015  # riesgo máximo, fracción del balance
    streak_threshold: int = 0  # 0 = racha desactivada
    streak_factor: float = 0.5  # multiplicador tras la racha (0 < f <= 1)

    def __post_init__(self) -> None:
        if self.kind not in {"fixed", "buffer_prop"}:
            raise ValueError(f"kind inválido: {self.kind}")
        if self.kind == "buffer_prop" and self.param <= 0:
            raise ValueError("buffer_prop requiere param (k) > 0")
        if not 0.0 <= self.floor_frac < self.cap_frac:
            raise ValueError("se requiere 0 <= floor_frac < cap_frac")
        if self.streak_threshold < 0:
            raise ValueError("streak_threshold debe ser >= 0")
        if not 0.0 < self.streak_factor <= 1.0:
            raise ValueError("streak_factor debe estar en (0, 1]")
        if self.streak_threshold > 0 and self.streak_factor >= 1.0:
            raise ValueError("una racha con factor >= 1 no reduce nada")


class SizingPolicy:
    """Estado vivo de una política dentro de una simulación de cuenta.

    ``risk_usd`` se llama por trade ANTES de dimensionar; ``note_result`` se
    llama tras cerrar el trade (P&L realizado en dólares de la cuenta).
    """

    def __init__(self, cfg: SizingPolicyConfig) -> None:
        self.cfg = cfg
        self.streak_losses = 0

    def risk_usd(self, *, balance: Decimal, floor: Decimal, base_risk_usd: Decimal) -> Decimal:
        """Riesgo nominal en USD para el próximo trade, dado el estado actual."""
        if self.cfg.kind == "fixed":
            risk = base_risk_usd
        else:  # buffer_prop
            buffer = balance - floor
            lo = Decimal(str(self.cfg.floor_frac)) * balance
            hi = Decimal(str(self.cfg.cap_frac)) * balance
            risk = max(min(Decimal(str(self.cfg.param)) * buffer, hi), lo)
        if self.cfg.streak_threshold > 0 and self.streak_losses >= self.cfg.streak_threshold:
            risk *= Decimal(str(self.cfg.streak_factor))
        return max(risk, Decimal("0"))

    def note_result(self, net_pnl: Decimal) -> None:
        """Actualiza la racha con el P&L realizado del trade cerrado."""
        if net_pnl < 0:
            self.streak_losses += 1
        else:
            self.streak_losses = 0
