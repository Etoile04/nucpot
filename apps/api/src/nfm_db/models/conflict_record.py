"""Conflict record model (stub — full implementation pending).

This stub unblocks the import chain for tests while the full model
implementation is being completed on a separate branch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base


class ConflictRecord(Base):
    __tablename__ = "conflict_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("materials.id"))
    property_type_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("property_types.id"))
    source_values: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    resolution: Mapped[str | None] = mapped_column(String, default=None)
    resolved_value: Mapped[Any] = mapped_column(JSON, default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )
    resolved_by: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
