# PLAN — validación del bot ORB (señales / riesgo / incertidumbre / costes)

**Estado:** PROPUESTA → a crítica de Muse antes de implementar.
**Alcance:** investigación histórica bajo `lab_artifacts/orb_protocol/`. Sin datos en vivo, sin broker, sin credenciales, sin órdenes reales, sin tocar FARS Core ni especificaciones, sin commit/merge/push.

---

## 0. Hechos verificados hoy (base del plan)

- Ledger decisorio `ledger_orb_fars_stop.csv`: 2.444 trades, MNQ, costes realistas (0,62 USD/side + 1,5 ticks/side). `fars audit` 2.444/2.444, 0 errores, 3 capacidades.
- FARS Core **sí** tiene bootstrap utilizable: `src/bootstrap.py` trae **IID Bootstrap y Circular Block Bootstrap (CBB)**, diagnósticos de dependencia (Ljung-Box por rangos, runs test), longitud óptima de bloque (`arch.bootstrap.optimal_block_length`), intervalos BCa y studentizados, y `fars bootstrap` en el CLI. Requiere `B >= 2000` y `master_seed` entero.
- El replay de 3 meses del paper bot aceptó **42 órdenes y rechazó el resto por `MAX_TRADES_REACHED`**.
- `AccountAwareRiskEngine` deniega con `UNKNOWN_CRITICAL_STATE` si falta `peak_equity` (fail-closed).

## 1. Pregunta A — semántica de `MAX_TRADES_REACHED` (lo que hay que auditar)

**Hipótesis de trabajo (a confirmar/refutar):** el límite NO es mensual. El engine compara
`snap.trades_applied >= rules.max_trades` y `trades_applied` es un contador que **el caller** alimenta. Nuestro bot lo pasa como acumulado de toda la corrida, **sin reinicio mensual**. Por eso `max_trades=42` (pensado como ~2/día × 21 días) actuó como **techo global del replay de 3 meses**, no como cupo mensual.

Preguntas que el trabajo debe cerrar:
1. ¿El reinicio de períodos es responsabilidad del caller? ¿Dónde debe vivir (estrategia, snapshot o `FundedAccountRules`)?
2. ¿Cómo se calculan los límites diarios? (`_daily_loss_violated` mide contra `_start_of_day_equity`, que se fija con el **primer snapshot del día**; `_utc_day` usa UTC, no la sesión del mercado → riesgo de desfase de sesión.)
3. ¿El drawdown `trailing` usa `peak_equity` externo? ¿Quién lo mantiene y cuándo se actualiza?
4. **Tests que crucen cierres mensuales** (≥3 meses) y cierres de día: reinicio correcto, no-reinicio indebido, y consistencia entre `trades_applied` y los fills.

Riesgo declarado: si el límite global cortó señales rentables, la secuencia aceptada difiere materialmente de la del backtest y **no se debe pasar a datos en vivo**.

## 2. Pregunta B — separar señales / aceptadas / rechazadas y medir ambas secuencias

Entregables:
- **S** señales generadas por la estrategia (bruto).
- **A** órdenes aceptadas por el risk engine (subsecuencia de S).
- **R** rechazadas, con **motivo** (MAX_TRADES, DAILY_LOSS, DRAWDOWN, UNKNOWN, …) y momento.
- Métricas FARS (E[R], WR, DD, rachas, t) sobre:
  - **ledger bruto** (las 2.444 del backtest);
  - **ledger riesgo-permitido** = la subsecuencia que el `AccountAwareRiskEngine` habría autorizado, reconstruyendo el camino de equity con las reglas (riesgo 1%, límite diario, drawdown trailing, máx ops) y los mismos fills del backtest.
- Declarar cuánto cambia el resultado entre ambos. Si la diferencia es material → no recomendar datos en vivo.

## 3. Pregunta C — incertidumbre (bootstrap IID y por bloques)

- Estimando principal: **E[R]**. Secundarios: win rate, DD máximo, racha perdedora máxima.
- **Diagnóstico primero**: los tests de dependencia de FARS (Ljung-Box por rangos, runs test) sobre la serie de `r_result`. El método principal se elige por ese diagnóstico, **no por el resultado**:
  - si la dependencia no se rechaza → **IID como principal**, CBB como robustez;
  - si se rechaza → **CBB como principal**, IID solo como referencia (y marcado como sesgado).
- **Advertencia metodológica que el plan asume como central:** el DD máximo y las rachas son **estadísticos de camino**. Un bootstrap IID rompe el orden temporal y **subestima** DD y rachas; por eso esas métricas se intervalan **solo con CBB** (preserva orden local) o se reportan del camino observado. El bootstrap IID de DD se reporta únicamente como comparación ilustrativa del sesgo.
- **Probabilidad de activar límites**: por cada réplica CBB, simular el camino de cuenta con las reglas de riesgo y registrar si se dispara límite diario / drawdown / máx ops. Semilla fija.
- Semillas reproducibles: `master_seed` documentado (propuesta: **20260928**), `B = 2000` (mínimo de FARS) y registro del RNG tree (FARS ya lo expone).
- **Prohibido** elegir longitud de bloque, B, ventana o métrica para mejorar el resultado. La longitud de bloque sale de `optimal_block_length`, no de ajuste.

## 4. Pregunta D — sensibilidad de costes

Tres escenarios × desglose (comisión vs slippage):
- base: 0,62 USD/side + 1,5 ticks/side (escenario decisorio actual)
- 1,5×: comisión y slippage multiplicados por 1,5
- 2,0×: idem ×2
y un cuarto que **separa** el efecto: solo comisión ×2 vs solo slippage ×2.
Se re-corre el motor del autor con `Config` modificada (los costes entran en `round_turn_cost`). Reportar E[R] y DD en cada caso. Si E[R] cambia de signo en 1,5×, eso es un resultado y se reporta tal cual.

## 5. Declaración obligatoria (no es un análisis, es un hecho)

**Elegimos ORB después de ver los resultados publicados del autor (2015–2025, Sharpe ~1,0).** El bootstrap cuantifica incertidumbre *bajo sus supuestos* (estacionariedad/de la muestra), pero **no elimina el sesgo de selección**. 2010–2026 tampoco es una muestra independiente: la estrategia se eligió viendo su desempeño publicado. Ningún intervalo de confianza corrige eso.

## 6. Roles (según lo pedido por Ricardo)

| Quién | Qué |
|---|---|
| Muse | auditoría crítica del plan y de sus supuestos estadísticos y de riesgo **antes** de implementar: sesgo de selección, dependencia temporal, errores en DD/rachas, fallos en límites |
| Opus 4.6 (Antigravity) | **solo** ambigüedades lógicas/metodológicas que queden sin resolver tras Muse: interpretaciones alternativas y cuál está mejor sustentada |
| Gemini | implementa **solo** los cambios aprobados, editando archivos, sin shell |
| Hermes | orquesta, inspecciona, define el plan, ejecuta comandos/tests, consolida el informe |
| Codex | revisión independiente del diff, sin implementar |

## 7. Orden de trabajo propuesto

1. Muse: auditoría crítica de este plan (read-only).
2. Hermes: consolida, resuelve lo no ambiguo, marca ambigüedades → Opus si hace falta.
3. Gemini: implementa (script de investigación + tests para Pregunta A/B/C/D dentro de `orb_protocol/`; **no** modifica FARS Core).
4. Hermes: corre tests, recopila evidencia, valida contra FARS (`fars metrics`, `fars bootstrap`).
5. Codex: revisa el diff → si FAIL, hallazgos a Gemini → re-tests → re-revisión hasta cerrar.
6. Informe actualizado: metodología, decisiones, resultados, comandos, pruebas, y veredicto sobre si los límites alteran materialmente el resultado.
