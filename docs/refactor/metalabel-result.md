# Resultado del meta-labeling (Tarea 5) — regresión logística

## Veredicto: la estrategia AMD+CRT no tiene señal predecible en sus variables

Corrida end-to-end sobre Databento MNQ_M5.csv (990,452 velas M5, 2010-2026),
con `AmdCrtStrategy()` + `run_backtest` + `join_decisions_to_outcomes` +
walk-forward cronológico (5 folds expanding-window, sin filtración).

### Métricas out-of-sample (agregadas)

| Métrica | Regresión logística | Baseline (predecir "pierde") |
|---|---|---|
| Accuracy | 0.539 | **0.567** |
| ROC AUC | 0.523 | 0.500 |
| Precision | 0.422 | — |
| Recall | 0.175 | — |
| n out-of-sample | 1135 | 1135 |

### Lectura honesta

1. El modelo NO supera el baseline trivial. Quedó POR DEBAJO (0.539 vs 0.567).
2. El baseline es "predecir siempre que la señal pierde", que acierta 56.7%
   porque la estrategia pierde ~58% de las veces.
3. ROC AUC 0.523 ≈ azar (0.5): las features causales registradas (ATR, ratio
   de compresión pre-NY/mediana, dirección, régimen EMA, SL/TP, weekday) NO
   contienen poder predictivo sobre si una señal va a ganar o perder.

### Conclusión

No es un problema de modelo. Es que el setup AMD+CRT, tal como está
parametrizado, no tiene edge, y sus variables de entrada no predicen el
resultado del trade. El framework de meta-labeling (decision log → join →
walk-forward → modelo) queda operativo y reutilizable para evaluar cualquier
estrategia futura con rigor.

## Siguiente paso

XGBoost (Tarea 6) con las MISMAS features no se espera que mejore
sustancialmente (la limitación es de features, no de modelo). El valor real
está en: (a) enriquecer features, o (b) aplicar esta maquinaria a una
estrategia/setup distinto. Ver plan en `AUTONOMY_ROADMAP.md`.
