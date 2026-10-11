# T3 — Revisión cruzada y sensibilidad de costos

Fecha de revisión: 2026-10-10

Alcance: T1 ORB MNQ, motor de cuenta y sensibilidades `slip+1.0`/`slip+2.0`.

Modo: auditoría independiente; no se modificaron `t1_orb_topstep_50k.py` ni `t4_pool_topstep_50k.py`.

## 1. Verdict

**NECESITA REVISIÓN. El resultado no aguanta +1 tick como evidencia de ventaja frente al control emparejado.** Con +1 tick por lado, A cae de 40.10% a 34.00% (-6.10 pp): todavía supera al control sin deriva por 7.60 pp, pero queda 3.35 pp por debajo del iid emparejado (680 vs 747 pases). Ya en el base queda 2.60 pp por debajo del iid, por lo que no había superioridad predictiva que preservar. Además, la cifra no puede aceptarse todavía como simulación de las reglas declaradas: el backtest cierra a las 14:30 CT, no a las 15:10 CT, y el MLL ratchetea el pico después de cada trade, no sólo al EOD. Esas dos divergencias invalidan la etiqueta “Topstep 50K · flat 15:10 CT” hasta una corrida corregida. La reejecución independiente de `+2.0` produjo 589/2000 pases para A, 528/2000 para sin deriva y 646/2000 para iid, iguales al archivo generado (`T1_ORB_TOPSTEP_50K_RESULTADOS_slip+2.0.txt:12-21`).

## 2. Hallazgos ordenados por severidad

### CRÍTICO 1 — El supuesto flat 15:10 CT se ejecuta a las 14:30 CT

T1 anuncia flat 15:10 (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:64`) pero construye `Config` sin cambiar `trade_end` (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:70-74`). El valor heredado es 14:30 (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:130-137`); el motor define el último bar desde `trade_end` (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:218-219`) y cierra largos/shorts allí (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:319-332`). El chequeo reproducible confirma `max_exit_clock=14:30` y 2,870 cierres a esa hora (`lab_artifacts/orb_protocol/T3_AUDIT_CHECKS.txt:6-7`). Por tanto, la paridad probada es MNQ/eod-14:30 del motor, no flat oficial 15:10.

**Impacto:** se excluyen 40 minutos diarios de exposición; el P(pasar), el ledger de 3,594 trades y la sensibilidad no responden a la pregunta declarada.

**Fix propuesto:** crear una variante T3/T1 nueva que fije explícitamente la hora correcta y vuelva a generar ledger, paridad, MBB y controles. No corregir el artefacto T1 congelado.

### CRÍTICO 2 — El MLL no actualiza el umbral sólo al EOD

El perfil declara `update_cadence="end_of_day"`, lock en balance inicial y monitoreo por trade cerrado (`src/funded_profiles.py:310-320`). Sin embargo, la simulación sube `peak_equity` inmediatamente después de cada trade (`lab_artifacts/orb_protocol/bootstrap_camino.py:315-325`) y calcula en ese mismo paso el piso `min(peak - MLL, ceiling)` (`lab_artifacts/orb_protocol/bootstrap_camino.py:354-369`). No hay estado separado de “máximo EOD confirmado”. El lock a $50,000 sí está codificado, pero la cadencia del ratchet es intradía/por trade.

**Impacto:** después de una ganancia y una pérdida el mismo día, el motor puede elevar el piso antes del cierre diario y quemar una réplica que el MLL EOD no quemaría. Esto afecta directamente el único estado de fracaso observado en A/B/C.

**Fix propuesto:** mantener un `eod_peak_balance` que sólo se actualice al cambio/cierre de día; monitorear contra el piso derivado de ese pico y añadir un test con dos trades el mismo día (+ganancia, luego pérdida).

### ALTO 1 — Los SL/TP tocados en la vela de entrada se ignoran

El motor sólo evalúa salidas si la vela actual es posterior a `entry_time` (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:300-304`), pero abre la posición usando el high/low de esa misma vela (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:337-370`). El stop-first conservador existe para velas posteriores (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:310-330`), no para la vela de entrada. La auditoría encontró 16/3,594 entradas cuya vela también toca una salida: 16 stops, 1 target y 1 vela que toca ambos (`lab_artifacts/orb_protocol/T3_AUDIT_CHECKS.txt:1-5`).

**Impacto:** el orden intrabarra es desconocido y el edge base es sólo +0.0134 R/trade; 16 casos ambiguos son materiales frente a un cambio de costo de 0.005 R/trade.

**Fix propuesto:** resolver esos casos con los datos de 1 minuto o aplicar una convención preregistrada conservadora en una corrida nueva.

### ALTO 2 — La interpretación “1 tick = 0.125 R/trade” es incorrecta

T1 fija stop de 100 puntos y $200 de riesgo (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:50-51,77-80`). El costo RT usa dos lados por `ticks_per_side × tick_value` (`E:/FARS-LAB/ext_review2/nq-intraday-breakout/src/nq_breakout/backtest.py:149-152`). En MNQ, +1 tick **por lado** agrega $1.00 RT: `2 × $0.50`; dividido por $200 son **0.005 R/trade**, no 0.125 R. El chequeo lo reproduce (`lab_artifacts/orb_protocol/T3_AUDIT_CHECKS.txt:8-9`) y los runs bajan E[R] exactamente ≈0.005 por nivel: +0.0134, +0.0084, +0.0034 (`T1_ORB_TOPSTEP_50K_RESULTADOS.txt:4`, `T1_ORB_TOPSTEP_50K_RESULTADOS_slip+1.0.txt:4`, `T1_ORB_TOPSTEP_50K_RESULTADOS_slip+2.0.txt:4`). La predicción textual de que +1 tick haría negativa la media (`E:/FARS-LAB/T1_ORB_TOPSTEP_50K.md:56-58`) queda refutada; la corrida numérica del script sí cobra el costo correcto.

### MEDIO 1 — “Mejor día” usa el máximo intradía acumulado, no el P&L final del día

`day_pnl` se reinicia al cambiar de fecha (`lab_artifacts/orb_protocol/bootstrap_camino.py:250-258`), pero `best_day_pnl` se actualiza después de cada trade (`lab_artifacts/orb_protocol/bootstrap_camino.py:315-325`). Así, un día +$800 y luego -$400 queda registrado como mejor día de $800, no $400. La fórmula parametrizable `best_day/ratio` sí es correcta (`lab_artifacts/orb_protocol/bootstrap_camino.py:371-375`). En T1 a un contrato no llega a atar —A y B son idénticos—, pero la regla general está modelada con una definición distinta.

**Fix propuesto:** consolidar `best_day_pnl` al cerrar/cambiar el día y probar explícitamente un día con dos trades de signos opuestos.

### MEDIO 2 — DLL soft y cap de contratos no están representados por el motor de cuenta

El DLL terminal está soportado: al cruzarlo asigna `breach_daily` y rompe (`lab_artifacts/orb_protocol/bootstrap_camino.py:345-352`). Con `stop_on_daily_loss=False` no hay pausa del resto de la sesión: la simulación continúa, por lo que no representa “flatten + pausa”. T1 C usa la variante terminal como cota (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:164-166`) y el base simula “sin DLL” mediante un umbral $25,000 (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:52-53`).

El perfil contiene caps 5 minis/50 micros (`src/funded_profiles.py:253-263,324-327`), pero `simulate_funded_trajectory` no recibe ni consulta esos campos; `FundedAccountRules` tampoco tiene cap por contratos (`src/types.py:175-188`). T1 queda dentro del cap únicamente porque fija manualmente `K=1` (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:48-51`).

### BAJO 1 — El perfil canónico dice 5 días, aunque T1 fuerza correctamente 2

El perfil guarda `minimum_trading_days=5` (`src/funded_profiles.py:319-323`), mientras T1 pasa `min_trading_days=2` en A/B/C (`lab_artifacts/orb_protocol/t1_orb_topstep_50k.py:129-130,164-166`). El resultado efectivo T1 sí exige dos días porque el motor registra las fechas operadas y comprueba el mínimo antes del pase (`lab_artifacts/orb_protocol/bootstrap_camino.py:315-317,371-378`). Queda una inconsistencia de fuente/configuración que puede afectar otros consumidores del perfil.

### Matriz de revisión de los ocho errores conocidos

| Chequeo | Dictamen | Evidencia |
|---|---|---|
| 1. Look-ahead | **OK en lo inspeccionado** | Barras ordenadas y recorridas secuencialmente (`backtest.py:198-210,271-299`); ventana 10:00 exclusiva por default (`backtest.py:64-67,290-298`). No se usan HTF ni fechas futuras. |
| 2. Fills fantasmas | **DIFERENTE / hallazgo ALTO** | Gap-through sí llena al open (`backtest.py:310-330`) y doble toque posterior usa stop-first, pero se omite la salida en vela de entrada (`backtest.py:300-304,337-370`); 16 casos (`T3_AUDIT_CHECKS.txt:1-5`). |
| 3. Remuestreo endógeno | **OK** | MBB se construye sobre `r_led` bruto (`t1_orb_topstep_50k.py:94-101,141-142`) y cada réplica aplica luego el motor de cuenta al mismo `resample` (`bootstrap_camino.py:463-498`). |
| 4. Riesgo aplicado vs declarado | **OK con caveat** | R divide el movimiento neto real —incluido gap— por stop nominal 100 pts (`t1_orb_topstep_50k.py:77-80`), y la cuenta multiplica ese R por el riesgo nominal (`bootstrap_camino.py:308-321`). Un gap puede perder >1R, correctamente, aunque el “riesgo $200” es presupuesto y no pérdida máxima. |
| 5. Estados terminales solapados | **OK** | Cada condición asigna un estado y hace `break` (`bootstrap_camino.py:345-378`). En los tres runs, target + breach = 2,000 en cada brazo (`T1_ORB_TOPSTEP_50K_RESULTADOS.txt:12-21`; archivos slip: líneas 12-21). |
| 6. Reloj sintético | **OK** | El timeline preserva gaps dentro de bloque y remuestrea interarribos empíricos en costuras (`bootstrap_camino.py:123-149`); meses usan diferencia temporal del timeline consumido (`t1_orb_topstep_50k.py:133-136`), no `trades/mes` ni cupos. |
| 7. Paridad MNQ/eod | **OK, pero es eod-14:30** | Assert vivo sólo en base: `n=3594` y tolerancia sobre E[R]=0.013403 (`t1_orb_topstep_50k.py:82-90`); resultado base (`T1_ORB_TOPSTEP_50K_RESULTADOS.txt:4`). |
| 8. Controles | **OK** | Sin deriva resta la media conservando la forma; iid usa Normal con media/sigma del ledger y semilla separada (`t1_orb_topstep_50k.py:98-101,169-179`). |

Los tests seleccionados pasaron (23/23): `test_backtest.py` completo y `test_topstep_consistency_y_min_dias`. Esto verifica el comportamiento implementado, no corrige las diferencias de especificación anteriores.

## 3. Tabla de sensibilidad slip+0 / +1 / +2

Probabilidades reconstruidas desde los conteos de B=2,000. Cada celda de escenario/control muestra `P(pasar) [delta vs slip+0]`.

| Slippage extra por lado | E[R] | A · cons .55 | B · cons .50 | C · cons .50 + DLL terminal | CTRL sin deriva | CTRL iid emparejado |
|---:|---:|---:|---:|---:|---:|---:|
| +0 ticks | +0.0134 | 40.10% [0.00] | 40.10% [0.00] | 40.10% [0.00] | 26.40% [0.00] | 42.70% [0.00] |
| +1 tick | +0.0084 | 34.00% [-6.10 pp] | 34.00% [-6.10 pp] | 34.00% [-6.10 pp] | 26.40% [0.00 pp] | 37.35% [-5.35 pp] |
| +2 ticks | +0.0034 | 29.45% [-10.65 pp] | 29.45% [-10.65 pp] | 29.45% [-10.65 pp] | 26.40% [0.00 pp] | 32.30% [-10.40 pp] |

| Slippage extra | A − sin deriva | A − iid emparejado | P(quemar) A | Mediana si pasa A |
|---:|---:|---:|---:|---:|
| +0 | +13.70 pp | -2.60 pp | 59.90% | 13.21 meses |
| +1 | +7.60 pp | -3.35 pp | 66.00% | 12.98 meses |
| +2 | +3.05 pp | -2.85 pp | 70.55% | 13.14 meses |

Fuentes: base (`lab_artifacts/orb_protocol/T1_ORB_TOPSTEP_50K_RESULTADOS.txt:12-25`), +1 (`lab_artifacts/orb_protocol/T1_ORB_TOPSTEP_50K_RESULTADOS_slip+1.0.txt:12-25`) y +2 reejecutado (`lab_artifacts/orb_protocol/T1_ORB_TOPSTEP_50K_RESULTADOS_slip+2.0.txt:12-25`). La tabla usa conteos exactos; por eso 747/2,000 aparece como 37.35% aunque el log imprime 37.4%.

**Dictamen de sensibilidad:** si “aguanta” significa conservar ventaja frente a ambos controles, **NO**. A conserva ventaja frente al control sin deriva a +1, pero ya pierde contra el iid emparejado en base y pierde más a +1. Si el único umbral fuese superar al control sin deriva, entonces sí lo supera a +1; esa lectura no demuestra edge específico del ORB.

## 4. Reglas — OK / DIFERENTE / FALTA

| Regla | Estado | Evidencia y alcance |
|---|---|---|
| MLL trailing EOD con piso lockeado en balance inicial | **DIFERENTE** | Lock correcto (`bootstrap_camino.py:365-369`), pero pico actualizado por trade (`bootstrap_camino.py:315-325`) pese a `end_of_day` en perfil (`src/funded_profiles.py:310-320`). |
| Consistencia `target = mejor_día / ratio`, ratio .55/.50 | **DIFERENTE** | Fórmula y parámetro correctos (`bootstrap_camino.py:371-375`; `t1_orb_topstep_50k.py:121-130`), pero “mejor día” toma máximo intradía acumulado (`bootstrap_camino.py:323-325`) en vez del cierre diario. No ata a K=1. |
| `min_trading_days` | **OK en T1** | T1 fuerza 2 y el pase cuenta días distintos (`t1_orb_topstep_50k.py:129-130,164-166`; `bootstrap_camino.py:315-317,375`). El perfil general aún dice 5 (`src/funded_profiles.py:321`). |
| DLL terminal vs soft | **DIFERENTE** | Terminal está implementado (`bootstrap_camino.py:345-352`); no existe pausa del resto de sesión para la variante soft. |
| Flat 15:10 CT, sin overnight | **DIFERENTE** | No hay overnight por `max_long_hold_days=1`, pero el cierre real es 14:30 (`backtest.py:130-137,218-219,319-332`; `T3_AUDIT_CHECKS.txt:6-7`). |
| Cap de contratos | **FALTA en el motor de cuenta** | El perfil declara 5 minis/50 micros (`src/funded_profiles.py:253-263,324-327`), pero el motor no lo consume. T1 sólo queda dentro por `K=1` hard-coded (`t1_orb_topstep_50k.py:48-51`). |

## 5. Unknowns / blockers

1. **Semántica exacta del flat:** ¿la posición debe liquidarse a las 15:10:00 CT, al close de la vela etiquetada 15:10 (15:14:59 por barras left-edge), o usando una orden anterior que garantice estar flat a las 15:10? Sin esa decisión no hay fill canónico.
2. **MLL EOD:** falta fijar explícitamente si el piso derivado del EOD previo se monitorea intradía por trade cerrado o sólo al EOD. El perfil actual dice actualización EOD y monitoreo closed-trade (`src/funded_profiles.py:310-320`), pero el runner no separa ambas cadencias.
3. **Orden intrabarra:** 16 entradas requieren resolución con M1 o una convención conservadora (`lab_artifacts/orb_protocol/T3_AUDIT_CHECKS.txt:2-5`). Con OHLC de 5 minutos no puede saberse si el breakout ocurrió antes o después del stop/target.
4. **Impacto cuantitativo de las dos divergencias críticas:** no se ejecutó una variante corregida a 15:10 + ratchet EOD porque T1 está congelado y esas decisiones cambian el experimento. Hasta correrla, 40.1%/34.0%/29.45% describen el motor actual, no la especificación declarada.
5. **Perfil vs T5 en días mínimos:** confirmar si `src/funded_profiles.py:321` debe cambiar de 5 a 2 para que otros consumidores no hereden una regla distinta de T1.
