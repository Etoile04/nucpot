"""Tests for CapacityGuardrails — the core NFM-3016 acceptance criteria.

AC coverage:
- [x] Writing a backup that would breach minFreeBytes is refused with [SRE-WARNING]
- [x] maxTotalBytes cap triggers pruner until total <= cap
- [x] Cap and floor are checked AFTER pruner run, not just before write
- [x] refusalCount and lastRefusalAt are tracked
- [x] Unit test: simulated run that would breach floor produces [SRE-WARNING]
- [x] refuseOnFloorBreach=false disables the floor check
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezonefrom pathlib import Path

import pytest

from nfm_db.services.backup.config import BackupCapacityConfig
from nfm_db.services.backup.guardrails import (
    BackupEntry,
    CapacityGuardrails,
    DiskUsage,
    FloorBreachEvent,
            refused_at=datetime.now(timezone.utc),            capacity_total_bytes=4,
        )
        with pytest.raises(AttributeError):
            e.free_bytes = 0  # type: ignore[misc]

    def test_backup_entry_frozen(self) -> None:
        e = BackupEntry(path=Path("/x"), size_bytes=1, modified_at=0.0)
        with pytest.raises(AttributeError):
            e.size_bytes = 0  # type: ignore[misc]
