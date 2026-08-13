"""
SLURM Executor (NFM-377).

Implements the Executor Protocol from ports.py.
Submits LAMMPS jobs to HPC clusters via SLURM (sbatch/squeue/scancel).
All SSH communication is delegated to an SSHTransport instance.
"""

from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

from nfm_md_runner.ports import (
    ExecutionError,
    JobOutput,
    JobSpec,
    JobStatus,
)

logger = logging.getLogger(__name__)

# Regex to parse "Submitted batch job <N>" from sbatch output
_SbatchJobIdRe = re.compile(r"Submitted batch job\s+(\d+)", re.IGNORECASE)

# SLURM state code to JobStatus mapping
_SLURM_STATE_MAP: dict[str, JobStatus] = {
    "PD": JobStatus.PENDING,
    "R": JobStatus.RUNNING,
    "CG": JobStatus.RUNNING,  # completing
    "CD": JobStatus.COMPLETED,
    "COMPLETED": JobStatus.COMPLETED,
    "FAILED": JobStatus.FAILED,
    "CANCELLED": JobStatus.CANCELLED,
    "CA": JobStatus.CANCELLED,
    "F": JobStatus.FAILED,
    "TIMEOUT": JobStatus.FAILED,
    "OOM": JobStatus.FAILED,
    "NF": JobStatus.FAILED,
}


@runtime_checkable
class SSHTransportProtocol(Protocol):
    """Minimal protocol for the SSH transport dependency."""

    async def exec_command(self, command: str) -> str: ...
    async def write_remote_file(self, content: str, remote_path: str) -> None: ...
    async def download_file(self, remote_path: str) -> bytes: ...
    async def close(self) -> None: ...


def _generate_sbatch_script(spec: JobSpec, work_dir: str, script_path: str) -> str:
    """Generate a SLURM batch script for a LAMMPS job.

    Args:
        spec: Job specification.
        work_dir: Remote working directory.
        script_path: Remote path where this script will be written.

    Returns:
        Complete sbatch script as a string.
    """
    partition_line = f"#SBATCH --partition={spec.partition}" if spec.partition else ""
    return f"""#!/bin/bash
#SBATCH --job-name=nfmd-md
#SBATCH --output={work_dir}/%j.out
#SBATCH --error={work_dir}/%j.err
#SBATCH --nodes={spec.nodes}
#SBATCH --time={spec.walltime}
{partition_line}
#SBATCH --chdir={work_dir}

# LAMMPS execution
lmp -in {script_path}.input
"""


def _generate_lammps_input(spec: JobSpec) -> str:
    """Generate a minimal LAMMPS input file from a JobSpec.

    Args:
        spec: Job specification.

    Returns:
        LAMMPS input script content.
    """
    return f"""# NFM-Runner generated LAMMPS input
units           metal
atom_style      atomic
dimension       3
boundary        p p p

read_data       {spec.structure_file}
pair_style      eam/alloy
pair_coeff      * * {spec.potential_file}

velocity        all create {spec.temperature} dist gaussian
thermo          100
thermo_style    custom step temp pe press

timestep        0.002
run             {spec.steps}

print           "NFM-Runner: simulation complete"
"""


def _parse_sbatch_output(stdout: str) -> str:
    """Extract the SLURM job ID from sbatch output.

    Args:
        stdout: Raw output from sbatch.

    Returns:
        The job ID string.

    Raises:
        ExecutionError: If no job ID can be parsed.
    """
    match = _SbatchJobIdRe.search(stdout)
    if not match:
        raise ExecutionError(
            f"Failed to parse SLURM job ID from output: {stdout}"
        )
    return match.group(1)


def _parse_squeue_line(line: str) -> JobStatus | None:
    """Parse a single squeue output line into a JobStatus.

    Args:
        line: Pipe-delimited squeue line: STATE|PARTITION|NODES|NODELIST|TIME

    Returns:
        JobStatus if recognized, else None.
    """
    state = line.strip().split("|")[0].strip()
    return _SLURM_STATE_MAP.get(state)


def _parse_sacct_line(line: str) -> JobStatus | None:
    """Parse a sacct output line into a JobStatus.

    Args:
        line: Space-delimited sacct line: STATE PARTITION ELAPSED ...

    Returns:
        JobStatus if recognized, else None.
    """
    state = line.strip().split()[0].strip().upper()
    return _SLURM_STATE_MAP.get(state)


class SlurmExecutor:
    """SLURM-based executor for HPC clusters.

    Implements the Executor Protocol. Delegates SSH to an injected
    transport, enabling easy testing with mocks.

    Usage::

        transport = SSHTransport(credentials=creds)
        await transport.connect()
        executor = SlurmExecutor(transport=transport, credentials=creds)
        job_id = await executor.submit(job_spec)
    """

    def __init__(
        self,
        transport: SSHTransportProtocol,
        credentials: object,
    ) -> None:
        self._transport = transport
        self._credentials = credentials

    @property
    def credentials(self) -> object:
        return self._credentials

    @property
    def _work_dir(self) -> str:
        work = getattr(self._credentials, "work_dir", None) or "/scratch/nfmd"
        return work

    async def submit(self, job: JobSpec) -> str:
        """Submit a job via sbatch.

        Creates remote directory, uploads script and LAMMPS input,
        then runs sbatch.

        Returns:
            The SLURM job ID.

        Raises:
            ExecutionError: If sbatch fails or output can't be parsed.
        """
        job_dir = f"{self._work_dir}/job-{hash(job) % 100000:05d}"

        try:
            # 1. Create remote working directory
            await self._transport.exec_command(f"mkdir -p {job_dir}")

            # 2. Generate and upload sbatch script
            script = _generate_sbatch_script(job, self._work_dir, f"{job_dir}/submit")
            await self._transport.write_remote_file(script, f"{job_dir}/submit.sh")

            # 3. Generate and upload LAMMPS input
            lammps_input = _generate_lammps_input(job)
            await self._transport.write_remote_file(
                lammps_input,
                f"{job_dir}/submit.input",
            )

            # 4. Run sbatch
            stdout = await self._transport.exec_command(f"sbatch {job_dir}/submit.sh")
            return _parse_sbatch_output(stdout)

        except ConnectionError as exc:
            raise ExecutionError(f"SSH connection failed: {exc}") from exc

    async def poll(self, job_id: str) -> JobStatus:
        """Poll job status via squeue, falling back to sacct.

        Args:
            job_id: SLURM job ID.

        Returns:
            Current JobStatus.

        Raises:
            ExecutionError: If SSH connection fails.
        """
        try:
            # Try squeue first (active jobs)
            squeue_out = await self._transport.exec_command(
                f"squeue -j {job_id} --noheader -o '%T|%P|%D|%N|%M' 2>/dev/null"
            )
            if squeue_out.strip():
                status = _parse_squeue_line(squeue_out.strip())
                if status is not None:
                    return status

            # Fall back to sacct (completed/failed jobs)
            sacct_out = await self._transport.exec_command(
                f"sacct -j {job_id} --noheader --format=State 2>/dev/null"
            )
            if sacct_out.strip():
                status = _parse_sacct_line(sacct_out.strip())
                if status is not None:
                    return status

            return JobStatus.UNKNOWN

        except ConnectionError as exc:
            raise ExecutionError(f"SSH connection failed: {exc}") from exc

    async def cancel(self, job_id: str) -> bool:
        """Cancel a job via scancel.

        Args:
            job_id: SLURM job ID.

        Returns:
            True if scancel succeeded, False otherwise.
        """
        try:
            await self._transport.exec_command(f"scancel {job_id}")
            return True
        except ConnectionError:
            return False

    async def retrieve_output(self, job_id: str) -> JobOutput:
        """Retrieve output for a completed job.

        Downloads stdout and stderr from the remote host.

        Args:
            job_id: SLURM job ID.

        Returns:
            JobOutput with logs and status.

        Raises:
            ExecutionError: If SSH connection fails.
        """
        try:
            # Check final status
            status = await self.poll(job_id)

            # Try to download stdout
            stdout_path = f"{self._work_dir}/{job_id}.out"
            log_output = ""
            error_message = None

            try:
                log_bytes = await self._transport.download_file(stdout_path)
                log_output = log_bytes.decode("utf-8", errors="replace")
            except ConnectionError:
                pass

            # Try to download stderr
            stderr_path = f"{self._work_dir}/{job_id}.err"
            try:
                err_bytes = await self._transport.download_file(stderr_path)
                err_text = err_bytes.decode("utf-8", errors="replace")
                if err_text.strip():
                    error_message = err_text
            except ConnectionError:
                pass

            return JobOutput(
                job_id=job_id,
                status=status,
                log_output=log_output,
                error_message=error_message,
            )

        except ConnectionError as exc:
            raise ExecutionError(f"SSH connection failed: {exc}") from exc
