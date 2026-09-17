# BLOCKERS — Bloque A2.5: Auditoría de Ambigüedad Intrabarra

## Estado General
- **Bloque completado:** Sí, 100 % offline utilizando barras M5 y M1 de `databento.zip`.
- **Integridad:** Sin modificaciones en `src/` ni `tests/`.

## Hallazgos Críticos
1. **Impacto en el Backtest M5:** Para estrategias de rango estrecho como SMC-FVG, un 10.4 % de trades ambiguos en M5 genera una distorsión sustancial si se evalúa exclusivamente en M5.
2. **Recomendación Operativa:** Para backtesting riguroso de estrategias scalping/SMC, FARS debe mantener el enfoque de dos pasadas: detección rápida en M5 y resolución causal selectiva con M1 para las barras ambiguas.
