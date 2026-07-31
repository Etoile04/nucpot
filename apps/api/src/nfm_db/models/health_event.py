"""Health event model for structured silent-failure tracking (NFM-2220).

Records every previously-swallowed silent exception as a first-class
event so that the ops dashboard can surface degradation that was
previously invisible.

Queried by NFM-2211-C via ``GET /api/v1/health/alerts``.

NFM-2241 H1: the PK is a UUID. The alert endpoint filters by
``event_type`` and ``severity`` and never references the primary key
column directly, so a UUID PK can ship without breaking the published
contract.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class HealthEvent(Base):
    """Structured health event emitted by services when exceptions are caught.

    Replaces bare ``except: pass`` blocks so that failures are
    always observable.
    """

    __tablename__ = "health_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    source_service: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    context: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<HealthEvent(id={self.id}, "
            f"event_type='{self.event_type}', "
            f"severity='{self.severity}', "
            f"source_service='{self.source_service}')>"
        )
