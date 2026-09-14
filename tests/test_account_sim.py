"""Tests for P6 offline account simulator."""
import pytest
from src.account_sim import AccountRules, FillEvent, SimulationResult, simulate_account


def _fill(trade_id, pnl_points, cost=0.0, direction="long", qty=1, ts="2024-01-01T10:00:00"):
    return FillEvent(
        trade_id=trade_id, direction=direction,
        entry_price=100.0, exit_price=100.0 + pnl_points,
        quantity=qty, points_pnl=pnl_points,
        timestamp=ts, cost_points=cost,
    )


class TestAccountRules:
    def test_valid_rules(self):
        r = AccountRules()
        assert r.validate() == []

    def test_negative_balance_rejected(self):
        r = AccountRules(initial_balance=-100)
        errors = r.validate()
        assert any("initial_balance" in e for e in errors)


class TestSimulation:
    def test_profit_target(self):
        fills = [_fill(f"t{i}", 200) for i in range(20)]
        r = simulate_account(fills, AccountRules(profit_target=3000))
        assert r.status == "PASSED_SIMULATION"
        assert r.final_balance > 50000

    def test_max_drawdown_breach(self):
        fills = [
            _fill("loss", -500, ts="2024-01-01T10:00:00"),
            _fill("loss2", -500, ts="2024-01-02T10:00:00"),
            _fill("loss3", -500, ts="2024-01-03T10:00:00"),
            _fill("loss4", -500, ts="2024-01-04T10:00:00"),
            _fill("loss5", -500, ts="2024-01-05T10:00:00"),
            _fill("loss6", -500, ts="2024-01-06T10:00:00"),
            _fill("loss7", -500, ts="2024-01-07T10:00:00"),
        ]
        r = simulate_account(fills, AccountRules(max_drawdown=3000, daily_loss_limit=5000))
        assert r.status == "FAILED_RULES"
        assert any(b["rule"] == "max_drawdown" for b in r.breaches)

    def test_daily_loss_limit(self):
        fills = [
            _fill("d1", -200, ts="2024-01-01T10:00:00"),
            _fill("d2", -200, ts="2024-01-01T11:00:00"),
            _fill("d3", -200, ts="2024-01-01T12:00:00"),
            _fill("d4", -200, ts="2024-01-01T13:00:00"),
            _fill("d5", -200, ts="2024-01-01T14:00:00"),
            _fill("d6", -200, ts="2024-01-01T15:00:00"),
            _fill("d7", -200, ts="2024-01-01T16:00:00"),
            _fill("d8", -200, ts="2024-01-01T17:00:00"),
        ]
        r = simulate_account(fills, AccountRules(daily_loss_limit=1500))
        assert r.status == "FAILED_RULES"
        assert any(b["rule"] == "daily_loss_limit" for b in r.breaches)

    def test_equity_curve(self):
        fills = [_fill(f"t{i}", 10) for i in range(5)]
        r = simulate_account(fills, AccountRules())
        assert len(r.equity_curve) == 5
        assert r.equity_curve[0] > 50000

    def test_costs_deducted(self):
        fills = [_fill("t1", 100, cost=2.0)]
        r_no_cost = simulate_account(fills, AccountRules(included_costs=False))
        r_with_cost = simulate_account(fills, AccountRules(included_costs=True))
        assert r_with_cost.final_balance < r_no_cost.final_balance

    def test_session_reset(self):
        fills = [
            _fill("d1", -100, ts="2024-01-01T10:00:00"),
            _fill("d2", 200, ts="2024-01-02T10:00:00"),
        ]
        r = simulate_account(fills, AccountRules(daily_loss_limit=1500))
        assert r.status == "PASSED_SIMULATION"
        assert len(r.daily_results) == 2

    def test_empty_fills(self):
        r = simulate_account([], AccountRules())
        assert r.status == "PASSED_SIMULATION"
        assert r.n_trades == 0

    def test_two_symbols_shared_balance(self):
        fills = [
            _fill("mnq1", 100, ts="2024-01-01T10:00:00"),
            _fill("ym1", -200, ts="2024-01-01T11:00:00"),
        ]
        r = simulate_account(fills, AccountRules(max_drawdown=5000))
        assert r.status == "PASSED_SIMULATION"
        assert r.final_balance == pytest.approx(50000 + (100 - 200) * 2.0)

    def test_insufficient_data_not_enough(self):
        fills = [_fill("t1", 10)]
        r = simulate_account(fills, AccountRules(profit_target=50000))
        assert r.status == "PASSED_SIMULATION"
        assert r.total_pnl < 50000

    def test_session_reset_at_2300_utc_rolls_over_to_next_day(self):
        """Fill at 23:00 UTC (=19:00 EDT in summer, =18:00 EST in winter) rolls over to next trading session.

        Since session_reset_hour is 17:00 NY, any trade after 17:00 NY belongs
        to the next calendar day's trading session.
        """
        # Summer (EDT, UTC-4): 23:00 UTC = 19:00 EDT (>= 17:00 -> rolls to 2024-06-11)
        fill_evening = _fill("t_eve", -100, ts="2024-06-10T23:00:00Z")
        # Next trade at 01:00 UTC on 2024-06-11 = 21:00 EDT on 2024-06-10 (>= 17:00 -> also session 2024-06-11)
        fill_night = _fill("t_night", -100, ts="2024-06-11T01:00:00Z")
        # Next trade at 14:00 UTC on 2024-06-11 = 10:00 EDT (< 17:00 -> session 2024-06-11)
        fill_morning = _fill("t_morn", 300, ts="2024-06-11T14:00:00Z")

        r = simulate_account([fill_evening, fill_night, fill_morning], AccountRules())
        assert r.status == "PASSED_SIMULATION"
        # All three fills belong to the single session date 2024-06-11
        assert len(r.daily_results) == 1
        assert r.daily_results[0]["date"] == "2024-06-11"

    def test_session_reset_dst_transition(self):
        """Clock-based NY session reset is invariant to DST clock shifts.

        US DST transition in March 2024 occurred Sunday, 2024-03-10:
        - Before DST (Friday 2024-03-08, EST UTC-5): 17:00 NY = 22:00 UTC.
          A fill at 21:30 UTC is 16:30 EST (< 17:00) -> session 2024-03-08.
          A fill at 22:30 UTC is 17:30 EST (>= 17:00) -> session 2024-03-09 (or Monday 2024-03-09).
        - After DST (Monday 2024-03-11, EDT UTC-4): 17:00 NY = 21:00 UTC.
          A fill at 20:30 UTC is 16:30 EDT (< 17:00) -> session 2024-03-11.
          A fill at 21:30 UTC is 17:30 EDT (>= 17:00) -> session 2024-03-12.
        """
        fills = [
            _fill("pre_dst_day", 100, ts="2024-03-08T21:30:00Z"),   # 16:30 EST -> 2024-03-08
            _fill("pre_dst_eve", 100, ts="2024-03-08T22:30:00Z"),   # 17:30 EST -> 2024-03-09
            _fill("post_dst_day", 100, ts="2024-03-11T20:30:00Z"),  # 16:30 EDT -> 2024-03-11
            _fill("post_dst_eve", 100, ts="2024-03-11T21:30:00Z"),  # 17:30 EDT -> 2024-03-12
        ]
        r = simulate_account(fills, AccountRules())
        dates = [d["date"] for d in r.daily_results]
        assert dates == ["2024-03-08", "2024-03-09", "2024-03-11", "2024-03-12"]

    def test_broker_margin_reported_as_no_evaluable(self):
        """When broker margin check is requested, offline simulator returns NO_EVALUABLE without inventing broker data."""
        fills = [_fill("t1", 100, ts="2024-01-01T10:00:00Z")]
        rules = AccountRules(check_broker_margin=True)
        r = simulate_account(fills, rules)
        assert r.broker_margin_status == "NO_EVALUABLE"
        assert r.broker_margin_evaluable is False
        assert r.status == "NO_EVALUABLE"
        assert any(b["rule"] == "broker_margin" and b["status"] == "NO_EVALUABLE" for b in r.breaches)
