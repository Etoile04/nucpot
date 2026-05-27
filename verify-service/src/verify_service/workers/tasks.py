"""Async verification tasks."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from verify_service.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=2)
def run_verification(
    self,
    verification_id: str,
    potential_id: str,
    properties: list[str],
    species: str = "U",
    structure: str = "BCC",
    lattice_guess: float | None = None,
):
    """Run a verification job.

    1. Update status → running
    2. Set up ASE calculator from potential info
    3. Compute requested properties
    4. Grade results against reference
    5. Write results back to Supabase
    """
    import httpx
    import os

    supabase_url = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321").rstrip("/")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    client = httpx.Client(base_url=f"{supabase_url}/rest/v1", headers=headers, timeout=30)

    def _update(data: dict):
        client.patch(
            "/verifications",
            params={"id": f"eq.{verification_id}"},
            json=data,
        )

    try:
        # Mark running
        _update({
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        })

        # Get potential info
        resp = client.get("/potentials", params={"id": f"eq.{potential_id}"})
        resp.raise_for_status()
        potentials = resp.json()
        if not potentials:
            raise ValueError(f"Potential {potential_id} not found")
        pot = potentials[0]

        # Get reference values from DB
        # Use material from potential elements
        elements = pot.get("elements", [species])
        material = "-".join(elements) if len(elements) > 1 else elements[0]

        ref_resp = client.get(
            "/reference_values",
            params={
                "material": f"eq.{material}",
                "structure": f"eq.{structure}",
            },
        )
        ref_resp.raise_for_status()
        ref_rows = ref_resp.json()
        reference = {}
        for row in ref_rows:
            reference[row["property_name"]] = {
                "value": row["value"],
                "unit": row["unit"],
                "source": row.get("source", ""),
            }

        # If no DB reference, use built-in
        if not reference:
            from verify_service.core.reference import get_reference
            ref_data = get_reference(material, structure)
            if ref_data:
                reference = ref_data

        # Set up calculator
        from verify_service.core.calculator import get_calculator

        pot_type = pot.get("type", "eam").lower()
        pot_file = pot.get("file_url")
        calc = get_calculator(
            potential_type=pot_type,
            potential_file=pot_file,
            kim_model=pot.get("lammps_config", {}).get("kim_model"),
        )

        # Compute properties
        from verify_service.core.properties import PropertyCalculator

        pc = PropertyCalculator()
        computed = pc.compute_all(
            calc=calc,
            properties=properties,
            species=species,
            structure=structure,
            lattice_guess=lattice_guess,
        )

        # Grade results
        from verify_service.core.grading import grade_results

        graded = grade_results(computed, reference)

        # Update verification
        _update({
            "status": "completed",
            "results": graded["results"],
            "overall_grade": graded["overall_grade"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            f"Verification {verification_id} completed. "
            f"Grade: {graded['overall_grade']}"
        )
        return graded

    except Exception as e:
        logger.error(f"Verification {verification_id} failed: {e}")
        _update({
            "status": "failed",
            "error_message": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        raise
