"""FastAPI routes for the NucPot verification service."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

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


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Service health check."""
    return HealthResponse(
        status="ok",
        version=__version__,
        active_jobs=get_active_job_count(),
    )


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
