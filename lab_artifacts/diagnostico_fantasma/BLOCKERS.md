# BLOCKERS — Diagnóstico del Runner Fantasma y Sesión Nocturna

## 1. Bloqueadores Técnicos Identificados

### B1. Falta de Daemonización / Supervisión de Proceso
- **Descripción:** El runner `session_runner_ghost.py` se ejecuta acoplado a la sesión de consola/subshell interactiva. Cuando el proceso padre (agente/terminal) expira o se cierra, el sistema operativo aborta el proceso vía `TerminateProcess`, impidiendo la ejecución de bloques `finally:`, signal handlers o el vaciado de buffers de métricas.
- **Acción requerida antes de reanudar:** Implementar un supervisor desacoplado o wrapper de proceso que aísle el ciclo de vida del runner y escriba un `heartbeat.json` periódico.

### B2. Bug de Guardia en Generación de Informes (`INFORME.md`)
- **Descripción:** `session_runner_ghost.py:L436` aborta silenciosamente con un `return` si `INFORME.md` no preexiste en el directorio de trabajo. Dado que `s2_protocol/` no contenía un `INFORME.md` inicial, el script nunca documentó el progreso de la sesión en markdown.
- **Acción requerida antes de reanudar:** Modificar la lógica para que instancie automáticamente un informe inicial si el archivo no existe.

### B3. Ausencia de Manejo del Halt de Mantenimiento de CME (17:00 - 18:00 ET)
- **Descripción:** El runner no distingue entre una desconexión anómala del broker y el cierre diario programado de liquidación y mantenimiento de CME Futures (17:00 - 18:00 ET). Esto induce falsas alarmas de congelamiento de datos o desconexión.
- **Acción requerida antes de reanudar:** Incorporar una máquina de estados de calendario de sesión (`CME_MAINTENANCE_HALT`) que suspenda el polling y las alertas de datos obsoletos durante dicha ventana horaria.

### B4. Discrepancia de Parámetros en Consulta de Barras (`--unit` vs `--unit-number`)
- **Descripción:** La API de ProjectX utiliza códigos numéricos donde `unit: 5` equivale a semanas (`w`) y `unit: 2` a minutos (`m`). Al solicitar barras M5 por CLI, pasar `--unit 5 --unit-number 1` consulta barras semanales y devuelve `[]`. La consulta de barras M5 debe invocar `--unit 2 --unit-number 5`.
