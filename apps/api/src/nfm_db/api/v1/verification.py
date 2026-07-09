"""Verification API endpoints (NFM-98).

Exposes the three Nuclear Domain Expert workflows:
- POST /api/v1/verification/check-gap — Reference validation workflow
- POST /api/v1/verification/adjudicate-grade — F-grade adjudication workflow
- POST /api/v1/verification/quarterly-audit — Quarterly audit workflow
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from nfm_db.core.auth import get_current_user
from nfm_db.models import User
from nfm_db.services.domain_expert import (
    AdjudicationRequest as DomainAdjudicationRequest,
)
from nfm_db.services.domain_expert import (
    AuditConfig,
    adjudicate_f_grade,
    run_quarterly_audit,
    validate_reference,
)
from nfm_db.services.domain_expert import (
    ReferenceCandidate as DomainReferenceCandidate,
)
from nfm_db.services.domain_expert.reference_validation import (
    SourceCredibility,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["数据验证"])


# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------


class ReferenceCandidateRequest(BaseModel):
    """Request body for POST /api/v1/verification/check-gap."""

    element_system: str = Field(..., max_length=50)
    property_name: str = Field(..., max_length=100)
    value: float = Field(...)
    unit: str = Field(..., max_length=20)
    source: str = Field(..., max_length=200)
    source_type: SourceCredibility = Field(
        default=SourceCredibility.UNKNOWN,
    )
    source_doi: str | None = Field(None, max_length=100)
    method: str | None = Field(None, max_length=100)
    uncertainty: float | None = Field(None)
    temperature: float | None = Field(None)
    phase: str | None = Field(None, max_length=50)


class LiteratureMatchItem(BaseModel):
    """A literature match from external sources."""

    source_name: str
    source_type: SourceCredibility
    value: float
    unit: str
    uncertainty: float | None = None
    source_doi: str | None = None
    method: str | None = None
    agreement_pct: float = 0.0


class ReferenceValidationResponse(BaseModel):
    """Response body for POST /api/v1/verification/check-gap."""

    validation_id: UUID
    validated_at: datetime
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    is_validated: bool
    needs_escalation: bool
    escalation_reason: str | None = None
    literature_matches: list[LiteratureMatchItem] = []
    estimated_uncertainty: float | None = None
    source_credibility_score: float = Field(..., ge=0.0, le=1.0)
    notes: str | None = None


class AdjudicationRequest(BaseModel):
    """Request body for POST /api/v1/verification/adjudicate-grade."""

    staging_id: UUID
    element_system: str
    property_name: str
    error_log: str = Field(...)
    potential_type: str | None = Field(None)
    lammps_version: str | None = Field(None)
    phase: str | None = Field(None)
    temperature: float | None = Field(None)


class FixSuggestionItem(BaseModel):
    """A suggested fix for an F-grade failure."""

    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    category: str


class AdjudicationResponse(BaseModel):
    """Response body for POST /api/v1/verification/adjudicate-grade."""

    adjudication_id: UUID
    adjudicated_at: datetime
    matched_patterns: list[str] = []
    primary_category: str
    suggested_fixes: list[FixSuggestionItem] = []
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    needs_escalation: bool
    escalation_reason: str | None = None
    resolved: bool
    notes: str | None = None


class QuarterlyAuditRequest(BaseModel):
    """Request body for POST /api/v1/verification/quarterly-audit."""

    quarter: str = Field(...)
    start_date: datetime
    end_date: datetime
    p0_systems: list[str] = Field(default_factory=lambda: ["U", "UO2", "Zr", "Fe", "U-Zr"])
    core_properties: list[str] = Field(
        default_factory=lambda: [
            "lattice_constant",
            "cohesive_energy",
            "bulk_modulus",
            "elastic_constants",
            "thermal_expansion",
        ]
    )
    min_uncertainty_coverage: float = Field(
        default=0.90, ge=0.0, le=1.0
    )
    max_days_since_verification: int = Field(
        default=90
    )


class AuditFindingItem(BaseModel):
    """A single finding from the quarterly audit."""

    finding_id: UUID
    severity: str
    check_type: str
    element_system: str
    property_name: str
    description: str
    recommendation: str
    metrics: dict[str, object] = {}


class QuarterlyAuditResponse(BaseModel):
    """Response body for POST /api/v1/verification/quarterly-audit."""

    report_id: UUID
    generated_at: datetime
    total_checks: int
    passed: int
    failed: int
    findings: list[AuditFindingItem] = []
    summary: str
    overall_health: str
    p0_uncertainty_coverage: dict[str, float] = {}
    verification_freshness: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/check-gap",
    response_model=ReferenceValidationResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate a reference candidate"
                "uncertainty estimation, confidence scoring. Escalates if confidence < 80%.",
)
async def check_reference_gap(
    request: ReferenceCandidateRequest,
    current_user: User = Depends(get_current_user),
) -> ReferenceValidationResponse:
    """验证新的参考数据候选值。

    This endpoint runs the reference-validation-workflow:
    1. Literature search (if skill available)
    2. Source credibility scoring
    3. Uncertainty estimation
    4. Property range sanity check
    5. Confidence scoring
    6. Escalation if confidence < 80%

    Returns validation result with confidence score and escalation information.
    """
    try:
        candidate = DomainReferenceCandidate(
            element_system=request.element_system,
            property_name=request.property_name,
            value=request.value,
            unit=request.unit,
            source=request.source,
            source_type=request.source_type,
            source_doi=request.source_doi,
            method=request.method,
            uncertainty=request.uncertainty,
            temperature=request.temperature,
            phase=request.phase,
        )
        result = validate_reference(candidate)

        return ReferenceValidationResponse(
            validation_id=result.validation_id,
            validated_at=result.validated_at,
            confidence_score=result.confidence_score,
            is_validated=result.is_validated,
            needs_escalation=result.needs_escalation,
            escalation_reason=result.escalation_reason,
            literature_matches=[
                LiteratureMatchItem(
                    source_name=m.source_name,
                    source_type=m.source_type,
                    value=m.value,
                    unit=m.unit,
                    uncertainty=m.uncertainty,
                    source_doi=m.source_doi,
                    method=m.method,
                    agreement_pct=m.agreement_pct,
                )
                for m in result.literature_matches
            ],
            estimated_uncertainty=result.estimated_uncertainty,
            source_credibility_score=result.source_credibility_score,
            notes=result.notes,
        )
    except Exception as e:
        logger.exception("Reference validation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reference validation failed: {e!s}",
        ) from e


@router.post(
    "/adjudicate-grade",
    response_model=AdjudicationResponse,
    status_code=status.HTTP_200_OK,
    summary="Adjudicate an F-grade LAMMPS failure"
                "pattern matches known failures, suggests fixes. Escalates if confidence < 70%.",
)
async def adjudicate_f_grade_endpoint(
    request: AdjudicationRequest,
    current_user: User = Depends(get_current_user),
) -> AdjudicationResponse:
    """裁定F级验证失败。

    This endpoint runs the f-grade-adjudication-workflow:
    1. Parse LAMMPS error log
    2. Pattern match against known failure modes
    3. Generate fix suggestions
    4. Confidence scoring
    5. Escalation if confidence < 70%

    Returns adjudication result with fix suggestions and escalation information.
    """
    try:
        adj_request = DomainAdjudicationRequest(
            staging_id=request.staging_id,
            element_system=request.element_system,
            property_name=request.property_name,
            error_log=request.error_log,
            potential_type=request.potential_type,
            lammps_version=request.lammps_version,
            phase=request.phase,
            temperature=request.temperature,
        )
        result = adjudicate_f_grade(adj_request)

        return AdjudicationResponse(
            adjudication_id=result.adjudication_id,
            adjudicated_at=result.adjudicated_at,
            matched_patterns=[p.value for p in result.matched_patterns],
            primary_category=result.primary_category.value,
            suggested_fixes=[
                FixSuggestionItem(
                    description=f.description,
                    confidence=f.confidence,
                    category=f.category,
                )
                for f in result.suggested_fixes
            ],
            confidence_score=result.confidence_score,
            needs_escalation=result.needs_escalation,
            escalation_reason=result.escalation_reason,
            resolved=result.resolved,
            notes=result.notes,
        )
    except Exception as e:
        logger.exception("F-grade adjudication failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"F-grade adjudication failed: {e!s}",
        ) from e


@router.post(
    "/quarterly-audit",
    response_model=QuarterlyAuditResponse,
    status_code=status.HTTP_200_OK,
    summary="Run quarterly P0 audit"
                "verification freshness, and conflicts. Generates audit report with findings.",
)
async def run_quarterly_audit_endpoint(
    request: QuarterlyAuditRequest,
    current_user: User = Depends(get_current_user),
) -> QuarterlyAuditResponse:
    """对P0安全关键体系执行季度审计。

    This endpoint runs the quarterly-audit-workflow:
    1. Uncertainty coverage check (target: 90%)
    2. Verification freshness check (within last quarter)
    3. Conflict detection (value dispersion > 20%)
    4. P0 completeness check (all core properties covered)

    Returns audit report with findings and overall health status.
    """
    try:
        config = AuditConfig(
            quarter=request.quarter,
            start_date=request.start_date,
            end_date=request.end_date,
            p0_systems=tuple(request.p0_systems),
            core_properties=tuple(request.core_properties),
            min_uncertainty_coverage=request.min_uncertainty_coverage,
            max_days_since_verification=request.max_days_since_verification,
        )
        # Note: In production, refs_by_system would be queried from the database
        # For now, we pass empty data to test the workflow logic
        result = run_quarterly_audit(config, refs_by_system={})

        return QuarterlyAuditResponse(
            report_id=result.report_id,
            generated_at=result.generated_at,
            total_checks=result.total_checks,
            passed=result.passed,
            failed=result.failed,
            findings=[
                AuditFindingItem(
                    finding_id=f.finding_id,
                    severity=f.severity.value,
                    check_type=f.check_type.value,
                    element_system=f.element_system,
                    property_name=f.property_name,
                    description=f.description,
                    recommendation=f.recommendation,
                    metrics=f.metrics,
                )
                for f in result.findings
            ],
            summary=result.summary,
            overall_health=result.overall_health,
            p0_uncertainty_coverage=result.p0_uncertainty_coverage,
            verification_freshness=result.verification_freshness,
        )
    except Exception as e:
        logger.exception("Quarterly audit failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Quarterly audit failed: {e!s}",
        ) from e


# ---------------------------------------------------------------------------
# Health check for the verification module
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Verification module health check",
)
async def verification_health() -> dict[str, str]:
    """验证模块健康检查。"""
    return {
        "status": "healthy",
        "module": "verification",
        "version": "1.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }
