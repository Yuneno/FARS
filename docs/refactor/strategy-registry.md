# Registro Canónico de Versiones y Estrategias — FARS Engine

Fecha de actualización: 2026-09-14  
Rama: `bloque-b-reconciliacion`  
Base commit: `0f2fb5b` (merge de Bloque A1+A2)

---

## 1. Nota Formal de Corrección de Alcance (§5 del Plan Original)

> [!IMPORTANT]
> **CORRECCIÓN DE ALCANCE VERIFICADA EN CÓDIGO E HISTORIAL GIT:**  
> El plan original (§5) contemplaba la premisa de que existían *"dos implementaciones rivales con el mismo nombre"* para SMC-FVG y EMAS (un supuesto motor divergente entre ramas vs `main`).
> 
> **Esta premisa es estrictamente falsa.**  
> - La rama `feature/port-smcfvg-emas` (commit `50d0efc`, *"port SMC-FVG and EMAS strategies from kai-backtesting"*) y la rama `main` contienen **exactamente la misma implementación y lógica algorítmica**.
> - La única diferencia respecto a los commits posteriores (`a3c41eb` y `836b92b`) consiste en la adición opcional del parámetro `discrete_partial_contracts` (`src/backtest/smc_fvg.py` +2 líneas; `src/backtest/emas.py` +6 líneas) para modelar salidas parciales discretas (`floor(qty/2)`) con contratos enteros de futuros.
> - **Conclusión:** No existen motores rivales, bifurcaciones incompatibles ni versiones alternativas de código para SMC-FVG o EMAS.

---

## 2. Registro Canónico de Estrategias

| estrategia | código fuente | commit | parámetros | frecuencia | política de concurrencia | instrumento | datos | costes |
|---|---|---|---|---|---|---|---|---|
| **SMC-FVG** | `src/backtest/smc_fvg.py` | `a3c41eb` *(port de `50d0efc` + `discrete_partial_contracts`)* | `tp_rr=2.0`, `partial_fraction=0.5` en 1R, `move_stop_to_be=True`, `pending_order_wait_bars=5`, `cooldown_bars=5`, `discrete_partial_contracts=False` (o `True`) | Alta (~5,986 trades / 7.33 años ≈ 3.3 trades/día) | 1 posición a la vez (`allow_multiple_positions=False`), cooldown 5 barras M5 tras cierre | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`) | Bruto ($0) / Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) |
| **EMAS** | `src/backtest/emas.py` | `836b92b` *(port de `50d0efc` + `discrete_partial_contracts`)* | EMA fast=9, EMA slow=21, stop en swing opuesto, TP 2R con parcial 0.5 en 1R y BE; sin cooldown/wait; `discrete_partial_contracts=False` (o `True`) | Alta (~5,098 trades / 7.33 años ≈ 2.8 trades/día) | 1 posición a la vez (`allow_multiple_positions=False`) | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`) | Bruto ($0) / Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) — **Control Negativo** |
| **CRT-TBS** | `src/backtest/crt_tbs.py` | `a1ef293` | Champion: `fixed_rr=2.0`, `require_4h_bias=True`, `allow_m5_rearm=True`, `h4_bias_threshold=0.0`, `h1_displacement_mult=1.0`, confirmación M5 | Selectiva (~428 trades / 7.33 años ≈ 0.23 trades/día) | 1 posición a la vez (`allow_multiple_positions=False`) | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`) con contexto causal H1/H4 | Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) |
| **ORB** | `src/backtest/orb.py` | `e232344` | Experimental: rango 09:30–10:00 NY, stop extremo opuesto, TP 2R (`fixed_rr=2.0`), sin bias (`require_bias=False`), `fade=False`, fin entradas 16:00 NY, `max_hold=192` M5 (16h) | Diaria (~1,563 trades / 7.33 años ≈ 0.85 trades/día) | 1 posición a la vez (`allow_multiple_positions=False`) | MNQ ($2/pto) | Databento MNQ M5 canónico (`timestamp >= 2019-05-06`) | Fricción canónica ($4.00 RT/contrato = 2.0 pts, 0 slippage) |
| **CRT 4H (Kai)** | **AUSENTE LOCALMENTE** *(sin repo `reference/kai-backtesting`)* | N/A *(código fuente inaccesible)* | Parámetros originales no inspeccionables localmente; rango 4H reportado | n=372 trades reportados en `canonical-dataset.md` | Desconocida | MNQ | Dataset cortado post-2019-05-06 | Reportado en bruto sin comisiones ni slippage (WR 35.5%, PF 2.689, netR +391.9 R / exp +1.054 R) — **BLOQUEADO (`reportado-sin-revisión`)** |

---

## 3. Declaración de Bloqueo Formal: CRT 4H de Kai (Tarea B4)

> [!CAUTION]
> **BLOQUEO FORMAL — ESTRATEGIA CRT 4H DE KAI:**
> 1. **Ausencia de código fuente:** El repositorio original `reference/kai-backtesting` **no está disponible en el entorno local**. No existe implementación ejecutable ni código fuente rastreable localmente.
> 2. **Imposibilidad de verificación causal:** Dado lo anterior, el trade atípico individual del **`2020-05-13 (+286.92 R)`** —que representa una fracción masiva de la supuesta ganancia de CRT 4H— **no se puede auditar, verificar causalmente ni reproducir de forma determinista**.
> 3. **Clasificación de métricas:** Los valores reportados en `docs/refactor/canonical-dataset.md` para CRT 4H (`n=372`, `WR 35.5%`, `PF 2.689`, `+391.9 R` / `exp +1.054 R` / `~+1,054 R bruto` en reportes previos) quedan catalogados formalmente con el estado:  
>    **`reportado-sin-revisión`**.
> 4. **Prohibición de sustitución:** En estricto apego al mandato de auditoría, **no se permite sustituir dicho trade ni imputar sintéticamente operaciones alternativas**.
