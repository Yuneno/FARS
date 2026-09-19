# HERMES_REVISION_FILLBAR — Verificación del bloque de medición fill-bar

**Fecha:** 2026-09-19 · **Revisor:** Hermes · **Entrega:** Gemini (Antigravity)
**Rama:** `bloque-fillbar-medicion` @ `47c0398` (base `main` `f7add46`)

## Veredicto: PASA con 2 refinamientos registrados

## Qué verifiqué (con evidencia)

1. **Puerta de control (la clave del bloque):** el brazo `control` reproduce los artefactos publicados.
   - SMC-FVG vs `c2_protocol/smc_fvg_baseline_fold_metrics.json`: n=3,624 en los 3 escenarios;
     E[R] y netR idénticos (p. ej. por_tramo: +0.097750 / +354.24 en ambos); WR idéntico
     (0.579470 vs 0.5795 del artefacto viejo, que guarda 4 decimales redondeados — benigno).
   - SMC-OB vs `smcob_protocol/resultados.json`: MNQ/MYM/MGC idénticos a 6 decimales en WR y E[R].
2. **Consistencia interna:** sumas fold→agregado correctas en las ~30 combinaciones
   (FVG: 3 configs × 3 escenarios × 3 modos; OB: 3 mercados × 3 modos), incluidos los conteos fill_bar.
3. **Aislamiento de código:** `src/`, `tests/`, `config.yaml` idénticos a `main` `f7add46` (git diff vacío).
   El fork difiere en 28+/1− líneas y SOLO contiene: campo `fillbar_mode` + validación, el gate
   `position["limit_entry"] and i == position["entry_index"]` (solo fills de límite) y el plumbing de
   `run_backtest`. La clave `limit_entry` existe de verdad (`src/backtest/executor.py:734`).
4. **Cifras del INFORME vs JSONs:** coinciden en todo lo muestreado (p. ej. A1 MNQ ΔWR −16.11pp,
   A2 155 fb-wins FVG, netR A1 canonico −813.51). Los veredictos siguen la regla ex-ante sin "casi".

## Refinamientos registrados (no bloquean; viajan con la próxima tarea)

1. **El hash de `src/backtest/executor.py` publicado en INFORME §7.2 no resuelve.**
   Publicado: `68a2bf16…`; real (bytes tal cual): `80dea045cc91a055cc68f12efa0dd607be401495433e18502efbbe8c36bba70d`
   (las normalizaciones LF/CRLF tampoco coinciden: 05d46e28… / 7df3bf3d…). El hash del fork y del diff
   SÍ resuelven. La integridad está garantizada por git; corregir o eliminar esa línea.
2. **La columna "Fill-Bar Wins (n)" subcuenta el efecto en SMC-OB.** Cuenta solo TPs *enteros*
   resueltos en la vela del fill (`entry_time == exit_time` y `exit_reason == take_profit`). El delta
   A1 incluye además flips por **tp1 (parcial) tocado en la vela del fill → BE posterior** (SMC-OB usa
   `partial_take_profit_fraction` + `move_stop_to_break_even`, `src/backtest/smc_ob.py:361-362`):
   MNQ flips 288 vs 88 de la columna (exceso +200), MYM +149, MGC +195. Renombrar la columna o añadir
   "flips netos". El veredicto no cambia: el artefacto es mayor de lo que la tabla sugiere, no menor.

## Resultado (números que quedan firmes, regla A1, escenario por_tramo)

| Libro | WR base → limpio | E[R] base → limpio | Folds+ |
|---|---|---|---|
| SMC-FVG baseline (risk 8) | 58.55% → 45.19% | +0.0977 → −0.1301 | 8/8 → 0/8 |
| SMC-FVG risk 5 / risk 10 | 57.91→42.58 / 58.74→45.97 | +0.0756→−0.2119 / +0.1031→−0.1051 | → 0/8 |
| SMC-OB MNQ / MYM / MGC | 60.23→44.12 / 58.03→45.27 / 57.87→44.47 | +0.0724→−0.1512 / −0.0300→−0.2090 / −0.0880→−0.2838 | 6/8→0/8 (y 4/8, 2/8 → 0/8) |

A2 (cierre confirmado) apenas recupera +0.5–0.7 pp de WR: no rescata ninguna configuración.
Baselines marcados **INFLADOS**; suspensión de promoción y revocación del sim de cuenta E1
(`smcob_protocol`) se endosan.

## Siguiente paso propuesto (espera decisión del dueño)

Bloque de CORRECCIÓN: adoptar la regla A1 para fills de límite en el ejecutor enhanced con tests de
regresión, re-congelar los baselines afectados con los números limpios y actualizar el ledger único.
Los 2 refinamientos de arriba se aplican en ese mismo pase. El fix NO se hace en este bloque
(aislamiento respetado).

*Autoría: código y artefactos por Gemini; verificación y cierre por Hermes. Los números de producción
(`src/`) no cambiaron — el fix es un bloque aparte.*
