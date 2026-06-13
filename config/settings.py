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

    # Celery background worker
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1",
        alias="CELERY_BROKER_URL",
        description="Celery broker URL (Redis instance)",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1",
        alias="CELERY_RESULT_BACKEND",
        description="Celery result backend URL",
    )

    # Notifications — Slack
    slack_webhook_url: str = Field(
        default="",
        alias="SLACK_WEBHOOK_URL",
        description="Slack incoming webhook URL for escalation alerts",
    )
    slack_escalation_channel: str = Field(
        default="#ap-escalations",
        alias="SLACK_ESCALATION_CHANNEL",
        description="Slack channel for escalated exceptions",
    )

    # Notifications — Email (SMTP)
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from_email: str = Field(default="noreply@meridian-ap.local", alias="SMTP_FROM_EMAIL")
    notification_email_to: str = Field(
        default="",
        alias="NOTIFICATION_EMAIL_TO",
        description="Comma-separated list of email addresses for escalation alerts",
    )

    # SAP Webhook security
    sap_webhook_secret: str = Field(
        default="",
        alias="SAP_WEBHOOK_SECRET",
        description="Shared secret for SAP webhook HMAC-SHA256 signature verification",
    )

    # JWT/OAuth2 authentication
    jwt_secret_key: str = Field(
        default="dev-secret-key-change-in-production",
        alias="JWT_SECRET_KEY",
        description="Secret key for signing JWT tokens",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    # Timeouts and resilience
    openai_timeout_secs: float = Field(
        default=30.0,
        alias="OPENAI_TIMEOUT_SECS",
        description="Timeout in seconds for OpenAI API calls (comms analysis)",
    )
    tavily_timeout_secs: float = Field(
        default=30.0,
        alias="TAVILY_TIMEOUT_SECS",
        description="Timeout in seconds for Tavily search API calls",
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
