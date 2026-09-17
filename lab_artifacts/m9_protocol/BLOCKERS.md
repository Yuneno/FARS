# BLOCKERS — Bloque M9: Screening de Entradas Crudas

Registro formal de decisiones declaradas, limitaciones metodológicas y estado de fricción.

## 1. Veredicto del Screening: Cero Entradas Supervivientes

- **Condición de Parada:** El criterio preregistrado establecía que una entrada requería coto inferior de IC95 (CBB) $> 0$ en $\ge 2$ mercados en el escenario realista para pasar a la fase C1.
- **Resultado:** 0/5 entradas cumplieron el criterio. Todas fueron descartadas.
- **Implicación para el Laboratorio:** Queda formalmente cerrado el camino de promover cualquiera de estas 5 variantes crudas a producción o a `src/`. No hay base estadística que justifique su despliegue.

## 2. Decisiones Declaradas en Preregistro y Arquitectura

- **Convención de Riesgo Fija:** $k = 1.0$ ($Stop = 1.0\cdot ATR(14)$, $Target = 2.0\cdot ATR(14)$) sin optimización ni barrido de parámetros. Declarado para evitar overfitting y «pesca».
- **Horizonte Temporal Máximo:** `max_bars_held = 48` (4 horas en velas M5). Operaciones abiertas al final del horizonte se cierran a precio de mercado (`bar.open`).
- **Cooldown Intradía:** `cooldown_bars = 6` (30 minutos tras cada salida) para evitar sobre-operar la misma señal consecutiva.
- **Ejecución al Siguiente Bar:** Toda señal generada en la barra $t$ entra en el open de la barra $t+1$ (`bar.open`), garantizando 100% causalidad point-in-time.

## 3. Estado de la Fricción y Modelado de Mercados

- **Validación Empírica de Slippage:** El modelo de costes realista ($1.24 RT + 1 tick slippage en entradas/stops a mercado) fue calibrado empíricamente sobre MNQ. En MES, MYM y MGC, se asumió 1 tick de slippage como aproximación estándar conservadora del mercado CME/COMEX/CBOT.
- **Impacto en los Resultados:** Dado que el rendimiento es negativo incluso en el escenario canónico sin slippage ($E[R] < 0$ y $PF < 0.85$ en la gran mayoría de celdas), la incertidumbre residual en el slippage de MES/MYM/MGC **no afecta el veredicto**: ninguna entrada fue rechazada al borde por causa del slippage.

## 4. Controles de Calidad y Fraude

- **Anti-Fraude de Envoltorios (Wrappers):** 20/20 comprobaciones pasaron con hash `trades_sha256` idéntico al 100% bit a bit entre la estrategia directa y su wrapper trivial.
- **Auditoría Causal:** 0 violaciones point-in-time en 156 decisiones muestreadas a lo largo de los 4 mercados.

