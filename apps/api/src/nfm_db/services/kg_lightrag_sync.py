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

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_utils import parse_aliases
from nfm_db.services.lightrag_client import (
    LightRAGClient,
    LightRAGConflictError,
    is_lightrag_configured,
)

logger = logging.getLogger(__name__)

# Strong-reference set for fire-and-forget ingest tasks. Without this,
# asyncio.Task objects scheduled via loop.create_task() can be garbage
# collected before they complete (RUF006). Completed tasks are evicted
# lazily on the next schedule call to bound memory.
_background_tasks: set[asyncio.Task[None]] = set()

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

    lines.append(f"[{edge.relation_type}] {source_label} -> {target_label}")

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
    extraction_job_id: uuid.UUID | None = None,
    source: str = "kg_pipeline",
) -> None:
    """Ingest serialized KG data into LightRAG.

    This is the main entry point called by ``GraphBuilder`` after
    constructing nodes and edges, and is also reused by the literature
    pipeline's inline-ingest path (``process_literature`` awaits it
    inside the Celery worker's ``asyncio.run`` loop).  It:

    1. Checks ``is_lightrag_configured()`` — skips entirely if not.
    2. Serializes the build result to structured text.
    3. Calls ``LightRAGProvider.ingest()`` directly.
    4. Persists the returned ``track_id`` to the ``ExtractionJob`` row
       when *extraction_job_id* is provided (NFM-2881).

    NFM-4719: this function NO LONGER swallows exceptions.  The
    previous ``except Exception:`` masked the inline-ingest silent
    failure — when the shared ``httpx.AsyncClient`` was bound to a
    closed loop (Celery worker re-entry), the POST raised
    ``RuntimeError: Event loop is closed`` and the caller logged
    "LightRAG inline ingest done (nodes=N edges=M)" anyway because
    the function returned normally.  The new contract is: any
    failure surfaces to the caller; the inline path wraps this call
    in its own ``try/except`` that logs the failure as
    ``process_literature: … inline LightRAG ingest failed (non-fatal)``,
    and the fire-and-forget wrapper
    (:func:`_fire_and_forget_ingest`) catches + logs without losing
    visibility.

    Args:
        nodes: Newly created ``KGNode`` records.
        edges: Newly created ``KGEdge`` records.
        node_labels: Mapping of node UUID -> label for edge serialization.
        extraction_job_id: Optional ``ExtractionJob`` PK whose ``track_id``
            column should be updated after ingest.
        source: Source marker stamped on the ingested document.  The
            literature pipeline (NFM-4636) passes
            ``data_source:<datasource-uuid>`` so the daily
            ``rag_audit_index_coverage`` reconciliation (NFM-4539 RAG-D)
            can match the doc back to its ``data_sources`` row — a
            generic ``kg_pipeline`` tag is invisible to that diff and
            the row reconciles as drift forever.
    """
    if not is_lightrag_configured():
        logger.debug("LightRAG not configured — skipping KG auto-ingest")
        return

    if not nodes and not edges:
        return

    from nfm_db.database import get_session_factory
    from nfm_db.services.lightrag_lifecycle import get_shared_lightrag_client
    from nfm_db.services.rag_provider import LightRAGProvider

    text = serialize_build_result(nodes, edges, node_labels)
    shared_client: LightRAGClient | None = get_shared_lightrag_client()
    # ``is_lightrag_configured()`` is the gate above; in production
    # the shared client exists.  Tests exercise this path with
    # ``shared_client=None`` and rely on ``LightRAGProvider.ingest``
    # to be a no-op when the sidecar isn't reachable.  Build the
    # provider unconditionally; for the 409-recovery branch (which
    # calls ``shared_client.delete_document`` directly) we narrow to
    # non-None at the call site, since ``delete_document`` requires
    # a live HTTP client.
    provider = LightRAGProvider(client=shared_client)

    # NFM-4758 / NFM-4730-FixA: the reextract path can hit a 409
    # ``Document storage already contains '<doc_id>'`` when the
    # ``data_source:<uuid>`` (or ``kg_pipeline``) marker is already in
    # ``lightrag_doc_status`` from a prior run — typically the
    # NFM-4680 recovery data, where the rebuild left the marker as
    # ``processed`` but never re-wrote the VDB rows.  Pre-fix, the
    # 409 propagated to the caller, the inline-ingest branch logged
    # "process_literature: … inline LightRAG ingest failed (non-fatal)",
    # and VDB growth silently dropped to zero.  Post-fix, we evict the
    # stale marker via ``DELETE /documents?doc_id=<source>`` and retry
    # the ingest exactly once.  Any subsequent failure propagates so
    # the caller's existing error handling still fires.
    track_id: str | None
    try:
        track_id = await provider.ingest(text=text, source=source)
    except LightRAGConflictError as exc:
        logger.info(
            "LightRAG ingest returned 409 for source=%r (already "
            "indexed) — deleting the stale marker and retrying once",
            source,
        )
        try:
            if shared_client is None:
                # Lifecycle invariant: ``is_lightrag_configured()``
                # returned True but no shared client exists.  We can't
                # DELETE the marker — propagate the original conflict
                # so the caller's error path still fires.
                logger.warning(
                    "LightRAG 409 received but shared_client is None; "
                    "cannot DELETE marker source=%r",
                    source,
                )
                raise exc
            await shared_client.delete_document(source)
        except Exception:
            # ``delete_document`` already raises a structured
            # ``LightRAGClientError``; any other failure here means the
            # DELETE call could not even reach the sidecar.  Log and
            # re-raise the original conflict so the caller's
            # ``except Exception`` branch fires with the 409 context
            # (not a silent swallow).
            logger.warning(
                "LightRAG DELETE /documents?doc_id=%r failed; "
                "propagating original 409 to caller",
                source,
                exc_info=True,
            )
            raise exc from None
        logger.info(
            "LightRAG stale marker source=%r deleted; retrying ingest",
            source,
        )
        track_id = await provider.ingest(text=text, source=source)

    # Persist track_id to the ExtractionJob row (NFM-2881 AC-2).
    if extraction_job_id is not None and track_id is not None:
        async with get_session_factory()() as session:
            from sqlalchemy import update

            from nfm_db.models.extraction_job import ExtractionJob

            await session.execute(
                update(ExtractionJob)
                .where(ExtractionJob.id == extraction_job_id)
                .values(track_id=track_id)
            )
            await session.commit()
            logger.info(
                "Persisted track_id=%s to ExtractionJob %s",
                track_id,
                extraction_job_id,
            )

    logger.info(
        "KG auto-ingest complete: %d nodes, %d edges (%d chars)",
        len(nodes),
        len(edges),
        len(text),
    )


async def _fire_and_forget_ingest(
    *,
    nodes: list[KGNode],
    edges: list[KGEdge],
    node_labels: dict[uuid.UUID, str],
    extraction_job_id: uuid.UUID | None = None,
) -> None:
    """Wrapper around :func:`ingest_kg_to_lightrag` for fire-and-forget use.

    NFM-4719: this wrapper is what ``fire_ingest_to_lightrag`` schedules.
    It catches every exception (so the loop-level ``Task`` never carries
    an unhandled traceback) AND logs a single WARNING that surfaces in
    the worker log.  The previous code path relied on the swallow inside
    ``ingest_kg_to_lightrag`` itself, which is now removed to fix the
    inline-ingest silent failure — so this wrapper is the dedicated
    place fire-and-forget callers report their (expected) failure.
    """
    try:
        await ingest_kg_to_lightrag(
            nodes=nodes,
            edges=edges,
            node_labels=node_labels,
            extraction_job_id=extraction_job_id,
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
    extraction_job_id: uuid.UUID | None = None,
) -> None:
    """Schedule a fire-and-forget LightRAG ingest as a background task.

    Creates an ``asyncio.Task`` that runs ``ingest_kg_to_lightrag``
    without blocking the caller.  The task is attached to the current
    event loop and its result is intentionally discarded.

    Args:
        nodes: Newly created ``KGNode`` records.
        edges: Newly created ``KGEdge`` records.
        node_labels: Mapping of node UUID -> label.
        extraction_job_id: Optional ``ExtractionJob`` PK to persist
            ``track_id`` (NFM-2881).
    """
    if not is_lightrag_configured():
        return

    try:
        loop = asyncio.get_running_loop()
        # NFM-4719: schedule the dedicated fire-and-forget wrapper, which
        # catches + logs failures.  Previously the swallow lived inside
        # ``ingest_kg_to_lightrag`` itself and silently dropped the
        # "Event loop is closed" traceback (Celery worker re-entry path),
        # leaving no breadcrumb in the worker log.
        task = loop.create_task(
            _fire_and_forget_ingest(
                nodes=nodes,
                edges=edges,
                node_labels=node_labels,
                extraction_job_id=extraction_job_id,
            ),
            name="kg-lightrag-ingest",
        )
        # Retain a strong reference so the task isn't garbage-collected
        # mid-execution (RUF006). Evict completed tasks to bound memory.
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError:
        # No running loop (e.g. in tests) — skip fire-and-forget
        logger.debug("No event loop — skipping fire-and-forget LightRAG ingest")
