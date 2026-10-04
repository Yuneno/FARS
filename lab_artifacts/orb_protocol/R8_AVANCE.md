# R8 AVANCE — ORB: Semántica Terminal MC Fondeo + Columna Pareada con Riesgo FARS

- **Tarea**: Cierre exhaustivo de los 2 CRITICAL y 4 WARNING + 1 SUGGESTION del review R7 de Codex sobre el protocolo ORB (semántica terminal Apex en Monte Carlo de fondeo con 4 estados mutuamente excluyentes, columna pareada con capa de riesgo FARS sobre el camino bruto, declaración formal de aproximación por cierres, normalización tz en `_ct_iso` con test UTC, sincronización documental de `INFORME_ORB.md` con la corrida reproducible y reporte de cupo con regla de tres).
- **Cambios hechos**:
  1. `CRITICAL 1` (Columna pareada con capa de riesgo FARS sobre el camino bruto):
     - En `lab_artifacts/orb_protocol/bootstrap_camino.py`: se eliminó la selección endógena (remuestreo sobre los 379 trades aceptados de una sola trayectoria histórica para evaluar fondeo). Se implementó `run_unwrapped_mbb_paired`: cada réplica remuestrea siempre el camino BRUTO (2.444 oportunidades) y corre en paralelo sobre el MISMO remuestreo y timeline cronológico:
       (i) Sin capa de riesgo: reglas de cuenta estándar (sizing entero dependiente de equity, límite de pérdida diaria $2.000, trailing DD $8.000, target +$6.000) con parada terminal al primer breach o target.
       (ii) Con capa de riesgo FARS: las mismas reglas pero cada oportunidad pasa primero por `AccountAwareRiskEngine` (mismo motor y reglas que `separar_senales.py`); un veto de riesgo DESCARTA esa operación (`trades_vetoed += 1`) y la réplica continúa; una violación terminal detiene la réplica.
     - En `run_full_bootstrap`: ahora calcula y expone las probabilidades de límite para `gross` y `allowed` mediante esta evaluación pareada sobre el camino bruto.
     - *Test antes y después:* Antes, `test_camino_doble_bruto_y_permitido` evaluaba probabilidades donde la columna permitida remuestreaba solo 379 trades con horizontes diferentes. Ahora, se evalúan sobre el mismo remuestreo bruto, y el test afirma la presencia de las 4 categorías terminales disjuntas (`p_target`, `p_breach_trailing`, `p_breach_daily`, `p_horizon_exhausted`), sumando 1,0 exactamente en ambas columnas y verificando `p_breach_total == p_breach_trailing + p_breach_daily`. Falla si falta alguna categoría o si hay solapamiento; pasa tras el fix.
  2. `CRITICAL 2` (Semántica terminal mutuamente excluyente: parada dura en daily loss tipo Apex):
     - En `lab_artifacts/orb_protocol/bootstrap_camino.py`: se estableció `stop_on_daily_loss = True` por defecto en `simulate_funded_trajectory`. Cada réplica termina en EXACTAMENTE UNO de 4 estados mutuamente excluyentes: `"target"`, `"breach_trailing"`, `"breach_daily"`, `"horizon_exhausted"`. Al cruzar la pérdida diaria acumulada del día (`day_pnl <= -daily_loss_limit_usd`), la réplica se detiene de inmediato como `"breach_daily"`, garantizando que `P(breach total) = P(breach_trailing) + P(breach_daily)` (disjuntos) y `P(target)` es estrictamente "target antes del primer breach".
     - En `lab_artifacts/orb_protocol/test_investigacion.py`:
       - Se corrigió `test_b3_probabilidades_responden_a_datos`: se separó el caso de pérdidas severas intradía (que activan `hit_daily=1.0` y `hit_dd=0.0` con `terminal_condition="breach_daily"`) del caso de pérdidas moderadas multi-día sin violación diaria (que activan `hit_dd=1.0` y `hit_daily=0.0` con `terminal_condition="breach_trailing"`). Se verifica la exclusión mutua estricta en cada réplica. (Antes fallaba con `stop_on_daily_loss=True` porque el test antiguo exigía erróneamente `hit_daily=1.0` y `hit_dd=1.0` simultáneamente).
       - Se corrigió `test_mc_fondeo_dos_replicas_juguete_calculables_a_mano`: Réplica 1 alcanza target y para; Réplica 2 se detiene en Trade 3 por pérdida diaria terminal (`hit_daily=True, hit_dd=False, trades_executed=3, sizes=[5, 4, 4]`); se añadieron Réplica 3 (trailing DD terminal sin daily breach), Réplica 4 (horizonte agotado sin breach ni target) y Réplica 5 (veto de la capa de riesgo FARS descartando la operación y continuando la réplica). Se verifica que la suma de estados terminales es exactamente 1 en cada réplica. (Antes el test consolidaba el error aceptando `hit_daily` y `hit_dd` en la misma réplica; ahora falla ante cualquier solapamiento y pasa con la semántica Apex estricta).
  3. `WARNING 1` (Aproximación por cierres declarada):
     - En `lab_artifacts/orb_protocol/bootstrap_camino.py` (CLI `main()` líneas 814-815), `INFORME_ORB.md` (secciones 6 y 7) y este reporte: se declaró formalmente: "Aproximación por cierres: trailing sobre equity a cierre de operación; excursiones intratrade no realizadas no elevan el piso".
  4. `WARNING 2` (Sincronización de `INFORME_ORB.md` con el artefacto canónico `paper_run.json`):
     - En `lab_artifacts/orb_protocol/INFORME_ORB.md:92`: se corrigió la mención desincronizada a 103 señales; ahora describe fielmente la corrida canónica: de 42 señales generadas, 42 aceptadas y ejecutadas hasta la parada por breach terminal de trailing drawdown.
     - En `lab_artifacts/orb_protocol/INFORME_ORB.md:95`: se aclaró que la semántica de fin de datos (`UNRESOLVED_SIGNAL`, `replay_end`) está probada en test unitario (`test_j_unresolved_signal_at_replay_end`), pero no ocurre en `paper_run.json` debido a la terminación anticipada por breach en la operación 42.
  5. `WARNING 3` (Sincronización de estadísticas de `INFORME_ORB.md` con la ejecución reproducible):
     - En `lab_artifacts/orb_protocol/INFORME_ORB.md:101-102`: se actualizaron las estadísticas obsoletas (`unsupported_or_inconclusive`, Ljung-Box 30,47, DD [18,48; 32,80]) a los valores reproducibles actuales: `dependent_resampling_candidate`, Ljung-Box principal (stat=5,1782, p=0,02287) y DD MBB [19,01; 59,50] R.
  6. `WARNING 4` (Normalización de zonas en `run_orb._ct_iso` con test UTC):
     - En `lab_artifacts/orb_protocol/run_orb.py` (`_ct_iso`): si el timestamp de entrada ya es tz-aware de otra zona horaria, se aplica `ts.tz_convert("America/Chicago")` en lugar de devolverlo sin normalizar; si es naive, se localiza con `tz_localize` determinista (`ambiguous=False`).
     - En `lab_artifacts/orb_protocol/test_investigacion.py` (`test_ledger_export_exit_time_real`): se añadió un 5to trade con timestamps tz-aware en UTC (`2026-07-08 14:00:00+00:00` entrada, `15:30:00+00:00` salida) y se verificó que el CSV exportado normaliza a `-05:00` (`09:00:00-05:00` y `10:30:00-05:00`). (Antes del fix de `run_orb.py`, un timestamp UTC permanecía con offset `+00:00` violando el contrato `America/Chicago`).
  7. `SUGGESTION 1` (Cota superior de regla de tres para cupo mensual):
     - En `lab_artifacts/orb_protocol/bootstrap_camino.py` (CLI `main()` líneas 825-827): si `p_max_ops == 0`, se reporta como `0/2000 (<3/2000)` con cota superior derivada de la regla de tres de Poisson, en lugar de 0,00% exacto.
- **Hallazgos**:
  - `INFO`: Con la parada terminal al primer breach (Apex stop_on_daily_loss = True), desaparece por completo la incoherencia estadística donde 376/2.000 réplicas figuraban simultáneamente como breach y profit target alcanzado.
  - `INFO`: Con los límites configurados, ambas columnas del MC son NECESARIAMENTE idénticas por construcción: la parada terminal al primer breach corta la réplica antes de que llegue cualquier señal posterior al engine, así que los vetos solo pueden darse en señales anteriores al primer breach y en la práctica no se dan. Cero vetos es ESTRUCTURAL a cualquier horizonte (no es un efecto de las ~35 ops/réplica) y la comparación (i)/(ii) es redundante con estos límites. Los 2.064 vetos históricos de `separar_senales.py` no lo contradicen: esa simulación sigue evaluando señales después del primer breach.
  - `INFO`: La aproximación por cierres de trade es una simplificación **optimista para supervivencia/fondeo** respecto a una evaluación tick a tick: ignora excursiones intratrade favorables que elevarían el piso trailing antes del cierre del trade y sumarían breaches adicionales (subestima la probabilidad de breach).
- **No revisado**:
  - Conexión a broker live o daemon en tiempo real (fuera del alcance del protocolo de investigación en sandbox).
  - Códigos fuera de `lab_artifacts/orb_protocol/` y `src/` (estrictamente preservados sin modificaciones).
- **Confianza**: Alta. La suite completa de 26 pruebas ejecuta y pasa al 100% en verde (26 passed en 94.39s) con el comando reglamentario de PowerShell.
- **Comandos para Hermes**:
  - Verificación de la suite completa de pruebas:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" -B -m pytest lab_artifacts/orb_protocol -m "not statistical or statistical" -q
    ```
  - Ejecución del Bootstrap de Camino y Monte Carlo de Fondeo pareado:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/bootstrap_camino.py
    ```
  - Ejecución de la separación de señales S-A-R:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/separar_senales.py
    ```
  - Ejecución del paper bot sobre el replay:
    ```powershell
    & "E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/orb_protocol/paper_bot.py --replay-mes 3
    ```
