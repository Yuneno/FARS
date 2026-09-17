# HERMES REVISION — Bloque S1 (sesión demo sombra en vivo · TopstepX/ProjectX)

**Fecha:** 2026-09-17 · **Revisor:** Hermes · **Implementador:** Gemini (Antigravity) · **Rama:**
`bloque-s1-sesion-demo` (`2884a08`, base `main` = `f1bb7d4`) · **Artefactos:** `lab_artifacts/s1_protocol/`

## Veredicto

**PASS — sesión válida y además verificada de forma independiente.** El pipeline completo funcionó contra
datos en vivo: feed sin huecos, decisiones deterministas, **veto fail-closed del motor de riesgo en las 2
señales** y **cero órdenes**. Dos avisos cosméticos (definición de la métrica de "latencia" y una aritmética
de cobertura) y una brecha sustantiva: el tramo de **ejecución no se ejercitó** (0 fills, por cuenta sin
colchón de drawdown).

## 1. Hechos de la sesión (12:17 → 16:00 ET)

| Métrica | Valor |
|---|---|
| Uptime | 222,9 min (13.375 s), hasta el cierre |
| Barras M5 recibidas | **68** (14:15 → 19:50 UTC, 0 huecos) |
| Señales de la estrategia | **2** (LONG 19:05Z y LONG 19:35Z, `smc_fvg_atr_k050`) |
| Decisiones del motor | 2 — **0 aprobadas, 2 vetadas**, motivo `DRAWDOWN_BUFFER_TOO_LOW` |
| Órdenes enviadas | **0** (`LIVE_EXECUTION_ENABLED = False`) |
| Fills | 0 (los vetos cortaron antes de la ejecución) |
| Reconstrucción del log | 73/73 eventos ✓ |

## 2. Verificado por el revisor (independiente)

| # | Qué | Resultado |
|---|---|---|
| 1 | **Fidelidad del camino live**: alimenté el log completo (68 barras) por la misma estrategia | ✅ reproduce **exactamente** las 2 señales registradas (19:05 y 19:35, ambas LONG) — determinista |
| 2 | **Sensibilidad al warmup** (mi sospecha previa, medida) | ✅ **refutada**: con warmups de 50/100/250/500/900 barras el conjunto de señales es **idéntico** (0 de diferencia). Las 43-68 barras de la sesión eran suficientes |
| 3 | Cadencia real de decisión | ✅ el runner sondea cada **15 s**; la señal de la barra 19:05 (cierra 19:10) se decidió a las **19:10:07** → **7 s después del cierre**, fiel al diseño |
| 4 | Integridad | ✅ manifest con sha256 de los 8 artefactos; commit `2884a08`; árbol limpio |
| 5 | Seguridad | ✅ 0 órdenes; el conector sigue read-only; cuenta simulada (`simulated: true`) |

## 3. Avisos (no bloquean)

1. **La métrica "latencia mediana" (311.586 ms) está mal nombrada, no mal medida:** mide la **edad de la
   barra al decidir** (≈5 min, porque una vela M5 solo puede decidirse al cerrar), no latencia de
   procesamiento — que es de ~7 s. Renombrar a `bar_age_at_decision_ms` o añadir la latencia real.
2. **Aritmética de cobertura inconsistente** en el informe: "68 recibidas / ~45 esperadas (100 %)" — son
   magnitudes distintas (la primera incluye 2 h de backfill del primer poll). Cosmético; la cobertura real de
   la ventana es 100 % con 0 huecos.
3. **Veto esperado y correcto:** la cuenta demo está quemada (balance −501,30 con peak 1.500) → no tiene
   colchón de drawdown, así que el motor veta **todas** las intenciones. Es el comportamiento fail-closed
   funcionando; pero implica que **el tramo de ejecución (riesgo → paper fill) no se ejercitó**.

## 4. Qué sigue (para que el siguiente intento sí pruebe todo)

1. **Cuenta Practice limpia o reiniciada** para la próxima sesión: con colchón de drawdown el motor aprobará y
   se verá el camino completo hasta el fill simulado.
2. **RT-9 (órdenes en Practice)** — ya aprobado y con la vía confirmada por documentación oficial
   (`/api/Order/place`, `cancel`, `search`, brackets; Topstep recomienda Practice al no existir sandbox).
   Reglas: no reenviar a ciegas `OrderPending`, `activeContract` por sesión (roll), cierre 3:10 PM CT,
   y verificar la prohibición de automatización en Live Funded antes de cualquier paso a real.
3. Mantener la sombra como red de seguridad: **la primera sesión con órdenes va en Practice, con kill-switch
   probado y con la revisión posterior de siempre**.
4. Nada se promueve: la estrategia sigue sin edge demostrado; esto fue una prueba de **fontanería**, y salió
   bien.
