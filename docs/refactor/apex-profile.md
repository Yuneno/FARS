# Perfil APEX Real — Especificación y Trazabilidad (Bloque D1)

Este documento establece la procedencia formal, constantes implementadas, contradicciones del código de referencia (`kai-backtesting`) y supuestos no resueltos para las cuentas de evaluación de **Apex Trader Funding** en FARS, de acuerdo con el encargo del Bloque D y la auditoría exhaustiva en `E:\FARS-LAB\KAI_APEX_SPEC.md`.

---

## 1. Tabla de Presets Verificados (`APEX_PRESETS` Intraday)

Implementados en `src/funded_profiles.py` mediante las funciones `apex_profile()`, `apex_25k_profile()`, `apex_50k_profile()`, `apex_100k_profile()`, `apex_150k_profile()`.

| Cuenta | Tamaño Inicial | Objetivo | Trailing DD (Intraday) | `lockBuffer` | Floor Ceiling (Bloqueo) | Pico que Bloquea Floor | Límite Micros (MNQ) | Límite Minis (NQ) | Procedencia en `KAI_APEX_SPEC.md` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **25K** | $25,000 | $1,500 | **$1,500** | $100 | $25,100 | $26,600 (DD + $100) | 20 | 2 | §1, §3.1 (`challenge_sim.py:72-73`) |
| **50K** | $50,000 | $3,000 | **$2,000** | $100 | $50,100 | $52,100 (DD + $100) | 40 | 4 | §1, §3.1 (`challenge_sim.py:74-75`) |
| **100K** | $100,000 | $6,000 | **$3,000** | $100 | $100,100 | $103,100 (DD + $100) | 60 | 6 | §1, §3.1 (`challenge_sim.py:76-77`) |
| **150K** | $150,000 | $9,000 | **$4,500** | $100 | $150,100 | $154,600 (DD + $100) | 100 | 10 | §1, §3.1 (`challenge_sim.py:78-79`) |

### Regla de Equivalencia Mini / Micro
- 10 contratos Micro (MNQ) = 1 contrato Mini (NQ).
- `micros_per_mini = Decimal("10")` (`strategy_sizing.py:44-59`).

---

## 2. Mecánica del Trailing Drawdown

1. **Monitoreo sobre Equity:** En FARS `mode="trailing"`, `reference="equity"`.
2. **Cálculo de Floor Dinámico:**
   $$\text{floor} = \min(\text{high\_watermark} - \text{trailing\_dd}, \;\text{starting\_balance} + \text{lockBuffer})$$
   En `src/funded_rules_v2.py`, esto se implementa algebraicamente como `_maximum_loss_threshold` con `threshold_ceiling = starting_balance + Decimal("100")`.
3. **Condición de Brecha (Quema):**
   $$\text{equity} \le \text{floor}$$
   Tocar exactamente el nivel de floor quema la cuenta (`breach_boundary="<="`, procedencia §4.6).
4. **Condición de Pase:**
   $$\text{balance} \ge \text{starting\_balance} + \text{target}$$
   Alcanzar o superar el objetivo antes de tocar el floor y dentro del horizonte califica para el pase (`pass_eligible`).

---

## 3. Contradicciones Identificadas en el Código de Referencia

De acuerdo con la auditoría de `KAI_APEX_SPEC.md`, se registran formalmente tres contradicciones en `kai-backtesting`:

### 3.1 Modelo de Drawdown: Intraday ($2,000/$4,500) vs Legacy/EOD ($2,500/$5,000)
- **Contradicción:** El spec de diseño antiguo (2026-07-29) usaba montos legacy ($2,500 en 50K y $5,000 en 150K).
- **Resolución en FARS:** Se usa estrictamente el modelo **Intraday** vigente de `APEX_PRESETS` ($2,000 en 50K y $4,500 en 150K), ya que usar los montos legacy concede $500 ficticios de margen inflando la tasa de pase un 25% (§1).

### 3.2 Comisión de MNQ: $1.00 vs $1.34 vs $1.42 Round-Trip
- **Contradicción:**
  - `src/kai_bt/core/contracts.py:59-64`: usa **$1.00 RT** ($0.50/lado).
  - `docs/COSTES-FUENTES-Y-SUPUESTOS.md`: cita **$1.34 RT** en backend TypeScript.
  - `README.md:24-26`: afirma **$1.42 RT**.
- **Resolución en FARS:** Para comparabilidad con el ejecutable Python se toma $1.00 RT base y en escenarios de estrés (C3/D) se evalúa hasta $1.42 y sobrecostes de fricción (§7).

### 3.3 Horizonte de Evaluación: 30 Días vs 90 Días vs Sin Límite
- **Contradicción:**
  - `challenge_sim.py:85-88`: fija **30 días de calendario** como plazo fatal.
  - `funded_sim.py:68-71`: afirma que "Apex NO capea el tiempo de evaluación (backend: solo minTradingDays, sin máximo)" y corre a **90 días**.
- **Resolución en FARS:** Se declara **30 días de calendario** como `SUPUESTO CONFIGURABLE` por defecto para paridad con el simulador del panel Kai, permitiendo evaluar la sensibilidad si se relaja (§5).

---

## 4. Parámetros Desconocidos / No Inventados en Evaluación

Siguiendo el principio de rigor metodológico, las siguientes reglas no se inventan ni se asumen:

1. **Regla de Consistencia:** En evaluación Apex no aplica consistencia (`consistency = None`, §10).
2. **Límite Diario de Pérdida (Daily Loss):** En evaluación no está activo (`daily_loss = None`, §10).
3. **Días Mínimos de Trading:** `minimum_trading_days = 0` (en el backend histórico no se aplicaba filtro bloqueante en evaluación, §10).
4. **Horarios y Noticias:** Se permite operar noticias (`news_trading_allowed = True`).
5. **Economía de Payouts:** No aplica a la fase de evaluación (challenge).

---

## 5. El Núcleo de D: Sesgo de Trades Cerrados vs MAE Intradía

El simulador de referencia de Kai evalúa el trailing únicamente sobre **equity de trades cerrados** (`challenge_sim.py:14-17`). El caveat literal documentado por ellos mismos es:
> *"Se evalúa sobre equity de trades CERRADOS. Apex mide el trailing INTRADÍA, así que un trade que va a −3R antes de girar a TP puede quemar una cuenta que aquí sobrevive. Para cerrar ese hueco haría falta el MAE por trade, que hoy las estrategias no devuelven — declarado, no resuelto."*

En FARS, gracias a los datos M1 canónicos (`src/backtest/intrabar.py`), se calcula el **Maximum Adverse Excursion (MAE)** exacto e intradía de cada trade. El perfil en `src/funded_profiles.py` soporta:
- `cadence="closed_trade"`: reproduce el modelo optimista de referencia.
- `cadence="intraday_event"`: aplica el trailing intradía real con MAE causal.
