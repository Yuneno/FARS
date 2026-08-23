"""Tests for the Phase 9A CLI (src/cli.py)."""

from __future__ import annotations

import json
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

    Installs the project into a throwaway venv (no deps, no build isolation to
    stay offline) and runs the generated binary from a directory that is not
    the repository, so `src` cannot be picked up from the checkout.
    """
    import shutil
    import subprocess
    import sys

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
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    pip = venv_dir / "bin" / "pip"
    subprocess.run(
        [
            str(pip),
            "install",
            "--quiet",
            "--no-deps",
            "--no-build-isolation",
            str(staging),
        ],
        check=True,
        capture_output=True,
    )
    fars = venv_dir / "bin" / "fars"
    assert fars.exists()

    csv_path = tmp_path / "trades.csv"
    csv_path.write_text(GOOD_CSV, encoding="utf-8")
    result = subprocess.run(
        [str(fars), "metrics", str(csv_path), "--outcomes-finalized", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == EXIT_OK, result.stderr
    payload = json.loads(result.stdout)
    assert payload["metrics"]["n_trades"] == 5
