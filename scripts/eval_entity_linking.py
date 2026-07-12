#!/usr/bin/env python3
"""Entity linking evaluation: assert ≥90% dedup rate on a synthetic corpus.

Standalone script — sets up an in-memory SQLite database, seeds it with
known KG nodes (exact matches, alias matches, and novel entities), then
runs the ``EntityLinker`` from ``kg_re`` against a test corpus of
``ExtractedEntity`` objects.

Exit codes:
    0  — evaluation passed (dedup rate ≥ 90%)
    1  — evaluation failed (dedup rate < 90%) or unexpected error
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Ensure apps/api/src is importable
# ---------------------------------------------------------------------------
import os
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_API_SRC = str(_REPO_ROOT / "apps" / "api" / "src")
if _API_SRC not in sys.path:
    sys.path.insert(0, _API_SRC)

# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestCase:
    """A single entity linking test case."""

    entity_label: str
    entity_type: str
    entity_aliases: list[str] = field(default_factory=list)
    expected_match: str | None = None
    """Label of the KG node this entity should link to, or None for novel."""


# Test corpus: 20 entities, mixing duplicates and novel entries.
TEST_CORPUS: list[TestCase] = [
    # --- Exact label + type matches (should link) ---
    TestCase("Uranium Dioxide", "Material", expected_match="Uranium Dioxide"),
    TestCase("Thermal Conductivity", "Property", expected_match="Thermal Conductivity"),
    TestCase("Irradiation Test #42", "Experiment", expected_match="Irradiation Test #42"),
    # --- Alias matches (should link via alias) ---
    TestCase("UO2", "Material", expected_match="Uranium Dioxide"),
    TestCase("UO_2", "Material", expected_match="Uranium Dioxide"),
    TestCase("TC", "Property", expected_match="Thermal Conductivity"),
    # --- Novel entities (no match expected) ---
    # Note: Plutonium Oxide is in seed nodes, so it's NOT a novel entity.
    # Use a truly novel material name instead.
    TestCase("Electrical Resistivity", "Property", expected_match=None),
    TestCase("Pellet Fabrication", "Experiment", expected_match=None),
    TestCase("1200K Inert Atmosphere", "Condition", expected_match=None),
    TestCase("Finkelstein 2001", "Publication", expected_match=None),
    # --- More exact matches ---
    TestCase("Melting Point", "Property", expected_match="Melting Point"),
    TestCase("Density", "Property", expected_match="Density"),
    # --- More alias matches ---
    TestCase("PuO2", "Material", expected_match="Plutonium Oxide"),
    # --- More novel entities ---
    TestCase("Zirconium Alloy", "Material", expected_match=None),
    TestCase("Corrosion Rate", "Property", expected_match=None),
    TestCase("Hydrostatic Test", "Experiment", expected_match=None),
    TestCase("600C Steam", "Condition", expected_match=None),
    TestCase("Smith 2023", "Publication", expected_match=None),
    # --- Duplicate of an earlier exact match ---
    TestCase("Uranium Dioxide", "Material", expected_match="Uranium Dioxide"),
    # --- Edge: case-insensitive alias match ---
    TestCase("dioxide fuel", "Material", expected_match="Uranium Dioxide"),
]


# Seed nodes for the in-memory DB
SEED_NODES: list[dict[str, Any]] = [
    {
        "label": "Uranium Dioxide",
        "node_type": "Material",
        "aliases": '["UO2", "UO_2", "UO₂", "Dioxide Fuel"]',
        "status": "active",
        "confidence": 0.95,
    },
    {
        "label": "Plutonium Oxide",
        "node_type": "Material",
        "aliases": '["PuO2", "PuO₂"]',
        "status": "active",
        "confidence": 0.90,
    },
    {
        "label": "Thermal Conductivity",
        "node_type": "Property",
        "aliases": '["TC", "k_th"]',
        "status": "active",
        "confidence": 0.98,
    },
    {
        "label": "Melting Point",
        "node_type": "Property",
        "aliases": None,
        "status": "active",
        "confidence": 0.99,
    },
    {
        "label": "Density",
        "node_type": "Property",
        "aliases": '["ρ"]',
        "status": "active",
        "confidence": 0.99,
    },
    {
        "label": "Irradiation Test #42",
        "node_type": "Experiment",
        "aliases": None,
        "status": "active",
        "confidence": 0.85,
    },
]


# ---------------------------------------------------------------------------
# DB session helpers (async SQLite in-memory)
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


async def _create_db_session():
    """Create an async SQLite in-memory session with KG tables."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from nfm_db.models.kg import Base, KGNode

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        _replace_jsonb(Base.metadata)
        _strip_dangling_fks(Base.metadata)
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session(), engine


async def _seed_nodes(async_session) -> None:
    """Insert seed KG nodes into the database."""
    from nfm_db.models.kg import KGNode

    for node_data in SEED_NODES:
        node = KGNode(
            label=node_data["label"],
            node_type=node_data["node_type"],
            aliases=node_data["aliases"],
            status=node_data.get("status", "active"),
            confidence=node_data.get("confidence", 1.0),
            properties={},
        )
        async_session.add(node)
    await async_session.commit()


# ---------------------------------------------------------------------------
# Evaluation logic
# ---------------------------------------------------------------------------

MIN_DEDUP_RATE: float = 0.90


async def run_evaluation() -> tuple[int, int, list[str]]:
    """Run entity linking against the test corpus.

    Returns:
        Tuple of (correct_links, total_linkable, errors).
    """
    from nfm_db.services.kg_re import EntityLinker, ExtractedEntity

    linker = EntityLinker()

    session, engine = await _create_db_session()
    try:
        await _seed_nodes(session)

        correct = 0
        total_linkable = 0
        errors: list[str] = []

        for tc in TEST_CORPUS:
            entity = ExtractedEntity(
                label=tc.entity_label,
                entity_type=tc.entity_type,
                confidence=0.9,
                aliases=tc.entity_aliases,
            )

            matched_node = await linker.find_matching_node(session, entity)

            if tc.expected_match is not None:
                total_linkable += 1
                if matched_node is not None and matched_node.label == tc.expected_match:
                    correct += 1
                else:
                    matched_label = matched_node.label if matched_node else "None"
                    errors.append(
                        f"  FAIL: '{tc.entity_label}' ({tc.entity_type}) "
                        f"expected '{tc.expected_match}', got '{matched_label}'"
                    )
            else:
                # Novel entity — should NOT match anything
                if matched_node is not None:
                    errors.append(
                        f"  WARN: novel entity '{tc.entity_label}' unexpectedly "
                        f"matched '{matched_node.label}' (false positive)"
                    )
        return correct, total_linkable, errors
    finally:
        await engine.dispose()


def main() -> None:
    """Entry point: run eval, report, exit with code."""
    import asyncio

    print("=" * 60)
    print("Entity Linking Evaluation")
    print(f"Corpus size: {len(TEST_CORPUS)} entities")
    print(f"Minimum dedup rate: {MIN_DEDUP_RATE:.0%}")
    print("=" * 60)

    correct, total_linkable, errors = asyncio.run(run_evaluation())

    rate = correct / total_linkable if total_linkable > 0 else 0.0
    passed = rate >= MIN_DEDUP_RATE and len(errors) == 0

    print(f"\nResults:")
    print(f"  Correct links:  {correct}/{total_linkable}")
    print(f"  Dedup rate:     {rate:.1%}")
    print(f"  Threshold:      {MIN_DEDUP_RATE:.0%}")

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for e in errors:
            print(e)

    print()
    if passed:
        print("PASSED: Entity linking dedup rate meets threshold.")
    else:
        print("FAILED: Entity linking dedup rate below threshold or errors present.")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
