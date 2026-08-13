"""Tests for LightRAG Pydantic schemas (NFM-862).

RED phase — these tests define the expected API contract for LightRAG
request/response models. They should fail until schemas are implemented.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# IngestRequest
# ---------------------------------------------------------------------------


class TestIngestRequest:
    """Tests for the document ingestion request schema."""

    def test_minimal_request(self) -> None:
        """An ingest request requires at least text content."""
        from nfm_db.schemas.lightrag import IngestRequest

        req = IngestRequest(text="Uranium dioxide is a ceramic nuclear fuel.")
        assert req.text == "Uranium dioxide is a ceramic nuclear fuel."
        assert req.file_source is None

    def test_full_request(self) -> None:
        """An ingest request accepts optional file_source."""
        from nfm_db.schemas.lightrag import IngestRequest

        req = IngestRequest(
            text="UO2 has a fluorite crystal structure.",
            file_source="nuclear_fuel_handbook.pdf",
        )
        assert req.file_source == "nuclear_fuel_handbook.pdf"

    def test_rejects_empty_text(self) -> None:
        """An ingest request must have non-empty text."""
        from nfm_db.schemas.lightrag import IngestRequest

        with pytest.raises(ValidationError):
            IngestRequest(text="")

    def test_text_stripped(self) -> None:
        """Whitespace-only text should be rejected (after strip)."""
        from nfm_db.schemas.lightrag import IngestRequest

        with pytest.raises(ValidationError):
            IngestRequest(text="   ")


# ---------------------------------------------------------------------------
# IngestResponse
# ---------------------------------------------------------------------------


class TestIngestResponse:
    """Tests for the document ingestion response schema."""

    def test_success_response(self) -> None:
        """A successful ingest returns status and track_id."""
        from nfm_db.schemas.lightrag import IngestResponse

        resp = IngestResponse(
            status="success",
            message="Text inserted successfully",
            track_id="abc-123",
        )
        assert resp.status == "success"
        assert resp.track_id == "abc-123"

    def test_orm_compat(self) -> None:
        """IngestResponse should support from_attributes for ORM compat."""
        from nfm_db.schemas.lightrag import IngestResponse

        resp = IngestResponse.model_validate(
            {"status": "success", "message": "ok", "track_id": "t-1"},
            from_attributes=True,
        )
        assert resp.status == "success"


# ---------------------------------------------------------------------------
# QueryRequest
# ---------------------------------------------------------------------------


class TestQueryRequest:
    """Tests for the semantic query request schema."""

    def test_minimal_request(self) -> None:
        """A query request requires only the query text."""
        from nfm_db.schemas.lightrag import QueryRequest

        req = QueryRequest(query="What are the properties of UO2?")
        assert req.query == "What are the properties of UO2?"
        assert req.mode == "mix"
        assert req.include_references is False

    def test_custom_mode(self) -> None:
        """A query request accepts different query modes."""
        from nfm_db.schemas.lightrag import QueryRequest

        req = QueryRequest(
            query="List all materials with high thermal conductivity",
            mode="global",
        )
        assert req.mode == "global"

    def test_include_references(self) -> None:
        """A query request can request references."""
        from nfm_db.schemas.lightrag import QueryRequest

        req = QueryRequest(
            query="Find relations between UO2 and Zircaloy",
            include_references=True,
        )
        assert req.include_references is True

    def test_rejects_empty_query(self) -> None:
        """A query request must have non-empty query text."""
        from nfm_db.schemas.lightrag import QueryRequest

        with pytest.raises(ValidationError):
            QueryRequest(query="")

    def test_invalid_mode_rejected(self) -> None:
        """An invalid query mode should be rejected."""
        from nfm_db.schemas.lightrag import QueryRequest

        with pytest.raises(ValidationError):
            QueryRequest(query="test", mode="invalid_mode")


# ---------------------------------------------------------------------------
# QueryResponse
# ---------------------------------------------------------------------------


class TestQueryResponse:
    """Tests for the semantic query response schema."""

    def test_minimal_response(self) -> None:
        """A query response contains answer text."""
        from nfm_db.schemas.lightrag import QueryResponse

        resp = QueryResponse(
            response="UO2 is a ceramic nuclear fuel material.",
        )
        assert resp.response == "UO2 is a ceramic nuclear fuel material."
        assert resp.references == []

    def test_response_with_references(self) -> None:
        """A query response can include references."""
        from nfm_db.schemas.lightrag import QueryResponse

        resp = QueryResponse(
            response="Answer text",
            references=[
                {
                    "reference_id": "1",
                    "file_path": "/docs/fuel.pdf",
                    "content": ["Chunk text from the document."],
                }
            ],
        )
        assert len(resp.references) == 1
        assert resp.references[0]["file_path"] == "/docs/fuel.pdf"

    def test_response_with_entities_and_relationships(self) -> None:
        """A query response can include structured KG data."""
        from nfm_db.schemas.lightrag import QueryResponse

        resp = QueryResponse(
            response="UO2 has a fluorite structure.",
            entities=[
                {
                    "entity_name": "UO2",
                    "entity_type": "Material",
                    "description": "Uranium dioxide fuel",
                }
            ],
            relationships=[
                {
                    "src_id": "UO2",
                    "tgt_id": "fluorite",
                    "description": "crystal structure",
                }
            ],
        )
        assert len(resp.entities) == 1
        assert resp.entities[0]["entity_name"] == "UO2"
        assert len(resp.relationships) == 1


# ---------------------------------------------------------------------------
# HealthResponse
# ---------------------------------------------------------------------------


class TestHealthResponse:
    """Tests for the health check response schema."""

    def test_healthy_response(self) -> None:
        """A health response reports service status."""
        from nfm_db.schemas.lightrag import HealthResponse

        resp = HealthResponse(status="healthy")
        assert resp.status == "healthy"

    def test_unhealthy_response(self) -> None:
        """A health response can report unhealthy status."""
        from nfm_db.schemas.lightrag import HealthResponse

        resp = HealthResponse(status="unhealthy", error="LightRAG service unavailable")
        assert resp.status == "unhealthy"
        assert resp.error == "LightRAG service unavailable"

    def test_error_optional(self) -> None:
        """The error field defaults to None."""
        from nfm_db.schemas.lightrag import HealthResponse

        resp = HealthResponse(status="healthy")
        assert resp.error is None
