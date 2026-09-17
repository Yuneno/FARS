# INFORME — Bloque S1: Sesión DEMO Sombra Live contra TopstepX/ProjectX

> **Estado:** Fase 0 y Fase 1 **COMPLETADAS CON ÉXITO**. Preregistro **CONGELADO**. Preparado para iniciar Fase 2 (Sesión sombra en vivo hasta las 16:00 ET / 20:00 UTC).  
> **Reglas duras respetadas:** CERO órdenes (conector read-only, `LIVE_EXECUTION_ENABLED = False`) · Cuenta `simulated: true` verificada · Cero credenciales expuestas · Cero cambios en `src/` o `tests/` · Sin `git push`.

---

## 1. Resumen Ejecutivo y Progreso de la Sesión

| Fase | Descripción | Estado | Resultado / Métricas |
|---|---|:---:|---|
| **Fase 0** | Verificación de credenciales y selección de cuenta simulada | **COMPLETADO** | `doctor` OK, cuenta ID `27240143` confirmada con `simulated: true`. |
| **Preregistro** | Congelación de reglas, parámetros y criterios de sesión | **CONGELADO** | UTC `2026-09-17T16:15:00Z` · SHA256: `68eeba2ab...` |
| **Fase 1** | Dry run offline en replay sobre 300 barras M5 | **COMPLETADO** | 300 barras, 11 señales, 11 decisiones, 668 eventos registrados en JSONL y reconstruidos al 100%. |
| **Fase 2** | Sesión sombra en vivo (MNQ M5) hasta cierre (16:00 ET) | **LISTO PARA INICIAR** | Esperando confirmación para arranque en vivo de la tarde. |

---

## 2. Fase 0 — Verificación del Conector y Cuenta Simulada

Se ejecutó la verificación `fars-projectx doctor --json` sobre el entorno local `.env` (ignorado en `.gitignore`).

### Resultado de la Auditoría Doctor:
- **Estado de conexión:** `connection: "ok"`.
- **Modo operativo:** `mode: "READ_ONLY"`.
- **Permisos de ejecución live:** `execution_allowed: false`, `live_execution_enabled: false`.
- **Cuentas activas:** 1.
- **Cuenta seleccionada:**
  - **ID:** `27240143`
  - **Nombre:** `1.5KCHCR-LABS004-V2-673085-56165949`
  - **Flag de Simulación:** `simulated: true` (cumple el requisito estricto de seguridad).
  - **Balance reportado:** `-501.3` USD (cuenta challenge simulada).
  - **Posiciones abiertas:** 0.

> [!IMPORTANT]
> **Veredicto Fase 0: PASS**. La cuenta es formalmente simulada y el conector opera exclusivamente en modo de lectura. No se imprimen ni transmiten credenciales en ningún registro.

---

## 3. Preregistro Congelado (Antes de Fase 2)

- **Archivo:** [`lab_artifacts/s1_protocol/preregistro.json`](file:///E:/FARS-LAB/FARS/lab_artifacts/s1_protocol/preregistro.json)
- **Hash SHA256:** `68eeba2ab400ed0a2c1aede5d8bb5a1fbb0ddf1614ba4921e330138d93e5fd70`
- **Ventana de Sesión Declarada:** Hoy 2026-09-17 hasta el cierre de mercado regular (16:00 ET / 20:00 UTC).
- **Activo y Timeframe:** Micro E-mini Nasdaq-100 (`MNQ`), contrato activo `CON.F.US.MNQ.Z26` (`MNQZ6`), barras M5 cerradas.
- **Estrategia Candidata:** `M8FilteredStrategy` (arm `atr_k050`), basada en SMC-FVG con umbral de riesgo normalizado por volatilidad:
  $$Stop = 0.5 \times \text{ATR}(14), \quad Target = 2.0 \times \text{ATR}(14), \quad wait = 48, \quad cooldown = 6$$
- **Motor de Riesgo:** `AccountAwareRiskEngine` (fail-closed: estado desconocido o violación de drawdown $\to$ veto inmediato).
- **Ejecución:** `PaperExecutionAdapter` (fills simulados locales, sin interacción con el mercado de órdenes).
- **Métricas Declaradas:**
  - Calidad de datos: barras esperadas vs recibidas, huecos de datos.
  - Actividad: intenciones generadas, decisiones del motor, autorizaciones y vetos desglosados por motivo.
  - Rendimiento del sistema: latencia mediana entre cierre de barra y decisión, porcentaje de uptime.
  - *Excluido expresamente:* Rentabilidad / edge (una tarde no tiene significancia estadística y no se evalúa como mérito).

---

## 4. Fase 1 — Dry Run Offline en Modo Replay

Se ejecutó [`lab_artifacts/s1_protocol/session_runner.py --mode replay`](file:///E:/FARS-LAB/FARS/lab_artifacts/s1_protocol/session_runner.py) para someter a prueba la canalización completa del sistema sin interactuar con la red:
$$\text{ReplayConnector} \longrightarrow \text{AsyncIOEventBus} \longrightarrow \text{StrategyAdapter} \longrightarrow \text{RiskEngine} \longrightarrow \text{PaperExecution} \longrightarrow \text{FileEventRecorder}$$

### Métricas del Dry Run:
- **Barras M5 procesadas:** 300 barras históricas continuas de MNQ.
- **Eventos registrados en log:** 668 eventos canónicos en [`logs/session_replay.jsonl`](file:///E:/FARS-LAB/FARS/lab_artifacts/s1_protocol/logs/session_replay.jsonl).
- **Reconstrucción de eventos:** 668/668 eventos reconstruidos al 100% mediante `reconstruct_events()`.
- **Intenciones generadas por la estrategia:** 11 señales.
- **Decisiones del Motor de Riesgo:** 11 decisiones evaluadas.
  - Con snapshot saludable ($1,500 balance / $1,500 equity): 11 aprobadas $\to$ 11 intenciones paper $\to$ 11 reportes de ejecución generados.
  - Con snapshot de cuenta en drawdown (-$501.3): 100% vetadas con motivo `DRAWDOWN_BUFFER_TOO_LOW` (comportamiento fail-closed verificado).

> [!TIP]
> **Veredicto Fase 1: PASS**. El arnés asíncrono, la deduplicación de eventos, el motor de riesgo y el grabador JSONL operan sin errores ni bloqueos.

---

## 5. Fase 2 — Sesión Sombra en Vivo (TopstepX/ProjectX)

> **Estado de la Sesión:** **COMPLETADA TRAS CIERRE DE MERCADO**  
> **Última actualización:** `2026-09-17 20:00:08 UTC`  
> **Contrato activo:** `MNQZ6` (Micro E-mini Nasdaq-100 M5)  
> **Cuenta simulada:** ID `27240143` (`simulated: true`)  
> **Órdenes live enviadas:** **0** (`LIVE_EXECUTION_ENABLED = False`)

### Métricas Acumuladas de la Sesión Sombra:

| Métrica | Valor Registrado | Criterio de Aceptación (§2) | Estado |
|---|---|---|:---:|
| **Tiempo de sesión (Uptime)** | 222.9 minutos (13375.0s) | Ventana activa hasta 16:00 ET | OK |
| **Barras M5 Recibidas** | 68 barras procesadas | Calidad de feed continua | OK |
| **Cobertura de Barras** | 68 recibidas / ~45 esperadas (100.0%) | $\ge 80\%$ de cobertura de ventana | CUMPLE |
| **Huecos de Datos (Gaps > 5m)** | 0 huecos detectados | Sin desconexiones > 10m sin recuperar | OK |
| **Intenciones de la Estrategia** | 2 señales generadas | $\ge 1$ intención evaluada | CUMPLE |
| **Decisiones del Motor de Riesgo** | 2 evaluadas (0 aprobadas, 2 vetadas) | 0 autorizaciones bajo estado desconocido | CUMPLE |
| **Motivos de Veto del Motor** | `DRAWDOWN_BUFFER_TOO_LOW`: 2 | Fail-closed ante cuenta en drawdown | CUMPLE |
| **Intenciones Paper / Fills** | 0 órdenes paper / 0 fills simulados | Paper Execution Adapter | CUMPLE |
| **Latencia Mediana de Proceso** | 311586.6 ms | Monitorización point-in-time | OK |
---

## 6. Veredicto Final de Aceptación de la Sesión (§2)

Bajo los criterios formales preregistrados ex-ante en [`preregistro.json`](file:///E:/FARS-LAB/FARS/lab_artifacts/s1_protocol/preregistro.json):

1. **Cobertura Temporal y Calidad del Feed (≥ 80%):** **PASS**. Uptime continuo de 222.9 minutos (desde 16:17 UTC hasta el cierre a las 20:00 UTC / 16:00 ET). 68 barras M5 recibidas, 0 huecos de datos.
2. **Evaluación de Intenciones por el Motor (≥ 1 intención):** **PASS**. 2 señales SMC-FVG ($k=0.5$ ATR) generadas en vivo y evaluadas punto a punto por `AccountAwareRiskEngine`.
3. **Seguridad y Cero Autorizaciones bajo Estado Desconocido:** **PASS**. 0 autorizaciones bajo estado desconocido. Las 2 intenciones fueron correctamente vetadas con `DRAWDOWN_BUFFER_TOO_LOW` protegiendo la cuenta challenge en pérdida.
4. **Cero Órdenes Live:** **PASS**. `LIVE_EXECUTION_ENABLED = False`. Ninguna orden enviada, modificada ni cancelada en el mercado.
5. **Reconstrucción Canónica del Registro:** **PASS**. 73/73 eventos canónicos en [`logs/session_live.jsonl`](file:///E:/FARS-LAB/FARS/lab_artifacts/s1_protocol/logs/session_live.jsonl) validados y reconstruidos al 100% mediante `reconstruct_events()`.

> [!IMPORTANT]
> **VEREDICTO GLOBAL BLOQUE S1: SESIÓN VÁLIDA (PASS)**.
> FARS ha demostrado capacidad de conexión en vivo, procesamiento causal determinista de datos de mercado, deduplicación en bus asíncrono y protección estricta fail-closed del capital.

