"""Pydantic schemas for extraction gap tracking (NFM-2586 / NFM-2575-T2).

API response shapes for ``ExtractionGap`` (the persistent gap record
created by ``GapScanService``) and for the ``GapScanResult`` summary
returned by ``scan_for_gaps``.

These schemas intentionally mirror the ORM models' field names so
callers can use ``ExtractionGapResponse.model_validate(gap_orm)``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from nfm_db.services.gap_scanner import GapScanResult as _GapScanResult


# ---------------------------------------------------------------------------
# ExtractionGapResponse — API shape for a single gap record
# ---------------------------------------------------------------------------


class ExtractionGapResponse(BaseModel):
    """Public representation of an ``ExtractionGap`` row.

    Returned by gap-listing endpoints and embedded in
    :class:`GapScanResult`-style responses.
    """

    id: uuid.UUID = Field(
        description="Globally unique gap identifier (UUID v4).",
    )
    ontology_version_id: uuid.UUID = Field(
        description="Ontology version that defines the expected schema.",
    )
    entity_type: str = Field(
        description="Entity type, e.g. NuclearMaterial, Isotope.",
        max_length=100,
    )
    property: str = Field(
        description="Property name, e.g. density, half_life.",
        max_length=200,
    )
    source_reference: str | None = Field(
        default=None,
        description="Source identifier where the gap was detected.",
    )
    chunk_id: uuid.UUID | None = Field(
        default=None,
        description="Extraction chunk being processed when gap was found.",
    )
    gap_status: str = Field(
        description="open | filling | filled | wont_fix.",
        max_length=20,
    )
    detected_at: datetime = Field(
        description="When the gap was first detected (UTC).",
    )
    resolved_at: datetime | None = Field(
        default=None,
        description="When the gap was filled or marked wont_fix (UTC).",
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# GapScanResultResponse — API shape for scan summary
# ---------------------------------------------------------------------------


class GapScanResultResponse(BaseModel):
    """API response for a single ``GapScanService.scan_for_gaps`` call.

    Mirrors the frozen dataclass field set so callers can render a
    summary card without touching ORM internals.
    """

    total_expected: int = Field(
        ge=0,
        description="Total (entity_type, property) pairs the ontology expects.",
    )
    gaps_found: int = Field(
        ge=0,
        description="Pairs whose chunk set did not contain a substring hit.",
    )
    gaps_created: int = Field(
        ge=0,
        description=(
            "New ``ExtractionGap`` rows actually inserted "
            "(<= gaps_found after dedup)."
        ),
    )
    scan_duration_ms: int = Field(
        ge=0,
        description="Wall-clock duration of the scan.",
    )

    @classmethod
    def from_domain(
        cls, result: _GapScanResult,
    ) -> "GapScanResultResponse":
        """Build a response from a service-layer :class:`GapScanResult`."""
        return cls(
            total_expected=result.total_expected,
            gaps_found=result.gaps_found,
            gaps_created=result.gaps_created,
            scan_duration_ms=result.scan_duration_ms,
        )


__all__ = [
    "ExtractionGapResponse",
    "GapScanResultResponse",
]
