# INFORME N1 — status ABSTENCION

Preregistro congelado sha256: `f9a1a419fe96d46f072db89c97190f6cff0620cf12985c88d911b3dc98babd16`

## Parametros

- k_d=0.1, k_stop=0.25, ATR(14) Wilder M5
- salida primaria: primera barra >= 12:00 ET; sensibilidad: 60 barras
- costes realista: 0,62 USD/side + 0,25 pt; sensibilidad: 2,0 pts RT exactos (1,0 pt/side comision, slip 0)
- seeds: cbb=20260928, control=20260929
- N_min: 150 trades (MNQ) / 100 eventos stage1
- stop A: extremo de la barra de barrido (high/low) +- k_stop*ATR; stop C: cierre breakout -+ k_stop*ATR (ancla C: gap declarado)
- sizing: riesgo 1% sobre 50k (motor canonico, max_contracts por defecto=10)

## Metricas

- MNQ (confirmatorio)/A: status=ABSTENCION n=0 E[budget-R]=+0.0000 E[stop-R]=+0.0000 CI95=ABSTENCION gaps=0 unres=0
  - stage1 event-study K=20: n=457 mean=-6.7708pts CI95=[-16.1773, 2.1173] gate=ABSTENCION-stage1(CI-no->0)
- MNQ (confirmatorio)/C: status=OK n=620 E[budget-R]=-0.0736 E[stop-R]=-0.1912 CI95=[-0.1652, 0.0258] gaps=0 unres=0
  - regresion beta=0.15286764564917157 se=0.15137291463984592 p_one_sided=0.1564753240476315 n=620
  - variante pct>50: n=267 E[R]=-0.0493 CI95=[-0.2112, 0.1357]
- MES (exploratorio)/A: status=ABSTENCION n=0 E[budget-R]=+0.0000 E[stop-R]=+0.0000 CI95=ABSTENCION gaps=0 unres=0
  - stage1 event-study K=20: n=463 mean=-0.7673pts CI95=[-2.5475, 1.0643] gate=ABSTENCION-stage1(CI-no->0)
- MES (exploratorio)/C: status=OK n=636 E[budget-R]=-0.0871 E[stop-R]=-0.4616 CI95=[-0.1294, -0.0395] gaps=0 unres=0
  - regresion beta=-0.07367436644638425 se=0.07683753547470071 p_one_sided=0.8309958149730526 n=636
  - variante pct>50: n=284 E[R]=-0.1082 CI95=[-0.1826, -0.0251]
- MYM (exploratorio)/A: status=ABSTENCION n=0 E[budget-R]=+0.0000 E[stop-R]=+0.0000 CI95=ABSTENCION gaps=0 unres=0
  - stage1 event-study K=20: n=434 mean=+0.8894pts CI95=[-11.0511, 12.7725] gate=ABSTENCION-stage1(CI-no->0)
- MYM (exploratorio)/C: status=OK n=575 E[budget-R]=-0.0374 E[stop-R]=-0.2118 CI95=[-0.0704, -0.0022] gaps=0 unres=0
  - regresion beta=-0.02635517745949269 se=0.057932882055032026 p_one_sided=0.6753326271053488 n=575
  - variante pct>50: n=251 E[R]=-0.0443 CI95=[-0.0973, 0.0171]
- MGC (exploratorio)/A: status=ABSTENCION n=0 E[budget-R]=+0.0000 E[stop-R]=+0.0000 CI95=ABSTENCION gaps=0 unres=0
  - stage1 event-study K=20: n=424 mean=-0.0307pts CI95=[-0.7779, 0.7083] gate=ABSTENCION-stage1(CI-no->0)
- MGC (exploratorio)/C: status=OK n=573 E[budget-R]=-0.1088 E[stop-R]=-0.5757 CI95=[-0.1605, -0.0541] gaps=0 unres=0
  - regresion beta=0.055493707046833034 se=0.09034690768284884 p_one_sided=0.2696542699759246 n=573
  - variante pct>50: n=282 E[R]=-0.1020 CI95=[-0.184, -0.012]

## Anomalias observadas

- MNQ: {"dias_media_sesion_excluidos": 461, "A_sweep_sin_rechazo": 29651, "A_sin_sweep": 1348, "C_vol_warmup_skip": 94, "A_senal": 2248, "C_senal": 1408, "C_sin_breakout": 296, "dias_roll_excluidos": 27}
- MES: {"dias_media_sesion_excluidos": 461, "A_sweep_sin_rechazo": 26927, "A_sin_sweep": 1257, "C_vol_warmup_skip": 94, "A_senal": 2339, "C_sin_breakout": 258, "C_senal": 1446, "dias_roll_excluidos": 27}
- MYM: {"dias_media_sesion_excluidos": 464, "A_sweep_sin_rechazo": 28843, "A_sin_sweep": 1338, "C_vol_warmup_skip": 94, "A_senal": 2252, "C_sin_breakout": 402, "C_senal": 1299, "dias_roll_excluidos": 27}
- MGC: {"dias_media_sesion_excluidos": 487, "A_senal": 2260, "C_vol_warmup_skip": 93, "A_sweep_sin_rechazo": 20731, "A_sin_sweep": 1278, "C_senal": 1348, "C_sin_breakout": 328, "dias_roll_excluidos": 27}

## Limitaciones declaradas

- W1: coste sensibilidad = 2,0 pts RT exactos (1,0 pt/side comision, slip 0).
- W2: lista de rolls unica para todos los mercados (proxy tercer viernes); roll real por contrato pendiente.
- W5: agrupacion por fecha ET en vez de sesion de exchange (limitacion declarada; parser rechaza timestamps duplicados).
- W6: SE de regresion OLS clasicos (sin HAC); la afirmacion confirmatoria usa CBB de la media, no el p-value de beta.

## Confianza en el calculo

- MNQ: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- MES: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- MYM: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- MGC: G1=PASS G2=PASS G3=PASS G4=PASS G5=PASS
- Conclusion cuantitativa: DIFERIDA a Hermes (multiplicidad >120 comparaciones; el preregistro no fija alfa).
- Gaps declarados: ancla del stop C; sensibilidad 2pt como canonico M9; roll-dates por CLI; MGC bajo regla 78 barras.
