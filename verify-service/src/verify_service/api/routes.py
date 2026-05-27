"""API route definitions."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from verify_service.api.schemas import (
    ReferenceValueCreate,
    ReferenceValueResponse,
    VerificationRequest,
    VerificationResponse,
    VerificationStatusResponse,
)
from verify_service.supabase import SupabaseClient, get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()


def _db() -> SupabaseClient:
    return get_supabase_client()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "nucpot-verify", "version": "0.1.0"}


# ---- Verification ----


@router.post("/verify/{potential_id}", response_model=VerificationResponse)
async def submit_verification(
    potential_id: str,
    body: VerificationRequest,
    db: SupabaseClient = Depends(_db),
):
    """Submit a verification job for a potential."""
    # Check potential exists
    pot = await db.get_potential(potential_id)
    if not pot:
        raise HTTPException(404, f"Potential {potential_id} not found")

    # Create verification record
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "potential_id": potential_id,
        "status": "pending",
        "properties_requested": body.properties,
        "results": {},
        "created_at": now,
    }
    row = await db.create_verification(data)

    # Dispatch async task
    try:
        from verify_service.workers.tasks import run_verification

        task = run_verification.delay(
            verification_id=row["id"],
            potential_id=potential_id,
            properties=body.properties,
            species=body.species,
            structure=body.structure,
            lattice_guess=body.lattice_guess,
        )
        logger.info(f"Dispatched verification task {task.id} for potential {potential_id}")
    except Exception as e:
        logger.warning(f"Celery dispatch failed: {e}")
        # Still return pending — can be picked up later

    return VerificationResponse(**row)


@router.get("/verify/{potential_id}/status", response_model=VerificationStatusResponse)
async def get_verification_status(
    potential_id: str,
    db: SupabaseClient = Depends(_db),
):
    """Get verification status for a potential."""
    rows = await db.get_verifications_for_potential(potential_id)
    if not rows:
        raise HTTPException(404, f"No verifications for potential {potential_id}")

    verifications = [VerificationResponse(**r) for r in rows]
    latest = verifications[-1] if verifications else None

    return VerificationStatusResponse(
        potential_id=potential_id,
        verifications=verifications,
        latest=latest,
    )


# ---- Reference Values ----


@router.get("/reference-values", response_model=list[ReferenceValueResponse])
async def list_reference_values(
    material: str | None = None,
    structure: str | None = None,
    db: SupabaseClient = Depends(_db),
):
    """List reference values, optionally filtered by material/structure."""
    rows = await db.get_reference_values(material=material, structure=structure)
    return [ReferenceValueResponse(**r) for r in rows]


@router.post(
    "/reference-values",
    response_model=ReferenceValueResponse,
    status_code=201,
)
async def add_reference_value(
    body: ReferenceValueCreate,
    db: SupabaseClient = Depends(_db),
):
    """Add a new reference value."""
    data = body.model_dump()
    row = await db.insert("reference_values", data)
    if isinstance(row, list):
        row = row[0]
    return ReferenceValueResponse(**row)
