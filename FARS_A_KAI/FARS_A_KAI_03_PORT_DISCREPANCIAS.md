# Reconciliation of Known Discrepancies: Kai Upstream vs. FARS Port

**Document:** `FARS_A_KAI_03_PORT_DISCREPANCIAS.md`  
**Audience:** Technical delivery for Kai (Architecture, Risk & Protocol Audit)  
**Status:** Canonical & Audited  
**Audit Sources:** `FARS_KAI_PORT_DIFF.md`, `lab_artifacts/smcob_protocol/GATE_ORDEN_PARCIALES.md`, `lab_artifacts/c2_protocol/HERMES_REVISION_C2.md`, `lab_artifacts/gross_vs_net_reconciliation.json`, `lab_artifacts/c3_protocol/HERMES_REVISION_C3.md`  

---

## 1. Executive Summary

During the porting and formal verification of the `kai-backtesting` strategy suite into the FARS production infrastructure, several discrepancies were identified between published research notes, documentation, and actual upstream source code. 

This document provides a factual, code-grounded audit of every verified discrepancy, explaining:
1. The exact technical divergence and its upstream origin.
2. How the issue was investigated, tested, and resolved in FARS.
3. Specific claims and metrics in circulation that have **no empirical evidence** and must not be used in production risk models.

---

## 2. Itemized Verified Discrepancies & Technical Resolutions

### 2.1 Discrepancy 1: Partial Fill Order (`tp1` before `target` in Same Bar)

* **Context & Bug Mechanism**: In strategies utilizing scaled exits (taking a partial at TP1, e.g. 1R, and moving the stop to Break-Even while leaving the remainder to run to Target), a bar can occasionally span a large range that sweeps both `tp1` and `target` without touching the stop. 
* **Upstream Finding**: In earlier upstream versions, the evaluation loop checked `hit_target` before `hit_tp1`, returning the full position size at the final target price. This omitted the partial booking at 1R, artificially inflating performance. Upstream commit `d370eb5` (*"corrige orden de fill de parciales (tp1 antes de tp) en resolve para SMC-OB, SMC-FVG y SMC-FVG-CE"*) resolved this. When upstream regenerated their metrics, performance dropped significantly:
  - **SMC-OB**: Swung from published $+336.2\text{ R}$ (PF 1.16) to **$-356.3\text{ R}$ (PF 0.90)**.
  - **Sapo**: Swung from published $+129.8\text{ R}$ (69.5% WR) to **$-261.8\text{ R}$ (PF 0.90)**.
* **FARS Audit & Resolution**:
  1. FARS inspected [`src/backtest/executor.py:951-965`](file:///E:/FARS-LAB/FARS/src/backtest/executor.py#L951-L965) and confirmed that the conservative logic is implemented:
     ```python
     elif hit_target:
         if not partial_taken and hit_tp1 and partial_fraction > 0.0:
             # Book partial first at TP1
             partial_qty = int(math.floor(current_qty * partial_fraction))
             ...
             current_qty -= partial_qty
         # Remainder exits at target
         reason, fill = "take_profit", target
     ```
  2. FARS audited 776 actual `break_even_stop` exits in `trades_MNQ.json`. All 776 recorded `stop_price == entry_price` and a median return of $+0.4109\text{ R} \approx 3/7\text{ R}$ (exactly matching $\lfloor \text{qty} \times 0.5 \rfloor$ booked at 1R and remainder at 0R).
  3. FARS implemented permanent regression tests in `tests/test_backtest_executor.py`:
     - `test_partial_tp1_before_target_same_bar_long`
     - `test_partial_tp1_before_target_same_bar_short`
     - `test_partial_tp1_before_target_same_bar_parity_with_sequential`
* **Status**: **RESOLVED & VERIFIED**. FARS operates strictly on the conservative, post-fix fill model.

---

### 2.2 Discrepancy 2: EMAS Target Ratio (`target_rr=0.4` vs `target_rr=3.0`)

* **Context & Upstream Divergence**: Early exploratory notes suggested configuring EMAS to `target_rr=0.4`, reporting a 70.9% pass rate in a 25k account (vs 33.0% with `target_rr=3.0`).
* **Upstream Reversal**: Upstream commit `e431df9` formally reverted EMAS to `target_rr=3.0`. Upstream README documentation acknowledged that the 0.4 configuration was a **phantom fill artifact**: the backtest was booking a 1R partial that market price never reached. When evaluated with honest fills and realistic costs at `target_rr=3.0`, EMAS produced **PF 0.89 and $-632.3\text{ R}$**.
* **FARS Audit & Resolution**:
  - In FARS Block C2 walk-forward evaluation, both configurations were evaluated out-of-sample across 518,237 canonical M5 bars:
    - `emas_baseline` (`target_rr=3.0`): $E[R] = +0.0201$, $\text{PF} = 1.040$, 50% positive folds, $DD = 47.7\text{ R}$.
    - `emas_rr_0_4`: $E[R] = -0.0247$, $\text{PF} = 0.916$, 25% positive folds, $DD = 96.2\text{ R}$.
  - FARS maintained `target_rr=3.0` as an explicit **negative control**. The 0.4 configuration was permanently rejected as an optimization artifact that violated risk-reward principles.
* **Status**: **RESOLVED**. Both teams agree that `target_rr=0.4` was an invalid fill artifact.

---

### 2.3 Discrepancy 3: SMC-FVG Risk Filter (`min_risk_pts=5.0` vs `min_risk_pts=8.0`)

* **Context & Divergence**: Initial notes from Kai v1 recommended shifting `min_risk_pts` from 8.0 down to 5.0 points.
* **Upstream Code vs. Text**: While some legacy text in the upstream repository mentioned 5.0 points, upstream commit `e431df9` explicitly locked the module defaults to `min_risk_pts=8.0` and `target_rr=1.5`. The code defaults governed production.
* **FARS Empirical Finding**:
  In FARS walk-forward evaluations under realistic per-leg friction:
  - `min_risk_pts=8.0`: $E[R] = +0.0882$, $\text{PF} = 1.195$, 100% positive folds, $DD = 31.8\text{ R}$.
  - `min_risk_pts=5.0`: $E[R] = +0.0679$, $\text{PF} = 1.143$, 87.5% positive folds, $DD = 70.4\text{ R}$. Under canonical flat costs, 5.0 pt collapsed to $-0.0777\text{ E[R]}$ and $451\text{ R}$ drawdown.
  - **Reason**: When stop loss distance is narrow (e.g. 5 points = $10 on MNQ), fixed transaction costs and bid-ask slippage consume an excessive fraction of the trade's R-multiple ($>10\%\text{ of R}$). An 8.0-point minimum risk filter preserves statistical edge against execution drag.
* **Status**: **ALIGNED**. Both FARS and Kai code baselines operate at `min_risk_pts=8.0`.

---

### 2.4 Discrepancy 4: CRT 4H Specification & Execution Bugs

* **Upstream Sources**: `strat_crt4h.py` and `kai_bt/live/live_crt4h_bridge.py`.
* **Two Specific Discrepancies Identified**:
  1. **Docstring Comment vs Code Formula for SL Pad**:
     - Upstream docstring stated: `sl_pad_frac: % of 5M sweep range`.
     - Actual upstream bridge code (`live_crt4h_bridge.py:106`):
       $$\text{pad} = |\text{extreme} - \text{close}(C1)| \times \text{sl\_pad\_frac}$$
     - *Resolution*: FARS ported the exact calculation from the code (`live_crt4h_bridge.py:106`) into `src/backtest/crt4h.py`. The docstring comment was documented as imprecise.
  2. **Single-Attempt MSS Bug**:
     - Early upstream implementations attempted displacement confirmation on a single candle following the sweep; if unconfirmed, the setup was discarded immediately, yielding 0 trades.
     - Upstream resolved this by holding an "armed state" across `max_hold_bars`.
     - *Resolution*: FARS implemented the multi-bar armed retry state from inception.
* **Empirical Outcome in FARS**:
  - CRT 4H generated 175 OOS trades across 2019–2026.
  - Performance: $E[R] = -0.1406$, $\text{PF} = 0.838$, only 25% positive folds.
  - *Conclusion*: Confirms Kai's own `RANKING.md` assessment: with an average of only 0.13 trades/day, the strategy cannot generate sufficient sample frequency to overcome friction.
* **Status**: **PORTED FAITHFULLY & CONFIRMED UNVIABLE**.

---

### 2.5 Discrepancy 5: Cost Accounting & Fee Models

* **Upstream Discrepancy**: Multiple conflicting round-turn (RT) commission values existed in upstream documentation and scripts:
  - `$1.42 RT` per contract in `README.md` and `RANKING.md`.
  - `$1.00 RT` per contract in `research/silverbullet.py:41`.
  - `$1.34 RT` per contract in the live execution backend.
* **FARS Resolution**: FARS established a transparent, per-leg friction architecture (`por_tramo`):
  - **Exchange/NFA/Broker Commission**: $\$0.62$ per side ($\$1.24\text{ RT}$) for micro contracts (MNQ/MES), with $\$1.42\text{ RT}$ maintained as an explicit Kai-reconciliation sensitivity scenario.
  - **Slippage Accounting**: 1 tick ($0.25\text{ pt}$) slippage applied **strictly to crossing orders** (MARKET entries and STOP loss exits). Passive LIMIT orders and TAKE_PROFIT limit executions incur **zero slippage**.
  - Flat models (e.g. legacy $\$4.00\text{ RT}$ penalizing passive limit orders) were deprecated.
* **Status**: **STANDARDIZED & AUDITED**.

---

### 2.6 Discrepancy 6: The "79.4% Pass Rate" EMAS + SMC-FVG Portfolio

* **Context**: Document `ISMA_STRATEGIES_AND_RISK_MODELS.md` (dated August 21, 2026) cited a $79.4\%$ combine pass rate for an EMAS + SMC-FVG multi-strategy allocation.
* **The Ghost Artifact**: Upstream commit `e1f795e` established that this figure was calculated prior to the revert of EMAS `target_rr=0.4`. Because the 79.4% number relied directly on the phantom fill bug, it was rendered completely invalid.
* **FARS Protocol Action**: FARS issued a strict research directive: **Citing the 79.4% pass figure is strictly prohibited across all FARS artifacts.** Any future portfolio combinations must be re-evaluated under verified post-fix parameters.
* **Status**: **RETRACTED & PROHIBITED**.

---

## 3. Inventory of Unsubstantiated Claims (Things Without Evidence)

The following claims have circulated informally but have **zero empirical backing** in code or verified data:

| Unsubstantiated Claim | Empirical Finding / Code Reality | Status in FARS |
|:---|:---|:---|
| *"SMC-OB has positive edge on Gold (MGC) and Dow (MYM)."* | Multi-market protocol evaluated SMC-OB on M5 canonical data: **MYM $E[R] = -0.0300$ (FAIL)**; **MGC $E[R] = -0.0880$ (FAIL)**. Edge exists solely on MNQ ($+0.0724\text{ R}$). | **REJECTED**: Multi-market hypothesis failed. |
| *"Kai backtest numbers can be compared 1:1 with FARS numbers."* | Kai backtests span 2010–2026 including a pre-2019 synthetic backfill and variable cost decks. FARS uses continuous Databento tick-derived M5 data strictly from 2019-05-06 onward. | **INVALID COMPARISON**: Datasets and cost models differ. |
| *"SMC-FVG has no statistical edge."* | In FARS walk-forward testing under realistic costs, SMC-FVG 8.0 pt achieves $E[R] = +0.0882$, $\text{PF} = 1.195$, 95% bootstrap CI excluding zero, and 100% positive folds. | **FALSE**: Edge is positive; strategy is constrained by drawdown ($31.8\text{ R}$ vs $12\text{ R}$ limit), requiring sizing/risk models. |
| *"Reversion strategies can run without tick or M1 granularity."* | Order flow sweeps and reclaim dynamics on M5 wicks suffer severe execution path dependency. | **UNPROVEN**: Requires M1/tick replay parity before production promotion. |

---

## 4. Reconciled Specification Reference

| Component / Parameter | Kai Upstream (`4a8605e`) | FARS Canonical (`main`) | Production Verdict |
|:---|:---:|:---:|:---:|
| **SMC-FVG `target_rr`** | 1.5 | 1.5 | **Identical** |
| **SMC-FVG `min_risk_pts`** | 8.0 pt | 8.0 pt | **Identical** |
| **SMC-FVG `swing_w`** | 5 bars | 5 bars | **Identical** |
| **EMAS `target_rr`** | 3.0 (reverted from 0.4) | 3.0 | **Identical** (Negative Control) |
| **Partials Execution Order** | `tp1` before `target` | `tp1` before `target` | **Identical** (Conservative) |
| **CRT 4H SL Pad** | Code formula (`close(C1)`) | Code formula (`close(C1)`) | **Identical** |
| **CRT 4H MSS State** | Multi-bar armed retry | Multi-bar armed retry | **Identical** |
| **Slippage on Limits** | 0.0 ticks | 0.0 ticks | **Identical** |
| **Dataset Starting Cutoff** | 2010 (synthetic backfill) | **2019-05-06 (Canonical Databento)** | **FARS Standard Governs** |
