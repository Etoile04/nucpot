"""Ontology service for NVL visualization data.

Two sections:

1. Legacy sample-data functions (``get_nvl_data`` / ``get_viz_stats``) back the
   demo ``/viz/nvl`` + ``/viz/stats`` routes. Kept intact so NFM-226's web
   regression gate stays green (the Phase 0 viewer reads a static JSON, not
   these routes — they are demonstration-only).
2. ``derive_ontology_graph`` — the Phase 1 backend NVL derivation. A pure,
   read-only function that derives the versioned NFM-227 contract envelope from
   the formal ``reference_values`` table filtered by ``corpus_id`` (= the
   ``source`` column, carried through 1:1 from the C-S1 ETL). No new
   persistence (NFM-266 invariant #3).

Read-path history
-----------------

Pre-NFM-3872 (C-S1) the function read from ``_ref_gap_fill_staging``
directly. After C-S1 the formal ``reference_values`` table is the
authoritative read source — the staging table remains as the audit /
review queue only (rows that did NOT pass the C-I1 admission gate
(NFM-3871) stay in staging and never enter the graph derivation).
This is the "fallback read path switches to formal table" contract
the pilot C-line decided on.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import quote

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.reference_value import ReferenceValue
from nfm_db.schemas.ontology import (
    CONTRACT_SCHEMA_VERSION,
    OntologyGraphResponse,
    OntologyNode,
    OntologyPagination,
    OntologyRelationship,
    OntologyStats,
)
from nfm_db.schemas.viz import Node, NvlResponse, Relationship, VizStatsResponse

# Per-kind visual defaults (NFM-3478): dynamic-corpus nodes previously shipped
# with color=null/size=null, which NVL renders as invisible zero-size dots
# (arrows still drawn — the "arrows but no nodes" symptom). Colors align with
# the vendored viewer's domain legend palette (wP) so both corpus kinds look
# coherent; size matches the static corpus default (30).
_KIND_COLORS: dict[str, str] = {
    "mat": "#FF5722",  # 材料 Material — deep orange (legend: 反应堆/材料)
    "prop": "#2ECC71",  # 性能 Property — green
    "method": "#F39C12",  # 方法/测量 — orange (legend: 模型/方法)
    "src": "#1ABC9C",  # 来源 Source — teal (legend: 辐照/来源)
}
_NODE_BASE_SIZE = 30.0

# Canonical provenance label for the ref-gap-fill derived view.
SOURCE_ONTOLOGY = "nfmd/ref-gap-fill"

# Safe slug — MUST stay in lockstep with CORPUS_ID_PATTERN in
# ``api/v1/ontology.py`` (the graph path-param validator). Duplicated here so
# the service layer does not import from the API layer.
CORPUS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

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
        r for r in SAMPLE_RELATIONSHIPS if r.source in node_ids and r.target in node_ids
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


def _format_measurement(
    value: float,
    unit: str,
    *,
    uncertainty: float | None,
    temperature: float | None,
    method: str | None,
    element_system: str,
    phase: str | None,
) -> str:
    """One staging row as a compact human-readable measurement string.

    E.g. ``"115 ±5 GPa (DFT, T=0 K) [U, bcc]"`` — the exact fields materials
    users ask about when clicking a property node.
    """
    parts: list[str] = []
    value_str = f"{value:g}"
    if uncertainty is not None:
        value_str += f" ±{uncertainty:g}"
    parts.append(f"{value_str} {unit}")
    qualifiers: list[str] = []
    if method:
        qualifiers.append(method)
    if temperature is not None:
        qualifiers.append(f"T={temperature:g} K")
    if qualifiers:
        parts.append(f"({', '.join(qualifiers)})")
    provenance = element_system
    if phase:
        provenance += f", {phase}"
    parts.append(f"[{provenance}]")
    return " ".join(parts)


def _doi_to_uri(doi: str) -> str:
    """Normalize a bare DOI into a resolvable https URI."""
    if doi.startswith(("http://", "https://")):
        return doi
    return f"https://doi.org/{doi.lstrip('/')}"


def _enrich_nodes(
    nodes: dict[str, OntologyNode],
    rows: Sequence[ReferenceValue],
    corpus_id: str,
) -> None:
    """Layer-A enrichment (NFM-3478): propagate measurement detail into nodes.

    Mutates the node dicts in place, AFTER the main derivation loop. The loop
    only carries bare names; the formal ``reference_values`` rows carry
    value/unit/uncertainty/temperature/method/DOI. Property nodes aggregate
    every measurement of that property in this corpus; material nodes
    summarize their property values; source nodes get the corpus's DOI when
    any row carries one.

    Read-path note (NFM-3872 / C-S1): the helper now takes
    ``ReferenceValue`` rows instead of ``RefGapFillStaging`` rows. The
    column names differ — ``element_system`` → ``element``,
    ``phase`` → ``crystal_structure`` — so any pre-C-S1 row attribute
    access has been renamed below.
    """
    prop_rows: dict[str, list[ReferenceValue]] = defaultdict(list)
    mat_rows: dict[str, list[ReferenceValue]] = defaultdict(list)
    for row in rows:
        prop_rows[row.property_name].append(row)
        mat_rows[row.element].append(row)

    # Property nodes: one comment line per measurement + DOI + deep link.
    for prop_name, prows in prop_rows.items():
        node = nodes.get(_node_id("prop", prop_name))
        if node is None:
            continue
        lines = [
            _format_measurement(
                r.value,
                r.unit,
                uncertainty=r.uncertainty,
                temperature=r.temperature,
                method=r.method,
                element_system=r.element,
                phase=r.crystal_structure,
            )
            for r in prows
        ]
        node.comment = "\n".join(lines)
        doi = next((r.source_doi for r in prows if r.source_doi), None)
        if doi:
            node.uri = _doi_to_uri(doi)
        node.record_ref = build_record_ref(
            corpus_id, prows[0].element, property_name=prop_name
        )

    # Material nodes: summary comment listing each measured property value.
    for mat_name, mrows in mat_rows.items():
        node = nodes.get(_node_id("mat", mat_name))
        if node is None:
            continue
        summary_lines = []
        for r in mrows:
            value_str = f"{r.value:g} {r.unit}"
            if r.uncertainty is not None:
                value_str = f"{r.value:g} ±{r.uncertainty:g} {r.unit}"
            summary_lines.append(f"{r.property_name} = {value_str}")
        node.comment = "\n".join(summary_lines)
        doi = next((r.source_doi for r in mrows if r.source_doi), None)
        if doi and not node.uri:
            node.uri = _doi_to_uri(doi)

    # Method nodes (NFM-3478): summarize what was measured with this method.
    # The derivation loop creates method class nodes (DFT, ADP, …) but Layer A
    # only enriched prop/mat/src, leaving method comments null (user-visible
    # regression: "DFT 是null"). Aggregate per-method measurements here.
    method_rows: dict[str, list[ReferenceValue]] = defaultdict(list)
    for row in rows:
        if row.method:
            method_rows[row.method].append(row)
    for method_name, mrows in method_rows.items():
        node = nodes.get(_node_id("method", method_name))
        if node is None:
            continue
        used_for = ", ".join(sorted({f"{r.property_name} ({r.element})" for r in mrows}))
        node.comment = f"{len(mrows)} measurement(s): {used_for}"
        doi = next((r.source_doi for r in mrows if r.source_doi), None)
        if doi and not node.uri:
            node.uri = _doi_to_uri(doi)

    # Source nodes: DOI deep link + provenance summary.
    src_node = nodes.get(_node_id("src", corpus_id))
    if src_node is not None:
        doi = next((r.source_doi for r in rows if r.source_doi), None)
        if doi:
            src_node.uri = _doi_to_uri(doi)
        src_node.comment = (
            f"{len(rows)} measurement(s) from {len(mat_rows)} material(s), "
            f"{len(prop_rows)} property type(s)"
        )


def build_record_ref(
    corpus_id: str,
    element_system: str,
    property_name: str | None = None,
) -> str:
    """Origin-relative, intent-encoded deep link to a material's property records.

    Deterministic pure function of the node's stable identity (``element_system``
    + ``corpus_id``; optional ``property_name`` narrows to a material→property edge).
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
    if property_name is not None:
        ref += f"&property={quote(property_name, safe='')}"
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
# Corpus discovery (NFM-3303) — dynamic corpus index
# ---------------------------------------------------------------------------


class OntologyCorpusInfo(BaseModel):
    """One queryable corpus in the dynamic index (NFM-3303)."""

    corpus_id: str = Field(min_length=1, max_length=64)
    row_count: int = Field(ge=1)
    last_updated: datetime | None = None


class OntologyCorporaResponse(BaseModel):
    """``GET /api/v1/ontology/corpora`` response envelope (NFM-3303)."""

    corpora: list[OntologyCorpusInfo] = Field(default_factory=list)


async def list_queryable_corpora(session: AsyncSession) -> OntologyCorporaResponse:
    """Enumerate corpora that actually have formal-table rows (NFM-3303 / NFM-3872).

    A corpus is queryable iff it resolves to >= 1 row in the formal
    ``reference_values`` table (the exact predicate ``derive_ontology_graph``
    uses to raise ``CorpusNotFoundError``) **and** its ``source`` is a valid
    slug per ``CORPUS_ID_PATTERN`` (the graph path param validator) — so a
    listed corpus can never 422/404 on its graph request. Rows with a blank
    ``source`` and DOI-shaped sources containing ``/`` are excluded for this
    reason. Read-only single aggregate query.

    Read-path history (NFM-3872 / C-S1): pre-C-S1 this read from
    ``_ref_gap_fill_staging``. After C-S1 the formal ``reference_values``
    table is the authoritative corpus source — staging rows that did NOT
    pass the C-I1 admission gate (NFM-3871) stay in staging as audit data
    and do NOT appear in the corpus index.
    """

    stmt = (
        select(
            ReferenceValue.source,
            func.count().label("row_count"),
            func.max(ReferenceValue.updated_at).label("last_updated"),
        )
        .where(ReferenceValue.source != "")
        .group_by(ReferenceValue.source)
        .order_by(func.count().desc(), ReferenceValue.source.asc())
    )
    rows = (await session.execute(stmt)).all()
    return OntologyCorporaResponse(
        corpora=[
            OntologyCorpusInfo(
                corpus_id=source,
                row_count=row_count,
                last_updated=last_updated,
            )
            for source, row_count, last_updated in rows
            if CORPUS_ID_RE.match(source)
        ]
    )


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
        rel for rel in relationships if rel.from_ in page_ids and rel.to in page_ids
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

    Reads the formal ``reference_values`` table where ``source == corpus_id``
    (parameterized — no string interpolation into SQL) and derives:

        material-(HAS_PROPERTY)->property-(MEASURED_BY)->method-(CITED_IN)->source

    Materials are individuals; property/method/source concepts are classes.
    Raises ``CorpusNotFoundError`` when the corpus resolves to no rows (the
    endpoint maps this to 404).

    Read-path history (NFM-3872 / C-S1): pre-C-S1 this read from
    ``_ref_gap_fill_staging`` directly. The C-S1 promotion moves the
    authoritative read source to the ``reference_values`` formal table —
    rows that did NOT pass the C-I1 admission gate (NFM-3871) stay in
    staging as audit data and never appear in the graph derivation.
    This is the "fallback read path switches to formal table" contract
    the pilot C-line decided on.
    """
    stmt = select(ReferenceValue).where(
        ReferenceValue.source == corpus_id,
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
                color=_KIND_COLORS.get(kind),
                size=_NODE_BASE_SIZE,
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
        material_id = _node_id("mat", row.element)
        property_id = _node_id("prop", row.property_name)
        if material_id not in nodes:
            nodes[material_id] = OntologyNode(
                id=material_id,
                type="individual",
                name=row.element,
                label=row.element,
                record_ref=build_record_ref(corpus_id, row.element),
                color=_KIND_COLORS["mat"],
                size=_NODE_BASE_SIZE,
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

    # --- NFM-3478 Layer A: enrich nodes with measured-value detail ----------
    # The formal ``reference_values`` rows carry the full measurement record
    # (value/unit/±/T/DOI), but the loop above only propagates bare names.
    # Aggregate per node kind so the viewer's property panel shows what
    # materials users actually ask for:
    #   prop  -> comment: "115 ±5 GPa (DFT, T=0 K) [U]" + uri: DOI + record_ref
    #   mat   -> comment: property list with values, e.g. "bulk_modulus=115 GPa"
    #   src   -> uri: first DOI, comment: provenance summary
    # All fields stay optional in the contract (null when the row lacks data),
    # so the digest drifts only when the underlying measurements change.
    _enrich_nodes(nodes, rows, corpus_id)

    # Corpus-level digest (NFM-227 semantics): stable across pages so it acts as
    # a corpus identity for provenance/drift, not a per-page value. Computed
    # over the full graph before chunking.
    corpus_digest = _compute_source_digest(full_nodes, relationship_list)

    effective_limit = (
        HARD_MAX_NODES if max_nodes is None else min(max(1, max_nodes), HARD_MAX_NODES)
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
            next_cursor=(_encode_cursor(next_offset) if next_offset is not None else None),
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
