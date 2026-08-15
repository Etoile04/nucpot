"""Backup management services with capacity guardrails (NFM-3016).

NFM-3024-B — 3-tier GFS scheduler with deterministic tier tagging.

Public API:
    BackupSnapshot  — frozen dataclass representing a backup snapshot.
    GFSTierConfig    — frozen dataclass for per-tier retention config.
    classify_tiers   — pure-function tier assignment (deterministic, idempotent).
    compute_retention_plan — convenience wrapper returning keep/prune lists.
    default_gfs_config      — factory for the NFM-3024 spec defaults.
"""

from nfm_db.services.backup.gfs_tier import (
    GFSTierConfig,
    RetentionPlan,
    classify_tiers,
    compute_retention_plan,
    default_gfs_config,
)
from nfm_db.services.backup.models import BackupSnapshot

__all__ = [
    "BackupSnapshot",
    "GFSTierConfig",
    "RetentionPlan",
    "classify_tiers",
    "compute_retention_plan",
    "default_gfs_config",
]
