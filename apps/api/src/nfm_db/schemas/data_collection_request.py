"""Pydantic schemas for DataCollectionRequest (NFM-2619).

API response shapes for ``DataCollectionRequest`` records and for
``CoverageMetrics`` summaries (mirrors ``RecallMetrics`` pattern from
the gap-scanner module).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


class DataCollectionRequestResponse(BaseModel):
    """Public representation of a ``DataCollectionRequest`` row."""

    id: uuid.UUID = Field(
        description="Globally unique request identifier (UUID v4).",
    )
    ontology_version_id: uuid.UUID = Field(
        description="Ontology version that defines the expected schema.",
    )
    entity_type: str = Field(
        description="Entity type, e.g. NuclearMaterial, Isotope.",
        max_length=100,
    )
    property: str = Field(
        description="Property name, e.g. thermal_conductivity, density.",
        max_length=200,
    )
    material_system: str = Field(
        description="Material system, e.g. UO2, Zr, U.",
        max_length=200,
    )
    urgency: int = Field(
        ge=0,
        description="Higher = more urgently needed.",
    )
    source_preference: str = Field(
        description="literature | dft | external_db | any.",
        max_length=30,
    )
    status: str = Field(
        description="open | in_progress | completed | declined.",
        max_length=20,
    )
    requested_at: datetime = Field(
        description="When the request was created (UTC).",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the request reached a terminal status (UTC).",
    )
    metadata_: dict[str, Any] | None = Field(
        default=None,
        description="Flexible metadata bag.",
    )
    created_at: datetime
    updated_at: datetime

    # --- Dispatch fields (NFM-2651) ---
    # Populated from ``metadata_["dispatch"]`` by ``_populate_dispatch_fields``
    # below.  Kept nullable because the ORM model has no dedicated columns
    # for these — they are derived from the JSON metadata bag written by
    # ``GapDispatchService.dispatch_request``.
    dispatched_at: datetime | None = Field(
        default=None,
        description="When the request was dispatched (UTC).",
    )
    dispatched_path: str | None = Field(
        default=None,
        description="Collection path used (literature | dft | external_db).",
    )
    dispatch_status: str | None = Field(
        default=None,
        description="Dispatch outcome (dispatched | failed | pending).",
    )
    result_reference: str | None = Field(
        default=None,
        description="Reference ID for the dispatched task or calculation.",
    )

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _populate_dispatch_fields(self) -> DataCollectionRequestResponse:
        """Derive dispatch fields from ``metadata_["dispatch"]`` if present.

        ``GapDispatchService`` writes dispatch state into the request's
        ``metadata_`` JSONB bag under the ``"dispatch"`` key.  We pull
        those values up to first-class fields so API consumers can read
        them without parsing the metadata blob.

        Only fills fields that are still ``None`` so explicit overrides
        are preserved.
        """
        dispatch_meta = (self.metadata_ or {}).get("dispatch")
        if not isinstance(dispatch_meta, dict):
            return self

        if self.dispatched_at is None and "dispatched_at" in dispatch_meta:
            raw = dispatch_meta["dispatched_at"]
            if isinstance(raw, str):
                try:
                    self.dispatched_at = datetime.fromisoformat(raw)
                except ValueError:
                    # Leave as None if the string isn't ISO-8601.
                    logger.warning(
                        "data_collection.dispatch.dispatched_at unparseable: %r",
                        raw,
                    )
            elif isinstance(raw, datetime):
                self.dispatched_at = raw

        if self.dispatched_path is None and "path_taken" in dispatch_meta:
            self.dispatched_path = dispatch_meta["path_taken"]

        if self.dispatch_status is None and "dispatch_status" in dispatch_meta:
            self.dispatch_status = dispatch_meta["dispatch_status"]

        if self.result_reference is None:
            ref = dispatch_meta.get("task_id") or dispatch_meta.get(
                "dft_calculation_id",
            )
            if ref is not None:
                self.result_reference = str(ref)

        return self


class CoverageMetricsResponse(BaseModel):
    """Aggregated coverage statistics for an ontology version.

    Mirrors the ``RecallMetrics`` dataclass from gap_scanner but
    scoped to data-collection coverage.
    """

    ontology_version_id: uuid.UUID = Field(
        description="Ontology version these metrics cover.",
    )
    total_requests: int = Field(
        ge=0,
        description="Total data-collection requests for this ontology version.",
    )
    open_requests: int = Field(
        ge=0,
        description="Requests with status ``open``.",
    )
    in_progress_requests: int = Field(
        ge=0,
        description="Requests with status ``in_progress``.",
    )
    completed_requests: int = Field(
        ge=0,
        description="Requests with status ``completed``.",
    )
    declined_requests: int = Field(
        ge=0,
        description="Requests with status ``declined``.",
    )
    coverage_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of requests completed (completed / total).",
    )
    computed_at: datetime = Field(
        description="When these metrics were calculated (UTC).",
    )

    model_config = ConfigDict(from_attributes=True)


__all__ = [
    "CoverageMetricsResponse",
    "DataCollectionRequestResponse",
]
