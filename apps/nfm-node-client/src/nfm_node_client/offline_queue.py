"""SQLite-backed offline queue for resource node operations.

Stores pending create/update/delete operations locally when the hub is
unreachable and tracks sync watermarks per hub URL. Implements FIFO
ordering with priority support.

Tables:
  - ``upload_queue``: pending operations with status tracking.
  - ``sync_metadata``: per-hub watermark for last successful sync point.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


_LOGGER = logging.getLogger("nfm_node_client.offline_queue")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS upload_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type     TEXT    NOT NULL,
    entity_type TEXT    NOT NULL,
    entity_id   TEXT    NOT NULL,
    payload     TEXT    NOT NULL DEFAULT '{}',
    priority    INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'pending',
    error       TEXT    DEFAULT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_uq_status
    ON upload_queue(status);

CREATE INDEX IF NOT EXISTS idx_uq_entity
    ON upload_queue(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS sync_metadata (
    hub_url         TEXT PRIMARY KEY,
    last_sync_id    INTEGER NOT NULL DEFAULT 0,
    last_sync_time  TEXT    DEFAULT NULL,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


class OperationType(str, Enum):
    """Type of offline-queued operation."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class PendingOperation:
    """A single operation waiting to be synced to the hub."""

    op_type: OperationType
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    status: str = "pending"
    error: str | None = None
    row_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON / SQLite storage."""
        return {
            "op_type": self.op_type.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": json.dumps(self.payload),
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingOperation:
        """Deserialize from a plain dict (inverse of ``to_dict``)."""
        payload_raw = data.get("payload", "{}")
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        return cls(
            op_type=OperationType(data["op_type"]),
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            payload=payload,
            priority=int(data.get("priority", 0)),
            status=data.get("status", "pending"),
            error=data.get("error"),
            row_id=data.get("row_id"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )


@dataclass(frozen=True)
class SyncWatermark:
    """Watermark tracking the last successful sync point for a hub."""

    hub_url: str
    last_sync_id: int = 0
    last_sync_time: str | None = None
    updated_at: str | None = None


class OfflineQueue:
    """SQLite-backed queue for offline operations and sync metadata.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Created automatically if it
        does not exist.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._closed = False
        self._open()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open (or reopen) the database connection and ensure schema."""
        if self._conn is not None:
            return
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA_SQL)
        self._conn = conn

    def _ensure_open(self) -> sqlite3.Connection:
        """Return the connection, raising RuntimeError if closed."""
        if self._closed:
            raise RuntimeError("OfflineQueue is closed; create a new instance")
        if self._conn is None:
            self._open()
        assert self._conn is not None
        return self._conn

    @staticmethod
    def _row_to_operation(row: sqlite3.Row) -> PendingOperation:
        """Convert a database row to a PendingOperation."""
        payload = json.loads(row["payload"]) if row["payload"] else {}
        return PendingOperation(
            op_type=OperationType(row["op_type"]),
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            payload=payload,
            priority=row["priority"],
            status=row["status"],
            error=row["error"],
            row_id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Enqueue / Dequeue
    # ------------------------------------------------------------------

    def enqueue(self, operation: PendingOperation) -> int:
        """Add an operation to the queue. Returns the row ID."""
        conn = self._ensure_open()
        data = operation.to_dict()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """
            INSERT INTO upload_queue
                (op_type, entity_type, entity_id, payload, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["op_type"],
                data["entity_type"],
                data["entity_id"],
                data["payload"],
                data["priority"],
                now,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def dequeue(self) -> PendingOperation | None:
        """Remove and return the highest-priority pending operation, or None."""
        conn = self._ensure_open()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM upload_queue
            WHERE status = 'pending'
            ORDER BY priority DESC, id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        op = self._row_to_operation(row)
        conn.execute("DELETE FROM upload_queue WHERE id = ?", (op.row_id,))
        conn.commit()
        return op

    # ------------------------------------------------------------------
    # Peek / Size / Queries
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Return the number of pending operations."""
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT COUNT(*) FROM upload_queue WHERE status = 'pending'"
        ).fetchone()
        return row[0] if row else 0

    def peek_all(self) -> list[PendingOperation]:
        """Return all pending operations without removing them."""
        conn = self._ensure_open()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM upload_queue WHERE status = 'pending' ORDER BY priority DESC, id ASC"
        ).fetchall()
        return [self._row_to_operation(r) for r in rows]

    def pending_by_entity(self, entity_id: str) -> list[PendingOperation]:
        """Return pending operations for a specific entity."""
        conn = self._ensure_open()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM upload_queue WHERE entity_id = ? AND status = 'pending' ORDER BY id ASC",
            (entity_id,),
        ).fetchall()
        return [self._row_to_operation(r) for r in rows]

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def mark_completed(self, row_id: int) -> None:
        """Remove a completed operation from the queue."""
        conn = self._ensure_open()
        conn.execute("DELETE FROM upload_queue WHERE id = ?", (row_id,))
        conn.commit()

    def mark_failed(self, row_id: int, *, error: str = "") -> None:
        """Mark an operation as failed with an error message."""
        conn = self._ensure_open()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE upload_queue SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (error, now, row_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Remove all operations from the queue."""
        conn = self._ensure_open()
        conn.execute("DELETE FROM upload_queue")
        conn.commit()

    # ------------------------------------------------------------------
    # Sync Watermark (AC-4)
    # ------------------------------------------------------------------

    def set_watermark(self, *, hub_url: str, last_sync_id: int) -> None:
        """Set or update the sync watermark for a hub."""
        conn = self._ensure_open()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO sync_metadata (hub_url, last_sync_id, last_sync_time, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(hub_url) DO UPDATE SET
                last_sync_id = excluded.last_sync_id,
                last_sync_time = excluded.last_sync_time,
                updated_at = excluded.updated_at
            """,
            (hub_url, last_sync_id, now, now),
        )
        conn.commit()

    def get_watermark(self, *, hub_url: str) -> SyncWatermark | None:
        """Return the sync watermark for a hub, or None if not set."""
        conn = self._ensure_open()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sync_metadata WHERE hub_url = ?", (hub_url,)
        ).fetchone()
        if row is None:
            return None
        return SyncWatermark(
            hub_url=row["hub_url"],
            last_sync_id=row["last_sync_id"],
            last_sync_time=row["last_sync_time"],
            updated_at=row["updated_at"],
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the database connection. Idempotent."""
        if self._closed:
            return
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None
        self._closed = True


__all__ = [
    "OfflineQueue",
    "OperationType",
    "PendingOperation",
    "SyncWatermark",
]
