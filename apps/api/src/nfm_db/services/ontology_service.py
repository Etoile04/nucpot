"""Ontology service for NVL visualization data.

Two sections:

1. Legacy sample-data functions (``get_nvl_data`` / ``get_viz_stats``) back the
   demo ``/viz/nvl`` + ``/viz/stats`` routes. Kept intact so NFM-226's web
   regression gate stays green (the Phase 0 viewer reads a static JSON, not
   these routes — they are demonstration-only).
2. ``derive_ontology_graph`` — the Phase 1 backend NVL derivation. A pure,
   read-only function that derives the versioned NFM-227 contract envelope from
   ``_ref_gap_fill_staging`` filtered by ``corpus_id`` (= ``source``). No new
   persistence (NFM-266 invariant #3).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.ref_gap_fill import RefGapFillStaging
from nfm_db.schemas.ontology import (
    CONTRACT_SCHEMA_VERSION,
    OntologyGraphResponse,
    OntologyNode,
    OntologyPagination,
    OntologyRelationship,
    OntologyStats,
)
from nfm_db.schemas.viz import Node, NvlResponse, Relationship, VizStatsResponse

# Canonical provenance label for the ref-gap-fill derived view.
SOURCE_ONTOLOGY = "nfmd/ref-gap-fill"

# Server hard ceiling — no single response may carry more nodes than this.
HARD_MAX_NODES = 50_000
_MAT_PREFIX = "mat:"


# Sample ontology data for demonstration
SAMPLE_NODES = [
    Node(
        id="metal-uranium",
        name="Uranium",
        classes=["Element", "Metal", "Actinide"],
        properties={"atomic_number": "92", "symbol": "U"},
    ),
    Node(
        id="metal-plutonium",
        name="Plutonium",
        classes=["Element", "Metal", "Actinide"],
        properties={"atomic_number": "94", "symbol": "Pu"},
    ),
    Node(
        id="compound-uo2",
        name="Uranium Dioxide",
        classes=["Compound", "Oxide", "NuclearMaterial"],
        properties={"formula": "UO2", "use": "Fuel"},
    ),
    Node(
        id="property-density",
        name="Density",
        classes=["Property"],
        properties={"unit": "g/cm³", "type": "Physical"},
    ),
]

SAMPLE_RELATIONSHIPS = [
    Relationship(
        id="rel-1",
        source="metal-uranium",
        target="compound-uo2",
        type="COMPOSES",
    ),
    Relationship(
        id="rel-2",
        source="metal-plutonium",
        target="compound-uo2",
        type="COMPOSES",
    ),
    Relationship(
        id="rel-3",
        source="compound-uo2",
        target="property-density",
        type="HAS_PROPERTY",
    ),
]


async def get_nvl_data(
    class_filter: str | None = None,
    search_term: str | None = None,
    max_nodes: int | None = None,
) -> NvlResponse:
    """Get NVL data with optional filtering.

    Args:
        class_filter: Filter nodes by class subtree
        search_term: Filter nodes by search term in name
        max_nodes: Limit number of nodes returned

    Returns:
        NvlResponse with filtered nodes and relationships
    """
    # Start with all nodes
    nodes = list(SAMPLE_NODES)

    # Apply class filter
    if class_filter:
        nodes = [n for n in nodes if class_filter in n.classes]

    # Apply search filter
    if search_term:
        search_lower = search_term.lower()
        nodes = [n for n in nodes if search_lower in n.name.lower()]

    # Apply max_nodes limit
    if max_nodes and len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]

    # Get relationships for filtered nodes
    node_ids = {n.id for n in nodes}
    relationships = [
        r for r in SAMPLE_RELATIONSHIPS
        if r.source in node_ids and r.target in node_ids
    ]

    return NvlResponse(nodes=nodes, relationships=relationships)


async def get_viz_stats() -> VizStatsResponse:
    """Get ontology statistics.

    Returns:
        VizStatsResponse with total counts and class distribution
    """
    class_counts: dict[str, int] = {}
    for node in SAMPLE_NODES:
        for cls in node.classes:
            class_counts[cls] = class_counts.get(cls, 0) + 1

    return VizStatsResponse(
        total_nodes=len(SAMPLE_NODES),
        total_relationships=len(SAMPLE_RELATIONSHIPS),
        class_counts=class_counts,
    )


# ---------------------------------------------------------------------------
# Phase 1 — versioned NVL derivation (NFM-270 / NFM-266)
# ---------------------------------------------------------------------------


class CorpusNotFoundError(LookupError):
    """Raised when a ``corpus_id`` resolves to zero staging rows."""

    def __init__(self, corpus_id: str) -> None:
        super().__init__(f"corpus not found: {corpus_id!r}")
        self.corpus_id = corpus_id


def _node_id(kind: str, key: str) -> str:
    return f"{kind}:{key}"


def _relationship_id(source: str, rel_type: str, target: str) -> str:
    return f"{source}|{rel_type}|{target}"


def build_record_ref(
    corpus_id: str,
    element_system: str,
    property: str | None = None,
) -> str:
    """Origin-relative, intent-encoded deep link to a material's property records.

    Deterministic pure function of the node's stable identity (``element_system``
    + ``corpus_id``; optional ``property`` narrows to a material→property edge).
    No DB access, no new storage (NFM-266 invariant #3) — a pure string
    derivation from existing staging identity.

    Relative by construction ⇒ shareable + session-proof: the same corpus is
    served from any origin (staging/prod/preview) and the link carries no host
    or session token. The frontend resolves it against the staging query
    (``_ref_gap_fill_staging`` filtered by ``element_system`` + ``source``).
    Phase 2 contract (NFM-267 §3).
    """
    encoded_element = quote(element_system, safe="")
    ref = f"/materials/{encoded_element}?corpus={quote(corpus_id, safe='')}"
    if property is not None:
        ref += f"&property={quote(property, safe='')}"
    return ref


def _compute_source_digest(
    nodes: list[OntologyNode],
    relationships: list[OntologyRelationship],
) -> str:
    """Short sha256 (16 hex) over the canonical graph serialization.

    Deterministic for a given corpus content — drift detection. Read-only: it
    never touches the DB.
    """
    canonical = {
        "nodes": sorted((n.id, n.type) for n in nodes),
        "relationships": sorted((r.from_, r.type, r.to) for r in relationships),
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Large-graph chunking (T6)
# ---------------------------------------------------------------------------


def _encode_cursor(offset: int) -> str:
    """Opaque cursor encoding a material-offset (not security-sensitive)."""
    token = json.dumps({"o": offset}).encode()
    return base64.urlsafe_b64encode(token).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    """Decode a cursor; malformed/absent → 0 (start)."""
    if not cursor:
        return 0
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return max(0, int(payload.get("o", 0)))
    except (ValueError, TypeError):
        return 0


def _material_ego_components(
    nodes_by_id: dict[str, OntologyNode],
    relationships: list[OntologyRelationship],
    source_node_id: str,
) -> dict[str, set[str]]:
    """Map each material node id → its ego subgraph node ids.

    Each material's ego subgraph keeps its mat→prop→method→src path intact so a
    chunked page is a coherent, referentially-complete subgraph. The shared
    ``source_node_id`` is included in every ego (it repeats across pages that
    reference it) — without it the CITED_IN edges would be dropped.
    """
    neighbors: dict[str, set[str]] = defaultdict(set)
    for rel in relationships:
        neighbors[rel.from_].add(rel.to)
        neighbors[rel.to].add(rel.from_)

    components: dict[str, set[str]] = {}
    for node_id in nodes_by_id:
        if node_id.startswith(_MAT_PREFIX):
            properties = set(neighbors[node_id])
            ego = {node_id, source_node_id} | properties
            for prop in properties:
                ego |= neighbors[prop]
            components[node_id] = ego
    return components


def _chunk_by_material(
    nodes_by_id: dict[str, OntologyNode],
    relationships: list[OntologyRelationship],
    ego: dict[str, set[str]],
    *,
    max_nodes: int,
    offset: int,
) -> tuple[list[OntologyNode], list[OntologyRelationship], int | None]:
    """Greedily pack material ego-subgraphs into a page bounded by ``max_nodes``.

    Returns the page nodes, the relationships fully inside the page, and the
    next material offset (or None when the corpus is exhausted).
    """
    materials = sorted(m for m in nodes_by_id if m.startswith(_MAT_PREFIX))
    total = len(materials)

    page_ids: set[str] = set()
    index = offset
    while index < total:
        component = ego[materials[index]]
        if page_ids and len(page_ids | component) > max_nodes:
            break
        page_ids |= component
        index += 1

    # Hard ceiling guarantee: a single ego subgraph larger than max_nodes (only
    # possible when it is the sole component on the page) is truncated so the
    # T6 invariant — no single response exceeds the ceiling — holds
    # unconditionally. Phase 1 corpora never hit this (single materials carry
    # far fewer than HARD_MAX_NODES properties).
    if len(page_ids) > max_nodes:
        page_ids = set(sorted(page_ids)[:max_nodes])

    page_nodes = [nodes_by_id[nid] for nid in sorted(page_ids)]
    page_relationships = [
        rel for rel in relationships
        if rel.from_ in page_ids and rel.to in page_ids
    ]
    next_offset = index if index < total else None
    return page_nodes, page_relationships, next_offset


async def derive_ontology_graph(
    session: AsyncSession,
    corpus_id: str,
    *,
    max_nodes: int | None = None,
    cursor: str | None = None,
) -> OntologyGraphResponse:
    """Derive the versioned NFM-227 NVL graph for a corpus.

    Reads ``_ref_gap_fill_staging`` rows where ``source == corpus_id``
    (parameterized — no string interpolation into SQL) and derives:

        material-(HAS_PROPERTY)->property-(MEASURED_BY)->method-(CITED_IN)->source

    Materials are individuals; property/method/source concepts are classes.
    Raises ``CorpusNotFoundError`` when the corpus resolves to no rows (the
    endpoint maps this to 404).
    """
    stmt = select(RefGapFillStaging).where(
        RefGapFillStaging.source == corpus_id,
    )
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        raise CorpusNotFoundError(corpus_id)

    nodes: dict[str, OntologyNode] = {}
    relationships: dict[str, OntologyRelationship] = {}

    def add_node(
        kind: str,
        key: str,
        *,
        node_type: str,
        record_ref: str | None = None,
    ) -> None:
        node_id = _node_id(kind, key)
        if node_id not in nodes:
            nodes[node_id] = OntologyNode(
                id=node_id,
                type=node_type,
                name=key,
                label=key,
                record_ref=record_ref,
            )

    def add_relationship(src: str, rel_type: str, dst: str) -> None:
        rel_id = _relationship_id(src, rel_type, dst)
        if rel_id not in relationships:
            relationships[rel_id] = OntologyRelationship(
                id=rel_id,
                from_=src,
                to=dst,
                type=rel_type,
            )

    source_node_id = _node_id("src", corpus_id)
    add_node("src", corpus_id, node_type="class")

    for row in rows:
        material_id = _node_id("mat", row.element_system)
        property_id = _node_id("prop", row.property_name)
        if material_id not in nodes:
            nodes[material_id] = OntologyNode(
                id=material_id,
                type="individual",
                name=row.element_system,
                label=row.element_system,
                record_ref=build_record_ref(corpus_id, row.element_system),
            )
        add_node("prop", row.property_name, node_type="class")
        add_relationship(material_id, "HAS_PROPERTY", property_id)

        method = row.method
        if method:
            method_id = _node_id("method", method)
            add_node("method", method, node_type="class")
            add_relationship(property_id, "MEASURED_BY", method_id)
            add_relationship(method_id, "CITED_IN", source_node_id)
        else:
            # No method recorded — cite the source directly from the property.
            add_relationship(property_id, "CITED_IN", source_node_id)

    full_nodes = sorted(nodes.values(), key=lambda n: n.id)
    relationship_list = list(relationships.values())
    total_nodes = len(full_nodes)

    # Corpus-level digest (NFM-227 semantics): stable across pages so it acts as
    # a corpus identity for provenance/drift, not a per-page value. Computed
    # over the full graph before chunking.
    corpus_digest = _compute_source_digest(full_nodes, relationship_list)

    effective_limit = (
        HARD_MAX_NODES
        if max_nodes is None
        else min(max(1, max_nodes), HARD_MAX_NODES)
    )

    if total_nodes <= effective_limit:
        page_nodes = full_nodes
        page_relationships = relationship_list
        pagination: OntologyPagination | None = None
    else:
        nodes_by_id = {n.id: n for n in full_nodes}
        ego = _material_ego_components(
            nodes_by_id,
            relationship_list,
            _node_id("src", corpus_id),
        )
        page_nodes, page_relationships, next_offset = _chunk_by_material(
            nodes_by_id,
            relationship_list,
            ego,
            max_nodes=effective_limit,
            offset=_decode_cursor(cursor),
        )
        pagination = OntologyPagination(
            next_cursor=(
                _encode_cursor(next_offset) if next_offset is not None else None
            ),
            total=total_nodes,
        )

    last_modified = max(
        (row.updated_at for row in rows),
        default=None,
    )

    graph = OntologyGraphResponse(
        schema_version=CONTRACT_SCHEMA_VERSION,
        corpus_id=corpus_id,
        generated_at=datetime.now(UTC),
        source_ontology=SOURCE_ONTOLOGY,
        source_digest=corpus_digest,
        stats=OntologyStats(
            nodes=len(page_nodes),
            relationships=len(page_relationships),
            classes=sum(1 for n in page_nodes if n.type == "class"),
            individuals=sum(1 for n in page_nodes if n.type == "individual"),
        ),
        nodes=page_nodes,
        relationships=page_relationships,
        pagination=pagination,
    )
    graph._last_modified = last_modified
    return graph
