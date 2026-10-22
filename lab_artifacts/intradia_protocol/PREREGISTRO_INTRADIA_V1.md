---
tags: [FARS, preregistro, intradia, EMAS]
fecha: 2026-10-11
estado: CONGELADO (sha256 al pie)
dueño: Ricardo
ejecutor: Hermes
autorizacion: "Camino 2 (investigación acotada) — Ricardo 10-oct-2026; arranca al aprobar Codex F1 (APTO r3, 11-oct)"
---

# PREREGISTRO — Investigación intradía V1 (estrategias que ganan con flat diario)

## Pregunta única

¿Alguna familia del catálogo tiene un edge que **sobreviva el flat diario** (todas las posiciones
cerradas a las 15:10 CT, sin overnight)? Esa es la única clase de edge que sirve en cualquier
proveedor que acepte bots.

## Marco (decisiones de Ricardo, 10-oct — inamovibles)

1. **Límite: 2 semanas** (11-oct → 25-oct). **Checkpoint a la mitad (18-oct):** si no hay señal,
   PARAR y avisar por Telegram. No se extiende sin nueva orden.
2. **Cada candidata se mide SOLA antes de combinar.** Prohibido apilar filtros (doctrina M7/M9).
3. **Contador de miras declarado:** este ciclo arranca en **mira #1**. Toda corrida consume mira y
   se registra abajo. Las miras previas de EMAS (H1, PASO1_DELTAS) quedan en su contador histórico.
4. **Folds 6..7 de H1 siguen SELLADOS** — no se usan ni se miran.
5. **Los ports de Juanca siguen APARCADOS** (D2L/L0200/ACT no se miden hasta que él pase los repos
   fuente). Sus "señales externas" quedan fuera de este ciclo.
6. Nada se mergea a main sin OK de Ricardo.

## Candidatas del ciclo (orden de medición, una por corrida)

| # | Candidata | Fuente | Estado |
|---|---|---|---|
| 1 | **EMAS señal sola, gestión target-puro RR3.0** (sin parcial ni BE) | `src/backtest/emas.py` (port auditado, 14 ejes de delta vs Kai) + brazo documentado en H1 | **ESTA corrida** |
| 2 | Zonas (entradas en zona/pool del motor Z1-Z6, salida al pool opuesto) | `src/zones/`, `src/detectors/` | pendiente, preregistro se enmienda ANTES de correr |
| 3 | Bias EMAS × zonas (solo si 1 o 2 muestran señal por separado) | Plan-EMAS F2 | bloqueada salvo que 1 o 2 pasen |

## Candidata 1 — diseño congelado

- **Estrategia:** señal EMAS (`EmasStrategy`, `emas_config`) con gestión **target-puro RR3.0**
  (el brazo documentado de H1; la gestión escalonada está falsada y no se reabre).
- **Restricción intradía (la variable del experimento):** semántica V-EOD — salida forzada al
  cierre de sesión (flat 15:10 CT), entrada solo intradía. Si la señal exige mantener posición más
  de un día, se cierra igual (eso es justamente lo que se mide).
- **Mercado/datos:** MNQ canónico (M5/M1, corte 2019-05-06, `databento` canónico). Costes realistas:
  0,62 USD/lado + 1,5 ticks/lado. Sensibilidad de costos: +1 y +2 ticks por lado.
- **Walk-forward:** plan C1 36/6/6, **folds 0..5** (los mismos que H1 para comparabilidad).
  Folds 6..7 SELLADOS.
- **Sizing/cuenta (yardstick):** motor de cuenta corregido (bootstrap_camino con
  `trailing_update_cadence="end_of_day"`, `consistency_day_consolidation="day_close"`), perfil
  **Topstep 50K** (target $3.000, MLL $2.000 EOD-trailing lock $50.000, min 2 días), **1 contrato**.
  MC B=2.000, MBB bloque=optimal, semilla maestra 20260928.

## Controles (COLA ACOTADA — regla 5 de Ricardo)

El control iid normal de rondas previas se descarta (cola izquierda sin acotada quemaba
artificialmente). Controles de este ciclo, ambos con la **distribución exacta de R**:

1. **Control emparejado-permutación:** permutación de los R del ledger (misma distribución, mismos
   momentos, sin estructura temporal). Semilla declarada.
2. **Control sin deriva:** permutación de R centrados (media 0).
Cada control corre con el MISMO motor y las mismas reglas; se publica el delta
control − candidato.

## Criterio de ÉXITO (verbatim de Ricardo — se lee literal)

> "P(pase) superior al control emparejado con IC que no cruce cero, y aguanta +1 tick de slippage."

Operativo:
- **CONDICIÓN A:** P(pase)_candidata − P(pase)_control_emparejado > 0 con IC95 bootstrap (B=2.000
  réplicas pareadas) que **no cruce cero**.
- **CONDICIÓN B:** con slippage +1 tick por lado, la condición A se mantiene (delta > 0, IC95 sin
  cruzar cero).
- Ambas se cumplen ⟺ candidata promovida a combinación. Si solo una se cumple: **NO CONFIRMADA**
  (etiqueta literal, no se mueve el umbral). Además gates de higiene: n ≥ 100 trades, causalidad
  sin violaciones, determinismo bit a bit en re-ejecución.

## Reglas de etiqueta (no se negocian)

- CONFIRMADA / FALSADA / INCONCLUSA se leen literalmente de este texto. Un resultado que no cumple
  A ni su negación es INCONCLUSO.
- Todo número se publica con su control emparejado; un número absoluto solo no es evidencia.
- Sensibilidad +1/+2 ticks se reporta SIEMPRE aunque la base pase.
- Si la candidata 1 falla, se registra y se pasa a la candidata 2 con **nuevo preregistro
  congelado** (este archivo no se edita después de ver resultados; las enmiendas van en
  `PREREGISTRO_INTRADIA_V1_ENMIENDA_N.md` congeladas aparte).

## Registro de corridas del ciclo (se llena al correr)

| Mira | Corrida | Fecha | Resultado |
|---|---|---|---|
| 1 | EMAS target-puro RR3 · flat 15:10 · MNQ · folds 0-5 | — | pendiente |

---
SHA256 de congelación: ver `PREREGISTRO_INTRADIA_V1.sha256` (se genera al escribir este archivo,
antes de cualquier corrida).
