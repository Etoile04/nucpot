"""Unit tests for HPCOrchestrator (hpc_orchestration.py).

Tests cover:
- Constructor: SSH manager, failover manager, with/without backup
- cleanup and __del__ lifecycle methods
- All delegate methods verify correct delegation to submodule functions
- submit_job: full flow with failover, validation, submission, record creation

All external submodule imports are mocked to isolate the composition root.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.hpc_ssh import SSHConnectionConfig

# ---------------------------------------------------------------------------
# Helper: names as they appear in hpc_orchestration module namespace
# ---------------------------------------------------------------------------

# These must match the actual names in hpc_orchestration.py imports.
# The file_transfer imports use aliased private names (_upload_file, etc.).
_ORCH_NAMES: list[str] = [
    "SSHConnectionManager",
    "HPCFailoverManager",
    "JobSubmissionError",
    "HPCConnectionError",
    "generate_slurm_script",
    "validate_simulation_params",
    "parse_walltime",
    "submit_to_slurm",
    "create_hpc_job_record",
    "poll_job_status",
    "execute_squeue",
    "update_job_status",
    "check_job_completion",
    "get_active_jobs",
    "sync_all_active_jobs",
    "_upload_file",
    "_upload_files",
    "_create_task_directory",
    "_download_file",
    "_download_results",
    "_verify_checksum",
    "_get_remote_checksum",
    "_save_to_object_storage",
    "_save_metadata",
    "_upload_file_with_retry",
    "_upload_file_with_resume",
    "_get_remote_file_position",
    "sync_hpc_job_status",
    "PROMETHEUS_AVAILABLE",
    "hpc_job_submissions",
    "hpc_job_duration",
    "hpc_file_transfer_bytes",
    "hpc_connection_errors",
    "hpc_failover_events",
    "hpc_active_connections",
    "failover_total",
    "failover_duration_seconds",
    "health_check_success",
]


def _build_orchestrator(config: SSHConnectionConfig) -> tuple[Any, dict[str, Any], list]:
    """Build an HPCOrchestrator with all submodule imports patched.

    Returns (orchestrator, mock_map, patchers).
    Call _cleanup(patchers) when done.
    """
    patchers: list[Any] = []
    mock_map: dict[str, Any] = {}

    for name in _ORCH_NAMES:
        target = f"nfm_db.services.hpc_orchestration.{name}"
        is_async = name in {
            "submit_to_slurm",
            "create_hpc_job_record",
            "poll_job_status",
            "execute_squeue",
            "update_job_status",
            "check_job_completion",
            "get_active_jobs",
            "sync_all_active_jobs",
            "_upload_file",
            "_upload_files",
            "_create_task_directory",
            "_download_file",
            "_download_results",
            "_verify_checksum",
            "_get_remote_checksum",
            "_save_to_object_storage",
            "_save_metadata",
            "_upload_file_with_retry",
            "_upload_file_with_resume",
            "_get_remote_file_position",
        }
        mock_val: Any = AsyncMock() if is_async else MagicMock()
        p = patch(target, mock_val, create=True)
        p.start()
        patchers.append(p)
        mock_map[name] = mock_val

    # Set useful return values for specific mocks
    mock_map["generate_slurm_script"].return_value = "#!/bin/bash\necho hello"
    mock_map["validate_simulation_params"].return_value = None
    mock_map["parse_walltime"].return_value = 60
    mock_map["submit_to_slurm"].return_value = "slurm-12345"
    mock_map["create_hpc_job_record"].return_value = None
    mock_map["poll_job_status"].return_value = "RUNNING"
    mock_map["execute_squeue"].return_value = "slurm-12345 RUNNING"
    mock_map["check_job_completion"].return_value = True
    mock_map["get_active_jobs"].return_value = []
    mock_map["_upload_file"].return_value = True
    mock_map["_upload_files"].return_value = {"file.txt": True}
    mock_map["_download_results"].return_value = {}
    mock_map["_verify_checksum"].return_value = True
    mock_map["_get_remote_checksum"].return_value = "abc123"
    mock_map["_save_to_object_storage"].return_value = {}
    mock_map["_upload_file_with_retry"].return_value = True
    mock_map["_upload_file_with_resume"].return_value = True
    mock_map["_get_remote_file_position"].return_value = 0

    try:
        from nfm_db.services.hpc_orchestration import HPCOrchestrator

        orchestrator = HPCOrchestrator(config)
        return orchestrator, mock_map, patchers
    except Exception:
        for p in patchers:
            p.stop()
        raise


def _cleanup(patchers: list) -> None:
    """Stop all patchers."""
    for p in patchers:
        p.stop()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def primary_config() -> SSHConnectionConfig:
    """Create a minimal SSHConnectionConfig for primary cluster only."""
    return SSHConnectionConfig(
        hosts=("login01.example.com",),
        username="testuser",
        ssh_key_path="/path/to/key",
    )


@pytest.fixture
def backup_config() -> SSHConnectionConfig:
    """Create an SSHConnectionConfig with both primary and backup clusters."""
    return SSHConnectionConfig(
        hosts=("login01.example.com",),
        username="testuser",
        ssh_key_path="/path/to/key",
        backup_hosts=("backup01.example.com",),
        backup_username="backupuser",
        backup_ssh_key_path="/path/to/backup/key",
    )


# ---------------------------------------------------------------------------
# Constructor tests
# ---------------------------------------------------------------------------


class TestHPCOrchestratorInit:
    """Tests for HPCOrchestrator.__init__."""

    @pytest.mark.unit
    def test_creates_ssh_manager(self, primary_config: SSHConnectionConfig) -> None:
        """Constructor should create a primary SSHConnectionManager."""
        _, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_map["SSHConnectionManager"].assert_called_once()
            call_kwargs = mock_map["SSHConnectionManager"].call_args[1]
            assert call_kwargs["username"] == "testuser"
            assert call_kwargs["ssh_key_path"] == "/path/to/key"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_sets_hpc_cluster_from_first_host(self, primary_config: SSHConnectionConfig) -> None:
        """Constructor should set hpc_cluster to the first host in config."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            assert orchestrator.hpc_cluster == "login01.example.com"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_creates_failover_manager(self, primary_config: SSHConnectionConfig) -> None:
        """Constructor should create HPCFailoverManager."""
        _, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_map["HPCFailoverManager"].assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_creates_backup_ssh_manager_when_configured(
        self,
        backup_config: SSHConnectionConfig,
    ) -> None:
        """Constructor should create backup SSH manager when backup hosts configured."""
        _, mock_map, patchers = _build_orchestrator(backup_config)
        try:
            # SSHConnectionManager should be called twice (primary + backup)
            assert mock_map["SSHConnectionManager"].call_count == 2

            # Second call should use backup credentials
            calls = mock_map["SSHConnectionManager"].call_args_list
            backup_call = calls[1]
            assert backup_call[1]["username"] == "backupuser"
            assert backup_call[1]["ssh_key_path"] == "/path/to/backup/key"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_no_backup_manager_when_not_configured(
        self,
        primary_config: SSHConnectionConfig,
    ) -> None:
        """Constructor should not create backup SSH manager when backup not configured."""
        _, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            # Only primary SSHConnectionManager should be created
            assert mock_map["SSHConnectionManager"].call_count == 1

            # HPCFailoverManager should receive backup_ssh_manager=None
            failover_call = mock_map["HPCFailoverManager"].call_args
            assert failover_call[1]["backup_ssh_manager"] is None
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_stores_config_reference(self, primary_config: SSHConnectionConfig) -> None:
        """Constructor should store the config reference."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            assert orchestrator.config is primary_config
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# cleanup and __del__
# ---------------------------------------------------------------------------


class TestHPCOrchestratorCleanup:
    """Tests for HPCOrchestrator.cleanup and __del__."""

    @pytest.mark.unit
    def test_cleanup_delegates_to_failover_manager(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """cleanup() should delegate to failover_manager.cleanup()."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            orchestrator.cleanup()
            mock_failover.cleanup.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_del_calls_cleanup(self, primary_config: SSHConnectionConfig) -> None:
        """__del__ should call cleanup()."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            del orchestrator
            mock_failover.cleanup.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_del_ignores_exceptions(self, primary_config: SSHConnectionConfig) -> None:
        """__del__ should silently ignore exceptions from cleanup."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            orchestrator.failover_manager.cleanup.side_effect = RuntimeError("cleanup error")
            # Should not raise
            del orchestrator
        except RuntimeError:
            pytest.fail("__del__ should not propagate exceptions")
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_del_works_when_failover_manager_missing(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """__del__ should handle missing failover_manager gracefully."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            orchestrator.failover_manager = None
            # Should not raise AttributeError
            del orchestrator
        except Exception:
            pytest.fail("__del__ should not propagate exceptions")
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# Failover delegate methods
# ---------------------------------------------------------------------------


class TestFailoverDelegates:
    """Tests for failover delegation methods."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_log_failover_event_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_log_failover_event uses module-level get_db to persist events."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            # _log_failover_event is an orchestrator method (not a delegate).
            # It imports get_db from nfm_db.database and uses it directly.
            # Patch get_db at the orchestration module level where it is imported.
            mock_db = MagicMock()
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()

            with patch("nfm_db.services.hpc_orchestration.get_db") as mock_get_db:

                async def mock_db_gen():
                    yield mock_db

                mock_get_db.return_value = mock_db_gen()

                await orchestrator._log_failover_event(
                    event_type="failover_triggered",
                    source_cluster="primary",
                    target_cluster="backup",
                    reason="health_check",
                    success=True,
                )

                # Verify DB commit was called
                mock_db.add.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_check_primary_health_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """check_primary_health should delegate to failover_manager."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.check_primary_health.return_value = True

            result = orchestrator.check_primary_health()
            assert result is True
            mock_failover.check_primary_health.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_should_trigger_failover_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """should_trigger_failover should delegate to failover_manager."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = False

            result = orchestrator.should_trigger_failover()
            assert result is False
            mock_failover.should_trigger_failover.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_trigger_failover_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """trigger_failover should delegate to failover_manager."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.trigger_failover = AsyncMock(return_value=True)

            result = await orchestrator.trigger_failover()
            assert result is True
            mock_failover.trigger_failover.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_try_recover_primary_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """try_recover_primary should delegate to failover_manager."""
        orchestrator, _, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.try_recover_primary = AsyncMock(return_value=True)

            result = await orchestrator.try_recover_primary()
            assert result is True
            mock_failover.try_recover_primary.assert_called_once()
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# SLURM delegate methods
# ---------------------------------------------------------------------------


class TestSlurmDelegates:
    """Tests for SLURM submission delegate methods."""

    @pytest.mark.unit
    def test_validate_simulation_params_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """_validate_simulation_params should call validate_simulation_params."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            params: dict[str, Any] = {"temperature": 300, "pressure": 0}
            orchestrator._validate_simulation_params(params)
            mock_map["validate_simulation_params"].assert_called_once_with(params)
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_generate_slurm_script_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_generate_slurm_script should call generate_slurm_script."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            params: dict[str, Any] = {"temperature": 300}
            result = orchestrator._generate_slurm_script(params)
            mock_map["generate_slurm_script"].assert_called_once_with(params)
            assert result == "#!/bin/bash\necho hello"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_to_slurm_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_submit_to_slurm should call submit_to_slurm with correct args."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"

            result = await orchestrator._submit_to_slurm("task-123", "#!/bin/bash")
            mock_map["submit_to_slurm"].assert_called_once()
            assert result == "slurm-12345"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    def test_parse_walltime_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_parse_walltime should call parse_walltime."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = orchestrator._parse_walltime("01:30:00")
            mock_map["parse_walltime"].assert_called_once_with("01:30:00")
            assert result == 60
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# Job monitoring delegate methods
# ---------------------------------------------------------------------------


class TestJobMonitorDelegates:
    """Tests for job monitoring delegation methods."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_poll_job_status_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """poll_job_status calls _execute_squeue which delegates to execute_squeue."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            # poll_job_status is an orchestrator method that calls
            # _execute_squeue -> execute_squeue(ssh_manager, job_id)
            result = await orchestrator.poll_job_status("slurm-12345")
            mock_map["execute_squeue"].assert_called_once_with(
                orchestrator.ssh_manager,
                "slurm-12345",
            )
            assert result == "RUNNING"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_execute_squeue_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_execute_squeue should delegate with ssh_manager and job_id."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator._execute_squeue("slurm-12345")
            mock_map["execute_squeue"].assert_called_once_with(
                orchestrator.ssh_manager,
                "slurm-12345",
            )
            assert result == "slurm-12345 RUNNING"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_update_job_status_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """update_job_status should delegate with ssh_manager and IDs."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            await orchestrator.update_job_status("task-123", "slurm-456")
            mock_map["update_job_status"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-123",
                "slurm-456",
            )
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_check_job_completion_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """_check_job_completion should delegate with ssh_manager and task_id."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator._check_job_completion("task-123")
            mock_map["check_job_completion"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-123",
            )
            assert result is True
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_active_jobs_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_get_active_jobs should delegate to get_active_jobs."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator._get_active_jobs()
            mock_map["get_active_jobs"].assert_called_once()
            assert result == []
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sync_all_active_jobs_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """sync_all_active_jobs calls _get_active_jobs and update_job_status."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            # sync_all_active_jobs is an orchestrator method that calls
            # _get_active_jobs -> get_active_jobs()
            await orchestrator.sync_all_active_jobs()
            mock_map["get_active_jobs"].assert_called_once()
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# File transfer delegate methods
# ---------------------------------------------------------------------------


class TestFileTransferDelegates:
    """Tests for file transfer delegation methods."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """upload_file should delegate to _upload_file with ssh_manager."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.upload_file("task-1", "/local/file", "/remote/file")
            mock_map["_upload_file"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                "/local/file",
                "/remote/file",
            )
            assert result is True
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_files_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """upload_files should delegate to _upload_files with ssh_manager."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            files = [("/local/a", "/remote/a")]
            result = await orchestrator.upload_files("task-1", files)
            mock_map["_upload_files"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                files,
            )
            assert result == {"file.txt": True}
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_task_directory_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """_create_task_directory should delegate to _create_task_directory."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            await orchestrator._create_task_directory("task-1")
            mock_map["_create_task_directory"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
            )
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_file_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """download_file should delegate to _download_file."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            await orchestrator.download_file("task-1", "/remote/out", "/local/out")
            mock_map["_download_file"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                "/remote/out",
                "/local/out",
            )
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_results_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """download_results should delegate to _download_results."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.download_results("task-1")
            mock_map["_download_results"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
            )
            assert result == {}
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_verify_checksum_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """verify_checksum should delegate to _verify_checksum."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.verify_checksum("/local/file", "abc123")
            mock_map["_verify_checksum"].assert_called_once_with(
                "/local/file",
                "abc123",
            )
            assert result is True
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_remote_checksum_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """get_remote_checksum should delegate to _get_remote_checksum."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.get_remote_checksum("task-1", "/remote/file")
            mock_map["_get_remote_checksum"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                "/remote/file",
            )
            assert result == "abc123"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_to_object_storage_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """save_to_object_storage should delegate to _save_to_object_storage."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            downloaded: dict[str, str] = {"out.txt": "/local/out.txt"}
            result = await orchestrator.save_to_object_storage("task-1", downloaded)
            mock_map["_save_to_object_storage"].assert_called_once_with(
                "task-1",
                downloaded,
            )
            assert result == {}
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_metadata_delegates(self, primary_config: SSHConnectionConfig) -> None:
        """_save_metadata should delegate to _save_metadata."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            metadata: dict[str, dict[str, Any]] = {"out.txt": {"size": 1024}}
            await orchestrator._save_metadata("task-1", metadata)
            mock_map["_save_metadata"].assert_called_once_with("task-1", metadata)
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_with_retry_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """upload_file_with_retry should delegate with all params."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.upload_file_with_retry(
                "task-1",
                "/local/file",
                "/remote/file",
                max_retries=5,
            )
            mock_map["_upload_file_with_retry"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                "/local/file",
                "/remote/file",
                5,
            )
            assert result is True
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_upload_file_with_resume_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """upload_file_with_resume should delegate with resume position."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.upload_file_with_resume(
                "task-1",
                "/local/file",
                "/remote/file",
                resume_position=1024,
            )
            mock_map["_upload_file_with_resume"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                "/local/file",
                "/remote/file",
                1024,
            )
            assert result is True
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_remote_file_position_delegates(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """get_remote_file_position should delegate to _get_remote_file_position."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            result = await orchestrator.get_remote_file_position("task-1", "/remote/file")
            mock_map["_get_remote_file_position"].assert_called_once_with(
                orchestrator.ssh_manager,
                "task-1",
                "/remote/file",
            )
            assert result == 0
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# submit_job full flow
# ---------------------------------------------------------------------------


class TestSubmitJobFlow:
    """Tests for the complete submit_job flow with failover and validation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_validates_params_first(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """submit_job should call validate_simulation_params before submission."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = False
            mock_failover.current_cluster = "primary"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"

            params: dict[str, Any] = {"temperature": 300, "pressure": 0}
            await orchestrator.submit_job("task-1", "/input.cif", params)

            mock_map["validate_simulation_params"].assert_called_once_with(params)
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_generates_slurm_script(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """submit_job should call generate_slurm_script with params."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = False
            mock_failover.current_cluster = "primary"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"

            params: dict[str, Any] = {"temperature": 300}
            await orchestrator.submit_job("task-1", "/input.cif", params)

            mock_map["generate_slurm_script"].assert_called_once_with(params)
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_creates_job_record(self, primary_config: SSHConnectionConfig) -> None:
        """submit_job should call create_hpc_job_record after successful submission."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = False
            mock_failover.current_cluster = "primary"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"

            params: dict[str, Any] = {"temperature": 300}
            hpc_job_id = await orchestrator.submit_job("task-1", "/input.cif", params)

            mock_map["create_hpc_job_record"].assert_called_once_with(
                "task-1",
                "slurm-12345",
                params,
                "primary",
            )
            assert hpc_job_id == "slurm-12345"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_returns_hpc_job_id(self, primary_config: SSHConnectionConfig) -> None:
        """submit_job should return the SLURM job ID from submit_to_slurm."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = False
            mock_failover.current_cluster = "primary"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"

            mock_map["submit_to_slurm"].return_value = "slurm-99999"

            params: dict[str, Any] = {"temperature": 300}
            result = await orchestrator.submit_job("task-1", "/input.cif", params)

            assert result == "slurm-99999"
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_triggers_failover_when_needed(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """submit_job should trigger failover when should_trigger_failover returns True."""
        orchestrator, _mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = True
            mock_failover.trigger_failover = AsyncMock(return_value=True)
            mock_failover.try_recover_primary = AsyncMock(return_value=False)
            mock_failover.current_cluster = "backup"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "backup"

            params: dict[str, Any] = {"temperature": 300}
            await orchestrator.submit_job("task-1", "/input.cif", params)

            mock_failover.trigger_failover.assert_called_once()
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_raises_when_failover_fails(
        self, primary_config: SSHConnectionConfig
    ) -> None:
        """submit_job should raise when failover and submission both fail."""
        orchestrator, _mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = True
            mock_failover.trigger_failover = AsyncMock(return_value=False)

            params: dict[str, Any] = {"temperature": 300}
            with pytest.raises(Exception):
                await orchestrator.submit_job("task-1", "/input.cif", params)
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_retries_on_backup_after_primary_failure(
        self,
        primary_config: SSHConnectionConfig,
    ) -> None:
        """submit_job should retry on backup when primary submission fails."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = False
            mock_failover.current_cluster = "primary"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"
            mock_failover.trigger_failover = AsyncMock(return_value=True)

            # First call fails, second succeeds
            mock_map["submit_to_slurm"].side_effect = [
                RuntimeError("Primary down"),
                "slurm-55555",
            ]

            params: dict[str, Any] = {"temperature": 300}
            result = await orchestrator.submit_job("task-1", "/input.cif", params)

            assert result == "slurm-55555"
            assert mock_map["submit_to_slurm"].call_count == 2
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_job_recovers_primary_before_submission(
        self,
        primary_config: SSHConnectionConfig,
    ) -> None:
        """submit_job should try to recover primary when currently on backup."""
        orchestrator, _mock_map, patchers = _build_orchestrator(primary_config)
        try:
            mock_failover = orchestrator.failover_manager
            mock_failover.should_trigger_failover.return_value = True
            mock_failover.trigger_failover = AsyncMock(return_value=True)
            mock_failover.try_recover_primary = AsyncMock(return_value=True)
            mock_failover.current_cluster = "backup"
            mock_failover.current_ssh_manager = MagicMock()
            mock_failover.current_cluster_name = "primary"

            params: dict[str, Any] = {"temperature": 300}
            await orchestrator.submit_job("task-1", "/input.cif", params)

            mock_failover.try_recover_primary.assert_called_once()
        finally:
            _cleanup(patchers)


# ---------------------------------------------------------------------------
# _create_hpc_job_record delegate
# ---------------------------------------------------------------------------


class TestCreateHpcJobRecord:
    """Tests for _create_hpc_job_record."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_uses_primary_cluster_name(self, primary_config: SSHConnectionConfig) -> None:
        """_create_hpc_job_record should pass primary cluster when cluster_used='primary'."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            await orchestrator._create_hpc_job_record(
                "task-1",
                "slurm-123",
                {"temp": 300},
                cluster_used="primary",
            )
            mock_map["create_hpc_job_record"].assert_called_once_with(
                "task-1",
                "slurm-123",
                {"temp": 300},
                "login01.example.com",
            )
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_uses_backup_cluster_name(self, backup_config: SSHConnectionConfig) -> None:
        """_create_hpc_job_record should pass backup host when cluster_used='backup'."""
        orchestrator, mock_map, patchers = _build_orchestrator(backup_config)
        try:
            await orchestrator._create_hpc_job_record(
                "task-1",
                "slurm-123",
                {"temp": 300},
                cluster_used="backup",
            )
            mock_map["create_hpc_job_record"].assert_called_once_with(
                "task-1",
                "slurm-123",
                {"temp": 300},
                "backup01.example.com",
            )
        finally:
            _cleanup(patchers)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_unknown_when_no_backup_hosts(self, primary_config: SSHConnectionConfig) -> None:
        """_create_hpc_job_record should use 'unknown' when backup used but no backup hosts."""
        orchestrator, mock_map, patchers = _build_orchestrator(primary_config)
        try:
            await orchestrator._create_hpc_job_record(
                "task-1",
                "slurm-123",
                {"temp": 300},
                cluster_used="backup",
            )
            mock_map["create_hpc_job_record"].assert_called_once_with(
                "task-1",
                "slurm-123",
                {"temp": 300},
                "unknown",
            )
        finally:
            _cleanup(patchers)
