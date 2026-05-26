"""Celery application configuration."""

from celery import Celery
from verify_service.config import get_settings


def make_celery() -> Celery:
    settings = get_settings()
    return Celery(
        "verify_service",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )


celery_app = make_celery()
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.task_track_started = True
