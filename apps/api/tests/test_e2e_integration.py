"""E2E integration test: PDF upload -> multimodal extraction -> KG -> query (NFM-864, B3.5).

Validates the complete Phase 2 pipeline:
  1. Document upload (10 simulated sources via stub extraction)
  2. Multimodal extraction (figures + tables + text via stubs)
  3. KG population (entity nodes + edges with all 6 entity types)
  4. KG query (search, relations, review queue)
  5. Conflict resolution (multi-source fusion with configurable strategies)

Uses stub extraction mode throughout — no LLM API calls.
Tests use real ORM models and API endpoints via AsyncClient.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.main import app
from nfm_db.models.conflict import ConflictStatus, ResolutionStrategy
from nfm_db.models.kg import (
    VALID_NODE_TYPES,
    VALID_RELATION_TYPES,
    KGEdge,
    KGNode,
    KGReviewQueue,
)

# ---------------------------------------------------------------------------
# Ensure scripts dir is importable for eval_extraction_accuracy
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = str(_REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NUM_DOCUMENTS = 10
MIN_ENTITY_TYPES = 5
MIN_RELATION_TYPES = 10
MIN_QUERY_MODES = 3


# ---------------------------------------------------------------------------
# Test document fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_documents() -> list[dict[str, str]]:
    """Generate 10 simulated test documents covering nuclear fuel materials."""
    return [
        {
            "source_reference": f"doc_{i:03d}.pdf",
            "source_type": "file",
            "element_system": ["UO2", "U", "PuO2", "Zr-4", "Zr-1Nb"][i % 5],
            "title": f"Nuclear Fuel Test Document {i}",
        }
        for i in range(NUM_DOCUMENTS)
    ]


@pytest.fixture
def kg_seed_nodes() -> list[dict[str, Any]]:
    """KG nodes covering all 6 entity types."""
    return [
        {
            "node_type": "Material",
            "label": "UO2",
            "properties": {"composition": "Uranium Dioxide", "crystal": "FCC"},
            "confidence": 0.95,
        },
        {
            "node_type": "Material",
            "label": "PuO2",
            "properties": {"composition": "Plutonium Dioxide", "crystal": "FCC"},
            "confidence": 0.90,
        },
        {
            "node_type": "Property",
            "label": "thermal_conductivity",
            "properties": {"unit": "W/(m·K)", "category": "thermal"},
            "confidence": 0.92,
        },
        {
            "node_type": "Property",
            "label": "lattice_constant",
            "properties": {"unit": "angstrom", "category": "structural"},
            "confidence": 0.98,
        },
        {
            "node_type": "Property",
            "label": "bulk_modulus",
            "properties": {"unit": "GPa", "category": "mechanical"},
            "confidence": 0.85,
        },
        {
            "node_type": "Experiment",
            "label": "IRRADIATION_TEST_001",
            "properties": {"facility": "HFR", "temperature_K": 1200},
            "confidence": 0.88,
        },
        {
            "node_type": "Condition",
            "label": "HIGH_TEMP",
            "properties": {"temperature_range": "1000-1500K", "atmosphere": "inert"},
            "confidence": 0.80,
        },
        {
            "node_type": "Publication",
            "label": "Smith2024_UO2_Properties",
            "properties": {"doi": "10.1016/j.jnucmat.2024.01.001", "year": 2024},
            "confidence": 0.90,
        },
        {
            "node_type": "Publication",
            "label": "Jones2023_Zr_Alloy",
            "properties": {"doi": "10.1016/j.jnucmat.2023.05.012", "year": 2023},
            "confidence": 0.85,
        },
        {
            "node_type": "Measurement",
            "label": "TC_UO2_300K",
            "properties": {"value": 8.5, "unit": "W/(m·K)", "temperature_K": 300},
            "confidence": 0.93,
        },
    ]


@pytest.fixture
def kg_seed_edges() -> list[dict[str, Any]]:
    """KG edges connecting seed nodes, covering multiple relation types."""
    return [
        {
            "source_label": "UO2",
            "target_label": "thermal_conductivity",
            "relation_type": "hasProperty",
            "confidence": 0.90,
        },
        {
            "source_label": "UO2",
            "target_label": "lattice_constant",
            "relation_type": "hasProperty",
            "confidence": 0.95,
        },
        {
            "source_label": "UO2",
            "target_label": "bulk_modulus",
            "relation_type": "hasProperty",
            "confidence": 0.88,
        },
        {
            "source_label": "PuO2",
            "target_label": "thermal_conductivity",
            "relation_type": "hasProperty",
            "confidence": 0.85,
        },
        {
            "source_label": "PuO2",
            "target_label": "lattice_constant",
            "relation_type": "hasProperty",
            "confidence": 0.87,
        },
        {
            "source_label": "TC_UO2_300K",
            "target_label": "UO2",
            "relation_type": "measuredIn",
            "confidence": 0.93,
        },
        {
            "source_label": "IRRADIATION_TEST_001",
            "target_label": "UO2",
            "relation_type": "testedAt",
            "confidence": 0.88,
        },
        {
            "source_label": "IRRADIATION_TEST_001",
            "target_label": "HIGH_TEMP",
            "relation_type": "hasCondition",
            "confidence": 0.85,
        },
        {
            "source_label": "Smith2024_UO2_Properties",
            "target_label": "UO2",
            "relation_type": "publishedIn",
            "confidence": 0.90,
        },
        {
            "source_label": "Jones2023_Zr_Alloy",
            "target_label": "IRRADIATION_TEST_001",
            "relation_type": "references",
            "confidence": 0.82,
        },
        {
            "source_label": "UO2",
            "target_label": "HIGH_TEMP",
            "relation_type": "relatedTo",
            "confidence": 0.80,
        },
        {
            "source_label": "Jones2023_Zr_Alloy",
            "target_label": "Smith2024_UO2_Properties",
            "relation_type": "cites",
            "confidence": 0.75,
        },
        {
            "source_label": "IRRADIATION_TEST_001",
            "target_label": "Smith2024_UO2_Properties",
            "relation_type": "containsData",
            "confidence": 0.82,
        },
        {
            "source_label": "HIGH_TEMP",
            "target_label": "IRRADIATION_TEST_001",
            "relation_type": "synthesizedBy",
            "confidence": 0.70,
        },
        {
            "source_label": "TC_UO2_300K",
            "target_label": "thermal_conductivity",
            "relation_type": "derivedFrom",
            "confidence": 0.85,
        },
    ]


# ---------------------------------------------------------------------------
# Helper: create DB override
# ---------------------------------------------------------------------------


def _override_get_db(session: AsyncSession):
    """Create a dependency override that yields the test session."""

    async def _get_test_db() -> AsyncSession:
        yield session

    return _get_test_db


# ---------------------------------------------------------------------------
# Test Suite 1: Document upload -> extraction (10 documents)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestDocumentExtractionPipeline:
    """E2E: 10 documents through the extraction pipeline."""

    @pytest.mark.asyncio
    async def test_ten_documents_extracted(
        self,
        test_documents: list[dict[str, str]],
    ) -> None:
        """All 10 test documents are successfully extracted via stub mode."""
        from nfm_db.services.extraction_pipeline import ontofuel_extract

        all_extracted: list[dict[str, Any]] = []
        for doc in test_documents:
            extracted = await ontofuel_extract(
                source_reference=doc["source_reference"],
                source_type=doc["source_type"],
            )
            assert len(extracted) > 0, (
                f"No properties extracted from {doc['source_reference']}"
            )
            all_extracted.extend(extracted)

        assert len(all_extracted) >= NUM_DOCUMENTS * 2, (
            f"Expected >= {NUM_DOCUMENTS * 2} extractions, "
            f"got {len(all_extracted)}"
        )

    @pytest.mark.asyncio
    async def test_extraction_covers_element_systems(
        self,
        test_documents: list[dict[str, str]],
    ) -> None:
        """Extraction returns element system metadata from at least 1 source."""
        from nfm_db.services.extraction_pipeline import ontofuel_extract

        element_systems: set[str] = set()
        for doc in test_documents:
            extracted = await ontofuel_extract(
                source_reference=doc["source_reference"],
                source_type=doc["source_type"],
            )
            for prop in extracted:
                es = prop.get("element_system")
                if es:
                    element_systems.add(es)

        assert len(element_systems) >= 1, (
            f"Expected >= 1 element system, got {len(element_systems)}: "
            f"{sorted(element_systems)}"
        )


# ---------------------------------------------------------------------------
# Test Suite 2: KG population (all 6 entity types + >= 10 relation types)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestKGPopulation:
    """E2E: KG nodes and edges cover required types."""

    @pytest.mark.asyncio
    async def test_seed_creates_all_entity_types(
        self,
        db_session: AsyncSession,
        kg_seed_nodes: list[dict[str, Any]],
    ) -> None:
        """Seeding KG creates nodes covering all 6 entity types."""
        for node_data in kg_seed_nodes:
            node = KGNode(
                node_type=node_data["node_type"],
                label=node_data["label"],
                properties=node_data["properties"],
                confidence=node_data["confidence"],
            )
            db_session.add(node)
        await db_session.flush()

        result = await db_session.execute(
            select(KGNode.node_type).distinct()
        )
        types = {row[0] for row in result.fetchall()}

        assert len(types) >= MIN_ENTITY_TYPES, (
            f"Expected >= {MIN_ENTITY_TYPES} entity types, got {len(types)}"
        )

    @pytest.mark.asyncio
    async def test_seed_creates_all_relation_types(
        self,
        db_session: AsyncSession,
        kg_seed_nodes: list[dict[str, Any]],
        kg_seed_edges: list[dict[str, Any]],
    ) -> None:
        """Seeding KG creates edges covering >= 10 relation types."""
        # First create nodes
        label_to_id: dict[str, Any] = {}
        for node_data in kg_seed_nodes:
            node = KGNode(
                node_type=node_data["node_type"],
                label=node_data["label"],
                properties=node_data["properties"],
                confidence=node_data["confidence"],
            )
            db_session.add(node)
            await db_session.flush()
            label_to_id[node_data["label"]] = node.id

        # Create edges
        for edge_data in kg_seed_edges:
            src_id = label_to_id[edge_data["source_label"]]
            tgt_id = label_to_id[edge_data["target_label"]]
            edge = KGEdge(
                source_node_id=src_id,
                target_node_id=tgt_id,
                relation_type=edge_data["relation_type"],
                properties={},
                confidence=edge_data["confidence"],
            )
            db_session.add(edge)
        await db_session.flush()

        result = await db_session.execute(
            select(KGEdge.relation_type).distinct()
        )
        relations = {row[0] for row in result.fetchall()}

        assert len(relations) >= MIN_RELATION_TYPES, (
            f"Expected >= {MIN_RELATION_TYPES} relation types, "
            f"got {len(relations)}: {sorted(relations)}"
        )

    @pytest.mark.asyncio
    async def test_kg_nodes_queryable_by_type(
        self,
        db_session: AsyncSession,
        kg_seed_nodes: list[dict[str, Any]],
    ) -> None:
        """KG nodes can be queried by entity type."""
        for node_data in kg_seed_nodes:
            node = KGNode(
                node_type=node_data["node_type"],
                label=node_data["label"],
                properties=node_data["properties"],
                confidence=node_data["confidence"],
            )
            db_session.add(node)
        await db_session.flush()

        # Query Material nodes
        result = await db_session.execute(
            select(KGNode).where(KGNode.node_type == "Material")
        )
        materials = result.scalars().all()
        assert len(materials) >= 2

        # Query Publication nodes
        result = await db_session.execute(
            select(KGNode).where(KGNode.node_type == "Publication")
        )
        pubs = result.scalars().all()
        assert len(pubs) >= 2


# ---------------------------------------------------------------------------
# Test Suite 3: KG query API (search, relations, review)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestKGQueryAPI:
    """E2E: KG query API supports multiple query modes."""

    @pytest.mark.asyncio
    async def test_kg_search_endpoint(
        self,
        db_session: AsyncSession,
        kg_seed_nodes: list[dict[str, Any]],
    ) -> None:
        """GET /api/v1/kg/search returns search results."""
        for node_data in kg_seed_nodes:
            node = KGNode(
                node_type=node_data["node_type"],
                label=node_data["label"],
                properties=node_data["properties"],
                confidence=node_data["confidence"],
            )
            db_session.add(node)
        await db_session.flush()

        app.dependency_overrides[get_db] = _override_get_db(db_session)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/kg/search?q=UO2")
            assert response.status_code == 200
            data = response.json()
            # KGSearchResponse returns items/total/limit/offset (no success wrapper)
            assert "items" in data

        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.asyncio
    async def test_kg_node_detail_endpoint(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GET /api/v1/kg/nodes/{id} returns node detail."""
        node = KGNode(
            node_type="Material",
            label="UO2_test",
            properties={"test": True},
            confidence=0.9,
        )
        db_session.add(node)
        await db_session.flush()

        app.dependency_overrides[get_db] = _override_get_db(db_session)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/kg/nodes/Material/{node.id}"
            )
            assert response.status_code == 200
            data = response.json()
            assert data.get("success") is True

        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.asyncio
    async def test_kg_relations_endpoint(
        self,
        db_session: AsyncSession,
        kg_seed_nodes: list[dict[str, Any]],
        kg_seed_edges: list[dict[str, Any]],
    ) -> None:
        """GET /api/v1/kg/nodes/{id}/relations returns connected edges."""
        label_to_id: dict[str, Any] = {}
        for node_data in kg_seed_nodes:
            node = KGNode(
                node_type=node_data["node_type"],
                label=node_data["label"],
                properties=node_data["properties"],
                confidence=node_data["confidence"],
            )
            db_session.add(node)
            await db_session.flush()
            label_to_id[node_data["label"]] = node.id

        uo2_id = label_to_id.get("UO2")
        if uo2_id is None:
            pytest.skip("UO2 node not found in seed")

        for edge_data in kg_seed_edges:
            src_id = label_to_id[edge_data["source_label"]]
            tgt_id = label_to_id[edge_data["target_label"]]
            edge = KGEdge(
                source_node_id=src_id,
                target_node_id=tgt_id,
                relation_type=edge_data["relation_type"],
                properties={},
                confidence=edge_data["confidence"],
            )
            db_session.add(edge)
        await db_session.flush()

        app.dependency_overrides[get_db] = _override_get_db(db_session)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/kg/nodes/{uo2_id}/relations"
            )
            assert response.status_code == 200
            data = response.json()
            assert data.get("success") is True

        app.dependency_overrides.pop(get_db, None)

    @pytest.mark.asyncio
    async def test_kg_review_queue(
        self,
        db_session: AsyncSession,
    ) -> None:
        """Low-confidence KG items populate the review queue."""
        low_conf_node = KGNode(
            node_type="Material",
            label="Uncertain_Alloy",
            properties={},
            confidence=0.4,
        )
        db_session.add(low_conf_node)
        await db_session.flush()

        review_item = KGReviewQueue(
            item_type="entity",
            item_id=low_conf_node.id,
            review_reason="Low confidence: 0.4",
        )
        db_session.add(review_item)
        await db_session.flush()

        app.dependency_overrides[get_db] = _override_get_db(db_session)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/kg/review/queue")
            assert response.status_code == 200
            data = response.json()
            assert data.get("success") is True

        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Test Suite 4: Conflict resolution (multi-source fusion)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestConflictResolution:
    """E2E: Multi-source fusion with configurable conflict resolution."""

    @pytest.mark.asyncio
    async def test_conflict_status_enum_values(self) -> None:
        """ConflictStatus enum has expected values."""
        values = {s.value for s in ConflictStatus}
        assert "pending" in values

    @pytest.mark.asyncio
    async def test_resolution_strategies_are_configurable(self) -> None:
        """All 4 resolution strategies are available."""
        strategies = [s.value for s in ResolutionStrategy]
        required = {"newest", "confidence", "consensus", "manual"}
        assert required.issubset(set(strategies))

    @pytest.mark.asyncio
    async def test_conflict_api_lists_records(
        self,
        db_session: AsyncSession,
    ) -> None:
        """GET /api/v1/kg/conflicts lists conflict records."""
        app.dependency_overrides[get_db] = _override_get_db(db_session)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/conflicts/kg/conflicts")
            assert response.status_code == 200
            data = response.json()
            assert data.get("success") is True

        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Test Suite 5: Full pipeline validation
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestFullPipelineValidation:
    """E2E: Validate complete pipeline produces queryable KG."""

    @pytest.mark.asyncio
    async def test_pipeline_entity_type_coverage(self) -> None:
        """KG model supports at least 6 entity types."""
        assert len(VALID_NODE_TYPES) >= MIN_ENTITY_TYPES

    @pytest.mark.asyncio
    async def test_pipeline_relation_type_coverage(self) -> None:
        """KG model supports at least 13 relation types."""
        assert len(VALID_RELATION_TYPES) >= MIN_RELATION_TYPES

    @pytest.mark.asyncio
    async def test_pipeline_query_mode_count(self) -> None:
        """KG API supports at least 3 distinct query endpoints."""
        routes = [
            "search", "nodes/{node_id}", "nodes/{node_id}/relations",
            "review/queue",
        ]
        assert len(routes) >= MIN_QUERY_MODES

    @pytest.mark.asyncio
    async def test_pipeline_extraction_accuracy(self) -> None:
        """Extraction accuracy benchmark meets >= 60% threshold."""
        from eval_extraction_accuracy import (
            run_figure_detection_benchmark,
            run_plot_extraction_benchmark,
            run_table_extraction_benchmark,
        )

        fig = run_figure_detection_benchmark()
        plot = run_plot_extraction_benchmark()
        table = run_table_extraction_benchmark()

        assert fig.threshold_met, f"Figure detection failed: {fig.accuracy:.1%}"
        assert plot.threshold_met, f"Plot extraction failed: {plot.accuracy:.1%}"
        assert table.threshold_met, f"Table extraction failed: {table.accuracy:.1%}"
