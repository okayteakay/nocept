"""Celery tasks for background exception processing."""
from __future__ import annotations

import logging
from celery.exceptions import SoftTimeLimitExceeded

from agent.langgraph_agent import run_pipeline
from audit.audit_logger import AuditEvent, AuditLogger
from clients.redis_client import RedisStreamsClient, get_redis_connection
from clients.tavily_client import TavilyClient
from config.settings import get_settings
from state.redis_backend import RedisStateStore
from worker.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_exception(self, exception_id: str) -> dict:
    """Process an invoice exception through the full LangGraph pipeline.

    Runs asynchronously in Celery. On transient failures (LLM timeouts,
    Tavily API errors, Redis timeouts), retries with exponential backoff.

    Args:
        self: Celery task instance (for retry logic)
        exception_id: ID of the exception to process

    Returns:
        Dict with exception_id, final_state, and action_taken

    Raises:
        KeyError: if exception not found
        Exception: if pipeline fails permanently
    """
    logger.info(f"[Task {self.request.id}] Starting pipeline for {exception_id}")

    try:
        # Initialise dependencies
        settings = get_settings()
        settings.configure_logging()

        r = get_redis_connection(settings.redis_url)
        store = RedisStateStore(r)
        streams = RedisStreamsClient(r, "ap:audit:events")
        audit = AuditLogger(streams)
        tavily = TavilyClient(settings.tavily_api_key)

        # Run the pipeline
        resolution = run_pipeline(
            exception_id,
            store,
            audit,
            settings,
            tavily,
        )

        logger.info(
            f"[Task {self.request.id}] Pipeline complete: "
            f"{exception_id} → {resolution.final_state.value}"
        )

        return {
            "exception_id": exception_id,
            "final_state": resolution.final_state.value,
            "action_taken": resolution.memo.action.value if resolution.memo else "UNKNOWN",
            "approved_by_step": resolution.memo.root_cause if resolution.memo else None,
        }

    except SoftTimeLimitExceeded:
        logger.warning(
            f"[Task {self.request.id}] Soft time limit exceeded for {exception_id}"
        )
        # Retry with backoff
        retry_delay = 60 * (2 ** (self.request.retries + 1))
        logger.info(
            f"[Task {self.request.id}] Retrying in {retry_delay}s "
            f"(attempt {self.request.retries + 1}/3)"
        )
        raise self.retry(exc=SoftTimeLimitExceeded(), countdown=retry_delay)

    except Exception as e:
        logger.error(
            f"[Task {self.request.id}] Error processing {exception_id}: {type(e).__name__}: {e}",
            exc_info=True,
        )

        # Transient errors: retry with exponential backoff
        transient_errors = (
            TimeoutError,
            ConnectionError,
            OSError,  # Redis connection issues
        )

        if isinstance(e, transient_errors) and self.request.retries < self.max_retries:
            retry_delay = 60 * (2 ** (self.request.retries + 1))
            logger.info(
                f"[Task {self.request.id}] Transient error; retrying in {retry_delay}s "
                f"(attempt {self.request.retries + 1}/{self.max_retries})"
            )
            raise self.retry(exc=e, countdown=retry_delay)

        # Permanent error or exhausted retries: fail the task
        logger.error(
            f"[Task {self.request.id}] Permanent failure or max retries exceeded for {exception_id}"
        )
        raise
