"""Package-level API and import-side-effect regressions."""

from pathlib import Path
import subprocess
import sys


def test_numerical_submodule_import_does_not_initialize_matplotlib():
    project_root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; "
        "import src.account; "
        "assert 'matplotlib' not in sys.modules; "
        "from src import Trade; "
        "assert Trade.__name__ == 'Trade'; "
        "from src import load_trade_csv; "
        "assert load_trade_csv.__name__ == 'load_trade_csv'; "
        "from src import IngestionProvenance; "
        "assert IngestionProvenance.__name__ == 'IngestionProvenance'; "
        "from src import load_account_trade_csv; "
        "assert load_account_trade_csv.__name__ == 'load_account_trade_csv'; "
        "from src import FundedAccountStateV2, rapid_25k_profile; "
        "assert FundedAccountStateV2.__name__ == 'FundedAccountStateV2'; "
        "assert rapid_25k_profile.__name__ == 'rapid_25k_profile'; "
        "from src import CAP_CLOSED_TRADE_EVENTS; "
        "assert CAP_CLOSED_TRADE_EVENTS == 'closed_trade_events'; "
        "assert 'matplotlib' not in sys.modules"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
