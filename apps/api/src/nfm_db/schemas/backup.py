"""Backup snapshot and stats response schemas — NFM-3044.

Provides Pydantic models for the admin backup API:

- ``BackupTier`` — enum for GFS retention tiers (hourly | daily | weekly).
- ``BackupSnapshotResponse`` — a single backup snapshot with tier metadata.
- ``BackupListResponse`` — list wrapper returned by ``GET /api/admin/backups``.
- ``BackupStatsResponse`` — disk/capacity metrics returned by
  ``GET /api/admin/backups/stats``.
- ``TierStats`` / ``TierBreakdown`` — per-tier ``{count, bytes}`` aggregates
  embedded in the stats response.

Field naming follows the issue spec: top-level stats fields serialize as
camelCase (``totalBytes``, ``freeBytes``, ``refusalCount``, ``lastRefusalAt``).
Python attribute names stay snake_case (idiomatic PEP 8) via Pydantic's
``alias_generator`` plus ``populate_by_name=True``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class BackupTier(str, Enum):
    """GFS retention tier assigned to a backup snapshot."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


class _CamelModel(BaseModel):
    """Base model: snake_case attributes, camelCase wire format.

    ``alias_generator=to_camel`` rewrites snake_case attribute names to
    camelCase aliases on both validation and serialization. With
    ``populate_by_name=True`` callers may still construct the model using
    the snake_case Python attribute names. ``serialize_by_alias=True`` makes
    ``model_dump()`` (and FastAPI's response serialization) emit camelCase
    by default.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class BackupSnapshotResponse(_CamelModel):
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


class BackupListResponse(_CamelModel):
    """Wrapper for the list of backup snapshots."""

    snapshots: list[BackupSnapshotResponse] = Field(
        default_factory=list, description="List of backup snapshots"
    )
    total: int = Field(ge=0, description="Total number of snapshots")


class TierStats(_CamelModel):
    """Per-tier aggregate: snapshot count and aggregate bytes.

    Attributes:
        count: Number of snapshots assigned to this tier.
        bytes: Sum of file sizes for snapshots in this tier.
    """

    count: int = Field(default=0, ge=0, description="Number of snapshots in tier")
    bytes: int = Field(default=0, ge=0, description="Aggregate bytes for tier")


class TierBreakdown(_CamelModel):
    """Per-tier breakdown of snapshot counts and bytes.

    All three tier keys are always present (zero-valued when empty) so the
    consumer can rely on a stable shape regardless of which tiers have any
    snapshots.
    """

    hourly: TierStats = Field(default_factory=TierStats, description="Hourly tier")
    daily: TierStats = Field(default_factory=TierStats, description="Daily tier")
    weekly: TierStats = Field(default_factory=TierStats, description="Weekly tier")


class BackupStatsResponse(_CamelModel):
    """Disk and capacity metrics for the backup subsystem.

    Top-level fields serialize as camelCase on the wire (matches the
    NFM-3044 spec); Python attribute names stay snake_case.

    Attributes:
        total_bytes: Total disk space in bytes on the backup volume.
        free_bytes: Free disk space in bytes on the backup volume.
        refusal_count: Number of backups refused due to capacity guardrails.
        last_refusal_at: Timestamp of the most recent refusal, or null.
        tiers: Per-tier snapshot count + bytes breakdown.
    """

    total_bytes: int = Field(
        ge=0, description="Total disk space in bytes",
    )
    free_bytes: int = Field(
        ge=0, description="Free disk space in bytes",
    )
    refusal_count: int = Field(
        ge=0, description="Backups refused by capacity guardrails",
    )
    last_refusal_at: datetime | None = Field(
        default=None, description="Timestamp of most recent refusal",
    )
    tiers: TierBreakdown = Field(
        default_factory=TierBreakdown,
        description="Per-tier snapshot counts and bytes",
    )
