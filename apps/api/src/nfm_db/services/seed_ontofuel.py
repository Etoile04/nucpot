"""OntoFuel seed pipeline (NFM-768).

Orchestrates: parse NVL JSON → dedup → write KG nodes/edges + OntologyIdMap → stats.

Key design decisions:

1. **KGNode for ALL nodes**: OntologyIdMap.node_id has a FK to kg_nodes.id,
   so every mapped node needs a KGNode row. Classes use node_type="Material"
   (the KG CHECK constraint limits to extraction types; OntoFuel classes
   are domain concepts mapped to the closest valid type).

2. **Idempotency**: Pre-flight SELECT collects existing IDs, then only
   inserts new ones. Works on both Postgres and SQLite.

3. **Dedup**: Secondary (label, type) dedup as a safety net.

4. **Relationships**: Only individual-to-individual links become KGEdge.

Corpus identifier: ``ontofuel`` (per NFM-768 AC#5).
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode, OntologyIdMap
from nfm_db.schemas.ontofuel_parser import parse_nvl_ontology
from nfm_db.schemas.ontology import OntologyGraphResponse

logger = logging.getLogger(__name__)

CORPUS_ID = "ontofuel"

DEFAULT_NVL_JSON = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "web"
    / "public"
    / "ontology-viewer"
    / "data"
    / "nvl_ontology_data.json"
)


@dataclass
class SeedStats:
    """Result statistics from the seed pipeline."""

    total_nodes: int = 0
    total_relationships: int = 0
    classes: int = 0
    individuals: int = 0
    nodes_deduped: int = 0
    id_maps_created: int = 0
    kg_nodes_created: int = 0
    kg_edges_created: int = 0
    duplicates_skipped: int = 0

    def summary(self) -> str:
        return (
            f"Seed complete: {self.total_nodes} nodes "
            f"({self.classes} classes, {self.individuals} individuals), "
            f"{self.total_relationships} relationships. "
            f"Dedup removed {self.nodes_deduped}. "
            f"DB: {self.kg_nodes_created} KGNodes, "
            f"{self.kg_edges_created} KGEdges, "
            f"{self.id_maps_created} OntologyIdMaps, "
            f"{self.duplicates_skipped} duplicates skipped (idempotent)."
        )


def _parse_and_dedup(
    json_path: Path,
) -> tuple[OntologyGraphResponse, list[dict[str, Any]], list[dict[str, Any]], int]:
    """Parse NVL JSON, dedup nodes by (type, label)."""
    doc = parse_nvl_ontology(json_path)

    seen: dict[tuple[str, str], dict[str, Any]] = {}
    removed = 0
    for node in doc.nodes:
        key = (node.type or "", node.label or "")
        if key in seen:
            removed += 1
        else:
            seen[key] = {
                "id": node.id,
                "type": node.type,
                "name": node.name,
                "label": node.label,
                "comment": node.comment,
                "uri": node.uri,
                "color": node.color,
                "size": node.size,
            }

    deduped_nodes = list(seen.values())
    rels = [
        {
            "id": r.id,
            "from": r.from_,
            "to": r.to,
            "type": r.type,
            "label": r.label,
        }
        for r in doc.relationships
    ]
    return doc, deduped_nodes, rels, removed


def _dry_run_stats(
    deduped_nodes: list[dict[str, Any]],
    rels: list[dict[str, Any]],
    removed: int,
) -> SeedStats:
    type_counts = Counter(n["type"] for n in deduped_nodes)
    return SeedStats(
        total_nodes=len(deduped_nodes),
        total_relationships=len(rels),
        classes=type_counts.get("class", 0),
        individuals=type_counts.get("individual", 0),
        nodes_deduped=removed,
    )


async def seed_ontofuel(
    session: AsyncSession,
    json_path: Path | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> SeedStats:
    """Run the full OntoFuel seed pipeline.

    Steps:
        1. Parse NVL JSON (via NFM-1820 parser)
        2. Dedup nodes by (type, label)
        3. Write KGNode for ALL nodes (classes + individuals)
           — required because OntologyIdMap.node_id FK → kg_nodes.id
        4. Write OntologyIdMap entries (idempotent)
        5. Write KGEdge for individual-to-individual relationships
        6. Return stats
    """
    path = json_path or DEFAULT_NVL_JSON
    if not path.exists():
        raise FileNotFoundError(f"OntoFuel JSON not found: {path}")

    doc, deduped_nodes, rels, removed = _parse_and_dedup(path)

    if dry_run:
        return _dry_run_stats(deduped_nodes, rels, removed)

    # --- Pre-flight: check if corpus already seeded (unless --force) ---
    if not force:
        existing = await session.execute(
            select(OntologyIdMap).where(OntologyIdMap.corpus_id == CORPUS_ID).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info("Corpus %s already seeded (use --force to re-seed)", CORPUS_ID)
            return await _count_existing(session)

    stats = SeedStats(
        total_nodes=len(deduped_nodes),
        total_relationships=len(rels),
        classes=sum(1 for n in deduped_nodes if n["type"] == "class"),
        individuals=sum(1 for n in deduped_nodes if n["type"] == "individual"),
        nodes_deduped=removed,
    )

    # --- Build lookup: nvl_id → internal UUID (deterministic) ---
    node_id_map: dict[str, uuid.UUID] = {}
    for node in deduped_nodes:
        node_id_map[node["id"]] = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ontofuel:{node['id']}",
        )

    # Collect existing KGNode IDs to skip
    all_internal_ids = set(node_id_map.values())
    existing_kg_ids_result = await session.execute(
        select(KGNode.id).where(KGNode.id.in_(all_internal_ids))
    )
    existing_kg_ids: set[uuid.UUID] = {row[0] for row in existing_kg_ids_result.all()}

    # --- Step 3: Write KGNode for ALL nodes (classes + individuals) ---
    for node in deduped_nodes:
        internal_id = node_id_map[node["id"]]
        if internal_id in existing_kg_ids:
            continue
        session.add(KGNode(
            id=internal_id,
            node_type="Material",
            label=node.get("label") or node.get("name") or node["id"],
            properties={
                "uri": node.get("uri", ""),
                "comment": node.get("comment", ""),
                "color": node.get("color"),
                "nvl_type": node["type"],
            },
            confidence=1.0,
            corpus_id=CORPUS_ID,
            review_status="approved",
        ))
        stats.kg_nodes_created += 1
    await session.flush()

    # --- Step 4: Write OntologyIdMap (all nodes, classes + individuals) ---
    existing_ids_result = await session.execute(
        select(OntologyIdMap.nvl_id).where(
            OntologyIdMap.corpus_id == CORPUS_ID,
        )
    )
    existing_ids: set[str] = {row[0] for row in existing_ids_result.all()}

    for node in deduped_nodes:
        nvl_id = node["id"]
        if nvl_id in existing_ids:
            stats.duplicates_skipped += 1
            continue
        internal_id = node_id_map[nvl_id]
        session.add(OntologyIdMap(
            nvl_id=nvl_id,
            corpus_id=CORPUS_ID,
            node_id=internal_id,
            graph_label=node.get("label"),
        ))
        stats.id_maps_created += 1
    await session.flush()

    # --- Step 5: Write KGEdge for individual-to-individual relationships ---
    node_type_lookup = {n["id"]: n["type"] for n in deduped_nodes}
    for rel in rels:
        from_id = rel["from"]
        to_id = rel["to"]

        if node_type_lookup.get(from_id) != "individual":
            continue
        if node_type_lookup.get(to_id) != "individual":
            continue

        src_uuid = node_id_map[from_id]
        tgt_uuid = node_id_map[to_id]
        relation_type = _map_relation_type(rel["type"])

        existing_edge = await session.execute(
            select(KGEdge).where(
                KGEdge.source_node_id == src_uuid,
                KGEdge.target_node_id == tgt_uuid,
                KGEdge.relation_type == relation_type,
            )
        )
        if existing_edge.scalar_one_or_none() is not None:
            continue

        session.add(KGEdge(
            source_node_id=src_uuid,
            target_node_id=tgt_uuid,
            relation_type=relation_type,
            properties={
                "original_type": rel["type"],
                "label": rel.get("label", ""),
            },
            confidence=1.0,
            corpus_id=CORPUS_ID,
        ))
        stats.kg_edges_created += 1

    await session.flush()
    logger.info(stats.summary())
    return stats


_RELATION_TYPE_MAP: dict[str, str] = {
    "INSTANCE_OF": "relatedTo",
    "SUBCLASS_OF": "relatedTo",
    "hasFiber": "alloyOf",
    "hasMatrix": "alloyOf",
    "causes": "relatedTo",
    "forms": "synthesizedBy",
    "exhibitsPhenomenon": "relatedTo",
    "comparesWith": "relatedTo",
}


def _map_relation_type(nvl_type: str) -> str:
    return _RELATION_TYPE_MAP.get(nvl_type, "relatedTo")


async def _count_existing(session: AsyncSession) -> SeedStats:
    """Return stats for an already-seeded corpus."""
    from sqlalchemy import func as sa_func

    id_map_count = (await session.execute(
        select(sa_func.count()).select_from(OntologyIdMap).where(
            OntologyIdMap.corpus_id == CORPUS_ID,
        )
    )).scalar_one()

    kg_node_count = (await session.execute(
        select(sa_func.count()).select_from(KGNode).where(
            KGNode.corpus_id == CORPUS_ID,
        )
    )).scalar_one()

    kg_edge_count = (await session.execute(
        select(sa_func.count()).select_from(KGEdge).where(
            KGEdge.corpus_id == CORPUS_ID,
        )
    )).scalar_one()

    return SeedStats(
        total_nodes=id_map_count,
        id_maps_created=id_map_count,
        kg_nodes_created=kg_node_count,
        kg_edges_created=kg_edge_count,
    )
