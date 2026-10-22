# CAND2 — Decisiones de implementación (MIRA 2, zona_rebote flat diario)

Harness: `run_cand2_zonas.py` (este directorio). Preregistro congelado:
`PREREGISTRO_INTRADIA_V2.md` (sha256 `82330fcd…68441f9e1a`; el harness aborta
si no coincide — verificado contra `PREREGISTRO_INTRADIA_V2.sha256` al
redactar). Rama `fix/motor-r2`. Sin shell en esta sesión: lo ejecuta Hermes.

## 1. Idea central (sin tocar `src/`)

Estructura copiada de `run_cand1_emas.py` (mira 1); cambia SOLO la capa de
señal (`EmasStrategy` → `ZonasReboteStrategy`) más dos añadidos de salida
(atribución por tipo de zona, `CAND2_RUN_LOG.txt`). Todo lo demás —carga de
datos, partición por día con warmup, corte 15:10 CT, pines del executor, plan
walk-forward, semillas, yardstick, gates— es idéntico a la mira 1:

- Slice del día S = **500 barras previas** + **barras de la sesión S con
  `ts <= 15:10 CT de la fecha S`** (instante DST-safe vía zoneinfo, misma
  semántica "rango" de CAND1 §2, incluida la apertura vespertina del día
  previo dentro de la sesión).
- Cada slice corre `run_backtest` con `end_of_data_policy="close"`;
  `DayGatedStrategy` suprime señales en warmup avanzando el estado con
  `observe()` (motor de zonas + ATR bit a bit iguales a corrida continua).
- Ledger de 19 columnas idéntico al de mira 1 (prefijo `cand2-` en
  `trade_id`); `exit_reason="end_of_data"` → `eod_flat` con original en
  `executor_exit_reason`; folds por índice de barra de entrada; G4 con assert
  y días sellados saltados enteros; bordes fuera de dev dropeados y contados.

## 2. La capa de señal: `ZonasReboteStrategy`

Interfaz `evaluate(history) -> Signal | None` para
`src.backtest.executor.run_backtest` (ruta legacy, niveles absolutos).

- **Estado incremental**: `ZoneEngine(symbol="MNQ", timeframe="M5",
  tick_size=0.25, session_pools=True)` con **todos los defaults del motor**
  (pivotes 3/3, `min_gap_pct=0.001`, liquidez 2 toques / tol 4 ticks /
  0.10 ATR / lookback 4000, sin `max_age_bars`, `d20_sessions=20`,
  mitigación FVG `full_fill`). Alimentado con `update(history)` barra a
  barra; `_sync` propio con chequeo append-only espejo del de `EmasStrategy`.
- **ATR(14)**: la de `emas_config` en sentido de fórmula — SMA de los últimos
  14 true ranges, copiada de `EmasStrategy._append` (`ATR_PERIOD` importado
  de `src/backtest/emas.py`). Al motor **no** se le pasa `atr_series`
  (consecuencia: la tolerancia de niveles de sesión usa el fallback del
  motor `tick_size*10`; determinista, declarado). Sin ATR finito > 0 o con
  menos de 15 barras en el historial → sin señal.
- **Universo de entrada: TODAS las zonas activas** (`active_zones()` = estado
  no-terminal: `active/touched/partial/swept`) con `available_at <= ts` de la
  barra evaluada, `zone_id` no usado y `direction` long/short. Esto incluye
  `fvg`, `liquidity`, `support`, `resistance`, `order_block` y
  `session_level` (también D20 como zona de entrada, aunque no como pool —
  ver A3). `direction == "neutral"` (FVG sin sesgo) se salta: no mapea a
  LONG/SHORT.
- **Regla por barra** (orden determinista del motor `(available_at,
  zone_id)`, primera zona que califica; máx 1 señal por barra):
  demanda → `low <= z.upper` y `close > midpoint` → LONG;
  oferta → `high >= z.lower` y `close < midpoint` → SHORT.
- **Stop absoluto** (`stop_target_as_points=False`): largo `z.lower −
  0,5·ATR`; corto `z.upper + 0,5·ATR`.
- **Target absoluto**: `midpoint` del pool de sesión más cercano en dirección
  (`mid > close` en largos) con `1R ≤ d ≤ 5R` (bordes inclusivos, sin
  tolerancia numérica; `R = |close − stop|`, `d = |mid − close|` medidos al
  close de la barra señal como proxy del fill a open siguiente). Sin pool
  válido → `None` (no se opera). Máx 1 posición (la da el executor, que solo
  consulta `evaluate` en plano) y `used_zone_ids` marcado **al emitir**.
- Cada señal aceptada se registra en `Cand2Decision` (timestamp, dirección,
  zona, pool, niveles, R).

## 3. Ambigüedades del preregistro resueltas (lectura más simple)

- **A1 toque literal unilateral.** Se implementa exactamente el paréntesis:
  `low <= z.upper` / `high >= z.lower`, sin exigir solape por el otro lado.
  Efecto conocido: una barra entera por debajo de una zona de demanda también
  "toca". Se acepta como lectura literal; no se añade filtro.
- **A2 "ACTIVA" = no-terminal.** Incluye `touched/partial/swept`, no solo
  `active`. Filtro causal `available_at <= ts` aplicado también a zonas
  nacidas en la propia barra (pasan por `<=`).
- **A3 pools = 4 anclajes literales** (`prev_day_high/low`,
  `overnight_high/low`). Las zonas `d20_high/low` existen en el motor pero
  quedan **fuera** de candidatos a target; siguen elegibles como entrada
  (universo TODAS).
- **A4 mapeo literal de `Zone.direction`.** `long → LONG`, `short → SHORT`.
  Consecuencia declarada: en FVG/OB/SR esto es rebote, pero en
  `liquidity` (EQH→long, EQL→short) y `session_level` (PDH/ONH→long,
  PDL/ONL→short) el mapeo literal produce operaciones de **ruptura** del
  pool, no de rebote. Es lo que la regla congela; cambiarlo sería un filtro
  adicional prohibido.
- **A5 sin fallback a segunda zona.** Si la primera zona que califica no
  tiene pool válido, la barra no opera (no se prueba con la siguiente).
- **A6 `entry_ref` = close señal.** El executor ignora `Signal.entry` en el
  fill (siempre open siguiente); el close se usa como proxy para R y ventana
  del pool. Divergencias open-vs-close las resuelve la mecánica del executor
  (niveles validados tras redondeo a tick; barra de entrada evalúa TP/SL).
- **A7 target = `midpoint` del pool.** Los bounds `±` tolerancia definen la
  banda, no el nivel; el nivel es el midpoint.
- **A8 `close == midpoint` no confirma** (desigualdad estricta).
- **A9 zona usada = señal emitida**, aunque el executor la rechace después
  (gap o niveles colapsados). `used_zone_ids` vive por slice diario
  (estrategia fresca por día), coherente con el flat diario.
- **A10 guarda `risk <= 0` inalcanzable** (con confirmación estricta y ATR >
  0 el riesgo es siempre positivo por invariante `lower <= mid <= upper`).
- **A11 sin gate RTH ni tope de trades/día.** Además de la mecánica
  congelada no hay otro filtro (una sola variante). Entradas overnight
  dentro de la sesión permitidas, igual que mira 1.
- **A12 atribución desde decisiones aceptadas**, no desde trades ejecutados:
  puede divergir en ±`gap_rejections`/rechazos de nivel. El ledger no cambia
  de esquema por esto.

## 4. Desglose por tipo en la salida

- `cand2_metrics_*.json`: clave `signal_attribution` (global:
  `n_accepted_signals`, `n_signals_border_fold`, `by_zone_type`,
  `by_pool_anchor`, `by_direction`; por fold: `by_fold`) + bloque
  `metadata.zones` con los parámetros congelados.
- `cand2_yardstick_*.txt`: primera línea con señales aceptadas por tipo y
  pools. Ledger CSV sin cambios de esquema (19 columnas de mira 1).

## 5. Decisiones heredadas de mira 1 (sin reabrir)

Pines del executor idénticos (sizing por riesgo, budget $500; "1 contrato"
solo en yardstick con 1R ≡ $200); base 1 tick/lado + sensibilidad +1/+2
(el preregistro dice "1,5 ticks/lado": implementado H1, ver Q1);
`end_of_data_slippage_points=0.25`; CBB seed 42 con regla G5; semillas
`SeedSequence(20260928).spawn(4)` con slot 1 reservado; MBB bloque óptimo
circular compartido candidato↔controles; deltas pareados control−candidato;
criterio literal con gate n≥100 (ABSTENCIÓN por muestra).

## 6. Salidas y determinismo

Por nivel de slip: `cand2_ledger_{base,slip+1ticks,slip+2ticks}.csv`,
`cand2_metrics_*.json`, `cand2_yardstick_*.{json,txt}`, más
`cand2_manifest.json` global y `CAND2_RUN_LOG.txt` (tee de todo el stdout;
la única línea fuera del log es la confirmación final de escritura, que por
construcción es posterior al log; el manifest sí hashea el log vía globs
`cand2_*` + `CAND2_RUN_LOG*.txt`). Determinismo: días cronológicos, ledger
y decisiones ordenados, sin iterar sets en salidas (`by_*` ordenados),
`trades_sha256` por nivel para la doble corrida. Flags: `--smoke` (10 días,
base, B=200, sufijo `_SMOKE`), `--slip-extra {0,1,2}`, `--no-yardstick`.
Prohibiciones respetadas: solo se escribe bajo `lab_artifacts/
intradia_protocol/`; `src/`, mira 1, folds 6..7 y preregistros intactos
(sha verificado al arrancar). Nada ejecutado aquí (sin shell).

## 7. Preguntas para Hermes (ver respuesta)

Q1 base 1 vs 1,5 ticks · Q2 D20 fuera de pools · Q3 lectura ruptura en
pools de liquidez/sesión (A4) · Q4 sin fallback a segunda zona (A5) ·
Q5 entradas overnight (A11).
