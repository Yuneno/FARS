# Combinatorial Purged Cross-Validation (CPCV), PBO, and DSR in FARS

**Document:** `FARS_A_KAI_02_CPCV_PBO_DSR.md`  
**Audience:** Technical delivery for Kai (Quant Research & Risk Engineering)  
**Status:** Canonical & Audited  
**Repository Source:** `E:\FARS-LAB\FARS\src\cpcv.py`, `lab_artifacts/c3_protocol/`  

---

## 1. Context & Methodological Purpose

In quantitative strategy research, standard $K$-fold cross-validation fails because financial time series violate the Independent and Identically Distributed (I.I.D.) assumption. Standard cross-validation leaks information across folds via three primary transmission channels:
1. **Serial Correlation**: Persistence in volatility, order flow, and momentum regimes across adjacent periods.
2. **Holding-Time Overlap**: Trades entered in a training set that remain open across an evaluation boundary, contaminating the out-of-sample test partition.
3. **Selection Bias & Multiple Testing**: Selecting the top performer from $M$ strategy trials inflates backtest metrics via selection overfit.

To provide rigorous selection-overfit diagnostics without compromising the causal integrity of walk-forward evaluation, FARS implements **Combinatorial Purged Cross-Validation (CPCV)** and computes the empirical **Probability of Backtest Overfitting (PBO)** alongside the **Deflated Sharpe Ratio (DSR)** framework.

> **Operational Distinction in FARS**: CPCV is executed strictly on frozen, chronologically indexed trade outcomes. It **never** trains or fits strategy parameters, and it **never** replaces causal out-of-sample walk-forward backtesting. Its exclusive mission is selection-overfit diagnostic and dispersion measurement.

---

## 2. Mathematical Formulation & Architecture (`src/cpcv.py`)

The implementation in `src/cpcv.py` adheres strictly to the methodology developed by Marcos López de Prado and David H. Bailey (2014, *The Journal of Computational Finance*):

### 2.1 The Observation Model (`CpcvObservation`)

A trade observation represents a discrete, closed trade mapped to the canonical global bar index:

```python
@dataclass(frozen=True)
class CpcvObservation:
    entry_idx: int
    exit_idx: int
    r_result: float
```
- **Invariants**: $0 \le \text{entry\_idx} \le \text{exit\_idx}$ and $\text{r\_result} \in (-\infty, +\infty)$ is finite.

### 2.2 Contiguous Group Partitioning (`contiguous_groups`)

The complete timeline of $T$ bars is divided into $N$ contiguous, chronological groups $[S_g, E_g)$ for $g \in \{0, \dots, N-1\}$. The group sizes differ by at most one bar:
$$\Delta_g = E_g - S_g = \lfloor T / N \rfloor + \mathbf{1}_{\{g < (T \bmod N)\}}$$

```python
def contiguous_groups(n_items: int, n_groups: int) -> tuple[tuple[int, int], ...]:
    quotient, remainder = divmod(n_items, n_groups)
    groups = []
    start = 0
    for group_id in range(n_groups):
        size = quotient + (1 if group_id < remainder else 0)
        groups.append((start, start + size))
        start += size
    return tuple(groups)
```

### 2.3 Combinatorial Test Paths

For a choice of $k$ test groups out of $N$, the algorithm generates $\binom{N}{k}$ distinct combinatorial paths:
$$P = \binom{N}{k} = \frac{N!}{k!(N-k)!}$$
For $N = 12$ and $k = 3$:
$$P = \binom{12}{3} = \frac{12 \times 11 \times 10}{3 \times 2 \times 1} = 220 \text{ distinct evaluation paths}$$

Each path defines:
- **Test Set**: The union of intervals of the $k$ chosen test groups.
- **Candidate Train Set**: The union of intervals of the remaining $N - k$ groups.

---

## 3. Purging and Embargo Mechanics (`split_path_observations`)

To ensure absolute independence between train and test sets for every path, `split_path_observations` applies two rigorous spatial filters:

```mermaid
flowchart LR
    subgraph Timeline
        direction LR
        Train1[Train Group]
        Purged[Purged Zone<br/>Overlapping Trades]
        Test[Test Group k]
        Embargo[Embargo Zone<br/>h = 192 bars]
        Train2[Train Group]
    end
    Train1 --> Purged --> Test --> Embargo --> Train2
```

### 3.1 Purging (Holding Period Overlap)

A trade whose entry occurs prior to a test interval but whose holding duration extends into or across that test interval contains information shared between train and test. 

For any observation $j$ not inside the test intervals, it is **purged** from the training set if:
$$\exists [t_{\text{start}}, t_{\text{end}}) \in \text{TestIntervals} \quad \text{such that} \quad \text{entry}_j < t_{\text{end}} \quad \text{and} \quad \text{exit}_j \ge t_{\text{start}}$$

This guarantees that zero market exposure from training trades overlaps with any portion of the test evaluation window.

### 3.2 Embargo (Post-Test Autocorrelation Buffer)

Because financial markets exhibit memory (e.g. volatility clustering, autoregressive order flow), trades executed immediately following a test period may reflect conditions correlated with that test period.

An embargo window of $h$ bars is erected immediately following the end of each test group:
$$\text{EmbargoInterval}_g = \left[t_{\text{end}, g},\ \min\left(T,\ t_{\text{end}, g} + h\right)\right)$$

Any training trade with $\text{entry}_j \in \text{EmbargoInterval}_g$ is discarded from the training set.

### 3.3 Audit Ledger

For every path and configuration, an audit ledger records the exact counts:
- `candidate_train_trades`
- `retained_train_trades`
- `test_trades`
- `purged_train_trades`
- `embargoed_train_trades`

---

## 4. Selection, Ranking, and Logit Log-Odds

For each path $p \in \{1, \dots, P\}$:

### 4.1 In-Sample Winner Selection
1. Evaluate performance metrics across retained training trades for all $M$ configurations:
   - Primary metric: Mean net return $E[R] = \frac{1}{n}\sum_{i=1}^n R_i$ (`mean_r`).
   - Secondary metric: Sample Sharpe ratio $\frac{\bar{R}}{s_R}$ with $ddof=1$ (`sharpe_like`).
2. Identify the in-sample winning configuration:
   $$c^*_{\text{train}} = \arg\max_{c \in \{1, \dots, M\}} \text{Metric}_{\text{train}}(c)$$
   *(Ties are broken deterministically using alphabetical configuration ID).*

### 4.2 Out-of-Sample Ranking
1. Evaluate test performance metrics for all $M$ configurations on the out-of-sample test partition.
2. Rank all $M$ configurations by their out-of-sample metric:
   $$\text{Rank}_{\text{OOS}}(c) \in \{1, \dots, M\}$$
   where Rank 1 is the worst performer and Rank $M$ is the best performer.
3. Extract the out-of-sample rank of the in-sample winner:
   $$r^* = \text{Rank}_{\text{OOS}}\left(c^*_{\text{train}}\right)$$

### 4.3 Normalized Rank $\omega$ & Logit Score $\lambda$
Following Bailey & López de Prado, the rank is converted into a normalized relative percentile $\omega \in (0, 1)$:
$$\omega = \frac{r^* - 0.5}{M}$$
The log-odds logit transform $\lambda$ is computed:
$$\lambda = \text{logit}(\omega) = \ln\left(\frac{\omega}{1 - \omega}\right)$$

### 4.4 The Overfitting Condition
A selection trial is deemed to suffer from selection overfitting if the in-sample winner performs below the median out-of-sample:
$$\text{below\_median} \iff r^* < \frac{M + 1}{2} \iff \omega < 0.5 \iff \lambda < 0.0$$

---

## 5. Probability of Backtest Overfitting (PBO)

The Probability of Backtest Overfitting (PBO) is defined empirically as the fraction of all combinatorial paths where the in-sample optimal configuration fails out-of-sample (ranks below the cross-sectional median):

$$\text{PBO} = \frac{1}{P} \sum_{p=1}^P \mathbf{1}_{\{\lambda_p < 0.0\}}$$

In `src/cpcv.py`, PBO is computed for both:
- **Primary PBO**: Evaluated using $E[R]$ (`mean_r`).
- **Secondary PBO**: Evaluated using Sharpe-like ratio (`sharpe_like`).

---

## 6. Empirical Results in FARS (Block C3 Audit)

The canonical parameters and empirical results from FARS Block C3 (`lab_artifacts/c3_protocol/cpcv_pbo.json`) are detailed below:

### 6.1 Setup & Parameters

| Parameter | Value | Description |
|:---|:---|:---|
| $N$ (Total Groups) | 12 | Contiguous chronological groups over 518,237 M5 bars (2019–2026) |
| $k$ (Test Groups) | 3 | Simultaneous test groups per path |
| Total Paths $P$ | $\binom{12}{3} = \mathbf{220}$ | Complete combinatorial path exploration |
| Embargo $h$ | **192 bars** | Exactly 1 trading day (16 hours $\times$ 12 bars/hour on M5) |
| Universe ($M$) | 7 configurations | Competing strategies across SMC, EMAS, and CRT families |

#### Strategy Universe Evaluated ($M=7$)
1. `crt4h_defaults`: CRT 4H baseline
2. `emas_baseline`: EMAS with `target_rr=3.0`
3. `emas_rr_0_3`: EMAS variation `target_rr=0.3`
4. `emas_rr_0_4`: EMAS variation `target_rr=0.4`
5. `smc_fvg_baseline`: SMC-FVG baseline with `min_risk_pts=8.0`
6. `smc_fvg_risk_10`: SMC-FVG with `min_risk_pts=10.0`
7. `smc_fvg_risk_5`: SMC-FVG with `min_risk_pts=5.0`

### 6.2 Empirical Results Table

| Diagnostic Metric | Primary ($E[R]$) | Secondary (Sharpe-like) |
|:---|:---:|:---:|
| **PBO ($\lambda < 0.0$)** | **0 / 220 = 0.0%** | **0 / 220 = 0.0%** |
| Logit Distribution Median | 1.2993 | 1.2993 |
| Logit Distribution Mean | 1.1154 | 1.2862 |
| Logit Distribution Std Dev ($\sigma$) | 0.5745 | 0.6291 |
| Logit Min / Max | [0.0000, 2.5649] | [0.0000, 2.5649] |
| Selected OOS Metric Median | +0.0989 R | +0.1042 |
| Selected OOS Metric Mean | +0.0968 R | +0.1002 |
| Selected OOS Metric Min / Max | [−0.0319 R, +0.1751 R] | [−0.0326, +0.1805] |

> **Historical Note on Pre-Fix Numbers**: In early exploratory passes prior to Block E5 bug resolutions (e.g. phantom fills and edge-order bugs), PBO was reported at 0.91% (2/220) primary and 1.36% (3/220) secondary. Following the resolution of executor fill bugs, the post-fix rerun recorded **0 / 220 = 0.0%** across all 220 paths.

---

## 7. Deflated Sharpe Ratio (DSR) & Multiple Testing

### 7.1 Analytical Formulation

The Deflated Sharpe Ratio (Bailey & López de Prado 2014) addresses the inflation of estimated Sharpe ratios as a function of the number of independent trials $M$, sample length $T$, variance across trials, and non-normality:

$$\text{DSR} \equiv \Phi\left( \frac{(\widehat{SR} - SR^*) \sqrt{T - 1}}{\sqrt{1 - \widehat{\gamma}_3 \widehat{SR} + \frac{\widehat{\gamma}_4 - 1}{4} \widehat{SR}^2}} \right)$$

where:
- $\Phi(\cdot)$ is the standard normal cumulative distribution function.
- $\widehat{SR}$ is the annualized sample Sharpe ratio.
- $\widehat{\gamma}_3, \widehat{\gamma}_4$ are the sample skewness and kurtosis of returns.
- $SR^*$ is the expected maximum Sharpe ratio under the null hypothesis ($H_0: SR = 0$) across $M$ independent trials:
  $$SR^* \approx \sqrt{2 \ln M} + \frac{\gamma}{\sqrt{2 \ln M}} \quad (\gamma \approx 0.5772 \text{ is the Euler-Mascheroni constant})$$

### 7.2 FARS Architecture: Coupling CPCV with Non-Parametric Bootstrap

While closed-form DSR provides useful theoretical intuition, it relies on asymptotic approximations that can become unreliable under structural market regime shifts or non-stationary volatility. 

In FARS, overfit defense is implemented through a two-pillar architecture:
1. **Combinatorial Empirical Dispersion (CPCV/PBO)**: Directly maps the empirical distribution of out-of-sample ranks across 220 cross-validated paths without parametric assumptions (`src/cpcv.py`).
2. **Family-Wise Error Rate Control (Gate 7 Bootstrap)**: Implemented in `src/bootstrap.py`, this module subjects strategy candidates to moving-block and stationary bootstrap resamples across macro market regimes (Welch's t-test on return thirds, variance-ratio tests, and Bonferroni/FWER adjustments) to verify that the edge is statistically distinct from zero across independent trials.

---

## 8. How to Reproduce & Verify

### 8.1 Running Unit Tests
To verify the mathematical invariants of `src/cpcv.py` (group partitioning, overlap purging, embargo buffers, logit calculation):

```powershell
E:\FARS-LAB\.venv-fars\Scripts\python.exe -m pytest tests/test_cpcv.py -v
```

Expected output: `3 passed in < 0.5s`.

### 8.2 Reproducing Full CPCV/PBO Metrics
To regenerate the complete 220-path CPCV evaluation across the canonical 2019–2026 Databento M5 dataset:

```powershell
E:\FARS-LAB\.venv-fars\Scripts\python.exe lab_artifacts/run_c3_protocol.py
```

The output JSON will be written to `lab_artifacts/c3_protocol/cpcv_pbo.json`, containing the full path ledger, audits, logit arrays, and OOS metric distributions.
