"""Comprehensive tests for nfm_db.services.hpc_slurm.

Covers all public functions:
- generate_slurm_script
- validate_simulation_params
- parse_walltime
- upload_script_via_sftp
- submit_to_slurm
- create_hpc_job_record
"""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from nfm_db.services.hpc_slurm import (
    generate_slurm_script,
    validate_simulation_params,
    parse_walltime,
    upload_script_via_sftp,
    submit_to_slurm,
    create_hpc_job_record,
)
from nfm_db.services.hpc_ssh import JobSubmissionError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_params() -> dict:
    """Minimal valid SLURM script parameters."""
    return {
        "job_name": "md_verification",
        "nodes": 1,
        "cpus_per_task": 4,
        "memory": "16G",
        "walltime": "02:00:00",
        "partition": "compute",
        "output_file": "lammps.out",
    }


@pytest.fixture
def valid_sim_params() -> dict:
    """Valid simulation parameters for validate_simulation_params."""
    return {
        "temperature": 300,
        "pressure": 1.0,
        "steps": 10000,
    }


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """Async mock database session with commit/rollback/add."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# generate_slurm_script
# ---------------------------------------------------------------------------


class TestGenerateSlurmScript:
    """Tests for generate_slurm_script."""

    @pytest.mark.unit
    def test_basic_params(self, default_params: dict) -> None:
        """Script contains all SBATCH directives from basic parameters."""
        script: str = generate_slurm_script(default_params)

        assert "#!/bin/bash" in script
        assert "#SBATCH --job-name=md_verification" in script
        assert "#SBATCH --nodes=1" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --mem=16G" in script
        assert "#SBATCH --time=02:00:00" in script
        assert "#SBATCH --partition=compute" in script
        assert "#SBATCH --output=lammps.out" in script

    @pytest.mark.unit
    def test_custom_job_name(self, default_params: dict) -> None:
        """Custom job_name appears in the SBATCH directive."""
        default_params["job_name"] = "my_custom_job"
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --job-name=my_custom_job" in script

    @pytest.mark.unit
    def test_custom_nodes(self, default_params: dict) -> None:
        """Custom nodes value is rendered."""
        default_params["nodes"] = 8
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --nodes=8" in script

    @pytest.mark.unit
    def test_custom_cpus_per_task(self, default_params: dict) -> None:
        """Custom cpus_per_task value is rendered."""
        default_params["cpus_per_task"] = 16
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --cpus-per-task=16" in script

    @pytest.mark.unit
    def test_custom_memory(self, default_params: dict) -> None:
        """Custom memory value is rendered."""
        default_params["memory"] = "64G"
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --mem=64G" in script

    @pytest.mark.unit
    def test_custom_walltime(self, default_params: dict) -> None:
        """Custom walltime value is rendered."""
        default_params["walltime"] = "08:30:00"
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --time=08:30:00" in script

    @pytest.mark.unit
    def test_custom_partition(self, default_params: dict) -> None:
        """Custom partition value is rendered."""
        default_params["partition"] = "gpu"
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --partition=gpu" in script

    @pytest.mark.unit
    def test_custom_output_file(self, default_params: dict) -> None:
        """Custom output_file value is rendered."""
        default_params["output_file"] = "results/%j.out"
        script: str = generate_slurm_script(default_params)

        assert "#SBATCH --output=results/%j.out" in script

    @pytest.mark.unit
    def test_with_lammps_executable(self, default_params: dict) -> None:
        """When lammps_executable is set, mpirun command is included."""
        default_params["lammps_executable"] = "/opt/lammps/bin/lmp_mpi"
        script: str = generate_slurm_script(default_params)

        assert "mpirun" in script
        assert "/opt/lammps/bin/lmp_mpi" in script

    @pytest.mark.unit
    def test_with_lammps_executable_and_input_file(
        self, default_params: dict
    ) -> None:
        """mpirun references the provided input_file."""
        default_params["lammps_executable"] = "/opt/lammps/bin/lmp_mpi"
        default_params["input_file"] = "in.uo2_vacancy"
        script: str = generate_slurm_script(default_params)

        assert "mpirun /opt/lammps/bin/lmp_mpi -in in.uo2_vacancy" in script

    @pytest.mark.unit
    def test_with_lammps_executable_default_input_file(
        self, default_params: dict
    ) -> None:
        """When lammps_executable is set but input_file omitted, defaults to in.lammps."""
        default_params["lammps_executable"] = "/opt/lammps/bin/lmp_mpi"
        script: str = generate_slurm_script(default_params)

        assert "in.lammps" in script

    @pytest.mark.unit
    def test_without_lammps_executable_no_mpirun(
        self, default_params: dict
    ) -> None:
        """Without lammps_executable, no mpirun command appears."""
        script: str = generate_slurm_script(default_params)

        assert "mpirun" not in script

    @pytest.mark.unit
    def test_default_values_when_empty_params(self) -> None:
        """Empty params dict uses all default values."""
        script: str = generate_slurm_script({})

        assert "#SBATCH --job-name=md_verification" in script
        assert "#SBATCH --nodes=1" in script
        assert "#SBATCH --cpus-per-task=4" in script
        assert "#SBATCH --mem=16G" in script
        assert "#SBATCH --time=02:00:00" in script
        assert "#SBATCH --partition=compute" in script
        assert "#SBATCH --output=lammps.out" in script

    @pytest.mark.unit
    def test_script_contains_echo_statements(self, default_params: dict) -> None:
        """Script always contains start/complete echo markers."""
        script: str = generate_slurm_script(default_params)

        assert "Starting MD verification job" in script
        assert "Job completed" in script


# ---------------------------------------------------------------------------
# validate_simulation_params
# ---------------------------------------------------------------------------


class TestValidateSimulationParams:
    """Tests for validate_simulation_params."""

    @pytest.mark.unit
    def test_valid_params_pass(self, valid_sim_params: dict) -> None:
        """Valid parameters raise no exception."""
        validate_simulation_params(valid_sim_params)  # should not raise

    @pytest.mark.unit
    def test_missing_temperature(self, valid_sim_params: dict) -> None:
        """Missing temperature raises ValueError."""
        params = {**valid_sim_params}
        del params["temperature"]

        with pytest.raises(ValueError, match="temperature"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_missing_pressure(self, valid_sim_params: dict) -> None:
        """Missing pressure raises ValueError."""
        params = {**valid_sim_params}
        del params["pressure"]

        with pytest.raises(ValueError, match="pressure"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_missing_steps(self, valid_sim_params: dict) -> None:
        """Missing steps raises ValueError."""
        params = {**valid_sim_params}
        del params["steps"]

        with pytest.raises(ValueError, match="steps"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_all_required_missing(self) -> None:
        """Empty dict raises ValueError listing all three required params."""
        with pytest.raises(ValueError, match="temperature.*pressure.*steps"):
            validate_simulation_params({})

    @pytest.mark.unit
    def test_temperature_too_low(self, valid_sim_params: dict) -> None:
        """Temperature at zero is rejected."""
        params = {**valid_sim_params, "temperature": 0}

        with pytest.raises(ValueError, match="Temperature"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_temperature_too_high(self, valid_sim_params: dict) -> None:
        """Temperature at 10000 is rejected (exclusive bound)."""
        params = {**valid_sim_params, "temperature": 10000}

        with pytest.raises(ValueError, match="Temperature"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_temperature_negative(self, valid_sim_params: dict) -> None:
        """Negative temperature is rejected."""
        params = {**valid_sim_params, "temperature": -50}

        with pytest.raises(ValueError, match="Temperature"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_temperature_at_boundaries(self, valid_sim_params: dict) -> None:
        """Temperature at 1 and 9999 (exclusive boundaries) passes."""
        params_low = {**valid_sim_params, "temperature": 1}
        params_high = {**valid_sim_params, "temperature": 9999}

        validate_simulation_params(params_low)
        validate_simulation_params(params_high)

    @pytest.mark.unit
    def test_pressure_too_low(self, valid_sim_params: dict) -> None:
        """Pressure at zero is rejected."""
        params = {**valid_sim_params, "pressure": 0}

        with pytest.raises(ValueError, match="Pressure"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_pressure_too_high(self, valid_sim_params: dict) -> None:
        """Pressure at 1000 is rejected (exclusive bound)."""
        params = {**valid_sim_params, "pressure": 1000}

        with pytest.raises(ValueError, match="Pressure"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_pressure_negative(self, valid_sim_params: dict) -> None:
        """Negative pressure is rejected."""
        params = {**valid_sim_params, "pressure": -1}

        with pytest.raises(ValueError, match="Pressure"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_pressure_at_boundaries(self, valid_sim_params: dict) -> None:
        """Pressure at 0.1 and 999.9 (within bounds) passes."""
        params_low = {**valid_sim_params, "pressure": 0.1}
        params_high = {**valid_sim_params, "pressure": 999.9}

        validate_simulation_params(params_low)
        validate_simulation_params(params_high)

    @pytest.mark.unit
    def test_steps_too_low(self, valid_sim_params: dict) -> None:
        """Steps at 1000 is rejected (exclusive bound)."""
        params = {**valid_sim_params, "steps": 1000}

        with pytest.raises(ValueError, match="Steps"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_steps_too_high(self, valid_sim_params: dict) -> None:
        """Steps at 10 million is rejected (exclusive bound)."""
        params = {**valid_sim_params, "steps": 10_000_000}

        with pytest.raises(ValueError, match="Steps"):
            validate_simulation_params(params)

    @pytest.mark.unit
    def test_steps_at_boundaries(self, valid_sim_params: dict) -> None:
        """Steps at 1001 and 9_999_999 (within bounds) passes."""
        params_low = {**valid_sim_params, "steps": 1001}
        params_high = {**valid_sim_params, "steps": 9_999_999}

        validate_simulation_params(params_low)
        validate_simulation_params(params_high)


# ---------------------------------------------------------------------------
# parse_walltime
# ---------------------------------------------------------------------------


class TestParseWalltime:
    """Tests for parse_walltime."""

    @pytest.mark.unit
    def test_standard_format(self) -> None:
        """Standard HH:MM:SS is parsed correctly (seconds ignored)."""
        assert parse_walltime("02:00:00") == 120

    @pytest.mark.unit
    def test_standard_format_with_minutes(self) -> None:
        """HH:MM:SS with non-zero minutes."""
        assert parse_walltime("01:30:00") == 90

    @pytest.mark.unit
    def test_short_format_hh_mm(self) -> None:
        """HH:MM format (two parts)."""
        assert parse_walltime("00:30") == 30

    @pytest.mark.unit
    def test_short_format_hours_only(self) -> None:
        """Just hours (single part)."""
        assert parse_walltime("5") == 300

    @pytest.mark.unit
    def test_zero_hours(self) -> None:
        """Zero hours returns zero minutes."""
        assert parse_walltime("0") == 0

    @pytest.mark.unit
    def test_large_hours(self) -> None:
        """Large hour values."""
        assert parse_walltime("48") == 2880

    @pytest.mark.unit
    def test_fractional_hours_raises(self) -> None:
        """Fractional hours raise ValueError."""
        with pytest.raises(ValueError):
            parse_walltime("2.5")


# ---------------------------------------------------------------------------
# upload_script_via_sftp
# ---------------------------------------------------------------------------


class TestUploadScriptViaSftp:
    """Tests for upload_script_via_sftp."""

    @pytest.mark.unit
    def test_successful_upload(self) -> None:
        """Script content is written to remote path via SFTP."""
        mock_client = MagicMock()
        mock_sftp = MagicMock()
        mock_file = MagicMock()
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.open_sftp.return_value = mock_sftp

        upload_script_via_sftp(
            mock_client, "#!/bin/bash\necho hello", "/scratch/nfm-md/task1/submit.sh"
        )

        mock_client.open_sftp.assert_called_once()
        mock_sftp.mkdir.assert_called_once_with("/scratch/nfm-md/task1")
        mock_sftp.file.assert_called_once_with(
            "/scratch/nfm-md/task1/submit.sh", "w"
        )
        mock_file.write.assert_called_once_with("#!/bin/bash\necho hello")
        mock_sftp.close.assert_called_once()

    @pytest.mark.unit
    def test_directory_creation_ignores_existing(self) -> None:
        """mkdir IOError is silently ignored when directory already exists."""
        mock_client = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.mkdir.side_effect = IOError("File exists")
        mock_file = MagicMock()
        mock_sftp.file.return_value.__enter__ = MagicMock(return_value=mock_file)
        mock_sftp.file.return_value.__exit__ = MagicMock(return_value=False)
        mock_client.open_sftp.return_value = mock_sftp

        upload_script_via_sftp(
            mock_client, "script body", "/scratch/nfm-md/task1/run.sh"
        )

        mock_sftp.mkdir.assert_called_once()
        # Should still have written the file
        mock_file.write.assert_called_once_with("script body")

    @pytest.mark.unit
    def test_sftp_open_fails_closes_gracefully(self) -> None:
        """If open_sftp raises, sftp.close is not called (sftp is None)."""
        mock_client = MagicMock()
        mock_client.open_sftp.side_effect = IOError("Connection refused")

        with pytest.raises(IOError, match="Connection refused"):
            upload_script_via_sftp(mock_client, "data", "/path/to/script.sh")

        mock_client.open_sftp.assert_called_once()

    @pytest.mark.unit
    def test_sftp_close_called_in_finally(self) -> None:
        """SFTP is closed even if write raises."""
        mock_client = MagicMock()
        mock_sftp = MagicMock()
        mock_sftp.file.side_effect = IOError("Write failed")
        mock_client.open_sftp.return_value = mock_sftp

        with pytest.raises(IOError, match="Write failed"):
            upload_script_via_sftp(mock_client, "data", "/path/to/script.sh")

        mock_sftp.close.assert_called_once()


# ---------------------------------------------------------------------------
# submit_to_slurm
# ---------------------------------------------------------------------------


class TestSubmitToSlurm:
    """Tests for submit_to_slurm (async)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_submission(self) -> None:
        """Happy path: sbatch returns numeric job ID."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 0
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b"12345\n"
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ) as mock_upload:
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", False
            ):
                result = await submit_to_slurm(
                    mock_manager, "cluster01", "task-abc", "#!/bin/bash"
                )

        assert result == "slurm-12345"
        mock_upload.assert_called_once()
        mock_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_queue_full_socket_timeout(self) -> None:
        """Socket timed out error triggers queue_full JobSubmissionError."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 1
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b""
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"Socket timed out on login01\n"
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", True
            ) as mock_prom:
                mock_prom_inc = MagicMock()
                with patch(
                    "nfm_db.services.hpc_slurm.hpc_job_submissions"
                ) as mock_metric:
                    mock_metric.labels.return_value.inc = mock_prom_inc
                    with pytest.raises(
                        JobSubmissionError, match="queue is full"
                    ):
                        await submit_to_slurm(
                            mock_manager,
                            "cluster01",
                            "task-abc",
                            "script",
                        )

        mock_metric.labels.assert_called_with(
            cluster="cluster01", status="queue_full"
        )
        mock_prom_inc.assert_called_once()
        mock_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_queue_full_qos_limit(self) -> None:
        """QOSMaxSubmitJobLimit triggers queue_full JobSubmissionError."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 1
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b""
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = (
            b"sbatch: error: qos: QOSMaxSubmitJobLimit\n"
        )
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", False
            ):
                with pytest.raises(
                    JobSubmissionError, match="queue is full"
                ):
                    await submit_to_slurm(
                        mock_manager,
                        "cluster01",
                        "task-abc",
                        "script",
                    )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_permission_denied(self) -> None:
        """Permission denied error triggers appropriate JobSubmissionError."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 1
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b""
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"sbatch: error: Permission denied\n"
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", True
            ):
                with patch(
                    "nfm_db.services.hpc_slurm.hpc_job_submissions"
                ) as mock_metric:
                    mock_prom_inc = MagicMock()
                    mock_metric.labels.return_value.inc = mock_prom_inc
                    with pytest.raises(
                        JobSubmissionError, match="Permission denied"
                    ):
                        await submit_to_slurm(
                            mock_manager,
                            "cluster01",
                            "task-abc",
                            "script",
                        )

        mock_metric.labels.assert_called_with(
            cluster="cluster01", status="permission_denied"
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_generic_slurm_failure(self) -> None:
        """Unknown sbatch error triggers generic JobSubmissionError."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 1
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b""
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"sbatch: error: Batch job submission failed\n"
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", False
            ):
                with pytest.raises(
                    JobSubmissionError, match="submission failed"
                ):
                    await submit_to_slurm(
                        mock_manager,
                        "cluster01",
                        "task-abc",
                        "script",
                    )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_job_id_response(self) -> None:
        """Non-numeric job ID triggers invalid_response JobSubmissionError."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 0
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b"Submitted batch job abc\n"
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", True
            ):
                with patch(
                    "nfm_db.services.hpc_slurm.hpc_job_submissions"
                ) as mock_metric:
                    mock_prom_inc = MagicMock()
                    mock_metric.labels.return_value.inc = mock_prom_inc
                    with pytest.raises(
                        JobSubmissionError, match="Invalid job ID"
                    ):
                        await submit_to_slurm(
                            mock_manager,
                            "cluster01",
                            "task-abc",
                            "script",
                        )

        mock_metric.labels.assert_called_with(
            cluster="cluster01", status="invalid_response"
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_error_wrapped(self) -> None:
        """Non-JobSubmissionError exceptions are wrapped as connection failure."""
        mock_manager = MagicMock()
        mock_manager.acquire_connection.side_effect = ConnectionError("SSH down")

        with patch(
            "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", True
        ):
            with patch(
                "nfm_db.services.hpc_slurm.hpc_job_submissions"
            ) as mock_metric:
                mock_prom_inc = MagicMock()
                mock_metric.labels.return_value.inc = mock_prom_inc
                with pytest.raises(
                    JobSubmissionError, match="HPC connection failed"
                ):
                    await submit_to_slurm(
                        mock_manager,
                        "cluster01",
                        "task-abc",
                        "script",
                    )

        mock_metric.labels.assert_called_with(
            cluster="cluster01", status="error"
        )
        mock_manager.release_connection.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_error_prometheus_unavailable(self) -> None:
        """When PROMETHEUS_AVAILABLE is False, no metrics inc is called."""
        mock_manager = MagicMock()
        mock_manager.acquire_connection.side_effect = RuntimeError("timeout")

        with patch(
            "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", False
        ):
            with pytest.raises(JobSubmissionError, match="HPC connection failed"):
                await submit_to_slurm(
                    mock_manager,
                    "cluster01",
                    "task-abc",
                    "script",
                )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_client_cleanup_in_finally(self) -> None:
        """Connection is released even when JobSubmissionError is raised."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 1
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b""
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b"Socket timed out\n"
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", False
            ):
                with pytest.raises(JobSubmissionError):
                    await submit_to_slurm(
                        mock_manager,
                        "cluster01",
                        "task-abc",
                        "script",
                    )

        mock_manager.release_connection.assert_called_once_with(mock_client)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_success_increments_prometheus_counter(self) -> None:
        """Successful submission increments success metric."""
        mock_manager = MagicMock()
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_exit_status.return_value = 0
        mock_stdout.channel = mock_channel
        mock_stdout.read.return_value = b"98765"
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        mock_client.exec_command.return_value = (None, mock_stdout, mock_stderr)
        mock_manager.acquire_connection.return_value = mock_client

        with patch(
            "nfm_db.services.hpc_slurm.upload_script_via_sftp"
        ):
            with patch(
                "nfm_db.services.hpc_slurm.PROMETHEUS_AVAILABLE", True
            ):
                with patch(
                    "nfm_db.services.hpc_slurm.hpc_job_submissions"
                ) as mock_metric:
                    mock_prom_inc = MagicMock()
                    mock_metric.labels.return_value.inc = mock_prom_inc

                    result = await submit_to_slurm(
                        mock_manager,
                        "gpu-cluster",
                        "task-xyz",
                        "script",
                    )

        assert result == "slurm-98765"
        mock_metric.labels.assert_called_with(
            cluster="gpu-cluster", status="success"
        )
        mock_prom_inc.assert_called_once()


# ---------------------------------------------------------------------------
# create_hpc_job_record
# ---------------------------------------------------------------------------


class TestCreateHpcJobRecord:
    """Tests for create_hpc_job_record (async)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_success(self, mock_db_session: AsyncMock) -> None:
        """Record is created with correct fields on success."""
        task_id = str(uuid.uuid4())
        params = {
            "partition": "gpu",
            "nodes": 2,
            "walltime": "04:00:00",
        }

        async def mock_db_gen():
            yield mock_db_session

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch(
                "nfm_db.models.md_verification.HpcJob", autospec=True
            ) as mock_hpc_job_cls:
                mock_hpc_job_cls.return_value = MagicMock(
                    id=uuid.uuid4()
                )
                await create_hpc_job_record(
                    task_id, "slurm-12345", params, "cluster01"
                )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_awaited_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_database_error_triggers_rollback(
        self, mock_db_session: AsyncMock
    ) -> None:
        """On exception, rollback is called and exception propagates."""
        task_id = str(uuid.uuid4())
        params: dict = {}

        async def mock_db_gen():
            yield mock_db_session

        mock_db_session.commit.side_effect = RuntimeError("DB down")

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch(
                "nfm_db.models.md_verification.HpcJob", autospec=True
            ):
                with pytest.raises(RuntimeError, match="DB down"):
                    await create_hpc_job_record(
                        task_id, "slurm-12345", params, "cluster01"
                    )

        mock_db_session.rollback.assert_awaited_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_default_partition(self, mock_db_session: AsyncMock) -> None:
        """Default partition 'compute' is used when not in params."""
        task_id = str(uuid.uuid4())
        params: dict = {}

        async def mock_db_gen():
            yield mock_db_session

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch(
                "nfm_db.models.md_verification.HpcJob", autospec=True
            ) as mock_hpc_job_cls:
                await create_hpc_job_record(
                    task_id, "slurm-99", params, "cluster01"
                )

        # HpcJob was constructed with partition="compute"
        _call = mock_hpc_job_cls.call_args
        assert _call is not None
        assert (
            _call.kwargs.get("partition") == "compute"
            or _call[1].get("partition") == "compute"  # type: ignore[index]
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_default_nodes(self, mock_db_session: AsyncMock) -> None:
        """Default nodes=1 when not in params."""
        task_id = str(uuid.uuid4())
        params: dict = {}

        async def mock_db_gen():
            yield mock_db_session

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch(
                "nfm_db.models.md_verification.HpcJob", autospec=True
            ) as mock_hpc_job_cls:
                await create_hpc_job_record(
                    task_id, "slurm-99", params, "cluster01"
                )

        _call = mock_hpc_job_cls.call_args
        assert _call is not None
        assert (
            _call.kwargs.get("nodes") == 1
            or _call[1].get("nodes") == 1  # type: ignore[index]
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_default_walltime(self, mock_db_session: AsyncMock) -> None:
        """Default walltime '02:00:00' parses to 120 minutes."""
        task_id = str(uuid.uuid4())
        params: dict = {}

        async def mock_db_gen():
            yield mock_db_session

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch(
                "nfm_db.models.md_verification.HpcJob", autospec=True
            ) as mock_hpc_job_cls:
                await create_hpc_job_record(
                    task_id, "slurm-99", params, "cluster01"
                )

        _call = mock_hpc_job_cls.call_args
        assert _call is not None
        walltime_val = (
            _call.kwargs.get("walltime_requested")
            or _call[1].get("walltime_requested")  # type: ignore[index]
        )
        assert walltime_val == 120

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_db_generator_exhausted(
        self, mock_db_session: AsyncMock
    ) -> None:
        """Second __anext__ call (cleanup) is handled gracefully."""
        task_id = str(uuid.uuid4())
        params: dict = {}

        async def mock_db_gen():
            yield mock_db_session

        with patch("nfm_db.database.get_db", return_value=mock_db_gen()):
            with patch(
                "nfm_db.models.md_verification.HpcJob", autospec=True
            ):
                await create_hpc_job_record(
                    task_id, "slurm-99", params, "cluster01"
                )

        # The generator should have been fully consumed (cleanup __anext__)
        # No assertion needed -- just ensuring no StopAsyncIteration leak
