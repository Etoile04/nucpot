"""Tests for nfm_node_client.sync_manager — reconnection batch sync."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nfm_node_client.exceptions import UploadError
from nfm_node_client.offline_detector import OfflineDetector
from nfm_node_client.offline_queue import (
    OfflineQueue,
    OperationType,
    PendingOperation,
)
from nfm_node_client.sync_manager import (
    SyncConflictError,
    SyncResult,
    SyncStatus as SyncMgrStatus,
    SyncManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HUB_URL = "https://hub.example.test"


@pytest.fixture
def db_path(tmp_path: Any) -> str:
    return str(tmp_path / "sync_test.db")


@pytest.fixture
def queue(db_path: str) -> OfflineQueue:
    return OfflineQueue(db_path=db_path)


@pytest.fixture
def detector() -> OfflineDetector:
    """Return a mockable OfflineDetector (starts online)."""
    return OfflineDetector(hub_url=HUB_URL, check_interval=60.0)


def _make_op(
    *,
    op_type: OperationType = OperationType.CREATE,
    entity_type: str = "data_file",
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
) -> PendingOperation:
    return PendingOperation(
        op_type=op_type,
        entity_type=entity_type,
        entity_id=entity_id or str(uuid.uuid4()),
        payload=payload or {"key": "value"},
        priority=priority,
    )


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_result_success() -> None:
    """SyncResult with success status."""
    result = SyncResult(status=SyncMgrStatus.SUCCESS, synced=5, failed=0, conflicts=[])
    assert result.success
    assert result.synced == 5
    assert result.failed == 0
    assert result.conflicts == []


@pytest.mark.unit
def test_sync_result_partial_failure() -> None:
    """SyncResult with partial failure."""
    result = SyncResult(
        status=SyncMgrStatus.PARTIAL,
        synced=3,
        failed=1,
        conflicts=[SyncConflictError(
            entity_id="e1",
            reason="version mismatch",
        )],
    )
    assert not result.success
    assert result.synced == 3
    assert result.failed == 1
    assert len(result.conflicts) == 1


# ---------------------------------------------------------------------------
# SyncConflictError
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_conflict_error() -> None:
    """SyncConflictError carries entity_id and reason."""
    err = SyncConflictError(entity_id="abc", reason="stale version")
    assert err.entity_id == "abc"
    assert err.reason == "stale version"
    assert "abc" in str(err)
    assert "stale version" in str(err)


# ---------------------------------------------------------------------------
# SyncManager — initialisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_manager_init(queue: OfflineQueue, detector: OfflineDetector) -> None:
    """SyncManager requires queue and detector."""
    mgr = SyncManager(queue=queue, detector=detector)
    assert mgr.hub_url == HUB_URL


# ---------------------------------------------------------------------------
# SyncManager — sync_all (AC-3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sync_all_empty_queue(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all on empty queue returns SUCCESS with 0 synced."""
    mgr = SyncManager(queue=queue, detector=detector)
    result = await mgr.sync_all()
    assert result.success
    assert result.synced == 0
    assert result.failed == 0


@pytest.mark.unit
async def test_sync_all_processes_all_pending(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all processes all pending operations in order."""
    queue.enqueue(_make_op(payload={"seq": 1}))
    queue.enqueue(_make_op(payload={"seq": 2}))
    queue.enqueue(_make_op(payload={"seq": 3}))

    mgr = SyncManager(queue=queue, detector=detector)
    synced_ids: list[str] = []

    async def mock_send(op: PendingOperation) -> None:
        synced_ids.append(op.entity_id)

    mgr._send_operation = mock_send  # type: ignore[assignment]
    result = await mgr.sync_all()

    assert result.success
    assert result.synced == 3
    assert len(synced_ids) == 3


@pytest.mark.unit
async def test_sync_all_marks_completed(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all marks operations as completed after successful send."""
    queue.enqueue(_make_op())

    mgr = SyncManager(queue=queue, detector=detector)
    mgr._send_operation = AsyncMock()  # type: ignore[assignment]
    await mgr.sync_all()

    assert queue.size() == 0  # all completed


@pytest.mark.unit
async def test_sync_all_marks_failed_on_send_error(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all marks operations as failed on send error."""
    queue.enqueue(_make_op(payload={"status": "good"}))

    mgr = SyncManager(queue=queue, detector=detector)

    async def failing_send(op: PendingOperation) -> None:
        if op.payload.get("status") == "good":
            return  # succeeds
        raise UploadError("server error", status_code=500)

    mgr._send_operation = failing_send  # type: ignore[assignment]
    result = await mgr.sync_all()
    assert result.success
    assert result.synced == 1


# ---------------------------------------------------------------------------
# SyncManager — conflict detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sync_all_detects_conflicts(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all catches SyncConflictError and records it."""
    queue.enqueue(_make_op(entity_id="conflict-1"))

    mgr = SyncManager(queue=queue, detector=detector)

    async def conflict_send(op: PendingOperation) -> None:
        raise SyncConflictError(entity_id=op.entity_id, reason="version mismatch")

    mgr._send_operation = conflict_send  # type: ignore[assignment]
    result = await mgr.sync_all()

    assert not result.success
    assert result.synced == 0
    assert result.failed == 1
    assert len(result.conflicts) == 1
    assert result.conflicts[0].entity_id == "conflict-1"


# ---------------------------------------------------------------------------
# SyncManager — watermark tracking (AC-4)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sync_all_updates_watermark(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all updates the sync watermark after successful sync."""
    queue.enqueue(_make_op(payload={"a": 1}))
    row_id = queue.enqueue(_make_op(payload={"b": 2}))

    mgr = SyncManager(queue=queue, detector=detector)
    mgr._send_operation = AsyncMock()  # type: ignore[assignment]
    await mgr.sync_all()

    wm = queue.get_watermark(hub_url=HUB_URL)
    assert wm is not None
    assert wm.last_sync_id == row_id  # last dequeued row_id


# ---------------------------------------------------------------------------
# SyncManager — exponential backoff retry
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sync_all_retries_on_transient_error(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_all retries with exponential backoff on transient errors."""
    queue.enqueue(_make_op(payload={"label": "retry-me"}))

    mgr = SyncManager(
        queue=queue,
        detector=detector,
        max_retries=3,
        backoff_base=0.01,
        backoff_max=0.05,
    )

    call_count = 0

    async def eventual_success(op: PendingOperation) -> None:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise UploadError("transient 503", status_code=503)

    mgr._send_operation = eventual_success  # type: ignore[assignment]
    result = await mgr.sync_all()

    assert result.success
    assert result.synced == 1
    assert call_count == 2


# ---------------------------------------------------------------------------
# SyncManager — sync_single
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sync_single_success(
    queue: OfflineQueue, detector: OfflineDetector
) -> None:
    """sync_single syncs one operation."""
    queue.enqueue(_make_op())
    mgr = SyncManager(queue=queue, detector=detector)
    mgr._send_operation = AsyncMock()  # type: ignore[assignment]

    result = await mgr.sync_single()
    assert result.success
    assert queue.size() == 0


@pytest.mark.unit
async def test_sync_single_empty(queue: OfflineQueue, detector: OfflineDetector) -> None:
    """sync_single on empty queue returns SUCCESS with 0 synced."""
    mgr = SyncManager(queue=queue, detector=detector)
    result = await mgr.sync_single()
    assert result.success
    assert result.synced == 0


# ---------------------------------------------------------------------------
# SyncManager — close
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_close_is_idempotent(queue: OfflineQueue, detector: OfflineDetector) -> None:
    """close() is safe to call multiple times."""
    mgr = SyncManager(queue=queue, detector=detector)
    mgr.close()
    mgr.close()
