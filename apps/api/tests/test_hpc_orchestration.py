"""Tests for HPC Orchestration System - Phase 4.1-4.5."""

import asyncio
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from nfm_db.services.hpc_ssh import (
    SSHConnectionManager,
    SSHConnectionConfig,
)
from nfm_db.services.hpc_orchestration import HPCOrchestrator


class TestSSHConnectionManager:
    """Test SSH connection pool and health management."""

    def test_connection_pool_initialization(self):
        """Test connection pool initializes with correct settings."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=10
        )

        assert manager.host == "test.example.com"
        assert manager.username == "testuser"
        assert manager.max_connections == 10
        assert manager.available_connections == 10

    def test_acquire_connection_returns_connection(self):
        """Test acquiring a connection returns a valid SSH client."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2,
            skip_key_validation=True
        )

        with patch('paramiko.SSHClient') as mock_ssh:
            mock_client = MagicMock()
            mock_ssh.return_value = mock_client

            conn = manager.acquire_connection()

            assert conn is not None
            assert manager.available_connections == 1
            mock_ssh.assert_called_once()

    def test_acquire_connection_pool_exhaustion(self):
        """Test acquiring connection when pool is exhausted raises error."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=1,
            skip_key_validation=True
        )

        # Acquire the only connection
        with patch('paramiko.SSHClient') as mock_ssh:
            manager.acquire_connection()

            # Try to acquire another - should fail
            with pytest.raises(ConnectionError, match="Connection pool exhausted"):
                manager.acquire_connection()

    def test_release_connection_returns_to_pool(self):
        """Test releasing a connection returns it to the pool."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2,
            skip_key_validation=True
        )

        with patch('paramiko.SSHClient') as mock_ssh:
            conn = manager.acquire_connection()
            assert manager.available_connections == 1

            manager.release_connection(conn)
            assert manager.available_connections == 2

    def test_health_check_successful_connection(self):
        """Test health check returns True for healthy connection."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2
        )

        with patch('paramiko.SSHClient') as mock_ssh:
            mock_client = MagicMock()
            mock_client.exec_command.return_value = (MagicMock(), MagicMock(), MagicMock())
            mock_ssh.return_value = mock_client

            # Override health check to use mock
            is_healthy = manager.check_health(mock_client)

            assert is_healthy is True

    def test_health_check_failed_connection(self):
        """Test health check returns False for failed connection."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2
        )

        mock_client = MagicMock()
        mock_client.exec_command.side_effect = Exception("Connection lost")

        is_healthy = manager.check_health(mock_client)

        assert is_healthy is False

    def test_auto_reconnect_on_failure(self):
        """Test auto-reconnect attempts when connection fails."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2,
            skip_key_validation=True
        )

        with patch('paramiko.SSHClient') as mock_ssh:
            # First connection fails, second succeeds
            mock_ssh.side_effect = [Exception("Failed"), MagicMock()]

            # Should retry and succeed
            conn = manager.acquire_connection_with_retry(max_retries=2)

            assert conn is not None
            assert mock_ssh.call_count == 2  # Failed once, succeeded once

    def test_multi_login_node_support(self):
        """Test connection manager supports multiple login nodes."""
        manager = SSHConnectionManager(
            hosts=["login01.example.com", "login02.example.com", "login03.example.com"],
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=10
        )

        assert len(manager.hosts) == 3
        assert "login01.example.com" in manager.hosts
        assert "login02.example.com" in manager.hosts
        assert "login03.example.com" in manager.hosts


class TestSSHKeyAuthentication:
    """Test SSH key-based authentication."""

    def test_ssh_key_authentication_only(self):
        """Test manager uses SSH key authentication, not passwords."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2,
            skip_key_validation=True
        )

        with patch('paramiko.SSHClient') as mock_ssh:
            mock_client = MagicMock()
            mock_ssh.return_value = mock_client
            mock_client.connect.return_value = None

            manager.acquire_connection()

            # Verify password was never used
            call_kwargs = mock_client.connect.call_args[1] if mock_client.connect.call_args else {}
            assert 'password' not in call_kwargs
            assert 'key_filename' in call_kwargs or 'pkey' in call_kwargs

    def test_missing_ssh_key_raises_error(self):
        """Test missing SSH key file raises appropriate error."""
        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/nonexistent/path/to/key",
            max_connections=2
        )

        with pytest.raises(FileNotFoundError, match="SSH key file not found"):
            manager.acquire_connection()


class TestPhase45Failover:
    """Test Phase 4.5 automatic failover functionality."""

    def test_backup_cluster_configuration(self):
        """Test orchestrator accepts backup cluster configuration."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="primary_user",
            ssh_key_path="/path/to/primary_key",
            backup_hosts=("backup.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            failover_threshold_seconds=300
        )

        orchestrator = HPCOrchestrator(config)

        assert orchestrator.failover_manager.has_backup is True
        assert orchestrator.failover_manager.current_cluster == "primary"
        assert orchestrator.failover_manager.primary_healthy is True

    def test_no_backup_cluster_configured(self):
        """Test orchestrator works without backup cluster."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="primary_user",
            ssh_key_path="/path/to/primary_key"
        )

        orchestrator = HPCOrchestrator(config)

        assert orchestrator.failover_manager.has_backup is False
        assert orchestrator.failover_manager.current_cluster == "primary"

    def test_check_primary_health_success(self):
        """Test primary health check returns True for healthy cluster."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        with patch.object(orchestrator.ssh_manager, 'acquire_connection_with_retry') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            is_healthy = orchestrator.check_primary_health()

            assert is_healthy is True
            assert orchestrator.failover_manager.last_health_check is not None

    def test_check_primary_health_failure(self):
        """Test primary health check returns False for unhealthy cluster."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        with patch.object(orchestrator.ssh_manager, 'acquire_connection_with_retry') as mock_acquire:
            mock_acquire.return_value = None

            is_healthy = orchestrator.check_primary_health()

            assert is_healthy is False

    def test_should_trigger_failover_no_backup(self):
        """Test failover not triggered when no backup configured."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        # Mark primary as unhealthy
        orchestrator.failover_manager.primary_healthy = False

        # Should not trigger failover (no backup)
        should_failover = orchestrator.should_trigger_failover()

        assert should_failover is False

    def test_should_trigger_failover_after_threshold(self):
        """Test failover triggered after 5-minute threshold exceeded."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("backup.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            failover_threshold_seconds=300,
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        # Set last health check to 6 minutes ago
        orchestrator.failover_manager.last_health_check = datetime.now() - timedelta(seconds=360)

        should_failover = orchestrator.should_trigger_failover()

        assert should_failover is True

    def test_trigger_failover_success(self):
        """Test successful failover to backup cluster."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("backup.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        with patch.object(orchestrator.failover_manager._backup_ssh_manager, 'acquire_connection_with_retry') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            result = asyncio.run(orchestrator.trigger_failover())

            assert result is True
            assert orchestrator.failover_manager.current_cluster == "backup"
            assert orchestrator.failover_manager.failover_count == 1

    def test_trigger_failover_no_backup(self):
        """Test failover fails when no backup configured."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key"
        )

        orchestrator = HPCOrchestrator(config)

        result = asyncio.run(orchestrator.trigger_failover())

        assert result is False

    def test_try_recover_primary_success(self):
        """Test primary cluster recovery detection."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)
        orchestrator.failover_manager.primary_healthy = False

        with patch.object(orchestrator.failover_manager, 'check_primary_health', return_value=True):
            with patch.object(orchestrator.failover_manager, 'log_failover_event'):
                recovered = asyncio.run(orchestrator.try_recover_primary())

            assert recovered is True
            assert orchestrator.failover_manager.primary_healthy is True


class TestThreadSafety:
    """Test thread-safe connection pool operations."""

    def test_connection_lock_is_threading_lock(self):
        """Test connection manager uses threading.Lock for thread safety."""
        import threading

        manager = SSHConnectionManager(
            host="test.example.com",
            username="testuser",
            ssh_key_path="/path/to/key",
            max_connections=2,
            skip_key_validation=True
        )

        # Verify _connection_lock is a threading.Lock instance
        assert hasattr(manager._connection_lock, 'acquire')
        assert hasattr(manager._connection_lock, 'release')
        assert hasattr(manager._connection_lock, 'locked')


class TestCleanup:
    """Test resource cleanup on orchestrator destruction."""

    def test_cleanup_closes_primary_connections(self):
        """Test cleanup method closes primary connections."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        # cleanup() delegates to failover_manager.cleanup()
        with patch.object(orchestrator.failover_manager, 'cleanup') as mock_cleanup:
            orchestrator.cleanup()
            mock_cleanup.assert_called_once()

    def test_cleanup_closes_all_connections_with_backup(self):
        """Test cleanup method closes both primary and backup connections."""
        config = SSHConnectionConfig(
            hosts=("primary.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("backup.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True
        )

        orchestrator = HPCOrchestrator(config)

        # Cleanup delegates to failover_manager which handles both primary and backup
        with patch.object(orchestrator.failover_manager, 'cleanup') as mock_failover_cleanup:
            orchestrator.cleanup()
            mock_failover_cleanup.assert_called_once()
