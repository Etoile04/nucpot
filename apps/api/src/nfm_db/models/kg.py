"""Knowledge Graph ORM models.

Phase 2B tables: kg_nodes, kg_edges, kg_review_queue.
Stores extracted knowledge graph entities and their relationships,
with provenance tracking, confidence scores, and a human review queue.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.source import DataSource


class KGNode(TimestampMixin, Base):
    """A knowledge graph node representing an entity.

    Entity types: Material, Property, Experiment, Condition, Publication.
    """

    __tablename__ = "kg_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('Material', 'Property', 'Experiment', "
            "'Condition', 'Publication')",
            name="ck_kg_nodes_node_type",
        ),
        CheckConstraint(
            "status IN ('active', 'merged', 'deprecated', 'pending_review')",
            name="ck_kg_nodes_status",
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_kg_nodes_confidence",
        ),
        Index("ix_kg_nodes_type", "node_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    aliases: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="JSON array of alternative names, stored as JSON text",
    )
    properties: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        default=1.0,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
    )

    # -- relationships --
    source: Mapped["DataSource | None"] = relationship()
    outgoing_edges: Mapped[list["KGEdge"]] = relationship(
        back_populates="source_node",
        foreign_keys="[KGEdge.source_node_id]",
    )
    incoming_edges: Mapped[list["KGEdge"]] = relationship(
        back_populates="target_node",
        foreign_keys="[KGEdge.target_node_id]",
    )

    def __repr__(self) -> str:
        return f"<KGNode id={self.id!s} type={self.node_type!r} label={self.label!r}>"


class KGEdge(TimestampMixin, Base):
    """A directed edge connecting two knowledge graph nodes.

    Relation types: hasProperty, measuredIn, relatedTo, cites, hasCondition.
    """

    __tablename__ = "kg_edges"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_kg_edges_confidence",
        ),
        UniqueConstraint(
            "source_node_id",
            "target_node_id",
            "relation_type",
            name="uq_kg_edges_source_target_relation",
        ),
        Index("ix_kg_edges_source", "source_node_id"),
        Index("ix_kg_edges_target", "target_node_id"),
        Index("ix_kg_edges_relation", "relation_type"),
        Index("ix_kg_edges_source_relation", "source_node_id", "relation_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    properties: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        default=1.0,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -- relationships --
    source_node: Mapped["KGNode"] = relationship(
        back_populates="outgoing_edges",
        foreign_keys=[source_node_id],
    )
    target_node: Mapped["KGNode"] = relationship(
        back_populates="incoming_edges",
        foreign_keys=[target_node_id],
    )
    source: Mapped["DataSource | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<KGEdge id={self.id!s} "
            f"{self.source_node_id!s} -[{self.relation_type}]-> {self.target_node_id!s}>"
        )


# ---------------------------------------------------------------------------
# Valid types (enforced at service layer, not DB constraint)
# ---------------------------------------------------------------------------

VALID_RELATION_TYPES: frozenset[str] = frozenset(
    {"hasProperty", "measuredIn", "relatedTo", "cites", "hasCondition"}
)

VALID_NODE_TYPES: frozenset[str] = frozenset(
    {"Material", "Property", "Experiment", "Condition", "Publication"}
)


class KGReviewQueue(Base):
    """Human review queue for low-confidence or ambiguous KG items.

    Auto-populated by the GraphBuilder when:
    - Entity/relation confidence < 0.6
    - Dedup similarity is borderline (0.7-0.85)
    """

    __tablename__ = "kg_review_queue"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ('entity', 'relation')",
            name="ck_kg_review_queue_item_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'modified')",
            name="ck_kg_review_queue_status",
        ),
        Index("ix_kg_review_queue_status", "status"),
        Index("ix_kg_review_queue_item", "item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    review_reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
    )
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<KGReviewQueue id={self.id!s} type={self.item_type!r} "
            f"status={self.status!r}>"
        )
