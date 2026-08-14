"""Backup snapshot and stats response schemas (NFM-3024-D / NFM-3052).

Pydantic models for the admin backup API:

- ``BackupTier`` — ``hourly`` | ``daily`` | ``weekly`` (all optional;
  pre-migration snapshots serialize as ``null`` per AC3).
- ``BackupSnapshotResponse`` — single snapshot with tier metadata
  (``Optional[BackupTier]`` so legacy snapshots surface as ``null``).
- ``BackupListResponse`` — wrapper for ``GET /api/admin/backups``.
- ``BackupStatsResponse`` — disk/capacity metrics for
  ``GET /api/admin/backups/stats``.
- ``BackupRefusalsSnapshot`` — internal-immutable record returned by the
  refusal-counter helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class BackupTier(str, Enum):
    """GFS retention tier assigned to a backup snapshot."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"


# Camels the JSON keys per NFM-3052 / NFM-3024-D spec (``totalBytes``,
# ``freeBytes``, ``refusalCount``, ``lastRefusalAt``).  ``populate_by_name``
# keeps the snake_case Python attributes usable in code and tests.
_RESPONSE_CONFIG = ConfigDict(
    alias_generator=to_camel,
    populate_by_name=True,
    frozen=False,
)


class BackupSnapshotResponse(BaseModel):
    """A single backup snapshot with tier metadata.

    ``tier`` is **nullable** so that pre-migration snapshots (no tier
    metadata persisted) surface as ``null`` rather than being silently
    bucketed into an arbitrary tier — see AC3 of NFM-3052.
    """

    model_config = _RESPONSE_CONFIG

    filename: str = Field(description="Backup file name")
    size_bytes: int = Field(ge=0, description="File size in bytes")
    created_at: datetime = Field(description="Snapshot creation timestamp")
    tier: BackupTier | None = Field(
        default=None,
        description=(
            "GFS retention tier (``hourly`` | ``daily`` | ``weekly``). "
            "``null`` for pre-migration snapshots."
        ),
    )


class BackupListResponse(BaseModel):
    """Wrapper for the list of backup snapshots returned by the list endpoint."""

    model_config = _RESPONSE_CONFIG

    snapshots: list[BackupSnapshotResponse] = Field(
        default_factory=list, description="List of backup snapshots"
    )
    total: int = Field(ge=0, description="Total number of snapshots")


class BackupStatsResponse(BaseModel):
    """Disk and capacity metrics for the backup subsystem.

    All numeric fields are non-negative integers; ``last_refusal_at`` is
    nullable and must be ``null`` whenever ``refusal_count == 0``.

    Serialized JSON keys are camelCase (``totalBytes``, ``freeBytes``,
    ``refusalCount``, ``lastRefusalAt``) per the NFM-3052 contract.
    """

    model_config = _RESPONSE_CONFIG

    total_bytes: int = Field(ge=0, description="Total disk space in bytes")
    free_bytes: int = Field(ge=0, description="Free disk space in bytes")
    refusal_count: int = Field(
        ge=0, description="Backups refused by capacity guardrails"
    )
    last_refusal_at: datetime | None = Field(
        default=None, description="ISO8601 timestamp of most recent refusal"
    )


@dataclass(frozen=True)
class BackupRefusalsSnapshot:
    """Immutable snapshot of refusal-counter state.

    Returned by :func:`nfm_db.services.backup_service.snapshot_refusals`
    so callers cannot mutate the live counter.
    """

    refusal_count: int
    last_refusal_at: datetime | None
