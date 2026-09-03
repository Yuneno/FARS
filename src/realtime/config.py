"""Local credential loading for the ProjectX connector."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ProjectXConfigurationError(ValueError):
    """Raised when local ProjectX configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class ProjectXCredentials:
    username: str
    api_key: str = field(repr=False)
    account_name: str | None = None

    def __post_init__(self) -> None:
        if not self.username.strip():
            raise ProjectXConfigurationError("FARS_PROJECTX_USERNAME is required")
        if not self.api_key.strip():
            raise ProjectXConfigurationError("FARS_PROJECTX_API_KEY is required")
        object.__setattr__(self, "username", self.username.strip())
        object.__setattr__(self, "api_key", self.api_key.strip())
        if self.account_name is not None:
            value = self.account_name.strip()
            object.__setattr__(self, "account_name", value or None)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum():
            raise ProjectXConfigurationError(f"invalid .env assignment on line {line_number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def load_projectx_credentials(
    env_file: str | Path = ".env",
    environ: Mapping[str, str] | None = None,
) -> ProjectXCredentials:
    """Load credentials without executing shell syntax or logging secrets."""
    file_values = _parse_env_file(Path(env_file))
    active_environment = os.environ if environ is None else environ

    def value(name: str) -> str:
        return active_environment.get(name, file_values.get(name, ""))

    return ProjectXCredentials(
        username=value("FARS_PROJECTX_USERNAME"),
        api_key=value("FARS_PROJECTX_API_KEY"),
        account_name=value("FARS_PROJECTX_ACCOUNT_NAME") or None,
    )
