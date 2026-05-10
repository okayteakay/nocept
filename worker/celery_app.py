"""Celery application for background invoice exception processing."""
from celery import Celery
from config.settings import get_settings

settings = get_settings()

app = Celery(
    "ap_agent",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes={"worker.tasks.*": {"queue": "ap_pipeline"}},
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes hard limit
    task_soft_time_limit=25 * 60,  # 25 minutes soft timeout
    worker_prefetch_multiplier=1,  # Process one task at a time
    worker_max_tasks_per_child=100,  # Restart worker every 100 tasks
)
