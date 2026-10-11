# PREREGISTRO V3-TOPSTEP — churn MC con las reglas REALES de Topstep (CONGELADO 2026-10-05)

**Objetivo:** el número real de fondeo para Topstep (donde tenemos API key): ¿con qué probabilidad y en cuánto tiempo se pasa la **Combine 50k** (y 100k de contraste) operando el pool MNQ+MGC?

## Reglas congeladas (leídas de `src/funded_profiles.py::topstep_profile`, verificadas 2026-10-05)

- 50k: target $3,000 · MLL trailing $2,000 (EOD, se LOCKea en $50,000) · 100k: $6,000 / $3,000 (lock $100,000).
- **Consistencia suave:** mejor día ≤ 50% del target; al romperla el target sube a 2×mejor día (modelado con `consistency_max_day_ratio=0.5`).
- **Mínimo de días:** 5 (conservador; el help oficial dice "as few as two days" — contradicción declarada, sensibilidad con 2).
- **DLL:** opcional/ajustable por el trader → primario SIN DLL; sensibilidad con el default $1,000 (50k) como terminal.
- No existe cuenta 25k en Topstep — la unidad de churn es la 50k.

## Hipótesis

- **H6:** con el pool MNQ+MGC y 2 contratos, la Combine 50k tiene **P(pasar) ≥ 45%** y mediana (si pasa) **≤ 2 meses**.
  - CONFIRMADA ⟺ ambas condiciones. FALSADA ⟺ cualquier otra. Se reportan TODAS las celdas.

## Escenarios predeclarados (todos se reportan)

Primarios (orden por CIERRE, semántica del V2 aprobado): 50k @1,2,3,5 contratos · 100k @1,2.
Sensibilidades declaradas: 50k@2 con min_días=2 (regla oficial) · 50k@2 con DLL $1,000 terminal · orden por ENTRADA.

## Métricas
P(pasar), meses mediana si pasa/muere (directos del timeline), intentos esperados, ciclo, cuentas/año (throughput TEÓRICO). B=2000, semilla 20260928.

## NO es
No modela la fase Express Funded (próximo bloque con las reglas de payout ya encontradas). No usa reglas de Apex. No cambia la estrategia.
