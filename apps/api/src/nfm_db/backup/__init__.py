"""Retention-tier-aware backup subsystem.

NFM-3024-T1 — see :mod:`nfm_db.backup.tier_engine` for the classification
function and :mod:`nfm_db.config.backup` for the Pydantic schema that
describes the tier counts.

NFM-3066 / NFM-3024-A1: backup configuration schema.

Public surface:
    :class:`BackupConfig` — top-level schema, loaded from ``config.json``.
    :class:`RetentionConfig` — per-tier retention block (hourly/daily/weekly).
    :class:`TierSpec` — single tier's ``intervalMinutes`` + ``count``.

Backward compatibility: ``retentionDays`` is accepted as a deprecated alias for
one release cycle. When supplied without an explicit ``retention`` block, the
legacy flat 7-day behavior is materialized into a derived ``retention`` block
and a single WARN-level log line is emitted at startup.
"""

from nfm_db.backup.config import (
    BackupConfig,
    RetentionConfig,
    TierSpec,
    reset_retention_days_warned,
)

__all__ = [
    "BackupConfig",
    "RetentionConfig",
    "TierSpec",
    "reset_retention_days_warned",
]
