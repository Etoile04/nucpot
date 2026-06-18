"""Potential ORM model for interatomic potentials.

Mirrors the legacy Supabase `potentials` schema but uses cross-database-safe
JSON columns (not PG-only ARRAY/JSONB) so SQLite test fixtures work.
Element-overlap and JSONB filtering happen in the service layer for portability.
"""

import uuid

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, TimestampMixin


class Potential(TimestampMixin, Base):
    """An interatomic potential record (EAM, MEAM, MTP, etc.)."""

    __tablename__ = "potentials"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(256), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    type: Mapped[str] = mapped_column(String(64))  # EAM, MEAM, MTP, ACE, LJ
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    format: Mapped[str | None] = mapped_column(String(64), nullable=True)
    elements: Mapped[list] = mapped_column(JSON, default=list)  # ["U", "Mo"]
    system_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    system_tags: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str | None] = mapped_column(nullable=True)
    applicability: Mapped[dict] = mapped_column(JSON, default=dict)
    references: Mapped[list] = mapped_column(JSON, default=list)
    developers: Mapped[list] = mapped_column(JSON, default=list)
    verified_props: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sim_software: Mapped[list] = mapped_column(JSON, default=list)
    lammps_config: Mapped[dict] = mapped_column(JSON, default=dict)
    file_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int | None] = mapped_column(nullable=True)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_doi: Mapped[str | None] = mapped_column(String(128), nullable=True)
    license: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[str] = mapped_column(String(16), default="1.0")
    status: Mapped[str] = mapped_column(String(16), default="published")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
