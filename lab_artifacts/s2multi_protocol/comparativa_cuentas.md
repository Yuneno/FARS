# Comparativa de Cuentas Fantasma — Bloque S2-Multi

> **Resumen:** Evaluación en paralelo de 3 cuentas de fondeo Topstep (50k, 100k, 150k)
> procesando exactamente el **mismo stream continuo de señales** (SMC-FVG k=0.5) en modo replay offline.

---

## 1. Tabla Comparativa de Desempeño y Estado

| Métrica / Parámetro | Topstep 50K Express | Topstep 100K Express | Topstep 150K Express |
|---|---|---|---|
| **Balance Inicial** | \$50,000.00 | \$100,000.00 | \$150,000.00 |
| **Balance Final** | **\$50,805.37** | **\$101,698.12** | **\$152,503.82** |
| **PnL Neto Total** | **+805.37 USD** | **+1,698.12 USD** | **+2,503.82 USD** |
| **Objetivo de Beneficio (Target)** | \$3,000.00 | \$6,000.00 | \$9,000.00 |
| **Límite Máximo de Pérdida (MLL)** | \$2,000.00 (trailing) | \$3,000.00 (trailing) | \$4,500.00 (trailing) |
| **Límite de Pérdida Diaria (DLL)** | \$1,000.00 | \$2,000.00 | \$3,000.00 |
| **Max Drawdown Registrado** | \$304.85 (0.61 %) | \$521.04 (0.52 %) | \$825.89 (0.55 %) |
| **Riesgo Nominal por Trade** | 0.5 % (\$250.00) | 0.5 % (\$500.00) | 0.5 % (\$750.00) |
| **Límite Contratos Micros** | 50 micros | 100 micros | 150 micros |
| **Señales Recibidas / Evaluadas** | 18 señales | 18 señales | 18 señales |
| **Señales Aprobadas por Riesgo** | 17 aprobadas | 17 aprobadas | 17 aprobadas |
| **Señales Vetadas por Riesgo** | 1 vetadas | 1 vetadas | 1 vetadas |
| **Órdenes Paper / Fills** | 17 órdenes | 17 órdenes | 17 órdenes |
| **Trades Cerrados** | 17 trades | 17 trades | 17 trades |
| **Trades Ganadores / Perdedores** | 7 W / 10 L | 7 W / 10 L | 7 W / 10 L |
| **Win Rate de la Sesión** | 41.2 % | 41.2 % | 41.2 % |
| **Estado Final de la Cuenta** | **ACTIVE** | **ACTIVE** | **ACTIVE** |

---

## 2. Observaciones del Despacho Multi-Cuenta

1. **Sincronización Total:** Las tres cuentas recibieron exactamente las mismas señales al mismo instante de tiempo.
2. **Dimensionamiento Proporcional:** Al usar un riesgo del 0,5 %, la cuenta de 150k opera 3 veces el tamaño de la de 50k, reflejándose proporcionalmente en el PnL y en el drawdown en dólares sin violar los límites de contratos.
3. **Independencia de Estado:** Cada cuenta mantiene su propio colchon de pérdida, trailing floor y reglas de parada sin interferencia mutua.
