# Re-baseline del backtest AMD+CRT sobre Databento — 2026-09-09

## Resultados Databento (MNQ_M5.csv, limpio, 990,452 velas)

| Config | n | win | exp | PF | maxDD_R | tiempo |
|---|---|---|---|---|---|---|
| AMD+CRT (sin EMA) | 1362 | 0.423 | −0.0037 | 0.941 | 6.83 | 482s |
| AMD+CRT + EMA10/4h | 739 | 0.424 | −0.0011 | 0.982 | 4.87 | 2824s |

## Baseline extended (referencia previa, con ~54% proxy)

| Config | n | win | exp | PF | maxDD_R |
|---|---|---|---|---|---|
| AMD+CRT (sin EMA) | 1502 | 0.416 | −0.0050 | 0.913 | 9.01 |
| AMD+CRT + EMA10/4h | 824 | 0.415 | −0.0020 | 0.962 | 5.96 |

## Comparación y conclusiones

1. **Menos trades en Databento** (1362 vs 1502 base; 739 vs 824 EMA). Esperable:
   Databento tiene 139,030 barras M5 menos que extended (la mayoría del proxy
   NQ pre-2019), así que hay menos oportunidades de señal.

2. **Las conclusiones CUALITATIVAS son idénticas en ambos datasets:**
   - La estrategia base PIERDE en ambos (exp negativa, PF < 1).
   - El filtro EMA10/4h MEJORA en ambos (PF sube, exp menos negativa, maxDD baja),
     pero NO vuelve la estrategia rentable.
   - Win rate ~42% en ambos (por debajo del 50% de moneda).

3. **El fix de rendimiento (Tarea 1) se confirma:**
   - Base: 973s (extended, antes) → 482s (Databento, después). Nota: no son
     comparables directo (distinta cantidad de velas), pero el speedup de
     `_sl_tp` (46.8x) sí es real.
   - EMA sigue siendo ~5.9x más lento que base (2824s vs 482s) porque el ciclo
     de re-evaluación por barra cuando EMA rechaza aún no está resuelto del todo
     (el cache de CRT solo cubre confirmación positiva). Es un punto pendiente.

4. **Conclusión de fondo (NO cambia con el dataset):** la estrategia AMD+CRT,
   con o sin EMA, no tiene edge. Optimizar el dataset no arregla la expectancy
   negativa. El camino a edge real sigue siendo el meta-labeling (registrar
   señales y entrenar un clasificador), no el ajuste de parámetros.

## Estado del dataset canónico

- Databento MNQ_M5.csv es limpio y directo (sin proxy). Recomendado como
  canónico para MNQ.
- PENDIENTE (para otra tarea): localizar exactamente las 139k barras de
  diferencia con extended y confirmar que son todo proxy pre-2019.
- El contrato de datos multi-mercado (adaptadores por símbolo) sigue pendiente.
