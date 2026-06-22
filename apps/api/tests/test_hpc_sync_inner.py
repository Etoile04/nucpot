"""Tests for hpc_sync._sync_jobs inner coroutine.

Exercises the _sync_jobs async function by patching at the deferred
import locations: nfm_db.services.hpc_ssh (for SSHConnectionConfig and
SSHConnectionManager) and nfm_db.services.hpc_job_monitor (for
sync_all_active_jobs).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV_DEFAULTS = {
    "NFM_HPC_PRIMARY_HOST": "hpc01.example.com",
    "NFM_HPC_PRIMARY_USER": "testuser",
    "NFM_HPC_PRIMARY_SSH_KEY_PATH": "/path/to/key",
    "NFM_HPC_MAX_CONNECTIONS": "10",
}


def _make_getenv(**overrides: str):
    env = {**_ENV_DEFAULTS, **overrides}
    return lambda k, d=None: env.get(k, d)


def _make_mock_config():
    """Create a mock SSHConnectionConfig with attribute access."""
    config = MagicMock()
    config.hosts = ["hpc01.example.com"]
    config.username = "testuser"
    config.ssh_key_path = "/path/to/key"
    config.max_connections = 10
    return config


def _make_mock_manager():
    """Create a mock SSHConnectionManager."""
    manager = MagicMock()
    manager.cleanup = MagicMock()
    return manager


def _run_sync(**env_overrides):
    """Execute sync_hpc_job_status with patches applied.

    Patches are applied at the source modules where _sync_jobs does
    its deferred imports. The real asyncio.run is used to execute
    the inner coroutine.
    """
    from nfm_db.services.hpc_sync import sync_hpc_job_status

    mock_config = _make_mock_config()
    mock_manager = _make_mock_manager()

    with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config):
        with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
            with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv(**env_overrides)):
                    return sync_hpc_job_status()


# ---------------------------------------------------------------------------
# _sync_jobs inner coroutine tests
# ---------------------------------------------------------------------------


class TestSyncJobsInnerCoroutine:
    """Tests that exercise the _sync_jobs inner async function.

    All tests patch at the source module where _sync_jobs does its
    deferred imports: nfm_db.services.hpc_ssh and
    nfm_db.services.hpc_job_monitor.
    """

    @pytest.mark.unit
    def test_sync_jobs_returns_success(self) -> None:
        """_sync_jobs should return success dict after sync completes."""
        result = _run_sync()
        assert result["status"] == "success"
        assert result["message"] == "HPC job status sync completed"

    @pytest.mark.unit
    def test_sync_jobs_calls_cleanup_in_finally(self) -> None:
        """_sync_jobs should call manager.cleanup() in finally block."""
        from nfm_db.services.hpc_ssh import SSHConnectionManager

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config):
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager) as mock_cls:
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv()):
                        from nfm_db.services.hpc_sync import sync_hpc_job_status
                        sync_hpc_job_status()

        mock_manager.cleanup.assert_called_once()

    @pytest.mark.unit
    def test_sync_jobs_returns_error_on_sync_failure(self) -> None:
        """_sync_jobs should return error dict when sync_all_active_jobs raises."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config):
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock, side_effect=RuntimeError("sync failed")):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv()):
                        result = sync_hpc_job_status()

        assert result["status"] == "error"
        assert "sync failed" in result["message"]
        assert result["jobs_processed"] == 0
        mock_manager.cleanup.assert_called_once()

    @pytest.mark.unit
    def test_sync_jobs_returns_error_on_generic_exception(self) -> None:
        """_sync_jobs should return error dict for any exception."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config):
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock, side_effect=ValueError("bad data")):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv()):
                        result = sync_hpc_job_status()

        assert result["status"] == "error"
        assert "bad data" in result["message"]
        assert result["jobs_processed"] == 0

    @pytest.mark.unit
    def test_sync_jobs_calls_ssh_config_from_lists(self) -> None:
        """_sync_jobs should call SSHConnectionConfig.from_lists."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config) as mock_from_lists:
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv()):
                        sync_hpc_job_status()

        mock_from_lists.assert_called_once()

    @pytest.mark.unit
    def test_sync_jobs_creates_manager_with_config_attrs(self) -> None:
        """_sync_jobs should create SSHConnectionManager with config attributes."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config):
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager") as mock_cls:
                mock_cls.return_value = mock_manager
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv()):
                        sync_hpc_job_status()

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["host"] == ["hpc01.example.com"]
        assert call_kwargs["username"] == "testuser"
        assert call_kwargs["ssh_key_path"] == "/path/to/key"
        assert call_kwargs["max_connections"] == 10

    @pytest.mark.unit
    def test_sync_jobs_with_backup_env_vars(self) -> None:
        """_sync_jobs should pass backup env vars to from_lists."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config) as mock_from_lists:
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv(
                        NFM_HPC_BACKUP_HOST="backup.example.com",
                        NFM_HPC_BACKUP_USER="backup_user",
                        NFM_HPC_BACKUP_SSH_KEY_PATH="/path/to/backup_key",
                        NFM_HPC_FAILOVER_THRESHOLD_SECONDS="600",
                    )):
                        sync_hpc_job_status()

        call_kwargs = mock_from_lists.call_args[1]
        assert call_kwargs["backup_hosts"] == ["backup.example.com"]
        assert call_kwargs["backup_username"] == "backup_user"
        assert call_kwargs["backup_ssh_key_path"] == "/path/to/backup_key"
        assert call_kwargs["failover_threshold_seconds"] == 600

    @pytest.mark.unit
    def test_sync_jobs_no_backup_when_env_unset(self) -> None:
        """_sync_jobs should pass None for backup_hosts when not set."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = _make_mock_config()
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config) as mock_from_lists:
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv()):
                        sync_hpc_job_status()

        call_kwargs = mock_from_lists.call_args[1]
        assert call_kwargs.get("backup_hosts") is None

    @pytest.mark.unit
    def test_sync_jobs_custom_env_overrides(self) -> None:
        """_sync_jobs should use custom env values for config."""
        from nfm_db.services.hpc_sync import sync_hpc_job_status

        mock_config = MagicMock()
        mock_config.hosts = ["custom-host.org"]
        mock_config.username = "customuser"
        mock_config.ssh_key_path = "/custom/key"
        mock_config.max_connections = 20
        mock_manager = _make_mock_manager()

        with patch("nfm_db.services.hpc_ssh.SSHConnectionConfig.from_lists", return_value=mock_config) as mock_from_lists:
            with patch("nfm_db.services.hpc_ssh.SSHConnectionManager", return_value=mock_manager):
                with patch("nfm_db.services.hpc_job_monitor.sync_all_active_jobs", new_callable=AsyncMock):
                    with patch("nfm_db.services.hpc_sync.os.getenv", side_effect=_make_getenv(
                        NFM_HPC_PRIMARY_HOST="custom-host.org",
                        NFM_HPC_PRIMARY_USER="customuser",
                        NFM_HPC_PRIMARY_SSH_KEY_PATH="/custom/key",
                        NFM_HPC_MAX_CONNECTIONS="20",
                    )):
                        sync_hpc_job_status()

        call_kwargs = mock_from_lists.call_args[1]
        assert call_kwargs["hosts"] == ["custom-host.org"]
        assert call_kwargs["username"] == "customuser"
        assert call_kwargs["ssh_key_path"] == "/custom/key"
        assert call_kwargs["max_connections"] == 20
