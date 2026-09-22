# Implementación: MNQ Priced-Intent Bridge (Offline Only) — Corrección F2

## 1. Archivos Tocados

- `src/realtime/priced_intents.py`:
  - `round_mnq_prices_conservative`: Actualizada la política de redondeo para `entry` a múltiplos de tick de MNQ (`0.25`), eliminando el riesgo de subestimación detectado en F1:
    - Para `LONG`: `entry = ceil(raw_entry / tick) * tick`; stop `floor(raw_stop / tick) * tick`; target `floor(raw_target / tick) * tick`.
      - Efecto: la distancia de stop (`entry - stop`) se expande o preserva, asegurando estrictamente que `rounded_risk >= raw_risk`.
    - Para `SHORT`: `entry = floor(raw_entry / tick) * tick`; stop `ceil(raw_stop / tick) * tick`; target `ceil(raw_target / tick) * tick`.
      - Efecto: la distancia de stop (`stop - entry`) se expande o preserva, asegurando estrictamente que `rounded_risk >= raw_risk`.
    - Validación de polaridad post-redondeo mantenida intacta (`stop < entry < target` en LONG; `target < entry < stop` en SHORT) con cláusulas de seguridad anti-colapso.
    - Docstrings actualizados para documentar la política integral.
  - `MnqPricedIntentBridge.create_intent`:
    - Se reordenó la marcación de consumo: `self._consumed.add(key)` ahora ocurre **estrictamente después** de que `OrderIntent` se construye y `require_order_intent(intent)` valida el evento. Devuelve el intent validado. Si ocurre un fallo en la construcción/validación, el contexto no queda marcado como consumido espuriamente.

- `src/realtime/__init__.py`:
  - Intencionalmente **no modificado** para prevenir importaciones circulares: `src.backtest.history` importa conectores de `src.realtime`, por lo que mantener `priced_intents.py` como módulo independiente importable directamente preserva la separación limpia de dependencias.

- `tests/realtime/test_priced_intents.py`:
  - Se añadieron las pruebas de regresión F2 obligatorias (12 tests en total):
    - `test_conservative_entry_rounding_regressions_long_and_short`: Demuestra con exactitud que la política previa reducía el riesgo de `0.37` a `0.25` pts en los casos LONG (`100.37`, `100.00`, `101.00`) y SHORT (`99.63`, `100.00`, `99.00`), y valida que con la política F2 el riesgo redondeado (`0.50` pts) cumple `rounded_risk >= raw_risk` con polaridad estricta.
    - `test_consumed_only_after_require_order_intent_succeeds`: Demuestra que ante una excepción en la validación del intent, el contexto permanece intacto en `_contexts` y no es consumido, permitiendo reintentos o manejo de errores sin pérdida de estado previo.

---

## 2. Diseño e Invariantes Fail-Closed (Post-F2)

1. **Causalidad Estricta y Sin Lookahead:**
   - La evaluación de la estrategia en `MnqPricedStrategyAdapter` se realiza estrictamente barra a barra sobre eventos canónicos cerrados.
   - No se realiza búsqueda temporal ambigua ni se accede a datos futuros.

2. **Vinculación Unívoca de Identidad y Consumo Post-Validación:**
   - El contexto de precios queda registrado bajo la clave canónica `(signal.source, signal.event_id)`.
   - `MnqPricedIntentBridge.create_intent()` exige que `RiskDecision` referencie exactamente dicha señal (`signal_source` y `signal_id`).
   - El origen de la señal y de la decisión de riesgo deben coincidir (`decision.origin == signal.origin`). Sin embargo, la identidad/origin entre señal y RiskDecision no demuestra un binding independiente de origin dentro de `PricingContext` (tratado como hardening diferido).
   - El consumo del contexto (`self._consumed.add(key)`) ocurre secuencialmente tras el éxito de `require_order_intent(intent)` para evitar consumo espurio ante excepciones en la validación; esto no demuestra atomicidad concurrente (diseñado para ejecución síncrona en el pipeline).

3. **Garantía Matemática de No-Subestimación del Riesgo:**
   - Para LONG: $\text{entry}_{\text{rounded}} \ge \text{entry}_{\text{raw}}$ y $\text{stop}_{\text{rounded}} \le \text{stop}_{\text{raw}} \implies \Delta_{\text{rounded}} \ge \Delta_{\text{raw}}$.
   - Para SHORT: $\text{stop}_{\text{rounded}} \ge \text{stop}_{\text{raw}}$ y $\text{entry}_{\text{rounded}} \le \text{entry}_{\text{raw}} \implies \Delta_{\text{rounded}} \ge \Delta_{\text{raw}}$.
   - El redondeo a ticks garantiza que la distancia nominal al stop en puntos/dólares no se reduzca durante el pre-check del gate de `$200`. No obstante, esta garantía de redondeo no demuestra que todo el riesgo de ejecución o costes reales esté cubierto (slippage, gaps de mercado, comisiones y desvíos de ejecución en broker quedan fuera de este cálculo estático).

4. **Invariantes Fail-Closed:**
   - Símbolo: con la configuración por defecto se rechazan símbolos distintos de `"MNQ"`. El constructor permite configurar `symbol`; solo MNQ con tick 0.25 y 2 USD/punto está dentro del alcance probado.
   - Precios: Deben ser números finitos, positivos y mayores a cero.
   - Polaridad de Brackets: En LONG se requiere `stop < entry < target`; en SHORT se requiere `target < entry < stop`.
   - Contexto Faltante: Si no existe contexto de precios registrado para la señal, se eleva `ValueError`.
   - Duplicados Conflictivos: Si se intenta registrar una señal idéntica con precios o tamaño diferentes, se eleva `ValueError`. Duplicados idénticos son idempotentes.
   - Reuso: Un contexto consumido para generar un `OrderIntent` no puede volver a ser utilizado; un segundo intento eleva `ValueError`.
   - Autorización: Requiere decisión de riesgo explícitamente aprobada (`is_authorized(decision) is True`).

5. **Separación de Responsabilidades:**
   - `default_intent_factory` permanece priceless y compatible, garantizando que una sesión paper sin bridge de precios continúe fallando en el adapter de práctica (`MISSING_ENTRY_OR_STOP_PRICE`).
   - `PracticeExecutionAdapter` retiene la responsabilidad exclusiva de:
     - Clamp a 1 micro (`CLAMPED_TO_1_MICRO`).
     - Límite de riesgo por orden (`$200.00` máximo; si 1 micro supera `$200.00`, rechaza con `MAX_RISK_PER_ORDER`).
     - Allowlist de cuentas (`PRAC-V2-673085-85699223` / `27765990`).
     - Horario de mercado CME y cutoff diario (16:45 CT).
     - Despacho y construcción de brackets para el gateway de TopstepX/ProjectX.

---

## 3. Pruebas y Resultados Exactos

### 3.1 Suite Específico `tests/realtime/test_priced_intents.py` (12/12 PASS)

1. `test_default_factory_priceless_rejected_by_practice_adapter`: PASS
2. `test_smc_fvg_synthetic_produces_exact_and_tick_validated_intent`: PASS
3. `test_causal_parity_backtest_vs_realtime_adapter`: PASS
4. `test_long_and_short_signals_with_correct_bracket_polarity`: PASS
5. `test_fail_closed_on_identity_origin_symbol_and_action_mismatch`: PASS
6. `test_fail_closed_on_missing_conflicting_or_reused_context`: PASS
7. `test_size_greater_than_one_clamped_to_one_micro`: PASS
8. `test_risk_exceeding_max_risk_vetoed_on_both_paths`: PASS
9. `test_risk_within_limit_accepted_and_brackets_verified`: PASS
10. `test_offline_session_run_with_priced_bridge`: PASS
11. `test_conservative_entry_rounding_regressions_long_and_short` (F2): PASS
12. `test_consumed_only_after_require_order_intent_succeeds` (F2): PASS

Salida real:
```
"E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/realtime/test_priced_intents.py -q
............                                                             [100%]
============================= 12 passed in 0.63s ==============================
```

### 3.2 Suite de Tiempo Real `tests/realtime` (322/322 PASS)

Salida real:
```
"E:/FARS-LAB/.venv-fars/Scripts/python.exe" -m pytest tests/realtime -q
........................................................................ [ 22%]
........................................................................ [ 44%]
........................................................................ [ 67%]
........................................................................ [ 89%]
..................................                                       [100%]
============================= 322 passed in 2.52s =============================
```

*(La ejecución de la suite completa se reservó para Hermes según la directiva F2).*

---

## 4. Limitaciones y Hardening Diferido

- **Fixture de Fontanería Únicamente:** La estrategia `SmcFvgStrategy` con sus parámetros default (`MNQ M5`, `f=0.5`, `swing_w=5`, `target_rr=1.5`, `wait=48`, `min_risk_pts=8.0`, `cooldown=6`) se utiliza de forma exclusiva como fixture estructural para probar el transporte de precios.
- **Sin Pretensión de Edge o Rentabilidad:** No existe evidencia de rentabilidad ni estrategia promovible; las pruebas históricas previas perdieron validez tras el fix de fill-bar y los resultados limpios son negativos.
- **100% Offline:** Este bloque no habilita trading en vivo, no inicia conexión a brokers, no ejecuta `fars-projectx doctor`, no altera credenciales en `.env` y mantiene `LIVE_EXECUTION_ENABLED = False`.
- **Hardening Diferido (NO blockers reabiertos):**
  - **Historial y contextos sin cota:** `_history` en `MnqPricedStrategyAdapter` y `_contexts`/`_consumed` en `MnqPricedIntentBridge` crecen en memoria sin poda ni ventana deslizante durante la ejecución.
  - **Deduplicación de barras:** `MnqPricedStrategyAdapter.on_event()` apendiza cada barra sin verificar duplicidad previa de secuencias/timestamps.
  - **Origin de PricingContext:** `PricingContext` no almacena un campo `origin`; falta vincular independientemente el origen del contexto. La comparación entre `Signal` y `RiskDecision` sí existe.
  Estos puntos quedan registrados como hardening diferido para futuras iteraciones de robustez, no como bloqueadores del cierre offline actual.

---

## 5. `git status --short` Final

```
 M lab_artifacts/rt9_protocol/acceptance_mock_report.json
 M lab_artifacts/rt9_protocol/acceptance_mock_session.jsonl
?? lab_artifacts/_tmp_hermes_verify/
?? lab_artifacts/mnq_intent_bridge/
?? lab_artifacts/rt9_protocol/acceptance_live_session.jsonl
?? src/realtime/priced_intents.py
?? tests/realtime/test_priced_intents.py
```

*Nota: Los archivos modificados y untracked de `rt9_protocol` y `_tmp_hermes_verify` corresponden al baseline previo y se mantuvieron intactos sin modificaciones.*

---

## 6. Bloqueos Reales

- Ninguno. Las correcciones F2 solicitadas por Muse y Hermes están implementadas, probadas y documentadas offline.
