"""
Unit tests for HPC Adapter Module

Tests SSH connection management, SLURM job operations, and file transfer
using mocks to avoid actual HPC cluster connections.
"""

import os
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime
import pytest

from nfm_md_runner.hpc_adapter import (
    SSHConnectionManager,
    SLURMJobManager,
    HPCFileTransfer,
    HPCAdapter,
    JobStatus,
    ClusterType,
    HPCJob,
    ClusterConfig
)


@pytest.fixture
def mock_ssh_client():
    """Mock SSH client"""
    client = MagicMock()
    transport = MagicMock()
    transport.is_active.return_value = True
    transport.send_ignore.return_value = None
    client.get_transport.return_value = transport
    return client


@pytest.fixture
def mock_cluster_config():
    """Mock cluster configuration"""
    return ClusterConfig(
        name=ClusterType.GUANGZHOU,
        host="test.example.com",
        port=22,
        username="testuser",
        ssh_key_path=Path("/tmp/test_key"),
        work_dir=Path("/scratch/test"),
        is_primary=True
    )

@pytest.fixture
def mock_ssh_key_exists():
    """Mock SSH key file exists"""
    with patch('pathlib.Path.exists') as mock_exists:
        mock_exists.return_value = True
        yield mock_exists


class TestSSHConnectionManager:
    """Test SSH connection manager"""
    
    def test_connection_manager_init(self):
        """Test connection manager initialization"""
        manager = SSHConnectionManager(max_connections=2)
        assert manager.max_connections == 2
        assert manager._connections == {}
        assert manager._connection_last_used == {}
    
    @patch('nfm_md_runner.hpc_adapter.SSHClient')
    @patch('pathlib.Path.exists')
    @patch('pathlib.Path.stat')
    def test_create_connection_success(self, mock_stat, mock_exists, mock_ssh_class, mock_cluster_config, mock_ssh_client):
        """Test successful SSH connection creation"""
        mock_exists.return_value = True
        mock_stat_mode = MagicMock()
        mock_stat_mode.st_mode = 0o600
        mock_stat.return_value = mock_stat_mode
        mock_ssh_class.return_value = mock_ssh_client
        
        manager = SSHConnectionManager()
        conn = manager._create_connection(mock_cluster_config)
        
        assert conn == mock_ssh_client
        mock_ssh_client.connect.assert_called_once()
        mock_ssh_client.set_missing_host_key_policy.assert_called_once()
    
    @patch('nfm_md_runner.hpc_adapter.SSHClient')
    @patch('pathlib.Path.exists')
    @patch('pathlib.Path.stat')
    def test_get_connection_reuses_existing(self, mock_stat, mock_exists, mock_ssh_class, mock_cluster_config, mock_ssh_client):
        """Test that get_connection reuses existing connections"""
        mock_exists.return_value = True
        mock_stat_mode = MagicMock()
        mock_stat_mode.st_mode = 0o600
        mock_stat.return_value = mock_stat_mode
        mock_ssh_class.return_value = mock_ssh_client
        
        manager = SSHConnectionManager(max_connections=3)
        
        # First call creates connection
        conn1 = manager.get_connection(mock_cluster_config)
        
        # Second call should reuse
        conn2 = manager.get_connection(mock_cluster_config)
        
        assert conn1 == conn2
        assert mock_ssh_class.call_count == 1  # Only created once
    
    @patch('nfm_md_runner.hpc_adapter.SSHClient')
    @patch('pathlib.Path.exists')
    @patch('pathlib.Path.stat')
    def test_get_connection_recreates_stale(self, mock_stat, mock_exists, mock_ssh_class, mock_cluster_config, mock_ssh_client):
        """Test that stale connections are recreated"""
        mock_exists.return_value = True
        mock_stat_mode = MagicMock()
        mock_stat_mode.st_mode = 0o600
        mock_stat.return_value = mock_stat_mode
        mock_ssh_class.return_value = mock_ssh_client
        
        # Make connection appear stale
        mock_ssh_client.get_transport.return_value.is_active.return_value = False
        
        manager = SSHConnectionManager()
        
        # First connection
        conn1 = manager.get_connection(mock_cluster_config)
        
        # Second connection should detect stale and recreate
        conn2 = manager.get_connection(mock_cluster_config)
        
        # Both should be mocks, but connect should be called twice
        assert mock_ssh_client.connect.call_count == 2
    
    def test_close_all_connections(self, mock_ssh_client):
        """Test closing all connections"""
        manager = SSHConnectionManager()
        manager._connections = {
            ClusterType.GUANGZHOU: [mock_ssh_client]
        }
        
        manager.close_all()
        
        mock_ssh_client.close.assert_called_once()
        assert manager._connections == {}


class TestSLURMJobManager:
    """Test SLURM job manager"""
    
    @pytest.fixture
    def job_manager(self, mock_ssh_client):
        """Create job manager with mocked connection manager"""
        conn_manager = MagicMock()
        conn_manager.get_connection.return_value = mock_ssh_client
        return SLURMJobManager(conn_manager)
    
    def test_submit_job_success(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test successful job submission"""
        mock_ssh_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        # Mock output: "Submitted batch job 12345"
        mock_ssh_client.exec_command.return_value[1].read.return_value.decode.return_value = \
            "Submitted batch job 12345"
        
        script_content = "#!/bin/bash\necho 'test'"
        
        job_id = job_manager.submit_job(mock_cluster_config, script_content)
        
        assert job_id == "12345"
        assert mock_ssh_client.exec_command.call_count >= 2  # mkdir + sbatch
    
    def test_submit_job_parse_error(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test job submission with parse error"""
        mock_ssh_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        mock_ssh_client.exec_command.return_value[1].read.return_value.decode.return_value = \
            "Invalid output"
        
        with pytest.raises(ValueError, match="Failed to parse job ID"):
            job_manager.submit_job(mock_cluster_config, "#!/bin/bash\necho 'test'")
    
    def test_get_job_status_running(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test getting status for running job"""
        mock_ssh_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        mock_ssh_client.exec_command.return_value[1].read.return_value.decode.return_value = \
            "R|compute||32:00"
        
        job = job_manager.get_job_status(mock_cluster_config, "12345")
        
        assert job.job_id == "12345"
        assert job.cluster == ClusterType.GUANGZHOU
        assert job.status == JobStatus.RUNNING
    
    def test_get_job_status_completed(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test getting status for completed job"""
        # squeue returns empty (job not in queue)
        squeue_result = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        squeue_result[1].read.return_value.decode.return_value = ""
        
        # sacct returns space-delimited output (actual SLURM format)
        sacct_result = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        sacct_result[1].read.return_value.decode.return_value = \
            "COMPLETED compute 01:23:45 32 0:0"
        
        mock_ssh_client.exec_command.side_effect = [squeue_result, sacct_result]
        
        job = job_manager.get_job_status(mock_cluster_config, "12345")
        
        assert job.status == JobStatus.COMPLETED
    
    def test_cancel_job(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test cancelling a job"""
        mock_ssh_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        
        result = job_manager.cancel_job(mock_cluster_config, "12345")
        
        assert result is True
        mock_ssh_client.exec_command.assert_called_with("scancel 12345")


class TestHPCFileTransfer:
    """Test HPC file transfer"""
    
    @pytest.fixture
    def file_transfer(self, mock_ssh_client):
        """Create file transfer manager with mocked connection manager"""
        conn_manager = MagicMock()
        conn_manager.get_connection.return_value = mock_ssh_client
        return HPCFileTransfer(conn_manager)
    
    @patch('pathlib.Path.exists')
    def test_upload_file_success(self, mock_exists, file_transfer, mock_cluster_config, mock_ssh_client):
        """Test successful file upload"""
        mock_exists.return_value = True
        mock_sftp = MagicMock()
        mock_sftp.__enter__ = MagicMock(return_value=mock_sftp)
        mock_sftp.__exit__ = MagicMock(return_value=False)
        mock_ssh_client.open_sftp.return_value = mock_sftp
        
        local_file = Path("/tmp/test.txt")
        
        result = file_transfer.upload_file(
            mock_cluster_config,
            local_file,
            Path("/remote/test.txt")
        )
        
        assert result is True
        mock_sftp.put.assert_called_once()
    
    def test_upload_file_not_found(self, file_transfer, mock_cluster_config):
        """Test uploading non-existent file"""
        with pytest.raises(FileNotFoundError):
            file_transfer.upload_file(
                mock_cluster_config,
                Path("/nonexistent/file.txt"),
                Path("/remote/test.txt")
            )
    
    @patch('pathlib.Path.exists')
    def test_download_file_success(self, mock_exists, file_transfer, mock_cluster_config, mock_ssh_client):
        """Test successful file download"""
        mock_exists.return_value = False  # Local file doesn't exist
        mock_sftp = MagicMock()
        mock_sftp.__enter__ = MagicMock(return_value=mock_sftp)
        mock_sftp.__exit__ = MagicMock(return_value=False)
        mock_ssh_client.open_sftp.return_value = mock_sftp
        
        result = file_transfer.download_file(
            mock_cluster_config,
            Path("/remote/test.txt"),
            Path("/tmp/test.txt")
        )
        
        assert result is True
        mock_sftp.get.assert_called_once()


class TestHPCAdapter:
    """Test main HPC adapter"""
    
    @pytest.fixture
    def adapter(self):
        """Create HPC adapter with mocked settings"""
        with patch('nfm_md_runner.hpc_adapter.settings') as mock_settings:
            mock_settings.hpc_host = "test.example.com"
            mock_settings.hpc_port = 22
            mock_settings.hpc_user = "testuser"
            mock_settings.hpc_ssh_key_path = Path("/tmp/test_key")
            mock_settings.hpc_work_dir = Path("/scratch/test")
            mock_settings.lammps_modules = "module load lammps"
            mock_settings.ovito_enabled = False
            mock_settings.slurm_partition = "compute"
            mock_settings.slurm_nodes = 1
            mock_settings.slurm_ntasks_per_node = 32
            
            return HPCAdapter()
    
    def test_adapter_init(self, adapter):
        """Test adapter initialization"""
        assert adapter.conn_manager is not None
        assert adapter.job_manager is not None
        assert adapter.file_transfer is not None
        assert len(adapter._clusters) == 1
    
    def test_generate_slurm_script(self, adapter):
        """Test SLURM script generation"""
        config = {
            'partition': 'compute',
            'nodes': 2,
            'ntasks_per_node': 64,
            'walltime': '48:00:00'
        }
        
        script = adapter._generate_slurm_script(config)
        
        assert '#!/bin/bash' in script
        assert '#SBATCH --partition=compute' in script
        assert '#SBATCH --nodes=2' in script
        assert '#SBATCH --ntasks-per-node=64' in script
        assert '#SBATCH --time=48:00:00' in script
        assert 'lmp -in input.in' in script
    
    def test_submit_lammps_job(self, adapter, mock_cluster_config):
        """Test submitting LAMMPS job"""
        with patch.object(adapter, '_select_cluster', return_value=mock_cluster_config):
            with patch.object(adapter.file_transfer, 'upload_file'):
                with patch.object(adapter.job_manager, 'submit_job', return_value='12345'):
                    
                    potential_file = Path("/tmp/potential.file")
                    structure_file = Path("/tmp/structure.file")
                    potential_file.write_text("test")
                    structure_file.write_text("test")
                    
                    job_id = adapter.submit_lammps_job(
                        potential_file,
                        structure_file,
                        {'partition': 'compute'}
                    )
                    
                    assert job_id == '12345'


class TestSLURMJobManagerAdditional:
    """Additional SLURM job manager tests for coverage"""

    @pytest.fixture
    def job_manager(self, mock_ssh_client):
        conn_manager = MagicMock()
        conn_manager.get_connection.return_value = mock_ssh_client
        return SLURMJobManager(conn_manager)

    def test_get_job_status_unknown(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test getting status when both squeue and sacct fail"""
        squeue_result = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        squeue_result[1].read.return_value.decode.return_value = ""

        sacct_result = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        sacct_result[1].read.return_value.decode.return_value = ""

        mock_ssh_client.exec_command.side_effect = [squeue_result, sacct_result]

        job = job_manager.get_job_status(mock_cluster_config, "99999")

        assert job.job_id == "99999"
        assert job.status == JobStatus.UNKNOWN

    def test_get_job_status_pending(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test getting status for pending job"""
        mock_ssh_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=0))),
            MagicMock()
        )
        mock_ssh_client.exec_command.return_value[1].read.return_value.decode.return_value = \
            "PD|compute||32:00"

        job = job_manager.get_job_status(mock_cluster_config, "12345")

        assert job.status == JobStatus.PENDING
        assert job.partition == "compute"

    def test_submit_job_no_work_dir(self, job_manager):
        """Test submit job with no work directory configured"""
        no_work_dir_config = ClusterConfig(
            name=ClusterType.GUANGZHOU,
            host="test.example.com",
            work_dir=None,
        )

        with pytest.raises(ValueError, match="HPC work directory not configured"):
            job_manager.submit_job(no_work_dir_config, "#!/bin/bash\necho test")

    def test_cancel_job_failure(self, job_manager, mock_cluster_config, mock_ssh_client):
        """Test cancelling a job that fails"""
        mock_ssh_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(channel=MagicMock(recv_exit_status=MagicMock(return_value=1))),
            MagicMock(read=MagicMock(decode=MagicMock(return_value="error")))
        )

        result = job_manager.cancel_job(mock_cluster_config, "12345")
        assert result is False

    def test_parse_sacct_empty_output(self):
        """Test sacct output parsing with empty output"""
        conn_manager = MagicMock()
        jm = SLURMJobManager(conn_manager)

        result = jm._parse_sacct_output("12345", ClusterType.GUANGZHOU, "")
        assert result.status == JobStatus.UNKNOWN

    def test_parse_squeue_empty_output(self):
        """Test squeue output parsing with empty output"""
        conn_manager = MagicMock()
        jm = SLURMJobManager(conn_manager)

        result = jm._parse_squeue_output("12345", ClusterType.GUANGZHOU, "")
        assert result.status == JobStatus.UNKNOWN


class TestHPCFileTransferAdditional:
    """Additional file transfer tests for coverage"""

    @pytest.fixture
    def file_transfer(self, mock_ssh_client):
        conn_manager = MagicMock()
        conn_manager.get_connection.return_value = mock_ssh_client
        return HPCFileTransfer(conn_manager)

    def test_download_file_exists_no_overwrite(self, file_transfer, mock_cluster_config, tmp_path):
        """Test downloading when local file exists and overwrite=False"""
        local_file = tmp_path / "existing.txt"
        local_file.write_text("already exists")

        with pytest.raises(FileExistsError, match="Local file exists"):
            file_transfer.download_file(
                mock_cluster_config,
                Path("/remote/test.txt"),
                local_file,
                overwrite=False,
            )

    @patch('pathlib.Path.exists')
    def test_upload_file_exception(self, mock_exists, file_transfer, mock_cluster_config, mock_ssh_client):
        """Test upload file when SFTP raises exception"""
        mock_exists.return_value = True
        mock_ssh_client.open_sftp.side_effect = RuntimeError("SFTP error")

        with pytest.raises(RuntimeError, match="SFTP error"):
            file_transfer.upload_file(
                mock_cluster_config,
                Path("/tmp/test.txt"),
                Path("/remote/test.txt"),
            )

    def test_list_directory(self, file_transfer, mock_cluster_config, mock_ssh_client):
        """Test listing remote directory"""
        mock_sftp = MagicMock()
        mock_sftp.__enter__ = MagicMock(return_value=mock_sftp)
        mock_sftp.__exit__ = MagicMock(return_value=False)
        mock_sftp.listdir.return_value = ["file1.txt", "file2.log"]
        mock_ssh_client.open_sftp.return_value = mock_sftp

        result = file_transfer.list_directory(
            mock_cluster_config,
            Path("/remote/dir"),
        )

        assert result == ["file1.txt", "file2.log"]

    def test_list_directory_error(self, file_transfer, mock_cluster_config, mock_ssh_client):
        """Test listing directory with error"""
        mock_ssh_client.open_sftp.side_effect = RuntimeError("Connection lost")

        with pytest.raises(RuntimeError, match="Connection lost"):
            file_transfer.list_directory(
                mock_cluster_config,
                Path("/remote/dir"),
            )


class TestHPCAdapterAdditional:
    """Additional adapter tests for coverage"""

    @pytest.fixture
    def adapter(self):
        with patch('nfm_md_runner.hpc_adapter.settings') as mock_settings:
            mock_settings.hpc_host = "test.example.com"
            mock_settings.hpc_port = 22
            mock_settings.hpc_user = "testuser"
            mock_settings.hpc_ssh_key_path = Path("/tmp/test_key")
            mock_settings.hpc_work_dir = Path("/scratch/test")
            mock_settings.lammps_modules = "module load lammps"
            mock_settings.ovito_enabled = False
            mock_settings.slurm_partition = "compute"
            mock_settings.slurm_nodes = 1
            mock_settings.slurm_ntasks_per_node = 32
            return HPCAdapter()

    def test_select_cluster_no_clusters(self, adapter):
        """Test _select_cluster raises when no clusters configured"""
        adapter._clusters = []
        with pytest.raises(ValueError, match="No HPC clusters configured"):
            adapter._select_cluster(None)

    def test_get_cluster_config_not_found(self, adapter):
        """Test _get_cluster_config raises for unknown cluster"""
        with pytest.raises(ValueError, match=r"Cluster not configured: .*TIANJIN"):
            adapter._get_cluster_config(ClusterType.TIANJIN)

    def test_monitor_job(self, adapter, mock_cluster_config):
        """Test monitor_job delegates to job_manager"""
        mock_job = HPCJob(
            job_id="42", cluster=ClusterType.GUANGZHOU, status=JobStatus.RUNNING
        )
        with patch.object(adapter, '_get_cluster_config', return_value=mock_cluster_config):
            with patch.object(adapter.job_manager, 'get_job_status', return_value=mock_job):
                result = adapter.monitor_job("42", ClusterType.GUANGZHOU)
                assert result.status == JobStatus.RUNNING

    def test_download_results(self, adapter, mock_cluster_config, tmp_path):
        """Test download_results fetches files from cluster"""
        with patch.object(adapter, '_get_cluster_config', return_value=mock_cluster_config):
            with patch.object(
                adapter.file_transfer, 'list_directory',
                return_value=["output.log", "trajectory.dump"],
            ):
                with patch.object(adapter.file_transfer, 'download_file'):
                    result = adapter.download_results("42", ClusterType.GUANGZHOU, tmp_path)

                    assert len(result) == 2

    def test_adapter_context_manager(self, adapter):
        """Test adapter works as context manager"""
        with patch.object(adapter.conn_manager, 'close_all') as mock_close:
            with adapter:
                pass
            mock_close.assert_called_once()

    def test_connection_manager_context_manager(self):
        """Test SSHConnectionManager context manager"""
        manager = SSHConnectionManager()
        with patch.object(manager, 'close_all') as mock_close:
            with manager:
                pass
            mock_close.assert_called_once()

    def test_create_connection_key_not_found(self):
        """Test connection fails when SSH key doesn't exist"""
        with patch('nfm_md_runner.hpc_adapter.SSHClient') as mock_ssh_class:
            mock_client = MagicMock()
            mock_ssh_class.return_value = mock_client

            with patch('pathlib.Path.exists', return_value=False):
                with pytest.raises(ConnectionError, match="SSH key not found"):
                    manager = SSHConnectionManager()
                    config = ClusterConfig(
                        name=ClusterType.GUANGZHOU,
                        host="test.example.com",
                        username="testuser",
                        ssh_key_path=Path("/nonexistent/key"),
                    )
                    manager._create_connection(config)

    def test_create_connection_connect_failure(self):
        """Test connection handles SSH connect failure"""
        with patch('nfm_md_runner.hpc_adapter.SSHClient') as mock_ssh_class:
            mock_client = MagicMock()
            mock_client.connect.side_effect = Exception("Connection refused")
            mock_ssh_class.return_value = mock_client

            with patch('pathlib.Path.exists', return_value=True):
                with patch('pathlib.Path.stat') as mock_stat:
                    mock_stat_mode = MagicMock()
                    mock_stat_mode.st_mode = 0o600
                    mock_stat.return_value = mock_stat_mode

                    with pytest.raises(ConnectionError, match="Failed to connect"):
                        manager = SSHConnectionManager()
                        config = ClusterConfig(
                            name=ClusterType.GUANGZHOU,
                            host="test.example.com",
                            username="testuser",
                            ssh_key_path=Path("/tmp/key"),
                        )
                        manager._create_connection(config)


@pytest.mark.integration
class TestHPCAdapterIntegration:
    """
    Integration tests (require actual HPC access)
    
    These tests are skipped by default. Run with:
        pytest tests/test_hpc_adapter.py -v -m integration
    """
    
    @pytest.fixture
    def real_cluster_config(self):
        """Real cluster configuration from environment"""
        return ClusterConfig(
            name=ClusterType.GUANGZHOU,
            host=os.environ.get('NFM_HPC_HOST', 'xylogin1.gznet.ac.cn'),
            username=os.environ.get('NFM_HPC_USER'),
            ssh_key_path=Path(os.environ.get('NFM_HPC_SSH_KEY_PATH', '')),
            work_dir=Path(os.environ.get('NFM_HPC_WORK_DIR', '/scratch/test')),
            is_primary=True
        )
    
    @pytest.mark.skipif(
        not os.environ.get('NFM_HPC_HOST'),
        reason="Requires NFM_HPC_HOST environment variable"
    )
    def test_real_ssh_connection(self, real_cluster_config):
        """Test real SSH connection to HPC cluster"""
        manager = SSHConnectionManager()
        
        with manager.get_connection(real_cluster_config) as conn:
            assert conn is not None
            transport = conn.get_transport()
            assert transport.is_active()
    
    @pytest.mark.skipif(
        not os.environ.get('NFM_HPC_HOST'),
        reason="Requires NFM_HPC_HOST environment variable"
    )
    def test_real_slurm_version_check(self, real_cluster_config):
        """Test SLURM version query on real cluster"""
        manager = SSHConnectionManager()
        conn = manager.get_connection(real_cluster_config)
        
        stdin, stdout, stderr = conn.exec_command("squeue --version")
        version = stdout.read().decode('utf-8').strip()
        
        assert 'slurm' in version.lower()
        print(f"SLURM version: {version}")
