"""Contract tests for NFM-3478 B2' — scoped Property→Condition edges.

Pins the GraphBuilder fix: temperature / pressure / method conditions
extracted per-property-dict must land as ``hasCondition`` edges from
exactly the property they were scoped to (raw extraction dict is the
ground truth), never cross-linked to sibling properties of the same
material.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.services.kg_re import ExtractedEntity, GraphBuilder, RelationExtractor


async def _build(db_session: AsyncSession, extracted: list[dict]):
    """Run a GraphBuilder pass with the linker stubbed to always miss."""
    builder = GraphBuilder(session=db_session, corpus_id="test-corpus", sync_to_age=False)
    with patch.object(
        builder._linker,
        "find_matching_node",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await builder.build_from_extraction(extracted)
    nodes = (await db_session.execute(select(KGNode))).scalars().all()
    edges = (await db_session.execute(select(KGEdge))).scalars().all()
    return nodes, edges


def _cond_edges(edges: list, nodes: list) -> list[tuple[str, str]]:
    """(prop_label, cond_label) pairs for hasCondition edges."""
    by_id = {n.id: n.label for n in nodes}
    return [
        (by_id[e.source_node_id], by_id[e.target_node_id])
        for e in edges
        if e.relation_type == "hasCondition"
    ]


@pytest.mark.asyncio
async def test_conditions_edge_to_their_own_property_only(db_session):
    """temp_C on bulk modulus must NOT leak onto lattice_constant_a."""
    extracted = [
        {
            "material_name": "U",
            "property": "体积模量",
            "value": "97",
            "unit": "GPa",
            "conditions": {"temp_C": "826.85", "simulation_method": "ADP"},
        },
        {
            "material_name": "U",
            "property": "晶格参数a",
            "value": "2.8552",
            "unit": "Å",
        },
    ]
    nodes, edges = await _build(db_session, extracted)

    pairs = _cond_edges(edges, nodes)
    assert ("体积模量", "temp_C=826.85") in pairs, (
        f"bulk modulus should carry its own temperature condition, got {pairs}"
    )
    assert ("体积模量", "simulation_method=ADP") in pairs
    # lattice property had no conditions of its own → no condition edges
    lattice_pairs = [p for p, _c in pairs if p == "晶格参数a"]
    assert lattice_pairs == []


@pytest.mark.asyncio
async def test_same_condition_label_scoped_to_two_properties(db_session):
    """One shared condition label in two prop dicts yields edges to both."""
    extracted = [
        {
            "material_name": "U",
            "property": "bulk_modulus",
            "value": "97",
            "unit": "GPa",
            "conditions": {"temp_C": "300"},
        },
        {
            "material_name": "U",
            "property": "formation_energy",
            "value": "0.045",
            "unit": "eV/atom",
            "conditions": {"temp_C": "300"},
        },
    ]
    nodes, edges = await _build(db_session, extracted)
    pairs = _cond_edges(edges, nodes)
    assert ("bulk_modulus", "temp_C=300") in pairs
    assert ("formation_energy", "temp_C=300") in pairs
    # exactly one node for the shared condition label
    cond_nodes = [n for n in nodes if n.node_type == "Condition" and n.label == "temp_C=300"]
    assert len(cond_nodes) == 1


@pytest.mark.asyncio
async def test_no_condition_edges_when_dict_has_none(db_session):
    extracted = [
        {
            "material_name": "U",
            "property": "bulk_modulus",
            "value": "97",
            "unit": "GPa",
        },
    ]
    nodes, edges = await _build(db_session, extracted)
    assert _cond_edges(edges, nodes) == []
    assert not [n for n in nodes if n.node_type == "Condition"]


def _extract_relations(entities: list[ExtractedEntity]):
    """extract_relations lives on RelationExtractor (kg_re.py:147+)."""
    return RelationExtractor.extract_relations(RelationExtractor(), entities)


def test_extract_relations_scoped_pairs_precede_type_rules():
    """Unit-level: scoped hasCondition pairs are emitted even when the
    global (Property, Condition) type-pair rule doesn't exist."""
    entities = [
        ExtractedEntity(
            label="bulk_modulus",
            entity_type="Property",
            parent_property_label="bulk_modulus",
        ),
        ExtractedEntity(
            label="temp_C=826.85",
            entity_type="Condition",
            parent_property_label="bulk_modulus",
        ),
    ]
    rels = _extract_relations(entities)
    scoped = [r for r in rels if r.relation_type == "hasCondition"]
    assert scoped, "scoped hasCondition edge must be emitted"
    pair = {(r.source_label, r.target_label) for r in scoped}
    assert ("bulk_modulus", "temp_C=826.85") in pair


def test_extract_relations_ignores_orphan_conditions():
    """Conditions without a scoped parent (legacy payloads) get no edges."""
    entities = [
        ExtractedEntity(label="bulk_modulus", entity_type="Property"),
        ExtractedEntity(label="temp_C=300", entity_type="Condition"),
    ]
    rels = _extract_relations(entities)
    assert [r for r in rels if r.relation_type == "hasCondition"] == []
