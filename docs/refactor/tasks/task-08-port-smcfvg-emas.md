# Tarea 8: port piloto de SMC-FVG y EMAS al framework FARS

## Alcance y decisión

Port de DOS estrategias de kai-backtesting al framework FARS, como las
siguientes candidatas tras el retiro de AMD+CRT:

- **SMC-FVG** (`strat_smc_fvg.py`): pivotes swing + BOS/CHoCH + entrada LIMIT en
  FVG. Es la única estrategia de kai con PF neto > 1 estable (README: PF 1.26).
- **EMAS** (`strat_emas.py`): EMA 10/20/55/200 + ruptura + gates horarios + stop
  ATR. En kai da PF 0.89 (negativo); se porta como control negativo y para
  validar que el framework reproduce la dirección del edge.

El dataset canónico es `databento_mnq/databento/MNQ_M1_2019-05-06.csv` (ver
`../canonical-dataset.md`, corte a post-lanzamiento real de MNQ, SHA-256
`9cbf7da1a1019c02a6b3c14c5e13cb63d583f1548fc19ac1cb8781165fd5dc5a`). La paridad
se valida en DIRECCIÓN y ORDEN DE MAGNITUD, no bit-for-bit: la tabla del README
de kai salió de un dataset 16-años más denso (incluido backfill sintético
pre-2019).

Baseline congelado: rama `main` @ `07921fa` (`docs: retire AMD+CRT strategy
hypothesis`). Rama de trabajo sugerida: `feature/port-smcfvg-emas`.

## Paso 0 — captura de referencia (obligatorio, antes de codificar)

Correr el código de kai SIN modificar sobre el canónico y registrar el target de
paridad:

```sh
cd reference/kai-backtesting
PYTHONPATH=src /opt/anaconda3/bin/python - <<'PY'
from kai_bt.core import bt
from kai_bt.core.loader import load_ohlc_arrays
import numpy as np
CSV = "/Users/ricardomedina/Documents/TRADING/databento_mnq/databento/MNQ_M1.csv"
data = load_ohlc_arrays(CSV, use_cache=False)
for key in ("smcfvg", "emas"):
    strat = bt.get_strategy(key)
    r, st, ets = strat.run(data, dict(strat.DEFAULTS))
    r = np.asarray(r, float); n = len(r)
    w = int((r > 0).sum()); gw = float(r[r > 0].sum()); gl = float(-r[r < 0].sum())
    print(key, dict(n=n, wr=round(w/n, 4) if n else None,
                    gross_pf=round(gw/gl, 4) if gl else None,
                    net_r=round(float(r.sum()), 2)))
PY
```

Registrar en este documento (tabla "Referencia canónica") el `n`, `wr`,
`gross_pf` y `net_r` de cada estrategia. Ese es el target. NO se persigue el
número exacto: se acepta dirección + orden de magnitud.

Referencia canónica CAPTURADA (2026-09-10, dataset cortado, DEFAULTS, bruto sin
costes):

| estrategia | n | win rate | PF bruto | netR |
|---|---:|---:|---:|---:|
| SMC-FVG | 5,979 | 59.2% | 1.437 | +1,067.3 |
| EMAS | 5,100 | 51.7% | 1.105 | +258.0 |
| CRT 4H | 372 | 35.5% | 2.689 | +391.9 |

**Paso 0 DONE.** Ese es el target de paridad (dirección + orden de magnitud, no
bit-for-bit).

## Arquitectura del port

Dos módulos nuevos en `src/backtest/`, simétricos a `amd_crt.py`:

- `src/backtest/smc_fvg.py` → clase `SmcFvgStrategy` + `smc_fvg_config()`.
- `src/backtest/emas.py` → clase `EmasStrategy` + `emas_config()`.

Cada uno implementa el protocolo `Strategy.evaluate(history) -> Signal | None`
(solo velas CERRADAS) y expone `parameters()`, `fresh()` y el decision log en el
mismo estilo que `AmdCrtStrategy`. El executor (`executor.py`) se extiende donde
haga falta (ver "Modelo de ejecución").

## Modelo de ejecución — decisión

Ambas estrategias comparten dos mecánicas que el executor actual NO modela:

1. **Parcial en TP1 (1R) + mover SL a break-even.** `f=0.5`: al tocar TP1 se
   cierra la mitad y el SL pasa a la entrada. Es central en el edge de ambas
   (corta el tail de pérdidas).
2. **SMC-FVG entra por LIMIT** en el borde del FVG (pendiente, se llena solo si
   el precio retrocede dentro de `wait=48` velas). EMAS entra a MERCADO en la
   apertura de la vela siguiente (ya coincide con el executor).

Decisión para el piloto:

- **Extender el executor con parcial+BE y cooldown** (mecánica 1 + `COOLDOWN=6`
  de SMC-FVG). Es una extensión acotada del contrato ya documentado en
  `fars-backtesting` (pitfall 11: `stop_target_as_points`, fills, gaps).
- **SMC-FVG: portar la entrada LIMIT fielmente** como orden pendiente con `wait`
  velas (igual que el bridge vivo de kai). NO simplificar a entrada a mercado:
  la entrada limit en el FVG es la hipótesis, no un detalle.
- **Extensión ADITIVA** (igual que Task 07): la parcial+BE, la orden pendiente y
  el cooldown entran como flags/params NUEVOS del executor con default OFF. El
  comportamiento por defecto (AMD+CRT) queda bit-for-bit intacto, verificado
  con tests de regresión explícitos (`test_backtest_executor.py`,
  `test_backtest_amd_crt.py` y `check_mnq_equivalence.py` pasan sin cambios).
- **Regla causal** (invariante): toda señal usa solo velas cerradas; los fills
  respetan SL-primero dentro de la vela; la orden pendiente no puede ver velas
  futuras a su barra de armado.

Si el executor no soporta una mecánica sin reescritura grande, documentarlo como
limitación EXPLÍCITA del piloto (con el efecto esperado sobre el resultado) y
NO inventar un atajo que cambie la semántica del fill.

## Criterios de aceptación

1. `SmcFvgStrategy` y `EmasStrategy` corren end-to-end por el executor sobre el
   canónico y escriben trades + `run_manifest.json` (mismo contrato que
   `amd-crt --write-trades`).
2. Paridad de DIRECCIÓN y ORDEN DE MAGNITUD vs la referencia del Paso 0:
   SMC-FVG con PF > 1 (positivo) y EMAS con PF < 1 (negativo); `n` dentro de
   [0.5×, 2×] del target; `wr` dentro de ±10 pp.
3. Cero look-ahead: señal sobre velas cerradas, fill en apertura siguiente (o
   limit pendiente sin mirar velas futuras), SL-primero intrabar.
4. Tests: unidad para cada estrategia (casos deterministas de señal, igual que
   `test_backtest_amd_crt.py`), más el test de equivalencia bit-a-bit antes/después
   de refactor (`check_mnq_equivalence.py` extendido a las dos estrategias).
5. `ruff check` y `git diff --check` limpios; suite completa verde.
6. El manifest registra hash del dataset canónico, `MarketSpec` MNQ, parámetros,
   split, commit y hashes de outputs.
7. La extensión del executor es ADITIVA: con los flags nuevos en OFF, AMD+CRT
   reproduce exactamente los resultados previos (regresión bit-for-bit); los
   tests existentes de executor/AMD+CRT pasan sin modificarse.
8. Tests unitarios ESPECÍFICOS por mecanismo de ejecución (además de la
   regresión bit-for-bit de AMD+CRT), cada uno con caso determinista y aserción
   exacta del R resultante:
   a. Parcial+BE: un trade que toca TP1 cierra exactamente f=0.5 de la posición
      y el SL restante queda en breakeven (precio de entrada); y, tras eso, si
      el SL en breakeven se dispara, el resultado = parcial ya cobrado sin
      pérdida en el resto.
   b. Limit pendiente que EXPIRA por cooldown/`wait` sin llenarse (no produce
      trade).
   c. Limit que SE LLENA en el retroceso al borde del FVG (produce trade).
   Propósito: si el gate de paridad de Fase 2 falla, poder atribuir la
   discrepancia a UN mecanismo concreto, no solo a "el PF no coincide".

## Decision log (patrón)

Cada estrategia expone decisiones en el formato de `AmdCrtDecision`
(`src/backtest/decisions.py`), reutilizable por meta-labeling: candidatura
(entrada, dirección, riesgo, hora) + rechazo con motivo + resultado
(`r_result`) al cerrar. El port es la fuente de los datos, NO un clasificador.

## Qué NO hacer

- No tunear parámetros para acercar el resultado al de kai (eso es data-mining;
  se valida con el split cronológico, no contra el target).
- No tocar `strat_smc_fvg.py` / `strat_emas.py` de kai (invariante de producto).
- No atribuir reglas a nadie: portar el CÓDIGO de kai tal cual, documentando
  cada supuesto (sesión ET, ATR SMA vs Wilder, siembra de EMA).
- No versionar el CSV canónico ni sus .cache; `git ls-files` debe quedar limpio
  de `data/` y `*.csv`.
