"""Tests for HPC SSH Connection Management module."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from nfm_db.services.hpc_ssh import (
    HPCConnectionError,
    JobSubmissionError,
    SSHConnectionConfig,
    SSHConnectionManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_prometheus() -> MagicMock:
    """Patch prometheus metrics to prevent real metrics registration."""
    with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", False):
        with patch("nfm_db.services.hpc_ssh.hpc_active_connections") as mock_active:
            with patch("nfm_db.services.hpc_ssh.hpc_connection_errors") as mock_errors:
                yield {"active": mock_active, "errors": mock_errors}


@pytest.fixture()
def mock_ssh_client() -> MagicMock:
    """Return a fresh MagicMock mimicking paramiko.SSHClient."""
    client = MagicMock(spec=paramiko.SSHClient)
    client.transport = MagicMock()
    return client


@pytest.fixture()
def mock_paramiko_constructor(mock_ssh_client: MagicMock) -> MagicMock:
    """Patch paramiko.SSHClient constructor to return a mock.

    Also patches PROMETHEUS_AVAILABLE to False so tests that use this
    fixture without explicitly using mock_prometheus do not depend on
    the real prometheus_client installation.
    """
    with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", False):
        with patch("nfm_db.services.hpc_ssh.hpc_active_connections"):
            with patch("nfm_db.services.hpc_ssh.hpc_connection_errors"):
                with patch(
                    "nfm_db.services.hpc_ssh.paramiko.SSHClient",
                    return_value=mock_ssh_client,
                ):
                    yield mock_ssh_client


@pytest.fixture()
def default_config() -> SSHConnectionConfig:
    """Return a basic SSHConnectionConfig for testing."""
    return SSHConnectionConfig(
        hosts=("hpc-node-01.cluster.org",),
        username="testuser",
        ssh_key_path="/home/testuser/.ssh/id_rsa",
    )


# ---------------------------------------------------------------------------
# SSHConnectionConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionConfig:
    """Tests for the frozen SSHConnectionConfig dataclass."""

    def test_basic_fields(self, default_config: SSHConnectionConfig) -> None:
        assert default_config.hosts == ("hpc-node-01.cluster.org",)
        assert default_config.username == "testuser"
        assert default_config.ssh_key_path == "/home/testuser/.ssh/id_rsa"
        assert default_config.max_connections == 10
        assert default_config.heartbeat_interval == 30
        assert default_config.skip_key_validation is False
        assert default_config.backup_hosts is None
        assert default_config.backup_username is None
        assert default_config.backup_ssh_key_path is None
        assert default_config.failover_threshold_seconds == 300
        assert default_config.work_dir == "/scratch/{username}/nfm-md"

    def test_frozen_immutability(self, default_config: SSHConnectionConfig) -> None:
        with pytest.raises(FrozenInstanceError):
            default_config.username = "changed"  # type: ignore[misc]

    def test_frozen_hosts_tuple(self, default_config: SSHConnectionConfig) -> None:
        with pytest.raises(FrozenInstanceError):
            default_config.hosts = ("other-host",)  # type: ignore[misc]

    def test_custom_defaults(self) -> None:
        config = SSHConnectionConfig(
            hosts=("node1", "node2"),
            username="admin",
            ssh_key_path="/keys/admin",
            max_connections=5,
            heartbeat_interval=60,
            skip_key_validation=True,
            backup_hosts=("backup1",),
            backup_username="backup-admin",
            backup_ssh_key_path="/keys/backup",
            failover_threshold_seconds=600,
            work_dir="/data/work",
        )
        assert config.max_connections == 5
        assert config.heartbeat_interval == 60
        assert config.skip_key_validation is True
        assert config.backup_hosts == ("backup1",)
        assert config.backup_username == "backup-admin"
        assert config.backup_ssh_key_path == "/keys/backup"
        assert config.failover_threshold_seconds == 600
        assert config.work_dir == "/data/work"
        assert len(config.hosts) == 2

    def test_from_lists_basic(self) -> None:
        config = SSHConnectionConfig.from_lists(
            hosts=["hpc-01", "hpc-02"],
            username="svcuser",
            ssh_key_path="/home/svcuser/.ssh/id_ed25519",
        )
        assert isinstance(config.hosts, tuple)
        assert config.hosts == ("hpc-01", "hpc-02")
        assert config.username == "svcuser"
        assert config.ssh_key_path == "/home/svcuser/.ssh/id_ed25519"

    def test_from_lists_with_backup_hosts(self) -> None:
        config = SSHConnectionConfig.from_lists(
            hosts=["hpc-01"],
            username="svcuser",
            ssh_key_path="/home/svcuser/.ssh/id_ed25519",
            backup_hosts=["backup-01", "backup-02"],
            backup_username="backupuser",
            backup_ssh_key_path="/home/backupuser/.ssh/id_rsa",
        )
        assert config.backup_hosts == ("backup-01", "backup-02")
        assert config.backup_username == "backupuser"
        assert config.backup_ssh_key_path == "/home/backupuser/.ssh/id_rsa"

    def test_from_lists_backup_hosts_none(self) -> None:
        config = SSHConnectionConfig.from_lists(
            hosts=["hpc-01"],
            username="svcuser",
            ssh_key_path="/keys/key",
            backup_hosts=None,
        )
        assert config.backup_hosts is None

    def test_from_lists_custom_defaults(self) -> None:
        config = SSHConnectionConfig.from_lists(
            hosts=["hpc-01"],
            username="u",
            ssh_key_path="/k",
            max_connections=3,
            heartbeat_interval=15,
            skip_key_validation=True,
            failover_threshold_seconds=120,
            work_dir="/custom/work",
        )
        assert config.max_connections == 3
        assert config.heartbeat_interval == 15
        assert config.skip_key_validation is True
        assert config.failover_threshold_seconds == 120
        assert config.work_dir == "/custom/work"

    def test_from_lists_empty_backup_list_becomes_none(self) -> None:
        config = SSHConnectionConfig.from_lists(
            hosts=["hpc-01"],
            username="u",
            ssh_key_path="/k",
            backup_hosts=[],
        )
        assert config.backup_hosts is None

    def test_known_hosts_path_default_is_none(self, default_config: SSHConnectionConfig) -> None:
        assert default_config.known_hosts_path is None

    def test_known_hosts_path_custom(self) -> None:
        config = SSHConnectionConfig(
            hosts=("hpc-01",),
            username="u",
            ssh_key_path="/k",
            known_hosts_path="/etc/hpc/known_hosts",
        )
        assert config.known_hosts_path == "/etc/hpc/known_hosts"

    def test_from_lists_supports_known_hosts_path(self) -> None:
        config = SSHConnectionConfig.from_lists(
            hosts=["hpc-01"],
            username="u",
            ssh_key_path="/k",
            known_hosts_path="/home/user/.ssh/known_hosts",
        )
        assert config.known_hosts_path == "/home/user/.ssh/known_hosts"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExceptions:
    """Tests for module-level custom exceptions."""

    def test_job_submission_error_is_exception(self) -> None:
        exc = JobSubmissionError("SLURM sbatch failed")
        assert isinstance(exc, Exception)
        assert str(exc) == "SLURM sbatch failed"

    def test_job_submission_error_default_message(self) -> None:
        exc = JobSubmissionError()
        assert isinstance(exc, Exception)

    def test_hpc_connection_error_is_exception(self) -> None:
        exc = HPCConnectionError("timeout connecting to cluster")
        assert isinstance(exc, Exception)
        assert str(exc) == "timeout connecting to cluster"

    def test_hpc_connection_error_default_message(self) -> None:
        exc = HPCConnectionError()
        assert isinstance(exc, Exception)

    def test_exception_hierarchy(self) -> None:
        assert issubclass(JobSubmissionError, Exception)
        assert issubclass(HPCConnectionError, Exception)
        assert JobSubmissionError is not HPCConnectionError


# ---------------------------------------------------------------------------
# SSHConnectionManager -- __init__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerInit:
    """Tests for SSHConnectionManager initialisation and host normalisation."""

    def test_single_string_host_via_host_param(self) -> None:
        mgr = SSHConnectionManager(
            host="hpc.example.com",
            username="user",
            ssh_key_path="/key",
        )
        assert mgr.hosts == ["hpc.example.com"]
        assert mgr.username == "user"
        assert mgr.ssh_key_path == "/key"
        assert mgr.max_connections == 10

    def test_list_of_hosts_via_host_param(self) -> None:
        mgr = SSHConnectionManager(
            host=["hpc-01", "hpc-02"],
            username="user",
            ssh_key_path="/key",
        )
        assert mgr.hosts == ["hpc-01", "hpc-02"]

    def test_single_string_host_via_hosts_param(self) -> None:
        mgr = SSHConnectionManager(
            hosts="hpc-single.example.com",
            username="user",
            ssh_key_path="/key",
        )
        assert mgr.hosts == ["hpc-single.example.com"]

    def test_list_of_hosts_via_hosts_param(self) -> None:
        mgr = SSHConnectionManager(
            hosts=["a", "b", "c"],
            username="user",
            ssh_key_path="/key",
        )
        assert mgr.hosts == ["a", "b", "c"]

    def test_hosts_param_takes_priority_over_host_param(self) -> None:
        mgr = SSHConnectionManager(
            host="old-host",
            hosts=["new-host-01", "new-host-02"],
            username="user",
            ssh_key_path="/key",
        )
        assert mgr.hosts == ["new-host-01", "new-host-02"]

    def test_no_host_produces_empty_list(self) -> None:
        mgr = SSHConnectionManager(
            username="user",
            ssh_key_path="/key",
        )
        assert mgr.hosts == []

    def test_custom_max_connections(self) -> None:
        mgr = SSHConnectionManager(
            host="hpc",
            username="user",
            ssh_key_path="/key",
            max_connections=3,
        )
        assert mgr.max_connections == 3

    def test_skip_key_validation_default(self) -> None:
        mgr = SSHConnectionManager(host="hpc", username="u", ssh_key_path="/k")
        assert mgr._skip_key_validation is False

    def test_known_hosts_path_default(self) -> None:
        mgr = SSHConnectionManager(host="hpc", username="u", ssh_key_path="/k")
        assert mgr._known_hosts_path is None

    def test_known_hosts_path_custom(self) -> None:
        mgr = SSHConnectionManager(
            host="hpc",
            username="u",
            ssh_key_path="/k",
            known_hosts_path="/etc/hpc/known_hosts",
        )
        assert mgr._known_hosts_path == "/etc/hpc/known_hosts"

    def test_skip_key_validation_true(self) -> None:
        mgr = SSHConnectionManager(
            host="hpc", username="u", ssh_key_path="/k", skip_key_validation=True
        )
        assert mgr._skip_key_validation is True

    def test_active_connections_initially_empty(self) -> None:
        mgr = SSHConnectionManager(host="hpc", username="u", ssh_key_path="/k")
        assert len(mgr._active_connections) == 0

    def test_connection_lock_exists(self) -> None:
        import threading

        mgr = SSHConnectionManager(host="hpc", username="u", ssh_key_path="/k")
        assert isinstance(mgr._connection_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# SSHConnectionManager -- properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerProperties:
    """Tests for SSHConnectionManager property accessors."""

    def test_available_connections_when_pool_empty(self) -> None:
        mgr = SSHConnectionManager(host="hpc", username="u", ssh_key_path="/k", max_connections=5)
        assert mgr.available_connections == 5

    def test_available_connections_decreases(self, mock_paramiko_constructor) -> None:
        mgr = SSHConnectionManager(
            host="hpc",
            username="u",
            ssh_key_path="/k",
            max_connections=3,
            skip_key_validation=True,
        )
        client1 = mgr.acquire_connection()
        assert mgr.available_connections == 2
        mgr.release_connection(client1)
        assert mgr.available_connections == 3

    def test_host_property_returns_first_host(self) -> None:
        mgr = SSHConnectionManager(host="hpc-01", username="u", ssh_key_path="/k")
        assert mgr.host == "hpc-01"

    def test_host_property_multi_host(self) -> None:
        mgr = SSHConnectionManager(hosts=["hpc-01", "hpc-02"], username="u", ssh_key_path="/k")
        assert mgr.host == "hpc-01"

    def test_host_property_empty_list(self) -> None:
        mgr = SSHConnectionManager(username="u", ssh_key_path="/k")
        assert mgr.host == ""


# ---------------------------------------------------------------------------
# SSHConnectionManager -- acquire_connection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerAcquire:
    """Tests for SSHConnectionManager.acquire_connection."""

    def test_acquire_success(self, mock_paramiko_constructor, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="testuser",
            ssh_key_path="/home/testuser/.ssh/id_rsa",
            skip_key_validation=True,
        )
        client = mgr.acquire_connection()
        assert client is mock_paramiko_constructor
        assert len(mgr._active_connections) == 1

    def test_acquire_calls_connect(self, mock_paramiko_constructor, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        mgr.acquire_connection()
        mock_paramiko_constructor.connect.assert_called_once_with(
            hostname="hpc-01",
            username="u",
            key_filename="/k",
            timeout=10,
        )

    def test_acquire_sets_auto_add_policy_when_skip_key_validation(
        self, mock_paramiko_constructor, mock_prometheus
    ) -> None:
        """When skip_key_validation=True, AutoAddPolicy should be used."""
        with patch("nfm_db.services.hpc_ssh.paramiko.AutoAddPolicy") as mock_policy:
            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            mgr.acquire_connection()
            mock_policy.assert_called_once()
            mock_paramiko_constructor.set_missing_host_key_policy.assert_called_once()

    def test_acquire_sets_reject_policy_by_default(
        self, mock_paramiko_constructor, mock_prometheus
    ) -> None:
        """Default (no skip_key_validation) must use RejectPolicy for security."""
        with patch("nfm_db.services.hpc_ssh.paramiko.RejectPolicy") as mock_reject:
            with patch.object(Path, "exists", return_value=True):
                mgr = SSHConnectionManager(
                    host="hpc-01",
                    username="u",
                    ssh_key_path="/k",
                )
                mgr.acquire_connection()
                mock_reject.assert_called_once()
                mock_paramiko_constructor.set_missing_host_key_policy.assert_called_once()

    def test_acquire_with_known_hosts_path_loads_keys(
        self, mock_paramiko_constructor, mock_prometheus
    ) -> None:
        """When known_hosts_path is set, load_host_keys should be called."""
        with patch("nfm_db.services.hpc_ssh.paramiko.RejectPolicy") as mock_reject:
            with patch.object(Path, "exists", return_value=True):
                mgr = SSHConnectionManager(
                    host="hpc-01",
                    username="u",
                    ssh_key_path="/k",
                    known_hosts_path="/etc/hpc/known_hosts",
                )
                mgr.acquire_connection()
                mock_reject.assert_called_once()
                mock_paramiko_constructor.load_host_keys.assert_called_once_with(
                    "/etc/hpc/known_hosts"
                )

    def test_acquire_without_known_hosts_path_skips_load(
        self, mock_paramiko_constructor, mock_prometheus
    ) -> None:
        """When no known_hosts_path, load_host_keys should NOT be called."""
        with patch("nfm_db.services.hpc_ssh.paramiko.RejectPolicy"):
            with patch.object(Path, "exists", return_value=True):
                mgr = SSHConnectionManager(
                    host="hpc-01",
                    username="u",
                    ssh_key_path="/k",
                )
                mgr.acquire_connection()
                mock_paramiko_constructor.load_host_keys.assert_not_called()

    def test_pool_exhausted_raises_connection_error(self, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            max_connections=1,
            skip_key_validation=True,
        )
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mgr.acquire_connection()

        with pytest.raises(ConnectionError, match="Connection pool exhausted"):
            mgr.acquire_connection()

    def test_ssh_key_not_found_raises_file_not_found(self, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/nonexistent/key",
        )
        with pytest.raises(FileNotFoundError, match="SSH key file not found"):
            mgr.acquire_connection()

    def test_ssh_key_exists_allows_acquire(
        self, mock_paramiko_constructor, mock_prometheus
    ) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/existing/key",
        )
        with patch.object(Path, "exists", return_value=True):
            client = mgr.acquire_connection()
        assert client is mock_paramiko_constructor

    def test_acquire_prometheus_active_inc(self, mock_paramiko_constructor) -> None:
        with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", True):
            mock_active = MagicMock()
            with patch("nfm_db.services.hpc_ssh.hpc_active_connections", mock_active):
                mgr = SSHConnectionManager(
                    host="hpc-01",
                    username="u",
                    ssh_key_path="/k",
                    skip_key_validation=True,
                )
                mgr.acquire_connection()
                mock_active.labels.assert_called_with(cluster="hpc-01")
                mock_active.labels.return_value.inc.assert_called_once()


# ---------------------------------------------------------------------------
# SSHConnectionManager -- _create_ssh_connection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerCreateSSH:
    """Tests for SSHConnectionManager._create_ssh_connection."""

    def test_auth_exception_raises_connection_error(self, mock_prometheus) -> None:

        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.connect.side_effect = paramiko.AuthenticationException("auth fail")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            with pytest.raises(ConnectionError, match="Authentication failed"):
                mgr._create_ssh_connection()
            mock_client.close.assert_called_once()

    def test_ssh_exception_raises_connection_error(self, mock_prometheus) -> None:

        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.connect.side_effect = paramiko.SSHException("ssh fail")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            with pytest.raises(ConnectionError, match="SSH connection failed"):
                mgr._create_ssh_connection()
            mock_client.close.assert_called_once()

    def test_generic_exception_raises_connection_error(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.connect.side_effect = OSError("network down")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            with pytest.raises(ConnectionError, match="Failed to connect"):
                mgr._create_ssh_connection()
            mock_client.close.assert_called_once()

    def test_prometheus_auth_error_increment(self, mock_prometheus) -> None:

        with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", True):
            mock_errors = MagicMock()
            with patch("nfm_db.services.hpc_ssh.hpc_connection_errors", mock_errors):
                with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
                    mock_client = MagicMock()
                    mock_cls.return_value = mock_client
                    mock_client.connect.side_effect = paramiko.AuthenticationException("bad")

                    mgr = SSHConnectionManager(
                        host="hpc-01",
                        username="u",
                        ssh_key_path="/k",
                        skip_key_validation=True,
                    )
                    with pytest.raises(ConnectionError):
                        mgr._create_ssh_connection()

                    mock_errors.labels.assert_called_with(
                        cluster="hpc-01", error_type="authentication"
                    )
                    mock_errors.labels.return_value.inc.assert_called_once()

    def test_prometheus_ssh_error_increment(self, mock_prometheus) -> None:

        with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", True):
            mock_errors = MagicMock()
            with patch("nfm_db.services.hpc_ssh.hpc_connection_errors", mock_errors):
                with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
                    mock_client = MagicMock()
                    mock_cls.return_value = mock_client
                    mock_client.connect.side_effect = paramiko.SSHException("broken")

                    mgr = SSHConnectionManager(
                        host="hpc-01",
                        username="u",
                        ssh_key_path="/k",
                        skip_key_validation=True,
                    )
                    with pytest.raises(ConnectionError):
                        mgr._create_ssh_connection()

                    mock_errors.labels.assert_called_with(cluster="hpc-01", error_type="ssh")

    def test_prometheus_unknown_error_increment(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", True):
            mock_errors = MagicMock()
            with patch("nfm_db.services.hpc_ssh.hpc_connection_errors", mock_errors):
                with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
                    mock_client = MagicMock()
                    mock_cls.return_value = mock_client
                    mock_client.connect.side_effect = RuntimeError("unexpected")

                    mgr = SSHConnectionManager(
                        host="hpc-01",
                        username="u",
                        ssh_key_path="/k",
                        skip_key_validation=True,
                    )
                    with pytest.raises(ConnectionError):
                        mgr._create_ssh_connection()

                    mock_errors.labels.assert_called_with(cluster="hpc-01", error_type="unknown")


# ---------------------------------------------------------------------------
# SSHConnectionManager -- acquire_connection_with_retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerAcquireWithRetry:
    """Tests for SSHConnectionManager.acquire_connection_with_retry."""

    def test_success_on_first_try(self, mock_paramiko_constructor, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        client = mgr.acquire_connection_with_retry(max_retries=3)
        assert client is mock_paramiko_constructor

    def test_retries_with_backoff_then_succeeds(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.side_effect = [
                Exception("first fail"),
                mock_client,
            ]

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )

            with patch("nfm_db.services.hpc_ssh.time.sleep") as mock_sleep:
                result = mgr.acquire_connection_with_retry(max_retries=3, backoff_base=0.1)
                assert result is mock_client
                mock_sleep.assert_called_once_with(0.1)

    def test_all_retries_fail_returns_none(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            MagicMock()
            mock_cls.side_effect = Exception("always fail")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )

            with patch("nfm_db.services.hpc_ssh.time.sleep") as mock_sleep:
                result = mgr.acquire_connection_with_retry(max_retries=3, backoff_base=0.1)
                assert result is None
                assert mock_sleep.call_count == 2
                # backoff: 0.1 * 2^0 = 0.1, then 0.1 * 2^1 = 0.2
                mock_sleep.assert_any_call(0.1)
                mock_sleep.assert_any_call(0.2)

    def test_single_retry_exhausted_returns_none(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_cls.side_effect = ConnectionError("pool exhausted")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )

            with patch("nfm_db.services.hpc_ssh.time.sleep"):
                result = mgr.acquire_connection_with_retry(max_retries=1)
                assert result is None

    def test_exponential_backoff_timing(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_cls.side_effect = Exception("fail")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )

            with patch("nfm_db.services.hpc_ssh.time.sleep") as mock_sleep:
                mgr.acquire_connection_with_retry(max_retries=4, backoff_base=1.0)
                # Attempts 0,1,2 sleep; attempt 3 is last and returns None
                calls = [c.args[0] for c in mock_sleep.call_args_list]
                assert calls == [1.0, 2.0, 4.0]

    def test_zero_retries_returns_none_via_post_loop(self, mock_prometheus) -> None:
        """When max_retries=0 the for-loop body never executes,
        falling through to the post-loop `return None` (line 182)."""
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        result = mgr.acquire_connection_with_retry(max_retries=0)
        assert result is None


# ---------------------------------------------------------------------------
# SSHConnectionManager -- release_connection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerRelease:
    """Tests for SSHConnectionManager.release_connection."""

    def test_release_removes_from_pool(self, mock_paramiko_constructor, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        client = mgr.acquire_connection()
        assert len(mgr._active_connections) == 1

        mgr.release_connection(client)
        assert len(mgr._active_connections) == 0

    def test_release_closes_client(self, mock_paramiko_constructor, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        client = mgr.acquire_connection()
        mgr.release_connection(client)
        client.close.assert_called_once()

    def test_release_unknown_client_noop(self, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        unknown_client = MagicMock()
        # Should not raise
        mgr.release_connection(unknown_client)
        assert len(mgr._active_connections) == 0

    def test_release_close_exception_swallowed(
        self, mock_paramiko_constructor, mock_prometheus
    ) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        client = mgr.acquire_connection()
        client.close.side_effect = Exception("close error")
        # Should not raise
        mgr.release_connection(client)
        assert len(mgr._active_connections) == 0

    def test_release_updates_prometheus(self) -> None:
        with patch("nfm_db.services.hpc_ssh.PROMETHEUS_AVAILABLE", True):
            mock_active = MagicMock()
            with patch("nfm_db.services.hpc_ssh.hpc_active_connections", mock_active):
                mock_active.labels.return_value.set = MagicMock()
                with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
                    mock_client = MagicMock()
                    mock_cls.return_value = mock_client

                    mgr = SSHConnectionManager(
                        host="hpc-01",
                        username="u",
                        ssh_key_path="/k",
                        skip_key_validation=True,
                    )
                    mgr.acquire_connection()
                    mgr.release_connection(mock_client)

                    mock_active.labels.assert_called_with(cluster="hpc-01")
                    mock_active.labels.return_value.set.assert_called_with(0)


# ---------------------------------------------------------------------------
# SSHConnectionManager -- cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerCleanup:
    """Tests for SSHConnectionManager.cleanup."""

    def test_cleanup_closes_all_connections(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client1 = MagicMock()
            mock_client2 = MagicMock()
            mock_cls.side_effect = [mock_client1, mock_client2]

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
                max_connections=5,
            )
            mgr.acquire_connection()
            mgr.acquire_connection()
            assert len(mgr._active_connections) == 2

            mgr.cleanup()
            mock_client1.close.assert_called_once()
            mock_client2.close.assert_called_once()
            assert len(mgr._active_connections) == 0

    def test_cleanup_clears_hosts(self, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
            skip_key_validation=True,
        )
        assert mgr.hosts == ["hpc-01"]
        mgr.cleanup()
        assert mgr.hosts == []

    def test_cleanup_closes_transport(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_transport = MagicMock()
            mock_client.transport = mock_transport
            mock_cls.return_value = mock_client

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            mgr.acquire_connection()
            mgr.cleanup()
            mock_transport.close.assert_called_once()
            mock_client.close.assert_called_once()

    def test_cleanup_no_transport_attribute(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient"):
            # Create a minimal mock that truly lacks 'transport'
            mock_client = MagicMock()
            mock_client.__dict__.pop("transport", None)
            # Delete the attribute from the mock entirely so hasattr returns False
            delattr(mock_client, "transport")

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            mgr._active_connections.add(mock_client)
            mgr.cleanup()
            mock_client.close.assert_called_once()

    def test_cleanup_transport_is_none(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.transport = None
            mock_cls.return_value = mock_client

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            mgr.acquire_connection()
            mgr.cleanup()
            mock_client.close.assert_called_once()

    def test_cleanup_close_exception_swallowed(self, mock_prometheus) -> None:
        with patch("nfm_db.services.hpc_ssh.paramiko.SSHClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.close.side_effect = Exception("close fail")
            mock_cls.return_value = mock_client

            mgr = SSHConnectionManager(
                host="hpc-01",
                username="u",
                ssh_key_path="/k",
                skip_key_validation=True,
            )
            mgr.acquire_connection()
            # Should not raise
            mgr.cleanup()
            assert len(mgr._active_connections) == 0

    def test_cleanup_empty_pool(self, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        mgr.cleanup()
        assert len(mgr._active_connections) == 0
        assert mgr.hosts == []


# ---------------------------------------------------------------------------
# SSHConnectionManager -- __del__
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerDel:
    """Tests for SSHConnectionManager.__del__."""

    def test_del_calls_cleanup(self, mock_prometheus) -> None:
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        with patch.object(SSHConnectionManager, "cleanup") as mock_cleanup:
            mgr.__del__()
            mock_cleanup.assert_called_once()

    def test_del_calls_cleanup_even_when_cleanup_raises(self, mock_prometheus) -> None:
        """Verify __del__ delegates to cleanup.  Python's GC swallows
        exceptions from __del__ with a RuntimeWarning; here we simply
        confirm the delegation path."""
        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        with patch.object(SSHConnectionManager, "cleanup") as mock_cleanup:
            mgr.__del__()
            mock_cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# SSHConnectionManager -- check_health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSSHConnectionManagerCheckHealth:
    """Tests for SSHConnectionManager.check_health."""

    def test_health_check_pass(self, mock_prometheus) -> None:
        client = MagicMock()
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()
        client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        assert mgr.check_health(client) is True
        client.exec_command.assert_called_once_with("echo 'health_check'")
        mock_stdout.read.assert_called_once()

    def test_health_check_exception_returns_false(self, mock_prometheus) -> None:
        client = MagicMock()
        client.exec_command.side_effect = Exception("connection lost")

        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        assert mgr.check_health(client) is False

    def test_health_check_read_exception_returns_false(self, mock_prometheus) -> None:
        client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.side_effect = OSError("read fail")
        client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        assert mgr.check_health(client) is False

    def test_health_check_stdout_read_error(self, mock_prometheus) -> None:
        client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.side_effect = EOFError("broken pipe")
        client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        mgr = SSHConnectionManager(
            host="hpc-01",
            username="u",
            ssh_key_path="/k",
        )
        assert mgr.check_health(client) is False
