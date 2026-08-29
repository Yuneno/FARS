"""Tests for the Phase 9A/10B CLI (src/cli.py)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.cli import (
    EXIT_BLOCKED,
    EXIT_OK,
    EXIT_STRUCTURAL,
    EXIT_USAGE,
    main,
)

GOOD_CSV = "r_result\n1.5\n-1.0\n2.0\n0.5\n-1.0\n"
SINGLE_TRADE_CSV = "r_result\n1.5\n"
REJECTED_ROW_CSV = "r_result\n1.5\nnot_a_number\n2.0\n"
MAPPED_CSV = "ResultR,Notes\n1.5,a\n-1.0,b\n"


def _write(tmp_path, content: str, name: str = "trades.csv"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _bootstrap_csv(n: int = 80, *, timestamps: bool = True) -> str:
    """Deterministic, nonconstant historical sample for Phase 10B CLI tests."""
    state = 9173
    values: list[float] = []
    for _ in range(n):
        state = (48271 * state) % 2147483647
        values.append(2.0 * (state / 2147483647.0 - 0.45))
    if not timestamps:
        return "r_result\n" + "\n".join(repr(float(value)) for value in values) + "\n"
    t0 = datetime(2024, 1, 2, tzinfo=timezone.utc)
    rows = ["timestamp,r_result"]
    rows.extend(
        f"{(t0 + timedelta(hours=i)).isoformat()},{float(value)!r}"
        for i, value in enumerate(values)
    )
    return "\n".join(rows) + "\n"


def test_audit_text_ok(tmp_path, capsys):
    path = _write(tmp_path, GOOD_CSV)
    code = main(["audit", str(path), "--outcomes-finalized"])
    out, err = capsys.readouterr()
    assert code == EXIT_OK
    assert "accepted=5" in out
    assert "rejected=0" in out
    assert "core_metrics: available" in out
    assert "source_sha256" in out
    assert err == ""


def test_audit_json_ok(tmp_path, capsys):
    path = _write(tmp_path, GOOD_CSV)
    code = main(["audit", str(path), "--outcomes-finalized", "--format", "json"])
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["rows"] == {"total": 5, "accepted": 5, "rejected": 0}
    assert payload["capabilities"]["core_metrics"]["available"] is True
    assert len(payload["provenance"]["source_sha256"]) == 64
    assert payload["provenance"]["outcomes_finalized"] is True


def test_metrics_text_ok(tmp_path, capsys):
    path = _write(tmp_path, GOOD_CSV)
    code = main(["metrics", str(path), "--outcomes-finalized"])
    out, err = capsys.readouterr()
    assert code == EXIT_OK
    assert "Trades: 5" in out
    assert "Win rate: 0.6" in out
    assert "Expectancy (R): 0.4" in out
    assert err == ""


def test_metrics_json_values(tmp_path, capsys):
    path = _write(tmp_path, GOOD_CSV)
    code = main(["metrics", str(path), "--outcomes-finalized", "--format", "json"])
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    metrics = json.loads(out)["metrics"]
    assert metrics["n_trades"] == 5
    assert metrics["win_rate"] == pytest.approx(0.6)
    assert metrics["expectancy_r"] == pytest.approx(0.4)
    assert metrics["max_losing_streak"] == 1


def test_metrics_json_undefined_values_are_null(tmp_path, capsys):
    path = _write(tmp_path, SINGLE_TRADE_CSV)
    code = main(["metrics", str(path), "--outcomes-finalized", "--format", "json"])
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    metrics = json.loads(out)["metrics"]
    assert metrics["n_trades"] == 1
    assert metrics["std_r"] is None
    assert metrics["skewness"] is None
    assert metrics["kurtosis"] is None


def test_metrics_text_undefined_values_render_na(tmp_path, capsys):
    path = _write(tmp_path, SINGLE_TRADE_CSV)
    code = main(["metrics", str(path), "--outcomes-finalized"])
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    assert "Std (R): n/a (statistically undefined)" in out


def test_metrics_blocked_when_core_metrics_unavailable(tmp_path, capsys):
    path = _write(tmp_path, REJECTED_ROW_CSV)
    code = main(["metrics", str(path), "--outcomes-finalized"])
    out, err = capsys.readouterr()
    assert code == EXIT_BLOCKED
    assert "core_metrics is unavailable" in err
    assert "rejected=1" in out


def test_audit_reports_rejected_rows_but_succeeds(tmp_path, capsys):
    path = _write(tmp_path, REJECTED_ROW_CSV)
    code = main(["audit", str(path), "--outcomes-finalized", "--format", "json"])
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["rows"]["rejected"] == 1
    assert payload["capabilities"]["core_metrics"]["available"] is False


def test_missing_outcomes_finalized_is_usage_error(tmp_path):
    path = _write(tmp_path, GOOD_CSV)
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", str(path)])
    assert excinfo.value.code == EXIT_USAGE


def test_multi_char_delimiter_is_usage_error(tmp_path):
    path = _write(tmp_path, GOOD_CSV)
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", str(path), "--outcomes-finalized", "--delimiter", ";;"])
    assert excinfo.value.code == EXIT_USAGE


@pytest.mark.parametrize("bad", ['"', "\n", "\r"])
def test_csv_incompatible_delimiter_is_usage_error(tmp_path, bad):
    path = _write(tmp_path, GOOD_CSV)
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", str(path), "--outcomes-finalized", "--delimiter", bad])
    assert excinfo.value.code == EXIT_USAGE


def test_tab_and_space_are_valid_delimiters(tmp_path, capsys):
    for delim in ("\t", " "):
        path = tmp_path / "data.csv"
        path.write_text(f"r_result{delim}extra\n1.5{delim}x\n-1.0{delim}y\n", encoding="utf-8")
        code = main(
            ["audit", str(path), "--outcomes-finalized", "--delimiter", delim, "--format", "json"]
        )
        out, _ = capsys.readouterr()
        assert code == EXIT_OK
        assert json.loads(out)["rows"]["accepted"] == 2


def test_extreme_values_overflow_blocks_metrics(tmp_path, capsys):
    path = _write(tmp_path, "r_result\n1e308\n1e308\n")
    code = main(["metrics", str(path), "--outcomes-finalized"])
    out, err = capsys.readouterr()
    assert code == EXIT_BLOCKED
    assert "overflow" in err
    assert "null" not in out
    # The audit is still emitted so the failure stays connected to its data.
    assert "Rows: total=2 accepted=2 rejected=0" in out


def test_tiny_nonconstant_values_underflow_blocks_metrics_with_audit(
    tmp_path, capsys
):
    path = _write(
        tmp_path,
        "r_result\n1e-162\n-1e-162\n2e-162\n-0.5e-162\n",
    )
    code = main(
        [
            "metrics",
            str(path),
            "--outcomes-finalized",
            "--format",
            "json",
        ]
    )
    out, err = capsys.readouterr()

    assert code == EXIT_BLOCKED
    assert "underflowed or overflowed" in err
    payload = json.loads(out)
    assert payload["rows"] == {"total": 4, "accepted": 4, "rejected": 0}
    assert len(payload["provenance"]["source_sha256"]) == 64
    assert "metrics" not in payload


def test_missing_file_is_structural_error(tmp_path, capsys):
    code = main(["audit", str(tmp_path / "nope.csv"), "--outcomes-finalized"])
    _, err = capsys.readouterr()
    assert code == EXIT_STRUCTURAL
    assert "error:" in err


def test_unknown_timezone_is_structural_error(tmp_path, capsys):
    path = _write(tmp_path, GOOD_CSV)
    code = main(
        ["audit", str(path), "--outcomes-finalized", "--timezone", "Not/AZone"]
    )
    _, err = capsys.readouterr()
    assert code == EXIT_STRUCTURAL
    assert "timezone" in err


def test_column_mapping_ok(tmp_path, capsys):
    path = _write(tmp_path, MAPPED_CSV)
    code = main(
        [
            "metrics",
            str(path),
            "--outcomes-finalized",
            "--map",
            "ResultR=r_result",
            "--format",
            "json",
        ]
    )
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["metrics"]["n_trades"] == 2
    assert payload["provenance"]["resolved_mapping"]["ResultR"] == "r_result"


def test_invalid_map_format_is_usage_error(tmp_path):
    path = _write(tmp_path, GOOD_CSV)
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", str(path), "--outcomes-finalized", "--map", "badformat"])
    assert excinfo.value.code == EXIT_USAGE


def test_invalid_arguments_are_usage_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        main(["audit"])
    assert excinfo.value.code == EXIT_USAGE


def test_delimiter_and_encoding_options(tmp_path, capsys):
    path = tmp_path / "semi.csv"
    path.write_text("r_result;extra\n1.5;x\n-1.0;y\n", encoding="utf-8")
    code = main(
        [
            "audit",
            str(path),
            "--outcomes-finalized",
            "--delimiter",
            ";",
            "--encoding",
            "utf-8",
            "--format",
            "json",
        ]
    )
    out, _ = capsys.readouterr()
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["rows"]["accepted"] == 2
    assert payload["provenance"]["delimiter"] == ";"


def test_bootstrap_json_is_reproducible_and_preserves_source(tmp_path):
    import os
    import subprocess
    import sys

    path = _write(tmp_path, _bootstrap_csv())
    source_before = path.read_bytes()
    command = [
        sys.executable,
        "-m",
        "src.cli",
        "bootstrap",
        str(path),
        "--outcomes-finalized",
        "--seed",
        "42",
        "--replicates",
        "2001",
        "--format",
        "json",
    ]
    project_root = Path(__file__).resolve().parent.parent
    mpl_cache = tmp_path / "mpl-cache"
    mpl_cache.mkdir()
    env = {**os.environ, "MPLCONFIGDIR": str(mpl_cache)}

    first = subprocess.run(
        command, cwd=project_root, capture_output=True, env=env, check=False
    )
    second = subprocess.run(
        command, cwd=project_root, capture_output=True, env=env, check=False
    )

    assert first.returncode == second.returncode == EXIT_OK
    assert first.stdout == second.stdout
    assert path.read_bytes() == source_before
    assert b"NaN" not in first.stdout
    payload = json.loads(first.stdout)
    result = payload["bootstrap"]
    assert result["rng"]["master_entropy"] == 42
    assert result["parameters"]["B"] == 2001
    assert result["provenance"]["source_sha256"] == payload["provenance"]["source_sha256"]
    assert result["eligibility"]["state"] in {
        "iid_eligible",
        "dependent_resampling_candidate",
    }


def test_bootstrap_text_discloses_validity_and_parameters(tmp_path, capsys):
    path = _write(tmp_path, _bootstrap_csv())
    code = main(
        ["bootstrap", str(path), "--outcomes-finalized", "--seed", "7"]
    )
    out, err = capsys.readouterr()

    assert code == EXIT_OK
    assert err == ""
    assert "Bootstrap:" in out
    assert "eligibility:" in out
    assert "seed: 7" in out
    assert "replicates: 2000" in out
    assert "validity=" in out


def test_bootstrap_refuses_missing_temporal_capability_with_structured_result(
    tmp_path, capsys
):
    path = _write(tmp_path, _bootstrap_csv(timestamps=False))
    code = main(
        [
            "bootstrap",
            str(path),
            "--outcomes-finalized",
            "--seed",
            "1",
            "--format",
            "json",
        ]
    )
    out, err = capsys.readouterr()

    assert code == EXIT_BLOCKED
    result = json.loads(out)["bootstrap"]
    assert result["eligibility"]["reasons"] == [
        "capability_unavailable:temporal_analysis"
    ]
    assert "capability_unavailable:temporal_analysis" in err


def test_bootstrap_refuses_missing_core_capability_with_structured_result(
    tmp_path, capsys
):
    rows = _bootstrap_csv().splitlines()
    timestamp, _value = rows[10].split(",", 1)
    rows[10] = f"{timestamp},not-a-number"
    path = _write(tmp_path, "\n".join(rows) + "\n")

    code = main(
        [
            "bootstrap",
            str(path),
            "--outcomes-finalized",
            "--seed",
            "1",
            "--format",
            "json",
        ]
    )
    out, err = capsys.readouterr()

    assert code == EXIT_BLOCKED
    payload = json.loads(out)
    assert "capability_unavailable:core_metrics" in payload["bootstrap"][
        "eligibility"
    ]["reasons"]
    assert "capability_unavailable:core_metrics" in err
    assert payload["rows"]["rejected"] == 1


def test_bootstrap_unsupported_diagnostics_return_blocked_with_result(tmp_path, capsys):
    path = _write(tmp_path, _bootstrap_csv(n=49))
    code = main(
        [
            "bootstrap",
            str(path),
            "--outcomes-finalized",
            "--seed",
            "1",
            "--format",
            "json",
        ]
    )
    out, err = capsys.readouterr()

    assert code == EXIT_BLOCKED
    result = json.loads(out)["bootstrap"]
    assert result["eligibility"]["reasons"] == [
        "insufficient_sample_for_diagnostics"
    ]
    assert "unsupported or inconclusive" in err


@pytest.mark.parametrize(
    "option,value",
    [
        ("--seed", "-1"),
        ("--seed", "not-an-integer"),
        ("--replicates", "1999"),
        ("--replicates", "2.5"),
    ],
)
def test_bootstrap_invalid_seed_or_replicates_is_usage_error(
    tmp_path, option, value
):
    path = _write(tmp_path, _bootstrap_csv())
    args = [
        "bootstrap",
        str(path),
        "--outcomes-finalized",
        "--seed",
        "1",
        option,
        value,
    ]
    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert excinfo.value.code == EXIT_USAGE


def test_bootstrap_requires_seed(tmp_path):
    path = _write(tmp_path, _bootstrap_csv())
    with pytest.raises(SystemExit) as excinfo:
        main(["bootstrap", str(path), "--outcomes-finalized"])
    assert excinfo.value.code == EXIT_USAGE


def test_cli_module_invocation_subprocess(tmp_path):
    import subprocess
    import sys

    path = _write(tmp_path, GOOD_CSV)
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "metrics",
            str(path),
            "--outcomes-finalized",
            "--format",
            "json",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == EXIT_OK, result.stderr
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n_trades"] == 5


def test_cli_module_invocation_missing_flag_exits_2(tmp_path):
    import subprocess
    import sys

    path = _write(tmp_path, GOOD_CSV)
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "audit", str(path)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == EXIT_USAGE
    assert "outcomes-finalized" in result.stderr


def test_installed_fars_binary_runs_outside_checkout(tmp_path):
    """Regression: the installed `fars` entry point must import cleanly.

    Installs the project into a throwaway prefix (no deps, no build isolation
    to stay offline) and runs the generated binary from a directory that is
    not the repository, so `src` cannot be picked up from the checkout.
    """
    import os
    import shutil
    import subprocess
    import sys
    import sysconfig

    project_root = Path(__file__).resolve().parent.parent
    # Install from a copy so build artifacts never pollute the checkout.
    staging = tmp_path / "pkg"
    staging.mkdir()
    shutil.copytree(
        project_root / "src",
        staging / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(project_root / "pyproject.toml", staging)
    shutil.copy2(project_root / "README.md", staging)
    install_prefix = tmp_path / "install"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--no-build-isolation",
            "--ignore-installed",
            "--prefix",
            str(install_prefix),
            str(staging),
        ],
        check=True,
        capture_output=True,
    )

    prefix_vars = {"base": str(install_prefix), "platbase": str(install_prefix)}
    scripts_dir = Path(sysconfig.get_path("scripts", vars=prefix_vars))
    site_packages = Path(sysconfig.get_path("purelib", vars=prefix_vars))
    fars = scripts_dir / ("fars.exe" if os.name == "nt" else "fars")
    assert fars.exists()

    csv_path = tmp_path / "trades.csv"
    csv_path.write_text(GOOD_CSV, encoding="utf-8")
    result = subprocess.run(
        [str(fars), "metrics", str(csv_path), "--outcomes-finalized", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(site_packages)},
    )
    assert result.returncode == EXIT_OK, result.stderr
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n_trades"] == 5
