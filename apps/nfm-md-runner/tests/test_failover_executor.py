"""Tests for the FailoverExecutor (NFM-377).

Wraps two Executor backends (primary + backup).
Automatically falls over when the primary is unhealthy.
"""

from __future__ import annotations

import pytest

from nfm_md_runner.failover_executor import FailoverExecutor
from nfm_md_runner.ports import (
    Executor,
    JobSpec,
    JobStatus,
    JobOutput,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubExecutor:
    """Trivial Executor stub for testing."""

    def __init__(self, *, healthy: bool = True) -> None:
        self._healthy = healthy
        self.submitted: list[JobSpec] = []
        self.polled_ids: list[str] = []
        self.cancelled_ids: list[str] = []
        self.retrieved_ids: list[str] = []

    async def submit(self, job: JobSpec) -> str:
        self.submitted.append(job)
        return "stub-123"

    async def poll(self, job_id: str) -> JobStatus:
        self.polled_ids.append(job_id)
        return JobStatus.COMPLETED

    async def cancel(self, job_id: str) -> bool:
        self.cancelled_ids.append(job_id)
        return True

    async def retrieve_output(self, job_id: str) -> JobOutput:
        self.retrieved_ids.append(job_id)
        return JobOutput(job_id=job_id, status=JobStatus.COMPLETED)


class _FailingExecutor:
    """Executor that always raises on submit."""

    async def submit(self, job: JobSpec) -> str:
        raise ConnectionError("primary cluster unreachable")

    async def poll(self, job_id: str) -> JobStatus:
        raise ConnectionError("primary cluster unreachable")

    async def cancel(self, job_id: str) -> bool:
        raise ConnectionError("primary cluster unreachable")

    async def retrieve_output(self, job_id: str) -> JobOutput:
        raise ConnectionError("primary cluster unreachable")


def _default_spec() -> JobSpec:
    return JobSpec(
        potential_file="UO2.eam.alloy",
        structure_file="UO2.lmp",
        temperature=300.0,
        steps=1000,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFailoverExecutorSubmit:
    """submit() falls over to backup when primary fails."""

    @pytest.mark.asyncio
    async def test_submit_primary_healthy(self):
        """Uses primary when healthy."""
        primary = _StubExecutor(healthy=True)
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        job_id = await ex.submit(_default_spec())

        assert job_id == "stub-123"
        assert len(primary.submitted) == 1
        assert len(backup.submitted) == 0

    @pytest.mark.asyncio
    async def test_submit_falls_over_to_backup(self):
        """Falls over to backup when primary submit fails."""
        primary = _FailingExecutor()
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        job_id = await ex.submit(_default_spec())

        assert job_id == "stub-123"
        assert len(backup.submitted) == 1

    @pytest.mark.asyncio
    async def test_submit_both_fail_raises(self):
        """Raises when both primary and backup fail."""
        primary = _FailingExecutor()
        backup = _FailingExecutor()
        ex = FailoverExecutor(primary=primary, backup=backup)

        with pytest.raises(ConnectionError, match="primary cluster unreachable"):
            await ex.submit(_default_spec())


class TestFailoverExecutorPoll:
    """poll() falls over to backup."""

    @pytest.mark.asyncio
    async def test_poll_primary_healthy(self):
        primary = _StubExecutor(healthy=True)
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        status = await ex.poll("job-1")

        assert status == JobStatus.COMPLETED
        assert "job-1" in primary.polled_ids

    @pytest.mark.asyncio
    async def test_poll_falls_over_to_backup(self):
        primary = _FailingExecutor()
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        status = await ex.poll("job-1")

        assert status == JobStatus.COMPLETED
        assert "job-1" in backup.polled_ids


class TestFailoverExecutorCancel:
    """cancel() falls over to backup."""

    @pytest.mark.asyncio
    async def test_cancel_primary_healthy(self):
        primary = _StubExecutor(healthy=True)
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        result = await ex.cancel("job-1")

        assert result is True
        assert "job-1" in primary.cancelled_ids

    @pytest.mark.asyncio
    async def test_cancel_falls_over_to_backup(self):
        primary = _FailingExecutor()
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        result = await ex.cancel("job-1")

        assert result is True
        assert "job-1" in backup.cancelled_ids


class TestFailoverExecutorRetrieve:
    """retrieve_output() falls over to backup."""

    @pytest.mark.asyncio
    async def test_retrieve_primary_healthy(self):
        primary = _StubExecutor(healthy=True)
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        output = await ex.retrieve_output("job-1")

        assert output.status == JobStatus.COMPLETED
        assert output.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_retrieve_falls_over_to_backup(self):
        primary = _FailingExecutor()
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        output = await ex.retrieve_output("job-1")

        assert output.status == JobStatus.COMPLETED
        assert output.job_id == "job-1"


class TestFailoverExecutorHealthCheck:
    """Health check heartbeat mechanism."""

    @pytest.mark.asyncio
    async def test_healthy_returns_true(self):
        primary = _StubExecutor(healthy=True)
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=primary, backup=backup)

        healthy = await ex.health_check()

        assert healthy is True

    @pytest.mark.asyncio
    async def test_unhealthy_primary_triggers_backup(self):
        """When primary health check fails, executor marks primary as unhealthy
        and subsequent calls go to backup."""
        unhealthy = _FailingExecutor()
        backup = _StubExecutor(healthy=True)
        ex = FailoverExecutor(primary=unhealthy, backup=backup)

        # First call fails over to backup
        job_id = await ex.submit(_default_spec())
        assert len(backup.submitted) == 1

        # Primary is now marked unhealthy — next call also goes to backup
        job_id2 = await ex.submit(_default_spec())
        assert len(backup.submitted) == 2

    @pytest.mark.asyncio
    async def test_no_backup_raises(self):
        """With no backup, failure propagates."""
        primary = _FailingExecutor()
        ex = FailoverExecutor(primary=primary, backup=None)

        with pytest.raises(ConnectionError):
            await ex.submit(_default_spec())


class TestFailoverExecutorProperties:
    """Property accessors."""

    def test_is_executor(self):
        """FailoverExecutor satisfies the Executor Protocol."""
        primary = _StubExecutor()
        ex = FailoverExecutor(primary=primary, backup=None)
        assert isinstance(ex, Executor)

    def test_primary_and_backup_access(self):
        primary = _StubExecutor()
        backup = _StubExecutor()
        ex = FailoverExecutor(primary=primary, backup=backup)
        assert ex.primary is primary
        assert ex.backup is backup
