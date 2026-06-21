"""HPC Failover Event Model for NFM-346 Phase 4.5.

This model tracks failover events between primary and backup HPC clusters.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class HPCFailoverEvent(Base):
    """Model for tracking HPC failover events.

    Records automatic failover from primary to backup cluster,
    including trigger reason, duration, and success status.
    """

    __tablename__ = 'hpc_failover_events'

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Event timestamp (when failover occurred)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )

    # Event type: failover_triggered, failover_failed, primary_recovered, etc.
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True
    )

    # Source cluster (where failover initiated from)
    source_cluster: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True
    )

    # Target cluster (where failover switched to, if successful)
    target_cluster: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True
    )

    # Reason for failover (human-readable description)
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    # Number of consecutive failures that triggered failover
    failure_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False
    )

    # Whether the failover operation succeeded
    success: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    # Additional metadata (JSONB for flexibility)
    # Can include: failover_duration_seconds, error_details, etc.
    event_metadata: Mapped[dict] = mapped_column(
        'metadata',
        JSONB,
        default=dict,
        nullable=False
    )

    # Record creation timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<HPCFailoverEvent(id={self.id}, "
            f"event_type='{self.event_type}', "
            f"source_cluster='{self.source_cluster}', "
            f"target_cluster='{self.target_cluster}', "
            f"success={self.success})>"
        )
