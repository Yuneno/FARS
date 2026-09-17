# BLOCKERS — Bloque S2-Multi

## Estado General
- **Bloque completado:** Sí, 100 % offline en modo replay.
- **Cero órdenes live:** Garantizado (`LIVE_EXECUTION_ENABLED = False`).
- **Sin conexión externa:** Todas las barras fueron ingeridas desde `databento.zip`.

## Observaciones Operativas
1. **Correlación Total:** Al estar alimentadas por la misma estrategia y el mismo activo (MNQ), las tres cuentas replican la misma curva de retornos escalada por el tamaño de balance. Si una cuenta entra en drawdown, las tres entran en drawdown simultáneamente.
2. **Replay vs Live:** Para una sesión en vivo futura con múltiples cuentas, el broker (TopstepX/ProjectX) requeriría credenciales con permisos multi-cuenta o un token por cada ID de cuenta, manteniendo el mismo despachador fan-out validado aquí.
