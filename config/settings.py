from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
    """Application configuration loaded from environment variables.

    All settings can be overridden via a .env file or environment variables.
    See .env.example for documentation of each variable.
    """

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # OpenAI-compatible (vision-capable model required for image/pdf parsing)
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # Business rules
    price_tolerance_pct: float = Field(
        default=0.01,
        alias="PRICE_TOLERANCE_PCT",
        description="Fractional price variance threshold for auto-approval (0.01 = 1%)",
    )
    qty_tolerance_pct: float = Field(
        default=0.02,
        alias="QTY_TOLERANCE_PCT",
        description="Fractional quantity variance threshold for auto-approval (0.02 = 2%)",
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Timeouts
    openai_timeout_secs: float = Field(
        default=30.0,
        alias="OPENAI_TIMEOUT_SECS",
        description="Timeout in seconds for OpenAI API calls",
    )
    redis_timeout_secs: float = Field(
        default=5.0,
        alias="REDIS_TIMEOUT_SECS",
        description="Timeout in seconds for Redis operations",
    )

    model_config = {
        "env_file": str(Path(__file__).resolve().parent.parent / ".env"),
        "populate_by_name": True,
        "extra": "ignore",
    }

    def configure_logging(self) -> None:
        """Apply log_level to the root logger."""
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


@lru_cache(maxsize=1)
def get_settings() -> AppConfig:
    """Return a cached singleton AppConfig instance."""
    return AppConfig()
