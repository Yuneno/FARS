# CAND1 — Decisiones de implementación (MIRA 1, EMAS target-puro RR3 flat diario)

Harness: `run_cand1_emas.py` (este directorio). Preregistro congelado:
`PREREGISTRO_INTRADIA_V1.md` (sha256 `bdfef5e8…03938d`; el harness aborta si no
coincide). Rama `fix/motor-r2`. Sin shell en esta sesión: lo ejecuta Hermes.

## 1. Idea central (sin tocar `src/`)

El executor no tiene flat por sesión (solo `max_bars_held`/`max_hold_minutes` y
`end_of_data_policy`). El flat se implementa como **corrida por día con warmup**:

- Clave de día: `src.session_calendar.session_date(ts)` con argumentos por
  defecto (convención del motor: TZ America/New_York, reset 17:00, skip-weekends).
- Slice del día S = **500 barras previas** (índices `i0-500:i0`, cualquier sesión,
  igual que `CALIBRATION_BARS_COUNT` de C1/H1) + **barras de la sesión S con
  `ts <= 15:10 CT de la fecha S`**.
- Cada slice corre `run_backtest` con `end_of_data_policy="close"`,
  `time_exit_mode="market"`, `end_of_data_slippage_points=0.25`: toda posición
  abierta al final del slice se cierra al close de la última barra + slip.
- `DayGatedStrategy`: si la última barra cerrada es `< day_start`, avanza el
  estado interno con `observe()` y devuelve `None`; desde `day_start` delega en
  `evaluate()`. El `_append` secuencial es idéntico al de una corrida continua,
  así que **EMAs/ATR/HTF del día son bit a bit iguales** a las de H1 sobre las
  mismas barras; `max_trades_day=3` se cuenta solo sobre el día (el interior
  nunca acepta en warmup).

## 2. Semántica exacta del corte 15:10 CT (con DST)

- Corte como **instante**: `datetime(S.año, S.mes, S.día, 15, 10, tzinfo=
  America/Chicago)` (`flat_cutoff_instant`). La comparación `bar.ts <= corte`
  usa timestamps aware → **DST correcto vía zoneinfo** sin ramas especiales
  (en marzo/noviembre el instante existe siempre: 15:10 local nunca cae en el
  hueco de primavera ni en la hora repetida de otoño).
- Lectura "rango", no "filtro de hora": entran **todas** las barras de la sesión
  S hasta el corte **inclusive**, incluida la apertura vespertina del día previo
  (p. ej. domingo 17:00 CT pertenece a la sesión del lunes y entra en su slice).
  Las barras de la sesión posteriores al corte (15:15 CT en adelante) se excluyen.
- Consecuencias documentadas: (a) la **última barra operable es la de las
  15:10 CT** (cubre 15:10–15:15) y el **fill forzado ocurre a su close
  (≈15:15 CT)** a `close ∓ slip`, redondeado a tick; (b) la **primera entrada
  posible del día es la apertura de la segunda barra del slice** (la señal se
  forma sobre barra cerrada y el fill es a la apertura siguiente); nunca hay
  entradas en warmup; (c) posiciones abiertas la noche civil previa **dentro de
  la misma sesión** (p. ej. domingo noche → lunes) se mantienen hasta el flat:
  nada cruza de sesión, pero sí puede cruzar la medianoche civil — ver duda D5.
- No hay gate de inicio adicional (sin restricción RTH): además de los gates
  propios de EMAS (`session_block="18,21,22,23"` ET, `blackout 09:15-09:45`),
  cualquier barra pre-corte de la sesión puede dar entrada. Ver duda D5.

## 3. Fills del cierre forzado (convención H1 para qué paga slip)

- El executor emite el cierre con `exit_reason="end_of_data"` (legado,
  `executor.py:579`; con `policy="close"` siempre cierra: `executor.py:554`).
  El harness lo **renombra a `exit_reason="eod_flat"`** en el ledger unificado y
  conserva el original en la columna extra `executor_exit_reason`.
- Pagan slip: entradas market y stops (`slippage_points`), salidas
  `time_exit` en modo market (`time_exit_slippage_points`), cierre forzado
  (`end_of_data_slippage_points`). El TP es límite y **no** paga slip.
  Idéntico a H1 salvo `end_of_data_slippage_points=0.25` (en H1 es 0.0 por
  omisión; aquí el flat depende de ese fill y la orden §3 lo fija en 0.25).
- Base realista: comisión 0.62/lado + 1 tick/lado (0.25 pts) en los tres knobs de
  slip. Sensibilidad: **+1 y +2 ticks por lado** suman 0.25/0.50 pts a los tres
  knobs a la vez (`build_config`); TP y comisiones intactos. Ver duda D1
  (el preregistro dice "1,5 ticks/lado" y H1 usa 1 tick: se implementó H1).

## 4. Qué se copió tal cual de H1 (y qué no)

Copiado verbatim (misma semántica, mismos redondeos):

- `load_market_bars_from_zip` (`run_h1.py:95-120`): mismo zip, miembro
  `databento/MNQ_M5.csv`, filtro `ts >= "2019-05-06"`, sha256 sobre bytes crudos.
  Paridad de carga: el manifiesto registra `bars_count`/`first`/`last`/`sha256`
  para comparar contra H1.
- Estrategia: `EmasStrategy(market=MNQ, target_rr=3.0, log_decisions=True)` con
  todos los defaults (incluido `max_trades_day=3`). OJO documentado: el `f=0.0`
  del brazo vive en el **executor** (`partial_take_profit_fraction=0.0`, sin BE
  → ruta legacy), no en el objeto estrategia (cuyo `f` por defecto es 0.5 e
  inerte en esa ruta). Ver duda D8.
- Pines del executor: todos los de `run_h1.py:65-87` **incluido
  `fixed_quantity=None`** (sizing por riesgo, budget $500 = 1% de $50k;
  `max_contracts=1_000_000`). "1 contrato" aplica solo al yardstick. Ver D2.
- `r_result` **sin recalcular**: sale del executor (`net_pnl / $500`,
  budget-R) y se serializa con los mismos redondeos (`serialize_trade`:
  4 decimales PnL, 6 decimales R). `effective_r` (= stop-distance-R) también.
- Agregados: `mean_er/mean_stop_r/PF/win_rate/net_r/net_pnl/max_drawdown_r`,
  CBB seed 42 vía `run_bootstrap_ci` (regla G5: aborta si None con n≥5),
  `trades_sha256`, `qty_distribution`, atribución slip/comisión.
- Plan walk-forward: `WalkForwardPlan.create_calendar_rolling` 36/6/6 idéntico;
  assert `n_folds == 8`; tramo dev folds 0..5; **G4**: los días que tocan
  ventanas 6..7 se saltan enteros (contador `n_days_skipped_sealed`) y hay
  assert `fold_id < 6` por trade.

No copiado (propio del experimento): partición por día, wrapper, renombre a
`eod_flat`, columnas extra (`fold_id`, `session_date`, `executor_exit_reason`),
semillas del preregistro intradía, yardstick Topstep.

## 5. Asignación de folds y G4

- Cada trade se asigna por **índice de su barra de entrada** a la ventana test
  que lo contiene (misma partición que las corridas por-fold de H1: un trade
  pertenece al fold de su entrada).
- Solo corren días que tocan ventanas dev **sin tocar selladas**. Días fuera de
  dev se cuentan (`n_days_out_of_scope`); días vacíos hasta el corte se cuentan.
- Entradas en bordes fuera de dev (p. ej. evening previo al inicio de un test):
  se **dropean y cuentan** (`n_trades_dropped_out_of_window`), no se reasignan.
  Entradas en 6..7: assert → aborta (G4). Ver duda D6.

## 6. Controles y semillas (cola acotada)

- (a) **Permutación de R** (`Generator(seed_perm).permutation(r)`): misma
  distribución y momentos, sin estructura temporal. (b) **Permutación de R
  centrados** (`seed_centered`, media 0). Fechas fijas (`session_date`) para
  conservar la geometría de calendario en `day_close`. Ver duda D7.
- Derivación: `SeedSequence(20260928).spawn(4)` → `[0]=seed_mbb` (mismo slot que
  T1), `[1]` reservado sin uso (slot iid de T1), `[2]=seed_perm`,
  `[3]=seed_centered`. Todas declaradas en manifiestos y salidas.
- Pareo: candidato y controles comparten **n, fechas, bloque MBB y seed_mbb** →
  misma matriz de índices y timelines → réplicas pareadas. Delta =
  `pass_ctrl − pass_cand ∈ {−1,0,1}` por réplica; IC95 normal sobre las B
  diferencias (`paired_delta`). El bloque es el óptimo Politis&White
  (`optimal_block_length`, circular, ceil) calculado sobre la candidata y
  reutilizado en los controles.

## 7. Yardstick (plantilla `t1_orb_topstep_50k.py`, bloque `correr`)

- `bootstrap_camino` con `trailing_update_cadence="end_of_day"`,
  `consistency_day_consolidation="day_close"`, perfil `topstep_50k_profile`
  (assert start 50k / target 3k / MLL 2k / lock 50k), **1 contrato**,
  B=2.000, escenarios A (cons 0.55, min2, sin DLL) / B (0.50) /
  C (0.50 + DLL $1.000 terminal como cota). Controles bajo A y B.
- **1R ≡ $200/contrato** (convención T1: `PTO=200`, `risk_pct=2·cap/start`):
  el yardstick valora la secuencia de R igual que en ORB aunque el budget del
  backtest EMAS sea $500. Ver duda D3.
- Orden del ledger para el yardstick: por `exit_time`; fechas = `session_date`.
- Lectura operativa (`evaluate_conditions`, la etiqueta la pone Hermes):
  A = delta(A, perm) < 0 con IC95 sin cruzar cero en base; B = igual en +1 tick.
  Gate n≥100 por nivel: si n<100 → **ABSTENCIÓN por muestra** (no INCONCLUSA).

## 8. Salidas y determinismo

- Por nivel de slip: `cand1_ledger_{base,slip+1ticks,slip+2ticks}.csv`
  (19 columnas), `cand1_metrics_*.json` (metadata/dataset/aggregate/folds con
  trades), `cand1_yardstick_*.{json,txt}`, más `cand1_manifest.json` global.
- Determinismo: días en orden cronológico, ledger ordenado por
  `(entry_time, exit_time, direction, trade_id)`, sin iteración sobre sets en
  salidas, RNG solo con semillas declaradas. `trades_sha256` por nivel para la
  doble corrida bit a bit de Hermes. Flags: `--smoke` (10 días, base, B=200,
  sufijo `_SMOKE`, no es resultado), `--slip-extra {0,1,2}`,
  `--no-yardstick` (solo ledger).
- Prohibiciones respetadas: solo se escribe bajo `lab_artifacts/
  intradia_protocol/`; `src/`, `run_h1.py`, folds 6..7 y preregistro intactos
  (verificado por sha al arrancar). Nada ejecutado aquí (sin shell).

## 9. Decisiones abiertas para Hermes (ver respuesta para el detalle)

D1 base 1 vs 1,5 ticks · D2 sizing risk-based vs 1 contrato en backtest ·
D3 1R≡$200 vs $500 en yardstick · D4 última barra 15:10 (fill ≈15:15) vs 15:05 ·
D5 entradas overnight/evening dentro de la sesión vs solo RTH ·
D6 dropear bordes vs reasignar · D7 bloque/fechas de controles ·
D8 f del objeto estrategia (0.5 inerte) vs f del executor (0.0).
