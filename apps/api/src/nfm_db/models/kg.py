"""Knowledge Graph ORM models.

Phase 2B tables: kg_nodes, kg_edges, kg_review_queue, ontology_id_map.
Stores extracted knowledge graph entities and their relationships,
with provenance tracking, confidence scores, and a human review queue.
Supports multi-corpus ontology via corpus_id and OntologyIdMap.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nfm_db.models import Base, TimestampMixin

if TYPE_CHECKING:
    from nfm_db.models.extraction_figure import ExtractionFigure
    from nfm_db.models.source import DataSource


class KGNode(TimestampMixin, Base):
    """A knowledge graph node representing an entity.

    Entity types: Material, Property, Experiment, Condition, Publication, Measurement.
    """

    ENTITY_TYPES: tuple[str, ...] = (
        "material",
        "property",
        "value",
        "crystal_structure",
        "dataset",
        "publication",
        "author",
        "experiment",
        "composition",
        "defect_mechanism",
        "other",
    )

    __tablename__ = "kg_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('Material', 'Property', 'Experiment', "
            "'Condition', 'Publication', 'Measurement')",
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
    properties: Mapped[dict[str, Any]] = mapped_column(
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
    figure_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extraction_figures.id", ondelete="SET NULL"),
        nullable=True,
        comment="Associated extraction figure (spec §6.2)",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
    )
    corpus_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Multi-corpus identifier; null = default corpus",
    )
    synced_to_graph: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether this node has been synced to AGE graph",
    )
    graph_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last AGE graph sync",
    )

    # -- relationships --
    source: Mapped["DataSource | None"] = relationship()
    figure: Mapped["ExtractionFigure | None"] = relationship()
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

    Relation types: hasProperty, measuredIn, relatedTo, cites, hasCondition,
    publishedIn, containsData, synthesizedBy, alloyOf, irradiatedIn,
    testedAt, references, derivedFrom.
    """

    RELATION_TYPES: tuple[str, ...] = (
        "has_property",
        "has_value",
        "has_structure",
        "cited_by",
        "composed_of",
        "measured_by",
        "authored_by",
        "related_to",
        "contains",
        "exhibits",
        "derives_from",
    )

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
    properties: Mapped[dict[str, Any]] = mapped_column(
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
    corpus_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Multi-corpus identifier; null = default corpus",
    )
    synced_to_graph: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether this edge has been synced to AGE graph",
    )
    graph_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last AGE graph sync",
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
    {
        "hasProperty",
        "measuredIn",
        "relatedTo",
        "cites",
        "hasCondition",
        "publishedIn",
        "containsData",
        "synthesizedBy",
        "alloyOf",
        "irradiatedIn",
        "testedAt",
        "references",
        "derivedFrom",
    }
)

VALID_NODE_TYPES: frozenset[str] = frozenset(
    {
        "Material",
        "Property",
        "Experiment",
        "Condition",
        "Publication",
        "Measurement",
    }
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
        server_default=func.now(),
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


class OntologyIdMap(Base):
    """Maps external ontology IDs (e.g. NVL v1.1) to internal KG nodes.

    Composite PK: (nvl_id, corpus_id) ensures one mapping per
    external identifier per corpus.  node_id is an FK to kg_nodes
    so the mapped KG node can be resolved in a single join.
    """

    __tablename__ = "ontology_id_map"
    __table_args__ = (
        UniqueConstraint(
            "nvl_id",
            "corpus_id",
            name="uq_ontology_id_map_nvl_corpus",
        ),
        Index("ix_ontology_id_map_node_id", "node_id"),
    )

    nvl_id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        comment="External ontology identifier (e.g. NVL v1.1 ID)",
    )
    corpus_id: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        comment="Corpus this mapping belongs to",
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"),
        nullable=False,
        comment="Internal KG node this external ID resolves to",
    )
    graph_label: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Label in the AGE graph (may differ from KGNode label)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # -- relationships --
    node: Mapped["KGNode"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<OntologyIdMap nvl_id={self.nvl_id!r} "
            f"corpus={self.corpus_id!r} node={self.node_id!s}>"
        )
