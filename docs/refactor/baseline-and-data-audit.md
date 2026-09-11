# Punto de partida + Auditoría de datos — 2026-09-09

## Punto de partida congelado

- Branch: `main`
- Commit: `7214f84 feat(backtest): add EMA10/4h confluence filter and causal bootstrap edge gate`
- Tag: `baseline-2026-09-09`
- Working tree: limpio (todo commiteado)
- 8 worktrees paralelos; el activo es `main` (7214f84)

## Dataset del backtest de referencia (el usado hasta hoy)

- Archivo: `mnq_data_zp/mnq_extended_m5.csv` (104,961,939 bytes)
- Columnas: `time,open,high,low,close,tick_volume,contract,is_real_mnq`
- Rango: 2010-06-07 00:00 → 2026-09-03 20:55 (UTC)
- Filas: 1,129,482 (M5)
- Resultados de referencia (config: 50K inicial, risk 1%, 1 micro, 60min exit,
  2pts friction round-trip):
  - AMD+CRT (sin EMA): n=1502, win=0.416, exp=-0.0050, PF=0.913, maxDD_R=9.01
  - AMD+CRT + EMA10/4h: n=824, win=0.415, exp=-0.0020, PF=0.962, maxDD_R=5.96

## Auditoría: hallazgos clave

### mnq_extended_m5.csv (el "viejo")
- 54% de las filas son `is_real_mnq=False` (614,187 filas). El MNQ (Micro
  E-mini Nasdaq-100) no existió hasta mayo 2019; el resto es un proxy
  (presumiblemente NQ E-mini full, ~10x el tick, rescalado a escala MNQ).
- Contrato: `MNQ_DATABENTO_CONTINUOUS` (sintético, ya empalmado) + `MNQU26`.
- Transición False→True: 2019-05-03 (último False) → 2019-05-05 (primer True).
- Cobertura: ~24h/día (00:00→23:55 UTC, 276 barras/día), 5040 días.
- Sin duplicados ni desorden (0 violaciones de orden estricto).

### MNQ_M5.csv de Databento (el "nuevo", extraído del ZIP)
- Archivo: `databento_mnq/databento/MNQ_M5.csv` (68,222,607 bytes)
- Columnas: `timestamp,open,high,low,close,volume,timeframe,symbol`
- Rango: 2010-06-07 00:00 → 2026-09-03 23:55 (UTC)
- Filas: 990,452 (M5)
- Cobertura: ~24h/día (00:00→23:55 UTC, 276 barras/día), 5040 días.
- Sin duplicados ni desorden.

### Comparación directa (CORRECCIÓN de la versión anterior)
- **Precios idénticos**: en 982,145 timestamps comunes, la mediana de
  diferencia de close es 0.0. Son el mismo activo, misma escala, mismos
  precios. NO hay diferencia de tick ni de escala.
- **Cobertura horaria idéntica**: ambos 00:00→23:55 UTC (~24h, 276 barras/día).
  La observación previa de "extended termina 20:55" era solo la última barra
  del archivo, no la cobertura diaria normal. AMBOS cubren ~24h.
- **La diferencia real es de CANTIDAD de barras**: extended tiene 139,030
  barras MÁS (1,129,482 vs 990,452). Esas barras extra están en timestamps que
  Databento NO tiene, y la mayoría caen en la región `is_real_mnq=False`
  (pre-2019). Hipótesis: extended = Databento + relleno de huecos con proxy NQ.

### Decisión ejecutada (2026-09-10)

Ver `canonical-dataset.md`. Resumen:

- Canónico MNQ = `databento_mnq/databento/MNQ_M1_2019-05-06.csv` (corte a
  post-lanzamiento real de MNQ, 2019-05-06 → 2026-09-03, 2,589,531 velas M1,
  SHA-256 `9cbf7da1a1019c02a6b3c14c5e13cb63d583f1548fc19ac1cb8781165fd5dc5a`).
  Todo pre-2019 (en cualquier fuente) es backfill sintético y se descarta.
- El MNQ-1M.csv de kai (5,346,796 velas M1, 2010-06-07 → 2026-07-08) NO se
  persigue: su tabla de referencia sirvió solo para confirmar dirección/orden
  de magnitud del edge, no como benchmark de paridad exacta.
- PENDIENTE (heredado, no bloqueante): tratamiento de rollover del Databento
  (no trae columna de contrato; inferir por precio/volumen si hace falta).
- El contrato de datos común (multi-mercado: MNQ, MYM, MGC, BTC, ETH, MES)
  se diseña con adaptadores por símbolo, no arquitectura exclusiva MNQ.

## Estado de tareas
- [x] AGENTS.md actualizado (Hermes=orquestador/revisor, Codex=programador)
- [x] Punto de partida congelado (tag baseline-2026-09-09)
- [x] Auditoría de datos MNQ (hallazgos arriba)
- [ ] Primera tarea concreta para Codex
