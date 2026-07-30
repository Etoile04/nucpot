"""Schemas for the literature API (Phase 2).

Defines the ``ExtractionResultItem`` shape returned by
``GET /api/v1/literature/{id}`` so callers can distinguish data that
came from the legacy ``extraction_results`` table (manual entries)
from rows produced by the OntoFuel LLM pipeline
(``kg_nodes`` / ``kg_edges``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LiteratureUploadResponse(BaseModel):
    """Response for literature upload initiation."""

    literature_id: UUID
    status: str = "uploaded"


class LiteratureStatusResponse(BaseModel):
    """Response for literature processing status."""

    id: UUID
    status: str
    progress: float = 0.0
    error: str | None = None


class LiteratureListItem(BaseModel):
    """Brief literature item for list views."""

    id: UUID
    title: str = ""
    doi: str | None = None
    journal: str | None = None
    year: int | None = None
    abstract: str | None = None
    status: str = "uploaded"
    source_id: UUID | None = None
    created_at: datetime


class LiteratureFigure(BaseModel):
    """A single extracted figure from a literature source."""

    id: UUID
    page_number: int | None = None
    figure_type: str | None = None
    image_path: str | None = None
    caption: str | None = None
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Extraction results (merged from extraction_results + kg_nodes + kg_edges)
# ---------------------------------------------------------------------------


#: Origin discriminator for items in ``LiteratureDetailResponse.extraction_results``.
ExtractionSourceType = Literal["manual", "kg_node", "kg_edge"]


class ExtractionResultItem(BaseModel):
    """One merged extraction row, regardless of where it came from.

    The :attr:`source_type` field tells callers whether the row originated
    from the legacy ``extraction_results`` table (``"manual"``) or from the
    OntoFuel LLM pipeline (``"kg_node"`` / ``"kg_edge"``).

    The schema is intentionally additive on top of the original ad-hoc dict
    shape — clients that only read :attr:`property_name`, :attr:`value`,
    :attr:`confidence`, etc. keep working unchanged.
    """

    model_config = ConfigDict(extra="ignore")

    # Discriminator (NEW in NFM-2224 — required).
    source_type: ExtractionSourceType

    # Common fields populated for every variant.
    id: str = Field(..., description="Stringified UUID of the source row.")
    property_name: str = Field(..., description="Human-readable label (property / node label / relation type).")
    item_type: str = Field(..., description="Coarse row category: material/property/edge/etc.")
    item_data: dict[str, Any] = Field(default_factory=dict, description="Raw payload blob from the source row.")
    value: Any | None = Field(None, description="Scalar value when one is exposed by the source row.")
    confidence: float | None = Field(None, description="Extraction confidence in [0.0, 1.0].")
    created_at: str | None = Field(None, description="ISO-8601 timestamp string from the source row.")

    # Legacy/manual-only field.
    review_status: str | None = Field(None, description="Manual review status — only set when source_type == 'manual'.")

    # KG-node-only fields.
    unit: str | None = Field(None, description="Unit string — only set when source_type == 'kg_node'.")
    source_page: int | None = Field(None, description="Page number in the source PDF (kg_node reads from properties).")
    source_paragraph: str | None = Field(
        None, description="Paragraph snippet the row was extracted from (kg_node)."
    )

    # KG-edge-only fields.
    source_node_id: str | None = Field(None, description="Edge source endpoint (kg_node/kg_edge).")
    source_target_id: str | None = Field(None, description="Edge target endpoint — only set when source_type == 'kg_edge'.")


class LiteratureDetailResponse(BaseModel):
    """Full literature detail with extraction results."""

    id: UUID
    title: str = ""
    doi: str | None = None
    journal: str | None = None
    year: int | None = None
    abstract: str | None = None
    status: str = "uploaded"
    source_id: UUID | None = None
    content_md: str | None = None
    figures: list[LiteratureFigure] = Field(default_factory=list)
    extraction_results: list[ExtractionResultItem] = Field(
        default_factory=list,
        description=(
            "Merged extraction results from extraction_results (manual) and "
            "kg_nodes / kg_edges (LLM). Each item carries a `source_type` "
            "discriminator of 'manual' / 'kg_node' / 'kg_edge'."
        ),
    )
    created_at: datetime
    updated_at: datetime


class LiteratureReextractResponse(BaseModel):
    """Response for re-extraction trigger."""

    id: UUID
    status: str = "parsing"
