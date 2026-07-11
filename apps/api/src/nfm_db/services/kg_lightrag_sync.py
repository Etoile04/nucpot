"""KG → LightRAG entity serialization and auto-ingest hook (NFM-1222).

Serializes ``KGNode`` and ``KGEdge`` ORM records into structured text
suitable for LightRAG ingestion, and provides a fire-and-forget ingest
helper that the ``GraphBuilder`` calls as a post-processing step.

Serialization format
--------------------
The chosen format is a compact, line-oriented text representation::

    [Material] UO2
    - crystal_structure: Fluorite
    - density: 10.97 g/cm³
    - confidence: 0.90

    [relatedTo] UO2 -> ZrO2
    - confidence: 0.85

**Why this format differs from AC #4 (NFM-1247, finding 4c):**

AC #4 originally specified a nested JSON format with embedded edge
references inside node blocks.  The implemented format is flat,
line-oriented text instead.  Reasons for the deviation:

1. **LightRAG compatibility** — LightRAG's ``ingest()`` API expects
   plain text (strings), not JSON blobs.  Structured text with ``[``
   type tags gives the best semantic extraction signal.
2. **Human readability** — Debugging and manual inspection of the
   ingested corpus is straightforward.
3. **Edges are first-class** — Edges are serialized as separate
   sections (not nested inside nodes) because LightRAG processes
   the entire document as a single text block; having edges as
   independent lines ensures the relation types and endpoint labels
   are surfaced during chunking and embedding.

If the AC needs updating to match this format, the canonical spec is
the output of ``serialize_kg_node`` and ``serialize_kg_edge`` below.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_utils import parse_aliases
from nfm_db.services.lightrag_client import is_lightrag_configured

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entity serialization
# ---------------------------------------------------------------------------


def serialize_kg_node(node: KGNode) -> str:
    """Serialize a ``KGNode`` into structured text for LightRAG ingestion.

    Produces a compact, human-readable representation that preserves
    entity type, label, properties, and aliases::

        [Material] UO2 (Uranium Dioxide)
        - crystal_structure: Fluorite
        - density: 10.97 g/cm³
        - aliases: Uranium Dioxide fuel, UO2 fuel

    Args:
        node: A ``KGNode`` ORM instance (must have been flushed / have
              an ``id`` assigned).

    Returns:
        Structured text string suitable for LightRAG ``ingest()``.
    """
    lines: list[str] = []

    # Header: [EntityType] Label
    lines.append(f"[{node.node_type}] {node.label}")

    # Properties as key-value pairs
    props = node.properties or {}
    for key, value in props.items():
        lines.append(f"- {key}: {value}")

    # Aliases (parsed from JSON text)
    raw_aliases = node.aliases
    if raw_aliases:
        aliases = parse_aliases(raw_aliases)
        if aliases:
            lines.append(f"- aliases: {', '.join(aliases)}")

    # Confidence score
    lines.append(f"- confidence: {node.confidence:.2f}")

    return "\n".join(lines)


def serialize_kg_edge(
    edge: KGEdge,
    source_label: str,
    target_label: str,
) -> str:
    """Serialize a ``KGEdge`` into structured text for LightRAG ingestion.

    Format::

        [related_to] UO2 -> ZrO2 via: fuel_cladding_interaction
        - confidence: 0.85

    Args:
        edge: A ``KGEdge`` ORM instance.
        source_label: Human-readable label of the source node.
        target_label: Human-readable label of the target node.

    Returns:
        Structured text string suitable for LightRAG ``ingest()``.
    """
    lines: list[str] = []

    lines.append(
        f"[{edge.relation_type}] {source_label} -> {target_label}"
    )

    # Properties
    props = edge.properties or {}
    for key, value in props.items():
        lines.append(f"- {key}: {value}")

    # Confidence score
    lines.append(f"- confidence: {edge.confidence:.2f}")

    return "\n".join(lines)


def serialize_build_result(
    nodes: list[KGNode],
    edges: list[KGEdge],
    node_labels: dict[uuid.UUID, str],
) -> str:
    """Serialize an entire build result (nodes + edges) into a single
    document suitable for a single LightRAG ``ingest()`` call.

    Combines all node serializations followed by all edge
    serializations, separated by blank lines.

    Args:
        nodes: Newly created ``KGNode`` records.
        edges: Newly created ``KGEdge`` records.
        node_labels: Mapping of node UUID -> label for edge serialization.

    Returns:
        Combined structured text document.
    """
    sections: list[str] = []

    for node in nodes:
        sections.append(serialize_kg_node(node))

    for edge in edges:
        source_label = node_labels.get(edge.source_node_id, str(edge.source_node_id))
        target_label = node_labels.get(edge.target_node_id, str(edge.target_node_id))
        sections.append(serialize_kg_edge(edge, source_label, target_label))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Fire-and-forget ingest
# ---------------------------------------------------------------------------


async def ingest_kg_to_lightrag(
    *,
    nodes: list[KGNode],
    edges: list[KGEdge],
    node_labels: dict[uuid.UUID, str],
) -> None:
    """Ingest serialized KG data into LightRAG (fire-and-forget safe).

    This is the main entry point called by ``GraphBuilder`` after
    constructing nodes and edges.  It:

    1. Checks ``is_lightrag_configured()`` — skips entirely if not.
    2. Serializes the build result to structured text.
    3. Calls ``LightRAGProvider.ingest()`` directly.
    4. Catches and logs all exceptions — never propagates failures.

    Args:
        nodes: Newly created ``KGNode`` records.
        edges: Newly created ``KGEdge`` records.
        node_labels: Mapping of node UUID -> label for edge serialization.
    """
    if not is_lightrag_configured():
        logger.debug("LightRAG not configured — skipping KG auto-ingest")
        return

    if not nodes and not edges:
        return

    try:
        from nfm_db.services.lightrag_lifecycle import get_shared_lightrag_client
        from nfm_db.services.rag_provider import LightRAGProvider

        text = serialize_build_result(nodes, edges, node_labels)
        shared_client = get_shared_lightrag_client()
        provider = LightRAGProvider(client=shared_client)
        await provider.ingest(text=text, source="kg_pipeline")

        logger.info(
            "KG auto-ingest complete: %d nodes, %d edges (%d chars)",
            len(nodes),
            len(edges),
            len(text),
        )
    except Exception:
        logger.warning(
            "KG auto-ingest to LightRAG failed (non-fatal, KG pipeline continues)",
            exc_info=True,
        )


def fire_ingest_to_lightrag(
    *,
    nodes: list[KGNode],
    edges: list[KGEdge],
    node_labels: dict[uuid.UUID, str],
) -> None:
    """Schedule a fire-and-forget LightRAG ingest as a background task.

    Creates an ``asyncio.Task`` that runs ``ingest_kg_to_lightrag``
    without blocking the caller.  The task is attached to the current
    event loop and its result is intentionally discarded.

    Args:
        nodes: Newly created ``KGNode`` records.
        edges: Newly created ``KGEdge`` records.
        node_labels: Mapping of node UUID -> label.
    """
    if not is_lightrag_configured():
        return

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            ingest_kg_to_lightrag(
                nodes=nodes,
                edges=edges,
                node_labels=node_labels,
            ),
            name="kg-lightrag-ingest",
        )
    except RuntimeError:
        # No running loop (e.g. in tests) — skip fire-and-forget
        logger.debug("No event loop — skipping fire-and-forget LightRAG ingest")
