# Preguntas exactas y límites de esta corrida

No hay bloqueo para ejecutar el encargo con `strat_smc.DEFAULTS`. No se escribe `.git`, no se intenta commit, push ni rebase. La aceptación independiente corresponde a Hermes.

1. ¿La próxima validación debe usar `strat_smc.DEFAULTS` (RR=3, CHoCH=True) o el override `live_smc_bridge.FROZEN` (RR=2)? Esta corrida usa exclusivamente los defaults, como indica el encargo; no mezcla ni compara configuraciones después de ver OOS.
2. ¿Cuál es la política original de selección de contratos, rollover y ajuste de precios de `MYM_M5.csv` y `MGC_M5.csv` y del miembro MNQ del ZIP? Se usan las fuentes y loaders C1/E7 existentes sin transformaciones adicionales y no se promueve un dataset canónico nuevo.
3. ¿El pool «MNQ+mercado_aprobado» pretende MNQ SMC-OB o la campeona MNQ SMC-FVG de E1? Se preregistra MNQ SMC-OB, la estrategia objeto de este encargo; no se inventa una selección de baseline FVG.
4. ¿Se autoriza en un encargo separado validar una simulación de cuenta por bloques/días conjuntos que conserve dependencia temporal y correlación entre mercados? El motor solicitado remuestrea trades IID y serializa sus tiempos; su puntuación no prueba que los trades sean IID ni conserva simultaneidad del pool. No se modifica ese motor aquí.
5. ¿Debe ampliarse en otro encargo `build_maes` de E7 a todas las fechas de trades overnight? Ese helper usa barras del día de entrada; si se activa E1, la cobertura overnight requiere advertencia expresa y no debe presentarse como MAE completo. El helper existente se reutiliza sin editarlo.

La ruta original del bridge no existe; se encontró y leyó `C:/Users/yo/Documents/GitHub/kai-backtesting/src/kai_bt/live/live_smc_bridge.py`.

Pytest no puede escribir la caché preexistente `.pytest_cache`; las comprobaciones usan `-p no:cacheprovider`. `git status` tampoco puede inspeccionar dos directorios temporales preexistentes de pytest; no se eliminan ni alteran permisos.

La primera suite completa dio 1.290 passed, 2 skipped y 212 errores de setup por `PermissionError` en `pytest-of-yo`. Un intento de basetemp nuevo también confirmó el problema de ACLs de mkdir(mode=0700) de Python 3.13. `run_tests.py` usa exclusivamente un nuevo árbol en Temp con ACL heredada; no cambia permisos de ningún directorio existente. El directorio de diagnóstico `pytest-temp-check` se conserva por la prohibición de borrar archivos. La corrida completa con ACL heredada se interrumpió al entrar en pruebas `statistical` lentas; se ejecuta la regresión sin ese marcador para respetar el encargo de no correr durante horas.

6. ¿Se desea un encargo separado para completar la suite de aceptación estadística lenta (`-m statistical`)? No se alteran ni rebajan sus umbrales en este port.

La limitación overnight del helper M5 no afecta esta puntuación: solo MNQ supera gates y los 1.755 MAEs usados son M1 causales. MGC tiene dos posiciones abiertas al fin de fold excluidas de métricas, explícitamente registradas. No se puntúan cuentas ni pools con mercados que fallaron.

Durante la regresión aparecieron cambios concurrentes ajenos en `src/backtest/smc_fvg.py`, `src/detectors/sweeps.py`, nuevos detectores/tests y `lab_artifacts/f_protocol` / `f2_protocol`. Al inicio git status no tenía cambios; estas rutas no fueron escritas ni revertidas por este encargo. Los hashes de las fuentes congeladas SMC-OB se vuelven a verificar. La suite general observa un árbol compartido mutable.

7. ¿Puede Hermes revisar SMC-OB contra el commit congelado `0792dab9e9d5880d1eaac8e632742d8857ad8af2`, aislando los cambios F/F2 concurrentes antes de aceptar el conjunto? No se hace commit ni se intenta separar por operaciones git destructivas.

Incidente de validación: la primera regresión con temporales accesibles terminó con 1.493 passed, 2 skipped, 10 deselected y un fallo en `test_installed_fars_binary_runs_outside_checkout`. Ese test existente invocó internamente `pip install --prefix <Temp>`; el intento falló. Debió excluirse antes por la prohibición expresa del encargo. No se reintenta instalación; `run_tests.py` lo deselecciona siempre a partir de ahora. Se conserva el log completo, sin ocultar el incidente.

Regresión final: 1.499 passed, 2 skipped, 11 deselected en 24.93s. Los seis tests adicionales respecto al intento previo aparecieron por el trabajo concurrente. La suite original completa no se declara verde: se excluyeron 10 tests statistical y el test de instalación. Los 14 tests del port pasan sin el adaptador de temporales.

HEAD también cambió concurrentemente: inicio `0792dab9e9d5880d1eaac8e632742d8857ad8af2`; observado al finalizar `1389484a90de1fa21fe30a798c73bf05ce688f38`. Este encargo no invocó ninguna operación de commit/push/rebase. Los hashes de las fuentes congeladas siguen coincidiendo; no se reescribe el preregistro para ocultar el cambio.
