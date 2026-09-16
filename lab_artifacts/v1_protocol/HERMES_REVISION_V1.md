# HERMES REVISION — Reversión V1 (C1 desde el día 1)

**Fecha:** 2026-09-16 · **Revisor:** Hermes · **Implementador:** Codex (`gpt-5.6-sol`/openai, effort low)
**Rama:** `bloque-v1-reversion` @ `88dd7e8` (worktree `E:\FARS-LAB\FARS-v1-reversion`, base `dcaef24`) · **Push:** ninguno (30 commits locales, 0 en origin)

## Veredicto

**Implementación: PASS** — fiel al encargo, sin tocar nada existente, honesta en sus límites.
**Bloque: INCOMPLETO por diseño declarado** — el protocolo C1 **no se ejecutó** (`not_run_compute_budget`):
existe la maquinaria y el preregistro, no las métricas. Nada se inventó. Correcto y honesto: es lo que
se pidió ante un presupuesto de 5-6 h de cómputo.

## 1. Verificado por el revisor (no self-report)

| # | Qué | Cómo | Resultado |
|---|---|---|---|
| 1 | 12 tests nuevos | Los corrí: `12 passed in 0.79s` | ✅ |
| 2 | Suite completa | En entorno limpio (el sandbox daba 212 errores de ACL en `tmp_path`): **`1575 passed, 2 skipped in 315.81s`** = baseline 1563 + 12 nuevos | ✅ |
| 3 | Nada existente modificado | `git status`: sólo 7 archivos **nuevos** (estrategia, tests, runner, preregistro, manifest, informe, blockers) | ✅ |
| 4 | Determinismo | Dos corridas sobre 20.000 barras canónicas: trades **idénticos** (R, timestamps, motivos) | ✅ |
| 5 | Composición §0 | Lectura de código: sweep sólo de zonas con `state == "swept"` (`liquidity` + `session_level`) y `available_at <= decisión`; **CHoCH/MSS real** (rechaza BOS de continuación); desplazamiento `disp_mult`×rango medio 20 barras; FVG de entrada con `available_at <= decisión`; CISD fresco (`available_at == decisión`); OTE opcional; **target = pool opuesto y `None` ⇒ no trade**; stop = extremo del barrido ± buffer; chequeo de geometría `stop < entry < target` | ✅ |
| 6 | Detección no duplicada | Consume `src/detectors/{sweeps,structure,fvg,cisd,pivots}` + `ZoneEngine` + `zones/fibonacci`; no reimplementa nada | ✅ |
| 7 | Preregistro antes de resultados | `preregistered_at_utc = 2026-09-16T22:43:51Z` < manifest `22:53:45Z`; sin resultados que puedan haberlo inducido | ✅ |
| 8 | Sin push / sin dependencias | Verificado | ✅ |

## 2. Hallazgos del revisor (medidos, no opinión)

1. **Frecuencia: 8 trades en 20.000 barras M5 (~69 días) ⇒ ~0,12 trades/día.** Bajo la lente de Apex
   (30 días), eso son **3-4 operaciones por challenge** — el mismo problema estructural que
   `RANKING.md` de Kai le achaca a CRT 4H (*«no es que pase poco, es que no le da tiempo a jugar la mano»*).
   Con `use_cisd=True` (arranque) y entrada límite. Las variantes declaradas (sin CISD, entrada a
   mercado) subirán la frecuencia — hay que medirlo, no suponerlo.
2. **Target = pool opuesto MÁS CERCANO ⇒ objetivos diminutos.** En la muestra: entrada 7545.2 →
   target 7538.5 = **6,7 puntos**, y el E[R] de los 8 trades ganadores fue **+0,18 R de media** (todos
   TP): el objetivo sistemáticamente **más pequeño que el riesgo**. Es consecuencia de la convención
   que fijé en el encargo ("el pool activo más cercano del lado contrario"). Alternativa **declarada**
   para la siguiente iteración (no arreglar en silencio): pool opuesto con `min_touches ≥ N` o el
   **nivel de sesión mayor** (PDH/PDL/D20) como target, y medir ambas.
3. **Throughput: 394 barras/s** (20.000 barras en 50,8 s) ⇒ una pasada completa del canónico ≈ **32 min**
   ⇒ variantes × 3 escenarios de coste ≈ las 5-6 h que estimó Codex. **Antes de quemar esa ventana conviene
   optimizar el bucle** (candidato claro: `self._engine.update(self._bars)` por barra + `all_zones()`
   ordenando en cada barra).
4. **Observación menor:** `_new_sweep` llama a `detect_sweeps(...)` "para sus diagnósticos causales" y
   **descarta el resultado** (el gate real es el lifecycle de zonas). No cambia el comportamiento, pero
   es una llamada muerta que confunde al leer; documentarlo o quitarla.
5. **Limitación declarada por Codex y aceptada:** el executor **no tiene hook de cancelación** de una
   orden límite pendiente (`evaluate() -> None` no la retira; sólo fill o expiración). La invalidación
   por estructura se registra en la estrategia pero su efecto ejecutivo es `no-evaluable`. Si se quiere
   la semántica completa, es un bloque aparte que toque el executor **con su propio gate**.
6. **Landmine heredado:** `reversal_v1_config` usa `max_contracts: 1_000_000` (patrón *research* de
   `smc_ob_config`). Correcto para medir, **prohibido** para el carril de fondeo: ahí va con cap por cuenta.

## 3. Estado del repo

- Commit de aterrizaje: **`88dd7e8`** en `bloque-v1-reversion` (el sandbox de Codex no podía escribir `.git`;
  los 7 archivos quedaron sin commit y los aterrizo Hermes). **Sin push.**
- `main` sigue en `dcaef24`; Z5 corre en paralelo en `E:\FARS-LAB\FARS` (otro agente, sin conflicto).
- Temporal pendiente de retirar con OK del usuario: `lab_artifacts/pytest-v1-clean/`.

## 4. Lo que sigue (decisión del usuario)

1. **Optimizar el bucle** (~5-10x esperado) antes de correr el protocolo: entonces las 5-6 h bajan a
   algo que cabe en una sesión. Requiere su propio encargo/micro-bloque (no mezclar con la estrategia).
2. **Corrida del protocolo** con la maquinaria ya existente (o con un recorte declarado: menos
   escenarios/variantes, nunca disimulado).
3. **Revisar la convención de target** (hallazgo 2) como variante declarada en el preregistro.
