"""RT-8 explicit acceptance result and structured artifact tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.realtime.acceptance as acceptance_module
from src.realtime.acceptance import (
    REQUIRED_CHECKS,
    RT8AcceptanceResult,
    RT8CheckResult,
    RT8_FAIL,
    RT8_PASS,
    run_rt8_acceptance,
    write_rt8_acceptance_report,
)
from src.realtime.interfaces import LIVE_EXECUTION_ENABLED


def test_rt8_acceptance_is_explicit_complete_and_keeps_live_disabled(tmp_path):
    result = run_rt8_acceptance(artifact_root=tmp_path)

    assert result.status == RT8_PASS
    assert tuple(check.name for check in result.checks) == REQUIRED_CHECKS
    assert all(check.passed for check in result.checks)
    assert result.failures == ()
    assert result.live_execution_enabled is False
    assert LIVE_EXECUTION_ENABLED is False
    assert any("not a live broker" in item for item in result.limitations)


def test_any_failed_check_forces_fail_status(monkeypatch, tmp_path):
    def passing(_root):
        return "passed"

    def failing(_root):
        raise AssertionError("forced acceptance failure")

    checks = tuple(
        (name, failing if name == "risk_enforcement" else passing)
        for name in REQUIRED_CHECKS
    )
    monkeypatch.setattr(acceptance_module, "_CHECKS", checks)

    result = run_rt8_acceptance(artifact_root=tmp_path)
    assert result.status == RT8_FAIL
    assert [check.name for check in result.failures] == ["risk_enforcement"]
    assert "forced acceptance failure" in result.failures[0].detail


def test_result_rejects_status_inconsistent_with_checks():
    checks = tuple(RT8CheckResult(name, True, "passed") for name in REQUIRED_CHECKS)

    with pytest.raises(ValueError, match="inconsistent"):
        RT8AcceptanceResult(
            status=RT8_FAIL,
            checks=checks,
            live_execution_enabled=False,
            limitations=("test",),
        )


def test_json_report_matches_structured_result(tmp_path):
    result = run_rt8_acceptance(artifact_root=tmp_path)
    path = tmp_path / "rt8-acceptance.json"
    write_rt8_acceptance_report(result, path)

    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed == result.to_dict()
    assert "NaN" not in path.read_text(encoding="utf-8")


def test_committed_acceptance_artifact_matches_current_harness(tmp_path):
    result = run_rt8_acceptance(artifact_root=tmp_path)
    committed = json.loads(
        (Path(__file__).parents[2] / "RT8_ACCEPTANCE.json").read_text(encoding="utf-8")
    )

    assert committed == result.to_dict()
