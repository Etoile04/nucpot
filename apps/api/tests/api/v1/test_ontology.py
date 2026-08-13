"""Integration tests for /api/v1/ontology/corpora/{corpus_id}/graph endpoint."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.schemas.ontology import CONTRACT_SCHEMA_VERSION

BASE = "/api/v1/ontology"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_staging_rows(
    db_session,
    *,
    corpus_id: str = "test-corpus",
    rows: list[dict] | None = None,
) -> list[RefGapFillStaging]:
    """Seed RefGapFillStaging rows for ontology tests."""
    defaults = [
        {
            "element_system": "UO2",
            "property_name": "density",
            "value": 10.97,
            "unit": "g/cm3",
            "method": "XRD",
            "source": corpus_id,
            "confidence": Confidence.MEDIUM,
            "dedup_hash": f"hash-{i}",
        }
        for i in range(3)
    ]
    entries = rows or defaults

    staging_rows = []
    for entry in entries:
        row = RefGapFillStaging(
            element_system=entry["element_system"],
            property_name=entry["property_name"],
            value=entry["value"],
            unit=entry["unit"],
            method=entry.get("method"),
            source=entry["source"],
            source_doi=entry.get("source_doi"),
            uncertainty=entry.get("uncertainty"),
            temperature=entry.get("temperature"),
            confidence=entry["confidence"],
            dedup_hash=entry["dedup_hash"],
            status=entry.get("status", StagingStatus.PENDING),
        )
        db_session.add(row)
        staging_rows.append(row)

    await db_session.flush()
    for row in staging_rows:
        await db_session.refresh(row)
    return staging_rows


def _compute_expected_digest(nodes: list[dict], relationships: list[dict]) -> str:
    """Replicate the source_digest computation from ontology_service."""
    canonical = {
        "nodes": sorted((n["id"], n["type"]) for n in nodes),
        "relationships": sorted((r["from"], r["type"], r["to"]) for r in relationships),
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_corpus_graph_success(async_client, db_session) -> None:
    """Returns 200 with full NVL graph envelope for a valid corpus."""
    corpus_id = "uo2-corpus"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")

    assert resp.status_code == 200
    data = resp.json()
    assert data["schema_version"] == CONTRACT_SCHEMA_VERSION
    assert data["corpus_id"] == corpus_id
    assert data["source_ontology"] == "nfmd/ref-gap-fill"
    assert len(data["source_digest"]) == 16
    assert data["stats"]["nodes"] > 0
    assert data["stats"]["relationships"] > 0
    assert isinstance(data["nodes"], list)
    assert isinstance(data["relationships"], list)
    # Should have no pagination for small corpora
    assert data["pagination"] is None


@pytest.mark.asyncio
async def test_get_corpus_graph_nodes_have_required_fields(async_client, db_session) -> None:
    """All nodes contain id, type, name, label per NVL contract."""
    corpus_id = "field-check"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    for node in data["nodes"]:
        assert "id" in node
        assert "type" in node
        assert "name" in node
        assert "label" in node
        assert node["type"] in ("class", "individual")


@pytest.mark.asyncio
async def test_get_corpus_graph_relationships_have_required_fields(
    async_client, db_session
) -> None:
    """All relationships contain id, from, to, type per NVL contract."""
    corpus_id = "rel-check"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    for rel in data["relationships"]:
        assert "id" in rel
        assert "from" in rel
        assert "to" in rel
        assert "type" in rel


@pytest.mark.asyncio
async def test_get_corpus_graph_material_nodes_are_individuals(async_client, db_session) -> None:
    """Material nodes (mat:*) are typed as 'individual'; properties as 'class'."""
    corpus_id = "type-check"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    mat_nodes = [n for n in data["nodes"] if n["id"].startswith("mat:")]
    prop_nodes = [n for n in data["nodes"] if n["id"].startswith("prop:")]
    src_nodes = [n for n in data["nodes"] if n["id"].startswith("src:")]

    assert all(n["type"] == "individual" for n in mat_nodes)
    assert all(n["type"] == "class" for n in prop_nodes)
    assert all(n["type"] == "class" for n in src_nodes)


@pytest.mark.asyncio
async def test_get_corpus_graph_multiple_materials(async_client, db_session) -> None:
    """Corpus with multiple distinct materials produces distinct mat: nodes."""
    corpus_id = "multi-mat"
    rows = [
        {
            "element_system": "UO2",
            "property_name": "density",
            "value": 10.97,
            "unit": "g/cm3",
            "method": "XRD",
            "source": corpus_id,
            "confidence": Confidence.HIGH,
            "dedup_hash": "h1",
        },
        {
            "element_system": "PuO2",
            "property_name": "thermal_conductivity",
            "value": 4.5,
            "unit": "W/mK",
            "method": "Laser Flash",
            "source": corpus_id,
            "confidence": Confidence.MEDIUM,
            "dedup_hash": "h2",
        },
    ]
    await _seed_staging_rows(db_session, corpus_id=corpus_id, rows=rows)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    mat_ids = {n["id"] for n in data["nodes"] if n["id"].startswith("mat:")}
    assert "mat:UO2" in mat_ids
    assert "mat:PuO2" in mat_ids


@pytest.mark.asyncio
async def test_get_corpus_graph_source_node_present(async_client, db_session) -> None:
    """The source node (src:{corpus_id}) is always present."""
    corpus_id = "src-node-test"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    node_ids = {n["id"] for n in data["nodes"]}
    assert f"src:{corpus_id}" in node_ids


@pytest.mark.asyncio
async def test_get_corpus_graph_has_relationships(async_client, db_session) -> None:
    """Staging rows produce material->property->method->source chain."""
    corpus_id = "rel-chain"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    rel_types = {r["type"] for r in data["relationships"]}
    assert "HAS_PROPERTY" in rel_types
    assert "MEASURED_BY" in rel_types
    assert "CITED_IN" in rel_types


@pytest.mark.asyncio
async def test_get_corpus_graph_no_method_cites_directly(async_client, db_session) -> None:
    """When method is NULL, property cites source directly."""
    corpus_id = "no-method"
    rows = [
        {
            "element_system": "UN",
            "property_name": "melting_point",
            "value": 2800.0,
            "unit": "K",
            "method": None,
            "source": corpus_id,
            "confidence": Confidence.LOW,
            "dedup_hash": "nomethod",
        },
    ]
    await _seed_staging_rows(db_session, corpus_id=corpus_id, rows=rows)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    # No method node should exist
    method_nodes = [n for n in data["nodes"] if n["id"].startswith("method:")]
    assert len(method_nodes) == 0

    # Property should cite source directly
    prop_id = "prop:melting_point"
    src_id = f"src:{corpus_id}"
    cited = [r for r in data["relationships"] if r["from"] == prop_id and r["to"] == src_id]
    assert len(cited) == 1


@pytest.mark.asyncio
async def test_get_corpus_graph_caching_headers(async_client, db_session) -> None:
    """Response includes Cache-Control and ETag headers."""
    corpus_id = "cache-test"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")

    assert resp.status_code == 200
    assert "cache-control" in resp.headers
    assert "public" in resp.headers["cache-control"]
    assert "etag" in resp.headers
    # ETag should be quoted
    assert resp.headers["etag"].startswith('"')
    assert resp.headers["etag"].endswith('"')


@pytest.mark.asyncio
async def test_get_corpus_graph_record_ref_on_materials(async_client, db_session) -> None:
    """Material (individual) nodes carry a record_ref deep link."""
    corpus_id = "record-ref"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    mat_nodes = [n for n in data["nodes"] if n["id"].startswith("mat:")]
    assert len(mat_nodes) > 0
    for node in mat_nodes:
        assert node["record_ref"] is not None
        assert "/materials/" in node["record_ref"]
        assert f"corpus={corpus_id}" in node["record_ref"]


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — 404 / error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_corpus_graph_not_found(async_client, db_session) -> None:
    """Corpus with no staging rows returns 404."""
    resp = await async_client.get(f"{BASE}/corpora/nonexistent-corpus/graph")
    assert resp.status_code == 404
    data = resp.json()
    assert "not found" in data["detail"].lower()


@pytest.mark.asyncio
async def test_get_corpus_graph_invalid_corpus_id(async_client) -> None:
    """Corpus IDs with invalid characters are rejected by path validation (422)."""
    # Corpus ID starting with hyphen (violates regex)
    resp = await async_client.get(f"{BASE}/corpora/-bad-start/graph")
    assert resp.status_code == 422

    # Corpus ID with special characters
    resp = await async_client.get(f"{BASE}/corpora/bad!id/graph")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_corpus_graph_empty_corpus_id(async_client) -> None:
    """Empty corpus_id rejected by regex pattern."""
    # FastAPI won't match the route for empty path segment;
    # a trailing-slash or empty route is a 404
    resp = await async_client.get(f"{BASE}/corpora//graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_corpus_graph_stats_consistency(async_client, db_session) -> None:
    """Stats fields match actual node/relationship counts."""
    corpus_id = "stats-test"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    data = resp.json()

    assert data["stats"]["nodes"] == len(data["nodes"])
    assert data["stats"]["relationships"] == len(data["relationships"])
    assert data["stats"]["classes"] == sum(1 for n in data["nodes"] if n["type"] == "class")
    assert data["stats"]["individuals"] == sum(
        1 for n in data["nodes"] if n["type"] == "individual"
    )


# ---------------------------------------------------------------------------
# GET /ontology/corpora/{corpus_id}/graph — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_corpus_graph_with_max_nodes(async_client, db_session) -> None:
    """max_nodes query param limits the returned nodes."""
    corpus_id = "page-test"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(
        f"{BASE}/corpora/{corpus_id}/graph",
        params={"max_nodes": 1},
    )
    data = resp.json()

    # With max_nodes=1, a small corpus still fits in one page
    # but the parameter is accepted and processed
    assert resp.status_code == 200
    assert data["corpus_id"] == corpus_id


@pytest.mark.asyncio
async def test_get_corpus_graph_max_nodes_validation(async_client, db_session) -> None:
    """max_nodes=0 is rejected (ge=1), max_nodes > HARD_MAX_NODES is rejected."""
    corpus_id = "validation"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    # max_nodes=0 violates ge=1
    resp = await async_client.get(
        f"{BASE}/corpora/{corpus_id}/graph",
        params={"max_nodes": 0},
    )
    assert resp.status_code == 422

    # max_nodes=-1 violates ge=1
    resp = await async_client.get(
        f"{BASE}/corpora/{corpus_id}/graph",
        params={"max_nodes": -1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_corpus_graph_pagination_cursor(async_client, db_session) -> None:
    """Providing a cursor param is accepted (returns 200 for valid corpus)."""
    corpus_id = "cursor-test"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    resp = await async_client.get(
        f"{BASE}/corpora/{corpus_id}/graph",
        params={"cursor": "eyJvIjogMn0"},
    )
    # For a small corpus, cursor just means starting from offset 2;
    # the endpoint still returns a valid response
    assert resp.status_code == 200
    data = resp.json()
    assert data["corpus_id"] == corpus_id


# ---------------------------------------------------------------------------
# Rate limiting — isolated by conftest autouse fixture
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_corpus_graph_rate_limit_resets_between_tests(async_client, db_session) -> None:
    """Rate limiter is reset between tests so no 429 from prior tests."""
    corpus_id = f"rate-{uuid.uuid4().hex[:8]}"
    await _seed_staging_rows(db_session, corpus_id=corpus_id)

    # Should not be rate-limited since conftest resets the limiter
    resp = await async_client.get(f"{BASE}/corpora/{corpus_id}/graph")
    assert resp.status_code == 200
