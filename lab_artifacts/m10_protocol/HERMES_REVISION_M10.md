# HERMES_REVISION_M10 — Verificación del bloque M10 (v2)

**Fecha:** 2026-09-19 (local; runs ~2026-09-20 00:27 UTC) · **Revisor:** Hermes · **Entrega:** Gemini
**Rama:** `bloque-m10-evaluacion` @ `43188a3` (base `bloque-fix-fillbar` @ `277efae`) · **Sin push** (decisión del dueño)

## Veredicto: PASA con 6 refinamientos registrados (ninguno toca los números; §3)

## 1. Lo verificado (con evidencia)

1. **Gate del stream C1 (recalculado por el revisor sobre el JSON entregado):** n = 3.583 ·
   WR = 45,185598660340 % · E[R] = −0,130067563494 · Σnet = −233.016,04 == metadata.
   → El stream ES el baseline limpio sancionado (idéntico a 12 decimales al gate del encargo).
2. **Manifest:** 13/13 SHA-256 recalculados y exactos.
3. **Preregistro congelado antes de correr** (mtime y lectura estructural del runner: lo verifica al arrancar);
   candidatos, sizing, semillas, perfiles, variantes y criterios económicos declarados ex-ante.
4. **CBB real y determinista:** re-corrida del revisor con las funciones del propio entregable →
   **9/9 celdas reproducen EXACTO** (C1 50k 0,5/1,0 %, 100k/150k 1,0 %; C3 matched y zero-edge;
   C2 −0,05/+0,10/+0,15). Scripts de verificación: `E:\FARS-LAB\_tmp_m10_check\`.
5. **Alcance:** solo `lab_artifacts/m10_protocol/` (14 archivos); `src/` y `tests/` intactos;
   0 push; commit único.
6. **MAE FULL (causal, M1 de databento.zip)** aplicado a C1 y al peldaño +0,10 R — convención D cumplida.
7. **Coherencia interna:** categorías suman 100 % en todas las celdas; escalera monótona; tabla económica
   con aritmética correcta (1/P; F/P); cross-checks en orden de magnitud.

## 2. Números que firman

- **C1:** 50k = 2,98 % (0,5 %) y **11,30 %** (1,0 %); 100k = 9,23 %; 150k = 9,26 %.
- **C1 ≈ control sintético emparejado** (11,30 vs 10,61) → la estrategia no aporta nada sobre una
  secuencia sintética con su misma deriva; y queda **por debajo del zero-edge** (27,94 % / peldaño
  +0,00 = 27,60 %) porque su deriva real es negativa (no "suerte pura": peor).
- **Umbral declarado:** 2×F se cruza en **+0,15 R** (57,30 %); +0,10 R = 47,58 % (a un paso).
  → **La meta de M11 queda cuantificada: E[R] ≥ +0,10–0,15 R con CI_low > 0.**
- **Gasto esperado por pase (foto actual):** $421–$1.685 según fee ($50–$200).
- **Veredicto: NO PAGAR** ✓ endosado.

## 3. Refinamientos registrados (no bloquean; NO aplicados aquí porque el manifest fija los hashes — para el próximo pase)

- **R1** — `preregistro.metadata.preregistered_at_utc` = `"2026-09-19T20:25:30+00:00"` está en **hora local
  etiquetada como UTC** (real: 2026-09-20T00:25:30Z). Corregir etiqueta (el orden de congelación es válido).
- **R2** — `delta_pass_pct` se publica como **+0,70 pp** y es una **caída** de 0,70 pp (11,30 → 10,60).
  Reetiquetar (Δ cerrado−full = −0,70 pp).
- **R3** — Frase de Q2/veredicto ("11,3 % de pase por puro azar (zero-edge ~27,9 %)"): induce a error.
  Correcto: **C1 ≈ control emparejado (sin aporte) y ambos por debajo del zero-edge** (deriva negativa).
- **R4** — El brazo **zero-edge** no consta en el preregistro (solo `matched`); declararlo como brazo
  de robustez o anotarlo en el acta del bloque.
- **R5** — Nota metodológica (1 línea): la mezcla quema/bloqueo **no es comparable entre brazos** — el
  control normal tiene cola izquierda suave (más quema por overshoot del piso), mientras las pérdidas
  reales están agrupadas en ≈ −1 R y la regla de colchón (0,75) las detiene "bloqueadas" antes del piso.
- **R6** — Q4/tabla económica usan el peldaño −0,13 R (11,87 %, semilla −13) en vez de C1 (11,30 %):
  unificar o etiquetar; y declarar que la tabla usa modelo EOD (con FULL, el gasto sube ~5-7 %).
- (menor) SyntaxWarning de escapes en la plantilla del INFORME dentro de `run_m10.py`.

## 4. Estado

Bloque **PASA**. Merge de la cadena a `main` (fillbar + M10): decisión de Ricardo.
Siguiente: **M11** (encargo del revisor en preparación) — su criterio de éxito es la meta que M10 acaba de cuantificar.

*Código y artefactos por Gemini; verificación independiente, refinamientos y cierre por Hermes (este review).*
