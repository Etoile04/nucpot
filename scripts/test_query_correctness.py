#!/usr/bin/env python3
"""Query correctness tests + ontology coverage check.

Standalone script that:
1. Seeds an in-memory SQLite KG with representative nodes and edges.
2. Runs ``query_graph_nodes`` and ``query_graph_edges`` from ``kg_re``
   with predefined queries and asserts correct results.
3. Verifies ontology coverage: ≥5 entity types + ≥10 relation types.

Exit codes:
    0  — all tests passed
    1  — one or more tests failed
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Ensure apps/api/src is importable
# ---------------------------------------------------------------------------
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_API_SRC = str(_REPO_ROOT / "apps" / "api" / "src")
if _API_SRC not in sys.path:
    sys.path.insert(0, _API_SRC)


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeQueryCase:
    """A query_graph_nodes test case."""

    description: str
    entity_types: list[str] | None = None
    query: str | None = None
    expected_labels: list[str] | None = None
    """Exact set of expected node labels, or None to only check > 0."""
    expect_empty: bool = False
    """If True, expect zero results."""


@dataclass(frozen=True)
class EdgeQueryCase:
    """A query_graph_edges test case."""

    description: str
    source_label: str | None = None
    target_label: str | None = None
    relation_type: str | None = None
    expected_count: int | None = None
    """Exact edge count expected, or None to only check > 0."""


NODE_QUERIES: list[NodeQueryCase] = [
    NodeQueryCase(
        description="Query all Material nodes",
        entity_types=["Material"],
        expected_labels=["UO2", "PuO2", "Zircaloy-4", "UN", "MOX"],
    ),
    NodeQueryCase(
        description="Query Property nodes by substring 'conduct'",
        entity_types=["Property"],
        query="conduct",
        expected_labels=["Thermal Conductivity"],
    ),
    NodeQueryCase(
        description="Query Experiment nodes",
        entity_types=["Experiment"],
        expected_labels=["IRR-001", "HYD-042"],
    ),
    NodeQueryCase(
        description="Query with invalid entity type returns empty",
        entity_types=["NonexistentType"],
        expect_empty=True,
    ),
    NodeQueryCase(
        description="Query Publication nodes",
        entity_types=["Publication"],
        expected_labels=["Smith 2023"],
    ),
    NodeQueryCase(
        description="Free-text query for 'UO2' (substring matches UO2 and PuO2)",
        query="UO2",
        expected_labels=["UO2", "PuO2"],
    ),
    NodeQueryCase(
        description="Query Condition nodes",
        entity_types=["Condition"],
        expected_labels=["1200K Inert", "600C Steam"],
    ),
]

EDGE_QUERIES: list[EdgeQueryCase] = [
    EdgeQueryCase(
        description="Query edges by relation_type=hasProperty",
        relation_type="hasProperty",
        expected_count=4,
    ),
    EdgeQueryCase(
        description="Query edges by relation_type=measuredIn",
        relation_type="measuredIn",
        expected_count=3,
    ),
    EdgeQueryCase(
        description="Query edges from UO2 node (outgoing only)",
        source_label="UO2",
        expected_count=2,
    ),
]


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_NODES: list[dict[str, Any]] = [
    # Materials (5)
    {"label": "UO2", "node_type": "Material", "confidence": 0.95},
    {"label": "PuO2", "node_type": "Material", "confidence": 0.90},
    {"label": "Zircaloy-4", "node_type": "Material", "confidence": 0.85},
    {"label": "UN", "node_type": "Material", "confidence": 0.80},
    {"label": "MOX", "node_type": "Material", "confidence": 0.88},
    # Properties (2)
    {"label": "Thermal Conductivity", "node_type": "Property", "confidence": 0.99},
    {"label": "Melting Point", "node_type": "Property", "confidence": 0.99},
    # Experiments (2)
    {"label": "IRR-001", "node_type": "Experiment", "confidence": 0.85},
    {"label": "HYD-042", "node_type": "Experiment", "confidence": 0.90},
    # Conditions (2)
    {"label": "1200K Inert", "node_type": "Condition", "confidence": 0.80},
    {"label": "600C Steam", "node_type": "Condition", "confidence": 0.80},
    # Publications (1)
    {"label": "Smith 2023", "node_type": "Publication", "confidence": 0.92},
]

SEED_EDGES: list[dict[str, str]] = [
    # UO2 hasProperty -> Thermal Conductivity
    {"source": "UO2", "target": "Thermal Conductivity", "relation": "hasProperty"},
    # UO2 hasProperty -> Melting Point
    {"source": "UO2", "target": "Melting Point", "relation": "hasProperty"},
    # PuO2 hasProperty -> Melting Point
    {"source": "PuO2", "target": "Melting Point", "relation": "hasProperty"},
    # UN hasProperty -> Thermal Conductivity
    {"source": "UN", "target": "Thermal Conductivity", "relation": "hasProperty"},
    # IRR-001 measuredIn -> UO2
    {"source": "IRR-001", "target": "UO2", "relation": "measuredIn"},
    # HYD-042 measuredIn -> Zircaloy-4
    {"source": "HYD-042", "target": "Zircaloy-4", "relation": "measuredIn"},
    # IRR-001 measuredIn -> PuO2
    {"source": "IRR-001", "target": "PuO2", "relation": "measuredIn"},
]


# ---------------------------------------------------------------------------
# Ontology coverage thresholds
# ---------------------------------------------------------------------------

MIN_ENTITY_TYPES: int = 5
MIN_RELATION_TYPES: int = 10


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _replace_jsonb(metadata) -> None:
    """Replace JSONB/ARRAY columns with JSON for SQLite compatibility."""
    from sqlalchemy import JSON
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB

    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()


def _strip_dangling_fks(metadata) -> None:
    """Remove FKs referencing tables not in the SQLite subset."""
    registered = set(metadata.tables.keys())
    for table in metadata.tables.values():
        for col in table.columns:
            dangling = [
                fk for fk in list(col.foreign_keys)
                if fk._colspec.split(".")[0].strip('"') not in registered
            ]
            for fk in dangling:
                col.foreign_keys.discard(fk)
        table_fks_to_remove = [
            fkc for fkc in list(table.constraints)
            if hasattr(fkc, "_colspec")
            and fkc._colspec.split(".")[0].strip('"') not in registered
        ]
        for fkc in table_fks_to_remove:
            table.constraints.discard(fkc)


async def _create_db_and_seed():
    """Create an in-memory DB, seed nodes and edges, return (session, engine, node_id_map)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from nfm_db.models.kg import Base, KGEdge, KGNode

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        _replace_jsonb(Base.metadata)
        _strip_dangling_fks(Base.metadata)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    session = session_factory()

    # Seed nodes
    node_id_map: dict[str, uuid.UUID] = {}
    for nd in SEED_NODES:
        node = KGNode(
            label=nd["label"],
            node_type=nd["node_type"],
            status="active",
            confidence=nd["confidence"],
            properties={},
        )
        session.add(node)
        await session.flush()
        node_id_map[nd["label"]] = node.id

    # Seed edges
    for ed in SEED_EDGES:
        source_id = node_id_map[ed["source"]]
        target_id = node_id_map[ed["target"]]
        edge = KGEdge(
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type=ed["relation"],
            confidence=0.90,
            properties={},
        )
        session.add(edge)

    await session.commit()
    return session, engine, node_id_map


# ---------------------------------------------------------------------------
# Test runners
# ---------------------------------------------------------------------------


async def _run_node_query_tests(
    session, node_id_map: dict[str, uuid.UUID],
) -> list[str]:
    """Run query_graph_nodes test cases. Returns list of error strings."""
    from nfm_db.services.kg_re import query_graph_nodes

    errors: list[str] = []

    for tc in NODE_QUERIES:
        try:
            results = await query_graph_nodes(
                session,
                entity_types=tc.entity_types,
                query=tc.query,
                limit=100,
            )

            labels = [r["label"] for r in results]

            if tc.expect_empty:
                if labels:
                    errors.append(
                        f"  FAIL [{tc.description}]: "
                        f"expected empty, got {labels}"
                    )
            elif tc.expected_labels is not None:
                if sorted(labels) != sorted(tc.expected_labels):
                    errors.append(
                        f"  FAIL [{tc.description}]: "
                        f"expected {sorted(tc.expected_labels)}, "
                        f"got {sorted(labels)}"
                    )
            else:
                if not labels:
                    errors.append(
                        f"  FAIL [{tc.description}]: "
                        f"expected non-empty result, got empty"
                    )

        except Exception as exc:
            errors.append(f"  ERROR [{tc.description}]: {exc}")

    return errors


async def _run_edge_query_tests(
    session, node_id_map: dict[str, uuid.UUID],
) -> list[str]:
    """Run query_graph_edges test cases. Returns list of error strings."""
    from nfm_db.services.kg_re import query_graph_edges

    errors: list[str] = []

    for tc in EDGE_QUERIES:
        try:
            source_id = node_id_map.get(tc.source_label) if tc.source_label else None
            target_id = node_id_map.get(tc.target_label) if tc.target_label else None

            results = await query_graph_edges(
                session,
                source_id=source_id,
                target_id=target_id,
                relation_type=tc.relation_type,
                limit=100,
            )

            if tc.expected_count is not None:
                if len(results) != tc.expected_count:
                    errors.append(
                        f"  FAIL [{tc.description}]: "
                        f"expected {tc.expected_count} edges, got {len(results)}"
                    )
            elif not results:
                errors.append(
                    f"  FAIL [{tc.description}]: "
                    f"expected non-empty result, got empty"
                )

        except Exception as exc:
            errors.append(f"  ERROR [{tc.description}]: {exc}")

    return errors


def _check_ontology_coverage() -> list[str]:
    """Check that VALID_NODE_TYPES and VALID_RELATION_TYPES meet thresholds."""
    from nfm_db.models.kg import VALID_NODE_TYPES, VALID_RELATION_TYPES

    errors: list[str] = []

    entity_count = len(VALID_NODE_TYPES)
    relation_count = len(VALID_RELATION_TYPES)

    if entity_count < MIN_ENTITY_TYPES:
        errors.append(
            f"  FAIL [Ontology Coverage]: "
            f"only {entity_count} entity types (need ≥{MIN_ENTITY_TYPES})"
        )

    if relation_count < MIN_RELATION_TYPES:
        errors.append(
            f"  FAIL [Ontology Coverage]: "
            f"only {relation_count} relation types (need ≥{MIN_RELATION_TYPES})"
        )

    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run_all() -> list[str]:
    """Run all tests in a single async context. Returns error list."""
    all_errors: list[str] = []

    print("\n--- Node Query Tests ---")
    session, engine, node_id_map = await _create_db_and_seed()
    node_errors = await _run_node_query_tests(session, node_id_map)
    if node_errors:
        all_errors.extend(node_errors)
        for e in node_errors:
            print(e)
    else:
        print(f"  {len(NODE_QUERIES)} node query tests PASSED")

    print("\n--- Edge Query Tests ---")
    edge_errors = await _run_edge_query_tests(session, node_id_map)
    if edge_errors:
        all_errors.extend(edge_errors)
        for e in edge_errors:
            print(e)
    else:
        print(f"  {len(EDGE_QUERIES)} edge query tests PASSED")

    await engine.dispose()
    return all_errors


def main() -> None:
    """Entry point: run all correctness tests and ontology check."""
    import asyncio

    print("=" * 60)
    print("Query Correctness + Ontology Coverage Tests")
    print("=" * 60)

    all_errors: list[str] = []

    # --- Query correctness tests ---
    all_errors.extend(asyncio.run(_run_all()))

    # --- Ontology coverage check ---
    print("\n--- Ontology Coverage Check ---")
    from nfm_db.models.kg import VALID_NODE_TYPES, VALID_RELATION_TYPES

    coverage_errors = _check_ontology_coverage()
    if coverage_errors:
        all_errors.extend(coverage_errors)
        for e in coverage_errors:
            print(e)
    else:
        print(f"  Entity types:   {len(VALID_NODE_TYPES)} (≥{MIN_ENTITY_TYPES} OK)")
        print(f"  Relation types: {len(VALID_RELATION_TYPES)} (≥{MIN_RELATION_TYPES} OK)")

    # --- Summary ---
    print(f"\n{'=' * 60}")
    total_tests = len(NODE_QUERIES) + len(EDGE_QUERIES) + 2  # +2 for coverage
    passed_count = total_tests - len(all_errors)
    print(f"Total: {passed_count}/{total_tests} passed")

    if all_errors:
        print(f"\nFAILED ({len(all_errors)} error(s))")
        sys.exit(1)
    else:
        print("\nPASSED: All query correctness and ontology coverage tests passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
