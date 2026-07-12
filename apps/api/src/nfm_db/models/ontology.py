"""Knowledge graph ontology registry ORM models.

Tables:
- kg_entity_types: controlled vocabulary for node types (Material, Property, etc.)
- kg_relation_types: controlled vocabulary for edge types (hasProperty, etc.)

NFM-716 — Phase 2B.2 NucMat Ontology
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nfm_db.models import Base, CompatJSONB, JSONArray, TimestampMixin

if TYPE_CHECKING:
    pass


class KEntityType(TimestampMixin, Base):
    """A controlled entity type in the knowledge graph ontology.

    Defines the shape requirements for nodes of this type:
    label_template, required_properties, and description.
    """

    __tablename__ = "kg_entity_types"
    __table_args__ = (
        UniqueConstraint("name", name="uq_kg_entity_types_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(50), unique=True)
    label_template: Mapped[str | None] = mapped_column(
        String(200), nullable=True,
    )
    required_properties: Mapped[list[str] | None] = mapped_column(
        JSONArray,
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<KEntityType id={self.id!s} name={self.name!r}>"


class KRelationType(TimestampMixin, Base):
    """A controlled relation type in the knowledge graph ontology.

    Defines allowed source/target entity types and a JSON Schema
    for the relation's properties payload.
    """

    __tablename__ = "kg_relation_types"
    __table_args__ = (
        UniqueConstraint("name", name="uq_kg_relation_types_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(100), unique=True)
    source_types: Mapped[list[str] | None] = mapped_column(
        JSONArray,
        nullable=True,
    )
    target_types: Mapped[list[str] | None] = mapped_column(
        JSONArray,
        nullable=True,
    )
    properties_schema: Mapped[dict[str, Any] | None] = mapped_column(
        CompatJSONB,
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<KRelationType id={self.id!s} name={self.name!r}>"
