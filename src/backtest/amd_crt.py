"""Causal AMD+CRT (no-EMA) confluence strategy for the FARS backtest.

Ports the detection rules of the MNQ executor
``research/paper_trading/amd_crt_confluence_trader.py`` **without** the temporal
look-ahead of its validating backtest (which filtered CRT by whole-day
``date.isin(crt_days)`` — a CRT that confirms at 15:00 would retroactively count
for a 09:35 AMD entry).

Here both confirmations are evaluated causally, bar by bar, using only bars up
to the evaluation bar:

* **AMD** — today's pre-NY range (00:00-09:30 ET) is "compressed" (its amplitude
  is below the weekday median of PRIOR days only) and then swept-and-closed-inside
  within 09:30-10:30 ET. A sweep of the pre-NY high -> fade SHORT; a sweep of the
  pre-NY low -> fade LONG.
* **CRT** — yesterday's RTH (09:30-16:00 ET) PDH/PDL is swept-and-closed-inside at
  ANY moment of today's RTH, using bars up to the evaluation bar only.

The entry fires on the first bar where BOTH confirmations are available, in
whatever order they arrive. If AMD confirms but CRT never does by end of day, the
signal expires (the executor emits no trade). One trade per day maximum.

The strategy emits a :class:`~src.backtest.strategy.Signal` whose ``stop`` and
``target`` are **distances in points** (positive), not absolute prices; the
executor resolves them against the actual entry fill via its
``stop_target_as_points`` flag. ``entry`` is unused (the executor fills at the
next bar's open).

SL/TP (candidate rules, not re-tuned): ``SL = min(ATR(period) * 2.0, 50pts)``,
``TP = SL * 2.0``. ATR is the classic True Range smoothed with an exponential
moving average (alpha = 1/period) over RTH bars — matching ``compute_current_atr``
in the MNQ executor, whose period is ``atr_period`` (default 14).

Calibration and session conventions (documented assumptions):
* ``mnq_extended_m5.csv`` contains approximately 54% proxy data. It must not be
  used to declare a definitive baseline or a final AMD+CRT+EMA comparison.
* The weekday median is computed over full-session amplitudes (high-low) of PRIOR
  days only (the current day is excluded), using the most recent bounded block
  once ``median_lookback_days`` sessions are available (default 260, matching the
  candidate's minimum D1 history requirement). A weekday is only usable once it
  has ``min_weekday_samples`` prior days.
* Provider D1 bars are unavailable in the intraday CSVs. As an explicit
  reconstruction heuristic, a session stands in for a D1 bar when at least half
  of the 78 expected M5 RTH opens are present. This retains abbreviated sessions
  that would have a provider D1 bar while rejecting sparse outage days. It is an
  approximation and cannot exactly reproduce unavailable provider D1 bars.
* A "day"/session boundary is ``session_start`` in ``session_tz`` (default 00:00
  America/New_York = calendar day ET). **This is an assumption**: the provider's
  D1 session boundary (MT5/AMP) could NOT be determined from the available
  intraday CSVs, so it has not been substituted with evidence — it is exposed as
  a parameter and must be validated against the provider before trusting any
  historical result.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np

from src.backtest.executor import BacktestConfig
from src.backtest.history import Bar
from src.backtest.strategy import Signal

ET = ZoneInfo("America/New_York")

PRE_NY_START = time(0, 0)
PRE_NY_END = time(9, 30)
CONFIRM_START = time(9, 30)
CONFIRM_END = time(10, 30)
RTH_START = time(9, 30)
RTH_END = time(16, 0)

DEFAULT_ATR_PERIOD = 14
SL_ATR_MULT = 2.0
TP_ATR_RATIO = 2.0
SL_CAP_PTS = 50.0
DEFAULT_MEDIAN_LOOKBACK_DAYS = 260
DEFAULT_MIN_WEEKDAY_SAMPLES = 30
RTH_M5_MINUTES = frozenset(range(9 * 60 + 30, 16 * 60, 5))
MIN_RTH_M5_BARS = len(RTH_M5_MINUTES) // 2
DEFAULT_CONTRACT_SESSION_BLOCK = 65  # approx D1 bars per MNQ contract (varies; explicit approximation)
EXPECTED_M5_INTERVAL = timedelta(minutes=5)

# EMA confluence filter (from amd_crt_ema_confluence_trader.py, config activa
# EMA10/4h). The close of the last CLOSED 4h bar vs its EMA(span=period,
# adjust=False): close > ema -> "long" regime, else "short". A trade only fires
# when the regime matches the AMD fade direction (100% causal).
DEFAULT_EMA_PERIOD = 10
DEFAULT_EMA_TIMEFRAME_MINUTES = 240  # 4h
DEFAULT_EMA_MIN_BARS = DEFAULT_EMA_PERIOD + 10  # margen para media estable

# Edge gate (PROVISIONAL, experimental): use the strategy's own CLOSED trades'
# R-multiples to estimate the current edge via a bootstrap confidence interval.
# Only trades CLOSED on days strictly BEFORE today are used (100% causal). If the
# CI lower bound is not convincingly positive, the setup is treated as "cold"
# and the signal is suppressed. Marked PROVISIONAL because the threshold/lookback
# are candidate values, not validated — must be confirmed out-of-sample, never
# tuned on the graded data.
DEFAULT_EDGE_LOOKBACK_TRADES = 30
DEFAULT_EDGE_MIN_TRADES = 20
DEFAULT_EDGE_CI_LEVEL = 0.84  # ~1 sigma lower bound (one-sided-ish)
DEFAULT_EDGE_BOOTSTRAP_N = 400

DecisionReason = Literal[
    "amd_not_confirmed",
    "crt_not_confirmed",
    "ema_against",
    "edge_cold",
    "risk_out_of_band",
    "atr_insufficient",
    "accepted",
]


@dataclass(frozen=True)
class AmdCrtDecision:
    """Causal snapshot of one AMD-confirmed candidate evaluation."""

    day: date
    weekday: int
    direction: Literal["long", "short"]
    crt_confirmed: bool
    ema_regime: Literal["long", "short"] | None
    atr: float | None
    sl_pts: float | None
    tp_pts: float | None
    pre_ny_amplitude: float
    median_amplitude: float
    decision: DecisionReason
    timestamp: datetime


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp {ts!r}; bars must be timezone-aware")
    return ts


def _et_time(ts: datetime) -> time:
    return _aware(ts).astimezone(ET).time()


def _ema_bucket_start(ts: datetime, timeframe_minutes: int) -> datetime:
    """Start of the EMA timeframe bucket containing ``ts`` (ET-anchored).

    Buckets align to the top of the day in ET (00:00, then every
    ``timeframe_minutes``). This mirrors pandas ``resample(rule,
    label="left", closed="left")`` on a tz-aware ET index.
    """
    et = _aware(ts).astimezone(ET)
    minutes = et.hour * 60 + et.minute
    bucket_min = (minutes // timeframe_minutes) * timeframe_minutes
    return et.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        minutes=bucket_min
    )


def _ema_regime_direction(
    history: Sequence[Bar],
    *,
    period: int = DEFAULT_EMA_PERIOD,
    timeframe_minutes: int = DEFAULT_EMA_TIMEFRAME_MINUTES,
    min_bars: int = DEFAULT_EMA_MIN_BARS,
) -> Literal["long", "short"] | None:
    """Causal EMA(period)/timeframe regime: direction of the LAST CLOSED bar.

    Mirrors ``current_ema_regime``/``regime_at`` in
    ``amd_crt_ema_confluence_trader.py``: resample M5 -> timeframe bars (last
    in-progress bar dropped), EMA(span=period, adjust=False) over closes, then
    ``"long"`` when the last closed bar's close > its EMA, else ``"short"``.
    Returns None when there is not enough closed-bar history for a stable EMA.

    CAUSAL: only bars up to ``history`` are used, and the in-progress bucket
    (containing the last bar) is excluded — the regime only sees CLOSED bars.
    """
    if period < 1 or timeframe_minutes < 1 or min_bars < 1:
        raise ValueError("ema period/timeframe/min_bars must be >= 1")
    buckets: dict[datetime, float] = {}
    for b in history:
        key = _ema_bucket_start(b.timestamp, timeframe_minutes)
        buckets[key] = b.close  # last close in bucket wins (bars are chronological)
    if len(buckets) < 2:
        return None
    # Drop the last bucket (in-progress); use only CLOSED buckets before it.
    closed_keys = sorted(buckets)[:-1]
    if len(closed_keys) < min_bars:
        return None
    closes = [buckets[k] for k in closed_keys]
    alpha = 2.0 / (period + 1.0)  # pandas ewm(span=period, adjust=False)
    ema = closes[0]
    for c in closes[1:]:
        ema = ema + alpha * (c - ema)
    last_close = closes[-1]
    return "long" if last_close > ema else "short"


def _bootstrap_ci_lower(
    r_results: Sequence[float],
    *,
    ci_level: float = DEFAULT_EDGE_CI_LEVEL,
    n_bootstrap: int = DEFAULT_EDGE_BOOTSTRAP_N,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Bootstrap mean and lower CI bound of a sequence of R-multiples.

    IID resampling of the observed trade outcomes (mean with replacement) to
    estimate the expectancy and its ``ci_level`` lower bound. Returns
    ``(mean, ci_lower)`` or None if there are fewer than 2 observations.

    ASSUMPTION: the outcomes are IID-exchangeable. This is a pragmatic
    PROVISIONAL gate, NOT the Phase 10A dependence-audited bootstrap — for a
    production decision the trade sequence must first pass ``iid_eligible``
    (src.bootstrap). If the outcomes are dependent (streaks), plain IID
    bootstrap understates the CI width.
    """
    values = np.asarray([float(r) for r in r_results], dtype=np.float64)
    if values.size < 2:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, values.size, size=(n_bootstrap, values.size))
    means = values[idx].mean(axis=1)
    mean = float(values.mean())
    ci_lower = float(np.percentile(means, 100.0 * (1.0 - ci_level)))
    return mean, ci_lower


def _session_date(
    ts: datetime,
    session_tz: ZoneInfo = ET,
    session_start: time = time(0, 0),
) -> date:
    """Trading-session date of a timestamp. With the default 00:00 start this is
    the calendar date in ``session_tz``; a later ``session_start`` shifts the
    boundary so that bars before it belong to the previous session."""
    local = _aware(ts).astimezone(session_tz)
    d = local.date()
    if session_start != time(0, 0) and local.time() < session_start:
        d = d - timedelta(days=1)
    return d


def _day_bars(history: Sequence[Bar], day: date, *, session_tz: ZoneInfo = ET,
              session_start: time = time(0, 0)) -> list[Bar]:
    """Bars belonging to session ``day`` (scan from the end for efficiency)."""
    out: list[Bar] = []
    for b in reversed(history):
        if _session_date(b.timestamp, session_tz, session_start) == day:
            out.append(b)
        elif out:
            break
    out.reverse()
    return out


def _median(values: list[float]) -> float:
    """Standard median: middle value for odd N, mean of the two middle for even N."""
    if not values:
        raise ValueError("empty values")
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _is_complete_rth_session(day_bars: list[Bar]) -> bool:
    """Whether intraday bars can approximately reconstruct one provider D1 bar.

    The provider's native D1 bars are not present in the CSVs. The input is M5,
    so this heuristic requires CONTIGUOUS M5 bars from the RTH open (09:30)
    covering at least half of the regular session. An abbreviated session (an
    early close) is contiguous from 09:30 and is accepted; a scattered outage
    (gaps inside the RTH) is not.
    """
    rth = sorted(
        (b for b in day_bars if RTH_START <= _et_time(b.timestamp) < RTH_END),
        key=lambda b: b.timestamp,
    )
    if not rth or _et_time(rth[0].timestamp) != RTH_START:
        return False
    for prev, curr in pairwise(rth):
        if curr.timestamp - prev.timestamp != EXPECTED_M5_INTERVAL:
            return False
    return len(rth) >= MIN_RTH_M5_BARS


def _weekday_median_amplitudes(
    history: Sequence[Bar],
    upto: date,
    *,
    lookback_days: int = DEFAULT_MEDIAN_LOOKBACK_DAYS,
    min_samples: int = DEFAULT_MIN_WEEKDAY_SAMPLES,
    contract_session_block: int = DEFAULT_CONTRACT_SESSION_BLOCK,
    session_tz: ZoneInfo = ET,
    session_start: time = time(0, 0),
) -> dict[int, float]:
    """Median D1 amplitude per weekday over complete prior sessions.

    ``lookback_days`` is the minimum count of reconstructed D1 sessions, matching
    the reference trader's requirement to gather sufficient D1 history. The
    reference chains recent contracts in roughly 65-session blocks and stops as
    soon as the minimum is reached. Contract identity is unavailable in the CSV,
    so the reproducible approximation here uses the most-recent tail rounded up
    to a 65-session block, rather than an expanding multi-year window.
    ``session_tz`` and ``session_start`` define the grouping boundary because the
    provider's native D1 boundary remains unknown.
    """
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")
    bounded_count = math.ceil(lookback_days / contract_session_block) * contract_session_block

    # Only the most-recent ``bounded_count`` COMPLETE sessions can affect the
    # median (older ones are dropped by ``complete_days[-bounded_count:]``), so
    # scan backward from the end and stop as soon as that many complete sessions
    # are gathered. This keeps the call O(bounded window) instead of O(history),
    # which is what makes the full multi-year backtest tractable, and it returns
    # the exact same sessions as the forward scan.
    complete_sessions: list[tuple[date, list[Bar]]] = []
    cur: date | None = None
    cur_bars: list[Bar] = []
    for b in reversed(history):
        d = _session_date(b.timestamp, session_tz, session_start)
        if d >= upto:
            continue
        if d == cur:
            cur_bars.append(b)
            continue
        if cur is not None and cur < upto and _is_complete_rth_session(cur_bars):
            complete_sessions.append((cur, cur_bars))
            if len(complete_sessions) >= bounded_count:
                break
        cur = d
        cur_bars = [b]
    if cur is not None and cur < upto and _is_complete_rth_session(cur_bars):
        complete_sessions.append((cur, cur_bars))

    if len(complete_sessions) < lookback_days:
        return {}
    amps_by_weekday: dict[int, list[float]] = defaultdict(list)
    for d, day_bars in complete_sessions:
        high = max(bar.high for bar in day_bars)
        low = min(bar.low for bar in day_bars)
        amps_by_weekday[d.weekday()].append(high - low)
    medians: dict[int, float] = {}
    for wd, amps in amps_by_weekday.items():
        if len(amps) >= min_samples:
            medians[wd] = _median(amps)
    return medians


def _detect_amd(
    history: Sequence[Bar],
    day: date,
    *,
    lookback_days: int = DEFAULT_MEDIAN_LOOKBACK_DAYS,
    min_samples: int = DEFAULT_MIN_WEEKDAY_SAMPLES,
    contract_session_block: int = DEFAULT_CONTRACT_SESSION_BLOCK,
    session_tz: ZoneInfo = ET,
    session_start: time = time(0, 0),
    medians: dict[int, float] | None = None,
) -> tuple[Literal["long", "short"], datetime] | None:
    """Return (direction, confirm_time) if AMD confirms today, else None.

    direction is the FADE direction: "short" on a pre-NY high sweep, "long" on a
    pre-NY low sweep (matches the MNQ executor).

    ``medians`` may be passed pre-computed (the weekday median is constant for the
    whole session — it depends only on PRIOR sessions, whose bars are frozen while
    the current one is in progress). Passing it avoids recomputing the expensive
    median on every intraday bar; when omitted it is computed here.
    """
    day_bars = _day_bars(history, day, session_tz=session_tz, session_start=session_start)
    if not day_bars:
        return None

    pre_ny = [b for b in day_bars if PRE_NY_START <= _et_time(b.timestamp) < PRE_NY_END]
    if not pre_ny:
        return None
    if _et_time(pre_ny[0].timestamp) != PRE_NY_START:
        return None  # pre-NY window must START at 00:00 (missing leading bars)
    pre_ny_high = max(b.high for b in pre_ny)
    pre_ny_low = min(b.low for b in pre_ny)
    pre_ny_amplitude = pre_ny_high - pre_ny_low

    if medians is None:
        medians = _weekday_median_amplitudes(
            history, day, lookback_days=lookback_days, min_samples=min_samples,
            contract_session_block=contract_session_block,
            session_tz=session_tz, session_start=session_start,
        )
    if day.weekday() not in medians:
        return None
    if not (pre_ny_amplitude < medians[day.weekday()]):
        return None  # range not compressed

    confirm = [
        b for b in day_bars
        if CONFIRM_START <= _et_time(b.timestamp) < CONFIRM_END
    ]
    if not confirm:
        return None

    # AMD depends on one continuous M5 observation window. Reject missing bars
    # inside pre-NY, inside confirmation before the sweep, or at their boundary;
    # otherwise an outage can manufacture both compression and a sweep. A later
    # gap cannot retroactively invalidate an earlier causal confirmation.
    if any(
        current.timestamp - previous.timestamp != EXPECTED_M5_INTERVAL
        for previous, current in pairwise(pre_ny)
    ):
        return None

    # first sweep-and-close-inside within the confirm window (causal)
    previous = pre_ny[-1]
    for b in confirm:
        if b.timestamp - previous.timestamp != EXPECTED_M5_INTERVAL:
            return None
        if b.high > pre_ny_high and b.close < pre_ny_high:
            return ("short", b.timestamp)
        if b.low < pre_ny_low and b.close > pre_ny_low:
            return ("long", b.timestamp)
        previous = b
    return None


def _detect_crt(
    history: Sequence[Bar],
    day: date,
    *,
    session_tz: ZoneInfo = ET,
    session_start: time = time(0, 0),
) -> bool:
    """True if the prior session's RTH PDH/PDL is swept-and-closed-inside today.

    Only bars up to the last bar of ``history`` are used, so a sweep is only
    visible once it has actually happened. The prior session's PDH/PDL and the
    current day's RTH bars are all that can matter, so scan backward from the end
    and stop once the current day plus the nearest complete prior RTH session are
    gathered — instead of scanning the whole history every call.
    """
    cur: date | None = None
    rth_by_day: list[tuple[date, list[Bar]]] = []
    cur_bars: list[Bar] = []
    for b in reversed(history):
        if not (RTH_START <= _et_time(b.timestamp) <= RTH_END):
            continue
        d = _session_date(b.timestamp, session_tz, session_start)
        if d == cur:
            cur_bars.append(b)
            continue
        if cur is not None:
            rth_by_day.append((cur, cur_bars))
            if len(rth_by_day) >= 2:
                break
        cur = d
        cur_bars = [b]
    if cur is not None and len(rth_by_day) < 2:
        rth_by_day.append((cur, cur_bars))

    # rth_by_day is most-recent-first: day, then the prior RTH session, etc.
    if not rth_by_day or rth_by_day[0][0] != day:
        return False
    if len(rth_by_day) < 2:
        return False  # no prior RTH session
    yesterday, yesterday_bars = rth_by_day[1]
    if not yesterday_bars or not _is_complete_rth_session(yesterday_bars):
        return False  # prior RTH must be complete enough for a trustworthy PDH/PDL
    pdh = max(b.high for b in yesterday_bars)
    pdl = min(b.low for b in yesterday_bars)

    today_bars = rth_by_day[0][1]
    for b in today_bars:
        if b.high > pdh and b.close < pdh:
            return True
        if b.low < pdl and b.close > pdl:
            return True
    return False


def _atr14(rth_bars: list[Bar], period: int = DEFAULT_ATR_PERIOD) -> float | None:
    """ATR(period) as True Range smoothed by EMA(alpha=1/period), over RTH bars."""
    if period < 1:
        raise ValueError("atr period must be >= 1")
    if len(rth_bars) < period + 2:
        return None
    trs = [rth_bars[0].high - rth_bars[0].low]
    prev_close = rth_bars[0].close
    for b in rth_bars[1:]:
        trs.append(max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close)))
        prev_close = b.close
    alpha = 1.0 / period
    atr = trs[0]
    for tr in trs[1:]:
        atr = atr + alpha * (tr - atr)
    return atr


class AmdCrtStrategy:
    """Stateful causal AMD+CRT strategy consumed by the backtest executor.

    ``evaluate`` is called by the executor on every bar while flat. The strategy
    keeps the day's AMD confirmation pending (so a later CRT can complete the
    confluence) and emits at most one signal per day.
    """

    def __init__(
        self,
        *,
        sl_cap_pts: float = SL_CAP_PTS,
        atr_period: int = DEFAULT_ATR_PERIOD,
        sl_atr_mult: float = SL_ATR_MULT,
        tp_atr_ratio: float = TP_ATR_RATIO,
        median_lookback_days: int = DEFAULT_MEDIAN_LOOKBACK_DAYS,
        min_weekday_samples: int = DEFAULT_MIN_WEEKDAY_SAMPLES,
        contract_session_block: int = DEFAULT_CONTRACT_SESSION_BLOCK,
        session_tz: ZoneInfo = ET,
        session_start: time = time(0, 0),
        # PROVISIONAL pre-trade risk filter: only enter when SL risk (in points)
        # falls within [min_risk_pts, max_risk_pts]. None = no bound (default =
        # current/legacy behavior). Marked PROVISIONAL because a filter that is
        # tuned on the same data it is graded on is data-mining; experiment,
        # then validate out-of-sample.
        min_risk_pts: float | None = None,
        max_risk_pts: float | None = None,
        # EMA confluence filter (amd_crt_ema_confluence_trader.py, EMA10/4h).
        # When ``use_ema_filter`` is True, a signal only fires if the EMA regime
        # direction (last CLOSED bar) matches the AMD fade direction. This is the
        # "triple confluence" AMD+CRT+EMA candidate — opt-in, off by default to
        # preserve the AMD+CRT (no-EMA) behavior.
        use_ema_filter: bool = False,
        ema_period: int = DEFAULT_EMA_PERIOD,
        ema_timeframe_minutes: int = DEFAULT_EMA_TIMEFRAME_MINUTES,
        ema_min_bars: int = DEFAULT_EMA_MIN_BARS,
        # Edge gate (PROVISIONAL, experimental): use this strategy's own CLOSED
        # trades (R-multiples from days strictly before today) to estimate edge
        # via a bootstrap CI lower bound. Suppress the signal when the edge is
        # not convincingly positive. Opt-in, off by default.
        use_edge_gate: bool = False,
        edge_lookback_trades: int = DEFAULT_EDGE_LOOKBACK_TRADES,
        edge_min_trades: int = DEFAULT_EDGE_MIN_TRADES,
        edge_ci_level: float = DEFAULT_EDGE_CI_LEVEL,
        log_decisions: bool = True,
    ) -> None:
        self.sl_cap_pts = sl_cap_pts
        self.atr_period = atr_period
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_ratio = tp_atr_ratio
        self.median_lookback_days = median_lookback_days
        self.min_weekday_samples = min_weekday_samples
        self.contract_session_block = contract_session_block
        self.session_tz = session_tz
        self.session_start = session_start
        self.min_risk_pts = min_risk_pts
        self.max_risk_pts = max_risk_pts
        self.use_ema_filter = use_ema_filter
        self.ema_period = ema_period
        self.ema_timeframe_minutes = ema_timeframe_minutes
        self.ema_min_bars = ema_min_bars
        self.use_edge_gate = use_edge_gate
        self.edge_lookback_trades = edge_lookback_trades
        self.edge_min_trades = edge_min_trades
        self.edge_ci_level = edge_ci_level
        self.log_decisions = log_decisions
        self.decisions: list[AmdCrtDecision] = []
        self._amd: tuple[Literal["long", "short"], datetime] | None = None
        self._amd_day: date | None = None
        self._crt_day: date | None = None
        self._signal_day: date | None = None
        self._medians: dict[int, float] | None = None
        self._medians_day: date | None = None
        self._ema_regime: Literal["long", "short"] | None = None
        self._ema_bucket: datetime | None = None
        # closed trades: (exit_date, r_result), populated by the executor via
        # note_trade(). Only exits on days strictly BEFORE today are eligible
        # for the edge gate (causal).
        self._closed: list[tuple[date, float]] = []
        self._edge_ok: bool | None = None
        self._edge_day: date | None = None

    def clear_decisions(self) -> None:
        """Empty the observational decision log without changing strategy state."""
        self.decisions.clear()

    def _record_decision(
        self,
        history: Sequence[Bar],
        day: date,
        direction: Literal["long", "short"],
        *,
        crt_confirmed: bool,
        ema_regime: Literal["long", "short"] | None,
        sl_tp: tuple[float, float] | None,
        decision: DecisionReason,
    ) -> None:
        """Append a causal snapshot; this method never participates in gating."""
        if not self.log_decisions:
            return
        day_bars = _day_bars(
            history, day, session_tz=self.session_tz,
            session_start=self.session_start,
        )
        pre_ny = [
            bar for bar in day_bars
            if PRE_NY_START <= _et_time(bar.timestamp) < PRE_NY_END
        ]
        # Real AMD confirmations imply both values exist. NaN fallbacks keep
        # observation inert for synthetic/custom detectors that do not honor
        # those detector invariants.
        pre_ny_amplitude = (
            max(bar.high for bar in pre_ny) - min(bar.low for bar in pre_ny)
            if pre_ny else math.nan
        )
        median_amplitude = (
            self._medians.get(day.weekday(), math.nan)
            if self._medians is not None else math.nan
        )
        atr = self._atr_value(history)
        observed_sl_tp = sl_tp
        if observed_sl_tp is None and atr is not None and atr > 0:
            observed_sl = min(atr * self.sl_atr_mult, self.sl_cap_pts)
            observed_sl_tp = (observed_sl, observed_sl * self.tp_atr_ratio)
        sl_pts, tp_pts = (
            observed_sl_tp if observed_sl_tp is not None else (None, None)
        )
        self.decisions.append(
            AmdCrtDecision(
                day=day,
                weekday=day.weekday(),
                direction=direction,
                crt_confirmed=crt_confirmed,
                ema_regime=ema_regime if self.use_ema_filter else None,
                atr=atr,
                sl_pts=sl_pts,
                tp_pts=tp_pts,
                pre_ny_amplitude=pre_ny_amplitude,
                median_amplitude=median_amplitude,
                decision=decision,
                timestamp=history[-1].timestamp,
            )
        )

    def note_trade(self, exit_time: datetime, r_result: float) -> None:
        """Record a CLOSED trade for the causal edge gate.

        Called by the executor once a trade closes. ``exit_time`` must be
        tz-aware; its session date is derived with the strategy's own session
        convention so a same-day exit can never feed a same-day decision.
        """
        d = _session_date(exit_time, self.session_tz, self.session_start)
        self._closed.append((d, float(r_result)))

    def _edge_gate_ok(self, day: date) -> bool:
        """True when recent CLOSED trades show a convincingly positive edge.

        Considers the last ``edge_lookback_trades`` exits on days STRICTLY
        before ``day`` (causal). Requires at least ``edge_min_trades``; with
        fewer, it is permissive (returns True — no evidence yet, so do not
        suppress). With enough history, requires the bootstrap CI lower bound
        of expectancy to be > 0.
        """
        prior = [r for d, r in self._closed if d < day]
        if len(prior) < self.edge_min_trades:
            return True
        recent = prior[-self.edge_lookback_trades :]
        est = _bootstrap_ci_lower(recent, ci_level=self.edge_ci_level)
        if est is None:
            return True
        _mean, ci_lower = est
        return ci_lower > 0.0

    def evaluate(self, history: Sequence[Bar]) -> Signal | None:
        if not history:
            return None
        # ``history`` arrives as an already-materialized list from the executor
        # (and is never mutated here), so avoid copying it on every call — a
        # per-bar copy of a growing history is O(n^2) and dominates a multi-year
        # run. Only the day/tail is needed.
        day = _session_date(history[-1].timestamp, self.session_tz, self.session_start)
        current_time = _et_time(history[-1].timestamp)
        if day.weekday() >= 5 or current_time >= RTH_END:
            return None

        if self._amd_day != day:
            self._amd_day = day
            self._amd = None
        if self._signal_day == day:
            return None  # at most one trade per day

        if self._amd is None:
            if self._medians_day != day:
                self._medians = _weekday_median_amplitudes(
                    history,
                    day,
                    lookback_days=self.median_lookback_days,
                    min_samples=self.min_weekday_samples,
                    contract_session_block=self.contract_session_block,
                    session_tz=self.session_tz,
                    session_start=self.session_start,
                )
                self._medians_day = day
            self._amd = _detect_amd(
                history, day,
                lookback_days=self.median_lookback_days,
                min_samples=self.min_weekday_samples,
                contract_session_block=self.contract_session_block,
                session_tz=self.session_tz,
                session_start=self.session_start,
                medians=self._medians,
            )
        if self._amd is None:
            # Not logged: by definition a candidate starts only once AMD has
            # confirmed. ``amd_not_confirmed`` remains part of DecisionReason
            # for schema compatibility, but emitting it here would violate the
            # task's candidate boundary.
            return None

        direction, _confirm_time = self._amd

        # A causal CRT confirmation is monotonic within a session: after a
        # qualifying sweep appears, it remains in every later history prefix.
        # Cache only positive confirmation, so a not-yet-confirmed CRT is still
        # reconsidered on the next bar without using future data.
        if self._crt_day != day:
            if not _detect_crt(
                history, day, session_tz=self.session_tz,
                session_start=self.session_start,
            ):
                self._record_decision(
                    history, day, direction, crt_confirmed=False,
                    ema_regime=None, sl_tp=None, decision="crt_not_confirmed",
                )
                return None
            self._crt_day = day

        # EMA confluence filter (opt-in). Cache the regime by the current 4h
        # bucket so we do not rescan history on every bar (O(n^2) otherwise);
        # the regime only changes when a new 4h bucket closes.
        if self.use_ema_filter:
            bucket = _ema_bucket_start(history[-1].timestamp, self.ema_timeframe_minutes)
            if bucket != self._ema_bucket:
                self._ema_bucket = bucket
                self._ema_regime = _ema_regime_direction(
                    history,
                    period=self.ema_period,
                    timeframe_minutes=self.ema_timeframe_minutes,
                    min_bars=self.ema_min_bars,
                )
            if self._ema_regime is None or self._ema_regime != direction:
                self._record_decision(
                    history, day, direction, crt_confirmed=True,
                    ema_regime=self._ema_regime, sl_tp=None,
                    decision="ema_against",
                )
                return None

        # Edge gate (opt-in, PROVISIONAL): only enter when the strategy's own
        # recent closed trades show a convincingly positive edge (bootstrap CI
        # lower bound > 0). Uses only trades CLOSED on days strictly before
        # today — 100% causal, no look-ahead. Cached per day.
        if self.use_edge_gate:
            if self._edge_day != day:
                self._edge_day = day
                self._edge_ok = self._edge_gate_ok(day)
            if not self._edge_ok:
                self._record_decision(
                    history, day, direction, crt_confirmed=True,
                    ema_regime=self._ema_regime, sl_tp=None,
                    decision="edge_cold",
                )
                return None

        sl_tp = self._sl_tp(history)
        if sl_tp is None:
            self._record_decision(
                history, day, direction, crt_confirmed=True,
                ema_regime=self._ema_regime, sl_tp=None,
                decision="atr_insufficient",
            )
            return None
        sl_pts, tp_pts = sl_tp
        # PROVISIONAL pre-trade risk filter (experimental, see __init__).
        # Reject the signal when the per-trade risk (SL distance in points) is
        # outside the configured band. Bound both sides; None skips that side.
        if self.min_risk_pts is not None and sl_pts < self.min_risk_pts:
            self._record_decision(
                history, day, direction, crt_confirmed=True,
                ema_regime=self._ema_regime, sl_tp=sl_tp,
                decision="risk_out_of_band",
            )
            return None
        if self.max_risk_pts is not None and sl_pts > self.max_risk_pts:
            self._record_decision(
                history, day, direction, crt_confirmed=True,
                ema_regime=self._ema_regime, sl_tp=sl_tp,
                decision="risk_out_of_band",
            )
            return None
        self._signal_day = day
        self._record_decision(
            history, day, direction, crt_confirmed=True,
            ema_regime=self._ema_regime, sl_tp=sl_tp, decision="accepted",
        )
        # stop/target are distances in points (positive); entry is unused.
        return Signal(
            direction=direction, entry=0.0, stop=sl_pts, target=tp_pts,
            stop_target_as_points=True,
        )

    def _atr_value(self, history: Sequence[Bar]) -> float | None:
        today = _session_date(history[-1].timestamp, self.session_tz, self.session_start)
        cutoff = datetime.combine(today - timedelta(days=10), time(0, 0), tzinfo=ET)
        # ``history`` is chronological. Walk backward only through the bounded
        # window that the legacy forward scan selected, then restore its order
        # so the ATR floating-point recurrence is bit-for-bit identical.
        rth_reversed: list[Bar] = []
        for b in reversed(history):
            if b.timestamp.astimezone(ET) < cutoff:
                break
            if RTH_START <= _et_time(b.timestamp) <= RTH_END:
                rth_reversed.append(b)
        rth = list(reversed(rth_reversed))
        return _atr14(rth, self.atr_period)

    def _sl_tp(self, history: Sequence[Bar]) -> tuple[float, float] | None:
        atr = self._atr_value(history)
        if atr is None or atr <= 0:
            return None
        sl_pts = min(atr * self.sl_atr_mult, self.sl_cap_pts)
        tp_pts = sl_pts * self.tp_atr_ratio
        return sl_pts, tp_pts

    def parameters(self) -> dict[str, object]:
        """Serializable constructor parameters for reproducible reports."""
        return {
            "sl_cap_pts": self.sl_cap_pts,
            "atr_period": self.atr_period,
            "sl_atr_mult": self.sl_atr_mult,
            "tp_atr_ratio": self.tp_atr_ratio,
            "median_lookback_days": self.median_lookback_days,
            "min_weekday_samples": self.min_weekday_samples,
            "contract_session_block": self.contract_session_block,
            "session_tz": str(self.session_tz),
            "session_start": self.session_start.isoformat(),
            "min_risk_pts": self.min_risk_pts,
            "max_risk_pts": self.max_risk_pts,
            "use_ema_filter": self.use_ema_filter,
            "ema_period": self.ema_period,
            "ema_timeframe_minutes": self.ema_timeframe_minutes,
            "ema_min_bars": self.ema_min_bars,
            "use_edge_gate": self.use_edge_gate,
            "edge_lookback_trades": self.edge_lookback_trades,
            "edge_min_trades": self.edge_min_trades,
            "edge_ci_level": self.edge_ci_level,
            "log_decisions": self.log_decisions,
        }

    def fresh(self) -> AmdCrtStrategy:
        """Return a new strategy with identical parameters and empty state."""
        return AmdCrtStrategy(
            sl_cap_pts=self.sl_cap_pts,
            atr_period=self.atr_period,
            sl_atr_mult=self.sl_atr_mult,
            tp_atr_ratio=self.tp_atr_ratio,
            median_lookback_days=self.median_lookback_days,
            min_weekday_samples=self.min_weekday_samples,
            contract_session_block=self.contract_session_block,
            session_tz=self.session_tz,
            session_start=self.session_start,
            min_risk_pts=self.min_risk_pts,
            max_risk_pts=self.max_risk_pts,
            use_ema_filter=self.use_ema_filter,
            ema_period=self.ema_period,
            ema_timeframe_minutes=self.ema_timeframe_minutes,
            ema_min_bars=self.ema_min_bars,
            use_edge_gate=self.use_edge_gate,
            edge_lookback_trades=self.edge_lookback_trades,
            edge_min_trades=self.edge_min_trades,
            edge_ci_level=self.edge_ci_level,
            log_decisions=self.log_decisions,
        )


def amd_crt_config(**overrides) -> BacktestConfig:
    """Default executor config for the AMD+CRT candidate run (public entry).

    Fixed 1 MNQ micro, 60-minute timestamp time-exit, 5-minute bar interval
    (for entry-gap detection), and the MNQ friction scenario (2 points
    round-trip) as a single aggregated cost assumption: commission carries the
    full round-trip and slippage is zero, so costs are never double-counted.
    Override ``friction_pts`` (points round-trip) or the explicit
    ``commission_per_side``/``slippage_points`` (PROVISIONAL MVP scenario) via
    keyword arguments.
    """
    friction_pts = overrides.pop("friction_pts", 2.0)
    if not math.isfinite(friction_pts) or friction_pts < 0:
        raise ValueError("friction_pts must be finite and >= 0")
    dollar_per_point = overrides.pop("dollar_per_point", 2.0)
    params = {
        "fixed_quantity": 1,
        "max_hold_minutes": 60.0,
        "bar_interval_seconds": 300,
        "commission_per_side": friction_pts * dollar_per_point / 2.0,
        "slippage_points": 0.0,
        "dollar_per_point": dollar_per_point,
    }
    params.update(overrides)
    return BacktestConfig(**params)


__all__ = [
    "DEFAULT_ATR_PERIOD",
    "DEFAULT_EMA_PERIOD",
    "DEFAULT_EMA_TIMEFRAME_MINUTES",
    "ET",
    "SL_CAP_PTS",
    "AmdCrtDecision",
    "AmdCrtStrategy",
    "_atr14",
    "_bootstrap_ci_lower",
    "_detect_amd",
    "_detect_crt",
    "_ema_regime_direction",
    "_median",
    "_weekday_median_amplitudes",
    "amd_crt_config",
]
