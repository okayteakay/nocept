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

    # OpenAI-compatible — read directly from env by comms_checker (Step 4).
    # Documented here so .env requirements are discoverable in one place.
    # Set OPENAI_BASE_URL to your nanogpt endpoint; leave blank for standard OpenAI.
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # Tavily — used by researcher.py (Step 5) and generate_real_company_data.py
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

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

    # Knowledge base / embeddings
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        alias="EMBEDDING_MODEL",
        description="sentence-transformers model name used for vector search",
    )
    vector_dimensions: int = Field(
        default=384,
        alias="VECTOR_DIMENSIONS",
        description="Output dimension of the embedding model (384 for all-MiniLM-L6-v2)",
    )
    vector_index_prefix: str = Field(
        default="kb:",
        alias="VECTOR_INDEX_PREFIX",
        description="Key prefix namespace for all knowledge-base Redis keys",
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
