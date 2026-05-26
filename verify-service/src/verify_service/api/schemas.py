"""Pydantic schemas for API request/response models."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---- Reference Values ----

class ReferenceValueResponse(BaseModel):
    id: str
    material: str
    structure: str
    property_name: str
    value: float
    unit: str
    source: str | None = None
    temperature: float = 300.0
    notes: str | None = None


class ReferenceValueCreate(BaseModel):
    material: str
    structure: str
    property_name: str
    value: float
    unit: str
    source: str | None = None
    temperature: float = 300.0
    notes: str | None = None


# ---- Verification ----

class VerificationRequest(BaseModel):
    properties: list[str] = Field(
        default=["lattice_constant", "bulk_modulus", "cohesive_energy"],
        description="Properties to calculate",
    )
    species: str = Field(default="U", description="Element symbol")
    structure: str = Field(default="BCC", description="Crystal structure")
    lattice_guess: float | None = Field(
        default=None, description="Initial lattice constant guess (Å)"
    )


class PropertyResult(BaseModel):
    computed: float | dict | None = None
    reference: float | dict | None = None
    unit: str
    absolute_error: float | None = None
    relative_error: float | None = None
    grade: str | None = None
    details: dict[str, Any] | None = None


class VerificationResponse(BaseModel):
    id: str
    potential_id: str
    status: str
    properties_requested: list[str]
    results: dict[str, PropertyResult] | None = None
    overall_grade: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class VerificationStatusResponse(BaseModel):
    potential_id: str
    verifications: list[VerificationResponse]
    latest: VerificationResponse | None = None
