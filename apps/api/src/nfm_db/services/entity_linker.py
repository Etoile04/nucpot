"""Entity linking service (NFM-856 [B2.2]).

Matches extracted entities against existing KG nodes and creates new
nodes for unmatched entities. Implements the B2.2 spec from the
multimodal extraction + knowledge graph project.

Matching strategies (applied in order):
    1. Exact label + node_type match
    2. Alias match (case-insensitive substring against KGNode.aliases)
    3. Property-based match (CAS number, chemical_formula)
    4. Fuzzy name match (Levenshtein distance ratio >= FUZZY_MATCH_THRESHOLD)

When a match is found, the existing KG node is updated with the new
entity's data:
    * aliases unioned (case-insensitive dedup)
    * properties merged (new keys added; existing keys preserved)
    * confidence = max(existing, new)
    * source_id appended to node.properties['provenance_sources']

When no match is found, a new KG node is created with the entity's data.

Low-confidence entities (confidence < REVIEW_CONFIDENCE_THRESHOLD) are
routed to the kg_review_queue regardless of match outcome, and newly
created nodes for low-confidence entities get status='pending_review'.

The module also provides within-batch deduplication helpers:
    * dedup_entities(): merge duplicate (label, entity_type) entries
      in a single extraction, keeping the highest confidence and
      collecting every source_id into provenance_sources.

Pure functions (no DB / I/O) are deliberately placed at the top so
they are trivially testable in isolation.
"""

from __future__ import annotations

import enum
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import VALID_NODE_TYPES, KGNode, KGReviewQueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level thresholds
# ---------------------------------------------------------------------------

#: Confidence below which an entity is routed to the review queue.
REVIEW_CONFIDENCE_THRESHOLD: float = 0.6

#: Minimum Levenshtein ratio to consider two names a fuzzy match.
FUZZY_MATCH_THRESHOLD: float = 0.70

#: Within-batch dedup threshold (reserved for future fuzzy in-batch dedup).
DUPLICATE_LABEL_THRESHOLD: float = 0.85

#: Property keys used for property-based matching (strategy 3).
PROPERTY_MATCH_KEYS: frozenset[str] = frozenset({"cas_number", "chemical_formula"})

#: Property key under which source provenance is tracked on a KG node.
PROVENANCE_SOURCES_KEY: str = "provenance_sources"


# ---------------------------------------------------------------------------
# Pure helpers (no DB / I/O)
# ---------------------------------------------------------------------------


def levenshtein_distance(a: str, b: str) -> int:
    """Return the edit distance between ``a`` and ``b``.

    Classic DP implementation with O(len(a) * len(b)) time and O(min(...))
    space (single rolling row). Pure Python — no native dependency required.

    Symmetric: ``levenshtein_distance(a, b) == levenshtein_distance(b, a)``.
    Distance from an empty string equals the length of the other.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # Ensure b is the shorter string for memory efficiency.
    if len(a) < len(b):
        a, b = b, a

    previous_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current_row = [i]
        for j, cb in enumerate(b, start=1):
            insertions = previous_row[j] + 1
            deletions = current_row[j - 1] + 1
            substitutions = previous_row[j - 1] + (0 if ca == cb else 1)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def levenshtein_ratio(a: str, b: str) -> float:
    """Return Levenshtein similarity ratio in [0.0, 1.0].

    Symmetric normalization against the **sum** of lengths:

        ``ratio = 1 - (2 * distance) / (len(a) + len(b))``

    This is more discriminating than ``1 - distance / max(len)``: two
    equally distant strings on very different lengths still score
    *very* differently, which matches the intuition that matching a
    short token against a much longer one is a weaker signal.

    Returns 1.0 for identical strings, 0.0 when either input is empty
    and the other is not, and a value in (0, 1) otherwise.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    total = len(a) + len(b)
    if total == 0:
        return 1.0
    distance = levenshtein_distance(a, b)
    ratio = 1.0 - (2.0 * distance / total)
    if ratio < 0.0:
        return 0.0
    if ratio > 1.0:
        return 1.0
    return ratio


def merge_alias_lists(
    existing: list[str] | None,
    new: list[str] | None,
) -> list[str]:
    """Return a new list with ``new`` aliases merged into ``existing``.

    Dedup is case-insensitive across **both** inputs and preserves the
    first observed casing for any duplicate. Order: existing entries
    first (in their original order), then genuinely-new aliases
    appended.
    """
    result: list[str] = []
    seen_lower: set[str] = set()
    for alias in list(existing or []) + list(new or []):
        key = alias.lower()
        if key not in seen_lower:
            result.append(alias)
            seen_lower.add(key)
    return result


def merge_node_properties(
    existing: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge new property keys into ``existing`` without overwriting.

    Behavior:
        * Existing keys are preserved verbatim (new values for existing
          keys are ignored — prevents silently clobbering curated data).
        * New keys (not in ``existing``) are appended.
        * The special key ``provenance_sources`` is always a list;
          entries from both sides are deduped (UUID strings).

    Returns a new dict; inputs are not mutated.
    """
    merged: dict[str, Any] = dict(existing or {})
    for key, value in (new or {}).items():
        if key == PROVENANCE_SOURCES_KEY:
            existing_sources = list(merged.get(PROVENANCE_SOURCES_KEY) or [])
            seen = {str(s) for s in existing_sources}
            for source in value or []:
                token = str(source)
                if token not in seen:
                    existing_sources.append(token)
                    seen.add(token)
            merged[PROVENANCE_SOURCES_KEY] = existing_sources
        elif key not in merged:
            merged[key] = value
        # else: key already present, preserve existing value
    return merged


def dedup_entities(
    entities: list[ExtractedEntity],
) -> list[ExtractedEntity]:
    """Collapse duplicate (label, entity_type) entries within a single batch.

    For each group of duplicates:
        * Keep one entity with the highest confidence.
        * Union every alias from the duplicates onto the survivor.
        * Collect every non-None source_id into ``provenance_sources``.

    Entities with different labels or different entity_types are never
    merged (a Material and a Property that share a label stay separate).

    This is a within-batch dedup; cross-batch dedup happens in the
    DB-backed ``EntityLinker.find_or_link`` path.
    """
    if not entities:
        return []
    if len(entities) == 1:
        return list(entities)

    grouped: dict[tuple[str, str], list[ExtractedEntity]] = {}
    for entity in entities:
        key = (entity.label, entity.entity_type)
        grouped.setdefault(key, []).append(entity)

    deduped: list[ExtractedEntity] = []
    for group in grouped.values():
        if len(group) == 1:
            deduped.append(group[0])
            continue

        # Survivor = highest confidence; tie-break by first occurrence.
        survivor = max(group, key=lambda e: e.confidence)
        merged_aliases = merge_alias_lists(
            list(survivor.aliases),
            [a for other in group if other is not survivor for a in other.aliases],
        )
        collected_sources = list(survivor.provenance_sources)
        if survivor.source_id is not None:
            collected_sources.append(survivor.source_id)
        for other in group:
            if other is survivor:
                continue
            if other.source_id is not None:
                collected_sources.append(other.source_id)
            for src in other.provenance_sources:
                if src not in collected_sources:
                    collected_sources.append(src)

        deduped.append(
            ExtractedEntity(
                label=survivor.label,
                entity_type=survivor.entity_type,
                confidence=survivor.confidence,
                properties=dict(survivor.properties),
                source_id=survivor.source_id,
                aliases=merged_aliases,
                provenance_sources=list(dict.fromkeys(collected_sources)),
            )
        )
    return deduped


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedEntity:
    """A single entity produced by the upstream relation extractor.

    Immutable so the same instance can be safely passed through the
    within-batch dedup pass before being handed to ``EntityLinker``.
    """

    label: str
    entity_type: str
    confidence: float
    properties: dict[str, Any] = field(default_factory=dict)
    source_id: uuid.UUID | None = None
    aliases: list[str] = field(default_factory=list)
    provenance_sources: list[uuid.UUID] = field(default_factory=list)


class LinkOutcome(str, enum.Enum):
    """What happened during ``EntityLinker.find_or_link``."""

    #: Existing KG node matched and updated.
    MATCHED = "matched"
    #: No match found; a brand-new KG node was created.
    CREATED = "created"
    #: Matched or created but confidence below review threshold;
    #: a ``KGReviewQueue`` row was inserted.
    NEEDS_REVIEW = "needs_review"


@dataclass(frozen=True)
class LinkResult:
    """Outcome of linking a single extracted entity to the KG."""

    outcome: LinkOutcome
    #: The canonical KG node (matched existing or newly created).
    node: KGNode | None = None
    #: The matched existing node, or ``None`` when ``outcome`` is CREATED.
    matched_node: KGNode | None = None
    #: ID of the ``KGReviewQueue`` row when routed for review, else None.
    review_queue_id: uuid.UUID | None = None
    #: Final confidence recorded on the node (max(existing, entity) on match,
    #: entity.confidence on create, or the strategy confidence score).
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# EntityLinker
# ---------------------------------------------------------------------------


def _load_json_field(raw: Any) -> dict[str, Any] | list[Any] | None:
    """Decode a JSON text column to its Python object, returning ``None``
    on missing/empty/null strings.

    Used to read ``KGNode.aliases`` and ``KGNode.properties`` (stored as
    JSON text for SQLite portability).
    """
    if raw is None or raw == "" or raw == "null":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    # json.loads returns Any; narrow to the declared return type.
    return cast("dict[str, Any] | list[Any] | None", decoded)


def _parse_aliases(raw: Any) -> list[str]:
    """Read a KGNode aliases column (Text / JSON text) into a ``list[str]``."""
    decoded = _load_json_field(raw)
    if decoded is None:
        return []
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    return []


def _parse_properties(raw: Any) -> dict[str, Any]:
    """Read a KGNode properties column into a ``dict[str, Any]``.

    The column is mapped as SQLAlchemy ``JSON``, so the attribute is
    already a dict on read in normal flows. Fall back to JSON decoding
    if the value comes through as a string (e.g. via raw SQL or a
    non-default dialect).
    """
    if isinstance(raw, dict):
        return raw
    decoded = _load_json_field(raw)
    if isinstance(decoded, dict):
        return decoded
    return {}


class EntityLinker:
    """Match an ``ExtractedEntity`` against existing KG nodes.

    The matching strategies are applied in fixed order (see module
    docstring). The first hit wins; ties are broken by highest node
    confidence.

    Thresholds are configurable per instance so callers can tune for
    specific domains without touching module-level constants.
    """

    def __init__(
        self,
        *,
        fuzzy_threshold: float = FUZZY_MATCH_THRESHOLD,
        review_threshold: float = REVIEW_CONFIDENCE_THRESHOLD,
    ) -> None:
        if not 0.0 < fuzzy_threshold <= 1.0:
            raise ValueError("fuzzy_threshold must be in (0.0, 1.0]")
        if not 0.0 <= review_threshold <= 1.0:
            raise ValueError("review_threshold must be in [0.0, 1.0]")
        self._fuzzy_threshold = fuzzy_threshold
        self._review_threshold = review_threshold

    # -- public properties for introspection ----------------------------

    @property
    def fuzzy_threshold(self) -> float:
        return self._fuzzy_threshold

    @property
    def review_threshold(self) -> float:
        return self._review_threshold

    # -- main entry point -----------------------------------------------

    async def find_or_link(
        self,
        session: AsyncSession,
        entity: ExtractedEntity,
        *,
        corpus_id: str | None = None,
    ) -> LinkResult:
        """Find an existing KG node for ``entity`` or create a new one.

        Returns a ``LinkResult`` describing the outcome. The session
        is mutated (nodes and review-queue rows added) but *not*
        committed — callers control the transaction boundary.
        """
        if entity.entity_type not in VALID_NODE_TYPES:
            raise ValueError(
                f"Unknown entity_type {entity.entity_type!r}; "
                f"valid types: {sorted(VALID_NODE_TYPES)}"
            )

        candidates = await self._fetch_candidates(session, entity, corpus_id)

        matched, strategy_confidence = self._match_strategies(entity, candidates)

        if matched is not None:
            return await self._handle_match(
                session,
                entity,
                matched,
                strategy_confidence,
                corpus_id,
            )
        return await self._handle_create(session, entity, corpus_id)

    # -- candidate fetch ------------------------------------------------

    async def _fetch_candidates(
        self,
        session: AsyncSession,
        entity: ExtractedEntity,
        corpus_id: str | None,
    ) -> list[KGNode]:
        """Fetch candidate KG nodes for matching.

        Scoped to the same ``node_type`` as the entity. Corpus scoping
        is best-effort: if ``corpus_id`` is provided we restrict the
        scan; otherwise the whole table is considered.
        """
        stmt = select(KGNode).where(KGNode.node_type == entity.entity_type)
        if corpus_id is not None:
            stmt = stmt.where(KGNode.corpus_id == corpus_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # -- strategy chain -------------------------------------------------

    def _match_strategies(
        self,
        entity: ExtractedEntity,
        candidates: list[KGNode],
    ) -> tuple[KGNode | None, float]:
        """Run the four matching strategies in order. Returns
        ``(node, strategy_confidence)`` or ``(None, 0.0)`` on no hit.
        """
        # Strategy 1: exact label match (case-insensitive).
        label_lower = entity.label.strip().lower()
        for node in candidates:
            if (node.label or "").strip().lower() == label_lower:
                return node, 1.0

        # Strategy 2: alias match. The entity label is contained in any
        # of the candidate's aliases (case-insensitive substring).
        if label_lower:
            for node in candidates:
                for alias in _parse_aliases(node.aliases):
                    if label_lower in alias.lower():
                        return node, 0.95

        # Strategy 3: property match (CAS number or chemical formula).
        prop_match = self._match_by_property(entity, candidates)
        if prop_match is not None:
            return prop_match, 0.95

        # Strategy 4: fuzzy match (Levenshtein ratio >= threshold).
        if label_lower:
            best_node: KGNode | None = None
            best_ratio = 0.0
            for node in candidates:
                candidate_label = (node.label or "").strip().lower()
                if not candidate_label:
                    continue
                ratio = levenshtein_ratio(label_lower, candidate_label)
                if ratio >= self._fuzzy_threshold and ratio > best_ratio:
                    best_node = node
                    best_ratio = ratio
            if best_node is not None:
                return best_node, best_ratio

        return None, 0.0

    @staticmethod
    def _match_by_property(
        entity: ExtractedEntity,
        candidates: list[KGNode],
    ) -> KGNode | None:
        """Find a candidate whose stored property value matches one of
        ``entity.properties`` on a known match key (CAS / formula)."""
        for key in PROPERTY_MATCH_KEYS:
            entity_value = entity.properties.get(key)
            if not entity_value:
                continue
            entity_value_str = str(entity_value).strip().lower()
            if not entity_value_str:
                continue
            for node in candidates:
                node_props = _parse_properties(node.properties)
                node_value = node_props.get(key)
                if node_value is None:
                    continue
                if str(node_value).strip().lower() == entity_value_str:
                    return node
        return None

    # -- matched path ----------------------------------------------------

    async def _handle_match(
        self,
        session: AsyncSession,
        entity: ExtractedEntity,
        matched: KGNode,
        strategy_confidence: float,
        corpus_id: str | None,
    ) -> LinkResult:
        # Update aliases, properties, confidence, and provenance in place.
        existing_aliases = _parse_aliases(matched.aliases)
        merged_aliases = merge_alias_lists(existing_aliases, list(entity.aliases))
        matched.aliases = json.dumps(merged_aliases)

        existing_props = _parse_properties(matched.properties)
        new_props: dict[str, Any] = dict(entity.properties)
        # Capture provenance at the merge boundary.
        new_sources: list[Any] = []
        if entity.source_id is not None:
            new_sources.append(entity.source_id)
        new_sources.extend(entity.provenance_sources)
        if new_sources:
            new_props[PROVENANCE_SOURCES_KEY] = [str(source) for source in new_sources]
        merged_props = merge_node_properties(existing_props, new_props)
        # KGNode.properties is JSON-typed: assign the dict directly.
        matched.properties = merged_props

        matched.confidence = max(float(matched.confidence or 0.0), entity.confidence)

        needs_review = entity.confidence < self._review_threshold
        review_queue_id: uuid.UUID | None = None
        if needs_review:
            review_queue_id = await self._enqueue_for_review(
                session,
                item_id=matched.id,
                reason=(
                    f"low-confidence match: extracted confidence={entity.confidence:.2f} "
                    f"< review threshold {self._review_threshold:.2f}"
                ),
            )
            outcome = LinkOutcome.NEEDS_REVIEW
        else:
            outcome = LinkOutcome.MATCHED

        final_confidence = matched.confidence
        logger.debug(
            "entity_linker.match",
            extra={
                "entity_label": entity.label,
                "node_id": str(matched.id),
                "outcome": outcome.value,
                "final_confidence": final_confidence,
                "strategy_confidence": strategy_confidence,
            },
        )

        return LinkResult(
            outcome=outcome,
            node=matched,
            matched_node=matched,
            review_queue_id=review_queue_id,
            confidence=final_confidence,
        )

    # -- create path -----------------------------------------------------

    async def _handle_create(
        self,
        session: AsyncSession,
        entity: ExtractedEntity,
        corpus_id: str | None,
    ) -> LinkResult:
        needs_review = entity.confidence < self._review_threshold

        initial_status = "pending_review" if needs_review else "active"

        provenance_sources: list[str] = []
        if entity.source_id is not None:
            provenance_sources.append(str(entity.source_id))
        for source in entity.provenance_sources:
            provenance_sources.append(str(source))
        provenance_sources = list(dict.fromkeys(provenance_sources))

        properties = dict(entity.properties)
        if provenance_sources:
            properties[PROVENANCE_SOURCES_KEY] = provenance_sources

        node = KGNode(
            id=uuid.uuid4(),
            node_type=entity.entity_type,
            label=entity.label,
            aliases=json.dumps(list(entity.aliases)),
            # KGNode.properties is JSON-typed: assign dict directly.
            properties=properties,
            confidence=float(entity.confidence),
            source_id=entity.source_id,
            figure_id=None,
            status=initial_status,
            corpus_id=corpus_id,
            synced_to_graph=False,
            graph_synced_at=None,
        )
        session.add(node)
        await session.flush()  # populate node.id without committing

        review_queue_id: uuid.UUID | None = None
        if needs_review:
            review_queue_id = await self._enqueue_for_review(
                session,
                item_id=node.id,
                reason=(
                    f"low-confidence new node: extracted confidence={entity.confidence:.2f} "
                    f"< review threshold {self._review_threshold:.2f}"
                ),
            )
            outcome = LinkOutcome.NEEDS_REVIEW
        else:
            outcome = LinkOutcome.CREATED

        final_confidence = node.confidence
        logger.debug(
            "entity_linker.create",
            extra={
                "entity_label": entity.label,
                "node_id": str(node.id),
                "outcome": outcome.value,
                "final_confidence": final_confidence,
            },
        )

        return LinkResult(
            outcome=outcome,
            node=node,
            matched_node=None,
            review_queue_id=review_queue_id,
            confidence=final_confidence,
        )

    # -- review queue ---------------------------------------------------

    async def _enqueue_for_review(
        self,
        session: AsyncSession,
        *,
        item_id: uuid.UUID,
        reason: str,
    ) -> uuid.UUID:
        """Insert a ``KGReviewQueue`` row and return its ID."""
        row = KGReviewQueue(
            id=uuid.uuid4(),
            item_type="entity",
            item_id=item_id,
            review_reason=reason,
            status="pending",
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        return row.id


__all__ = [
    "DUPLICATE_LABEL_THRESHOLD",
    "FUZZY_MATCH_THRESHOLD",
    "PROPERTY_MATCH_KEYS",
    "PROVENANCE_SOURCES_KEY",
    "REVIEW_CONFIDENCE_THRESHOLD",
    "EntityLinker",
    "ExtractedEntity",
    "LinkOutcome",
    "LinkResult",
    "dedup_entities",
    "levenshtein_distance",
    "levenshtein_ratio",
    "merge_alias_lists",
    "merge_node_properties",
]
