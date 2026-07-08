"""Integration tests for ontology query API using OntoFuel seed data (NFM-869).

Tests the GET /api/v1/ontology/corpora/{corpus_id}/graph endpoint with the
larger OntoFuel seed fixture (5 materials, 20 staging rows) to exercise:

  - Full NVL graph derivation with multiple materials and methods
  - Material->property->method->source relationship chains
  - Node type classification (class vs individual)
  - Source digest determinism
  - Record-ref deep links on material nodes
  - Stats consistency (classes + individuals = total nodes)
  - Pagination with max_nodes
  - Edge cases: missing method, no staging rows
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.schemas.ontology import CONTRACT_SCHEMA_VERSION

BASE = "/api/v1/ontology"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_SEED_PATH = _FIXTURES / "ontofuel_seed_sample.json"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_seed() -> dict:
    """Load the OntoFuel seed fixture."""
    return json.loads(_SEED_PATH.read_text())


@pytest.fixture
def seed_data() -> dict:
    """Return the parsed ontofuel seed fixture."""
    return _load_seed()


async def _seed_from_fixture(
    db_session,
    seed: dict | None = None,
) -> list[RefGapFillStaging]:
    """Seed RefGapFillStaging rows from the OntoFuel fixture."""
    data = seed or _load_seed()
    corpus_id = data["corpus_id"]
    confidence_map = {
        "HIGH": Confidence.HIGH,
        "MEDIUM": Confidence.MEDIUM,
        "LOW": Confidence.LOW,
    }

    staging_rows = []
    for entry in data["rows"]:
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
            confidence=confidence_map[entry["confidence"]],
            dedup_hash=entry["dedup_hash"],
            status=StagingStatus.PENDING,
        )
        db_session.add(row)
        staging_rows.append(row)

    await db_session.flush()
    for row in staging_rows:
        await db_session.refresh(row)
    return staging_rows


# ---------------------------------------------------------------------------
# Full corpus derivation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_corpus_graph_envelope(async_client, db_session, seed_data) -> None:
    """Full OntoFuel corpus returns valid NVL envelope with all required fields."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )

    assert resp.status_code == 200
    data = resp.json()

    assert data["schema_version"] == CONTRACT_SCHEMA_VERSION
    assert data["corpus_id"] == seed_data["corpus_id"]
    assert data["source_ontology"] == "nfmd/ref-gap-fill"
    assert len(data["source_digest"]) == 16
    assert data["stats"]["nodes"] > 0
    assert data["stats"]["relationships"] > 0
    assert data["stats"]["classes"] > 0
    assert data["stats"]["individuals"] > 0
    assert isinstance(data["nodes"], list)
    assert isinstance(data["relationships"], list)


@pytest.mark.asyncio
async def test_all_five_materials_present(async_client, db_session, seed_data) -> None:
    """OntoFuel seed produces mat: nodes for all 5 materials."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    materials = {
        r["element_system"] for r in seed_data["rows"]
    }
    mat_node_ids = {
        n["id"] for n in data["nodes"] if n["id"].startswith("mat:")
    }
    expected_mat_ids = {f"mat:{m}" for m in materials}

    assert expected_mat_ids == mat_node_ids


@pytest.mark.asyncio
async def test_material_nodes_are_individuals(async_client, db_session, seed_data) -> None:
    """All mat: nodes have type='individual', all others have type='class'."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    mat_nodes = [n for n in data["nodes"] if n["id"].startswith("mat:")]
    non_mat_nodes = [n for n in data["nodes"] if not n["id"].startswith("mat:")]

    assert len(mat_nodes) > 0
    assert all(n["type"] == "individual" for n in mat_nodes)
    assert all(n["type"] == "class" for n in non_mat_nodes)


@pytest.mark.asyncio
async def test_all_relationship_types_present(async_client, db_session, seed_data) -> None:
    """Full OntoFuel corpus produces HAS_PROPERTY, MEASURED_BY, CITED_IN edges."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    rel_types = {r["type"] for r in data["relationships"]}
    assert "HAS_PROPERTY" in rel_types
    assert "MEASURED_BY" in rel_types
    assert "CITED_IN" in rel_types


@pytest.mark.asyncio
async def test_stats_match_actual_counts(async_client, db_session, seed_data) -> None:
    """Stats fields exactly match node/relationship list lengths."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    assert data["stats"]["nodes"] == len(data["nodes"])
    assert data["stats"]["relationships"] == len(data["relationships"])
    assert data["stats"]["classes"] == sum(
        1 for n in data["nodes"] if n["type"] == "class"
    )
    assert data["stats"]["individuals"] == sum(
        1 for n in data["nodes"] if n["type"] == "individual"
    )
    assert (
        data["stats"]["classes"] + data["stats"]["individuals"]
        == data["stats"]["nodes"]
    )


@pytest.mark.asyncio
async def test_record_ref_deep_links(async_client, db_session, seed_data) -> None:
    """Material (individual) nodes carry record_ref with corpus query param."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    mat_nodes = [n for n in data["nodes"] if n["id"].startswith("mat:")]
    assert len(mat_nodes) > 0

    corpus_id = seed_data["corpus_id"]
    for node in mat_nodes:
        assert node["record_ref"] is not None
        assert "/materials/" in node["record_ref"]
        assert f"corpus={corpus_id}" in node["record_ref"]


@pytest.mark.asyncio
async def test_source_digest_is_deterministic(async_client, db_session, seed_data) -> None:
    """Two requests for the same corpus return identical source_digest."""
    await _seed_from_fixture(db_session, seed_data)

    resp_a = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    resp_b = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )

    assert resp_a.json()["source_digest"] == resp_b.json()["source_digest"]


@pytest.mark.asyncio
async def test_source_node_present(async_client, db_session, seed_data) -> None:
    """The source node (src:{corpus_id}) is always present."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    node_ids = {n["id"] for n in data["nodes"]}
    assert f"src:{seed_data['corpus_id']}" in node_ids


@pytest.mark.asyncio
async def test_method_variety(async_client, db_session, seed_data) -> None:
    """All distinct measurement methods appear as method: nodes."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    expected_methods = {
        m for r in seed_data["rows"] for m in [r["method"]] if m is not None
    }
    method_node_ids = {
        n["id"] for n in data["nodes"] if n["id"].startswith("method:")
    }
    expected_method_ids = {f"method:{m}" for m in expected_methods}

    assert expected_method_ids == method_node_ids


@pytest.mark.asyncio
async def test_no_pagination_for_small_corpus(async_client, db_session, seed_data) -> None:
    """A 20-row corpus fits in one page -- pagination is null."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )
    data = resp.json()

    assert data["pagination"] is None


@pytest.mark.asyncio
async def test_caching_headers_present(async_client, db_session, seed_data) -> None:
    """Response includes Cache-Control, ETag, and Last-Modified headers."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
    )

    assert resp.status_code == 200
    assert "cache-control" in resp.headers
    assert "public" in resp.headers["cache-control"]
    assert "etag" in resp.headers
    assert resp.headers["etag"].startswith('"')
    assert resp.headers["etag"].endswith('"')
    assert "last-modified" in resp.headers


# ---------------------------------------------------------------------------
# Pagination with max_nodes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_with_max_nodes(async_client, db_session, seed_data) -> None:
    """max_nodes=1 returns one page with a next_cursor."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": 1},
    )
    data = resp.json()

    assert resp.status_code == 200
    assert data["pagination"] is not None
    assert data["pagination"]["total"] > 1
    assert data["pagination"]["next_cursor"] is not None
    assert data["stats"]["nodes"] <= 1


@pytest.mark.asyncio
async def test_pagination_cursor_accepted(async_client, db_session, seed_data) -> None:
    """A valid cursor is accepted and returns a page."""
    await _seed_from_fixture(db_session, seed_data)

    # Get first page
    resp1 = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": 2},
    )
    data1 = resp1.json()
    assert data1["pagination"] is not None
    cursor = data1["pagination"]["next_cursor"]

    # Get next page
    resp2 = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": 2, "cursor": cursor},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["corpus_id"] == seed_data["corpus_id"]


@pytest.mark.asyncio
async def test_pagination_etag_includes_cursor(async_client, db_session, seed_data) -> None:
    """Paginated responses fold cursor into ETag so each page is distinct."""
    await _seed_from_fixture(db_session, seed_data)

    resp1 = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": 2},
    )
    cursor = resp1.json()["pagination"]["next_cursor"]

    resp2 = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": 2, "cursor": cursor},
    )

    assert resp1.headers["etag"] != resp2.headers["etag"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_method_cites_source_directly(async_client, db_session) -> None:
    """When method is NULL, the property cites source directly (no method node)."""
    seed = {
        "corpus_id": "no-method-corpus",
        "rows": [
            {
                "element_system": "UO2",
                "property_name": "melting_point",
                "value": 3138.0,
                "unit": "K",
                "method": None,
                "source": "no-method-corpus",
                "confidence": "LOW",
                "dedup_hash": "nomethod-edge",
            },
        ],
    }

    await _seed_from_fixture(db_session, seed)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed['corpus_id']}/graph",
    )
    data = resp.json()

    method_nodes = [n for n in data["nodes"] if n["id"].startswith("method:")]
    assert len(method_nodes) == 0

    prop_id = "prop:melting_point"
    src_id = "src:no-method-corpus"
    direct_cites = [
        r for r in data["relationships"]
        if r["from"] == prop_id and r["to"] == src_id
    ]
    assert len(direct_cites) == 1


@pytest.mark.asyncio
async def test_empty_corpus_returns_404(async_client, db_session) -> None:
    """A corpus with zero staging rows returns 404."""
    resp = await async_client.get(f"{BASE}/corpora/nonexistent/graph")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_invalid_corpus_id_returns_422(async_client) -> None:
    """Corpus IDs violating the slug pattern are rejected with 422."""
    resp = await async_client.get(f"{BASE}/corpora/-bad-start/graph")
    assert resp.status_code == 422

    resp = await async_client.get(f"{BASE}/corpora/bad!id/graph")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_max_nodes_validation(async_client, db_session, seed_data) -> None:
    """max_nodes=0 and negative values are rejected (ge=1 constraint)."""
    await _seed_from_fixture(db_session, seed_data)

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": 0},
    )
    assert resp.status_code == 422

    resp = await async_client.get(
        f"{BASE}/corpora/{seed_data['corpus_id']}/graph",
        params={"max_nodes": -1},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_seed_data_roundtrip(async_client, db_session, seed_data) -> None:
    """Seed fixture has correct structure and all 20 rows are persisted."""
    rows = await _seed_from_fixture(db_session, seed_data)

    assert len(rows) == len(seed_data["rows"])

    for row in rows:
        assert row.element_system is not None
        assert row.property_name is not None
        assert row.value > 0
        assert row.source == seed_data["corpus_id"]
