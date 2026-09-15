# Bloqueos de C2

## Escritura de Git

- Bloqueo: la rama `bloque-c2-busqueda` ya existe y parte de `8134f29`, pero el entorno permite leer `.git` y no escribirlo. El intento de `git add` y el commit local requerido fallan con `Unable to create '.git/index.lock': Permission denied`.
- Pregunta exacta: ¿puede habilitarse escritura en `E:/FARS-LAB/FARS/.git` para indexar estos cambios y realizar el commit local obligatorio?
- Mitigación: continuar únicamente con cambios del árbol de trabajo; no modificar `.git` por otra vía y no ejecutar backtests antes de congelar el preregistro.

## Especificación CRT 4H (resuelto)

- La autorización del 2026-09-14 permitió leer `strat_crt4h.py` y `live_crt4h_bridge.py` del repo de Kai. La especificación exacta se reimplementó sin copiar ni importar código y el bloqueo anterior queda cerrado.

## Cardinalidad de configuraciones

- Bloqueo: las tablas obligatorias registran 3 baselines y 5 candidatas (EMAS 2, SMC-FVG 2, CRT 4H 1), pero el orden de trabajo pide correr “6 configuraciones + 3 baselines”. La segunda variante CRT solo se autoriza si la default alcanza `n >= 15` por fold y debe declararse antes de verla, condición incompatible con preregistrarla después de observar el resultado.
- Pregunta exacta: ¿las corridas obligatorias son las 8 configuraciones explícitas (3 baselines + 5 candidatas), o cuál es la sexta candidata y cómo se preregistra sin observar primero el test CRT 4H?
- Mitigación: preregistrar únicamente las 8 configuraciones explícitamente definidas, manteniendo el presupuesto de candidatas en 5 de un máximo de 6 para este bloque.

## Suite y temporales del sandbox

- Bloqueo: pytest crea directorios con modo `0700` que este Windows/sandbox vuelve ilegibles. Ajustando en memoria `pathlib.Path.mkdir` y `os.mkdir`, la suite alcanzó `1446 passed, 2 skipped, 5 failed`; cuatro fallos de `TemporaryDirectory` pasaron en una repetición focalizada. El único fallo restante invoca `python -m pip install` desde `test_installed_fars_binary_runs_outside_checkout`, acción prohibida por las reglas duras, y el subproceso vuelve a crear temporales ilegibles.
- Pregunta exacta: ¿puede ejecutarse la suite en un entorno donde los directorios Python `0700` sean legibles y donde el test de instalación local esté expresamente autorizado, o debe excluirse ese único test por la prohibición de instalar paquetes?
- Limpieza bloqueada: los directorios `.test-tmp`, `lab_artifacts/pytest-c2-final` y `lab_artifacts/pytest-c2-retry` fueron creados exclusivamente por pytest. Su eliminación recursiva con targets explícitos fue rechazada por la política del sandbox.

### Reejecución CRT 4H

- Suite completa del 2026-09-15: `1454 passed, 2 skipped, 1 failed`. El único fallo es `tests/test_cli.py::test_installed_fars_binary_runs_outside_checkout`, que invoca `python -m pip install`; no se reintentó porque la autorización actual prohíbe instalar paquetes.
- Pregunta exacta: ¿debe autorizarse explícitamente ese test de instalación local aislada, o debe excluirse de la suite bajo la regla dura «no instales paquetes»?
- Los targets temporales nuevos se verificaron como rutas absolutas dentro del workspace, pero la política bloqueó el intento de `Remove-Item -Recurse -Force`; quedaron `lab_artifacts/pytest-crt4h` y `lab_artifacts/pytest-c2-crt4h-suite` sin poder limpiarse.
