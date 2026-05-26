"""Pydantic models for the Verification Service API."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class VerificationOut(BaseModel):
    id: str
    potential_id: str
    status: str  # pending / running / completed / failed
    results: dict = {}
    overall_grade: Optional[str] = None
    summary: Optional[str] = None
    error_log: Optional[str] = None
    compute_time: Optional[int] = None
    requested_by: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class ReferenceValueOut(BaseModel):
    id: str
    element_system: str
    phase: Optional[str] = None
    property: str
    value: float
    unit: Optional[str] = None
    source: Optional[str] = None
    method: Optional[str] = None
