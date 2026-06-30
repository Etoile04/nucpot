"""Unit tests for celery_app service (NFM-582).

Tests for Redis failure counter, HPC environment validation, and
monitor task error paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest


class TestGetRedisClient:
    """Tests for _get_redis_client helper."""

    def test_returns_redis_client_on_success(self) -> None:
        """GIVEN redis package is available and connection succeeds,
        THEN _get_redis_client returns a Redis instance."""
        from nfm_db.services.celery_app import _get_redis_client

        mock_redis_instance = MagicMock()
        mock_redis_module = MagicMock()
        mock_redis_module.Redis.return_value = mock_redis_instance

        with patch.dict("os.environ", {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_DB": "0"}):
            with patch.dict("sys.modules", {"redis": mock_redis_module}):
                result = _get_redis_client()
                assert result is mock_redis_instance

    def test_returns_none_when_redis_import_fails(self) -> None:
        """GIVEN redis package is not installed,
        THEN _get_redis_client returns None."""
        from nfm_db.services.celery_app import _get_redis_client

        with patch.dict("sys.modules", {"redis": None}):
            result = _get_redis_client()
            assert result is None

    def test_returns_none_when_redis_connection_fails(self) -> None:
        """GIVEN Redis connection raises an exception,
        THEN _get_redis_client returns None."""
        from nfm_db.services.celery_app import _get_redis_client

        mock_redis_module = MagicMock()
        mock_redis_module.Redis.side_effect = Exception("Connection refused")

        with patch.dict("os.environ", {"REDIS_HOST": "badhost", "REDIS_PORT": "6379", "REDIS_DB": "0"}):
            with patch.dict("sys.modules", {"redis": mock_redis_module}):
                result = _get_redis_client()
                assert result is None


class TestIncrementFailureCount:
    """Tests for increment_failure_count."""

    def test_returns_1_when_redis_unavailable(self) -> None:
        """GIVEN Redis is not available,
        THEN increment_failure_count returns 1 (in-memory fallback)."""
        from nfm_db.services.celery_app import increment_failure_count

        with patch(
            "nfm_db.services.celery_app._get_redis_client",
            return_value=None,
        ):
            count = increment_failure_count()
            assert count == 1

    def test_returns_1_on_redis_error(self) -> None:
        """GIVEN Redis incr operation fails,
        THEN increment_failure_count returns 1."""
        from nfm_db.services.celery_app import increment_failure_count

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.side_effect = Exception("Redis connection lost")

        with patch(
            "nfm_db.services.celery_app._get_redis_client",
            return_value=mock_redis,
        ):
            count = increment_failure_count()
            assert count == 1

    def test_returns_actual_count_on_success(self) -> None:
        """GIVEN Redis is healthy and count is 3,
        THEN increment_failure_count returns 3."""
        from nfm_db.services.celery_app import increment_failure_count

        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_pipe.execute.return_value = [3]

        with patch(
            "nfm_db.services.celery_app._get_redis_client",
            return_value=mock_redis,
        ):
            count = increment_failure_count()
            assert count == 3


class TestResetFailureCount:
    """Tests for reset_failure_count."""

    def test_does_nothing_when_redis_unavailable(self) -> None:
        """GIVEN Redis is not available,
        THEN reset_failure_count does not raise."""
        from nfm_db.services.celery_app import reset_failure_count

        with patch(
            "nfm_db.services.celery_app._get_redis_client",
            return_value=None,
        ):
            reset_failure_count()  # Should not raise

    def test_raises_on_redis_error(self) -> None:
        """GIVEN Redis delete raises an exception,
        THEN reset_failure_count does not raise (logs error)."""
        from nfm_db.services.celery_app import reset_failure_count

        mock_redis = MagicMock()
        mock_redis.delete.side_effect = Exception("Redis error")

        with patch(
            "nfm_db.services.celery_app._get_redis_client",
            return_value=mock_redis,
        ):
            reset_failure_count()  # Should not raise


class TestValidateHpcEnvironment:
    """Tests for _validate_hpc_environment."""

    def test_raises_on_missing_env_vars(self) -> None:
        """GIVEN required HPC env vars are missing,
        THEN _validate_hpc_environment raises ValueError."""
        from nfm_db.services.celery_app import _validate_hpc_environment

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="Required HPC environment variables"):
                _validate_hpc_environment()

    def test_raises_on_partial_missing_vars(self) -> None:
        """GIVEN only some HPC env vars are set,
        THEN _validate_hpc_environment raises ValueError listing missing vars."""
        from nfm_db.services.celery_app import _validate_hpc_environment

        with patch.dict(
            "os.environ",
            {"NFM_HPC_PRIMARY_HOST": "host", "NFM_HPC_PRIMARY_USER": "user"},
            clear=False,
        ):
            with pytest.raises(ValueError, match="NFM_HPC_PRIMARY_SSH_KEY_PATH"):
                _validate_hpc_environment()

    def test_raises_when_ssh_key_not_found(self) -> None:
        """GIVEN SSH key path does not point to an existing file,
        THEN _validate_hpc_environment raises FileNotFoundError."""
        from nfm_db.services.celery_app import _validate_hpc_environment

        with patch.dict(
            "os.environ",
            {
                "NFM_HPC_PRIMARY_HOST": "host",
                "NFM_HPC_PRIMARY_USER": "user",
                "NFM_HPC_PRIMARY_SSH_KEY_PATH": "/nonexistent/key",
            },
            clear=False,
        ):
            with pytest.raises(FileNotFoundError, match="SSH key not found"):
                _validate_hpc_environment()

    def test_passes_when_all_vars_set_and_key_exists(self) -> None:
        """GIVEN all required vars are set and SSH key file exists,
        THEN _validate_hpc_environment does not raise."""
        from nfm_db.services.celery_app import _validate_hpc_environment

        with patch.dict(
            "os.environ",
            {
                "NFM_HPC_PRIMARY_HOST": "host",
                "NFM_HPC_PRIMARY_USER": "user",
                "NFM_HPC_PRIMARY_SSH_KEY_PATH": "/dev/null",
            },
            clear=False,
        ):
            _validate_hpc_environment()  # Should not raise


class TestMonitorPrimaryClusterHealthErrors:
    """Tests for error paths in monitor_primary_cluster_health."""

    def test_monitor_returns_error_when_health_check_raises(self) -> None:
        """GIVEN orchestrator.check_primary_health raises an exception,
        THEN monitor returns error status with message."""
        from nfm_db.services.celery_app import monitor_primary_cluster_health

        with patch.dict("os.environ", {
            "NFM_HPC_PRIMARY_HOST": "test.example.com",
            "NFM_HPC_PRIMARY_USER": "testuser",
            "NFM_HPC_PRIMARY_SSH_KEY_PATH": '/tmp/test_key',
        }):
            with patch("nfm_db.services.celery_app._validate_hpc_environment"):
                with patch(
                    "nfm_db.services.hpc_orchestration.HPCOrchestrator",
                ) as mock_orch_class:
                    mock_orch = MagicMock()
                    mock_orch_class.return_value = mock_orch
                    mock_orch.check_primary_health.side_effect = RuntimeError(
                        "SSH connection failed"
                    )
                    mock_orch.cleanup = Mock()

                    result = monitor_primary_cluster_health()
                    assert result["status"] == "error"
                    assert "SSH connection failed" in result["message"]

    def test_monitor_returns_error_when_failover_fails(self) -> None:
        """GIVEN failover is triggered but fails,
        THEN monitor returns failover_failed status."""
        from nfm_db.services.celery_app import monitor_primary_cluster_health

        async def mock_trigger_failover_fail():
            return False

        with patch.dict("os.environ", {
            "NFM_HPC_PRIMARY_HOST": "test.example.com",
            "NFM_HPC_PRIMARY_USER": "testuser",
            "NFM_HPC_PRIMARY_SSH_KEY_PATH": '/tmp/test_key',
        }):
            with patch("nfm_db.services.celery_app._validate_hpc_environment"):
                with patch(
                    "nfm_db.services.hpc_orchestration.HPCOrchestrator",
                ) as mock_orch_class:
                    mock_orch = MagicMock()
                    mock_orch_class.return_value = mock_orch
                    mock_orch.check_primary_health.return_value = False
                    mock_orch.should_trigger_failover.return_value = True
                    mock_orch.trigger_failover.side_effect = mock_trigger_failover_fail
                    mock_orch.cleanup = Mock()

                    result = monitor_primary_cluster_health()
                    assert result["status"] == "failover_failed"

    def test_monitor_returns_success_when_primary_healthy(self) -> None:
        """GIVEN primary cluster is healthy,
        THEN monitor returns success status."""
        from nfm_db.services.celery_app import monitor_primary_cluster_health

        with patch.dict("os.environ", {
            "NFM_HPC_PRIMARY_HOST": "test.example.com",
            "NFM_HPC_PRIMARY_USER": "testuser",
            "NFM_HPC_PRIMARY_SSH_KEY_PATH": '/tmp/test_key',
        }):
            with patch("nfm_db.services.celery_app._validate_hpc_environment"):
                with patch(
                    "nfm_db.services.hpc_orchestration.HPCOrchestrator",
                ) as mock_orch_class:
                    mock_orch = MagicMock()
                    mock_orch_class.return_value = mock_orch
                    mock_orch.check_primary_health.return_value = True
                    mock_orch.current_cluster = "primary"
                    mock_orch.cleanup = Mock()

                    with patch("nfm_db.services.celery_app.reset_failure_count"):
                        result = monitor_primary_cluster_health()
                        assert result["status"] == "success"
                        assert result["primary_healthy"] is True
