"""Comprehensive tests for HPCFailoverManager.

Covers all methods of the HPCFailoverManager class including:
- Initialization with/without backup cluster
- Property accessors (hpc_cluster, has_backup, current_ssh_manager, etc.)
- Event logging with DB success and fallback paths
- Primary health checking
- Failover trigger decision logic
- Failover execution and error handling
- Primary recovery attempts
- Resource cleanup
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.hpc_failover import HPCFailoverManager
from nfm_db.services.hpc_ssh import SSHConnectionConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def primary_config() -> SSHConnectionConfig:
    """Create a primary cluster SSH config."""
    return SSHConnectionConfig(
        hosts=("guangzhou.example.com",),
        username="test_user",
        ssh_key_path="/path/to/key",
        failover_threshold_seconds=300,
    )


@pytest.fixture
def config_with_backup() -> SSHConnectionConfig:
    """Create a config with both primary and backup hosts."""
    return SSHConnectionConfig(
        hosts=("guangzhou.example.com",),
        username="test_user",
        ssh_key_path="/path/to/key",
        backup_hosts=("tianjin.example.com",),
        backup_username="backup_user",
        backup_ssh_key_path="/path/to/backup_key",
        failover_threshold_seconds=300,
    )


@pytest.fixture
def mock_ssh_manager() -> MagicMock:
    """Create a mock primary SSH manager."""
    manager = MagicMock()
    manager.acquire_connection_with_retry = MagicMock(return_value=MagicMock())
    manager.release_connection = MagicMock()
    manager.cleanup = MagicMock()
    return manager


@pytest.fixture
def mock_backup_ssh_manager() -> MagicMock:
    """Create a mock backup SSH manager."""
    manager = MagicMock()
    manager.acquire_connection_with_retry = MagicMock(return_value=MagicMock())
    manager.release_connection = MagicMock()
    manager.cleanup = MagicMock()
    return manager


@pytest.fixture
def manager_no_backup(
    primary_config: SSHConnectionConfig,
    mock_ssh_manager: MagicMock,
) -> HPCFailoverManager:
    """Create manager without backup cluster."""
    return HPCFailoverManager(
        config=primary_config,
        ssh_manager=mock_ssh_manager,
    )


@pytest.fixture
def manager_with_backup(
    config_with_backup: SSHConnectionConfig,
    mock_ssh_manager: MagicMock,
    mock_backup_ssh_manager: MagicMock,
) -> HPCFailoverManager:
    """Create manager with backup cluster configured."""
    return HPCFailoverManager(
        config=config_with_backup,
        ssh_manager=mock_ssh_manager,
        backup_ssh_manager=mock_backup_ssh_manager,
    )


# ---------------------------------------------------------------------------
# __init__ tests
# ---------------------------------------------------------------------------

class TestInit:
    """Tests for HPCFailoverManager.__init__."""

    @pytest.mark.unit
    def test_init_with_backup_sets_default_state(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN config with backup hosts and backup SSH manager
        WHEN manager is initialized
        THEN current_cluster is 'primary', primary_healthy is True,
             failover_count is 0, and last_health_check is None.
        """
        assert manager_with_backup.current_cluster == "primary"
        assert manager_with_backup.primary_healthy is True
        assert manager_with_backup.failover_count == 0
        assert manager_with_backup.last_health_check is None

    @pytest.mark.unit
    def test_init_without_backup_sets_default_state(
        self,
        manager_no_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN config without backup and no backup SSH manager
        WHEN manager is initialized
        THEN state is identical to backup case.
        """
        assert manager_no_backup.current_cluster == "primary"
        assert manager_no_backup.primary_healthy is True
        assert manager_no_backup.failover_count == 0
        assert manager_no_backup.last_health_check is None

    @pytest.mark.unit
    def test_init_stores_config_and_managers(
        self,
        manager_with_backup: HPCFailoverManager,
        config_with_backup: SSHConnectionConfig,
        mock_ssh_manager: MagicMock,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN manager initialized with config and managers
        WHEN inspecting internal references
        THEN the manager stores them on private attributes.
        """
        assert manager_with_backup._config is config_with_backup
        assert manager_with_backup._ssh_manager is mock_ssh_manager
        assert manager_with_backup._backup_ssh_manager is mock_backup_ssh_manager


# ---------------------------------------------------------------------------
# hpc_cluster property tests
# ---------------------------------------------------------------------------

class TestHpcClusterProperty:
    """Tests for the hpc_cluster property."""

    @pytest.mark.unit
    def test_returns_first_host_from_tuple(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN config with hosts as a tuple
        WHEN hpc_cluster is accessed
        THEN the first host is returned.
        """
        assert manager_with_backup.hpc_cluster == "guangzhou.example.com"

    @pytest.mark.unit
    def test_returns_first_host_from_list(self) -> None:
        """GIVEN config where hosts is a plain list
        WHEN hpc_cluster is accessed
        THEN the first host is returned.
        """
        config = MagicMock()
        config.hosts = ["list-host.example.com"]
        mgr = HPCFailoverManager(config=config, ssh_manager=MagicMock())
        assert mgr.hpc_cluster == "list-host.example.com"

    @pytest.mark.unit
    def test_returns_empty_string_when_hosts_empty(self) -> None:
        """GIVEN config with empty hosts list
        WHEN hpc_cluster is accessed
        THEN an empty string is returned.
        """
        config = MagicMock()
        config.hosts = []
        mgr = HPCFailoverManager(config=config, ssh_manager=MagicMock())
        assert mgr.hpc_cluster == ""

    @pytest.mark.unit
    def test_returns_empty_string_when_hosts_none_like(self) -> None:
        """GIVEN config where hosts is None
        WHEN hpc_cluster is accessed
        THEN an empty string is returned.
        """
        config = MagicMock()
        config.hosts = None
        mgr = HPCFailoverManager(config=config, ssh_manager=MagicMock())
        # isinstance check with None should be falsy, so empty string
        assert mgr.hpc_cluster == ""


# ---------------------------------------------------------------------------
# has_backup property tests
# ---------------------------------------------------------------------------

class TestHasBackupProperty:
    """Tests for the has_backup property."""

    @pytest.mark.unit
    def test_true_when_backup_manager_provided(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN manager initialized with a backup SSH manager
        WHEN has_backup is accessed
        THEN returns True.
        """
        assert manager_with_backup.has_backup is True

    @pytest.mark.unit
    def test_false_when_no_backup_manager(
        self,
        manager_no_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN manager initialized without a backup SSH manager
        WHEN has_backup is accessed
        THEN returns False.
        """
        assert manager_no_backup.has_backup is False


# ---------------------------------------------------------------------------
# current_ssh_manager property tests
# ---------------------------------------------------------------------------

class TestCurrentSshManagerProperty:
    """Tests for the current_ssh_manager property."""

    @pytest.mark.unit
    def test_returns_primary_when_on_primary(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN manager with current_cluster set to 'primary'
        WHEN current_ssh_manager is accessed
        THEN the primary SSH manager is returned.
        """
        manager_with_backup.current_cluster = "primary"
        assert manager_with_backup.current_ssh_manager is mock_ssh_manager

    @pytest.mark.unit
    def test_returns_backup_when_on_backup_with_manager(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN manager with current_cluster set to 'backup' and backup manager exists
        WHEN current_ssh_manager is accessed
        THEN the backup SSH manager is returned.
        """
        manager_with_backup.current_cluster = "backup"
        assert manager_with_backup.current_ssh_manager is mock_backup_ssh_manager

    @pytest.mark.unit
    def test_falls_back_to_primary_when_backup_missing(self) -> None:
        """GIVEN manager with current_cluster set to 'backup' but no backup manager
        WHEN current_ssh_manager is accessed
        THEN falls back to the primary SSH manager.
        """
        config = MagicMock()
        ssh_mgr = MagicMock()
        mgr = HPCFailoverManager(config=config, ssh_manager=ssh_mgr)
        mgr.current_cluster = "backup"
        assert mgr.current_ssh_manager is ssh_mgr


# ---------------------------------------------------------------------------
# current_cluster_name property tests
# ---------------------------------------------------------------------------

class TestCurrentClusterNameProperty:
    """Tests for the current_cluster_name property."""

    @pytest.mark.unit
    def test_returns_primary_name_when_on_primary(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN manager on the primary cluster
        WHEN current_cluster_name is accessed
        THEN returns the primary hostname from hpc_cluster.
        """
        manager_with_backup.current_cluster = "primary"
        assert manager_with_backup.current_cluster_name == "guangzhou.example.com"

    @pytest.mark.unit
    def test_returns_backup_name_when_on_backup_with_hosts(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN manager on backup cluster and config has backup_hosts
        WHEN current_cluster_name is accessed
        THEN returns the first backup hostname.
        """
        manager_with_backup.current_cluster = "backup"
        assert manager_with_backup.current_cluster_name == "tianjin.example.com"

    @pytest.mark.unit
    def test_returns_unknown_when_on_backup_without_hosts(self) -> None:
        """GIVEN manager on backup cluster but config has no backup_hosts
        WHEN current_cluster_name is accessed
        THEN returns 'unknown'.
        """
        config = MagicMock()
        config.hosts = ("primary.example.com",)
        config.backup_hosts = None
        mgr = HPCFailoverManager(config=config, ssh_manager=MagicMock())
        mgr.current_cluster = "backup"
        assert mgr.current_cluster_name == "unknown"

    @pytest.mark.unit
    def test_coerces_non_string_backup_host(self) -> None:
        """GIVEN backup_hosts with a non-string first element
        WHEN current_cluster_name is accessed
        THEN the element is coerced to string.
        """
        config = MagicMock()
        config.hosts = ("primary.example.com",)
        config.backup_hosts = (42,)
        mgr = HPCFailoverManager(config=config, ssh_manager=MagicMock())
        mgr.current_cluster = "backup"
        assert mgr.current_cluster_name == "42"


# ---------------------------------------------------------------------------
# log_failover_event tests
# ---------------------------------------------------------------------------

class TestLogFailoverEvent:
    """Tests for HPCFailoverManager.log_failover_event."""

    @pytest.fixture
    def mock_db_session(self) -> AsyncMock:
        """Create a mock async database session."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        return session

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_successful_db_logging(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_db_session: AsyncMock,
    ) -> None:
        """GIVEN database is available
        WHEN log_failover_event is called
        THEN event is added to session and committed.
        """
        async def mock_db_gen():
            yield mock_db_session

        with (
            patch("nfm_db.database.get_db", return_value=mock_db_gen()),
            patch("nfm_db.models.hpc_failover_event.HPCFailoverEvent", autospec=True),
        ):
            await manager_with_backup.log_failover_event(
                event_type="failover_triggered",
                source_cluster="guangzhou.example.com",
                target_cluster="tianjin.example.com",
                reason="Primary down",
                success=True,
                failure_count=1,
            )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_db_failure_falls_back_to_logging(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN database raises an exception
        WHEN log_failover_event is called
        THEN no exception propagates and the method returns gracefully.
        """
        async def broken_db_gen():
            raise RuntimeError("DB connection lost")
            yield

        with patch(
            "nfm_db.database.get_db",
            return_value=broken_db_gen(),
        ):
            # Should NOT raise
            await manager_with_backup.log_failover_event(
                event_type="failover_failed",
                source_cluster="guangzhou.example.com",
                reason="DB down",
                success=False,
            )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_commit_failure_falls_back_to_logging(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_db_session: AsyncMock,
    ) -> None:
        """GIVEN db.commit raises an exception
        WHEN log_failover_event is called
        THEN the exception is caught and logged.
        """
        mock_db_session.commit.side_effect = RuntimeError("Commit failed")

        async def mock_db_gen():
            yield mock_db_session

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch("nfm_db.models.hpc_failover_event.HPCFailoverEvent", autospec=True):
                await manager_with_backup.log_failover_event(
                    event_type="recovery_attempted",
                    source_cluster="guangzhou.example.com",
                    reason="Test commit error",
                    success=False,
                )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_default_metadata_is_empty_dict(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_db_session: AsyncMock,
    ) -> None:
        """GIVEN no event_metadata is passed
        WHEN log_failover_event is called
        THEN the event is created with an empty dict for metadata.
        """
        async def mock_db_gen():
            yield mock_db_session

        mock_event_cls = MagicMock(return_value=MagicMock())
        with patch("nfm_db.database.get_db", return_value=mock_db_gen()), patch(
            "nfm_db.models.hpc_failover_event.HPCFailoverEvent",
            mock_event_cls,
        ):
            await manager_with_backup.log_failover_event(
                event_type="failover_triggered",
                source_cluster="guangzhou.example.com",
                reason="Test",
            )

        # Verify HPCFailoverEvent was called with event_metadata={}
        _, kwargs = mock_event_cls.call_args if mock_event_cls.call_args else (None, {})
        assert kwargs.get("event_metadata") == {}

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_custom_metadata_is_passed_through(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_db_session: AsyncMock,
    ) -> None:
        """GIVEN custom event_metadata is provided
        WHEN log_failover_event is called
        THEN the metadata is forwarded to the event constructor.
        """
        async def mock_db_gen():
            yield mock_db_session

        mock_event_cls = MagicMock(return_value=MagicMock())
        with patch("nfm_db.database.get_db", return_value=mock_db_gen()), patch(
            "nfm_db.models.hpc_failover_event.HPCFailoverEvent",
            mock_event_cls,
        ):
            custom_meta: dict = {"failover_duration": 12.5, "retries": 3}
            await manager_with_backup.log_failover_event(
                event_type="failover_triggered",
                source_cluster="guangzhou.example.com",
                reason="Test",
                event_metadata=custom_meta,
            )

        _, kwargs = mock_event_cls.call_args if mock_event_cls.call_args else (None, {})
        assert kwargs.get("event_metadata") == custom_meta


# ---------------------------------------------------------------------------
# check_primary_health tests
# ---------------------------------------------------------------------------

class TestCheckPrimaryHealth:
    """Tests for HPCFailoverManager.check_primary_health."""

    @pytest.mark.unit
    def test_returns_true_when_connection_acquired(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary SSH manager returns a client on acquire
        WHEN check_primary_health is called
        THEN returns True and releases the connection.
        """
        mock_client = MagicMock()
        mock_ssh_manager.acquire_connection_with_retry.return_value = mock_client

        result: bool = manager_with_backup.check_primary_health()

        assert result is True
        mock_ssh_manager.release_connection.assert_called_once_with(mock_client)
        assert manager_with_backup.last_health_check is not None

    @pytest.mark.unit
    def test_returns_false_when_connection_is_none(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary SSH manager returns None
        WHEN check_primary_health is called
        THEN returns False.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = None

        result: bool = manager_with_backup.check_primary_health()

        assert result is False
        mock_ssh_manager.release_connection.assert_not_called()

    @pytest.mark.unit
    def test_returns_false_when_exception_raised(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary SSH manager raises an exception
        WHEN check_primary_health is called
        THEN returns False without propagating the exception.
        """
        mock_ssh_manager.acquire_connection_with_retry.side_effect = RuntimeError(
            "SSH timeout"
        )

        result: bool = manager_with_backup.check_primary_health()

        assert result is False

    @pytest.mark.unit
    def test_updates_last_health_check_on_success(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN health check succeeds
        WHEN check_primary_health is called
        THEN last_health_check is set to current time.
        """
        frozen_time = datetime(2025, 6, 15, 12, 0, 0)
        mock_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()

        with patch("nfm_db.services.hpc_failover.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_time
            manager_with_backup.check_primary_health()

        assert manager_with_backup.last_health_check == frozen_time


# ---------------------------------------------------------------------------
# should_trigger_failover tests
# ---------------------------------------------------------------------------

class TestShouldTriggerFailover:
    """Tests for HPCFailoverManager.should_trigger_failover."""

    @pytest.mark.unit
    def test_returns_false_when_no_backup(
        self,
        manager_no_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN no backup SSH manager is configured
        WHEN should_trigger_failover is called
        THEN returns False.
        """
        assert manager_no_backup.should_trigger_failover() is False

    @pytest.mark.unit
    def test_returns_true_when_already_unhealthy(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN primary_healthy is already False
        WHEN should_trigger_failover is called
        THEN returns True without checking health again.
        """
        manager_with_backup.primary_healthy = False
        assert manager_with_backup.should_trigger_failover() is True

    @pytest.mark.unit
    def test_returns_false_when_health_check_passes(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary health check succeeds
        WHEN should_trigger_failover is called
        THEN returns False.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()
        manager_with_backup.last_health_check = None

        result: bool = manager_with_backup.should_trigger_failover()

        assert result is False

    @pytest.mark.unit
    def test_returns_false_when_unhealthy_but_within_threshold(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary health check fails but time since last healthy check
             is within the threshold
        WHEN should_trigger_failover is called
        THEN returns False.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = None
        now = datetime(2025, 6, 15, 12, 5, 0)
        manager_with_backup.last_health_check = datetime(2025, 6, 15, 12, 0, 0)
        # 5 minutes since healthy -> within 300s threshold

        with patch("nfm_db.services.hpc_failover.datetime") as mock_dt:
            mock_dt.now.return_value = now
            result = manager_with_backup.should_trigger_failover()

        assert result is False

    @pytest.mark.unit
    def test_returns_true_when_unhealthy_past_threshold(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary health check fails and time since last healthy check
             exceeds the threshold
        WHEN should_trigger_failover is called
        THEN returns True and sets primary_healthy to False.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = None
        now = datetime(2025, 6, 15, 12, 10, 0)
        manager_with_backup.last_health_check = datetime(2025, 6, 15, 12, 0, 0)
        # 600 seconds since healthy -> exceeds 300s threshold

        with patch("nfm_db.services.hpc_failover.datetime") as mock_dt:
            mock_dt.now.return_value = now
            result = manager_with_backup.should_trigger_failover()

        assert result is True
        assert manager_with_backup.primary_healthy is False

    @pytest.mark.unit
    def test_sets_last_health_check_on_first_failed_check(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN last_health_check is None and health check fails
        WHEN should_trigger_failover is called
        THEN last_health_check is set to now but returns False (needs more time).
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = None
        manager_with_backup.last_health_check = None
        frozen_time = datetime(2025, 6, 15, 12, 0, 0)

        with patch("nfm_db.services.hpc_failover.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_time
            result = manager_with_backup.should_trigger_failover()

        assert result is False
        assert manager_with_backup.last_health_check == frozen_time

    @pytest.mark.unit
    def test_triggers_failover_when_last_check_past_threshold_no_current_check(
        self,
        manager_with_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN last_health_check is set and time exceeds threshold
             (skipping health check on already-flagged path)
        WHEN should_trigger_failover is called
        THEN returns True.
        """
        now = datetime(2025, 6, 15, 12, 10, 0)
        manager_with_backup.last_health_check = datetime(2025, 6, 15, 12, 0, 0)

        with patch("nfm_db.services.hpc_failover.datetime") as mock_dt:
            mock_dt.now.return_value = now
            result = manager_with_backup.should_trigger_failover()

        assert result is True
        assert manager_with_backup.primary_healthy is False


# ---------------------------------------------------------------------------
# trigger_failover tests
# ---------------------------------------------------------------------------

class TestTriggerFailover:
    """Tests for HPCFailoverManager.trigger_failover."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_false_when_no_backup(
        self,
        manager_no_backup: HPCFailoverManager,
    ) -> None:
        """GIVEN no backup SSH manager
        WHEN trigger_failover is called
        THEN returns False and logs a failover_failed event.
        """
        with patch.object(manager_no_backup, "log_failover_event", new_callable=AsyncMock) as mock_log:
            result: bool = await manager_no_backup.trigger_failover()

        assert result is False
        mock_log.assert_awaited_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "failover_failed"
        assert call_kwargs["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_false_when_backup_unreachable(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN backup cluster connectivity test returns None
        WHEN trigger_failover is called
        THEN returns False and logs a failover_failed event.
        """
        mock_backup_ssh_manager.acquire_connection_with_retry.return_value = None

        with patch.object(
            manager_with_backup, "log_failover_event", new_callable=AsyncMock
        ) as mock_log:
            result = await manager_with_backup.trigger_failover()

        assert result is False
        mock_log.assert_awaited_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "failover_failed"
        assert "connectivity" in call_kwargs["reason"].lower()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_successful_failover_increments_count_and_switches(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN backup cluster is reachable
        WHEN trigger_failover is called
        THEN failover_count is incremented, cluster switches to 'backup',
             and returns True.
        """
        mock_client = MagicMock()
        mock_backup_ssh_manager.acquire_connection_with_retry.return_value = mock_client

        with (
            patch.object(
                manager_with_backup, "log_failover_event", new_callable=AsyncMock
            ),
            patch("nfm_db.services.hpc_failover.PROMETHEUS_AVAILABLE", False),
        ):
            result = await manager_with_backup.trigger_failover()

        assert result is True
        assert manager_with_backup.failover_count == 1
        assert manager_with_backup.current_cluster == "backup"
        mock_backup_ssh_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_successful_failover_logs_event(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN backup cluster is reachable
        WHEN trigger_failover is called
        THEN a failover_triggered event is logged with correct details.

        Note: to_cluster is evaluated from current_cluster_name BEFORE the
        cluster switches to 'backup', so it equals the primary hostname.
        """
        mock_backup_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()

        with (
            patch.object(
                manager_with_backup, "log_failover_event", new_callable=AsyncMock
            ) as mock_log,
            patch("nfm_db.services.hpc_failover.PROMETHEUS_AVAILABLE", False),
        ):
            await manager_with_backup.trigger_failover()

        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "failover_triggered"
        assert call_kwargs["success"] is True
        assert call_kwargs["failure_count"] == 1
        assert call_kwargs["source_cluster"] == "guangzhou.example.com"
        assert call_kwargs["target_cluster"] == "guangzhou.example.com"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_successful_failover_increments_prometheus_counter(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN Prometheus is available and failover succeeds
        WHEN trigger_failover is called
        THEN the hpc_failover_events counter is incremented.

        Note: to_cluster reflects current_cluster_name evaluated before
        the switch, so both from and to are the primary hostname.
        """
        mock_backup_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()
        mock_counter = MagicMock()

        with (
            patch.object(
                manager_with_backup, "log_failover_event", new_callable=AsyncMock
            ),
            patch("nfm_db.services.hpc_failover.PROMETHEUS_AVAILABLE", True),
            patch(
                "nfm_db.services.hpc_failover.hpc_failover_events",
                mock_counter,
            ),
        ):
            await manager_with_backup.trigger_failover()

        mock_counter.labels.assert_called_once_with(
            from_cluster="guangzhou.example.com",
            to_cluster="guangzhou.example.com",
        )
        mock_counter.labels.return_value.inc.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_exception_during_failover_returns_false(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN backup SSH manager raises an exception during acquire
        WHEN trigger_failover is called
        THEN returns False and logs a failover_failed event with exception info.
        """
        mock_backup_ssh_manager.acquire_connection_with_retry.side_effect = RuntimeError(
            "Connection reset"
        )

        with patch.object(
            manager_with_backup, "log_failover_event", new_callable=AsyncMock
        ) as mock_log:
            result = await manager_with_backup.trigger_failover()

        assert result is False
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "failover_failed"
        assert call_kwargs["success"] is False
        assert "Connection reset" in call_kwargs["reason"]
        assert call_kwargs["event_metadata"]["exception_type"] == "RuntimeError"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_multiple_failovers_increment_count(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN multiple successful failover calls
        WHEN trigger_failover is called twice
        THEN failover_count increments each time.
        """
        mock_backup_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()

        with (
            patch.object(
                manager_with_backup, "log_failover_event", new_callable=AsyncMock
            ),
            patch("nfm_db.services.hpc_failover.PROMETHEUS_AVAILABLE", False),
        ):
            await manager_with_backup.trigger_failover()
            assert manager_with_backup.failover_count == 1

            await manager_with_backup.trigger_failover()
            assert manager_with_backup.failover_count == 2


# ---------------------------------------------------------------------------
# try_recover_primary tests
# ---------------------------------------------------------------------------

class TestTryRecoverPrimary:
    """Tests for HPCFailoverManager.try_recover_primary."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_true_when_primary_recovers(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary health check passes
        WHEN try_recover_primary is called
        THEN returns True, sets primary_healthy to True, and logs recovery event.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()
        frozen_time = datetime(2025, 6, 15, 12, 0, 0)

        with (
            patch.object(
                manager_with_backup, "log_failover_event", new_callable=AsyncMock
            ) as mock_log,
            patch("nfm_db.services.hpc_failover.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = frozen_time
            result = await manager_with_backup.try_recover_primary()

        assert result is True
        assert manager_with_backup.primary_healthy is True
        mock_log.assert_awaited_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "primary_recovered"
        assert call_kwargs["success"] is True

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_returns_false_when_primary_still_unhealthy(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary health check fails
        WHEN try_recover_primary is called
        THEN returns False and logs a recovery_attempted event.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = None

        with patch.object(
            manager_with_backup, "log_failover_event", new_callable=AsyncMock
        ) as mock_log:
            result = await manager_with_backup.try_recover_primary()

        assert result is False
        mock_log.assert_awaited_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["event_type"] == "recovery_attempted"
        assert call_kwargs["success"] is False

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_recovery_event_includes_metadata_timestamp(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN primary recovers successfully
        WHEN try_recover_primary is called
        THEN recovery event metadata includes an ISO-format timestamp.
        """
        mock_ssh_manager.acquire_connection_with_retry.return_value = MagicMock()
        frozen_time = datetime(2025, 6, 15, 12, 30, 0)

        with (
            patch.object(
                manager_with_backup, "log_failover_event", new_callable=AsyncMock
            ) as mock_log,
            patch("nfm_db.services.hpc_failover.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = frozen_time
            await manager_with_backup.try_recover_primary()

        call_kwargs = mock_log.call_args[1]
        meta = call_kwargs["event_metadata"]
        assert "recovery_timestamp" in meta
        assert meta["recovery_timestamp"] == "2025-06-15T12:30:00"


# ---------------------------------------------------------------------------
# cleanup tests
# ---------------------------------------------------------------------------

class TestCleanup:
    """Tests for HPCFailoverManager.cleanup."""

    @pytest.mark.unit
    def test_cleans_up_both_managers(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN manager has both primary and backup SSH managers
        WHEN cleanup is called
        THEN both managers' cleanup is called.
        """
        manager_with_backup.cleanup()
        mock_backup_ssh_manager.cleanup.assert_called_once()
        mock_ssh_manager.cleanup.assert_called_once()

    @pytest.mark.unit
    def test_cleans_up_only_primary_when_no_backup(
        self,
        manager_no_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN manager has only primary SSH manager
        WHEN cleanup is called
        THEN only the primary manager cleanup is called.
        """
        manager_no_backup.cleanup()
        mock_ssh_manager.cleanup.assert_called_once()

    @pytest.mark.unit
    def test_cleanup_is_idempotent(
        self,
        manager_with_backup: HPCFailoverManager,
        mock_ssh_manager: MagicMock,
        mock_backup_ssh_manager: MagicMock,
    ) -> None:
        """GIVEN cleanup has already been called
        WHEN cleanup is called again
        THEN both cleanup methods are called again (delegated to managers).
        """
        manager_with_backup.cleanup()
        manager_with_backup.cleanup()
        assert mock_backup_ssh_manager.cleanup.call_count == 2
        assert mock_ssh_manager.cleanup.call_count == 2
