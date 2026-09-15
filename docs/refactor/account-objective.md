# Criterio de cuenta (E1) — el yardstick de promoción

**Fecha:** 2026-09-15 · **Autor:** Hermes · **Estado:** en vigor desde el cierre de E5/E6.

## El criterio

A partir de ahora, **toda estrategia o config candidata se puntúa con el motor de
cuenta Apex 25K**: `P(pase)`, `quema` y `bloqueadas` en 30 días de calendario con
trailing intradía — **no** solo con E[R]/PF/DD del backtest.

Runner: `python lab_artifacts/run_account_score.py` (modelo rápido de trades
cerrados para rankear) y, para la campeona, verificación con el motor FULL
(`lab_artifacts/improvement_math/06_verificacion_5p0_full.py`, MAE M1 causal).

## Por qué

C3 eligió la config de mejor E[R]/PF/DD; el motor de cuenta demostró que el éxito
real es **pasar en 30 días con floor trailing** — y que ese juego **premia la
frecuencia sobre el edge** (min_risk=5.0, el peor E[R] de la escalera, es la
campeona de cuenta: 47.3% de pase vs 29.8% de la 10.0). Puntuar con el criterio
equivocado produce selecciones equivocadas.

## Qué NO cambia

- **Gates estadísticos** (IC excluye cero, PBO/CPCV, régimen, folds positivos):
  siguen siendo la higiene anti-sobreajuste. Una config que no pase la higiene
  **no se puntúa en cuenta**.
- **Gate 5 (DD < 12R)**: sigue como higiene (atrapa spikes de sobreajuste).
  **Decisión pendiente (E3):** para el plan de cuentas, el riesgo real es
  `P(quema)` del motor; se propone que Gate 5 deje de ser bloqueador de
  promoción y el motor sea el juez de riesgo. No se aplica hasta que
  Ricardo/Juanca decidan.
- **Preregistro**: toda corrida de cuenta para promoción se preregistra (config,
  fecha, seeds) como en C2/C3.

## Flujo de promoción (desde ahora)

```
candidata  ->  higiene estadistica (gates C1/C3)  ->  PUNTAJE DE CUENTA (E1)
                                                       ranking por P(pase)
                                                       + quema/bloqueadas
            ->  campeona: motor FULL (MAE intrabar)  ->  revision  ->  promocion
```

## Estado de decisiones

| Decisión | Dueño | Estado |
|---|---|---|
| E2: promover min_risk=5.0 | Ricardo/Juanca | pendiente ("un poco después") |
| E3: Gate 5 → P(quema) | Ricardo/Juanca | pendiente |
| E7: datos MYM/MGC | Ricardo | pendiente |
