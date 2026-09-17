# BLOCKERS — Bloque S1: Sesión DEMO Sombra Live

Registro formal de limitaciones técnicas, decisiones declaradas y requisitos pendientes para habilitar ejecución de órdenes en demo (RT-9).

---

## 1. Bloqueos Técnicos para Enviar Órdenes en Demo (Alcance RT-9)

En esta sesión (Bloque S1), la arquitectura opera en **modo sombra estricto** (`LIVE_EXECUTION_ENABLED = False`). Para que FARS pueda enviar órdenes activas a una cuenta demo en el futuro, se requieren los siguientes desarrollos estructurales fuera de este bloque:

1. **Desarrollo de Adaptador de Ejecución Live en ProjectX:**
   - Actualmente, el conector `src/realtime/connectors/projectx.py` es puramente de lectura (`_READ_ONLY_PATHS` cerrado a endpoints de consulta).
   - Los métodos de orden (`place_order`, `cancel_order`, `modify_order`, etc.) están explícitamente bloqueados en el diseño del gateway.
   - Se debe construir un adaptador de ejecución certificado (`ProjectXExecutionAdapter`) que implemente la interfaz `ExecutionAdapter` bajo protocolo RT-9.

2. **Gestión de Órdenes Bracket y Protección Ex-Ante:**
   - FARS exige que toda orden a mercado o límite lleve stop loss y target de beneficio acoplados (brackets OCO). La API de TopstepX/ProjectX debe validarse contra los tipos de orden admitidos por el motor de casación (CME Globex).

3. **Reconciliación de Posiciones en Tiempo Real:**
   - Manejo de desconexión con órdenes huérfanas en vuelo. El motor de riesgo debe contar con un reconciliador que verifique el estado del broker (`AccountSnapshot.working_orders`) frente al estado interno del bus antes de permitir cualquier nueva intención.

4. **Estado de la Cuenta Simulada Actual:**
   - La cuenta simulada seleccionada (`id: 27240143`) presenta un balance de `-$501.3 USD`.
   - Al aplicar las reglas de riesgo fondeado (`FundedAccountRules`), el motor de riesgo fail-closed `AccountAwareRiskEngine` detecta violación del colchón de drawdown y **veta cualquier orden** (`DRAWDOWN_BUFFER_TOO_LOW`).
   - Para ejecutar órdenes efectivas en demo bajo RT-9, se requerirá una cuenta de pruebas con balance positivo o reglas de riesgo ajustadas al tamaño del remanente.

---

## 2. Decisiones Declaradas en el Bloque S1

1. **Conexión Read-Only Inviolable:**
   - Se garantiza que durante la tarde ninguna instrucción enviará paquetes de modificación de órdenes al endpoint del broker.
   - La ejecución se simula localmente mediante `PaperExecutionAdapter`, garantizando el registro de métricas sin riesgo operativo.

2. **Alimentación de Datos por Polling de Barras M5:**
   - Se utiliza el endpoint oficial `/api/History/retrieveBars` consultando barras de 5 minutos cerradas del contrato activo de MNQ (`CON.F.US.MNQ.Z26`).
   - Esto evita la complejidad e inestabilidad de sockets SignalR cuando solo se evalúan decisiones de cierre de vela M5.

3. **Política de Manejo de Errores Fail-Closed:**
   - Si la API de ProjectX devuelve timeout, error HTTP 429, o desconexión, el runner emite inmediatamente un evento `SystemEvent(kind=SYSTEM_CONNECTOR_DISCONNECTED)`.
   - El motor de riesgo pasa a estado `_disconnected = True` y deniega automáticamente el 100% de las intenciones generadas hasta el restablecimiento verificado de la conexión.

4. **Mecanismo de Kill-Switch:**
   - Se dispuso el archivo centinela `lab_artifacts/s1_protocol/KILL_SWITCH` para detener la sesión de forma inmediata y ordenada en cualquier momento sin esperar al cierre de mercado.
