"""Tests for nfm_node_client.offline_queue — SQLite-backed offline queue."""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

import pytest

from nfm_node_client.offline_queue import (
    OfflineQueue,
    OperationType,
    PendingOperation,
    SyncWatermark,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Any) -> str:
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test_queue.db")


@pytest.fixture
def queue(db_path: str) -> OfflineQueue:
    """Return an OfflineQueue backed by a temp DB."""
    q = OfflineQueue(db_path=db_path)
    yield q
    q.close()


def _make_op(
    *,
    op_type: OperationType = OperationType.CREATE,
    entity_type: str = "data_file",
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
) -> PendingOperation:
    """Factory for PendingOperation test instances."""
    return PendingOperation(
        op_type=op_type,
        entity_type=entity_type,
        entity_id=entity_id or str(uuid.uuid4()),
        payload=payload or {"key": "value"},
        priority=priority,
    )


# ---------------------------------------------------------------------------
# PendingOperation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pending_operation_frozen() -> None:
    """PendingOperation is a frozen dataclass."""
    op = _make_op()
    with pytest.raises(AttributeError):
        op.op_type = OperationType.DELETE  # type: ignore[misc]


@pytest.mark.unit
def test_pending_operation_defaults() -> None:
    """Default priority is 0, payload is empty dict."""
    op = PendingOperation(
        op_type=OperationType.UPDATE,
        entity_type="node",
        entity_id=str(uuid.uuid4()),
    )
    assert op.priority == 0
    assert op.payload == {}


@pytest.mark.unit
def test_pending_operation_to_dict_roundtrip() -> None:
    """to_dict / from_dict roundtrip preserves all fields."""
    original = _make_op(
        op_type=OperationType.DELETE,
        entity_type="node",
        entity_id="12345",
        payload={"foo": "bar", "num": 42},
        priority=5,
    )
    d = original.to_dict()
    restored = PendingOperation.from_dict(d)
    assert restored == original


# ---------------------------------------------------------------------------
# OperationType
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_operation_type_values() -> None:
    """OperationType enum has CREATE, UPDATE, DELETE."""
    assert OperationType.CREATE.value == "create"
    assert OperationType.UPDATE.value == "update"
    assert OperationType.DELETE.value == "delete"


# ---------------------------------------------------------------------------
# OfflineQueue — initialisation / schema
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_queue_creates_tables_on_init(db_path: str) -> None:
    """OfflineQueue creates upload_queue and sync_metadata tables."""
    OfflineQueue(db_path=db_path)
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "upload_queue" in tables
    assert "sync_metadata" in tables


@pytest.mark.unit
def test_queue_idempotent_init(db_path: str) -> None:
    """Creating OfflineQueue twice on same path doesn't raise."""
    OfflineQueue(db_path=db_path)
    OfflineQueue(db_path=db_path)  # second init — no error


# ---------------------------------------------------------------------------
# OfflineQueue — enqueue / dequeue
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enqueue_returns_id(queue: OfflineQueue) -> None:
    """enqueue returns a positive integer row ID."""
    op = _make_op()
    row_id = queue.enqueue(op)
    assert isinstance(row_id, int)
    assert row_id > 0


@pytest.mark.unit
def test_enqueue_stores_all_fields(queue: OfflineQueue) -> None:
    """All operation fields are persisted correctly."""
    entity_id = str(uuid.uuid4())
    op = _make_op(
        op_type=OperationType.UPDATE,
        entity_type="measurement",
        entity_id=entity_id,
        payload={"sensor": "A1", "value": 3.14},
        priority=3,
    )
    queue.enqueue(op)
    pending = queue.peek_all()
    assert len(pending) == 1
    stored = pending[0]
    assert stored.row_id is not None
    assert stored.op_type == OperationType.UPDATE
    assert stored.entity_type == "measurement"
    assert stored.entity_id == entity_id
    assert stored.payload == {"sensor": "A1", "value": 3.14}
    assert stored.priority == 3
    assert stored.status == "pending"


@pytest.mark.unit
def test_dequeue_returns_fifo_order(queue: OfflineQueue) -> None:
    """dequeue returns operations in FIFO order (by row_id)."""
    id1 = queue.enqueue(_make_op(payload={"seq": 1}))
    id2 = queue.enqueue(_make_op(payload={"seq": 2}))
    id3 = queue.enqueue(_make_op(payload={"seq": 3}))

    first = queue.dequeue()
    assert first is not None
    assert first.row_id == id1
    assert first.payload["seq"] == 1

    second = queue.dequeue()
    assert second is not None
    assert second.row_id == id2

    third = queue.dequeue()
    assert third is not None
    assert third.row_id == id3


@pytest.mark.unit
def test_dequeue_empty_returns_none(queue: OfflineQueue) -> None:
    """dequeue on empty queue returns None."""
    assert queue.dequeue() is None


@pytest.mark.unit
def test_dequeue_respects_priority(queue: OfflineQueue) -> None:
    """Higher priority operations are dequeued first within same timestamp."""
    queue.enqueue(_make_op(payload={"label": "lo"}, priority=0))
    queue.enqueue(_make_op(payload={"label": "hi"}, priority=10))
    queue.enqueue(_make_op(payload={"label": "mid"}, priority=5))

    results: list[PendingOperation] = []
    while True:
        op = queue.dequeue()
        if op is None:
            break
        results.append(op)

    assert results[0].payload["label"] == "hi"
    assert results[1].payload["label"] == "mid"
    assert results[2].payload["label"] == "lo"


# ---------------------------------------------------------------------------
# OfflineQueue — size / peek
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_size_starts_empty(queue: OfflineQueue) -> None:
    """New queue has size 0."""
    assert queue.size() == 0


@pytest.mark.unit
def test_size_increments(queue: OfflineQueue) -> None:
    """size increments with each enqueue."""
    queue.enqueue(_make_op())
    queue.enqueue(_make_op())
    assert queue.size() == 2


@pytest.mark.unit
def test_size_decrements_on_dequeue(queue: OfflineQueue) -> None:
    """size decrements when operations are dequeued."""
    queue.enqueue(_make_op())
    queue.enqueue(_make_op())
    queue.dequeue()
    assert queue.size() == 1


@pytest.mark.unit
def test_peek_all_returns_all_pending(queue: OfflineQueue) -> None:
    """peek_all returns all pending operations without removing them."""
    queue.enqueue(_make_op(payload={"a": 1}))
    queue.enqueue(_make_op(payload={"b": 2}))
    peeked = queue.peek_all()
    assert len(peeked) == 2
    assert queue.size() == 2  # peek doesn't remove


# ---------------------------------------------------------------------------
# OfflineQueue — mark_completed / mark_failed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mark_completed_removes_from_queue(queue: OfflineQueue) -> None:
    """mark_completed removes the operation from the queue."""
    row_id = queue.enqueue(_make_op())
    assert queue.size() == 1
    queue.mark_completed(row_id)
    assert queue.size() == 0


@pytest.mark.unit
def test_mark_failed_updates_status(queue: OfflineQueue) -> None:
    """mark_failed sets status to 'failed' with error message (not in peek_all)."""
    row_id = queue.enqueue(_make_op())
    queue.mark_failed(row_id, error="conflict detected")
    # peek_all only returns pending — failed ops are excluded
    assert queue.size() == 0
    # but the failed row still exists in DB
    import sqlite3
    conn = sqlite3.connect(queue._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM upload_queue WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "conflict detected"


@pytest.mark.unit
def test_mark_completed_nonexistent_is_noop(queue: OfflineQueue) -> None:
    """mark_completed on nonexistent row_id is a no-op."""
    queue.mark_completed(99999)  # doesn't raise


# ---------------------------------------------------------------------------
# OfflineQueue — clear / entity queries
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_clear_removes_all(queue: OfflineQueue) -> None:
    """clear removes all operations from the queue."""
    queue.enqueue(_make_op())
    queue.enqueue(_make_op())
    queue.clear()
    assert queue.size() == 0


@pytest.mark.unit
def test_pending_by_entity(queue: OfflineQueue) -> None:
    """pending_by_entity returns operations for a specific entity."""
    entity_id = str(uuid.uuid4())
    queue.enqueue(_make_op(entity_id=entity_id))
    queue.enqueue(_make_op(entity_id="other"))
    queue.enqueue(_make_op(entity_id=entity_id, payload={"second": True}))
    entity_ops = queue.pending_by_entity(entity_id)
    assert len(entity_ops) == 2


# ---------------------------------------------------------------------------
# OfflineQueue — persistence across restarts (AC-2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_queue_persists_across_restart(db_path: str) -> None:
    """Operations survive OfflineQueue destruction and recreation (AC-2)."""
    q1 = OfflineQueue(db_path=db_path)
    q1.enqueue(_make_op(payload={"survive": True}))
    q1.enqueue(_make_op(payload={"also": True}))
    del q1  # close/destroy

    q2 = OfflineQueue(db_path=db_path)
    assert q2.size() == 2
    ops = q2.peek_all()
    assert ops[0].payload["survive"] is True
    assert ops[1].payload["also"] is True


# ---------------------------------------------------------------------------
# SyncMetadata / SyncWatermark
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_watermark_defaults() -> None:
    """SyncWatermark defaults: last_sync_id=0, last_sync_time=None."""
    wm = SyncWatermark(hub_url="https://hub.test")
    assert wm.last_sync_id == 0
    assert wm.last_sync_time is None


@pytest.mark.unit
def test_sync_watermark_frozen() -> None:
    """SyncWatermark is frozen."""
    wm = SyncWatermark(hub_url="https://hub.test", last_sync_id=42)
    with pytest.raises(AttributeError):
        wm.last_sync_id = 99  # type: ignore[misc]


@pytest.mark.unit
def test_sync_metadata_roundtrip(queue: OfflineQueue) -> None:
    """set_watermark / get_watermark roundtrip."""
    queue.set_watermark(hub_url="https://hub.test", last_sync_id=100)
    wm = queue.get_watermark(hub_url="https://hub.test")
    assert wm is not None
    assert wm.hub_url == "https://hub.test"
    assert wm.last_sync_id == 100


@pytest.mark.unit
def test_sync_metadata_none_for_unknown_hub(queue: OfflineQueue) -> None:
    """get_watermark returns None for unknown hub."""
    assert queue.get_watermark(hub_url="https://unknown.test") is None


@pytest.mark.unit
def test_sync_metadata_update(queue: OfflineQueue) -> None:
    """set_watermark updates existing watermark."""
    queue.set_watermark(hub_url="https://hub.test", last_sync_id=10)
    queue.set_watermark(hub_url="https://hub.test", last_sync_id=50)
    wm = queue.get_watermark(hub_url="https://hub.test")
    assert wm is not None
    assert wm.last_sync_id == 50


@pytest.mark.unit
def test_sync_metadata_persists_across_restart(db_path: str) -> None:
    """Watermark survives queue restart (AC-4)."""
    q1 = OfflineQueue(db_path=db_path)
    q1.set_watermark(hub_url="https://hub.test", last_sync_id=77)
    del q1

    q2 = OfflineQueue(db_path=db_path)
    wm = q2.get_watermark(hub_url="https://hub.test")
    assert wm is not None
    assert wm.last_sync_id == 77


@pytest.mark.unit
def test_sync_metadata_multiple_hubs(queue: OfflineQueue) -> None:
    """Watermarks are independent per hub URL."""
    queue.set_watermark(hub_url="https://hub1.test", last_sync_id=10)
    queue.set_watermark(hub_url="https://hub2.test", last_sync_id=20)
    assert queue.get_watermark(hub_url="https://hub1.test").last_sync_id == 10
    assert queue.get_watermark(hub_url="https://hub2.test").last_sync_id == 20


# ---------------------------------------------------------------------------
# OfflineQueue — close
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_close_is_idempotent(queue: OfflineQueue) -> None:
    """close() is safe to call multiple times."""
    queue.close()
    queue.close()  # no error


@pytest.mark.unit
def test_operations_after_close_raises(queue: OfflineQueue) -> None:
    """Operations after close raise RuntimeError."""
    queue.close()
    with pytest.raises(RuntimeError, match="closed"):
        queue.enqueue(_make_op())
