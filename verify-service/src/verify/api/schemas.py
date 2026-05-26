"""Pydantic schemas for the verification API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Request schemas ──────────────────────────────────────────

class VerificationRequest(BaseModel):
    """Request to verify a potential."""
    potential_id: str
    properties_to_compute: list[str] = Field(
        default_factory=lambda: [
            "lattice_constant",
            "elastic_constants",
            "bulk_modulus",
            "vacancy_formation_energy",
        ],
    )
    crystal_structure: str | None = None  # auto-detect if None
    element_mapping: dict[str, str] | None = None  # override ASE element symbols


# ── Response schemas ─────────────────────────────────────────

class PropertyResult(BaseModel):
    """Single property computation result."""
    name: str
    computed_value: float | None = None
    reference_value: float | None = None
    unit: str = ""
    relative_error: float | None = None
    grade: str | None = None  # A, B, C, D, F
    raw_data: dict[str, Any] | None = None  # extra info (e.g. EOS curve)


class VerificationResult(BaseModel):
    """Complete verification result."""
    id: str
    potential_id: str
    potential_name: str | None = None
    status: str  # pending, running, completed, failed
    properties: list[PropertyResult] = Field(default_factory=list)
    grades: dict[str, str] = Field(default_factory=dict)
    overall_grade: str | None = None
    error_message: str | None = None
    compute_time_ms: int | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    version: str = "0.1.0"
    active_jobs: int = 0


class ReferenceValueResponse(BaseModel):
    """Reference value lookup result."""
    element: str
    crystal_structure: str
    property_name: str
    value: float
    unit: str
    source: str | None = None
    notes: str | None = None
