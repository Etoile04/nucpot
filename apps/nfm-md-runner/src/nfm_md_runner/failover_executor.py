"""
Failover Executor (NFM-377).

Wraps two Executor backends (primary + backup).
Automatically falls over when the primary is unhealthy.
"""

from __future__ import annotations

import logging
from typing import Protocol

from nfm_md_runner.ports import (
    JobOutput,
    JobSpec,
    JobStatus,
)

logger = logging.getLogger(__name__)


class _ExecutorWithHealth(Protocol):
    """Extended executor protocol with optional health check."""

    async def submit(self, job: JobSpec) -> str: ...
    async def poll(self, job_id: str) -> JobStatus: ...
    async def cancel(self, job_id: str) -> bool: ...
    async def retrieve_output(self, job_id: str) -> JobOutput: ...


class FailoverExecutor:
    """Dual-cluster executor with automatic failover.

    Delegates all operations to the primary executor.
    If the primary fails, falls over to the backup (if configured).
    Once the primary is marked unhealthy, all subsequent calls go to backup.
    """

    def __init__(
        self,
        primary: _ExecutorWithHealth,
        backup: _ExecutorWithHealth | None = None,
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._primary_healthy = True

    @property
    def primary(self) -> _ExecutorWithHealth:
        return self._primary

    @property
    def backup(self) -> _ExecutorWithHealth | None:
        return self._backup

    async def submit(self, job: JobSpec) -> str:
        if self._primary_healthy:
            try:
                return await self._primary.submit(job)
            except Exception:
                logger.warning("Primary executor submit failed, failing over")
                self._primary_healthy = False

        if self._backup is not None:
            return await self._backup.submit(job)

        raise ConnectionError("Primary executor unavailable and no backup configured")

    async def poll(self, job_id: str) -> JobStatus:
        if self._primary_healthy:
            try:
                return await self._primary.poll(job_id)
            except Exception:
                logger.warning("Primary executor poll failed, failing over")
                self._primary_healthy = False

        if self._backup is not None:
            return await self._backup.poll(job_id)

        raise ConnectionError("Primary executor unavailable and no backup configured")

    async def cancel(self, job_id: str) -> bool:
        if self._primary_healthy:
            try:
                return await self._primary.cancel(job_id)
            except Exception:
                logger.warning("Primary executor cancel failed, failing over")
                self._primary_healthy = False

        if self._backup is not None:
            return await self._backup.cancel(job_id)

        return False

    async def retrieve_output(self, job_id: str) -> JobOutput:
        if self._primary_healthy:
            try:
                return await self._primary.retrieve_output(job_id)
            except Exception:
                logger.warning(
                    "Primary executor retrieve_output failed, failing over",
                )
                self._primary_healthy = False

        if self._backup is not None:
            return await self._backup.retrieve_output(job_id)

        raise ConnectionError("Primary executor unavailable and no backup configured")

    async def health_check(self) -> bool:
        """Check if the primary executor is reachable."""
        return self._primary_healthy
