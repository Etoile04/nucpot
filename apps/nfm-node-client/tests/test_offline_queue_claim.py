"""Tests for durable claim/ack/nack queue semantics."""

from __future__ import annotations

from nfm_node_client.offline_queue import OfflineQueue, OperationType, PendingOperation


def test_claim_retains_until_ack_and_nack_requeues(tmp_path) -> None:
    queue = OfflineQueue(str(tmp_path / "queue.db"))
    row_id = queue.enqueue(
        PendingOperation(
            op_type=OperationType.CREATE,
            entity_type="material",
            entity_id="UO2",
            payload={"formula": "UO2"},
        )
    )

    claimed = queue.claim()
    assert claimed is not None
    assert claimed.row_id == row_id
    assert claimed.status == "in_flight"
    assert queue.size() == 0

    queue.nack(row_id, error="hub unavailable")
    assert queue.size() == 1
    assert queue.peek_all()[0].error == "hub unavailable"

    claimed_again = queue.claim()
    assert claimed_again is not None
    queue.ack(row_id)
    assert queue.peek_all() == []
    queue.close()


def test_in_flight_operations_recover_after_restart(tmp_path) -> None:
    path = str(tmp_path / "queue.db")
    queue = OfflineQueue(path)
    row_id = queue.enqueue(
        PendingOperation(
            op_type=OperationType.UPDATE,
            entity_type="material",
            entity_id="UO2",
        )
    )
    assert queue.claim() is not None
    queue.close()

    reopened = OfflineQueue(path)
    assert reopened.recover_in_flight() == 1
    recovered = reopened.claim()
    assert recovered is not None
    assert recovered.row_id == row_id
    reopened.close()
