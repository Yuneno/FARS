# BLOCKERS — Bloque M10: Evaluacion Topstep

## Estado General
- **Bloque completado:** Sí, 100 % offline, sin push, sin dependencias externas.
- **Veredicto:** NO PAGAR. El candidato C1 no bate al control aleatorio C3.

## Hallazgos Críticos
1. **Falso positivo de P(pasar):** Una tasa de pase del ~30 % en cuentas de evaluacion parece atractiva a primera vista, pero el control aleatorio obligatorio (C3) demuestra que un generador de azar con la misma distribucion de R obtiene exactamente el mismo resultado (~29.5 % - 32.7 %). Pasar es varianza de camino de corto plazo, no alpha.
2. **MAE intrabar adverso:** El modelo con MAE intrabar (extraido de M1 de databento.zip) reduce P(pasar) en ~4.4 puntos porcentuales y eleva la tasa de quema por excursiones intradiarias.
3. **Riesgo de bloqueo de capital:** En todos los perfiles de Topstep, entre el 50 % y el 67 % de las simulaciones terminan en estado **risk-blocked** (el colchon de perdida restante es menor que el riesgo minimo de 1 contrato micro), congelando la cuenta antes de los 30 dias.
