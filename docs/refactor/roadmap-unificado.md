# Roadmap unificado FARS (post-D)

**Fecha:** 2026-09-15 · **Autor:** Hermes · **Estado:** plan (nada implementado de este doc todavía).
**Propósito:** responder 1) cuánto falta, 2) si las mejoras de cuenta se integran al código, 3) cómo cambia la forma de avanzar — con **cero procesos abiertos**: cada ítem tiene dueño, criterio de cierre y bloqueo explícito.

---

## 1. Estado global (cuánto falta)

| Pista | Estado | Falta |
|---|---|---|
| **Refactor A–D** (calidad, C1-C3, motor de cuentas) | ✅ **CERRADA** (main `ee1daed`) | Solo 3 micro-hilos de D (sección 3) |
| **FARS 1.2 Core** (fases 10B–14) | 10B ✅ 11A ✅ 11B ✅ 11C ✅ | **11D** (bloqueada por el export redactado de Tradovate que debe dar Ricardo), **12** Temporal/OOS, **13** Stress, **14** Reporting read-only |
| **Zonas/reversiones** | Auditada (6 pasos definidos), 0% implementada | **6 pasos** (sección 4): pivotes canónicos → BOS/CHoCH → DiagnosticEvent+lifecycle → pools+EQH/EQL → reversión v1 → OTE/fib/S-R |
| **Mejoras de cuenta** (nuevo, este análisis) | Análisis hecho, nada integrado | **7 ítems** (sección 2), 3 bloqueados por decisión |
| **Posteriores** | Sin empezar | Sesión MT5 (Ricardo avisa), módulo largo post-D (Ricardo avisa alcance), Juanca strategy 2 (REGISTERED en TASKS.json) |

**En números:** el refactor quedó al 100%. Del planing original quedan **4 fases Core (una bloqueada por un archivo tuyo), 6 pasos de zonas, y los módulos posteriores que tú anunciarás.** Las mejoras de cuenta son 7 ítems, la mayoría pequeños.

---

## 2. Integración de las mejoras de cuenta al código (Bloque E propuesto)

| # | Ítem | Dónde cae en el código | Tamaño | Bloqueo |
|---|---|---|---|---|
| E1 | **Criterio: puntuar por objetivo de cuenta** (P(pase) del motor, no E[R]/PF/DD) | Runner `lab_artifacts/improvement_math/05/06` → promover a `run_account_score.py` + sección en el protocolo de promoción (docs/refactor) | Pequeño | — (ya hecho el análisis) |
| E2 | **Promoción de `min_risk=5.0`** (46.7% vs 25.6% de pase) | Cambio de config (un valor) + entrada en el registry + corrida formal de gates con preregistro | Mínimo | **DECISIÓN Ricardo/Juanca** (pendiente) |
| E3 | **Enmienda Gate 5**: DD bruto deja de ser bloqueador de promoción; pasa a higiene anti-sobreajuste. El riesgo de cuenta se mide con **P(quema) del motor** | Módulo de gates + docs | Pequeño | **DECISIÓN Ricardo/Juanca** (pendiente) |
| E4 | **Capa de políticas de apuesta** (colchón k, throttle por racha, tope diario) | Módulo nuevo `src/account_policy.py` (sizing dinámico) + opción en `AccountEngineConfig` + tests + preregistro. **No toca la señal** | Mediano | — |
| E5 | **Fix de la cola de ejecución** (7 trades con salida fuera del stop; la quema baja ~66%) | Causa raíz en executor/fill (por verificar contra barras OHLC) → fix + test de regresión + re-corrida de los números de quema de D | Pequeño-mediano | Verificación pendiente (1 comando aprobado) |
| E6 | **Fix D-5** (métrico `max_drawdown_pct` mide excursiones post-muerte) | `src/account_engine.py` (computar el DD sobre la trayectoria viva) + test | Minúsculo | — |
| E7 | **Validación multi-mercado** (MYM/MGC con protocolo C2) | Loader de datos + corridas del protocolo por mercado | Mediano (análisis) | **DECISIÓN Ricardo** (OK a los datos de `D:\fars move`) |

**Viabilidad: alta.** Todo cae en módulos que ya existen (`account_engine`, executor, protocolo C1/C2). No hay frameworks nuevos, no hay terceras implementaciones de nada. **El único cambio de forma es conceptual:** el *yardstick* de promoción pasa a ser el objetivo de cuenta (E1), y eso lo aplica automáticamente a todo lo que venga después (zonas, estrategias nuevas).

---

## 3. Cierre de hilos abiertos (para no dejar nada abierto)

| Hilo | Cierre propuesto |
|---|---|
| D-5 (métrico DD) | E6 — fix + test, en el mismo bloque |
| Cola de ejecución (7 trades) | E5 — verificar → fix → re-correr quema de D |
| Registros degenerados no declarados (nota FIX-D) | Se declara el conteo en el manifiesto (1 línea) — se incluye en E5 |
| Frase no medida de `d-eligibility.md` (ventanas 60-90 días) | Se marca como "no medido" o se mide con el parámetro de horizonte del motor — decisión tuya, incluida en el cierre de E |
| `KAI_APEX_SPEC.md` (dentro o fuera del repo) | Se queda fuera del repo (E:\FARS-LAB). Cerrado: no viaja en push |
| Juanca strategy 2 (EMAS/CRT-TBS) | REGISTERED en TASKS.json; se retoma cuando tú lo pidas |

---

## 4. Lo que falta del planing original (zonas, 6 pasos ya auditados)

1. Pivotes canónicos (unificar los 2 de FARS en 1, semántica declarada) + test de equivalencia.
2. BOS/CHoCH extraído de `smc_fvg` con test bit a bit.
3. `DiagnosticEvent` extendido (tipos de nivel de Kai) + lifecycle de zona (caducidad causal por barras).
4. Proveedor de pools tipados (día previo / 20 días / overnight) + **EQH/EQL** (la única pieza realmente nueva).
5. **Reversión v1** = sweep + CHoCH + FVG/zona ± CISD, con paridad replay↔backtest, **al protocolo desde el día uno** — y ahora puntuada con el **objetivo de cuenta (E1)**, no solo E[R].
6. *(Opcional después)* OTE/fibonacci (0.705/0.62 de Kai, solo como cita), premium/discount, S/R con ancho. Volume/voids: **no reabrir** (Kai ya lo probó y descartó).

**Nota de coherencia:** zonas no es un desvío del criterio nuevo — al contrario: añade setups/día (frecuencia), que es exactamente lo que el objetivo de cuenta premia. Se puntuará con el mismo yardstick (E1).

---

## 5. Orden propuesto (cerrado, sin hilos abiertos)

```
Bloque E (mejoras de cuenta)          <- decisiones E2/E3/E7 en tu tejado
  E6 (D-5) + E5 (cola) primero (destapan el resto: quema corregida)
  E1 + E4 (criterio + políticas) con preregistro
  E2 + E3 (5.0 y Gate 5) cuando decidas
  E7 (MYM/MGC) cuando des el OK a los datos
Bloque F (zonas, 6 pasos)             <- empieza cuando E cierre (tú dijiste "todavía no")
FARS 1.2: 11D (cuando pases el export) → 12 → 13 → 14
MT5 / módulo largo                    <- cuando tú avises
```

**Reglas que no cambian:** cada bloque cierra con revisión (como A–D), commits locales, sin push hasta que tú lo digas, y cada mejora entra por el protocolo (preregistro + gates), no por la puerta de atrás.

**Decisiones pendientes tuyas/Juanca (3):** ~~① promover `min_risk=5.0` (E2) · ② enmendar Gate 5 → P(quema) (E3) · ③ OK a los datos MYM/MGC (E7)~~ — **RESUELTAS 2026-09-15:** ① objetivo = menor fracaso con mayor pase (frontera Pareto, E2 en curso) · ② Gate 5 → higiene, aprobado e implementado · ③ MYM/MGC autorizados (E7 en curso).
