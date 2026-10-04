# BLOCKERS.md — W4 (Muse, 2026-09-28)

## Incidencia: edición concurrente de `run_w4.py` a las ~06:48 (NO bloqueante)

Hechos observados (sin inferir autoría):

- `pytest test_w4.py` corrió VERDE-parcial con `Bon_sin_barra_1600 == 2` y,
  sin que esta sesión editara `run_w4.py` (todos los `edit_file` de la
  sesión apuntaron a `test_w4.py`; sondas solo leen y escriben en `/tmp`),
  una corrida posterior dio `Bon_sin_barra_1600 == 1`.
- `run_w4.py` pasó de 96902 bytes / 2098 líneas (mtime 01:11) a
  97330 bytes / 2107 líneas (mtime 6:48:32 AM).
- Diferencia reconstruida (+9 líneas, behavior-neutral salvo conteo diag):
  1. `parse_roll_dates`: docstring W3 aclarado (miercoles excluido;
     lunes/martes solo se cuentan).
  2. `run_bucket`: contadores diag `*_sin_barra_1600` y
     `*_salida_fuera_del_dia` ahora solo para el escenario realista
     (el que concilia G2); la sensibilidad sigue en `unresolved_sens`.
  3. `process_market`: si `lock_path` existe al entrar,
     `sealed_already_open = True`; al abrir la cola (`open_tail`) crea el
     lock con `write_sealed_lock` (prereg sha + fecha + brazos).
- Decisión: se acepta el estado en disco como autoridad (cambios
  compatibles con C2/W3/G2). `test_miercoles_corto_truncado` se ajustó a
  `== 1` y se agregó cobertura del lock persistente a nivel proceso
  (`lock_path` preexistente → `sellado_ya_abierto`, `tail` None).

Nada de lo anterior bloqueó el encargo. Si Hermes esperaba otro contenido
en esas líneas, indíquese y se re-alinea.
