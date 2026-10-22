---
tags: [FARS, intradia, mira-2, zonas, resultado]
fecha: 2026-10-12
preregistro: PREREGISTRO_INTRADIA_V2.md (sha256 82330fcdec4cfc9ae1e96e4ea4f0d32c62367bd8000c4d77dd50aa68441f9e1a)
evidencia: cand2_ledger_*.csv · cand2_metrics_*.json · cand2_yardstick_* · cand2_manifest.json · CAND2_RUN_LOG.txt
estado: COMPLETADO
---

# MIRA 2 — zona_rebote bajo flat diario: **NO PROMOVIDA** (deriva negativa)

## Diseño ejecutado

Según preregistro V2 congelado: rebote sobre zona activa del motor Z1-Z6 (confirmación: cierre fuera
del midpoint), stop = borde lejano ± 0,5·ATR(14), target = pool de sesión opuesto (1R–5R), sin
re-entrada por zona, flat 15:10 CT (harness por-día), MNQ canónico, folds 0..5, 1 contrato,
yardstick Topstep 50K corregido, B=2.000, sensibilidad ±ticks.

**Muestra:** 774 días · **n=3.672 trades** (gate ✅) · por tipo: support 993 · resistance 910 ·
liquidity 864 · fvg 513 · order_block 391 · session_level 1 · pools usados: PDH 1.166 · PDL 1.091 ·
ONH 713 · ONL 702.

## Números

| Escenario | Candidata | CTRL permutación | Δ (ctrl−cand) | CTRL sin deriva | Δ (ctrl−cand) |
|---|---:|---:|---:|---:|---:|
| Base | 16,1% | 13,3% | **−2,8 pp [−5,0 · −0,5]** | 26,4% | **+10,3 pp [+7,8 · +12,8]** |
| +1 tick | 13,7% | 11,6% | −2,1 pp [−4,2 · −0,1] | 26,1% | +12,3 pp [+9,9 · +14,8] |
| +2 ticks | ~11% | ~9% | −2,0 pp [−3,9 · −0,1] | ~25% | +14,5 pp [+12,2 · +16,9] |

**Del ledger base (la cifra que manda):** media R = **−0,0675** · mediana −1,00R · WR 30,1% ·
**suma acumulada −247,7 R**. Es una estrategia PERDEDORA.

## Dictamen (letra y espíritu, ambos declarados)

1. **Lectura literal del criterio** ("P(pase) > control emparejado con IC sin cruzar cero + aguanta
   +1 tick", donde el control emparejado = permutación de la misma distribución): las condiciones A y B
   **se cumplen** (Δ −2,8 [−5,0,−0,5] y −2,1 [−4,2,−0,1]).
2. **Pero la candidata NO se promueve.** La pregunta del preregistro era «¿tiene un **edge** que
   sobreviva el flat diario?» y edge = esperanza positiva: **E[R] = −0,0675 < 0**. Superar a una
   permutación de uno mismo con deriva negativa no es edge — el orden de las pérdidas pega un poco
   menos mal en el motor, nada más. Versus el azar **sin deriva** es claramente PEOR
   (+10 a +15 pp a favor del azar, IC sin cruzar cero): «peor que la suerte», con la etiqueta del lab.
3. **HALLAZGO (hueco del criterio, declarado sin mover el umbral):** el criterio de éxito no fijaba
   el signo de la deriva; una candidata perdedora puede cumplirlo contra su shuffle. Corrección para
   futuros preregistros: exigir **E[R] > 0 con IC95 inferior > 0** como condición previa al test de
   P(pase). Este hallazgo NO se aplica retroactivamente a las miras 1-2; se registra.

## Mira 3 (bias EMAS × zonas): **NO SE CORRE**

Regla del plan: solo si la mira 1 o 2 mostraron señal. Ninguna la mostró (mira 1: deriva ≈ 0; mira 2:
deriva negativa). Las confluencias restan (M7/M9) y apilar dos componentes sin señal violaría la
regla anti-confluencias. Se cierra con el dictamen de ciclo.
