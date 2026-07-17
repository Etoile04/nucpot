"""Knowledge Graph node models (B2.1 / NFM-856).

SQLAlchemy ORM models for the knowledge graph:
- KGNode: Core entity node (material, element, property, etc.)
- KGReviewQueue: Low-confidence matches pending human review
- KGProvenance: Source tracking for each entity extraction
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin


class KGNode(Base, TimestampMixin):
    """A node in the knowledge graph representing a physical entity.

    Entities include materials (UO2, Pu), elements (U, Pu, O),
    properties (lattice_constant), and other domain concepts.
    """

    __tablename__ = "kg_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    canonical_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    node_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    aliases: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )

    # Relationships
    review_entries: Mapped[list[KGReviewQueue]] = relationship(
        back_populates="node",
        foreign_keys="[KGReviewQueue.node_id]",
        cascade="all, delete-orphan",
    )
    provenance_entries: Mapped[list[KGProvenance]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<KGNode id={self.id} name={self.name!r} type={self.node_type!r}>"


class KGReviewQueue(Base, TimestampMixin):
    """Low-confidence match candidates pending human review.

    Created when entity matching produces confidence below the
    review threshold (default 0.6). Human reviewers approve or
    reject these before the node enters the knowledge graph.
    """

    __tablename__ = "kg_review_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("kg_nodes.id"),
        nullable=False,
    )
    entity_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    match_candidate_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("kg_nodes.id"),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    node: Mapped[KGNode] = relationship(
        back_populates="review_entries",
        foreign_keys=[node_id],
    )
    match_candidate: Mapped[KGNode | None] = relationship(
        foreign_keys=[match_candidate_id],
        remote_side="KGNode.id",
    )

    def __repr__(self) -> str:
        return (
            f"<KGReviewQueue id={self.id} entity={self.entity_name!r} "
            f"confidence={self.confidence}>"
        )


class KGProvenance(Base, TimestampMixin):
    """Provenance tracking for knowledge graph entities.

    Records where each entity was extracted from, enabling
    traceability and source attribution.
    """

    __tablename__ = "kg_provenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("kg_nodes.id"),
        nullable=False,
    )
    source_id: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    extracted_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    node: Mapped[KGNode] = relationship(
        back_populates="provenance_entries",
    )

    def __repr__(self) -> str:
        return (
            f"<KGProvenance id={self.id} node_id={self.node_id} "
            f"source={self.source_id!r}>"
        )
