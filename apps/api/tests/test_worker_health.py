"""Tests for WorkerHealthTracker (NFM-2014).

Covers AC-1 through AC-4.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nfm_db.monitoring.worker_health import ALERT_THRESHOLD, WorkerHealthTracker


@pytest.fixture()
def tracker() -> WorkerHealthTracker:
    """Fresh tracker per test."""
    return WorkerHealthTracker(alert_threshold=ALERT_THRESHOLD)


# ---------------------------------------------------------------------------
# AC-1: consecutive failure counter
# ---------------------------------------------------------------------------


class TestConsecutiveFailureCounter:
    """AC-1: Worker tracks consecutive failure count."""

    def test_initial_state_is_zero(self, tracker: WorkerHealthTracker) -> None:
        assert tracker.consecutive_failures == 0
        assert tracker.last_error is None
        assert tracker.last_success_at is None
        assert tracker.status == "ok"

    def test_single_failure_increments(self, tracker: WorkerHealthTracker) -> None:
        tracker.record_failure("LLM 502")
        assert tracker.consecutive_failures == 1
        assert tracker.last_error == "LLM 502"

    def test_multiple_failures_accumulate(self, tracker: WorkerHealthTracker) -> None:
        for i in range(4):
            tracker.record_failure(f"error {i}")
        assert tracker.consecutive_failures == 4
        assert tracker.last_error == "error 3"

    def test_error_truncated_to_500_chars(self, tracker: WorkerHealthTracker) -> None:
        long_error = "x" * 1000
        tracker.record_failure(long_error)
        assert len(tracker.last_error) == 500  # type: ignore[arg-type]

    def test_snapshot_returns_all_fields(self, tracker: WorkerHealthTracker) -> None:
        tracker.record_failure("test error")
        snap = tracker.snapshot()
        assert snap == {
            "status": "ok",
            "consecutive_failures": 1,
            "last_success_at": None,
            "last_error": "test error",
        }


# ---------------------------------------------------------------------------
# AC-2: CRITICAL log + Sentry event at 5 failures
# ---------------------------------------------------------------------------


class TestCriticalAlertAtThreshold:
    """AC-2: After 5 failures, CRITICAL log + Sentry event."""

    def test_critical_log_at_threshold(self, tracker: WorkerHealthTracker) -> None:
        with patch("nfm_db.monitoring.worker_health.logger") as mock_logger:
            for i in range(5):
                tracker.record_failure(f"fail {i}")
            mock_logger.critical.assert_called_once()
            call_args = mock_logger.critical.call_args
            assert "consecutive failures" in call_args[0][0]
            assert call_args[1]["extra"]["consecutive_failures"] == 5

    def test_sentry_event_fired_at_threshold(self, tracker: WorkerHealthTracker) -> None:
        with patch("nfm_db.monitoring.worker_health._try_sentry_capture") as mock_sentry:
            for i in range(5):
                tracker.record_failure(f"fail {i}")
            mock_sentry.assert_called_once()
            event = mock_sentry.call_args[0][0]
            assert event["level"] == "fatal"
            assert "5 consecutive" in event["message"]
            assert event["tags"]["component"] == "ingest-worker"

    def test_no_duplicate_alert_at_same_count(
        self, tracker: WorkerHealthTracker
    ) -> None:
        """After alerting at count 5, a 6th failure alerts again but 5th doesn't re-alert."""
        with patch("nfm_db.monitoring.worker_health.logger") as mock_logger:
            for i in range(5):
                tracker.record_failure(f"fail {i}")
            assert mock_logger.critical.call_count == 1
            # 6th failure should alert at new count
            tracker.record_failure("fail 5")
            assert mock_logger.critical.call_count == 2

    def test_sentry_not_required(self, tracker: WorkerHealthTracker) -> None:
        """Tracker works even if sentry-sdk is not installed."""
        with patch("nfm_db.monitoring.worker_health._try_sentry_capture"):
            tracker.record_failure("fail")
            # No exception raised

    def test_status_degraded_at_threshold(
        self, tracker: WorkerHealthTracker
    ) -> None:
        for _ in range(5):
            tracker.record_failure("fail")
        assert tracker.status == "degraded"

    def test_status_ok_below_threshold(
        self, tracker: WorkerHealthTracker
    ) -> None:
        for _ in range(4):
            tracker.record_failure("fail")
        assert tracker.status == "ok"


# ---------------------------------------------------------------------------
# AC-3: Counter resets on success
# ---------------------------------------------------------------------------


class TestCounterResetsOnSuccess:
    """AC-3: Counter resets on first success."""

    def test_reset_clears_counter(self, tracker: WorkerHealthTracker) -> None:
        for _ in range(7):
            tracker.record_failure("fail")
        tracker.record_success()
        assert tracker.consecutive_failures == 0
        assert tracker.status == "ok"

    def test_success_records_timestamp(
        self, tracker: WorkerHealthTracker
    ) -> None:
        tracker.record_success()
        assert tracker.last_success_at is not None
        assert "T" in tracker.last_success_at  # ISO-8601

    def test_failure_after_success_starts_at_one(
        self, tracker: WorkerHealthTracker
    ) -> None:
        tracker.record_failure("fail")
        tracker.record_success()
        tracker.record_failure("new fail")
        assert tracker.consecutive_failures == 1

    def test_alert_resets_after_success(
        self, tracker: WorkerHealthTracker
    ) -> None:
        """After success + 5 more failures, alert fires again."""
        with patch("nfm_db.monitoring.worker_health.logger") as mock_logger:
            # First batch — alert at 5
            for _ in range(5):
                tracker.record_failure("fail")
            assert mock_logger.critical.call_count == 1

            # Success resets
            tracker.record_success()

            # Second batch — alert at 5 again
            for _ in range(5):
                tracker.record_failure("fail")
            assert mock_logger.critical.call_count == 2


# ---------------------------------------------------------------------------
# AC-4: /health endpoint reports worker health state
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """AC-4: /health endpoint reports failure state."""

    @pytest.mark.asyncio
    async def test_health_includes_worker_fields(self) -> None:
        """Health endpoint returns worker health fields."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "consecutive_failures" in data
        assert "last_success_at" in data
        assert "last_error" in data

        worker_health.reset()

    @pytest.mark.asyncio
    async def test_health_shows_degraded_after_failures(self) -> None:
        """Health endpoint shows 'degraded' after >= 5 failures."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()
        for _ in range(5):
            worker_health.record_failure("LLM 502")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")
        data = response.json()
        assert data["status"] == "degraded"
        assert data["consecutive_failures"] == 5
        assert data["last_error"] == "LLM 502"

        worker_health.reset()

    @pytest.mark.asyncio
    async def test_health_shows_ok_initially(self) -> None:
        """Health endpoint shows 'ok' with zero failures by default."""
        from httpx import ASGITransport, AsyncClient

        from nfm_db.main import app
        from nfm_db.monitoring.worker_health import worker_health

        worker_health.reset()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")
        data = response.json()
        assert data["status"] == "ok"
        assert data["consecutive_failures"] == 0

        worker_health.reset()
