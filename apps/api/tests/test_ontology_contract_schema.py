"""Contract schema tests for the versioned NFM-227 NVL envelope (T1: RED phase).

The versioned contract is sourced verbatim from ontofuel
``schemas/nvl_contract.schema.json`` (NFM-227 D2) — we do not invent a shape.
Element shape must match what the Phase 0 viewer actually consumes
(``{id,type,name,label,comment,uri,color,size}`` nodes,
``{id,from,to,type,label}`` relationships), so a Phase 1 data-source swap
breaks zero viewer code (contract-as-firewall invariant).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.schemas.ontology import (
    CONTRACT_SCHEMA_VERSION,
    OntologyGraphResponse,
    OntologyNode,
    OntologyPagination,
    OntologyRelationship,
    OntologyStats,
)

# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_envelope_has_required_nfm227_fields() -> None:
    """Envelope carries the NFM-227 required fields + corpus_id."""
    env = OntologyGraphResponse(
        corpus_id="smirnov2014",
        generated_at="2026-06-18T00:00:00Z",
        source_ontology="nfmd/ref-gap-fill",
        source_digest="0d986d21a5a2b230",
        stats=OntologyStats(nodes=1, relationships=1, classes=1, individuals=0),
        nodes=[],
        relationships=[],
    )
    dumped = env.model_dump(by_alias=True)
    for field in (
        "schema_version",
        "generated_at",
        "source_ontology",
        "source_digest",
        "stats",
        "corpus_id",
        "nodes",
        "relationships",
    ):
        assert field in dumped, f"envelope missing required field {field!r}"


def test_envelope_defaults_to_pinned_contract_version() -> None:
    """schema_version defaults to the pinned contract version (1.1 in Phase 2)."""
    assert CONTRACT_SCHEMA_VERSION == "1.1"
    env = OntologyGraphResponse(
        corpus_id="c",
        generated_at="2026-06-18T00:00:00Z",
        source_ontology="x",
        source_digest="0d986d21a5a2b230",
        stats=OntologyStats(),
    )
    assert env.schema_version == "1.1"


def test_envelope_rejects_malformed_source_digest() -> None:
    """source_digest must be exactly 16 lowercase hex chars (sha256 short)."""
    with pytest.raises(ValidationError):
        OntologyGraphResponse(
            corpus_id="c",
            generated_at="2026-06-18T00:00:00Z",
            source_ontology="x",
            source_digest="not-a-hex-digest",
            stats=OntologyStats(),
        )


def test_envelope_pagination_is_optional() -> None:
    """pagination is optional in Phase 1 (only present when chunking)."""
    env = OntologyGraphResponse(
        corpus_id="c",
        generated_at="2026-06-18T00:00:00Z",
        source_ontology="x",
        source_digest="0d986d21a5a2b230",
        stats=OntologyStats(),
    )
    assert env.pagination is None


# ---------------------------------------------------------------------------
# Node element shape (viewer compatibility)
# ---------------------------------------------------------------------------


def test_node_accepts_viewer_shape_with_class_type() -> None:
    """Node matches the viewer's real element shape; type controlled."""
    node = OntologyNode(
        id="prop:lattice_constant",
        type="class",
        name="lattice_constant",
        label="{name}",
        comment="Crystal lattice constant",
        uri="http://example.org/materials#lattice_constant",
        color="#4A90E2",
        size=30,
    )
    dumped = node.model_dump(by_alias=True, exclude_none=True)
    assert dumped["id"] == "prop:lattice_constant"
    assert dumped["type"] == "class"


@pytest.mark.parametrize("bad_type", ["Class", "node", "", "CLASS"])
def test_node_type_is_controlled_to_class_or_individual(bad_type: str) -> None:
    """type must be exactly 'class' or 'individual' (drift firewall)."""
    with pytest.raises(ValidationError):
        OntologyNode(id="x", type=bad_type)


def test_node_record_ref_is_optional_nullable_slot() -> None:
    """record_ref is the reserved Phase 2 deep-link slot (optional/nullable)."""
    node = OntologyNode(id="x", type="class")
    assert node.record_ref is None  # absent by default in Phase 1


# ---------------------------------------------------------------------------
# Relationship element shape — the critical `from` alias
# ---------------------------------------------------------------------------


def test_relationship_serializes_from_alias_for_viewer() -> None:
    """Relationship emits `from` (not `from_`) — the key the viewer reads."""
    rel = OntologyRelationship(
        id="rel-1",
        **{"from": "mat:UO2"},
        to="prop:lattice_constant",
        type="HAS_PROPERTY",
        label="has property",
    )
    dumped = rel.model_dump(by_alias=True)
    assert dumped["from"] == "mat:UO2"
    assert dumped["to"] == "prop:lattice_constant"
    assert dumped["type"] == "HAS_PROPERTY"
    # The Python keyword-clash field name must not leak into the wire format.
    assert "from_" not in dumped


def test_relationship_accepts_from_on_input() -> None:
    """Relationship can be built from derived dicts keyed by `from`."""
    rel = OntologyRelationship.model_validate(
        {
            "id": "rel-2",
            "from": "prop:lattice_constant",
            "to": "method:DFT",
            "type": "MEASURED_BY",
        }
    )
    assert rel.from_ == "prop:lattice_constant"


def test_relationship_type_accepts_domain_verbs() -> None:
    """Relationship type is permissive (INSTANCE_OF/SUBCLASS_OF/domain verbs)."""
    for verb in ("INSTANCE_OF", "SUBCLASS_OF", "HAS_PROPERTY", "CITED_IN"):
        rel = OntologyRelationship(id="r", **{"from": "a"}, to="b", type=verb)
        assert rel.type == verb


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_carries_four_counts() -> None:
    """stats has nodes/relationships/classes/individuals (NFM-227)."""
    stats = OntologyStats(nodes=10, relationships=7, classes=4, individuals=6)
    dumped = stats.model_dump()
    assert dumped == {
        "nodes": 10,
        "relationships": 7,
        "classes": 4,
        "individuals": 6,
    }


def test_pagination_shape() -> None:
    """pagination carries optional next_cursor + total."""
    page = OntologyPagination(next_cursor="cursor-abc", total=9000)
    assert page.next_cursor == "cursor-abc"
    assert page.total == 9000
    assert OntologyPagination(total=5).next_cursor is None
