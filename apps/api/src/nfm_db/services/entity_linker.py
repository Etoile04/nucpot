"""Entity linking service (NFM-856 / B2.2).

Matches extracted entities against existing KG nodes and creates
new nodes for unmatched entities. Handles:
- Fuzzy name matching (Levenshtein distance, alias resolution)
- Property-based matching (CAS number, chemical formula)
- Deduplication within extraction batches
- Review queue routing for low-confidence matches (< 0.6)
- Provenance tracking per entity
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg_node import KGNode, KGProvenance, KGReviewQueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REVIEW_CONFIDENCE_THRESHOLD = 0.6
FUZZY_MATCH_THRESHOLD = 0.5
FUZZY_HIGH_CONFIDENCE = 0.9


# ---------------------------------------------------------------------------
# String similarity (Levenshtein-based)
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    """Normalize a string for comparison: lowercase, strip, unicode fold."""
    return unicodedata.normalize("NFKD", s.strip().lower())


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings.

    Pure Python implementation — no external dependencies needed.
    """
    n, m = len(s1), len(s2)
    if n == 0:
        return m
    if m == 0:
        return n

    # Use two-row optimization
    prev = list(range(m + 1))
    curr = [0] * (m + 1)

    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,       # insertion
                prev[j] + 1,             # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev, curr = curr, prev

    return prev[m]


def _levenshtein_similarity(s1: str, s2: str) -> float:
    """Return similarity ratio [0.0, 1.0] based on Levenshtein distance."""
    n = max(len(s1), len(s2))
    if n == 0:
        return 1.0
    dist = _levenshtein_distance(s1, s2)
    return 1.0 - (dist / n)


# ---------------------------------------------------------------------------
# Entity Linker
# ---------------------------------------------------------------------------


class EntityLinker:
    """Links extracted entities to existing KG nodes or creates new ones.

    Usage:
        linker = EntityLinker(session)
        result = await linker.link_entities(extracted_entities)
    """

    def __init__(
        self,
        session: AsyncSession | None = None,
        review_threshold: float = REVIEW_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._session = session
        self._review_threshold = review_threshold

    # ------------------------------------------------------------------
    # Deduplication (pure function, no DB)
    # ------------------------------------------------------------------

    def deduplicate_entities(
        self,
        entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge duplicate entities within an extraction batch.

        Identical and case-insensitive duplicates are merged.
        Properties are combined (union merge). Source IDs are collected.
        Highest confidence is kept.
        """
        if not entities:
            return []

        merged: dict[str, dict[str, Any]] = {}

        for entity in entities:
            key = _normalize(entity.get("name", ""))
            existing = merged.get(key)

            if existing is None:
                # First occurrence: create a new merged entry
                new_entity = dict(entity)
                new_entity["source_ids"] = [
                    entity.get("source_id", entity.get("source_ids", "unknown"))
                ]
                merged[key] = new_entity
            else:
                # Merge: keep highest confidence, union properties, collect sources
                existing_conf = existing.get("confidence", 0.0)
                new_conf = entity.get("confidence", 0.0)

                if new_conf > existing_conf:
                    merged_props = existing.get("properties", {})
                    new_props = entity.get("properties", {})
                    combined = {**merged_props, **new_props}
                    merged[key] = {
                        **existing,
                        **entity,
                        "name": entity["name"] if new_conf > existing_conf else existing["name"],
                        "confidence": new_conf,
                        "properties": combined,
                    }
                else:
                    existing_props = existing.get("properties", {})
                    new_props = entity.get("properties", {})
                    existing["properties"] = {**existing_props, **new_props}

                # Collect source IDs
                new_source = entity.get("source_id", entity.get("source_ids", "unknown"))
                existing_sources = existing.get("source_ids", [])
                if isinstance(new_source, list):
                    existing_sources.extend(new_source)
                elif new_source not in existing_sources:
                    existing_sources.append(new_source)
                existing["source_ids"] = existing_sources

        return list(merged.values())

    # ------------------------------------------------------------------
    # Matching (pure function, no DB)
    # ------------------------------------------------------------------

    def find_best_match(
        self,
        entity_name: str,
        kg_nodes: list[dict[str, Any]],
        entity_properties: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Find the best matching KG node for an extracted entity.

        Matching strategies (in priority order):
        1. CAS number match (confidence >= 0.95)
        2. Chemical formula match (confidence >= 0.95)
        3. Exact canonical name match (confidence = 1.0)
        4. Exact name or alias match (confidence >= 0.9)
        5. Fuzzy name match (confidence based on Levenshtein)

        Returns dict with 'node_id', 'confidence', 'match_type' or None.
        """
        if not kg_nodes:
            return None

        best_match: dict[str, Any] | None = None
        best_confidence = 0.0

        props = entity_properties or {}

        for node in kg_nodes:
            node_id = node.get("id")
            node_name = node.get("name", "")
            canonical = node.get("canonical_name", "")
            aliases = node.get("aliases", [])
            node_props = node.get("properties", {})

            # Strategy 1: CAS number match
            if (
                "cas_number" in props
                and "cas_number" in node_props
                and props["cas_number"].strip() == node_props["cas_number"].strip()
                and best_confidence < 0.95
            ):
                best_match = {
                    "node_id": node_id,
                    "confidence": 0.95,
                    "match_type": "cas_number",
                }
                best_confidence = 0.95

            # Strategy 2: Chemical formula match
            if (
                "chemical_formula" in props
                and "chemical_formula" in node_props
                and props["chemical_formula"].strip() == node_props["chemical_formula"].strip()
                and best_confidence < 0.95
            ):
                best_match = {
                    "node_id": node_id,
                    "confidence": 0.95,
                    "match_type": "chemical_formula",
                }
                best_confidence = 0.95

            # Strategy 3: Exact canonical name match
            if _normalize(entity_name) == _normalize(canonical):
                if best_confidence < 1.0:
                    best_match = {
                        "node_id": node_id,
                        "confidence": 1.0,
                        "match_type": "canonical_name",
                    }
                    best_confidence = 1.0
                continue

            # Strategy 4: Exact name or alias match
            if _normalize(entity_name) == _normalize(node_name):
                confidence = FUZZY_HIGH_CONFIDENCE
                if confidence > best_confidence:
                    best_match = {
                        "node_id": node_id,
                        "confidence": confidence,
                        "match_type": "exact_name",
                    }
                    best_confidence = confidence
                continue

            normalized_entity = _normalize(entity_name)
            for alias in aliases:
                if normalized_entity == _normalize(alias):
                    confidence = 0.85
                    if confidence > best_confidence:
                        best_match = {
                            "node_id": node_id,
                            "confidence": confidence,
                            "match_type": "alias",
                        }
                        best_confidence = confidence
                    break

        # Strategy 5: Fuzzy name match (if no exact match found)
        if best_confidence < FUZZY_MATCH_THRESHOLD:
            normalized_entity = _normalize(entity_name)
            for node in kg_nodes:
                node_name = _normalize(node.get("name", ""))
                canonical = _normalize(node.get("canonical_name", ""))
                similarity = max(
                    _levenshtein_similarity(normalized_entity, node_name),
                    _levenshtein_similarity(normalized_entity, canonical),
                )
                if similarity >= FUZZY_MATCH_THRESHOLD and similarity > best_confidence:
                    best_match = {
                        "node_id": node.get("id"),
                        "confidence": round(similarity, 2),
                        "match_type": "fuzzy_name",
                    }
                    best_confidence = similarity

        if best_confidence < FUZZY_MATCH_THRESHOLD:
            return None

        return best_match

    # ------------------------------------------------------------------
    # Node creation (requires DB)
    # ------------------------------------------------------------------

    async def create_unmatched_node(
        self,
        entity: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a new KG node for an unmatched entity with provenance."""
        if self._session is None:
            raise RuntimeError("EntityLinker requires a database session for create_unmatched_node")

        node = KGNode(
            name=entity.get("name", "Unknown"),
            canonical_name=entity.get("name", "Unknown"),
            node_type=entity.get("node_type", "material"),
            properties=entity.get("properties", {}),
            confidence_score=entity.get("confidence", 0.5),
        )
        self._session.add(node)
        await self._session.flush()
        await self._session.refresh(node)

        # Create provenance entry
        prov = KGProvenance(
            node_id=node.id,
            source_id=entity.get("source_id", "unknown"),
            source_type=entity.get("source_type", "unknown"),
            extracted_at=datetime.now(UTC),
        )
        self._session.add(prov)
        await self._session.flush()
        await self._session.refresh(prov)

        return {
            "action": "created",
            "node_id": node.id,
            "name": node.name,
            "provenance_id": prov.id,
        }

    # ------------------------------------------------------------------
    # Review queue routing (requires DB)
    # ------------------------------------------------------------------

    async def route_entity(
        self,
        entity: dict[str, Any],
        kg_node_dicts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Route an entity: match, create, or send to review queue."""
        if self._session is None:
            raise RuntimeError("EntityLinker requires a database session for route_entity")

        entity_name = entity.get("name", "")
        entity_confidence = entity.get("confidence", 0.5)
        entity_props = entity.get("properties", {})

        match = self.find_best_match(
            entity_name=entity_name,
            kg_nodes=kg_node_dicts,
            entity_properties=entity_props,
        )

        if match is not None and match["confidence"] >= self._review_threshold:
            return {
                "action": "match",
                "node_id": match["node_id"],
                "confidence": match["confidence"],
                "match_type": match["match_type"],
            }

        if match is not None:
            # Low confidence match → review queue
            node = KGNode(
                name=entity_name,
                canonical_name=entity_name,
                node_type=entity.get("node_type", "material"),
                properties=entity_props,
                confidence_score=entity_confidence,
            )
            self._session.add(node)
            await self._session.flush()
            await self._session.refresh(node)

            review = KGReviewQueue(
                node_id=node.id,
                entity_name=entity_name,
                match_candidate_id=match["node_id"],
                confidence=match["confidence"],
                status="pending",
                reason=f"Low confidence match ({match['confidence']:.2f}) below threshold {self._review_threshold}",
            )
            self._session.add(review)
            await self._session.flush()
            await self._session.refresh(review)

            return {
                "action": "review",
                "node_id": node.id,
                "confidence": match["confidence"],
                "review_id": review.id,
            }

        # No match at all → create new node
        return await self.create_unmatched_node(entity)

    # ------------------------------------------------------------------
    # Full orchestration
    # ------------------------------------------------------------------

    async def link_entities(
        self,
        extracted: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Full entity linking pipeline.

        1. Deduplicate extracted entities
        2. Load existing KG nodes
        3. Match, create, or route each entity
        4. Return summary statistics
        """
        if self._session is None:
            raise RuntimeError("EntityLinker requires a database session for link_entities")

        # Step 1: Deduplicate
        deduplicated = self.deduplicate_entities(extracted)

        # Step 2: Load existing KG nodes
        stmt = select(KGNode)
        result = await self._session.execute(stmt)
        existing_nodes = result.scalars().all()

        kg_node_dicts = [
            {
                "id": n.id,
                "name": n.name,
                "canonical_name": n.canonical_name,
                "aliases": n.aliases or [],
                "properties": n.properties or {},
            }
            for n in existing_nodes
        ]

        # Step 3: Match/create/route each deduplicated entity
        matched_count = 0
        created_count = 0
        review_count = 0

        for entity in deduplicated:
            result_dict = await self.route_entity(entity, kg_node_dicts)
            action = result_dict.get("action", "unknown")

            if action == "match":
                matched_count += 1
            elif action == "created":
                created_count += 1
            elif action == "review":
                review_count += 1

        await self._session.commit()

        return {
            "total_input": len(extracted),
            "deduplicated_count": len(deduplicated),
            "matched_count": matched_count,
            "created_count": created_count,
            "review_count": review_count,
        }
