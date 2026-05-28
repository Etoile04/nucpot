"""FastAPI routes for the NucPot verification service."""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import __version__
from ..database import db
from .schemas import (
    HealthResponse,
    ReferenceValueResponse,
    VerificationRequest,
    VerificationResult,
    PropertyResult,
)
from ..workers.tasks import get_job, get_active_job_count, submit_verification

logger = logging.getLogger(__name__)


# ── Frontend-compatible request schemas ──────────────────────

class VerificationV2Request(BaseModel):
    """Request from the frontend VerificationPanel."""
    potential_name: str
    template: str | None = None
    properties: list[str] | None = None
    parameters: dict[str, Any] | None = None


class TemplateResponse(BaseModel):
    """Verification template exposed to the frontend."""
    id: str
    name: str
    properties: list[str]
    description: str
    estimated_time: str


# ── Built-in templates ───────────────────────────────────────

TEMPLATES: list[TemplateResponse] = [
    TemplateResponse(
        id="basic",
        name="基础验证",
        properties=["lattice_constant", "cohesive_energy", "bulk_modulus"],
        description="晶格常数、结合能、体积模量等基本性质",
        estimated_time="~30s",
    ),
    TemplateResponse(
        id="mechanical",
        name="力学验证",
        properties=["lattice_constant", "elastic_constants", "bulk_modulus", "shear_modulus"],
        description="弹性常数、体积模量等力学性质",
        estimated_time="~60s",
    ),
    TemplateResponse(
        id="defect",
        name="缺陷验证",
        properties=["vacancy_formation_energy", "interstitial_formation_energy", "surface_energy"],
        description="空位形成能、间隙形成能、表面能",
        estimated_time="~90s",
    ),
    TemplateResponse(
        id="comprehensive",
        name="全面验证",
        properties=[
            "lattice_constant", "cohesive_energy", "elastic_constants",
            "bulk_modulus", "shear_modulus", "vacancy_formation_energy",
            "surface_energy",
        ],
        description="所有可用性质的综合验证",
        estimated_time="~120s",
    ),
]

_TEMPLATE_MAP = {t.id: t for t in TEMPLATES}


# ── App lifecycle ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup/shutdown."""
    logger.info("NucPot Verify Service v%s starting", __version__)
    yield
    await db.close()
    logger.info("Service shutting down")


app = FastAPI(
    title="NucPot Verification Service",
    description="Automated verification of interatomic potentials for nuclear materials",
    version=__version__,
    lifespan=lifespan,
)


# ── Health ───────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Service health check."""
    return HealthResponse(
        status="ok",
        version=__version__,
        active_jobs=get_active_job_count(),
    )


# ── Templates (frontend compatibility) ───────────────────────

@app.get("/api/templates", response_model=list[TemplateResponse])
async def list_templates():
    """List available verification templates.

    Frontend VerificationPanel calls this on mount.
    """
    return TEMPLATES


# ── Verification v2 (frontend compatibility) ─────────────────

@app.post("/api/verification/v2", status_code=202)
async def create_verification_v2(req: VerificationV2Request):
    """Submit a verification job using potential_name.

    The frontend VerificationPanel sends potential_name (string) instead of
    potential_id (UUID).  We look up the potential by name in Supabase first,
    then delegate to the existing pipeline.
    """
    # 1. Resolve potential_name → potential_id
    potential = await db.get_potential_by_name(req.potential_name)
    if not potential:
        raise HTTPException(
            status_code=404,
            detail=f"Potential '{req.potential_name}' not found",
        )

    potential_id = potential["id"]

    # 2. Determine properties from template or explicit list
    if req.properties:
        properties = req.properties
    elif req.template and req.template in _TEMPLATE_MAP:
        properties = _TEMPLATE_MAP[req.template].properties
    else:
        # Default: basic set
        properties = ["lattice_constant", "cohesive_energy", "bulk_modulus"]

    # 3. Submit job
    job_id = await submit_verification(
        potential_id=potential_id,
        properties=properties,
        crystal_structure=req.parameters.get("crystal_structure") if req.parameters else None,
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"Verification submitted for {req.potential_name}",
    }


@app.get("/api/verification/{job_id}")
async def get_verification_v2(job_id: str):
    """Get verification job status and results (frontend format).

    Returns data in the format the frontend VerificationPanel expects:
      { status, overall_grade, results: [{property_name, computed_value, ...}] }
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Convert internal property_results to frontend format
    results = []
    for pr in job.get("property_results", []):
        results.append({
            "property_name": pr.get("name", ""),
            "computed_value": pr.get("computed_value"),
            "reference_value": pr.get("reference_value"),
            "unit": pr.get("unit", ""),
            "relative_error": pr.get("relative_error"),
            "grade": pr.get("grade"),
        })

    return {
        "id": job["id"],
        "potential_id": job["potential_id"],
        "potential_name": job.get("potential_name"),
        "status": job["status"],
        "overall_grade": job.get("overall_grade"),
        "results": results,
        "error_message": job.get("error_message"),
        "compute_time_ms": job.get("compute_time_ms"),
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }


@app.get("/api/verification/{job_id}/report")
async def get_verification_report(job_id: str):
    """Get a formatted verification report for a completed job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed yet")

    overall = job.get("overall_grade", "N/A")
    results = job.get("property_results", [])

    # Build a human-readable summary
    summary_lines = []
    for pr in results:
        name = pr.get("name", "?")
        comp = pr.get("computed_value")
        ref = pr.get("reference_value")
        err = pr.get("relative_error")
        grade = pr.get("grade", "N/A")
        summary_lines.append(
            f"  {name}: computed={comp}, reference={ref}, "
            f"error={f'{err*100:.2f}%' if err is not None else 'N/A'}, grade={grade}"
        )

    report_text = (
        f"Verification Report\n"
        f"{'='*40}\n"
        f"Potential: {job.get('potential_name', '?')}\n"
        f"Overall Grade: {overall}\n"
        f"Properties:\n" +
        "\n".join(summary_lines)
    )

    return {
        "job_id": job_id,
        "potential_name": job.get("potential_name"),
        "overall_grade": overall,
        "results": [
            {
                "property_name": pr.get("name", ""),
                "computed_value": pr.get("computed_value"),
                "reference_value": pr.get("reference_value"),
                "unit": pr.get("unit", ""),
                "relative_error": pr.get("relative_error"),
                "grade": pr.get("grade"),
            }
            for pr in results
        ],
        "summary": report_text,
    }


# ── Original /api/verify routes (admin panel compatibility) ──

@app.post("/api/verify", status_code=202)
async def create_verification(req: VerificationRequest):
    """Submit a verification job.

    Returns job_id immediately; computation runs in background.
    """
    # Validate potential exists
    potential = await db.get_potential(req.potential_id)
    if not potential:
        raise HTTPException(status_code=404, detail="Potential not found")

    job_id = await submit_verification(
        potential_id=req.potential_id,
        properties=req.properties_to_compute,
        crystal_structure=req.crystal_structure,
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "message": f"Verification submitted for potential {potential.get('name', req.potential_id)}",
    }


@app.get("/api/verify/{job_id}", response_model=VerificationResult)
async def get_verification(job_id: str):
    """Get verification job status and results."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build property results list
    prop_results = []
    for pr in job.get("property_results", []):
        prop_results.append(PropertyResult(**pr))

    return VerificationResult(
        id=job["id"],
        potential_id=job["potential_id"],
        potential_name=job.get("potential_name"),
        status=job["status"],
        properties=prop_results,
        grades=job.get("grades", {}),
        overall_grade=job.get("overall_grade"),
        error_message=job.get("error_message"),
        compute_time_ms=job.get("compute_time_ms"),
        created_at=job.get("created_at"),
        completed_at=job.get("completed_at"),
    )


# ── Reference values ─────────────────────────────────────────

@app.get("/api/reference-values/{element}/{crystal_structure}")
async def get_reference_values(element: str, crystal_structure: str):
    """Get reference values for an element and crystal structure."""
    values = await db.get_reference_values(element, crystal_structure)
    if not values:
        raise HTTPException(
            status_code=404,
            detail=f"No reference values for {element}/{crystal_structure}",
        )
    return [ReferenceValueResponse(**v) for v in values]


@app.get("/api/potentials/{potential_id}")
async def get_potential(potential_id: str):
    """Get potential metadata (proxy to Supabase)."""
    potential = await db.get_potential(potential_id)
    if not potential:
        raise HTTPException(status_code=404, detail="Potential not found")
    return potential
