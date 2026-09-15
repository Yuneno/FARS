# HERMES — Revisión del FIX-D (cierre del Bloque D)

**Revisor:** Hermes · **Ejecutor:** Gemini · **Fecha:** 2026-09-15
**Base:** `main` @ `eb911bf` · **Rama:** `bloque-d-motor-cuentas` @ `bd3e3a4` (sobre `114987f`)

**VEREDICTO: PASS. Los 3 hallazgos están cerrados y verificados de forma independiente. El Bloque D queda apto para decidir con sus números.**

---

## 1. Cierre de los 3 hallazgos (verificado por mí)

| Hallazgo | Estado | Mi verificación |
|---|---|---|
| **1 · Sizing con el stop final** | ✅ **CERRADO** | Código: `account_engine.py:201-212` usa `budgeted_risk_dollars / max(quantity, 1)`; **no queda ninguna ruta que dimensione con `stop_price`**; el fallback de 10 puntos eliminado. **Prueba independiente:** el P&L aplicado por trade pasó de **+0.384R** (bug) a **+0.0904R media / 1.145R σ**, frente a la distribución real de la estrategia (+0.0879R / 1.13R) → **el riesgo efectivo ya es el nominal** |
| **2 · Días en reloj comprimido** | ✅ **CERRADO** | Código: `real_days = res.trades_executed / trades_per_day` con `trades_per_day = len(trades)/span_days` declarado (1.8631). Mi MC independiente: **20.9 días** vs sus **20.8** ✅ |
| **3 · Cobertura MAE + declaración falsa** | ✅ **CERRADO** | `grep -c '"trade_id"'` = **2,842** (antes 639); **todas `m1_causal`**, 0 fallbacks; el manifiesto lleva `mae_coverage_summary` con los conteos reales. **Causa raíz documentada** (colisión de `trade_id`: `bt-1`, `bt-2`… repetidos en cada uno de los 8 folds → el diccionario conservaba solo 639) — buena cacería |

## 2. Reproducción independiente del resultado corregido (25K @ 0.4671 %)

Mi Monte Carlo, escrito desde cero, replicando las reglas declaradas:

| Métrica | Mi MC | Entregado |
|---|---:|---:|
| P(pase) intradía (MAE) | **25.65 %** | 25.60 % |
| Quema intradía | **6.15 %** | 6.60 % |
| P(pase) cerrado | **25.75 %** | 25.70 % |
| Quema cerrado | **3.15 %** | 3.60 % |
| Bloqueadas | **7.00 %** | 7.05 % |
| Días (reales) al pase | **20.9** | 20.8 |
| P&L aplicado | **+0.0904R** | (era +0.384R) |

Las diferencias residuales (≈0.45 pp en quema) provienen de detalles que no replico (orden exacto de comprobaciones y conteo de `trades_executed`). **Concordancia excelente en todo lo que decide.**

## 3. Lo que cambió en las conclusiones (y por qué importa)

| Métrica (25K @ 0.4671 %) | Antes del fix | **Después del fix** |
|---|---:|---:|
| P(pase) intradía | 73.05 % | **25.60 %** |
| Quema intradía | 11.65 % | **6.60 %** |
| Factor "quema oculta" (intradía vs cerrados) | 6.1× | **1.83×** |
| Días al pase | "0.69 d" | **20.8 d reales** |

- El **titular sigue en pie**: el modelo de trades cerrados **subestima la quema** (+14 % a +88 % según sizing, hasta +3 pp absolutos). Pero la magnitud honesta es **~1.8×, no 6.1×** — el 6.1× era un artefacto del sizing inflado.
- **Consecuencia operativa dura:** con esta estrategia en una 25K y horizonte de 30 días, el pase máximo medido es **~28.6 %** (a 0.5 % de riesgo) con ~6.75 % de quema y ~7.45 % de cuentas bloqueadas. En los sizings "conservadores" el pase cae a **1-1.4 %**. Es un dato incómodo pero real, y es exactamente lo que había que medir antes de comprar cuentas.
- Se reconfirma (con números corregidos) que **la 25K es la mejor cuenta**: en 150K el fallo catastrófico (quema + bloqueo) llega al **47 %** frente al **13.6 %** de la 25K.

## 4. Notas menores (no bloquean el cierre)

1. **Registros degenerados:** el código los salta con `continue` **sin contarlos ni declararlos** en el manifiesto. El encargo pedía declararlos (`skipped_degenerate_record`). Cambio de una línea: contador + clave en el manifiesto. Impacto real: probablemente 0 registros, pero la declaración es parte de la disciplina.
2. **Afirmación no medida en `d-eligibility.md`:** "en ventanas de 60-90 días la capitalización es altamente consistente". **No está medido** — y además el horizonte es justo la contradicción sin resolver del spec de Kai (30 vs 90). O se mide (el parámetro ya es configurable) o se marca como no medido.
3. `d-eligibility.md` conserva las conclusiones **relativas** (intradía vs cerrados; ranking de cuentas) que siguen siendo válidas, y ha sustituido correctamente las **absolutas**.

## 5. Determinismo y suite

- Re-corrida completa del protocolo por Hermes: ✅ **5 de 6 artefactos idénticos** (el `manifest.json` difiere solo porque hashea a los demás, que llevan timestamps). **Determinismo reconfirmado con el fix.**
- Suite completa propia: ✅ **`1,475 passed, 2 skipped`** (0 fallos; +1 test nuevo del FIX-D1).

*(Ambos se actualizan al cerrar la revisión.)*

## 6. Cierre del Bloque D

- **Bloque D cerrado** una vez confirmados determinismo y suite: `main` recibe C + D, y queda **solo pendiente la fase de zonas** (y la sesión MT5 cuando Ricardo avise).
- Lo que D deja para decidir **con números**: el plan de cuentas tiene una **probabilidad de pase del 25-28 % en 30 días** con ~7 % de quema y ~7 % de bloqueo en 25K. Eso es información de negocio — no una promesa.
