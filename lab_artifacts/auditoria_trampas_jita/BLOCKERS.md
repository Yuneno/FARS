# Bloqueadores — Auditoría Anti-Trampas (FARS vs jita-bot)

Las siguientes cuestiones no pueden determinarse únicamente mediante la lectura estática del código fuente y requieren medición empírica o confirmación externa de especificaciones con proveedores/brokers.

---

### Bloqueador 1: Impacto cuantitativo de la trampa "TP en la vela del fill" en los libros de FARS

* **Contexto:** La lectura de `src/backtest/executor.py:441-465` (legacy) y `src/backtest/executor.py:908-965` (enhanced) demuestra que el ejecutor evalúa el Take Profit en los extremos (`high`/`low`) de la **misma vela M5 en que se produce el fill**, tanto para órdenes a mercado como para órdenes límite (`pending_limit_entry: True`). En órdenes límite en retroceso (SMC-OB, SMC-FVG, CRT 4H), el máximo/mínimo previo al fill dentro de esa misma vela se computa erróneamente como TP alcanzado (el mismo artefacto que infló jita-bot del 49% al 65-74%). Además, la resolución intrabar M1 (`intrabar.py:42-81`) solo se invoca si se tocan simultáneamente SL y TP (`if hit_target and hit_stop`), por lo que si en la vela del fill solo se tocó el TP, M1 jamás se consulta y el win se valida directamente en M5.
* **Pregunta exacta:**
  > *¿Qué porcentaje exacto de los trades ganadores históricos de SMC-OB, SMC-FVG y CRT 4H en los backtests de FARS alcanzaron su Take Profit en la misma vela M5 del fill, y cuánto cae el Win Rate, el Profit Factor y la Esperanza Matemática E[R] si se aplica la regla limpia de no contar TP en la vela del fill (o exigir que sea en velas posteriores / con cierre confirmado)?*

---

### Bloqueador 2: Especificación y ciclo de vida de órdenes límite en el gateway real ProjectX / TopstepX

* **Contexto:** En el camino de tiempo real actual (`src/realtime/interfaces.py:22`), `LIVE_EXECUTION_ENABLED = False`. El adaptador paper (`src/realtime/paper.py:73, 108-136`) genera fills inmediatos sin precio, sin cantidad y sin modelar colas de órdenes (`"unmodeled: OrderIntent has no price"`). El cliente de ProjectX prohíbe taxativamente métodos de ordenación viva (`src/realtime/connectors/projectx.py:33-43`). Por contra, el backtest enhanced modela órdenes límite descansando con caducidad fija de 36 a 48 barras M5 (3 a 4 horas) mediante `pending_order_wait_bars` (`src/backtest/executor.py:879-882`).
* **Pregunta exacta:**
  > *Dado que no existe implementación de colocación o cancelación de órdenes límite en vivo en FARS, ¿cuál es el protocolo técnico que utilizará FARS al conectar con ProjectX/TopstepX para órdenes límite (tipo de vigencia GTC vs DAY, si el broker admite expiración en minutos o si FARS debe enviar cancelaciones explícitas por temporizador, y qué slippage o tasa de rechazo ocurre en órdenes límite en horario de alta volatilidad)?*

---

### Bloqueador 3: Hora contractual exacta y modelado de flatten diario en cuentas Apex y TopstepX

* **Contexto:** En la sesión sombra viva de S1 (`lab_artifacts/s1_protocol/session_runner.py:533-557`), la ejecución se desconecta a las 16:00 ET (20:00 UTC). Sin embargo, el motor de backtest (`src/backtest/executor.py:354-1053`) carece de una regla de cierre forzado diario (flatten EOD): solo tiene `max_hold_minutes` (60m en AMD+CRT) o `max_bars_held` (192 barras en ORB/CRT-TBS, y 1.000.000.000 barras en SMC-FVG y SMC-OB). En SMC-FVG y SMC-OB, las posiciones pernoctan abiertas durante días o fines de semana en el backtest. En una cuenta financiada real de Apex o TopstepX, no estar flat antes del cierre de sesión constituye rescisión inmediata de la cuenta.
* **Pregunta exacta:**
  > *¿A qué hora exacta (16:00, 16:15 o 16:59 ET) exige contractualmente cada firma (Apex y TopstepX) tener liquidadas todas las posiciones intradía, y cuál es el impacto en métricas de incorporar un flatten obligatorio a esa hora en el bucle diario del backtest (liquidando a mercado con slippage adverso `time_exit_slippage_points`) para SMC-FVG y SMC-OB?*

---

### Bloqueador 4: Discrepancia del coste en el escenario "Kai" entre protocolos

* **Contexto:** En el protocolo C2 (`lab_artifacts/run_c2_walkforward.py:37`), el escenario de sensibilidad `"kai"` está configurado con `commission_per_side = 0.71` (\$1.42 RT) y `slippage_points = 0.25`. Sin embargo, en el motor de cuentas de Apex (`src/account_engine.py:48`) y en la auditoría de procedencia (`docs/refactor/apex-profile.md:49`), se documenta explícitamente que el código original de Kai (`kai_bt/core/contracts.py:59-64`) utiliza `$1.00 RT` (\$0.50 por lado).
* **Pregunta exacta:**
  > *¿Cuál es el fundamento de la discrepancia entre la comisión de \$1.42 RT (\$0.71/lado) usada en el runner de C2 y el \$1.00 RT (\$0.50/lado) del código fuente original de Kai referenciado en el motor de cuentas, y cuál de las dos cifras debe tomarse como baseline canónico para pruebas de paridad?*
