# GATE v2 — orden de fill de parciales (`tp1` antes de `tp`) en el port SMC-OB

**Fecha:** 2026-09-16 · **Autor:** Hermes (verificación independiente) · **Disparador:** el update
upstream de Kai (`4a8605e`) publica que **SMC-OB y Sapo cambiaron de signo** tras su fix de orden de
parciales, mientras nuestro port seguía reportando PASS. Cierra el punto 7 de
`E:\FARS-LAB\FARS_KAI_PORT_DIFF.md` (v2).

## Qué cambió upstream

Commit `d370eb5` (nuestra base de port, 2026-08-21) — *«corrige orden de fill de parciales (tp1 antes
de tp) en resolve para SMC-OB, SMC-FVG y SMC-FVG-CE»*. Semántica del fix: si una vela toca el **target
final**, el parcial de `tp1` ya se cruzó por el camino → hay que **contabilizarlo primero** y el resto
al target. Antes, esa rama devolvía el **tamaño completo al target** (parcial no cobrado) → sesgo
optimista. Su README v2 (tras regenerar): SMC-OB **PF 1,16 / +336,2 R → PF 0,90 / −356,3 R**.

## Verificación en nuestro motor (`605c9dd`)

1. **La rama existe y en el orden correcto** — `src/backtest/executor.py:951-965`
   (`elif hit_target:` → si `not partial_taken and hit_tp1 and partial_fraction > 0`, se cobra el
   parcial **antes** de `reason, fill = "take_profit", target`). La misma rama se repite en el camino
   resuelto por M1 (`:918-931`). **No hay rama que devuelva tamaño completo sin cobrar el parcial.**
2. **El parcial se cobró de verdad en la corrida del protocolo** (`trades_MNQ.json`): de 776 salidas
   `break_even_stop`, las 776 tienen `stop_price == entry_price` y R mediana **+0,4109 ≈ 3/7 R** — es
   decir, `floor(qty·0,5)` contratos cobrados a 1R y el resto al BE. Con `qty` de 2 a 82 contratos
   (sizing por riesgo), el parcial **no** es degenerado.
3. **Contrafactual medido del bug** (si nuestro motor tuviera la semántica pre-fix): los 285 trades
   de `take_profit` ganarían **+0,2427 R cada uno** → efecto agregado **+0,0394 R por trade sobre
   n = 1.755**.

## Veredicto

**GATE CERRADO: el PASS de SMC-OB en MNQ (+0,0724 R) NO depende del bug de orden de parciales.**
Al contrario: con la semántica pre-fix habría medido ≈ **+0,112 R** — el fix nos **cuesta** 0,039 R,
así que nuestro número está del lado **conservador** de esa discrepancia. La candidata MNQ5+MGC3 no
se cae por este gate.

Métricas del port (`resultados.json`, sin cambios): MNQ E[R] **+0,07236**, netR +126,99, **PASS**
(IC excluye cero) · MYM −0,0300 **FAIL** · MGC −0,0880 **FAIL**.

## Hallazgo abierto (no bloquea, pero es real)

**No hay ningún test que cubra el camino `tp1` + `target` en la misma vela**: `grep -n "tp1"
tests/test_backtest_executor.py` → **0 coincidencias**, y es exactamente el camino que tocó el fix
upstream. La evidencia de arriba es lectura de código + huella en los artefactos, no un test de
regresión. → **Acción pendiente (barata):** añadir un test al executor que verifique, en una vela que
toca `tp1` y `target` a la vez, que `booked_pnl = q_tp1·1R` y el resto sale al target; y que con
`discrete_partial_contracts=True` y `qty=1` el parcial es degenerado (`q_tp1=0`) sin romper el BE.

## Comparabilidad (declarado)

Sus números (MNQ 2010-2026, datos con tramo pre-2019 sintético, su escalera de coste) y los nuestros
(canónico post-2019, coste por pata) **no son comparables 1:1**. Coincidimos en signo y magnitud
«edge fino», que es lo relevante. Nada de esto se cita como nuestro resultado.
