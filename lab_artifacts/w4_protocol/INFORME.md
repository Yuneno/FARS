# INFORME W4 — candidata B Wednesday Weekly Cycle — status ABSTENCION

Preregistro congelado sha256: `a80b5746c8073d8a314629c12b52669d4caebcefa5146f4501a8f3cebaa60dd4`

## Parametros (pins del preregistro)

- target 1.5R absoluto desde entry estimado (Alt B); drift R nominal vs efectivo por trade
- salida primaria: primera barra >= 16:00 ET del mismo dia (390 min)
- reloj: signal 09:25 ET / fill 09:30 ET / 300 s; sesiones: 78 slots [09:30,16:00) ET, basis identico
- costes realista: 0,62 USD/side + 0,25 pt; sensibilidad: 2,0 pts RT exactos (1,0 pt/side comision, slip 0)
- seeds: cbb=20260930, control=20261001
- n_min: 100 trades por mercado (dev < 100 -> ABSTENCION, no se abre el sellado)
- stop: extremo del barrido del martes (margen 0), tick por mercado, polaridad estricta
- sizing: cantidad fija 1 contrato; headline stop-R (primario), budget-R (secundario)
- G5 fail-fast SOLO sobre stop_R_dev_MNQ y stop_R_tail_MNQ

## Metricas

- MNQ (confirmatorio)/B_on: status=ABSTENCION signals_dev=30 n=30 E[stop-R]=-0.0783 CI95=ABSTENCION E[budget-R]=+0.0670 gaps=0 beyond=0 unres=0
- MNQ (confirmatorio)/B_flip: status=ABSTENCION signals_dev=30 n=30 E[stop-R]=+0.0190 CI95=ABSTENCION E[budget-R]=-0.0329 gaps=0 beyond=0 unres=0
  - flip apareado: n_comun=30 E[on-flip]=-0.0973 CI95=ABSTENCION
  - control pareado: n=30 mean=-0.2722 CI95=ABSTENCION
  - comparador temporal puro (declarado): n=30 E[stop-R]=-0.1496 (sin target 1.5R; conserva stop estructural; salida temporal única (sub-análisis, sin estatus de test))
  - inside_close=OFF (declarado): signals_dev=86 n=86 dias_comunes_n=30 E[on-off]=+0.0000 (sin estatus de test)
  - drift (variacion distancia al stop): n=30 mean=0.006812 p5=-0.002468 p95=0.031279 min=-0.002976 max=0.051724
  - R_efectivo_target=|target-entry_fill|/|entry_fill-stop| (R_nominal=1.5): n=30 mean=1.483547 p5=1.422934 p95=1.506186 min=1.377049 max=1.507463
  - sellado: abierto=False motivo=dev-n-30-lt-100-ABSTENCION dev_pasa=False ya_abierto=False
- MES (exploratorio)/B_on: status=ABSTENCION signals_dev=28 n=28 E[stop-R]=-0.1353 CI95=ABSTENCION E[budget-R]=-0.1179 gaps=0 beyond=0 unres=0
- MES (exploratorio)/B_flip: status=ABSTENCION signals_dev=28 n=28 E[stop-R]=+0.2802 CI95=ABSTENCION E[budget-R]=+0.2131 gaps=0 beyond=0 unres=0
  - flip apareado: n_comun=28 E[on-flip]=-0.4155 CI95=ABSTENCION
  - control pareado: n=28 mean=-0.1243 CI95=ABSTENCION
  - comparador temporal puro (declarado): n=28 E[stop-R]=+0.0319 (sin target 1.5R; conserva stop estructural; salida temporal única (sub-análisis, sin estatus de test))
  - inside_close=OFF (declarado): signals_dev=89 n=89 dias_comunes_n=28 E[on-off]=+0.0000 (sin estatus de test)
  - drift (variacion distancia al stop): n=28 mean=0.016099 p5=0.0 p95=0.047232 min=0.0 max=0.1
  - R_efectivo_target=|target-entry_fill|/|entry_fill-stop| (R_nominal=1.5): n=28 mean=1.461315 p5=1.376363 p95=1.5 min=1.272727 max=1.50303
  - sellado: abierto=False motivo=dev-n-28-lt-100-ABSTENCION dev_pasa=False ya_abierto=False
- MYM (exploratorio)/B_on: status=ABSTENCION signals_dev=33 n=33 E[stop-R]=-0.2205 CI95=ABSTENCION E[budget-R]=-0.0865 gaps=0 beyond=0 unres=0
- MYM (exploratorio)/B_flip: status=ABSTENCION signals_dev=33 n=33 E[stop-R]=-0.2025 CI95=ABSTENCION E[budget-R]=+0.1037 gaps=0 beyond=0 unres=0
  - flip apareado: n_comun=33 E[on-flip]=-0.0180 CI95=ABSTENCION
  - control pareado: n=33 mean=-0.0153 CI95=ABSTENCION
  - comparador temporal puro (declarado): n=33 E[stop-R]=-0.2603 (sin target 1.5R; conserva stop estructural; salida temporal única (sub-análisis, sin estatus de test))
  - inside_close=OFF (declarado): signals_dev=86 n=86 dias_comunes_n=33 E[on-off]=+0.0000 (sin estatus de test)
  - drift (variacion distancia al stop): n=33 mean=0.005483 p5=-0.008882 p95=0.048955 min=-0.071429 max=0.153846
  - R_efectivo_target=|target-entry_fill|/|entry_fill-stop| (R_nominal=1.5): n=33 mean=1.474596 p5=1.216969 p95=1.526087 min=1.0 max=1.692308
  - sellado: abierto=False motivo=dev-n-33-lt-100-ABSTENCION dev_pasa=False ya_abierto=False

## Deltas respecto a run_n1.py (fork, importacion prohibida)

- 1. Senal: N1 intradia (sweep 9:00-9:30 + rechazo / OR-breakout 9:30-10:00 con k_d*ATR) -> W4 semanal D1 lunes/martes (barrido + cierre interior estrictos; igualdad no cuenta; doble sweep / sin cierre interior / sin barrido -> NO_SIGNAL). Sin ATR, sin k_d, sin k_stop, sin percentil de vol.
- 2. Sesiones por FECHAS EXACTAS lun/mar de la misma semana + completitud 78 slots [09:30,16:00) ET con basis identico; dedup (iso_year, iso_week) max 1 entrada/semana (interanual incluido).
- 3. Reloj senal 09:25 ET (close = entry estimado) / fill 09:30 ET open, bar_interval=300 (vs reloj intradia N1).
- 4. Stop estructural = extremo del barrido del martes, margen 0 (vs stop N1 con k_stop*ATR); target 1.5R ABSOLUTO desde entry estimado (Alt B) con drift R nominal vs efectivo medido POR TRADE (media/p5/p95/min/max, sin claims escalares).
- 5. Salida primaria primera barra >= 16:00 ET del mismo dia (390 min, vs 12:00 ET en N1); sensibilidad = SOLO escenario de costes 2.0 pts RT (sin variante de 60 barras ni event-study K=20).
- 6. mirror_valid_levels extendido: fill que cruza el TARGET -> gap_reject_beyond_target (evita TP instantaneo espurio), con contador diag propio.
- 7. Sizing cantidad fija 1 contrato (vs riesgo 1% sobre 50k en N1); headline stop-R primario + budget-R secundario; risk_dollars + elegibilidad Practice <= $200 solo telemetria.
- 8. Brazos B_on (H_B) + B_flip (misma D1, lado opuesto, apareada trade a trade) + comparador temporal puro + require_inside_close=OFF como sub-analisis declarados SIN estatus de test (vs A_on/A_off/C + event-study + regresion en N1).
- 9. n_min=100 trades por mercado (vs 150/100 en N1); dev < 100 -> ABSTENCION y NO se abre el sellado; G5 fail-fast SOLO sobre stop_R_dev_MNQ y stop_R_tail_MNQ (comparador/exploratorios/sensibilidad/drift excluidos).
- 10. Mercados --markets MNQ,MES,MYM por defecto (cada uno con su tick/dollar/coste); MGC EXCLUIDO por pin y rechazado por CLI (gate=MGC); rolls POR MERCADO via --roll-dates (declarados en manifest).
- 11. Sin imports de run_n1 (fork standalone autocontenido); artefactos metrics_<MKT>_W4.json / w4_status.json.
- 12. Regla A1 (solo SL en vela de fill) por composicion sin tocar src/: el motor corre con target lejano (fill, SL, gap-stop y salida temporal fieles; nunca TP) y el target real se re-arma desde la siguiente vela (prioridad SL; TP a precio target sin slippage).
- 13. B_flip apareada de verdad (C3): ambos brazos corren SOLO sobre dias elegibles por los dos (invariante N_on==N_flip y mismos timestamps, gate=G1); G1 a nivel de senal (C4); sellado solo si dev pasa con lock persistente w4_sealed.lock (C2); slots 78 exactos con segundos a cero y basis canonico W2 (W2); rolls solo el miercoles (W3); R_efectivo_target + atr14 por trade (W4); comparador temporal con redaccion literal (W5).

## Anomalias observadas

- MNQ: {"martes_incompleto": 284, "lunes_incompleto": 172, "no_signal_sin_cierre_interior": 317, "reloj_sin_barra_0925": 12, "no_signal_sin_barrido": 150, "colapso_tick": 118, "falta_lunes": 4, "no_signal_doble_sweep": 128, "senal_long": 238, "senal_short": 260, "falta_martes": 4, "reloj_sin_barra_0930": 1}
- MES: {"martes_incompleto": 284, "lunes_incompleto": 172, "no_signal_sin_cierre_interior": 317, "reloj_sin_barra_0925": 19, "colapso_tick": 109, "falta_lunes": 4, "no_signal_doble_sweep": 146, "senal_long": 251, "no_signal_sin_barrido": 116, "falta_martes": 4, "senal_short": 265, "reloj_sin_barra_0930": 1}
- MYM: {"martes_incompleto": 288, "lunes_incompleto": 174, "no_signal_sin_cierre_interior": 312, "reloj_sin_barra_0925": 15, "no_signal_sin_barrido": 122, "colapso_tick": 113, "falta_lunes": 4, "no_signal_doble_sweep": 140, "senal_long": 253, "senal_short": 266, "falta_martes": 4, "reloj_sin_barra_0930": 1}

## Cobertura de gates (W1: lo no cubierto se declara, sin PASS no acreditado)

- MNQ G1: paridad-a-nivel-de-senal (week_key,side,stop,target) realista-vs-sensibilidad por brazo; drops post-prechequeo excluidos (los concilia G2)
- MNQ G2: mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles en solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final) por brazo x segmento x escenario; pares no apareados (C3) cuentan como excluidos en diag, no como drops
- MNQ G3: regeneracion-completa-de-senales (compute_signal_W4+flip, dev+sellado) + recorrida-motor-realista-y-sensibilidad-dev-4-brazos; sellado-motor-no-recorrido (ver nota)
- MNQ G2-dev: mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles en solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final) por brazo x segmento x escenario; pares no apareados (C3) cuentan como excluidos en diag, no como drops
- MES G1: paridad-a-nivel-de-senal (week_key,side,stop,target) realista-vs-sensibilidad por brazo; drops post-prechequeo excluidos (los concilia G2)
- MES G2: mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles en solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final) por brazo x segmento x escenario; pares no apareados (C3) cuentan como excluidos en diag, no como drops
- MES G3: regeneracion-completa-de-senales (compute_signal_W4+flip, dev+sellado) + recorrida-motor-realista-y-sensibilidad-dev-4-brazos; sellado-motor-no-recorrido (ver nota)
- MES G2-dev: mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles en solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final) por brazo x segmento x escenario; pares no apareados (C3) cuentan como excluidos en diag, no como drops
- MYM G1: paridad-a-nivel-de-senal (week_key,side,stop,target) realista-vs-sensibilidad por brazo; drops post-prechequeo excluidos (los concilia G2)
- MYM G2: mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles en solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final) por brazo x segmento x escenario; pares no apareados (C3) cuentan como excluidos en diag, no como drops
- MYM G3: regeneracion-completa-de-senales (compute_signal_W4+flip, dev+sellado) + recorrida-motor-realista-y-sensibilidad-dev-4-brazos; sellado-motor-no-recorrido (ver nota)
- MYM G2-dev: mapeo-trade-a-senal + reconciliacion exacta outputs==senales elegibles en solo-dev-4-brazos-realista-y-sensibilidad (ver G2 final) por brazo x segmento x escenario; pares no apareados (C3) cuentan como excluidos en diag, no como drops

## Limitaciones declaradas

- Rolls: lista por mercado via --roll-dates; SOLO excluye entradas en fecha de roll del miercoles (pin literal); roll en lunes/martes se cuenta en diag 'semana_con_roll_en_lunes_o_martes' sin filtrar; lista vacia = limitacion declarada (sin calendario ex-ante).
- Festivos/early-closes: sin calendario ex-ante; se detectan post-hoc (lunes/martes incompleto -> NO_SIGNAL; falta barra 16:00 -> unresolved).
- MGC excluido por pin (sesion 08:20-13:30 ET incompatible con salida 16:00 ET).
- Parser rechaza timestamps duplicados; agrupacion por fecha ET.

## Confianza en el calculo

- MNQ: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- MES: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- MYM: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- Conclusion cuantitativa: DIFERIDA a Hermes (multiplicidad >130 comparaciones; sin alfa pre-fijado).
