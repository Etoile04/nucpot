"""Backup retention configuration schema (NFM-3036).

Pydantic models for the 3-tier GFS retention config.  Supports both
the new ``retention`` object and the deprecated ``retentionDays`` flat
integer, emitting a :class:`DeprecationWarning` when the old form is
used.
"""

from __future__ import annotations

import json
import warnings
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, model_validator


class Tier(str, Enum):
    """Retention tier for a backup file."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    EXPIRED = "expired"


class TierConfig(BaseModel):
    """Configuration for a single retention tier.

    Accepts both ``intervalMinutes`` (camelCase, JSON convention) and
    ``interval_minutes`` (snake_case) keys for ergonomic interop with
    existing config files.

    Attributes:
        interval_minutes: Minutes between backups in this tier.
        count: Maximum number of backups to keep in this tier.
    """

    interval_minutes: int = Field(
        gt=0,
        validation_alias=AliasChoices(
            "intervalMinutes",
            "interval_minutes",
        ),
    )
    count: int = Field(ge=0)


class RetentionConfig(BaseModel):
    """GFS-style 3-tier retention configuration.

    When ``retention_days`` is provided instead of a tier breakdown,
    it is converted into a flat hourly schedule with
    ``count = retention_days * 24``.
    """

    hourly: TierConfig = TierConfig(interval_minutes=60, count=24)
    daily: TierConfig = TierConfig(interval_minutes=1440, count=7)
    weekly: TierConfig = TierConfig(interval_minutes=10080, count=4)
    retention_days: int | None = Field(default=None, ge=1, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _convert_deprecated_retention_days(
        cls,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Derive hourly count from deprecated ``retentionDays``.

        If ``retention_days`` is set and no explicit ``hourly`` /
        ``daily`` / ``weekly`` keys are present, compute a flat
        hourly schedule.  When both forms are present the new schema
        takes precedence (no error — silent upgrade).
        """
        retention_days = values.get("retentionDays") or values.get(
            "retention_days"
        )
        if retention_days is None:
            return values

        # New-style keys explicitly provided → keep as-is.
        has_new_keys = any(
            k in values
            for k in ("hourly", "daily", "weekly")
            if isinstance(values.get(k), dict)
        )
        if has_new_keys:
            return values

        # Derive flat hourly schedule from legacy retentionDays.
        hourly_count = int(retention_days) * 24
        values["hourly"] = {
            "intervalMinutes": 60,
            "count": hourly_count,
        }
        values["daily"] = {"intervalMinutes": 1440, "count": 0}
        values["weekly"] = {"intervalMinutes": 10080, "count": 0}
        return values

    @property
    def total_slots(self) -> int:
        """Total number of backup slots across all tiers."""
        return self.hourly.count + self.daily.count + self.weekly.count


class BackupConfig(BaseModel):
    """Top-level backup configuration.

    Wraps the retention schedule together with capacity guardrails.

    Attributes:
        retention: GFS retention tier configuration.
        max_total_bytes: Soft cap on total backup size (bytes).
        min_free_bytes: Refuse new backups when free disk drops below this.
        refuse_on_floor_breach: When True, block writes at the floor.
    """

    retention: RetentionConfig = Field(
        default_factory=RetentionConfig,
    )
    max_total_bytes: int = Field(
        default=12884901888,
        ge=0,
        alias="maxTotalBytes",
    )
    min_free_bytes: int = Field(
        default=21474836480,
        ge=0,
        alias="minFreeBytes",
    )
    refuse_on_floor_breach: bool = Field(
        default=True,
        alias="refuseOnFloorBreach",
    )

    _uses_deprecated_retention_days: bool = False

    @model_validator(mode="before")
    @classmethod
    def _lift_deprecated_retention_days(
        cls,
        values: Any,
    ) -> Any:
        """Lift top-level ``retentionDays`` into the nested ``retention`` field.

        When legacy configs include ``retentionDays`` (or its snake-case
        alias) at the top level — alongside ``maxTotalBytes`` etc. —
        Pydantic would otherwise drop it as an unknown field because
        :class:`RetentionConfig`'s own ``before`` validator only runs
        when the nested ``retention`` payload is being validated.
        Routing the deprecated value into ``retention`` here keeps the
        backward-compatible shape working.
        """
        if not isinstance(values, dict):
            return values
        if "retention" in values:
            return values
        deprecated = values.get("retentionDays") or values.get(
            "retention_days"
        )
        if deprecated is None:
            return values
        hourly_count = int(deprecated) * 24
        new_values = dict(values)
        new_values["retention"] = {
            "hourly": {"intervalMinutes": 60, "count": hourly_count},
            "daily": {"intervalMinutes": 1440, "count": 0},
            "weekly": {"intervalMinutes": 10080, "count": 0},
        }
        return new_values

    @model_validator(mode="after")
    def _detect_deprecated_usage(
        self,
    ) -> BackupConfig:
        """Tag when deprecated ``retentionDays`` was consumed.

        Heuristic: if only hourly has a non-zero count and
        daily/weekly are zero *and* hourly.count is a multiple of
        24, flag as deprecated (flat schedule derived from days).
        """
        r = self.retention
        if (
            r.daily.count == 0
            and r.weekly.count == 0
            and r.hourly.count > 0
            and r.hourly.count % 24 == 0
        ):
            object.__setattr__(
                self,
                "_uses_deprecated_retention_days",
                True,
            )
        return self


def load_backup_config(path: Path) -> BackupConfig:
    """Load and validate a backup configuration from a JSON file.

    The JSON file must contain a top-level ``backup`` key whose
    value conforms to :class:`BackupConfig`.

    Emits a :class:`DeprecationWarning` when the deprecated
    ``retentionDays`` field is used without the new ``retention``
    object.

    Args:
        path: Path to the JSON configuration file.

    Returns:
        A validated :class:`BackupConfig` instance.

    Raises:
        FileNotFoundError: If *path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValidationError: If the schema is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Backup config not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    backup_data = raw.get("backup", raw)
    cfg = BackupConfig.model_validate(backup_data)

    if cfg._uses_deprecated_retention_days:
        warnings.warn(
            "The 'retentionDays' config field is deprecated and will "
            "be removed in a future release.  Migrate to the 'retention' "
            "object with hourly/daily/weekly tiers.  See NFM-3024.",
            DeprecationWarning,
            stacklevel=2,
        )

    return cfg
