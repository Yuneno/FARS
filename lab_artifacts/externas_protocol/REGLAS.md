# REGLAS MECÁNICAS — SEÑALES EXTERNAS (JITA-BOT → FARS)

**Fecha:** 2026-09-28  
**Bloque:** EXTERNAS-E1  
**Fuentes analizadas:** `E:\FARS-LAB\jita-bot-review\` (`patterns/monday_effect.py`, `patterns/amd.py`, `patterns/crt.py`, `patterns/crt_ema.py`, `patterns/ema.py`, `patterns/ny_time.py`, `patterns/c2c_crt.py`, `signals/engine.py`, `research/canales_youtube/RESULTADOS_MAESTROS.md`, `research/libros_vivos_oos/INFORME.md`).

---

## 1. Contexto y Objetivos

El objetivo de este documento es formalizar con rigor estadístico y precisión causal las reglas mecánicas exactas de las señales validadas en el repositorio de Juanca (`jita-bot-review`), separando:
1. Lo que está **formalizado en código**.
2. Lo que está **reportado como métrica de backtest** en `RESULTADOS_MAESTROS.md`.
3. Las **discrepancias, supuestos implícitos o lagunas de evidencia**.

Falta de evidencia = desconocido; ninguna regla se inventa.

---

## 2. Inventario de Señales Validadas

Las señales que pasan los criterios de validación estadística en la tabla maestra de Juanca son:

| Clave | Nombre en Juanca | T-stat Juanca | Muestra Juanca | Claim/Referencia Juanca |
|---|---|---|---|---|
| **D2L** | Ventana domingo 18:05 → lunes 16:00 ET | T = 4.12 | n = 168 semanas | +70.09 pts netos, DSR = 0.9995, perm p ≈ 0.0000 |
| **L0200** | Entrada lunes anticipada 02:00 ET (LucidFlex) | T = 3.74 | 7/9 horarios pasan | Bonferroni OK, candidato operativo |
| **ACT** | Confluencia triple AMD + CRT + EMA50/4h | T = 4.85 | n = 60 trades | WR 73.3%, +58.08 pts, cross-val MES T=2.48 |
| **AC** | Confluencia AMD + CRT sin filtro EMA | T = 6.37 | n = 138 trades | Control del filtro (mismo trade 145/303 días) |

---

## 3. Reglas Mecánicas Señal por Señal

### 3.1. D2L — Drift Domingo 18:05 ET → Lunes 16:00 ET

* **Hipótesis económica:** El drift alcista del equity index (MNQ) durante el arranque semanal se concentra en la reapertura dominical de CME y la sesión del lunes RTH; de martes a viernes el drift neto se aplana o se vuelve negativo.
* **Activo:** Micro E-mini Nasdaq-100 (MNQ), futuros CME. Multiplicador: $2.00 / punto; tick: 0.25 puntos.
* **Dirección:** Estrictamente **LONG** (compra). Sin posiciones cortas.
* **Horario y Semántica de Sesión:**
  * Zona horaria canónica: `America/New_York` (ET con soporte exacto de DST: UTC-5 en invierno EST, UTC-4 en verano EDT).
  * Inicio de ventana: Domingo a partir de las 18:00 ET (reapertura semanal de CME Globex). En compresión M5, la primera vela que abre a las 18:00 cierra a las 18:05 ET; la entrada al open de la barra siguiente es a las **18:05 ET**.
  * Fin de ventana: Lunes a las 16:00 ET (cierre de Regular Trading Hours, RTH).
* **Entrada:**
  * Orden a mercado a la apertura de la barra M5 de las 18:05 ET (o el open de la primera barra disponible tras la apertura dominical 18:00 ET).
  * Si la barra de reapertura dominical falta por gap de datos o festivo, no se emite señal (fail-closed, nunca interpolar precio).
  * Idempotencia: Una única entrada por semana calendario (`week = domingo.isoformat()`).
* **Gestión de Posición (Stop Loss y Take Profit):**
  * **SL validado:** 372.9 puntos de MNQ ($745.80 por micro). SL catastrófico de emergencia.
  * **TP validado:** 150.0 puntos de MNQ ($300.00 por micro).
  * **Salida por tiempo (Time Exit):** Si ni TP ni SL son alcanzados durante la sesión, la posición se liquida obligatoriamente al cierre RTH del lunes a las 16:00 ET (orden a mercado en la apertura de la barra 16:00 o cierre de 15:55).
* **Discrepancia documentada en código de Juanca:**
  * En `patterns/monday_effect.py` líneas 9-13, Juanca advierte expresamente:
    * El paper-trading de su bot tenía configurado un `SL = 150 pts`, pero dicho SL **NUNCA se validó** estadísticamente ($T=2.45$, cae bajo umbral $T \ge 3.0$), debido a que el MAE semanal tiene mediana 85.8 pts y percentil 90 de 314.4 pts. Un SL de 150 pts corta prematuramente ganadoras 1 de cada 3 semanas.
    * El SL que **sí superó la validación** es `SL = 372.9 pts` y `TP = 150.0 pts` ($T=2.75$ a $4.12$ según holding).
    * En el backtest no-SL/holding puro (Strategy 21 de `RESULTADOS_MAESTROS.md`), el resultado reportado es $+70.09$ pts netos con $T=4.12$.

---

### 3.2. L0200 — Entrada Lunes Anticipada 02:00 ET (Variante LucidFlex)

* **Hipótesis económica:** En lugar de asumir el riesgo del fin de semana completo (apertura domingo 18:00 ET hasta madrugada), entrar en la apertura europea (Frankfurt/London: 02:00 ET / 07:00-08:00 UTC) captura la mayor parte del drift alcista del lunes reduciendo horas de exposición nocturna.
* **Activo:** MNQ (Micro E-mini Nasdaq-100).
* **Dirección:** Estrictamente **LONG** (compra).
* **Horario y Sesión:**
  * Hora de señal: Lunes a las 01:55 ET cerrada → Entrada a las **02:00 ET** (open de la barra M5).
  * Salida obligatoria: Lunes a las **16:00 ET** (cierre RTH).
* **SL / TP:**
  * SL de emergencia: 372.9 puntos (mismo stop validado que D2L) o stop estructural de la sesión asiática previa (según variante LucidFlex).
  * TP: 150.0 puntos o salida por tiempo a las 16:00 ET.
* **Discrepancia documentada:**
  * En `RESULTADOS_MAESTROS.md`, Juanca anota: "(7/9 horarios pasan) T=3.74, Bonferroni OK". Indica que evaluó 9 horas de entrada alternativas (multi-testing), de las cuales 02:00 ET fue la más representativa.
  * Por protocolo de multiplicidad de hipótesis, cualquier análisis de L0200 debe reportar la penalización de Bonferroni con $m=9$ variantes.

---

### 3.3. ACT — Confluencia Triple AMD + CRT + EMA50/4h

* **Hipótesis económica:** Un patrón de manipulación intradía (AMD) y barrido de rango previo (CRT) solo tiene ventaja predictiva genuina si opera a favor de la tendencia intermedia superior (EMA50 en velas de 4 horas).
* **Componente 1: Ciclo AMD (Accumulation - Manipulation - Distribution):**
  * **Acumulación (Pre-NY):** Rango de 00:00 a 09:30 ET.
    * Condición de compresión: Amplitud del rango pre-NY ($\text{High} - \text{Low}$) estrictamente MENOR que la mediana de amplitudes del mismo día de la semana observada en sesiones previas (lookback mínimo 30 muestras por día de semana, ventana histórica rodante).
  * **Manipulación (Confirmación RTH):** Ventana de 09:30 a 10:30 ET (primeros 60 min de RTH).
    * Si una vela M5 rompe el $\text{High}_{\text{pre-NY}}$ con mecha pero su cierre cae por debajo ($\text{Close} < \text{High}_{\text{pre-NY}}$) → Manipulación alcista confirmada → Señal **SHORT** (fade).
    * Si una vela M5 rompe el $\text{Low}_{\text{pre-NY}}$ con mecha pero su cierre queda por encima ($\text{Close} > \text{Low}_{\text{pre-NY}}$) → Manipulación bajista confirmada → Señal **LONG** (fade).
* **Componente 2: Patrón CRT (Candle Range Theory) sobre PDH/PDL:**
  * Nivel de referencia: Rango RTH del día anterior (09:30 a 16:00 ET).
    * $\text{PDH}$ = Previous Day High (RTH).
    * $\text{PDL}$ = Previous Day Low (RTH).
  * Gatillo causal: Dentro del RTH de hoy (09:30 a 16:00 ET), el precio barre con mecha $\text{PDH}$ y cierra por debajo (dirección SHORT), o barre $\text{PDL}$ y cierra por encima (dirección LONG).
  * Causalidad estricta: Ambas confirmaciones deben haber ocurrido en o antes de la vela de entrada.
* **Componente 3: Filtro de Tendencia EMA 50 / 4 Horas:**
  * Rejilla 4H: Horarios canónicos NY (01:00, 05:00, 09:00, 13:00, 17:00, 21:00 ET).
  * Causalidad: Se toma únicamente la **última vela de 4H completamente cerrada** antes de la hora actual de decisión (la vela 4H en curso se excluye estrictamente para evitar repintado).
  * Regla de filtro:
    * Para trades LONG: $\text{Close}_{\text{H4 cerrado}} > \text{EMA}_{50}(\text{H4})$.
    * Para trades SHORT: $\text{Close}_{\text{H4 cerrado}} < \text{EMA}_{50}(\text{H4})$.
  * Si el régimen EMA se opone a la dirección del fade AMD/CRT, la señal queda vetada.
* **SL / TP y Sizing:**
  * $\text{SL} = \min(2.0 \times \text{ATR}_{14}(\text{M5 RTH}), 50.0\text{ pts})$.
  * $\text{TP} = 2.0 \times \text{SL}$ (ratio beneficio/riesgo 2:1).
  * Sizing: 1 contrato micro MNQ.
  * Time Exit: 60 minutos de permanencia máxima o fin de sesión RTH (16:00 ET).
* **Discrepancias críticas documentadas en el código:**
  1. **Look-ahead en la investigación de Juanca:** En `amd_crt.py` de FARS (líneas 4-7) se documenta que la investigación original de Juanca en `mnq-strategies` utilizó un filtro no causal de CRT: `date.isin(crt_days)`. Un barrido de CRT ocurrido a las 15:30 ET validaba retroactivamente un AMD disparado a las 09:35 ET. El port de FARS exige causalidad estricta barra a barra.
  2. **EMA10 vs EMA50 en 4H:** En `patterns/crt_ema.py` Juanca utiliza `EMA_PERIOD = 10` sobre H4, mientras que en `RESULTADOS_MAESTROS.md` (estrategia 23) afirma confluencia con `EMA50/4h` ($n=60, T=4.85$). El módulo de señales de FARS debe soportar explícitamente `EMA50/4h` como la regla de la tabla maestra y permitir contrastar con `EMA10`.

---

### 3.4. AC — Brazo de Control AMD + CRT sin Filtro EMA

* **Definición:** Exactamente la misma lógica de confluencia causal AMD + CRT descrita en §3.3, pero con `use_ema_filter = False`.
* **Función metodológica:** Sirve de brazo de control para aislar el valor añadido real del filtro EMA50/4h.
* **Referencia en Juanca:** Estrategia 25 de `RESULTADOS_MAESTROS.md`: $n=138$ trades, $T=6.37$ (correlacionado con ACT: comparten 145/303 días candidatos).

---

## 4. Matriz de Parámetros y Convenciones

| Parámetro | D2L | L0200 | ACT | AC (Control) |
|---|---|---|---|---|
| **Instrumento** | MNQ | MNQ | MNQ | MNQ |
| **Timeframe Señal** | M5 | M5 | M5 | M5 |
| **Timeframe Filtro** | N/A | N/A | H4 (240 min) | N/A |
| **Período EMA** | N/A | N/A | 50 (opcional 10) | N/A |
| **Ventana Entrada** | Dom 18:05 ET | Lun 02:00 ET | 09:30–10:30 ET | 09:30–10:30 ET |
| **SL (pts)** | 372.9 | 372.9 | $\min(2\times\text{ATR}_{14}, 50)$ | $\min(2\times\text{ATR}_{14}, 50)$ |
| **TP (pts)** | 150.0 | 150.0 | $2.0 \times \text{SL}$ | $2.0 \times \text{SL}$ |
| **Salida Tiempo** | Lun 16:00 ET | Lun 16:00 ET | 60 min / 16:00 ET | 60 min / 16:00 ET |
| **Sizing** | 1 contrato | 1 contrato | 1 contrato | 1 contrato |
| **Comisión / lado**| $0.62 | $0.62 | $0.62 | $0.62 |
| **Slippage base** | 0.25 pts | 0.25 pts | 0.25 pts | 0.25 pts |
| **Ambigüedad intra**| SL-first | SL-first | SL-first / M1 | SL-first / M1 |

---

## 5. Reglas de Validación y Prevención de Sesgos

1. **Cero Look-Ahead:** Toda señal se calcula con barras cerradas en $t \le t_{\text{eval}}$. La ejecución se registra al precio de apertura de $t+1$.
2. **Prioridad SL en ambigüedad:** Si el rango $[\text{Low}, \text{High}]$ de la barra contiene simultáneamente el TP y el SL, se asume que el Stop Loss se ejecutó primero (criterio conservador), salvo resolución granular con barras M1 contiguas (`resolve_intrabar_with_m1`).
3. **DST y Horario ET:** El reloj del CME opera en horario ET. Las conversiones de UTC a ET deben respetar rigurosamente los cambios de horario de verano/invierno para que las ventanas 18:00, 02:00, 09:30 y 16:00 no sufran desfase de 1 hora.
4. **Corte Canónico de Datos:** 2019-05-06 en adelante (nacimiento oficial del contrato MNQ). Datos previos son proxy rescalado y deben evaluarse únicamente como sensibilidad fuera de muestra histórica.
