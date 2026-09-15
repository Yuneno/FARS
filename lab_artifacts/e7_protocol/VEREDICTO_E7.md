# E7 — Veredicto multi-mercado (MYM/MGC)

**Fecha:** 2026-09-15 · **Preregistro:** `preregistro_multimercado.json` · **Runner:** `run_e7_multimercado.py` · **Resultados:** `resultados.json`

## Per-market (por_tramo, post-E5)

| Mercado | Config | n | E[R] | PF | folds+ | Veredicto |
|---|---|---:|---:|---:|---:|---|
| MYM | 5.0 | 8,697 | −0.197 | 0.669 | 0% | **FAIL (edge muerto)** |
| MYM | 8.0 | 6,683 | −0.102 | 0.811 | 0% | FAIL |
| MYM | 10.0 | 5,452 | −0.074 | 0.858 | 12% | FAIL |
| **MGC** | 5.0 | 452 | **+0.090** | 1.199 | 75% | FAIL — **INSUFFICIENT_SAMPLE** (4/8 folds delgados) |
| **MGC** | 8.0 | 189 | **+0.206** | 1.508 | 75% | FAIL — INSUFFICIENT_SAMPLE |
| **MGC** | 10.0 | 125 | **+0.170** | 1.407 | 75% | FAIL — INSUFFICIENT_SAMPLE |

**Lectura:** el port directo de SMC-FVG **no sobrevive en MYM** (la microestructura del Dow es otra: tick 1.0 vs 0.25, magnitudes distintas — los stops en puntos absolutos no transfieren). En **MGC el edge sobrevive y es más fuerte que en MNQ** (PF 1.20-1.51 vs 1.16-1.24) pero la muestra es chica → gate honesto de insuficiencia.

## Cuenta combinada (motor FULL, 0.4671%, 25K)

| Pool | Config/Política | Pase | Quema | Bloqueadas | vs MNQ solo |
|---|---|---:|---:|---:|---|
| MNQ+MYM+MGC | 5.0 fija | 16.0% | 65.3% | 18.3% | 💀 el MYM envenena el pool |
| **MNQ+MGC** | 5.0 fija | **51.8%** | 13.0% | 17.7% | **+5.2pp de pase**, fracaso igual |
| MNQ+MGC | 5.0 buffer k=0.10 | 47.3% | 7.2% | 10.3% | −13pp de fracaso |
| **MNQ+MGC** | 10.0 buffer k=0.10 | **37.0%** | 2.6% | 6.2% | **+4.9pp de pase** |

*(MAEs de MGC = fallback M5 conservador, sin M1 en disco — los números reales de MGC podrían ser mejores; los de MYM no se salvan.)*

## Veredicto final E7

1. **MYM: descartado.** El port directo se desangra (E[R] negativo en las 3 configs).
2. **MGC: candidato real, evidencia insuficiente.** Siguiente paso si se quiere cerrar: conseguir M1 de MGC (o más historia) y re-validar; o aceptar la muestra y usar MGC como diversificador menor.
3. **MNQ+MGC en la misma cuenta MEJORA el plan:** 51.8% de pase (5.0 fija) o 37.0% con 8.7% de fracaso (10.0 buffer). El multi-mercado funciona **cuando el mercado añadido tiene edge** — y lo medimos, no lo asumimos.
4. **La frecuencia sigue siendo el rey**, y el camino honesto para más frecuencia ahora es **el Bloque F (zonas/reversiones)**: más setups en MNQ (donde el edge está probado) en lugar de más mercados sin edge.
