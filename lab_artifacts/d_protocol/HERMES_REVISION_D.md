# HERMES — Revisión del Bloque D (motor de cuentas de fondeo · perfil APEX)

**Revisor:** Hermes · **Ejecutor:** Gemini · **Fecha:** 2026-09-15
**Base:** `main` @ `eb911bf` · **Rama:** `bloque-d-motor-cuentas` @ `114987f`

**VEREDICTO: PASS de infraestructura e implementación, con 2 hallazgos de método.**
El motor está bien construido, es determinista y **el titular del bloque se confirma de forma independiente**.
**Pero los hallazgos cambian las conclusiones de *sizing* y de *días*: hay que corregirlos antes de usar la tabla para decidir dinero.** Ninguno invalida las conclusiones *relativas* (intradía vs cerrados; ranking de tamaños de cuenta).

---

## 1. Verificaciones de Hermes (todas independientes)

| # | Verificación | Método | Resultado |
|---|---|---|---|
| 1 | **Perfil APEX sin invención** | `docs/refactor/apex-profile.md` + `src/funded_profiles.py` vs `KAI_APEX_SPEC.md` | ✅ Cada constante citada (§1, §3.1); contradicciones registradas (DD Intraday vs Legacy, $1.00/$1.34/$1.42, 30d vs 90d); DESCONOCIDO declarados (consistencia, daily loss, días mínimos, noticias, payouts). **Nada inventado** |
| 2 | **Trailing NO duplicado** | Lectura de `src/account_engine.py` | ✅ El estado de cuenta se delega 100% en `FundedAccountStateV2.apply()` (`funded_rules_v2`). Una sola fuente de verdad |
| 3 | **MAE causal (función)** | Lectura de `src/backtest/mae.py` (145 líneas) | ✅ La función es correcta: solo M1 dentro de `[entry, exit]`; fallbacks conservadores declarados. **Pero ver Hallazgo 3: la COBERTURA real es del 22.5 %, no del 100 %** |
| 4 | **Test decisivo real** | Ejecutado por mí | ✅ `test_trade_winner_that_blows_on_intraday_mae_decisive` **PASA**: trade que cierra +$500/contrato y quema la cuenta por su MAE de 40 pts. No es un mock |
| 5 | **Preregistro anterior a la corrida** | timestamps | ✅ `preregistered_at_utc=2026-09-15T15:50:00Z` vs `started_at_utc=15:54:14Z` (4 min antes) |
| 6 | **Aviso de correlación** | `multicuenta_portfolio.json` + `d-eligibility.md §4` | ✅ `correlation_coefficient: 1.0` con el texto de advertencia explícito |
| 7 | **Determinismo** | Re-corrida completa del protocolo + comparación | ✅ (ver §4) |
| 8 | **Suite completa** | Corrida propia | ✅ `1,474 passed, 2 skipped` (1,458 + 16 nuevos) |
| 9 | **Titular del bloque (intradía vs cerrados)** | **MC propio**, replicando la lógica del motor desde cero | ✅ **Confirmado**: mi MC independiente da 79.0 % pase / 4.5 % quema (cerrado) y 73.5 % / 14.2 % (intradía) — frente a 78.8 %/1.9 % y 73.0 %/11.7 % entregados. **El sesgo existe y es del orden reportado** |

## 2. 🔴 Hallazgo 1 (material): el sizing usa el stop FINAL, no el riesgo inicial

**Evidencia (todo medido por mí, no del informe):**
- **34.2 % de los trades tienen `stop_price == entry_price`** — es el stop *al salir* (movido a break-even), no el riesgo con el que se entró.
- El motor dimensiona con `stop_points = |entry − stop_price|` y, cuando es 0, cae a un **fallback inventado de 10 puntos** (`account_engine.py:200-205`).
- Consecuencia medida: el P&L que se aplica a la cuenta por trade es **`media +$44.88 / σ $241`**, es decir **+0.384R de media y 2.065R de σ** en unidades del riesgo nominal ($116.77) — cuando la distribución REAL de la estrategia es **+0.0879R de media y 1.13R de σ**.
- **El riesgo efectivo por operación es ≈ 4.4× el `risk_pct` declarado.** Lo que la tabla llama `0.4671 %` es en realidad ≈2 % por trade en la 25K; el "recomendado" `0.1946 %` es ≈0.85 %.

**Impacto:** las conclusiones **relativas** sobreviven (intradía vs cerrados; 25K mejor que 100K/150K), pero **las recomendaciones absolutas de sizing de `d-eligibility.md §6` no son utilizables**: prometen una quema (<1.6 %) que corresponde a un riesgo mucho mayor del etiquetado.

**Cambio mínimo propuesto (FIX-D1):** dimensionar con el **riesgo inicial** del trade, que ya está en el registro: `unit_risk_usd = trade.budgeted_risk_dollars / max(trade.quantity, 1)`. Sin fallback de 10 puntos, sin `stop_price`. Test: para un trade con stop inicial de 40 pts, `qty` debe ser `round(nominal/80)`, no `round(nominal/20)`.

## 3. 🟠 Hallazgo 2: los "días" se miden en un reloj sintético comprimido

`days_to_outcome = (last_ts − first_ts)/86400` sobre los timestamps **sintéticos** del Monte Carlo, que avanzan **15 min + duración por trade** (`account_engine.py:455-466, 365-368`).

- Mediana del avance sintético: **20 min/trade** (medido por mí).
- La frecuencia real de la estrategia es **1.95 trades/día** → ~12.3 h por trade.
- Por tanto **"0.69 días" ≈ 50 trades ≈ 25 días reales de operación.** El reloj está comprimido ~15-20×.

**Impacto:** la narrativa de "pasa rápido" es un artefacto. El "13 % en 30 días" de los sizings conservadores **sí es correcto** (lo que manda es el presupuesto de 58 trades ≈ 30 días al ritmo real) — pero los "días medianos a pasar" no.

**Cambio mínimo propuesto (FIX-D2):** reportar `trades_executed / trades_per_day` (o timestamps con el hueco real entre trades). Documentarlo en `d-eligibility.md`.

## 4. 🔴 Hallazgo 3 (material): cobertura MAE del 22.5 % declarada como 100 %

**Evidencia medida por mí:**
- `lab_artifacts/d_protocol/smc_fvg_risk_10_maes.json` contiene **639 entradas** (`grep -c '"trade_id"'`), **no 2.842**.
- Su metadata declara `"total_trades": 2842` y **`"m1_causal_coverage_pct": 100.0`** → **declaración falsa**.
- El log de la corrida lo dice sin ambigüedad: `Computed causal MAE for all 639 trades`.
- Las 2.203 operaciones sin MAE precomputada caen al **fallback #3** (`stop_bound_conservative`): MAE = distancia al stop **final** — que en el 34.2 % de los trades es **0 puntos** (stop movido a BE) → **esas operaciones no aplican ninguna excursión adversa**.

**Impacto (en la dirección buena, pero hay que decirlo):** el brazo "intradía" es **solo parcialmente intradía**, así que la quema de 11.65 % y el factor 6.1× son **cotas inferiores**. El titular del bloque se sostiene y probablemente es **más grande** de lo reportado. Pero un artefacto que declara 100 % de cobertura y entrega 22.5 % **no es presentable como evidencia** — es el mismo tipo de defecto que cazamos en A.3 (declaración falsa de fin de dataset).

**Cambio mínimo propuesto (FIX-D3):** calcular el MAE para los 2.842 trades (o explicar y declarar la cobertura real, trade a trade, con el motivo), y sustituir la declaración por el conteo verdadero. Si el fallback sigue haciendo falta, que sea visible por trade (`resolution_mode`) y agregado en el manifiesto — nunca un 100 % que no existe.

## 5. Determinismo y suite

- Re-corrida completa del protocolo por Hermes: ✅ **5 de 6 artefactos idénticos** ignorando `*_at_utc`/`wall_seconds`/`generated_at`. El `manifest.json` difiere solo porque hashea a los demás (que llevan timestamps) — esperado. **Determinismo confirmado.**
- Suite propia: **`1,474 passed, 2 skipped`** ✅ (0 fallos).

## 5. Lo que el ejecutor hizo bien y quiero que conste

- Declaró los **DESCONOCIDO** en vez de rellenarlos (consistencia, daily loss, días mínimos, payouts).
- **Umbral de quema**: `breach_boundary="<="` (tocar exactamente el floor quema) — procedencia citada.
- Modeló la **cuenta bloqueada** (`risk-blocked:dd-buffer`) como estado propio, separado de "quemada" y "pasada".
- El aviso de correlación unitaria está en el código **y** en el doc, no escondido.
- La tabla de contradicciones del spec de Kai (dos modelos de DD, tres comisiones, 30 vs 90 días) está documentada con la resolución elegida y su porqué.

## 6. Para cerrar D

1. **FIX-D1** (sizing por riesgo inicial) y **re-correr la rejilla completa** → nueva tabla de sizing.
2. **FIX-D2** (días reales) → corregir la tabla y la narrativa.
3. **FIX-D3** (cobertura MAE real + declaración honesta) → recalcular el comparativo intradía vs cerrados con cobertura completa (la quema subirá).
4. Re-emitir `d-eligibility.md` con las recomendaciones absolutas corregidas (y mantener las relativas, que se sostienen).
5. Confirmar determinismo + suite en el commit del fix.
