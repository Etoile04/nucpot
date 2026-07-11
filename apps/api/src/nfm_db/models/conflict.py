"""Conflict record ORM model for multi-source fusion (NFM-839 B3.2).

Tracks property value conflicts detected across multiple literature sources
and records resolution decisions for auditability.

NOTE: The ConflictRecord ORM class lives in conflict_record.py (stub used by
the /api/v1/kg/conflicts router and its tests). This module defines only the
ConflictStatus and ResolutionStrategy enums, plus re-exports ConflictRecord for
backward compatibility with fusion_pipeline.py and conflict_resolution.py.
"""

from __future__ import annotations

from enum import StrEnum

from nfm_db.models.conflict_record import ConflictRecord

__all__ = ["ConflictRecord", "ConflictStatus", "ResolutionStrategy"]


class ConflictStatus(StrEnum):
    """Lifecycle states for a conflict record."""

    PENDING = "pending"
    AUTO_RESOLVED = "auto_resolved"
    MANUALLY_RESOLVED = "manually_resolved"
    ESCALATED = "escalated"
    DISMISSED = "dismissed"


class ResolutionStrategy(StrEnum):
    """Available conflict resolution strategies (ADR-NFM-817-3)."""

    NEWEST = "newest"
    CONFIDENCE = "confidence"
    CONSENSUS = "consensus"
    MANUAL = "manual"
