"""Tests for HPC Orchestration System - Phase 4.1: SSH Infrastructure."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from nfm_db.services.hpc_orchestration import SSHConnectionManager, HPCOrchestrator


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
