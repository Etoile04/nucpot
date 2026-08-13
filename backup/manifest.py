"""Sidecar manifest for GFS snapshot tier metadata.

Each snapshot has a corresponding JSON entry in a shared manifest file.
This allows tier metadata to persist across backup scheduler runs without
relying on filename conventions.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backup.snapshot import Snapshot
from backup.tier import Tier

_MANIFEST_FILENAME = "backup_manifest.json"


class Manifest:
    """Persistent JSON manifest tracking snapshot tier metadata.

    Args:
        directory: The directory where the manifest file lives.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / _MANIFEST_FILENAME
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        """Load manifest entries from disk."""
        if self._path.exists():
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
            self._entries = data.get("snapshots", {})
        else:
            self._entries = {}

    def _save(self) -> None:
        """Persist manifest entries to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"snapshots": self._entries}
        self._path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

    def write(self, snapshot: Snapshot) -> None:
        """Write or overwrite a snapshot's metadata in the manifest."""
        self._entries[snapshot.snapshot_id] = {
            "snapshot_id": snapshot.snapshot_id,
            "timestamp": snapshot.timestamp.isoformat(),
            "tier": snapshot.tier.value,
            "size_bytes": snapshot.size_bytes,
            "path": str(snapshot.path),
        }
        self._save()

    def read(self, snapshot_id: str) -> Snapshot | None:
        """Read a snapshot's metadata from the manifest.

        Returns ``None`` if the snapshot is not tracked.
        """
        entry = self._entries.get(snapshot_id)
        if entry is None:
            return None
        return Snapshot(
            snapshot_id=entry["snapshot_id"],
            timestamp=datetime.fromisoformat(entry["timestamp"]),
            tier=Tier(entry["tier"]),
            size_bytes=entry["size_bytes"],
            path=Path(entry["path"]),
        )

    def list_all(self) -> list[Snapshot]:
        """Return all snapshots tracked in the manifest."""
        result: list[Snapshot] = []
        for sid in self._entries:
            snap = self.read(sid)
            if snap is not None:
                result.append(snap)
        return result

    def update_tier(self, snapshot_id: str, new_tier: Tier) -> None:
        """Update a snapshot's tier in the manifest."""
        entry = self._entries.get(snapshot_id)
        if entry is None:
            raise KeyError(f"Snapshot '{snapshot_id}' not found in manifest")
        entry["tier"] = new_tier.value
        self._save()

    def delete(self, snapshot_id: str) -> None:
        """Remove a snapshot entry from the manifest."""
        self._entries.pop(snapshot_id, None)
        self._save()
