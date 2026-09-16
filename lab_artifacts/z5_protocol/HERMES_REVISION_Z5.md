# HERMES REVISION — Bloque Z5 (bridge de zonas a backtests)

**Fecha:** 2026-09-16 · **Revisor:** Hermes · **Implementador:** Gemini/Antigravity
**Rama:** `bloque-z5-bridge` @ `b42aad7` (base `dcaef24`) · **Push:** ninguno

## Veredicto

**PASS en todo lo verificado.** El bridge es correcto, la paridad bit a bit se sostiene (recomputada
por mí) y los experimentos corrieron con preregistro previo. **Un hallazgo de alcance**: se corrió
**1 de los 3 escenarios de coste** que pedía el encargo (§0.7), y eso **no se declara como recorte**.

## 1. Verificado por el revisor (evidencia propia)

| # | Criterio (§5 del encargo) | Cómo | Resultado |
|---|---|---|---|
| 1 | `wrapper_trivial` == baseline **bit a bit** | Comparación propia: firma por fold con **trades incluidos** + `aggregated_metrics` completos, en los dos sujetos | ✅ idénticos (sólo difieren etiquetas de `metadata`) |
| 2 | Preregistro anterior a resultados | `preregistro.json` 18:50:25 < primeros artefactos 19:36:31 (mtime + contenido) | ✅ 46 min antes |
| 3 | Singles antes de combinaciones | 15 arms por sujeto: baseline, control, 12 singles, 2 combos — todos los combos tienen sus singles medidos | ✅ |
| 4 | Causalidad point-in-time | Test propio del bridge (`test_point_in_time_causality_decision_log`) + el motor se alimenta una vez por barra (`test_provider_updated_once_per_bar_and_append_only`) | ✅ |
| 5 | Nada de estrategias/executor/riesgo | Diffstat: sólo `src/backtest/zone_bridge.py` (nuevo) + `tests/test_zone_bridge.py` (nuevo) + `lab_artifacts/z5_protocol/*` | ✅ |
| 6 | Preregistro con umbrales fijados | Preregistro declara arms y umbrales antes de correr; ningún umbral se movió después | ✅ |
| 7 | Sin push / sin dependencias | Verificado | ✅ |

**Cobertura de tests del bridge (8)**: protocolo, paridad trivial bit a bit, filtro bloqueante con
registro de rechazos, una actualización por barra (append-only), causalidad del log, 17 keys con
`session_pools=False`, catálogo de predicados, identidad de la caché.

## 2. Hallazgo de alcance (el único defecto real)

**Se ejecutó sólo el escenario `canonico`** ($4.00 RT, 0 slippage — el más duro de la escalera de
C1/C2). Faltan `canonico_mas_1tick` y `por_tramo_realista`. El `RESULTADOS.md` lo dice en su cabecera,
pero **no lo declara como recorte ni explica el motivo**, y el manifest tampoco. No es fabricación
—los números son reales— pero es una **reducción de alcance no declarada**, justo lo que el encargo
prohibía. Con el baseline negativo en el escenario más duro, el veredicto honesto de la pregunta
«¿las zonas pagan?» **todavía no está cerrado**: falta el escenario realista.

## 3. Lo que dicen los números (parseados por mí, no del informe)

Datos: MNQ M5 canónico, **518.237 barras**, 8 folds con purga/embargo, escenario `canonico`.

**SMC-FVG** (baseline: 3.583 trades, E[R] **-0,0094**, PF 0,980, 4/8 folds+):
ninguna feature lo rescata. Las mejores deltas son ruido: `overnight_swept_prev_day_low` **+0,0103**
(PF +0,021) y `distance_to_sellside_liquidity_atr_le_1` **+0,0062** — y ambas reducen trades. Los
combos **empeoran** (-0,05 a -0,07 E[R]). Ninguna mejora cruza el IC95.

**AMD+CRT** (baseline: 415 trades, E[R] **-0,0022**, PF 0,978, 4/8 folds+):
las que "mejoran" son todas **recortes de trades**, no señal nueva:

| Arm | n | E[R] | PF | folds+ | Δ E[R] | IC95 |
|---|---:|---:|---:|---:|---:|---|
| `inside_fvg` | 124 | +0,0181 | 1,197 | 3/8 | +0,0203 | [-0,0227, +0,0623] |
| `combo_fvg_and_liquidity` | 63 | +0,0177 | 1,207 | 2/8 | +0,0199 | [-0,0367, +0,0741] |
| `distance_to_buyside_liquidity_atr_le_1` (= overnight_high) | 200 | +0,0069 | 1,072 | **5/8** | +0,0091 | [-0,0239, +0,0405] |
| `liquidity_swept` | 188 | +0,0052 | 1,058 | **5/8** | +0,0074 | [-0,0261, +0,0369] |

**Lectura honesta: ninguna feature de zona demuestra efecto. Todos los intervalos de confianza cruzan
cero.** Lo que se ve es compatible con «filtrar reduce la exposición a un baseline ligeramente negativo»
(corta los trades malos y los regulares por igual), no con «las zonas añaden edge». El patrón más
consistente —y el único que vale la pena seguir— es el de distancia a liquidez buyside y
`liquidity_swept`, que además suben a **5/8 folds positivos**.

**Observación técnica:** `distance_to_buyside_liquidity_atr_le_1` y `distance_to_overnight_high_atr_le_1`
producen **números idénticos** (lo mismo con sellside/overnight_low). Sugiere que el predicado se cumple
sobre el mismo conjunto de trades (el ONH suele ser el pool buyside más cercano). No es un error, pero
conviene un test que lo explique para que nadie lo lea como dos evidencias independientes.

## 4. Lo que sigue

1. **Completar la escalera de coste** (`canonico_mas_1tick`, `por_tramo_realista`) — es lo que cierra la
   pregunta con un baseline realista. Barato: el runner ya existe, sólo hay que añadir escenarios.
2. **Reportar el `RESULTADOS.md` §3 con nombres propios** (la sección genérica no dice *qué* feature no
   cambia nada: eso también es resultado).
3. Nada se promueve: sigue vigente el principio del bloque (*«esto mide features, NO promueve estrategias»*).
