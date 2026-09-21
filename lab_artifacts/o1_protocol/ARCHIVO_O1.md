# O1 — Archivo congelado

**Veredicto:** `NO_DEV_PULSE` — revisado por Muse sin bloqueadores y reproducido por Hermes. No abrir validation/final y no retocar esta misma hipótesis.

## Resultado DEV

| Configuración | n | E[R] | PF | Folds + | CBB CI 98.75% |
|---|---:|---:|---:|---:|---:|
| L20 realista | 3,614 | -0.0627 | 0.8464 | 2/8 | [-0.0965, -0.0269] |
| L20 estrés | 3,614 | -0.1157 | 0.7390 | 0/8 | [-0.1492, -0.0803] |
| L55 realista | 2,065 | -0.0523 | 0.8779 | 3/8 | [-0.1039, -0.0040] |
| L55 estrés | 2,065 | -0.1049 | 0.7740 | 0/8 | [-0.1559, -0.0568] |

Gates: muestra 1/1; los otros 5 fallaron. Hubo 4 posiciones truncadas al final de folds, por lo que el gate fail-closed también rechazó integridad operativa. La pérdida gross ya era negativa; el resultado no es un artefacto de costes.

## Hipótesis refutada

En MNQ M5 DEV 2019-05-06–2023-12-29, **NR7 en t-1 + ruptura Donchian L20/L55**, entrada al open siguiente, stop 1×ATR14, target 2×ATR14 y time exit 48 barras, no aporta asimetría neta bajo los dos costes declarados.

Esto no refuta otras definiciones de régimen/entrada/salida. Reabrir exige una hipótesis distinta preregistrada o nueva información; no ajustes posteriores de L, ATR o costes sobre estos mismos datos.

## Evidencia congelada (SHA-256)

- `preregistro.json`: `2b1bc4321a7d4165b92eed8cb80bd98b6b6698e369e16aa8acb09fb9e7b6b4ed`
- `entries_o1.py`: `84cb6e508d2a9a227ff8e295dc7e310b26602fe9f900589a3d7592fe5ba352f9`
- `run_o1.py`: `9da18d9d2786ccc120600a2cbde892fc5a8e69738984b3dc0d2f9225b5a32d79`
- `test_entries_o1.py`: `94fed012377c06d597a8016b5dd34f4a9c428392624e3b7b26a4b06c7e464b84`
- `evaluation_gates.json`: `72324f84bad70bbb69e3fc999b516ae4fcdec102e655c488a6dccadb8951fa7e`
- `metrics_L20_realista.json`: `a9934ed73d3955e434113fd28abf591dddd3f05df13cab70edc0005eb9159d41`
- `metrics_L20_estres.json`: `131817f7b6490595b6c4134e821118dd7a524b1ce5ec95883f41131797c49aac`
- `metrics_L55_realista.json`: `46e8510b1cdb215f9966922ae1da56b80762627c9ff0953454dcfac75309c195`
- `metrics_L55_estres.json`: `57488b90c40b3f44fb1b9ff877a6580500e8558289469cb8424e9d75a7dc94a3`

## Reproducción

```bash
"E:/FARS-LAB/.venv-fars/Scripts/python.exe" lab_artifacts/o1_protocol/run_o1.py --workers 4
```

Esperado en esta máquina: ~4 segundos para la matriz después de cargar 328,715 barras; veredicto `NO_DEV_PULSE`.

## Próximo paso

No continuar O1. Priorizar operacionalizar el pilar SMC-FVG MNQ ya respaldado y validar por separado MGC `min_risk=3.0` antes de cualquier integración multimerkado.
