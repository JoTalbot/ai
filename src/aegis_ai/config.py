"""Environment-based configuration with validation and safe defaults."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "local-model"
    api_key: str | None = None
    timeout_seconds: float = 60.0
    audit_db: Path = Path.home() / ".aegis" / "audit.db"

    def __post_init__(self) -> None:
        parts = urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ConfigurationError("base_url must be an absolute HTTP or HTTPS URL")
        if not self.model.strip():
            raise ConfigurationError("model must not be empty")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ConfigurationError("timeout_seconds must be a positive finite number")

    @classmethod
    def from_env(cls) -> Settings:
        raw_timeout = os.getenv("AEGIS_TIMEOUT_SECONDS", "60")
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ConfigurationError("AEGIS_TIMEOUT_SECONDS must be a number") from exc

        return cls(
            base_url=os.getenv("AEGIS_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/"),
            model=os.getenv("AEGIS_MODEL", "local-model"),
            api_key=os.getenv("AEGIS_API_KEY") or None,
            timeout_seconds=timeout,
            audit_db=Path(
                os.getenv("AEGIS_AUDIT_DB", str(Path.home() / ".aegis" / "audit.db"))
            ).expanduser(),
        )
