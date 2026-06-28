"""HPC SLURM Job Submission.

Handles SLURM script generation, parameter validation, job submission
via SSH/SFTP, and HPC job database record creation.
"""

import contextlib
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from nfm_db.services.hpc_metrics import PROMETHEUS_AVAILABLE, hpc_job_submissions
from nfm_db.services.hpc_ssh import JobSubmissionError

logger = logging.getLogger(__name__)

# Characters/patterns that enable shell injection
_SHELL_DANGEROUS_PATTERN = re.compile(r"[;&\n\0`$|]")


def validate_shell_safe(value: str, field_name: str) -> str:
    """Validate that a value is safe for shell script interpolation.

    Rejects values containing shell metacharacters that could enable
    command injection: semicolons, newlines, null bytes, backticks,
    dollar signs (for $() and $var), and pipes.

    Args:
        value: The string to validate.
        field_name: Human-readable field name for error messages.

    Returns:
        The validated string (unchanged).

    Raises:
        ValueError: If the value contains shell-unsafe characters.
    """
    if _SHELL_DANGEROUS_PATTERN.search(value):
        raise ValueError(
            f"Unsafe shell character in {field_name}: "
            f"only alphanumeric, dashes, underscores, dots, slashes, "
            f"and percent signs are permitted"
        )
    return value


def generate_slurm_script(params: dict[str, Any]) -> str:
    """Generate SLURM batch script from parameters.

    Args:
        params: Dictionary containing job parameters

    Returns:
        Complete SLURM batch script content

    Raises:
        ValueError: If any user-controlled string parameter contains
            shell-unsafe characters.
    """
    job_name = validate_shell_safe(
        str(params.get("job_name", "md_verification")), "job_name"
    )
    nodes = params.get("nodes", 1)
    cpus_per_task = params.get("cpus_per_task", 4)
    memory = params.get("memory", "16G")
    walltime = params.get("walltime", "02:00:00")
    partition = validate_shell_safe(
        str(params.get("partition", "compute")), "partition"
    )
    output_file = validate_shell_safe(
        str(params.get("output_file", "lammps.out")), "output_file"
    )

    script = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --nodes={nodes}
#SBATCH --cpus-per-task={cpus_per_task}
#SBATCH --mem={memory}
#SBATCH --time={walltime}
#SBATCH --partition={partition}
#SBATCH --output={output_file}

echo "Starting MD verification job at $(date)"
echo "Job ID: $SLURM_JOB_ID"

# Load modules if needed
# module load lammps

# Run LAMMPS simulation
"""

    if "lammps_executable" in params:
        lammps_exec = validate_shell_safe(
            str(params["lammps_executable"]), "lammps_executable"
        )
        input_file = validate_shell_safe(
            str(params.get("input_file", "in.lammps")), "input_file"
        )
        script += f"""
# Run LAMMPS with MPI
mpirun {lammps_exec} -in {input_file}
"""

    script += """
echo "Job completed at $(date)"
"""
    return script


def validate_simulation_params(params: dict[str, Any]) -> None:
    """Validate simulation parameters.

    Args:
        params: Simulation parameters to validate

    Raises:
        ValueError: If required parameters are missing or invalid
    """
    required_params = ["temperature", "pressure", "steps"]
    missing_params = [p for p in required_params if p not in params]

    if missing_params:
        raise ValueError(f"Missing required parameters: {missing_params}")

    if not (0 < params["temperature"] < 10000):
        raise ValueError("Temperature must be between 0 and 10000 K")

    if not (0 < params["pressure"] < 1000):
        raise ValueError("Pressure must be between 0 and 1000 GPa")

    if not (1000 < params["steps"] < 10000000):
        raise ValueError("Steps must be between 1000 and 10 million")


def parse_walltime(walltime: str) -> int:
    """Parse SLURM walltime format to minutes.

    Args:
        walltime: Walltime in format "HH:MM:SS"

    Returns:
        Walltime in minutes
    """
    parts = walltime.split(":")
    hours = int(parts[0])
    minutes = int(parts[1]) if len(parts) > 1 else 0
    return hours * 60 + minutes


def upload_script_via_sftp(
    client,
    script_content: str,
    remote_path: str,
) -> None:
    """Upload script content to remote path via SFTP.

    Args:
        client: SSH client with active connection
        script_content: Script file content
        remote_path: Remote file path
    """
    sftp = None
    try:
        sftp = client.open_sftp()
        remote_dir = "/".join(remote_path.split("/")[:-1])
        try:
            sftp.mkdir(remote_dir)
        except OSError:
            pass  # Directory may already exist

        with sftp.file(remote_path, 'w') as f:
            f.write(script_content)
    finally:
        if sftp:
            sftp.close()


async def submit_to_slurm(
    cluster_manager,
    cluster_name: str,
    task_id: str,
    slurm_script: str,
) -> str:
    """Submit job to SLURM cluster via SSH.

    Args:
        cluster_manager: SSHConnectionManager for the target cluster
        cluster_name: Cluster hostname (for logging/metrics)
        task_id: Task identifier for logging
        slurm_script: Complete SLURM script content

    Returns:
        SLURM job ID (e.g., "slurm-12345")

    Raises:
        JobSubmissionError: If submission fails
    """
    client = None
    try:
        client = cluster_manager.acquire_connection()

        script_path = f"$SCRATCH/nfm-md/{task_id}/submit.sh"
        upload_script_via_sftp(client, slurm_script, script_path)

        _stdin, stdout, stderr = client.exec_command(f"sbatch {script_path}")
        exit_status = stdout.channel.recv_exit_status()

        if exit_status != 0:
            error_msg = stderr.read().decode()
            if "Socket timed out" in error_msg or "qos: QOSMaxSubmitJobLimit" in error_msg:
                if PROMETHEUS_AVAILABLE:
                    hpc_job_submissions.labels(cluster=cluster_name, status='queue_full').inc()
                raise JobSubmissionError(f"SLURM queue is full: {error_msg}")
            elif "Permission denied" in error_msg:
                if PROMETHEUS_AVAILABLE:
                    hpc_job_submissions.labels(cluster=cluster_name, status='permission_denied').inc()
                raise JobSubmissionError(f"Permission denied: {error_msg}")
            else:
                if PROMETHEUS_AVAILABLE:
                    hpc_job_submissions.labels(cluster=cluster_name, status='failed').inc()
                raise JobSubmissionError(f"SLURM submission failed: {error_msg}")

        job_id = stdout.read().decode().strip()
        if not job_id.isdigit():
            if PROMETHEUS_AVAILABLE:
                hpc_job_submissions.labels(cluster=cluster_name, status='invalid_response').inc()
            raise JobSubmissionError(f"Invalid job ID returned: {job_id}")

        logger.info(f"Job submitted successfully to {cluster_name}: {job_id}")

        if PROMETHEUS_AVAILABLE:
            hpc_job_submissions.labels(cluster=cluster_name, status='success').inc()

        return f"slurm-{job_id}"

    except JobSubmissionError:
        raise
    except Exception as e:
        logger.error(f"Failed to submit job to {cluster_name}: {e}")
        if PROMETHEUS_AVAILABLE:
            hpc_job_submissions.labels(cluster=cluster_name, status='error').inc()
        raise JobSubmissionError(f"HPC connection failed: {e}")
    finally:
        if client:
            cluster_manager.release_connection(client)


async def create_hpc_job_record(
    task_id: str,
    hpc_job_id: str,
    params: dict[str, Any],
    hpc_cluster: str,
) -> None:
    """Create record in hpc_jobs table.

    Args:
        task_id: MD verification job ID
        hpc_job_id: SLURM job identifier
        params: Job parameters
        hpc_cluster: Which cluster hostname was used
    """
    from nfm_db.database import get_db
    from nfm_db.models.md_verification import HpcJob, HpcJobStatus

    db_gen = get_db()
    db = await db_gen.__anext__()

    try:
        hpc_job = HpcJob(
            verification_job_id=uuid.UUID(task_id),
            hpc_cluster=hpc_cluster,
            hpc_job_id=hpc_job_id,
            status=HpcJobStatus.PENDING,
            partition=params.get("partition", "compute"),
            nodes=params.get("nodes", 1),
            walltime_requested=parse_walltime(params.get("walltime", "02:00:00")),
            submitted_at=datetime.utcnow(),
        )

        db.add(hpc_job)
        await db.commit()

        logger.info(f"Created HPC job record: {hpc_job.id} on cluster {hpc_cluster}")
    except Exception:
        await db.rollback()
        raise
    finally:
        with contextlib.suppress(StopAsyncIteration):
            await db_gen.__anext__()
