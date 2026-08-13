"""Pydantic schemas for the backup subsystem (NFM-3024-T1).

The new ``retention`` object supersedes the legacy flat ``retentionDays``
key. Both are accepted for one release cycle; loading the legacy alias emits
a deprecation warning via :mod:`nfm_db.backup.config_loader`.

JSON field names use camelCase (matching the wake payload example) and are
exposed on the Pydantic model as snake_case Python attributes via
``Field(alias=..., populate_by_name=True)``.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TierSpec(BaseModel):
    """A single retention tier — how many snapshots to keep and how often."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # Accept both ``intervalMinutes`` (camelCase, wake-payload JSON) and
        # ``interval_minutes`` (snake_case, Python idiom). The serialized
        # Python attribute is always snake_case.
        populate_by_name=True,
    )

    interval_minutes: int = Field(
        ...,
        gt=0,
        alias="intervalMinutes",
        description="How often a snapshot in this tier is produced, in minutes.",
    )
    count: int = Field(
        ...,
        gt=0,
        description="How many snapshots in this tier to retain.",
    )


class RetentionConfig(BaseModel):
    """The three-tier GFS retention policy.

    Defaults match the NFM-3024-T1 spec example (24 hourly, 7 daily, 4 weekly)
    so a freshly-loaded config behaves sensibly without explicit values.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    hourly: TierSpec = Field(
        default_factory=lambda: TierSpec.model_validate({"intervalMinutes": 60, "count": 24}),
    )
    daily: TierSpec = Field(
        default_factory=lambda: TierSpec.model_validate({"intervalMinutes": 1440, "count": 7}),
    )
    weekly: TierSpec = Field(
        default_factory=lambda: TierSpec.model_validate({"intervalMinutes": 10080, "count": 4}),
    )


class BackupConfig(BaseModel):
    """Top-level ``backup`` config block.

    Accepts either the new ``retention`` object OR the legacy
    ``retentionDays`` alias (one release cycle). When both are present the
    ``retention`` object wins and ``retention_days`` is preserved for
    round-tripping.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    retention: RetentionConfig | None = None
    # Legacy: flat day-count retention. Deprecated; will be removed next cycle.
    retention_days: int | None = Field(
        default=None,
        ge=1,
        alias="retentionDays",
        description="DEPRECATED — use ``retention`` instead.",
    )
    max_total_bytes: int | None = Field(default=None, ge=0, alias="maxTotalBytes")
    min_free_bytes: int | None = Field(default=None, ge=0, alias="minFreeBytes")
    refuse_on_floor_breach: bool = Field(default=False, alias="refuseOnFloorBreach")

    @model_validator(mode="after")
    def _require_retention_or_legacy(self) -> Self:
        """At least one of ``retention`` / ``retention_days`` must be set."""
        if self.retention is None and self.retention_days is None:
            raise ValueError(
                "BackupConfig requires either 'retention' or 'retentionDays'."
            )
        return self


__all__ = ["BackupConfig", "RetentionConfig", "TierSpec"]
