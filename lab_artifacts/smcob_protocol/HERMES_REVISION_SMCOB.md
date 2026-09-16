# HERMES — Revisión del port SMC-OB (Codex, gpt-6-astra medium)

**Fecha:** 2026-09-16 · **Encargo:** `E:\FARS-LAB\FARS_SMCOB_ENCARGO.md` · **Ejecutor:** Codex · **Revisor:** Hermes

## Verificación independiente (no confié en el self-report)

| Chequeo | Resultado |
|---|---|
| Tests del port en MI corrida | ✅ **14/14 passed** (`tests/test_backtest_smc_ob.py`) |
| Re-corrida del runner multi-mercado | ✅ **Idéntico a Codex**: MNQ n=1.755, E[R] +0.0724, CI [0.0269, 0.1187], folds 6/8 — coincidencia a la 4ª decimal |
| Disciplina de paridad | ✅ `_atr` copia EXACTA; `_SmcObSignal` interfaz idéntica; test de paridad AST contra el source de Kai incluido |
| Prohibiciones | ✅ Sin commits, sin push, sin pip (el incidente del test de empaquetado quedó documentado y excluido — correcto) |
| Honestidad de artefactos | ✅ BLOCKERS.md con 5 preguntas exactas + incidentes; preregistro congelado con hashes |

## Resultado (defaults de Kai: swing=10, RR=3, CHoCH=True, lookback=60)

| Mercado | n | WR | E[R] | PF | CI 95% | Folds+ | Veredicto |
|---|---:|---:|---:|---:|---:|---:|---|
| MNQ | 1.755 | 60.2% | **+0.0724** | 1.172 | [+0.027, +0.119] | 6/8 | **PASS** |
| MYM | 1.794 | 58.0% | −0.0300 | 0.938 | [−0.093, +0.027] | 4/8 | FAIL |
| MGC | 1.773 | 57.9% | −0.0880 | 0.824 | [−0.140, −0.038] | 2/8 | FAIL |

## Veredicto

1. **El port es correcto y determinista** — aceptado y commiteado.
2. **SMC-OB NO revive MYM** (E[R] −0.030, CI cruza cero). La pareja "prime" de Kai no sobrevive a nuestro canon con costes reales. Con esto, el Dow queda descartado para AMBAS estrategias (SMC-FVG: −0.07..−0.20; SMC-OB: −0.03). **La explicación "MYM era prime" queda archivada: era el setup live de Kai, no un edge validado en nuestro canon.**
3. **SMC-OB en MNQ pasa gates con edge moderado (+0.072)** — inferior al pilar SMC-FVG (+0.104). Su rol posible: **diversificador de portafolio** (correlación distinta, RR=3 asimétrico), no campeona. Su cuenta (8.7% fixed / 13.1% buffer) es débil vs el pilar (41.6%).
4. **MGC con SMC-OB falla** (E[R] −0.088) — el oro es de SMC-FVG (recalibrado 2.0-3.0, ver carril 2).

## Respuestas a las 5 preguntas de Codex (para Ricardo)

1. **RR=3 vs RR=2**: correr también el override live (RR=2) como variante preregistrada en el próximo encargo de portafolio, si Ricardo lo quiere.
2. **Procedencia MYM/MGC**: pendiente formalizar (mismo proveedor, corte 2019-05-06 aplicado; no promovidos como canónicos nuevos).
3. **Pool MNQ+aprobado**: respuesta correcta de Codex — MNQ SMC-OB, no mezclar con la FVG.
4. **Simulación por bloques/días conjuntos (no-IID)**: pregunta legítima — el motor actual remuestrea IID; una simulación con dependencia temporal es trabajo futuro.
5. **Cobertura overnight del helper M5**: limitación conocida; no afecta esta corrida (solo MNQ pasó y usa M1 causal).

## Estado del árbol

`src/backtest/smc_ob.py` + `tests/test_backtest_smc_ob.py` + `lab_artifacts/smcob_protocol/` — untracked de Codex, **commiteados por Hermes tras esta revisión**.
