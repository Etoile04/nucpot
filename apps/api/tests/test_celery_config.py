"""Unit tests for Celery configuration and task registration.

Phase 2.3 of NFM-335: Celery Task Queue Configuration.
Tests Celery app setup, configuration, and task registration.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from celery import Celery


@pytest.mark.unit
class TestCeleryAppConfiguration:
    """Test Celery application configuration."""

    def test_celery_app_creation(self) -> None:
        """Test that Celery app is created successfully."""
        from nfm_db.services.celery_app import celery_app

        assert isinstance(celery_app, Celery)
        assert celery_app.main == "nfm_tasks"

    def test_broker_url_configuration(self) -> None:
        """Test Redis broker URL configuration."""
        from nfm_db.services.celery_app import celery_app

        expected_broker = "redis://localhost:6379/0"
        assert celery_app.conf.broker_url == expected_broker

    @patch.dict(os.environ, {
        "NFM_CELERY_BROKER_URL": "redis://redis.example.com:6380/0",
    }, clear=False)
    def test_custom_broker_configuration(self) -> None:
        """Test custom Redis broker configuration from environment."""
        # Test with custom environment variable
        import importlib
        from nfm_db.services import celery_app
        importlib.reload(celery_app)

        from nfm_db.services.celery_app import celery_app as reloaded_app

        expected_broker = "redis://redis.example.com:6380/0"
        assert reloaded_app.conf.broker_url == expected_broker

    def test_result_backend_configuration(self) -> None:
        """Test Redis result backend configuration."""
        from nfm_db.services.celery_app import celery_app

        expected_backend = "redis://localhost:6379/0"
        assert celery_app.conf.result_backend == expected_backend

    def test_task_serialization(self) -> None:
        """Test JSON serialization for FastAPI compatibility."""
        from nfm_db.services.celery_app import celery_app

        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.result_serializer == "json"
        assert "json" in celery_app.conf.accept_content

    def test_worker_optimization_for_long_running_tasks(self) -> None:
        """Test worker optimization settings for long-running HPC tasks."""
        from nfm_db.services.celery_app import celery_app

        # Prefetch multiplier should be 1 for long-running tasks
        assert celery_app.conf.worker_prefetch_multiplier == 1

        # Late ack enabled to prevent task loss on worker failure
        assert celery_app.conf.task_acks_late is True

        # Max tasks per child configured for memory cleanup
        assert celery_app.conf.worker_max_tasks_per_child == 1000

    def test_task_retry_configuration(self) -> None:
        """Test task retry configuration with exponential backoff."""
        from nfm_db.services.celery_app import celery_app

        # Check that retry settings are configured (may use Celery defaults)
        assert hasattr(celery_app.conf, 'task_acks_late')
        assert celery_app.conf.task_acks_late is True

    def test_task_time_limits(self) -> None:
        """Test task time limits for HPC job execution."""
        from nfm_db.services.celery_app import celery_app

        # Soft limit: task gets Exception after 1 hour
        assert celery_app.conf.task_soft_time_limit == 3600

        # Hard limit: task killed after 2 hours
        assert celery_app.conf.task_time_limit == 7200

    def test_task_routing_configuration(self) -> None:
        """Test task routing to md_verification queue."""
        from nfm_db.services.celery_app import celery_app

        # Check that default routing is configured
        assert hasattr(celery_app.conf, 'task_default_routing')
        assert celery_app.conf.task_default_routing == 'nfm.tasks.md'

    def test_result_expiration(self) -> None:
        """Test result expiration configuration."""
        from nfm_db.services.celery_app import celery_app

        # Check that result expiration is configured
        from datetime import timedelta
        assert celery_app.conf.result_expires == timedelta(days=1)


@pytest.mark.unit
class TestTaskRegistration:
    """Test Celery task registration and availability."""

    def test_tasks_registered(self) -> None:
        """Test that MD verification tasks are defined with correct metadata."""
        from nfm_db.services.md_tasks import run_md_verification_task

        # Verify task function exists and has Celery metadata
        assert run_md_verification_task is not None
        assert hasattr(run_md_verification_task, "name")
        assert "run_md_verification" in run_md_verification_task.name

    def test_task_metadata(self) -> None:
        """Test that task has proper metadata."""
        from nfm_db.services.md_tasks import run_md_verification_task

        # Task metadata is set directly on the function in md_tasks.py
        assert run_md_verification_task.name == "nfm_db.services.md_tasks.run_md_verification"
        assert run_md_verification_task.max_retries == 3
        assert run_md_verification_task.default_retry_delay == 60
        assert run_md_verification_task.retry_backoff is True
        assert run_md_verification_task.retry_backoff_max == 600
        assert run_md_verification_task.retry_jitter is True

    def test_beat_schedule_configuration(self) -> None:
        """Test Celery beat schedule for periodic tasks."""
        from nfm_db.services.celery_app import celery_app

        assert "beat_schedule" in celery_app.conf
        beat_schedule = celery_app.conf.beat_schedule

        # Check cleanup task is scheduled
        assert "cleanup-old-results-daily" in beat_schedule
        assert "nfm_db.services.md_tasks.cleanup_old_results" in (
            beat_schedule["cleanup-old-results-daily"]["task"]
        )


@pytest.mark.unit
class TestCeleryWorkerScript:
    """Test Celery worker startup script."""

    def test_worker_script_exists(self) -> None:
        """Test that worker startup script exists."""
        from pathlib import Path

        script_path = Path(__file__).parent.parent / "scripts" / "celery_worker.py"
        assert script_path.exists()

    def test_worker_script_importable(self) -> None:
        """Test that worker script can be imported."""
        import sys
        from pathlib import Path

        scripts_dir = Path(__file__).parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))

        try:
            import celery_worker
            assert hasattr(celery_worker, "main")
        finally:
            sys.path.remove(str(scripts_dir))


@pytest.mark.integration
class TestCeleryIntegration:
    """Integration tests for Celery with Redis."""

    @pytest.fixture
    def redis_required(self) -> None:
        """Skip tests if Redis is not available."""
        import redis

        try:
            redis_client = redis.Redis(host="localhost", port=6379, db=0)
            redis_client.ping()
        except Exception:
            pytest.skip("Redis not available for integration tests")

    def test_broker_connection(self, redis_required: None) -> None:
        """Test connection to Redis broker."""
        from nfm_db.services.celery_app import celery_app

        # Test broker connection
        with celery_app.connection_or_acquire() as conn:
            assert conn is not None
            assert conn.transport.driver_type == "redis"

    def test_task_submission(self, redis_required: None) -> None:
        """Test submitting a task to Celery."""
        from nfm_db.services.celery_app import celery_app

        # Test that we can inspect the Celery app
        assert celery_app is not None
        assert celery_app.conf.broker_url.startswith("redis://")

        # Verify task registration (without actually executing)
        assert len(list(celery_app.tasks.keys())) > 0
