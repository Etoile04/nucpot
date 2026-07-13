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

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
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
from nfm_db.services.kg_utils import parse_aliases

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence threshold for review queue
# ---------------------------------------------------------------------------

REVIEW_CONFIDENCE_THRESHOLD: float = 0.6

# ---------------------------------------------------------------------------
# Data transfer objects (frozen / immutable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedEntity:
    """An entity extracted from a paper or data source."""

    label: str
    entity_type: str
    confidence: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    source_id: uuid.UUID | None = None
    aliases: list[str] = field(default_factory=list)


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


class EntityLinker:
    """Links extracted entities to existing KG nodes.

    Matching strategy:
    1. Exact label + node_type match
    2. Fuzzy alias match (case-insensitive substring)
    3. No match -> schedule new node creation
    """

    async def find_matching_node(
        self,
        session: AsyncSession,
        entity: ExtractedEntity,
        corpus_id: str | None = None,
    ) -> KGNode | None:
        """Find an existing KG node matching the given entity.

        Args:
            session: Async database session.
            entity: Extracted entity to match.
            corpus_id: Optional corpus filter.

        Returns:
            Matching KGNode or None if no match found.
        """
        # Strategy 1: Exact label + type match
        exact_match = await self._exact_label_match(
            session,
            entity.label,
            entity.entity_type,
            corpus_id,
        )
        if exact_match is not None:
            logger.debug(
                "Exact match for entity '%s' (%s) -> node %s",
                entity.label,
                entity.entity_type,
                exact_match.id,
            )
            return exact_match

        # Strategy 2: Fuzzy alias match
        alias_match = await self._fuzzy_alias_match(
            session,
            entity.label,
            corpus_id,
        )
        if alias_match is not None:
            logger.debug(
                "Alias match for entity '%s' -> node %s (label='%s')",
                entity.label,
                alias_match.id,
                alias_match.label,
            )
            return alias_match

        logger.debug("No match found for entity '%s' (%s)", entity.label, entity.entity_type)
        return None

    async def _exact_label_match(
        self,
        session: AsyncSession,
        label: str,
        node_type: str,
        corpus_id: str | None,
    ) -> KGNode | None:
        """Query for exact label + node_type match."""
        query = select(KGNode).where(
            KGNode.label == label,
            KGNode.node_type == node_type,
            KGNode.status == "active",
        )
        if corpus_id is not None:
            query = query.where(KGNode.corpus_id == corpus_id)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    async def _fuzzy_alias_match(
        self,
        session: AsyncSession,
        label: str,
        corpus_id: str | None,
    ) -> KGNode | None:
        """Query for active nodes whose aliases contain the label (case-insensitive)."""
        query = select(KGNode).where(
            KGNode.aliases.is_not(None),
            KGNode.status == "active",
        )
        if corpus_id is not None:
            query = query.where(KGNode.corpus_id == corpus_id)

        result = await session.execute(query)
        nodes = result.scalars().all()

        label_lower = label.lower().strip()
        for node in nodes:
            node_aliases = parse_aliases(node.aliases)
            if any(label_lower in alias.lower() for alias in node_aliases):
                return node

        return None


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

        # Phase 2: Entity linking
        node_map: dict[str, KGNode] = {}
        new_nodes: list[KGNode] = []
        new_edges: list[KGEdge] = []
        nodes_created = 0
        nodes_matched = 0
        review_count = 0

        for entity in entities:
            existing = await self._linker.find_matching_node(
                self._session,
                entity,
                self._corpus_id,
            )

            if existing is not None:
                node_map[entity.label] = existing
                nodes_matched += 1
            else:
                new_node = await self._create_node(entity)
                node_map[entity.label] = new_node
                new_nodes.append(new_node)
                nodes_created += 1

                if entity.confidence < REVIEW_CONFIDENCE_THRESHOLD:
                    await self._queue_for_review(
                        new_node.id,
                        "entity",
                        f"Low confidence entity: {entity.label} "
                        f"(confidence={entity.confidence:.2f})",
                    )
                    review_count += 1

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
            new_edges.append(edge)
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

        # Post-processing: fire-and-forget LightRAG auto-ingest (NFM-1222)
        if nodes_created > 0 or edges_created > 0:
            self._fire_lightrag_ingest(new_nodes, new_edges)

        return result

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

    async def _create_node(self, entity: ExtractedEntity) -> KGNode:
        """Create a new KGNode from an extracted entity."""
        node = KGNode(
            node_type=entity.entity_type,
            label=entity.label,
            aliases=json.dumps(entity.aliases) if entity.aliases else None,
            properties=dict(entity.properties),
            confidence=entity.confidence,
            source_id=entity.source_id,
            corpus_id=self._corpus_id,
            status="active"
            if entity.confidence >= REVIEW_CONFIDENCE_THRESHOLD
            else "pending_review",
        )
        self._session.add(node)

        if self._sync_to_age:
            try:
                from nfm_db.services.ontology_sync import sync_node

                await sync_node(self._session, node.id)
            except Exception:
                logger.warning(
                    "AGE sync failed for node %s (non-fatal)",
                    node.label,
                    exc_info=True,
                )

        return node

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
            created_at=datetime.utcnow(),
        )
        self._session.add(queue_entry)

    def _fire_lightrag_ingest(
        self,
        nodes: list[KGNode],
        edges: list[KGEdge],
    ) -> None:
        """Fire-and-forget: serialize and ingest new KG data to LightRAG.

        Non-blocking — failures are logged but never propagate.
        """
        try:
            from nfm_db.services.kg_lightrag_sync import fire_ingest_to_lightrag

            node_labels = {n.id: n.label for n in nodes}
            fire_ingest_to_lightrag(
                nodes=nodes,
                edges=edges,
                node_labels=node_labels,
            )
        except Exception:
            logger.warning(
                "Failed to schedule LightRAG ingest (non-fatal)",
                exc_info=True,
            )


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
    extract_figures: bool = False,
    extract_tables: bool = False,
    node_ids: list[uuid.UUID] | None = None,
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
