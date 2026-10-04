# R6 AVANCE — ORB: Breach de Cuenta + Monte Carlo de Fondeo Correcto

- **Tarea**: Cierre exhaustivo de los 3 CRITICAL y 4 WARNING pendientes del review R5 de Codex sobre el protocolo ORB (declaración de breach de trailing drawdown en `paper_bot.py`, proceso de interarribos monótono para cupo mensual en `bootstrap_camino.py`, Monte Carlo de fondeo en réplica con stopping conditions, corrección del encabezado CLI de bootstrap, aserción de step-down 5→4 contratos, semántica de fin de datos y sincronización de `INFORME_ORB.md`).
- **Cambios hechos**:
  1. `lab_artifacts/orb_protocol/paper_bot.py`:
     - Implementada función de control de riesgo `check_drawdown_breach(equity, peak_equity, rules)` integrada de forma obligatoria tras cada actualización de balance (salida intrabarra en barra t+1, salida regular de posición preexistente y liquidación por fin de sesión/replay).
     - Al cruzar el piso trailing (`equity <= floor`): se marca `terminal_condition="max_drawdown"`, `halted=True`, se emite `guard.halt(...)`, se registra el evento `BREACH` en el journal de auditoría y se termina el loop de ejecución inmediatamente.
     - Condicionada la exportación de `paper_run.json`: únicamente se sobrescribe el archivo canónico si `df is None` (ejecución real de replay), protegiendo el artefacto frente a corridas de test con DataFrames sintéticos reducidos.
     - Resuelta la semántica de fin de datos para señales generadas en la última barra (`paper_bot.py:470,477`): se emite evento formal `UNRESOLVED_SIGNAL` con motivo `replay_end` y estado `unresolved`.
  2. `lab_artifacts/orb_protocol/test_paper_bot_periods.py`:
     - Añadido `test_i_terminal_trailing_drawdown_breach_halts_replay`: fuerza una salida que cruza el piso trailing y comprueba que se declare `halted=True`, `terminal_condition="max_drawdown"`, evento `BREACH` en el journal y su persistencia en el JSON. (Falla antes del fix con `halted=False`, pasa después).
     - Añadido `test_j_unresolved_signal_at_replay_end`: comprueba la resolución explícita de señales pendientes al agotar las barras del replay. (Falla antes del fix, pasa después).
  3. `lab_artifacts/orb_protocol/bootstrap_camino.py`:
     - **Fix CRITICAL 2 (Cupo mensual calendario sintético):** Implementadas funciones `_prepare_interarrivals` y `build_replicate_timeline`. En lugar de remuestrear fechas históricas y acumular apariciones repetidas del mismo string `YYYY-MM` entre bloques inconexos, se construye un cronograma sintético que avanza estrictamente hacia adelante en el tiempo. Dentro de bloques contiguos preserva la separación empírica; en las costuras entre bloques muestrea interarribos empíricos. Ningún mes calendario pasado se vuelve a visitar.
     - **Fix CRITICAL 3 (Monte Carlo de Fondeo en cada réplica):** Implementada `simulate_funded_trajectory`. Cada réplica remuestrea el camino bruto y re-ejecuta de forma endógena:
       a) Sizing entero: `risk_usd = min(equity * 1%, $1.000)`, `size = int(risk_usd / 200)`.
       b) Cupo mensual: máximo 42 operaciones por mes calendario sintético (las siguientes se descartan / no se toman).
       c) Límite de pérdida diaria: $2.000 USD (bloquea nuevas entradas por el resto de esa jornada).
       d) Trailing drawdown: piso a $8.000 USD del pico de equity. Al cruzarlo, se declara breach terminal (`max_drawdown`) y la réplica se DETIENE.
       e) Profit target: al alcanzar +$6.000 USD ($106.000 USD), la cuenta aprueba (`profit_target`) y la réplica se DETIENE.
     - **Fix WARNING 1 (Clasificación de bootstrap en reporte CLI):** En `main()`, `method_title` se deriva dinámicamente mediante `classify_bootstrap_method(state)`, evitando que estados como `unsupported_or_inconclusive` se titulen erróneamente como `CIRCULAR BLOCK BOOTSTRAP`.
     - Actualizada la sección 4 de CLI para reportar formalmente las probabilidades y métricas del Monte Carlo de Fondeo con sus supuestos explícitamente declarados.
  4. `lab_artifacts/orb_protocol/test_investigacion.py`:
     - **Fix WARNING 3 (Aserción de step-down):** En `test_b3_probabilidades_responden_a_datos`, se añadió la aserción explícita de reducción de contratos `assert acts_sz[0]["sizes"] == [5, 4]`.
     - **Fix CRITICAL 3 (Test de juguete calculable a mano):** Añadido `test_mc_fondeo_dos_replicas_juguete_calculables_a_mano` con dos réplicas deterministas:
       - Réplica 1: 3 trades ganadores (+2R cada uno) alcanzan exactamente el Profit Target de $106.000 USD (3 trades ejecutados con tamaño 5 contratos, trade 4 perdedor posterior no ejecutado tras la parada).
       - Réplica 2: 3 trades perdedores en el día 1 activan `hit_daily=True` tras caer -$2.600 USD (con step-down verificado 5→4 contratos); 2 trades perdedores en los días siguientes cruzan el piso de trailing drawdown a $89.400 USD activando `hit_dd=True` con condición terminal `max_drawdown` (trade 6 ganador posterior no ejecutado tras el breach).
  5. `lab_artifacts/orb_protocol/INFORME_ORB.md`:
     - Sincronizadas las secciones 6, 7 y 8 con los artefactos del 2026-10-04: replay de 3 meses con 42 aceptados y breach terminal de trailing drawdown, separación S-A-R (379 aceptadas, 2.065 denegadas por buffer de drawdown), y el Monte Carlo de fondeo completado sobre el camino bruto.
- **Hallazgos**:
  - `INFO`: La comisión y slippage modelados ($2,74 USD/contrato round-turn) descuentan $13,70 en una posición estándar de 5 contratos MNQ, llevando el equity tras una pérdida de 1R de $100.000 a $98.986,30 USD.
  - `INFO`: Bajo la arquitectura de fondeo Apex ($100k capital, trailing DD $8.000 USD sobre pico no realizado), el replay real confirma que arriesgar 1% por orden (5 contratos MNQ) expone la cuenta a breach terminal tras rachas de pérdidas normales, aconsejando bajar el riesgo operativo a 0,25%–0,50% (1–2 contratos).
- **No revisado**:
  - Conexión a brokers en vivo o daemon de tiempo real (fuera del alcance del protocolo de investigación).
  - Archivos fuera de `lab_artifacts/orb_protocol/` y `src/` (estrictamente no tocados).
- **Confianza**: Alta. Los 26 tests unitarios y de integración pasan limpiamente (26 passed en 89s).
- **Comandos para Hermes**:
  - Ejecución de la suite completa de pruebas:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -B -m pytest lab_artifacts/orb_protocol -m "not statistical or statistical" -q
    ```
  - Regeneración del replay canónico de producción (actualiza `paper_run.json` con breach terminal declarado):
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/paper_bot.py --replay-mes 3
    ```
  - Ejecución del Bootstrap de Camino y Monte Carlo de Fondeo:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/bootstrap_camino.py
    ```
  - Regeneración de la separación de señales S-A-R:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/separar_senales.py
    ```
