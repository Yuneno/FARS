# Bloqueos de C2

## Escritura de Git

- Bloqueo: el entorno permite leer `.git` pero no escribirlo. `git switch -c bloque-c2-busqueda b322363` falla con `Unable to create '.git/index.lock': Permission denied`.
- Pregunta exacta: ¿puede habilitarse escritura en `E:/FARS-LAB/FARS/.git` para crear la rama `bloque-c2-busqueda` y realizar los commits locales obligatorios?
- Mitigación: continuar únicamente con cambios del árbol de trabajo; no modificar `.git` por otra vía y no ejecutar backtests antes de congelar el preregistro.

## Especificación CRT 4H incompleta en las fuentes autorizadas

- Bloqueo: `FARS_KAI_PORT_DIFF.md` §2.3 contiene defaults y una secuencia de alto nivel, pero no define de forma exacta el sesgo D1, los buffers, los índices `argmin/argmax`, la definición del market structure shift/FVG ni el target. El encargo prohíbe importar el código de Kai, inventar o alterar esas decisiones.
- Pregunta exacta: ¿cuál es la especificación algorítmica completa y autorizada de `strat_crt4h.py` (incluidos sesgo D1, buffers, orden/indexación de extremos, MSS, FVG y target) que debe reimplementarse sin consultar/importar el repo de Kai?
- Mitigación: no inventar el port ni producir resultados CRT 4H hasta disponer de esa especificación; avanzar con EMAS/SMC-FVG y el runner común cuando sea posible.

## Cardinalidad de configuraciones

- Bloqueo: las tablas obligatorias registran 3 baselines y 5 candidatas (EMAS 2, SMC-FVG 2, CRT 4H 1), pero el orden de trabajo pide correr “6 configuraciones + 3 baselines”. La segunda variante CRT solo se autoriza si la default alcanza `n >= 15` por fold y debe declararse antes de verla, condición incompatible con preregistrarla después de observar el resultado.
- Pregunta exacta: ¿las corridas obligatorias son las 8 configuraciones explícitas (3 baselines + 5 candidatas), o cuál es la sexta candidata y cómo se preregistra sin observar primero el test CRT 4H?
- Mitigación: preregistrar únicamente las 8 configuraciones explícitamente definidas, manteniendo el presupuesto de candidatas en 5 de un máximo de 6 para este bloque.

## Suite y temporales del sandbox

- Bloqueo: pytest crea directorios con modo `0700` que este Windows/sandbox vuelve ilegibles. Ajustando en memoria `pathlib.Path.mkdir` y `os.mkdir`, la suite alcanzó `1446 passed, 2 skipped, 5 failed`; cuatro fallos de `TemporaryDirectory` pasaron en una repetición focalizada. El único fallo restante invoca `python -m pip install` desde `test_installed_fars_binary_runs_outside_checkout`, acción prohibida por las reglas duras, y el subproceso vuelve a crear temporales ilegibles.
- Pregunta exacta: ¿puede ejecutarse la suite en un entorno donde los directorios Python `0700` sean legibles y donde el test de instalación local esté expresamente autorizado, o debe excluirse ese único test por la prohibición de instalar paquetes?
- Limpieza bloqueada: los directorios `.test-tmp`, `lab_artifacts/pytest-c2-final` y `lab_artifacts/pytest-c2-retry` fueron creados exclusivamente por pytest. Su eliminación recursiva con targets explícitos fue rechazada por la política del sandbox.
