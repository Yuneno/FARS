"""Market contract and frozen pre-refactor MNQ regression evidence."""

import hashlib
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from docs.refactor.check_mnq_equivalence import capture, encoded
from src.backtest.amd_crt import (
    AmdCrtStrategy,
    _atr14,
    _detect_amd,
    _detect_crt,
    _ema_bucket_start,
    _ema_regime_direction,
    _is_complete_rth_session,
    _weekday_median_amplitudes,
    amd_crt_config,
)
from src.backtest.cli import _parser, main
from src.backtest.executor import executed_to_core_trades, run_backtest
from src.backtest.history import Bar
from src.backtest.markets import MARKETS, MES, MGC, MNQ, MYM, get_market_spec
from src.backtest.strategy import Signal


@pytest.mark.parametrize(
    "symbol,multiplier,tick,opening,closing,friction",
    [
        ("MNQ", 2.0, 0.25, time(9, 30), time(16), 2.0),
        ("MES", 5.0, 0.25, time(9, 30), time(16), None),
        ("MYM", 0.5, 1.0, time(9, 30), time(16), None),
        ("MGC", 10.0, 0.1, time(8, 20), time(13, 30), None),
    ],
)
def test_registered_market_values(symbol, multiplier, tick, opening, closing, friction):
    spec = get_market_spec(symbol.lower())
    assert spec is MARKETS[symbol]
    assert spec.symbol == symbol
    assert spec.session_timezone == ZoneInfo("America/New_York")
    assert (spec.pre_session_start, spec.pre_session_end) == (time(0), opening)
    assert (spec.regular_session_start, spec.regular_session_end) == (opening, closing)
    assert (spec.dollar_per_point, spec.tick_size, spec.friction_points) == (
        multiplier,
        tick,
        friction,
    )
    assert set(MARKETS) == {"MNQ", "MES", "MYM", "MGC"}


def test_registry_and_specs_are_immutable():
    with pytest.raises(FrozenInstanceError):
        MNQ.tick_size = 1.0
    with pytest.raises(TypeError):
        MARKETS["MNQ"] = MES
    with pytest.raises(ValueError, match="unknown market"):
        get_market_spec("missing")


def test_mnq_defaults_and_injection_survive_fresh_and_reports():
    assert get_market_spec() is MNQ
    assert AmdCrtStrategy().market is MNQ
    assert AmdCrtStrategy(market=MNQ).parameters() == AmdCrtStrategy().parameters()
    assert amd_crt_config(market=MNQ) == amd_crt_config()
    custom = replace(MGC, session_timezone=ZoneInfo("Asia/Tokyo"), friction_points=0.8)
    strategy = AmdCrtStrategy(market=custom)
    strategy._signal_day = date(2026, 9, 7)
    fresh = strategy.fresh()
    assert fresh.market is custom
    assert fresh.session_tz == custom.session_timezone
    assert fresh.parameters() == strategy.parameters()
    assert fresh.parameters()["market"] == custom.to_dict()
    assert fresh._signal_day is None


@pytest.mark.parametrize("spec", [MES, MYM, MGC])
def test_unknown_costs_require_an_explicit_scenario(spec):
    with pytest.raises(ValueError, match=f"{spec.symbol} friction_points is unvalidated"):
        amd_crt_config(market=spec)
    assert amd_crt_config(market=spec, friction_pts=1.0).commission_per_side == (
        spec.dollar_per_point / 2
    )


@pytest.mark.parametrize("spec", [MNQ, MES, MYM, MGC])
def test_money_tick_and_core_symbol_use_market_spec(spec):
    # Synthetic executor unit test, not a historical backtest of these markets.
    config = amd_crt_config(market=spec, friction_pts=2.0)
    assert config.tick_size == spec.tick_size
    assert config.dollar_per_point == spec.dollar_per_point
    assert config.commission_per_side == spec.dollar_per_point

    class OneSignal:
        market = spec

        def evaluate(self, history):
            return Signal("long", 0.0, 5.0, 10.0, stop_target_as_points=True)

    start = datetime(2026, 9, 7, 10, tzinfo=UTC)
    bars = [
        Bar(start, 100.0, 101.0, 99.0, 100.0, 1.0),
        Bar(start + timedelta(minutes=5), 100.0, 111.0, 99.0, 110.0, 1.0),
    ]
    result = run_backtest(bars, OneSignal(), config)
    (trade,) = result.trades
    assert trade.gross_pnl == 10 * spec.dollar_per_point
    assert trade.net_pnl == 8 * spec.dollar_per_point
    assert trade.stop_risk_dollars == 5 * spec.dollar_per_point
    (core,) = executed_to_core_trades(result.trades, config, symbol=spec.symbol)
    assert core.asset == spec.symbol


def regular_bars(spec, day, n=None):
    start = datetime.combine(day, spec.regular_session_start, spec.session_timezone)
    end = datetime.combine(day, spec.regular_session_end, spec.session_timezone)
    count = int((end - start).total_seconds() // 300) if n is None else n
    return [
        Bar(start + timedelta(minutes=5 * i), 150.0, 200.0, 100.0, 150.0, 1.0) for i in range(count)
    ]


def pre_bars(spec, day):
    start = datetime.combine(day, spec.pre_session_start, spec.session_timezone)
    end = datetime.combine(day, spec.pre_session_end, spec.session_timezone)
    return [
        Bar(start + timedelta(minutes=5 * i), 110.0, 125.0, 100.0, 110.0, 1.0)
        for i in range(int((end - start).total_seconds() // 300))
    ]


def test_gold_regular_window_and_completeness_do_not_use_index_hours():
    day = date(2026, 9, 4)
    bars = regular_bars(MGC, day)
    assert len(bars) == 62
    assert _is_complete_rth_session(bars[:31], MGC)
    assert not _is_complete_rth_session(bars[:30], MGC)
    assert not _is_complete_rth_session(bars[:31], MNQ)
    assert not _is_complete_rth_session(bars[1:32], MGC)
    assert _weekday_median_amplitudes(
        bars,
        day + timedelta(days=1),
        lookback_days=1,
        min_samples=1,
        contract_session_block=1,
        market=MGC,
    ) == {day.weekday(): 100.0}


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Tokyo"])
def test_all_signal_windows_come_from_injected_market(zone):
    spec = replace(MGC, session_timezone=ZoneInfo(zone), pre_session_start=time(1))
    day = date(2026, 9, 7)
    prior = regular_bars(spec, date(2026, 9, 4))
    pre = pre_bars(spec, day)
    opening = datetime.combine(day, spec.regular_session_start, spec.session_timezone)
    sweep = Bar(opening, 120.0, 205.0, 100.0, 120.0, 1.0)
    history = prior + pre + [sweep]
    # UTC input must produce the same local sessions, including Tokyo's prior UTC day.
    history = [replace(bar, timestamp=bar.timestamp.astimezone(UTC)) for bar in history]
    assert _detect_amd(history, day, market=spec, medians={0: 100.0}) == ("short", opening)
    assert _detect_crt(history, day, market=spec)
    assert _detect_amd(history, day, medians={0: 100.0}) is None
    strategy = AmdCrtStrategy(
        market=spec, median_lookback_days=1, min_weekday_samples=1, contract_session_block=1
    )
    strategy._medians_day, strategy._medians = day, {0: 100.0}
    signal = strategy.evaluate(history)
    assert signal is not None and signal.direction == "short"
    assert strategy.decisions[-1].pre_ny_amplitude == 25.0
    assert strategy._atr_value(history) == _atr14(prior + [sweep])
    closing = datetime.combine(day, spec.regular_session_end, spec.session_timezone)
    assert strategy.fresh().evaluate(history + [replace(sweep, timestamp=closing)]) is None


def test_gold_confirmation_still_lasts_exactly_one_hour():
    day = date(2026, 9, 7)
    pre = pre_bars(MGC, day)
    opening = datetime.combine(day, MGC.regular_session_start, MGC.session_timezone)
    quiet = [
        Bar(opening + timedelta(minutes=5 * i), 110.0, 120.0, 105.0, 110.0, 1.0) for i in range(12)
    ]
    before_end = replace(quiet[-1], high=130.0, close=120.0)
    at_end = replace(before_end, timestamp=opening + timedelta(hours=1))
    assert _detect_amd(pre + quiet[:-1] + [before_end], day, market=MGC, medians={0: 100.0}) == (
        "short",
        before_end.timestamp,
    )
    assert _detect_amd(pre + quiet + [at_end], day, market=MGC, medians={0: 100.0}) is None


def test_ema_buckets_use_market_timezone():
    spec = replace(MNQ, session_timezone=ZoneInfo("Asia/Tokyo"))
    timestamp = datetime(2026, 9, 7, 0, 30, tzinfo=UTC)
    expected = datetime(2026, 9, 7, 8, tzinfo=spec.session_timezone)
    assert _ema_bucket_start(timestamp, 240, spec) == expected
    assert _ema_bucket_start(timestamp, 240, spec) != _ema_bucket_start(timestamp, 240)
    bars = [
        Bar(timestamp + timedelta(hours=i), 100.0, 101.0, 99.0, float(i), 1.0) for i in range(16)
    ]
    assert _ema_regime_direction(bars, period=2, min_bars=2, market=spec) == "long"


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"symbol": ""}, "symbol"),
        ({"session_timezone": "bad/zone"}, "session_timezone"),
        ({"dollar_per_point": 0}, "dollar_per_point"),
        ({"dollar_per_point": float("inf")}, "dollar_per_point"),
        ({"tick_size": -1}, "tick_size"),
        ({"tick_size": True}, "tick_size"),
        ({"friction_points": float("nan")}, "friction_points"),
        ({"friction_points": -1}, "friction_points"),
        ({"regular_session_start": time(16)}, "sessions"),
        ({"pre_session_end": time(9)}, "sessions"),
        ({"pre_session_start": time(18)}, "sessions"),
        ({"regular_session_end": time(10)}, "60-minute"),
        ({"regular_session_end": time(16, 1)}, "M5-aligned"),
        ({"pre_session_start": time(0, tzinfo=UTC)}, "M5-aligned"),
    ],
)
def test_invalid_specs_fail_clearly(changes, field):
    with pytest.raises((ValueError, TypeError), match=field):
        replace(MNQ, **changes)


@pytest.mark.parametrize("factory", [AmdCrtStrategy, amd_crt_config])
def test_invalid_injection_fails_clearly(factory):
    with pytest.raises(TypeError, match="market must be a MarketSpec"):
        factory(market="MGC")


def test_cli_defaults_preserve_mnq_and_allow_explicit_market():
    defaults = _parser().parse_args(["amd-crt"])
    assert defaults.market == "MNQ" and defaults.friction_pts is None
    explicit = _parser().parse_args(["amd-crt", "--market", "MGC", "--friction-pts", "0.8"])
    assert explicit.market == "MGC" and explicit.friction_pts == 0.8


def test_runner_injects_the_same_market_into_strategy_and_config(monkeypatch, tmp_path):
    seen = {}

    def pipeline(bars, strategy, config, **kwargs):
        seen.update(market=strategy.market, config=config)
        return {}

    monkeypatch.setattr("src.backtest.cli.load_mnq_csv", lambda *a, **k: [object()])
    monkeypatch.setattr("src.backtest.cli.run_pipeline", pipeline)
    monkeypatch.setattr("src.backtest.cli.write_report", lambda *a: {"summary": "unused"})
    assert (
        main(
            [
                "amd-crt",
                "--market",
                "MGC",
                "--friction-pts",
                "0.8",
                "--bars-csv",
                "fixture.csv",
                "--out-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert seen["market"] is MGC
    assert seen["config"] == amd_crt_config(market=MGC, friction_pts=0.8)


@pytest.mark.parametrize(
    "ema,reference",
    [
        (False, "71debb93509da2e7ce2875ba2ab1c8eb2e7cd1cb6e1602e07f4daf3c87e756e5"),
        (True, "0a80e9ef7fed6e7cb03fbb337da909ce2ff5925ac4c76977c67c5479ed3758d2"),
    ],
)
def test_mnq_bit_for_bit_against_commit_1fa30ae(ema, reference):
    # Captured on 1fa30ae BEFORE editing production code. Includes every field
    # of every trade, aggregate metrics, equity, funded simulation, decisions,
    # and parameters. Never regenerate these hashes to accommodate a refactor.
    payload = capture(ema=ema)
    assert hashlib.sha256(encoded(payload)).hexdigest() == reference
    for name, case in payload.items():
        direction, reason = name.split("_", 1)
        if ema and direction == "long":
            assert case["result"]["n_trades"] == 0
        else:
            (trade,) = case["result"]["trades"]
            assert trade["direction"] == direction
            assert (
                trade["exit_reason"]
                == {
                    "stop": "stop_loss",
                    "target": "take_profit",
                    "time_exit": "time_exit",
                }[reason]
            )
