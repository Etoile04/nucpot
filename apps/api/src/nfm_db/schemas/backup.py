"""Backup snapshot and stats response schemas — NFM-3017.

Provides Pydantic models for the admin backup API:

- ``BackupTier`` — enum for GFS retention tiers (hourly | daily | weekly).
- ``BackupSnapshotResponse`` — a single backup snapshot with tier metadata.
- ``BackupListResponse`` — list wrapper returned by ``GET /api/admin/backups``.
- ``BackupStatsResponse`` — disk/capacity metrics returned by
  ``GET /api/admin/backups/stats``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BackupTier(str, Enum):
    """GFS retention tier assigned to a backup snapshot."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class BackupSnapshotResponse(BaseModel):
    """A single backup snapshot with tier metadata.

    Attributes:
        filename: Backup file name.
        size_bytes: File size in bytes.
        created_at: Snapshot creation timestamp.
        tier: GFS retention tier (hourly, daily, weekly).
    """

    filename: str = Field(description="Backup file name")
    size_bytes: int = Field(ge=0, description="File size in bytes")
    created_at: datetime = Field(description="Snapshot creation timestamp")
    tier: BackupTier = Field(description="GFS retention tier")


class BackupListResponse(BaseModel):
    """Wrapper for the list of backup snapshots."""

    snapshots: list[BackupSnapshotResponse] = Field(
        default_factory=list, description="List of backup snapshots"
    )
    total: int = Field(ge=0, description="Total number of snapshots")


class BackupStatsResponse(BaseModel):
    """Disk and capacity metrics for the backup subsystem.

    Attributes:
        total_bytes: Total disk space in bytes on the backup volume.
        free_bytes: Free disk space in bytes on the backup volume.
        refusal_count: Number of backups refused due to capacity guardrails.
        last_refusal_at: Timestamp of the most recent refusal, or null.
    """

    total_bytes: int = Field(ge=0, description="Total disk space in bytes")
    free_bytes: int = Field(ge=0, description="Free disk space in bytes")
    refusal_count: int = Field(ge=0, description="Backups refused by capacity guardrails")
    last_refusal_at: Optional[datetime] = Field(
        default=None, description="Timestamp of most recent refusal"
    )
