# Tarea 6: contrato de mercado para AMD+CRT

## Punto de partida y alcance

Implementación directa autorizada por el usuario el 2026-09-10, incluyendo
revisión y un único commit local. Esa instrucción reemplaza el flujo de Hermes
de `AGENTS.md` para esta tarea.

- Inicio verificado: `main`, working tree limpio,
  `1fa30ae48518b6fbacf6be2aaee4a34d6eb68406` (Tarea 5).
- `baseline-2026-09-09` es una etiqueta anotada: objeto
  `d163e4273a38dd81948b11e65042bf5b9d262d7e`, commit destino
  `7214f84f150c6b27c8daa13b2db0a2fd6f6bf528`.
- Rama de entrega: `feature/multi-market-contract`.
- El commit de esta entrega es el que incorpora este reporte; obtenerlo con
  `git log -1 --format=%H -- docs/refactor/task-06-multi-market.md`.
- No se ejecutaron backtests históricos de MES, MYM o MGC, ni entrenamiento de
  modelos, optimización, merge o push. Los tests económicos usan dos barras
  artificiales por especificación, únicamente para verificar aritmética.

Los resultados de equivalencia proceden de ejecutar el código de Tarea 5 en
esta sesión. No se reutilizan conclusiones ni cifras de versiones anteriores.

## Inventario de constantes inspeccionadas

| Lugar | Configuración encontrada | Tratamiento |
|---|---|---|
| `amd_crt.py` | ET, pre-NY 00:00–09:30, confirmación 09:30–10:30, RTH 09:30–16:00 | Ventanas y zona desde `MarketSpec`; confirmación sigue siendo una hora desde apertura |
| `amd_crt.py` | 78 barras M5, mínimo 39 para reconstrucción D1 | Se derivan de la ventana regular; MNQ sigue 78/39, MGC 62/31 |
| `amd_crt.py` | Zona ET en buckets EMA, cutoff ATR, evaluación y observación | Zona de mercado; orden de cálculo intacto |
| `amd_crt_config` | 2 USD/punto, fricción 2 puntos | Valores por defecto desde MNQ; comisión = fricción × USD/punto / 2 por lado |
| `executor.py` | Tick 0.25, 2 USD/punto, asset `MNQ` al mapear a Core | Defaults del registro; símbolo de la estrategia transmitido al adaptador |
| `cli.py` | Fricción MNQ 2 puntos | Default delegado al contrato; `--market` inyecta el mismo objeto a estrategia y configuración |
| `mnq_csv.py` | Nombre MNQ y metadatos documentales; timestamps con offset, agregación UTC | Sin cambios: no contiene multiplicadores, ticks ni filtros de sesión |
| `metalabel.py` | Runner explícitamente MNQ; nombres de features `pre_ny_*` | Sin cambios: continúa usando los defaults compatibles |
| `history.py`, CLI download, Breakout, batch | Defaults/documentación del MVP MNQ y sus escenarios propios | Fuera de la lógica AMD+CRT; no se rediseñan adaptadores ni escenarios |

Las comisiones/slippage provisionales de `BacktestConfig()` genérico (0.62 por
lado / 0.25 puntos) permanecen como estaban. AMD+CRT usa su factory, que concentra
la fricción total en comisión y deja el slippage en cero, exactamente como antes.

## Diseño y valores

`src/backtest/markets.py` define `@dataclass(frozen=True) MarketSpec` con los nueve
campos solicitados: símbolo, `ZoneInfo`, cuatro horas locales `datetime.time`,
USD/punto, tick y fricción agregada round-trip en puntos. `MARKETS` es un registro
inmutable (`MappingProxyType`), y `get_market_spec()` resuelve símbolos sin
duplicar configuración. No hay valores heredados implícitamente entre mercados.

| Mercado | Zona | Pre-sesión | Sesión regular de investigación | USD/punto | Tick (puntos) | Fricción round-trip (puntos) |
|---|---|---|---|---:|---:|---:|
| MNQ | America/New_York | 00:00–09:30 | 09:30–16:00 | 2.0 | 0.25 | 2.0 |
| MES | America/New_York | 00:00–09:30 | 09:30–16:00 | 5.0 | 0.25 | `None`: pendiente |
| MYM | America/New_York | 00:00–09:30 | 09:30–16:00 | 0.5 | 1.0 | `None`: pendiente |
| MGC | America/New_York | 00:00–08:20 | 08:20–13:30 | 10.0 | 0.1 | `None`: pendiente |

Procedencia (fuentes consultadas el 2026-09-10):

- MNQ: constantes de `amd_crt.py`, `amd_crt_config` y `BacktestConfig` en
  `1fa30ae`; fricción como escenario agregado existente, no cotización del broker.
- MES/MYM: la búsqueda del repositorio solo encontró los nombres, sin valores.
  Multiplicadores y ticks tomados de la
  [ficha oficial de futuros Micro E-mini de CME](https://www.cmegroup.com/trading/equity-index/files/cme-micro-e-mini-futures-fact-card.pdf).
- MGC: 10 onzas y tick de 0.10 USD/onza, por tanto 10 USD por punto de precio y
  1 USD por tick, según la
  [guía oficial de metales CME, sección Micro Gold Futures](https://www.cmegroup.com/trading/metals/files/metals-prod-guide-2023.pdf).
- Las ventanas MES/MYM adoptan explícitamente la convención de investigación de
  índices ya utilizada por MNQ. No representan toda la sesión Globex.
- La ventana MGC adopta explícitamente el horario **histórico** de piso COMEX
  Gold 08:20–13:30 ET documentado en la
  [ficha histórica de CME](https://www.cmegroup.com/trading/metals/files/MT-023_NewMetals_WhitePaper_SR.pdf).
  Es una convención de investigación pendiente de validar para MGC y el
  proveedor; no se afirma que sea el horario electrónico actual de MGC.
- No existen costes verificados para MES/MYM/MGC. Decisión conservadora:
  `friction_points=None` expresa el dato pendiente, y `amd_crt_config(market=...)`
  falla claramente hasta recibir `friction_pts` explícito. Nunca se convierte
  automáticamente en cero ni se copia el coste de MNQ.

Validación: símbolo no vacío, timezone `ZoneInfo`, horas locales sin tzinfo y
alineadas a M5, ventanas ordenadas dentro del mismo día, pre-sesión contigua a
apertura regular, sesión con espacio para la confirmación de una hora, tick y
multiplicador positivos finitos, fricción finita no negativa cuando se conoce.
Una especificación con costes desconocidos es válida como descripción; todavía
no es ejecutable con costes por defecto.

`AmdCrtStrategy(market=MNQ)` y `amd_crt_config(market=MNQ)` conservan los defaults.
`fresh()` conserva el mercado y limpia el estado. Los reportes MNQ mantienen su
estructura exacta; para una especificación distinta se añade únicamente
`strategy_parameters.market`, con el contrato completo serializable.
`pre_ny_amplitude` se conserva en decisiones/features para no romper esquemas.

Los parámetros heredados `session_tz` / `session_start` siguen definiendo el
agrupamiento del día, como antes. Al omitirse `session_tz` se usa la zona de
`MarketSpec`. Para configurar un mercado nuevo, cambiar la zona en el contrato;
el override heredado afecta únicamente el agrupamiento, no las ventanas o EMA.

El API requiere pasar la misma especificación a estrategia y factory; la CLI
lo hace automáticamente. Ejemplo de construcción, sin ejecutar backtests:

```python
from dataclasses import replace
from src.backtest import AmdCrtStrategy, amd_crt_config, get_market_spec

mnq = get_market_spec("MNQ")
strategy = AmdCrtStrategy(market=mnq)
config = amd_crt_config(market=mnq)
# Tras validar un coste, crear una variante explícita con
# replace(get_market_spec("MGC"), friction_points=coste_validado).
```

## Validación y equivalencia

Antes de editar producción se ejecutaron los tests relevantes (105 aprobados)
y se inició la suite completa (1077 aprobados, 355.54 s). Se guardaron resultados
de referencia mientras producción seguía en `1fa30ae`. El proceso del CSV
completo importó esa versión antes de comenzar el refactor.

El comando solicitado sin ajuste de entorno falló con exit 139 al importar
`readline` desde el plugin de depuración de pytest, antes de recoger tests,
tanto dentro como fuera del sandbox. El entorno heredaba `LC_ALL=C.UTF-8`, no
soportada aquí. Inicialmente se pudo ejecutar con
`PYTEST_ADDOPTS='-p no:debugging'`. Después se verificó
`LC_ALL=C /opt/anaconda3/bin/python -c 'import readline'` y se usó `LC_ALL=C`
para la validación final con todos los plugins. No se modificó Anaconda ni la
configuración de pytest del proyecto.

Validación específica final con todos los plugins: **143 aprobados en 25.08 s**
(38 nuevos, 92 AMD+CRT, 7 decisiones y 6 meta-labeling). La primera suite
posterior pasó 1114 tests en 411.57 s; después se añadió una prueba del runner,
que también pasó. La suite final terminó con **1115 aprobados en 355.87 s**.
Ruff sobre todos los archivos Python de la entrega y `git diff --check` pasan.

La herramienta `docs/refactor/check_mnq_equivalence.py` serializa cada float
mediante `struct.pack('!d', value).hex()`: conserva todos los bits IEEE-754,
incluyendo cero con signo y NaN. Compara bytes completos, no tolerancias ni
métricas redondeadas. Incluye todos los campos de `BacktestResult`, todos los
trades (timestamps, direcciones, precios, motivos, cantidades, PnL, costes y R),
equity curve, simulación de reglas, decisiones y parámetros de estrategia.
La herramienta rechaza usar el mismo archivo como salida y referencia
(incluyendo enlaces al mismo archivo); se verificó el rechazo con exit 2 y
sin sobrescribir la captura original.

Referencias sintéticas capturadas ANTES del refactor e incorporadas como tests:

| Caso | Trades | SHA-256 antes = después |
|---|---:|---|
| 6 escenarios MNQ: long/short × stop/target/time exit | 6 | `71debb93509da2e7ce2875ba2ab1c8eb2e7cd1cb6e1602e07f4daf3c87e756e5` |
| Mismos escenarios con EMA habilitada | 3, más 3 rechazados | `0a80e9ef7fed6e7cb03fbb337da909ce2ff5925ac4c76977c67c5479ed3758d2` |

CSV completo utilizado sin modificar:
`/Users/ricardomedina/Documents/TRADING/databento_mnq/databento/MNQ_M5.csv`.
SHA-256 del archivo:
`fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`.
El loader y el tratamiento de timestamps/contratos son los heredados; esta
tarea no certifica ni reinterpreta la procedencia histórica del CSV.

La referencia histórica anterior tiene 990452 barras, 1362 trades (688 short,
674 long), 62293 decisiones, 633 time exits, 553 stop losses y 176 take profits.
Primer entry: `2013-04-23T17:10:00+00:00`; último exit:
`2026-08-28T14:55:00+00:00`. La captura completa ocupa 24914014 bytes.
Su SHA-256 es
`5e0e8d9d66e4a2e86f5972351fc213aebe38161bcb3400cbe603a8d771f385b1`.
La captura posterior tiene exactamente el mismo tamaño y SHA-256.
El comparador verificó **igualdad byte por byte del archivo completo** y terminó
con exit 0: `BIT-FOR-BIT IDENTICAL (full results, decisions and parameters)`.
También se verificó con `cmp` independientemente del comparador Python.

| Métrica | Antes (`1fa30ae`) | Después |
|---|---:|---:|
| Trades | 1362 | 1362 |
| PnL neto (USD) | -2523.5 | -2523.5 |
| Win rate | 0.42290748898678415 | 0.42290748898678415 |
| Profit factor | 0.9407879299826365 | 0.9407879299826365 |
| Expectancy (USD/trade) | -1.8527900146842877 | -1.8527900146842877 |
| Max drawdown (fracción) | 0.06829 | 0.06829 |
| Comisión total (USD) | 5448.0 | 5448.0 |
| Slippage total (USD) | 0.0 | 0.0 |
| Rechazos por gaps / posiciones sin resolver | 0 / 0 | 0 / 0 |

La tabla es una presentación decimal; la evidencia de igualdad usa los bits
originales. SHA-256 de la lista completa de trades serializada:
`100ca0e2af9ebd8eb7e6bf72273610db0ddc35eec71841399dad8d108d8abac6`.

Artefactos completos antes/después: `/private/tmp/fars-task6-4D4ZNS/`.
Son evidencia local temporal; el script, hashes y tests quedan versionados.

## Comandos ejecutados

Inspección con `git status --short`, `git branch --show-current`, `git log -1`,
`git rev-parse`, `rg --files`, `rg -n`, `cat` y `sed`; lectura de `AGENTS.md`,
estrategia, loader, executor, pipeline, CLI, runner de meta-labeling y tests.
Rama creada con `git switch -c feature/multi-market-contract`.

```sh
# Baseline: intento normal y reintento fuera del sandbox (exit 139).
/opt/anaconda3/bin/python -m pytest tests/test_backtest_amd_crt.py tests/test_backtest_decisions.py tests/test_backtest_metalabel.py -q
# Baseline ejecutable (105 y 1077 tests).
PYTEST_ADDOPTS='-p no:debugging' /opt/anaconda3/bin/python -m pytest tests/test_backtest_amd_crt.py tests/test_backtest_decisions.py tests/test_backtest_metalabel.py -q
PYTEST_ADDOPTS='-p no:debugging' /opt/anaconda3/bin/python -m pytest tests/ -q
# Validación: comando solicitado (exit 139) y entorno corregido.
/opt/anaconda3/bin/python -m pytest tests/ -q
LC_ALL=C /opt/anaconda3/bin/python -m pytest tests/ -q
LC_ALL=C /opt/anaconda3/bin/python -m pytest tests/test_backtest_markets.py tests/test_backtest_amd_crt.py tests/test_backtest_decisions.py tests/test_backtest_metalabel.py -q
# Captura con producción en 1fa30ae, y comparación posterior.
/opt/anaconda3/bin/python -m docs.refactor.check_mnq_equivalence --output /private/tmp/fars-task6-4D4ZNS/synthetic-before.json
/opt/anaconda3/bin/python -m docs.refactor.check_mnq_equivalence --ema --output /private/tmp/fars-task6-4D4ZNS/synthetic-ema-before.json
/opt/anaconda3/bin/python -m docs.refactor.check_mnq_equivalence --output /private/tmp/fars-task6-4D4ZNS/synthetic-after.json --reference /private/tmp/fars-task6-4D4ZNS/synthetic-before.json
/opt/anaconda3/bin/python -m docs.refactor.check_mnq_equivalence --ema --output /private/tmp/fars-task6-4D4ZNS/synthetic-ema-after.json --reference /private/tmp/fars-task6-4D4ZNS/synthetic-ema-before.json
/opt/anaconda3/bin/python -m docs.refactor.check_mnq_equivalence --csv /Users/ricardomedina/Documents/TRADING/databento_mnq/databento/MNQ_M5.csv --output /private/tmp/fars-task6-4D4ZNS/mnq-before.json
/opt/anaconda3/bin/python -m docs.refactor.check_mnq_equivalence --csv /Users/ricardomedina/Documents/TRADING/databento_mnq/databento/MNQ_M5.csv --output /private/tmp/fars-task6-4D4ZNS/mnq-after.json --reference /private/tmp/fars-task6-4D4ZNS/mnq-before.json
cmp /private/tmp/fars-task6-4D4ZNS/mnq-before.json /private/tmp/fars-task6-4D4ZNS/mnq-after.json
/opt/anaconda3/bin/python -m ruff check src/backtest/markets.py src/backtest/amd_crt.py src/backtest/executor.py src/backtest/cli.py src/backtest/__init__.py tests/test_backtest_markets.py docs/refactor/check_mnq_equivalence.py
git diff --check
```

Hubo dos fallos en aserciones nuevas que nombraban las salidas como `stop` y
`target`; se corrigieron a los nombres preexistentes `stop_loss` y `take_profit`
tras consultar la captura anterior. Los hashes ya coincidían y no se cambiaron.

## Archivos y pendientes

- Nuevos: `src/backtest/markets.py`, `tests/test_backtest_markets.py`,
  `docs/refactor/check_mnq_equivalence.py`, este reporte.
- Modificados: `src/backtest/amd_crt.py`, `src/backtest/executor.py`,
  `src/backtest/cli.py`, `src/backtest/__init__.py`.

Pendiente antes de investigar otros instrumentos: validar ventanas de sesión,
costes reales, frontera D1 y tratamiento de contratos/rollover. Se conserva la
aproximación D1 de media sesión y bloques de 65 sesiones, así como ATR14,
SL=min(2×ATR, 50 puntos), TP=2×SL y time exit de 60 minutos; su idoneidad en otros
mercados no queda demostrada por este refactor. Solo se aceptan ventanas diurnas
contiguas alineadas a M5; calendarios de festivos y ventanas que cruzan medianoche
no están implementados. Meta-labeling sigue teniendo un runner MNQ.

La prueba histórica completa cubre AMD+CRT por defecto; EMA se cubre mediante
tests existentes y referencia sintética exacta, no una nueva corrida histórica
completa con EMA. No se afirma mejora predictiva ni rentabilidad.
