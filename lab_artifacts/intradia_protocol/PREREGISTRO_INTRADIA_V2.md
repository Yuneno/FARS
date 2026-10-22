---
tags: [FARS, preregistro, intradia, zonas]
fecha: 2026-10-12
estado: CONGELADO (sha256 en PREREGISTRO_INTRADIA_V2.sha256)
dueño: Ricardo
ejecutor: Hermes (bloque autónomo 12-oct, orden "desde las 8 hasta que se dictamine")
---

# PREREGISTRO — Investigación intradía V2 · Mira 2: ZONAS (rebote de zona)

Idéntico marco que `PREREGISTRO_INTRADIA_V1.md` (mismo criterio de éxito literal, mismos controles
de cola acotada, mismo yardstick Topstep 50K corregido, mismo límite de ciclo y checkpoint 18-oct,
folds 6..7 sellados, mercado: NADA — solo históricos por orden de Ricardo 11-oct). Este archivo
define SOLO la candidata 2. Mira 1 (EMAS RR3 flat) ya dictaminada NO CONFIRMADA.

## Candidata 2 — definición mecánica congelada (una sola variante)

**Nombre:** `zona_rebote` (rebote de zona activa, sin sesgo adicional).

- **Motor de zonas:** `src/zones/engine.py::ZoneEngine` (Z1-Z6: FVG, order blocks, S/R, liquidez,
  session levels) alimentado incrementalmente con barras M5 cerradas del día (causal: la zona está
  disponible solo si `available_at <= timestamp de la barra evaluada`).
- **Entrada (una por barrera de lógica, sin filtros apilados):** al cierre de una barra M5, si el
  rango de la barra **tocó** una zona ACTIVA (`low <= z.upper` para zona de demanda; `high >=
  z.lower` para zona de oferta) y la barra cierra **fuera/por encima del midpoint** (demanda) o
  **fuera/por debajo** (oferta) — confirmación mínima de rebote — → entrada a mercado en la
  apertura de la siguiente barra:
  - zona `demand` → LONG · zona `supply` → SHORT (`Zone.direction`).
  - Máx **1 posición abierta**; sin re-entrada en la misma zona (`zone_id` ya usado = agotado).
- **Stop:** borde lejano de la zona − (largos) / + (cortos) **0,5 × ATR(14)** de M5 (k=0.5 declarado,
  doctrina de umbrales ATR-normalizados).
- **Target = pool opuesto:** el nivel de pool de sesión más cercano en dirección del trade
  (`SessionLevelsBuilder`: PDH/PDL/ONH/ONL del día previo/actual) que cumpla
  **1,0R ≤ distancia ≤ 5,0R** desde la entrada. Si no existe pool válido → **no se opera** (filtro
  mecánico, no criterio discrecional).
- **Gestión:** target fijo del pool o stop; sin parciales, sin BE, sin trailing (lo único que se mide
  es la señal). **Flat diario 15:10 CT** (harness por-día de la mira 1: warmup 500 barras +
  `end_of_data_policy="close"`).
- **Datos/mercado:** MNQ canónico M5 (mismo zip/corte que mira 1), costes 0,62/side + 1,5 ticks/side
  (sensibilidad +1 y +2 ticks), folds 0..5 del plan C1.

## Gates de higiene (idénticos V1)

n ≥ 100 trades (si no: ABSTENCIÓN por muestra, no INCONCLUSA) · causalidad sin violaciones ·
determinismo doble corrida · toda cifra con su control emparejado.

## Criterio de éxito (literal, no se mueve)

> "P(pase) superior al control emparejado con IC que no cruce cero, y aguanta +1 tick de slippage."

Controles (cola acotada): permutación de R del ledger y permutación centrada (media 0), mismas
semillas derivadas de `SeedSequence(20260928)`.

## Contador de miras

| Mira | Candidata | Fecha | Resultado |
|---|---|---|---|
| 1 | EMAS target-puro RR3 · flat diario | 11-oct | NO CONFIRMADA (CAND1_RESULTADO.md) |
| 2 | **zona_rebote (este archivo)** | 12-oct | pendiente |
| 3 | bias EMAS × zonas (SOLO si 1 o 2 mostraron señal) | — | bloqueada por ahora |

Si la mira 2 es NO CONFIRMADA y no hay señal que justifique la 3, el bloque se cierra con
RESUMEN_BLOQUE_12OCT.md y dictamen de ciclo.
