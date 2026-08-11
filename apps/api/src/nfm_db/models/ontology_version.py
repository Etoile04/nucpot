"""OntologyVersion ORM model.

Versioned ontology management — each row represents a published or draft
ontology schema (NFM-2579).  Versions follow semver (e.g. 1.2.0) and
carry the full ontology definition as a JSONB blob.

Status lifecycle: draft → published → deprecated.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, CompatJSONB, TimestampMixin

# Allowed statuses for an ontology version.
ONTOLOGY_VERSION_STATUSES: tuple[str, ...] = (
    "draft",
    "published",
    "deprecated",
)


class OntologyVersion(TimestampMixin, Base):
    """A versioned ontology schema snapshot (NFM-2579).

    Stores the complete ontology definition as a JSONB payload alongside
    metadata (semver string, changelog, status, author).
    """

    __tablename__ = "ontology_versions"

    # Named so migration 044 and autogenerate agree on the constraint identity.
    __table_args__ = (
        UniqueConstraint("version", name="uq_ontology_versions_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- Version identity ---
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Semver version string, e.g. 1.2.0.",
    )

    # --- Status ---
    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        nullable=False,
        comment=(
            "draft | published | deprecated — see "
            "ONTOLOGY_VERSION_STATUSES."
        ),
    )

    # --- Changelog ---
    changelog: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Human-readable changelog. Required when publishing "
            "(enforced at API layer, not DB constraint)."
        ),
    )

    # --- Authorship ---
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        comment="User who created this ontology version.",
    )

    # --- Ontology payload ---
    ontology_data: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        default=None,
        nullable=True,
        comment="The actual ontology schema content as JSON.",
    )

    # --- Back-populated from type tables (NFM-2873) ---
    entity_types = relationship(
        "KEntityType",
        back_populates="ontology_version",
        lazy="selectin",
    )
    relation_types = relationship(
        "KRelationType",
        back_populates="ontology_version",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<OntologyVersion id={self.id!s} "
            f"version={self.version!r} status={self.status!r}>"
        )
