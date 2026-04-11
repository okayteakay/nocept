from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class AppConfig(BaseSettings):
    """Application configuration loaded from environment variables.

    All settings can be overridden via a .env file or environment variables.
    See .env.example for documentation of each variable.
    """

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Tavily
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    # IBM watsonx
    watsonx_api_key: str = Field(default="", alias="WATSONX_API_KEY")
    watsonx_url: str = Field(
        default="https://us-south.ml.cloud.ibm.com", alias="WATSONX_URL"
    )
    watsonx_project_id: str = Field(default="", alias="WATSONX_PROJECT_ID")

    # Business rules
    price_tolerance_pct: float = Field(
        default=0.05,
        alias="PRICE_TOLERANCE_PCT",
        description="Fractional price variance threshold for auto-approval (0.05 = 5%)",
    )
    qty_tolerance_pct: float = Field(
        default=0.02,
        alias="QTY_TOLERANCE_PCT",
        description="Fractional quantity variance threshold for auto-approval (0.02 = 2%)",
    )
    auto_resolve_max_variance_usd: float = Field(
        default=500.0,
        alias="AUTO_RESOLVE_MAX_VARIANCE_USD",
        description="Maximum absolute USD variance eligible for auto-resolution",
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = {"env_file": ".env", "populate_by_name": True}

    def configure_logging(self) -> None:
        """Apply log_level to the root logger."""
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


@lru_cache(maxsize=1)
def get_settings() -> AppConfig:
    """Return a cached singleton AppConfig instance."""
    return AppConfig()
