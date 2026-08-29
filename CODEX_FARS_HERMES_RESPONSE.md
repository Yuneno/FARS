# Codex RT-4 follow-up

CRITICAL: valid. Replay now takes replay_session(), wait_idle() before the first clock.set, and rejects foreign publishers. Dispatcher subscribers can still publish. Regression: test_replay_waits_for_busy_bus_before_moving_clock, test_replay_rejects_concurrent_producers.

WARNING origin: not rewritten. Spec §10: origin is provenance. Live journals stay live. Replay is FrozenClock + exclusive bus. Documented on ReplayEngine. Tests: test_live_journal_keeps_recorded_origin, test_recorded_decision_chain_is_replayed_as_stored.

WARNING reproducibility: valid as a test gap. test_replay_is_bit_for_bit_across_two_pipelines runs the same journal twice on fresh buses and compares order, observed clocks, Signals, and RiskDecisions.

131 realtime tests passed. No commit.
