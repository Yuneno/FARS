# Dataset canónico MNQ — convención única y verificación

## Decisión y Convención Única

Dataset canónico de FARS para MNQ:
- **Ubicación:** Miembro `databento/MNQ_M5.csv` en `E:\FARS-LAB\databento.zip`
- **Filtro canónico:** `timestamp >= 2019-05-06` (post-lanzamiento real del contrato MNQ)
- **SHA-256 del miembro del zip:** `fbed6061205b8299af140f85e36b472f5f1d88084977ad9c4ca9aa1f817b8a96`
- **Velas M5 resultantes:** 518,237 barras · 7.33 años
- **Rango temporal:** 2019-05-06T00:00:00.000Z → 2026-09-03T23:55:00.000Z (UTC)
- **Script de verificación reproducible:** `lab_artifacts/verify_canonical_dataset.py` (hashea el miembro crudo y valida el conteo de barras)

## Justificación

MNQ (Micro E-mini Nasdaq-100) lanzó en mayo 2019. TODO dato anterior a esa
fecha, en CUALQUIER fuente (nuestra o la de kai), es backfill sintético: el
contrato no existía, así que no es trading real de MNQ. Es irrelevante cuán
denso esté el backfill de minutos — es sintético y se descarta. Se descartan
472,215 velas M5 (47.7% del rango 2010-2026).

## Hallazgo SMC-FVG: el corte purifica, no perjudica

Corrida completa del código de kai, split por timestamp de entrada:

| tramo | trades | netR | win rate |
|---|---:|---:|---:|
| pre-2019 (sintético) | 584 | −75.5 | 44.3% |
| post-2019 (real) | 5,980 | +1,066.3 | 59.2% |

El 91% de los trades de SMC-FVG y TODO su edge están en datos reales post-2019.
El tramo sintético pre-2019 le RESTABA edge (−75.5R). Cortarlo deja la
estrategia igual o mejor y limpia el baseline.

## Limitación CRT 4H: meta-labeling sub-potenciado

Sobre el dataset cortado CRT 4H queda con n=372 trades (~132 wins / 240
losses). Eso basta para bootstrap de expectancy/win-rate, pero es INSUFICIENTE
para meta-labeling con la confianza del análisis de AMD+CRT (que usó ~1,362
trades para concluir "sin señal", AUC≈0.523). Cualquier resultado de
meta-labeling sobre CRT 4H debe marcarse como **"insuficiente para descartar
ausencia de señal"**, no como conclusión firme.

## Referencia canónica (kai, DEFAULTS, bruto sin costes)

Corrida del código de kai SIN modificar sobre el dataset cortado:

| estrategia | n | win rate | PF bruto | netR | exp |
|---|---:|---:|---:|---:|---:|
| SMC-FVG | 5,979 | 59.2% | 1.437 | +1,067.3 | +0.179 R |
| EMAS | 5,100 | 51.7% | 1.105 | +258.0 | +0.051 R |
| CRT 4H | 372 | 35.5% | 2.689 | +391.9 | +1.054 R |

Estos son los targets de paridad de Fase 1 (dirección + orden de magnitud, no
bit-for-bit). Los PF del README de kai salieron de un dataset 16-años más denso
y NO son comparables directo.

## Alcance del corte a otros mercados

La misma regla aplica a los demás micros (MES, MYM, MGC lanzaron ~mayo 2019):
cuando se usen sus datasets para estrategias portadas, se aplicará el mismo
corte a post-lanzamiento. BTC/ETH son cripto 24/7 y no tienen "lanzamiento" de
contrato futuro micro — se tratan aparte.
