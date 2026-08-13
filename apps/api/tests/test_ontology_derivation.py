"""Derivation service tests (T3: RED phase).

``derive_ontology_graph`` reads ``_ref_gap_fill_staging`` filtered by
``corpus_id`` (= ``source``) and derives a read-only ontology graph:
material-(HAS_PROPERTY)->property-(MEASURED_BY)->method-(CITED_IN)->source.
No new persistence (NFM-266 invariant #3).
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.ontology_service import (
    CorpusNotFoundError,
    derive_ontology_graph,
)
from tests.nvl_conformance import assert_nvl_contract
from tests.ontology_seed import seed_corpus

_CORPUS = "smirnov2014"
_DIGEST_RE = re.compile(r"^[a-f0-9]{16}$")


def _node_map(graph) -> dict[str, dict]:
    return {n["id"]: n for n in graph.model_dump(by_alias=True)["nodes"]}


def _edge_set(graph) -> set[tuple[str, str, str]]:
    return {
        (r["from"], r["to"], r["type"]) for r in graph.model_dump(by_alias=True)["relationships"]
    }


@pytest.mark.asyncio
async def test_derive_builds_material_property_method_source_graph(
    db_session: AsyncSession,
) -> None:
    """Two staging rows derive the full 4-hop graph topology."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
            {
                "element_system": "U",
                "property_name": "bulk_modulus",
                "value": 200.0,
                "unit": "GPa",
                "method": "EXP",
            },
        ],
    )

    graph = await derive_ontology_graph(db_session, _CORPUS)

    nodes = _node_map(graph)
    # Materials are individuals; property/method/source concepts are classes.
    assert nodes["mat:UO2"]["type"] == "individual"
    assert nodes["mat:U"]["type"] == "individual"
    for class_node in (
        "prop:lattice_constant",
        "prop:bulk_modulus",
        "method:DFT",
        "method:EXP",
        f"src:{_CORPUS}",
    ):
        assert nodes[class_node]["type"] == "class", class_node

    edges = _edge_set(graph)
    assert ("mat:UO2", "prop:lattice_constant", "HAS_PROPERTY") in edges
    assert ("prop:lattice_constant", "method:DFT", "MEASURED_BY") in edges
    assert ("method:DFT", f"src:{_CORPUS}", "CITED_IN") in edges
    assert ("mat:U", "prop:bulk_modulus", "HAS_PROPERTY") in edges
    assert ("prop:bulk_modulus", "method:EXP", "MEASURED_BY") in edges
    assert ("method:EXP", f"src:{_CORPUS}", "CITED_IN") in edges


@pytest.mark.asyncio
async def test_derive_envelope_is_versioned_and_conforms(
    db_session: AsyncSession,
) -> None:
    """Derived envelope is the versioned NFM-227 contract and passes the gate."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )

    graph = await derive_ontology_graph(db_session, _CORPUS)

    # Phase 2 (NFM-267): additive minor bump — record_ref now populated.
    assert graph.schema_version == "1.1"
    assert graph.corpus_id == _CORPUS
    assert _DIGEST_RE.match(graph.source_digest), graph.source_digest
    # Cross-check with the dual-provider conformance checker.
    assert_nvl_contract(graph.model_dump(by_alias=True), corpus_id=_CORPUS)


@pytest.mark.asyncio
async def test_derive_stats_match_payload(db_session: AsyncSession) -> None:
    """stats counts are consistent with the derived nodes/relationships."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
            {
                "element_system": "U",
                "property_name": "lattice_constant",
                "value": 3.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )
    graph = await derive_ontology_graph(db_session, _CORPUS)
    payload = graph.model_dump(by_alias=True)
    # 2 materials (individuals) + 1 property + 1 method + 1 source (classes).
    assert graph.stats.nodes == 5
    assert graph.stats.individuals == 2
    assert graph.stats.classes == 3
    # 2 HAS_PROPERTY + 1 MEASURED_BY (deduped) + 1 CITED_IN (deduped) = 4.
    assert graph.stats.relationships == len(payload["relationships"]) == 4


@pytest.mark.asyncio
async def test_derive_is_read_only_no_persistence_side_effect(
    db_session: AsyncSession,
) -> None:
    """Derivation must not write — re-deriving yields an identical digest."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
        ],
    )
    first = await derive_ontology_graph(db_session, _CORPUS)
    second = await derive_ontology_graph(db_session, _CORPUS)
    assert first.source_digest == second.source_digest


@pytest.mark.asyncio
async def test_derive_unknown_corpus_raises(db_session: AsyncSession) -> None:
    """An empty/unknown corpus raises CorpusNotFoundError (endpoint maps to 404)."""
    with pytest.raises(CorpusNotFoundError):
        await derive_ontology_graph(db_session, "no-such-corpus")


@pytest.mark.asyncio
async def test_derive_populates_record_ref_on_material_individuals(
    db_session: AsyncSession,
) -> None:
    """mat: individual nodes carry a deterministic, relative record_ref deep link;
    class nodes (prop/method/src) omit it — no single material record (Phase 2 NFM-267)."""
    await seed_corpus(
        db_session,
        source=_CORPUS,
        rows=[
            {
                "element_system": "UO2",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
            },
            {
                "element_system": "U",
                "property_name": "bulk_modulus",
                "value": 200.0,
                "unit": "GPa",
                "method": "EXP",
            },
        ],
    )
    graph = await derive_ontology_graph(db_session, _CORPUS)
    nodes = _node_map(graph)

    # Material individuals carry an origin-relative, intent-encoded deep link.
    assert nodes["mat:UO2"]["record_ref"] == "/materials/UO2?corpus=smirnov2014"
    assert nodes["mat:U"]["record_ref"] == "/materials/U?corpus=smirnov2014"

    # Class nodes carry no single-material record link.
    for class_node in (
        "prop:lattice_constant",
        "method:DFT",
        f"src:{_CORPUS}",
    ):
        assert nodes[class_node]["record_ref"] is None, class_node


def test_build_record_ref_is_deterministic_relative_and_encoded() -> None:
    """The pure deep-link builder is a deterministic, origin-relative, encoded URL.

    No DB, no new storage — a pure function of the node's stable identity. Relative
    by construction ⇒ shareable + session-proof (Phase 2 NFM-267 §3).
    """
    from nfm_db.services.ontology_service import build_record_ref

    # Deterministic: same identity ⇒ same link.
    expected = "/materials/UO2?corpus=smirnov2014"
    assert build_record_ref("smirnov2014", "UO2") == expected
    assert build_record_ref("smirnov2014", "UO2") == build_record_ref("smirnov2014", "UO2")

    # Origin-relative (no scheme/host) — shareable + session-proof.
    ref = build_record_ref("smirnov2014", "UO2")
    assert ref.startswith("/")
    assert "://" not in ref

    # Optional property narrows to a material+property edge.
    assert build_record_ref("smirnov2014", "UO2", property_name="lattice_constant") == (
        "/materials/UO2?corpus=smirnov2014&property=lattice_constant"
    )

    # URL-encodes unsafe path/query segments.
    assert build_record_ref("smirnov 2014", "U O2") == ("/materials/U%20O2?corpus=smirnov%202014")
