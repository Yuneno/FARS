from pathlib import Path

import pytest

from src.realtime.config import ProjectXConfigurationError, load_projectx_credentials


def test_env_file_is_parsed_without_shell_execution(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "FARS_PROJECTX_USERNAME=test-user\n"
        "FARS_PROJECTX_API_KEY='secret-key'\n"
        "FARS_PROJECTX_ACCOUNT_NAME=TEST-ACCOUNT\n",
        encoding="utf-8",
    )
    credentials = load_projectx_credentials(path, environ={})
    assert credentials.username == "test-user"
    assert credentials.api_key == "secret-key"
    assert credentials.account_name == "TEST-ACCOUNT"


def test_process_environment_overrides_file(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "FARS_PROJECTX_USERNAME=file-user\nFARS_PROJECTX_API_KEY=file-key\n",
        encoding="utf-8",
    )
    credentials = load_projectx_credentials(
        path,
        environ={
            "FARS_PROJECTX_USERNAME": "env-user",
            "FARS_PROJECTX_API_KEY": "env-key",
        },
    )
    assert credentials.username == "env-user"
    assert credentials.api_key == "env-key"


def test_missing_key_fails_before_network(tmp_path: Path):
    with pytest.raises(ProjectXConfigurationError, match="API_KEY"):
        load_projectx_credentials(tmp_path / "missing", environ={"FARS_PROJECTX_USERNAME": "x"})


def test_invalid_env_line_fails_closed(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("not a valid assignment\n", encoding="utf-8")
    with pytest.raises(ProjectXConfigurationError, match="invalid .env"):
        load_projectx_credentials(path, environ={})
