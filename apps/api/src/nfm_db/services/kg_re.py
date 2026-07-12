"""Knowledge Graph Relation Extraction & Graph Builder service (NFM-984).

Extracts relationships between entities from extraction pipeline results,
creates KG nodes/edges, performs entity linking with deduplication, and
queues low-confidence items for human review.

Architecture:
  extraction_pipeline result -> RelationExtractor -> GraphBuilder
                                                     -> KGReviewQueue (< 0.6 confidence)
                                                     -> ontology_sync (AGE graph)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import (
    VALID_NODE_TYPES,
    VALID_RELATION_TYPES,
    KGEdge,
    KGNode,
    KGReviewQueue,
)
from nfm_db.services.entity_linker import (
    REVIEW_CONFIDENCE_THRESHOLD,
    EntityLinker,
    ExtractedEntity,
    LinkOutcome,
    dedup_entities,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data transfer objects (frozen / immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedRelation:
    """A relation extracted between two entities."""

    source_label: str
    source_type: str
    target_label: str
    target_type: str
    relation_type: str
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    source_id: uuid.UUID | None = None


@dataclass(frozen=True)
class BuildResult:
    """Summary of graph construction results."""

    nodes_created: int = 0
    nodes_matched: int = 0
    edges_created: int = 0
    review_queue_items: int = 0

    @property
    def total_nodes_processed(self) -> int:
        return self.nodes_created + self.nodes_matched


# ---------------------------------------------------------------------------
# Relation extraction
# ---------------------------------------------------------------------------


class RelationExtractor:
    """Identifies relationships between extracted entities.

    Applies domain heuristics to infer relations from entity types and
    co-occurrence patterns. For example:
    - Material + Property co-mentioned -> hasProperty
    - Experiment + Material co-mentioned -> measuredIn
    - Material + Material co-mentioned -> relatedTo
    """

    # Mapping of (source_type, target_type) -> candidate relation types
    _TYPE_PAIR_RULES: dict[tuple[str, str], list[str]] = {
        ("Material", "Property"): ["hasProperty"],
        ("Material", "Material"): ["relatedTo"],
        ("Experiment", "Material"): ["measuredIn"],
        ("Experiment", "Condition"): ["hasCondition"],
        ("Publication", "Publication"): ["cites"],
    }

    def extract_relations(
        self,
        entities: list[ExtractedEntity],
    ) -> list[ExtractedRelation]:
        """Extract relations from a list of co-occurring entities.

        Scans all entity pairs and applies type-pair rules to determine
        candidate relations. Confidence is the geometric mean of both
        entity confidences.

        Args:
            entities: List of extracted entities from a single document.

        Returns:
            List of inferred relations with confidence scores.
        """
        relations: list[ExtractedRelation] = []

        for i, source in enumerate(entities):
            for target in entities[i + 1 :]:
                candidates = self._find_candidate_relations(source, target)
                for relation_type in candidates:
                    confidence = self._compute_relation_confidence(
                        source.confidence,
                        target.confidence,
                    )
                    relations.append(
                        ExtractedRelation(
                            source_label=source.label,
                            source_type=source.entity_type,
                            target_label=target.label,
                            target_type=target.entity_type,
                            relation_type=relation_type,
                            confidence=confidence,
                            properties=self._build_relation_properties(source, target),
                            source_id=source.source_id,
                        )
                    )

        logger.info(
            "Extracted %d relations from %d entities",
            len(relations),
            len(entities),
        )
        return relations

    def _find_candidate_relations(
        self,
        source: ExtractedEntity,
        target: ExtractedEntity,
    ) -> list[str]:
        """Find candidate relation types for an entity pair.

        Checks both (source, target) and (target, source) direction
        against the type-pair rule table.
        """
        candidates: list[str] = []

        forward = (source.entity_type, target.entity_type)
        reverse = (target.entity_type, source.entity_type)

        if forward in self._TYPE_PAIR_RULES:
            candidates.extend(self._TYPE_PAIR_RULES[forward])
        if reverse in self._TYPE_PAIR_RULES:
            # Reverse the pair but keep the original direction labels
            # The caller decides direction based on entity order
            candidates.extend(self._TYPE_PAIR_RULES[reverse])

        # Validate against known relation types
        return [rt for rt in candidates if rt in VALID_RELATION_TYPES]

    @staticmethod
    def _compute_relation_confidence(
        source_confidence: float,
        target_confidence: float,
    ) -> float:
        """Compute relation confidence as geometric mean of entity confidences."""
        if source_confidence <= 0 or target_confidence <= 0:
            return 0.0
        return float((source_confidence * target_confidence) ** 0.5)

    @staticmethod
    def _build_relation_properties(
        source: ExtractedEntity,
        target: ExtractedEntity,
    ) -> dict[str, Any]:
        """Build metadata properties for a relation."""
        return {
            "source_entity_type": source.entity_type,
            "target_entity_type": target.entity_type,
            "extraction_method": "heuristic_type_pair",
        }


# ---------------------------------------------------------------------------
# Entity linking
# ---------------------------------------------------------------------------
#
# The EntityLinker implementation moved to ``nfm_db.services.entity_linker``
# (NFM-856 [B2.2]). This module re-uses the new module-level
# EntityLinker, ExtractedEntity, REVIEW_CONFIDENCE_THRESHOLD, and the
# dedup_entities helper imported at the top of the file.


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


class GraphBuilder:
    """Orchestrates creation of KG nodes and edges from extraction results.

    Responsibilities:
    - Entity linking: Match extracted entities to existing nodes
    - Node creation: Create new nodes for unmatched entities
    - Edge creation: Create edges from extracted relations
    - Review queue: Route low-confidence items (< 0.6) to KGReviewQueue
    - AGE sync: Trigger ontology_sync for newly created nodes/edges
    """

    def __init__(
        self,
        session: AsyncSession,
        corpus_id: str | None = None,
        sync_to_age: bool = True,
    ) -> None:
        self._session = session
        self._corpus_id = corpus_id
        self._sync_to_age = sync_to_age
        self._extractor = RelationExtractor()
        self._linker = EntityLinker()

    async def build_from_extraction(
        self,
        extracted_properties: list[dict[str, Any]],
        source_id: uuid.UUID | None = None,
    ) -> BuildResult:
        """Build KG nodes and edges from extraction pipeline results.

        Args:
            extracted_properties: Raw property dicts from extraction_pipeline.
            source_id: Optional data source ID for provenance.

        Returns:
            BuildResult summary with counts.
        """
        # Phase 1: Convert extraction results to entities
        entities = self._convert_to_entities(extracted_properties, source_id)
        if not entities:
            logger.info("No entities to build graph from")
            return BuildResult()

        # Phase 1.5: Within-batch deduplication (merges same-label+type
        # duplicates, keeping the highest confidence and collecting
        # every source_id into provenance_sources).
        entities = dedup_entities(entities)

        # Phase 2: Entity linking — delegated to EntityLinker.find_or_link
        # which handles match / create / update / review-queue routing.
        node_map: dict[str, KGNode] = {}
        nodes_created = 0
        nodes_matched = 0
        review_count = 0

        for entity in entities:
            link_result = await self._linker.find_or_link(
                self._session,
                entity,
                corpus_id=self._corpus_id,
            )

            canonical = link_result.node
            if canonical is None:
                # Defensive: find_or_link always returns a node, but
                # guard against future implementation changes.
                logger.warning(
                    "EntityLinker returned no node for label=%s; skipping",
                    entity.label,
                )
                continue

            node_map[entity.label] = canonical

            if link_result.outcome is LinkOutcome.CREATED:
                nodes_created += 1
                if self._sync_to_age:
                    await self._sync_new_node_to_age(canonical)
            elif link_result.outcome is LinkOutcome.MATCHED:
                nodes_matched += 1
            elif link_result.outcome is LinkOutcome.NEEDS_REVIEW:
                # Review-queue row was already created by EntityLinker.
                review_count += 1
                if link_result.matched_node is not None:
                    nodes_matched += 1
                else:
                    nodes_created += 1
                    if self._sync_to_age:
                        await self._sync_new_node_to_age(canonical)

        # Phase 3: Relation extraction and edge creation
        relations = self._extractor.extract_relations(entities)
        edges_created = 0

        for relation in relations:
            source_node = node_map.get(relation.source_label)
            target_node = node_map.get(relation.target_label)

            if source_node is None or target_node is None:
                logger.warning(
                    "Skipping relation %s -> %s: missing node(s)",
                    relation.source_label,
                    relation.target_label,
                )
                continue

            edge = await self._create_edge(relation, source_node.id, target_node.id)
            edges_created += 1

            if relation.confidence < REVIEW_CONFIDENCE_THRESHOLD:
                await self._queue_for_review(
                    edge.id,
                    "relation",
                    f"Low confidence relation: "
                    f"{relation.source_label} -[{relation.relation_type}]-> "
                    f"{relation.target_label} (confidence={relation.confidence:.2f})",
                )
                review_count += 1

        await self._session.flush()

        result = BuildResult(
            nodes_created=nodes_created,
            nodes_matched=nodes_matched,
            edges_created=edges_created,
            review_queue_items=review_count,
        )

        logger.info(
            "Graph build complete: %d created, %d matched, %d edges, %d queued",
            result.nodes_created,
            result.nodes_matched,
            result.edges_created,
            result.review_queue_items,
        )
        return result

    async def _sync_new_node_to_age(self, node: KGNode) -> None:
        """Best-effort sync of a newly created node to the AGE graph."""
        try:
            from nfm_db.services.ontology_sync import sync_node

            await sync_node(self._session, node.id)
        except Exception:
            logger.warning(
                "AGE sync failed for node %s (non-fatal)",
                node.label,
                exc_info=True,
            )

    def _convert_to_entities(
        self,
        properties: list[dict[str, Any]],
        source_id: uuid.UUID | None,
    ) -> list[ExtractedEntity]:
        """Convert extraction pipeline property dicts to ExtractedEntity list.

        Heuristically maps extraction fields to KG entity types.
        """
        seen_labels: set[str] = set()
        entities: list[ExtractedEntity] = []

        for prop in properties:
            # Material entity
            material_name = (
                prop.get("material_name") or prop.get("composition") or prop.get("element_system")
            )
            if material_name and material_name not in seen_labels:
                seen_labels.add(material_name)
                entities.append(
                    ExtractedEntity(
                        label=str(material_name),
                        entity_type="Material",
                        confidence=_confidence_to_float(prop.get("confidence")),
                        properties={"source_file": prop.get("source_file")},
                        source_id=source_id,
                    )
                )

            # Property entity
            prop_name = prop.get("property") or prop.get("property_name")
            if prop_name and prop_name not in seen_labels:
                seen_labels.add(prop_name)
                entities.append(
                    ExtractedEntity(
                        label=str(prop_name),
                        entity_type="Property",
                        confidence=_confidence_to_float(prop.get("confidence")),
                        properties={
                            "unit": prop.get("unit"),
                            "value": prop.get("value"),
                        },
                        source_id=source_id,
                    )
                )

            # Condition entity (from temperature, pressure, etc.)
            conditions = prop.get("conditions")
            if isinstance(conditions, dict):
                for cond_key, cond_value in conditions.items():
                    cond_label = f"{cond_key}={cond_value}"
                    if cond_label not in seen_labels:
                        seen_labels.add(cond_label)
                        entities.append(
                            ExtractedEntity(
                                label=cond_label,
                                entity_type="Condition",
                                confidence=_confidence_to_float(prop.get("confidence")) * 0.8,
                                properties={
                                    "condition_key": cond_key,
                                    "condition_value": str(cond_value),
                                },
                                source_id=source_id,
                            )
                        )

            # Experiment entity (from method)
            method = prop.get("method")
            if method and method not in seen_labels:
                seen_labels.add(method)
                entities.append(
                    ExtractedEntity(
                        label=str(method),
                        entity_type="Experiment",
                        confidence=_confidence_to_float(prop.get("confidence")),
                        properties={},
                        source_id=source_id,
                    )
                )

        return entities

    async def _create_edge(
        self,
        relation: ExtractedRelation,
        source_node_id: uuid.UUID,
        target_node_id: uuid.UUID,
    ) -> KGEdge:
        """Create a new KGEdge from an extracted relation."""
        edge = KGEdge(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation.relation_type,
            properties=dict(relation.properties),
            confidence=relation.confidence,
            source_id=relation.source_id,
            corpus_id=self._corpus_id,
        )
        self._session.add(edge)

        if self._sync_to_age:
            try:
                from nfm_db.services.ontology_sync import sync_edge

                await sync_edge(self._session, edge.id)
            except Exception:
                logger.warning(
                    "AGE sync failed for edge (non-fatal)",
                    exc_info=True,
                )

        return edge

    async def _queue_for_review(
        self,
        item_id: uuid.UUID,
        item_type: str,
        review_reason: str,
    ) -> None:
        """Add a low-confidence item to the review queue."""
        queue_entry = KGReviewQueue(
            item_type=item_type,
            item_id=item_id,
            review_reason=review_reason,
            status="pending",
            created_at=datetime.now(UTC),
        )
        self._session.add(queue_entry)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _confidence_to_float(raw_confidence: Any) -> float:
    """Convert a raw confidence value (string or number) to float 0-1.

    Mapping: 'high' -> 0.9, 'medium' -> 0.6, 'low' -> 0.3.
    Numeric values are clamped to [0, 1].
    """
    if isinstance(raw_confidence, (int, float)):
        return max(0.0, min(1.0, float(raw_confidence)))

    if isinstance(raw_confidence, str):
        mapping = {"high": 0.9, "medium": 0.6, "low": 0.3}
        return mapping.get(raw_confidence.lower(), 0.6)

    return 0.6


# ---------------------------------------------------------------------------
# Query functions (read-only — used by MCP tools and external services)
# ---------------------------------------------------------------------------


# Default limits and ceilings for query functions.
_QUERY_DEFAULT_LIMIT: int = 50
_QUERY_MAX_LIMIT: int = 500


async def query_graph_nodes(
    session: AsyncSession,
    *,
    entity_types: list[str] | None = None,
    query: str | None = None,
    limit: int = _QUERY_DEFAULT_LIMIT,
    corpus_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query KG nodes with optional filters.

    Filters (all AND-combined):
      - ``entity_types``: only include nodes whose ``node_type`` is in
        the given list (PascalCase: Material, Property, Experiment,
        Condition, Publication). Unknown values are silently dropped.
      - ``query``: case-insensitive substring match against ``label``.
      - ``corpus_id``: filter to a specific corpus; ``None`` returns all.

    Args:
        session: Async database session.
        entity_types: Optional whitelist of node types.
        query: Optional free-text search term.
        limit: Maximum number of rows to return (default 50, max 500).
        corpus_id: Optional corpus filter.

    Returns:
        List of dicts with keys: id, node_type, label, confidence,
        status, properties.  Empty list when no matches.
    """
    safe_limit = max(1, min(int(limit), _QUERY_MAX_LIMIT))

    stmt = select(KGNode).where(KGNode.status != "deprecated")

    if entity_types:
        valid_types = [t for t in entity_types if t in VALID_NODE_TYPES]
        if valid_types:
            stmt = stmt.where(KGNode.node_type.in_(valid_types))
        else:
            # All requested types are invalid -> return empty result
            return []

    if corpus_id is not None:
        stmt = stmt.where(KGNode.corpus_id == corpus_id)

    if query:
        pattern = f"%{query.lower()}%"
        # Match against label (case-insensitive substring).
        stmt = stmt.where(KGNode.label.ilike(pattern))

    stmt = stmt.order_by(KGNode.confidence.desc()).limit(safe_limit)

    result = await session.execute(stmt)
    nodes = result.scalars().all()

    return [
        {
            "id": str(node.id),
            "node_type": node.node_type,
            "label": node.label,
            "confidence": float(node.confidence),
            "status": node.status,
            "properties": dict(node.properties or {}),
        }
        for node in nodes
    ]


async def query_graph_edges(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    relation_type: str | None = None,
    limit: int = _QUERY_DEFAULT_LIMIT,
    corpus_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query KG edges with optional filters.

    Filters (all AND-combined):
      - ``source_id``: only edges originating from this node id.
      - ``target_id``: only edges pointing at this node id.
      - ``relation_type``: only edges of this type (e.g. ``hasProperty``).
      - ``corpus_id``: filter to a specific corpus; ``None`` returns all.

    Args:
        session: Async database session.
        source_id: Optional source node UUID.
        target_id: Optional target node UUID.
        relation_type: Optional relation type whitelist entry.
        limit: Maximum number of rows to return (default 50, max 500).
        corpus_id: Optional corpus filter.

    Returns:
        List of dicts with keys: id, source_node_id, target_node_id,
        relation_type, confidence, properties.  Empty list when no
        matches.
    """
    safe_limit = max(1, min(int(limit), _QUERY_MAX_LIMIT))

    stmt = select(KGEdge)

    if source_id is not None:
        stmt = stmt.where(KGEdge.source_node_id == source_id)
    if target_id is not None:
        stmt = stmt.where(KGEdge.target_node_id == target_id)
    if relation_type is not None:
        stmt = stmt.where(KGEdge.relation_type == relation_type)
    if corpus_id is not None:
        stmt = stmt.where(KGEdge.corpus_id == corpus_id)

    stmt = stmt.order_by(KGEdge.confidence.desc()).limit(safe_limit)

    result = await session.execute(stmt)
    edges = result.scalars().all()

    return [
        {
            "id": str(edge.id),
            "source_node_id": str(edge.source_node_id),
            "target_node_id": str(edge.target_node_id),
            "relation_type": edge.relation_type,
            "confidence": float(edge.confidence),
            "properties": dict(edge.properties or {}),
        }
        for edge in edges
    ]
