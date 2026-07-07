"""Knowledge Graph ORM models (NFM-838 Batch 2).

Tables: kg_nodes, kg_edges, kg_review_queue.
Per CTO spec §3 and ADR-NFM-817-2: relational tables with Apache AGE
graph view for Cypher-based path traversal.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    pass


class KGNode(TimestampMixin, Base):
    """A node in the nuclear materials knowledge graph.

    Entity types include: material, property, value, crystal_structure,
    dataset, publication, author, experiment, composition, defect_mechanism.
    """

    __tablename__ = "kg_nodes"
    __table_args__ = (
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_kg_nodes_confidence",
        ),
        CheckConstraint(
            "entity_type IN ("
            "'material', 'property', 'value', 'crystal_structure', "
            "'dataset', 'publication', 'author', 'experiment', "
            "'composition', 'defect_mechanism', 'other')",
            name="ck_kg_nodes_entity_type",
        ),
        Index("idx_kg_nodes_type", "entity_type"),
        Index("idx_kg_nodes_name", "name"),
        Index("idx_kg_nodes_source", "source_document_id"),
        Index("idx_kg_nodes_figure", "figure_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(1000), nullable=False)
    label: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="JSON-encoded additional properties (ontology-specific)",
    )
    confidence_score: Mapped[float] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        doc="Extraction confidence 0.0–1.0",
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
        doc="Provenance: which source document this node was extracted from",
    )
    figure_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        doc="Provenance: which figure (if any) this node was extracted from",
    )
    extraction_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="'manual', 'ner_auto', 'llm_extracted', 'ontology_mapped'",
    )

    # -- relationships --
    outgoing_edges: Mapped[list["KGEdge"]] = relationship(
        back_populates="source_node",
        foreign_keys="KGEdge.source_id",
    )
    incoming_edges: Mapped[list["KGEdge"]] = relationship(
        back_populates="target_node",
        foreign_keys="KGEdge.target_id",
    )

    def __repr__(self) -> str:
        return f"<KGNode id={self.id!s} type={self.entity_type!r} name={self.name!r}>"


class KGEdge(TimestampMixin, Base):
    """A directed edge in the nuclear materials knowledge graph.

    Relation types include: has_property, has_value, has_structure,
    cited_by, composed_of, measured_by, authored_by, related_to,
    contains, exhibits, derives_from.
    """

    __tablename__ = "kg_edges"
    __table_args__ = (
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_kg_edges_confidence",
        ),
        CheckConstraint(
            "source_id != target_id",
            name="ck_kg_edges_no_self_loop",
        ),
        Index("idx_kg_edges_source", "source_id"),
        Index("idx_kg_edges_target", "target_id"),
        Index("idx_kg_edges_type", "relation_type"),
        Index(
            "idx_kg_edges_source_target",
            "source_id",
            "target_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    label: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )
    properties: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="JSON-encoded edge properties (weight, qualifiers, etc.)",
    )
    confidence_score: Mapped[float] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
        doc="Provenance: which document this edge was extracted from",
    )
    extraction_method: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # -- relationships --
    source_node: Mapped["KGNode"] = relationship(
        back_populates="outgoing_edges",
        foreign_keys=[source_id],
    )
    target_node: Mapped["KGNode"] = relationship(
        back_populates="incoming_edges",
        foreign_keys=[target_id],
    )

    def __repr__(self) -> str:
        return (
            f"<KGEdge id={self.id!s} "
            f"{self.source_id!s} --[{self.relation_type!r}]--> {self.target_id!s}>"
        )


class KGReviewQueue(TimestampMixin, Base):
    """Review queue for low-confidence KG entities and relations.

    Items with confidence < 0.6 are automatically queued for human
    review. Supports approve/reject workflows.
    """

    __tablename__ = "kg_review_queue"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ('node', 'edge')",
            name="ck_kg_review_queue_item_type",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'skipped')",
            name="ck_kg_review_queue_status",
        ),
        Index("idx_kg_review_status", "review_status"),
        Index("idx_kg_review_item", "item_type", "item_id"),
        Index("idx_kg_review_confidence", "confidence_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    item_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        doc="References kg_nodes.id or kg_edges.id depending on item_type",
    )
    confidence_score: Mapped[float] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    review_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        nullable=True,
        doc="User who reviewed this item",
    )
    review_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Reviewer's comment on the decision",
    )
    reviewed_at: Mapped[str | None] = mapped_column(
        nullable=True,
        doc="ISO timestamp of when the review was completed",
    )
    original_data: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="JSON snapshot of the original node/edge data at queue time",
    )

    def __repr__(self) -> str:
        return (
            f"<KGReviewQueue id={self.id!s} "
            f"type={self.item_type!r} status={self.review_status!r}>"
        )
