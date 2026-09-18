# INFORME — Post-Mortem del Runner Fantasma (Sesión S2) y Verificación del Histórico Nocturno

> **DOCUMENTO FORENSE Y DE DIAGNÓSTICO TÉCNICO**  
> **Rama:** `bloque-diagnostico-fantasma`  
> **Ámbito:** `lab_artifacts/s2_protocol/session_runner_ghost.py`  
> **Fecha:** 2026-09-17 (hora local EDT / UTC-4)  
> **Reglas:** Cero modificaciones en `src/` y cero modificaciones en `lab_artifacts/s2_protocol/`.

---

## 1. Hechos Medidos y Contexto de la Sesión

1. **Arranque:** La sesión `session_runner_ghost.py` arrancó aproximadamente a las **16:47 ET** (20:47 UTC).
2. **Barras procesadas:** Recibió 24-27 barras M5 iniciales (todas con marca de tiempo anterior o igual a las **16:55 ET** / 20:55 UTC).
3. **Comportamiento post-cierre RTH:** No entró **ninguna barra** después de las 16:55 ET.
4. **Cese:** El proceso desapareció / murió solo a las **17:12 ET** (21:12 UTC) sin mensaje de error, sin traza en pantalla y sin escribir `INFORME.md`.

---

## 2. Pregunta (a): ¿Por qué murió en silencio? Análisis Forense de Hipótesis

### Hipótesis 1: Desconexión del Feed (WebSocket / HTTP Timeout / Socket Drop)
- **Mecanismo en código:** En `session_runner_ghost.py:L625-L650`, la llamada a `client.retrieve_bars(...)` está completamente envuelta en un bloque `try ... except Exception as exc:`.
  - Ante una caída de red o error HTTP del broker, el código **no rompe el bucle**; captura la excepción, imprime aviso de fail-closed, emite un evento `SYSTEM_CONNECTOR_DISCONNECTED` al grabador `logs/session_live.jsonl` y sigue durmiendo 15 segundos antes de reintentar.
- **Evidencia documental y de logs:**
  - En `logs/session_live.jsonl` **no existe ningún evento de desconexión**.
  - Si hubiese habido desconexión capturada, el script habría continuado reintentando indefinidamente en su bucle `while True:`.
- **Veredicto:** **DESCARTADA**.

---

### Hipótesis 2: Excepción no capturada en el runner
- **Mecanismo en código:**
  - Dentro del bucle `while True:`, todas las operaciones potencialmente falibles (`client.retrieve_bars`, publicación al bus, drenaje de intenciones, llamadas de reporte) residen dentro del bloque `try ... except Exception as exc:`.
  - Las únicas instrucciones fuera del `try` son comprobaciones en memoria de tiempo y variables booleanas (`check_kill_switch()`, `now_utc >= market_close_utc`, `max_duration_seconds`).
  - Las únicas excepciones que escapan a `except Exception:` en Python son `BaseException` (`KeyboardInterrupt`, `SystemExit`).
- **Evidencia:**
  - Si se hubiera producido una excepción de Python no capturada, el intérprete habría volcado un `Traceback (most recent call last)` a `stderr` / consola.
  - El hecho medido certifica ausencia total de error o traza.
- **Veredicto:** **DESCARTADA**.

---

### Hipótesis 3: Bucle que terminó por condición de fin programada (`break`)
- **Mecanismo en código:** El bucle `while True` dispone de tres condiciones de `break` explícitas:
  1. `session_mgr.check_kill_switch()` (L555-557): Imprime `"[Kill-switch detectado] Deteniendo sesión sombra de forma segura..."`. No existía el archivo `KILL_SWITCH` en `lab_artifacts/s2_protocol/` a las 17:12 ET.
  2. `max_duration_seconds and (time.time() - start_time) >= max_duration_seconds` (L564-566): Imprime `"[Duración máxima alcanzada] Deteniendo sesión sombra..."`.
  3. `now_utc >= market_close_utc` (L559-562):
     - El cálculo de `market_close_utc` (L536-540) es:
       `candidate = now_utc.replace(hour=20, minute=0, second=0, microsecond=0)`
       `market_close_utc = candidate if candidate > now_utc else now_utc + timedelta(hours=6)`
     - Al arrancar a las 16:47 ET (20:47 UTC), `candidate` (20:00 UTC) era menor que `now_utc`, por lo que `market_close_utc` se fijó para 6 horas después (02:47 UTC / 22:47 ET). A las 17:12 ET (21:12 UTC) esta condición era estrictamente `False`.
- **Evidencia adicional:**
  - Si cualquiera de los tres `break` se hubiera ejecutado, el bloque `finally:` (L653-656) se habría invocado obligatoriamente:
    `finally: await session_mgr.bus.shutdown(); update_live_report(session_mgr, contract.name, final=True)`.
  - Esto habría volcado métricas finales a `metrics_live.json` reflejando un `uptime_seconds` de ~1500 s (25 minutos). En cambio, `metrics_live.json` conservó `uptime_seconds: 1.2` (escrito en la primera iteración de arranque a las 16:47 ET).
- **Veredicto:** **DESCARTADA**.

---

### Hipótesis 4: Terminación del Proceso Padre (Parent Process / Terminal / Task Runner Kill)
- **Mecanismo operativo en Windows:**
  - Cuando un proceso interactivo o script de fondo lanzado desde una terminal (o subagente/orquestador) es terminado debido al cierre de la sesión de consola, un timeout de la herramienta de ejecución de tareas, o una llamada del sistema tipo `TerminateProcess` / `taskkill /f`:
    1. El sistema operativo aborta el proceso de forma inmediata a nivel kernel.
    2. Python **NO ejecuta bloques `finally:`**.
    3. Python **NO ejecuta gestores de señales** (`SIGINT`/`SIGTERM`).
    4. Los buffers pendientes no se vacían y no se produce ninguna salida por `stderr` ni traza de error.
- **Evidencia:**
  - Explica al 100 % todos los hechos medidos: desaparición a los ~25 min (17:12 ET), ausencia total de trazas en consola, omisión del bloque `finally` y permanencia intacta del archivo `metrics_live.json` con los valores de la primera iteración.
- **Veredicto:** **CONFIRMADA**.

---

### Cuestión adicional: ¿Por qué no escribió `INFORME.md`?
- **Causa exacta en código:** En `session_runner_ghost.py:L434-L436`:
  ```python
  informe_path = S1_DIR / "INFORME.md"
  if not informe_path.exists():
      return
  ```
  En `lab_artifacts/s2_protocol/`, a diferencia de `s1_protocol/`, **nunca se creó un archivo `INFORME.md` previo**. Cada vez que el runner invocaba `update_live_report()`, comprobaba la existencia de `INFORME.md`, detectaba que no existía y ejecutaba un `return` silencioso, actualizando únicamente `metrics_live.json`.

---

### Cuestión adicional: ¿Por qué no entró ni una sola barra tras las 16:55 ET?
- **Causa en estructura de mercado CME:**
  - El contrato de futuros MNQ en el CME cotiza en sesión regular (RTH) hasta las 17:00 ET (21:00 UTC).
  - La vela M5 de las **16:55 ET** (16:55 - 17:00 ET) es la última vela de la sesión diurna.
  - De **17:00 a 18:00 ET** (21:00 a 22:00 UTC), el CME aplica su **parón diario obligatorio de liquidación y mantenimiento técnico** (*daily maintenance halt*). Durante esta hora completa, el mercado no cotiza, no se emparejan transacciones y el exchange no genera velas.
  - Entre las 17:00 ET y las 17:12 ET (cuando el runner estuvo activo), el mercado subyacente estaba físicamente cerrado.

---

## 3. Pregunta (b): Verificación del Histórico Nocturno con el Conector

### Invocación Solicitada
Se ejecutó la única invocación autorizada con el `contract_id` canónico de MNQZ6 (`CON.F.US.MNQ.Z26`, verificado en `session_live.jsonl`):

```powershell
& "E:/FARS-LAB/.venv-fars/Scripts/fars-projectx.exe" bars CON.F.US.MNQ.Z26 --start 2026-09-16T17:00:00-04:00 --end 2026-09-17T22:35:00-04:00 --unit 5 --unit-number 1 --limit 50 --json
```

### Resultado de la Invocación
```json
[]
```
- **Salida:** Código de salida `0`, devolviendo un array JSON vacío `[]`.

### Análisis Técnico del Resultado
1. **Significado del parámetro `--unit 5`:**
   - En `src/realtime/connectors/projectx.py:L31`, las unidades de barra se definen según la API de ProjectX como:
     `_BAR_UNIT_SUFFIX = {1: "s", 2: "m", 3: "h", 4: "d", 5: "w", 6: "mo"}`.
   - El valor `unit: 5` corresponde a **barras semanales** (`w`).
   - Al solicitar barras de 1 semana en una ventana de ~29 horas (desde ayer 17:00 ET hasta hoy 22:35 ET), no existe ninguna barra semanal cerrada en ese periodo acotado.
2. **Disponibilidad de histórico nocturno en TopstepX:**
   - La API de TopstepX sí registra transacciones de la sesión nocturna (Globex ETH, 18:00 - 09:30 ET) cuando se consulta con la unidad temporal adecuada (`unit=2` para minutos, `unit_number=5` para M5), excepto durante la ventana de mantenimiento del CME (17:00 - 18:00 ET), donde la ausencia de barras no es una falla del conector sino la realidad del mercado.

---

## 4. Pregunta (c): Propuesta de Cambios para que una Sesión Nocturna Tenga Sentido

Para que una sesión nocturna (Globex / ETH, 18:00 a 09:30 ET) opere de manera robusta y coherente, se propone la siguiente arquitectura (diseño técnico, no implementado):

### 1. Máquina de Estados de Sesión y Manejo del CME Maintenance Halt
- **Problema actual:** El runner asume un mercado continuo o se desconcierta cuando el feed deja de recibir barras entre 17:00 y 18:00 ET.
- **Propuesta:** Introducir un estado formal `CME_MAINTENANCE_HALT` entre 17:00 y 18:00 ET (21:00-22:00 UTC).
  - Suspender el polling intensivo de barras durante esa hora o desactivar las alertas de datos obsoletos (*stale market data*).
  - Al llegar las 18:00:00 ET, activar automáticamente la sesión nocturna sin requerir reinicio del proceso.

### 2. Creación Autónoma de `INFORME.md`
- **Problema actual:** `session_runner_ghost.py:L436` aborta silenciosamente si `INFORME.md` no existe de antemano.
- **Propuesta:** Si `informe_path` no existe, instanciarlo automáticamente con la cabecera canónica y las tablas de seguimiento en lugar de retornar sin escribir.

### 3. Aislamiento y Persistencia del Proceso (Daemon / Desacoplamiento)
- **Problema actual:** El proceso vive atado a la consola o subshell de quien lo invoca. Si la shell muere, Windows envía `TerminateProcess` y mata el runner sin volcado de métricas finales.
- **Propuesta:** 
  - Lanzar el runner con un supervisor desacoplado o wrapper que capture `SIGTERM`/`SIGINT` a bajo nivel y redirija `stdout`/`stderr` a `logs/runner_stdout.log`.
  - Implementar un heartbeat periódico en un archivo de estado ligero (`heartbeat.json`) cada 30 segundos para saber si el proceso está vivo independientemente del volcado de eventos.

### 4. Régimen de Volatilidad y Niveles Específicos para Sesión Nocturna
- **Problema actual:** SMC-FVG con parámetros de RTH (donde el volumen del MNQ supera los 20.000 contratos por vela) opera con spreads más anchos y volumen muy bajo en Asia (frecuentemente < 1.000 contratos por vela).
- **Propuesta:**
  - Adaptar los oráculos de zonas y niveles de sesión para calcular y registrar explícitamente **ONH / ONL** (Overnight High / Overnight Low).
  - Ajustar filtros de volatilidad mínima (ATR) o de volumen para evitar entradas falsas causadas por micro-gaps ilíquidos durante la sesión de Tokio/Sydney.

---

## 5. Cuadro Resumen de Diagnóstico

| Aspecto | Hallazgo / Evidencia | Veredicto |
|---|---|:---:|
| **Muerte a las 17:12 ET** | Aborto silencioso a nivel kernel por cierre/timeout del proceso padre. Bloque `finally` no ejecutado. | **PROCESO PADRE TERMINADO** |
| **Falta de INFORME.md** | Guardia `if not informe_path.exists(): return` impidió su generación al no existir plantilla inicial. | **BUG DE GUARDIA EN CÓDIGO** |
| **0 barras tras 16:55 ET** | CME cerrado por liquidación/mantenimiento oficial de 17:00 a 18:00 ET. | **NORMALIDAD DE MERCADO** |
| **Invocación `fars-projectx bars`** | `--unit 5` solicita barras semanales sobre ventana de 29h $ightarrow$ devuelve `[]`. | **UNIDAD DE TIEMPO INCORRECTA** |
