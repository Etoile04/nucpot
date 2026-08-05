"""Sync manager for reconnect batch sync with conflict detection.

Orchestrates the replay of offline-queued operations when the hub
comes back online. Provides exponential backoff retry on transient
failures, conflict detection, and watermark tracking.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum

from nfm_node_client.exceptions import NfmNodeClientError
from nfm_node_client.offline_detector import OfflineDetector
from nfm_node_client.offline_queue import OfflineQueue, PendingOperation
from nfm_node_client.retry import RetryPolicy, compute_backoff_delay


_LOGGER = logging.getLogger("nfm_node_client.sync_manager")

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BACKOFF_BASE = 0.5
_DEFAULT_BACKOFF_MAX = 30.0


class SyncStatus(str, Enum):
    """Result status of a sync operation."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class SyncConflictError(NfmNodeClientError):
    """Raised when a sync operation detects a conflict on the hub."""

    def __init__(self, entity_id: str, reason: str) -> None:
        super().__init__(f"conflict for {entity_id}: {reason}")
        self.entity_id = entity_id
        self.reason = reason


@dataclass(frozen=True)
class SyncResult:
    """Summary of a sync pass."""

    status: SyncStatus
    synced: int
    failed: int
    conflicts: list[SyncConflictError] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """True if all operations synced without conflicts."""
        return self.status == SyncStatus.SUCCESS and self.failed == 0


class SyncManager:
    """Manages batch replay of offline-queued operations.

    Parameters
    ----------
    queue:
        The OfflineQueue holding pending operations.
    detector:
        The OfflineDetector for hub state tracking.
    max_retries:
        Per-operation retry count on transient errors (default 3).
    backoff_base:
        Base delay in seconds for exponential backoff.
    backoff_max:
        Maximum backoff delay in seconds.
    """

    def __init__(
        self,
        queue: OfflineQueue,
        detector: OfflineDetector,
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        backoff_base: float = _DEFAULT_BACKOFF_BASE,
        backoff_max: float = _DEFAULT_BACKOFF_MAX,
    ) -> None:
        self._queue = queue
        self._detector = detector
        self._hub_url = detector.hub_url
        self._policy = RetryPolicy(
            max_retries=max_retries,
            backoff_base=backoff_base,
            backoff_max=backoff_max,
        )
        self._closed = False

    @property
    def hub_url(self) -> str:
        """Hub URL this manager syncs against."""
        return self._hub_url

    # ------------------------------------------------------------------
    # Send operation (override for testing)
    # ------------------------------------------------------------------

    async def _send_operation(self, operation: PendingOperation) -> None:
        """Send a single operation to the hub.

        Override this method for testing or to customize the HTTP call.
        The default implementation is a no-op placeholder — in
        production, this delegates to NfmNodeClient methods.

        Raises SyncConflictError on version conflicts.
        Raises NfmNodeClientError on transient or permanent failures.
        """
        # Production implementation would call the appropriate
        # NfmNodeClient method based on operation.op_type.

    # ------------------------------------------------------------------
    # Sync single operation
    # ------------------------------------------------------------------

    async def sync_single(self) -> SyncResult:
        """Sync the highest-priority pending operation.

        Returns a SyncResult with synced=0 if the queue is empty.
        """
        op = self._queue.dequeue()
        if op is None:
            return SyncResult(status=SyncStatus.SUCCESS, synced=0, failed=0)

        return await self._sync_with_retry(op)

    # ------------------------------------------------------------------
    # Sync all (AC-3)
    # ------------------------------------------------------------------

    async def sync_all(self) -> SyncResult:
        """Sync all pending operations from the queue in priority order.

        Operations are dequeued one at a time. Each is retried with
        exponential backoff. Conflicts are recorded but don't stop
        the sync of remaining operations. After a successful pass,
        the sync watermark is updated (AC-4).
        """
        total_synced = 0
        total_failed = 0
        conflicts: list[SyncConflictError] = []
        last_row_id: int | None = None

        while True:
            op = self._queue.dequeue()
            if op is None:
                break

            result = await self._sync_with_retry(op)
            last_row_id = op.row_id

            if result.synced > 0:
                total_synced += result.synced
            else:
                total_failed += result.failed
                conflicts.extend(result.conflicts)

        # Update watermark (AC-4)
        if last_row_id is not None and total_failed == 0:
            self._queue.set_watermark(hub_url=self._hub_url, last_sync_id=last_row_id)

        status = self._compute_status(total_synced, total_failed, conflicts)
        return SyncResult(
            status=status,
            synced=total_synced,
            failed=total_failed,
            conflicts=conflicts,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _sync_with_retry(self, op: PendingOperation) -> SyncResult:
        """Sync a single operation with exponential backoff retry.

        Returns SyncResult with synced=1 on success, failed=1 on failure.
        """
        last_exception: BaseException | None = None

        for attempt in range(self._policy.max_retries + 1):
            try:
                await self._send_operation(op)
                self._queue.mark_completed(op.row_id)  # type: ignore[arg-type]
                return SyncResult(status=SyncStatus.SUCCESS, synced=1, failed=0)

            except SyncConflictError as exc:
                self._queue.mark_failed(op.row_id, error=exc.reason)  # type: ignore[arg-type]
                return SyncResult(
                    status=SyncStatus.PARTIAL,
                    synced=0,
                    failed=1,
                    conflicts=[exc],
                )

            except NfmNodeClientError as exc:
                last_exception = exc
                if attempt >= self._policy.max_retries:
                    break
                delay = compute_backoff_delay(
                    attempt,
                    base=self._policy.backoff_base,
                    maximum=self._policy.backoff_max,
                )
                _LOGGER.warning(
                    "sync retry %d/%d for %s %s (delay=%.1fs): %s",
                    attempt + 1,
                    self._policy.max_retries,
                    op.op_type.value,
                    op.entity_id,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        assert last_exception is not None
        self._queue.mark_failed(
            op.row_id, error=str(last_exception)  # type: ignore[arg-type]
        )
        return SyncResult(status=SyncStatus.PARTIAL, synced=0, failed=1)

    @staticmethod
    def _compute_status(
        synced: int, failed: int, conflicts: list[SyncConflictError]
    ) -> SyncStatus:
        """Determine overall SyncStatus from counts."""
        if failed == 0 and not conflicts:
            return SyncStatus.SUCCESS
        if synced > 0:
            return SyncStatus.PARTIAL
        return SyncStatus.FAILED

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the sync manager. Idempotent."""
        if self._closed:
            return
        self._closed = True


__all__ = [
    "SyncConflictError",
    "SyncManager",
    "SyncResult",
    "SyncStatus",
]
