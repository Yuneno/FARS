# Carril 2 — Veredicto del screening de recalibración por mercado

**Fecha:** 2026-09-16 · **Preregistro:** `preregistro_recalibracion.json` (escrito ANTES de correr) · **Resultados:** `screening.json`

## Pregunta

¿Un ajuste de `min_risk_pts` por mercado revive MYM o mejora MGC/MNQ (idea de Ricardo: ajustar la fórmula según el mercado)?

## Respuesta

| Mercado | Veredicto | Detalle |
|---|---|---|
| **MNQ** | Zona campeona **confirmada** | 5.0-15.0 pasan screening y **sobreviven Bonferroni** (8.0: folds+ 100%; 10.0: E[R] +0.104). Bajar de 5 mata por costes (2.0: −0.072). Subir de 20 pierde robustez (CI deja de excluir cero a α/24). |
| **MYM** | ❌ **No se revive** | Negativo en TODA la escalera (2→30: de −0.219 a +0.015 con CI cruzando cero). El sangrado del Dow es **estructural**, no de parámetro — al menos para SMC-FVG. |
| **MGC** | ✅ **Sí se mejora** | Bajar el filtro resuelve el problema de muestra Y el edge aguanta: **2.0** (n=1.938, E[R] +0.086, CI [+0.029,+0.139]) y **3.0** (n=1.127, E[R] +0.140, PF 1.327, folds+ 88%, CI [+0.081,+0.203]) pasan screening **con Bonferroni** (α=0.00208). |

## Candidatas oficiales (para validación C2-style propia, preregistrada)

1. **MGC min_risk=3.0** — la joya: mejor E[R]/PF de la criba, 88% folds positivos, CI robusto a multiplicidad.
2. **MGC min_risk=2.0** — la de más frecuencia (1.938 trades), edge menor pero CI limpio.

MNQ 5.0/8.0/10.0 ya son pilar/alternativas registradas (E2). MYM queda descartado para SMC-FVG (la SMC-OB sigue en camino con Codex — es la vía correcta para el Dow).

## Siguiente (ya corrido en esta sesión)

- Puntuación de cuenta (motor Apex 25K, intraday, políticas fixed/buffer k=0.10) para MGC 2.0/3.0 solas y para pools MNQ+MGC con la config recalibrada.
- La promoción final requiere la validación C2-style propia de la candidata ganadora (walk-forward completo + gates + preregistro) — el screening no promueve.
