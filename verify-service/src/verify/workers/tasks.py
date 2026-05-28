"""Background verification task runner.

MVP uses asyncio background tasks (no Celery). Jobs tracked in-memory.
For production, replace with Celery + Redis.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from ..core.calculator import PropertyCalculator
from ..core.grading import compute_overall_grade, compute_relative_error, grade_property
from ..core.potential_loader import PotentialLoader
from ..database import db

logger = logging.getLogger(__name__)

# In-memory job store (MVP). Key: job_id -> job dict
_job_store: dict[str, dict[str, Any]] = {}


def get_job(job_id: str) -> dict[str, Any] | None:
    """Get job status from in-memory store, falling back to Supabase."""
    job = _job_store.get(job_id)
    if job:
        return job
    # Could fetch from Supabase here for persistence across restarts
    return None


def get_active_job_count() -> int:
    """Count currently running jobs."""
    return sum(1 for j in _job_store.values() if j["status"] == "running")


async def submit_verification(
    potential_id: str,
    properties: list[str],
    crystal_structure: str | None = None,
) -> str:
    """Submit a verification job. Returns job_id.

    Creates job in Supabase and dispatches background task.
    """
    job_id = str(uuid.uuid4())

    # Create in-memory record
    _job_store[job_id] = {
        "id": job_id,
        "potential_id": potential_id,
        "status": "pending",
        "properties_requested": properties,
        "crystal_structure": crystal_structure,
        "results": {},
        "grades": {},
        "overall_grade": None,
        "error_message": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Create in Supabase
    try:
        await db.save_verification({
            "id": job_id,
            "potential_id": potential_id,
            "status": "pending",
            "properties_requested": properties,
            "results": {},
            "grades": {},
        })
    except Exception as e:
        logger.warning("Failed to create job in Supabase: %s", e)
        # Continue anyway — in-memory store is source of truth for MVP

    # Dispatch background task
    asyncio.create_task(_run_verification(job_id))

    return job_id


async def _run_verification(job_id: str) -> None:
    """Execute the verification computation in background."""
    job = _job_store.get(job_id)
    if not job:
        logger.error("Job %s not found", job_id)
        return

    job["status"] = "running"
    job["started_at"] = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    try:
        # Update Supabase status
        await _safe_update(job_id, {"status": "running", "started_at": job["started_at"]})

        # 1. Fetch potential from Supabase
        record = await db.get_potential(job["potential_id"])
        if not record:
            raise ValueError(f"Potential {job['potential_id']} not found")

        elements = record.get("elements", [])
        crystal = job.get("crystal_structure") or _guess_crystal(record)

        # 2. Build ASE calculator + atoms
        atoms, calc = PotentialLoader.from_potential_record(record)
        job["potential_name"] = record.get("display_name") or record.get("name")

        # 3. Run property calculations
        engine = PropertyCalculator(atoms, calc)
        computed = engine.run_all(job["properties_requested"])
        meta = computed.pop("_meta", {})

        # 4. Fetch reference values from Supabase
        primary_element = elements[0] if elements else "Al"
        ref_values = await db.get_reference_values(primary_element, crystal)

        # Build reference lookup
        ref_map = {}
        for rv in ref_values:
            prop_name = rv["property_name"]
            # Map DB property names to our computation keys
            mapped = _map_property_name(prop_name)
            if mapped:
                ref_map[mapped] = rv["value"]

        # 5. Grade results
        grades = {}
        property_results = []

        for prop_name in job["properties_requested"]:
            result = computed.get(prop_name, {"error": "not computed"})

            if "error" in result:
                grades[prop_name] = "F"
                property_results.append({
                    "name": prop_name,
                    "computed_value": None,
                    "reference_value": None,
                    "unit": "",
                    "relative_error": None,
                    "grade": "F",
                    "error": result["error"],
                })
                continue

            comp_value = result.get("value")
            ref_value = ref_map.get(prop_name)

            if comp_value is not None and ref_value is not None:
                rel_err = compute_relative_error(comp_value, ref_value)
                grade = grade_property(comp_value, ref_value)
            else:
                rel_err = None
                grade = "N/A"

            grades[prop_name] = grade
            property_results.append({
                "name": prop_name,
                "computed_value": comp_value,
                "reference_value": ref_value,
                "unit": result.get("unit", ""),
                "relative_error": round(rel_err, 6) if rel_err is not None else None,
                "grade": grade,
                "raw_data": {k: v for k, v in result.items() if k != "value"},
            })

        overall = compute_overall_grade([g for g in grades.values() if g != "N/A"])
        elapsed_ms = int((time.time() - t0) * 1000)

        # Update job
        job["status"] = "completed"
        job["results"] = computed
        job["property_results"] = property_results
        job["grades"] = grades
        job["overall_grade"] = overall
        job["compute_time_ms"] = elapsed_ms
        job["completed_at"] = datetime.now(timezone.utc).isoformat()

        # Save to Supabase
        await _safe_update(job_id, {
            "status": "completed",
            "results": {p: r for p, r in computed.items()},
            "grades": grades,
            "overall_grade": overall,
            "compute_time_ms": elapsed_ms,
            "completed_at": job["completed_at"],
        })

        # Write verified_props back to potentials table so detail page shows grades
        # Frontend detail page expects property_name key (not name)
        frontend_results = []
        for pr in property_results:
            frontend_results.append({
                "property_name": pr.get("name", ""),
                "computed_value": pr.get("computed_value"),
                "reference_value": pr.get("reference_value"),
                "unit": pr.get("unit", ""),
                "relative_error": pr.get("relative_error"),
                "grade": pr.get("grade", "N/A"),
            })
        verified_props_payload = {
            "overall_grade": overall,
            "verified_at": job["completed_at"],
            "source": "nucpot-autovc",
            "results": frontend_results,
        }
        try:
            await db.update_potential(job["potential_id"], {
                "verified_props": verified_props_payload,
                "updated_at": job["completed_at"],
            })
            logger.info("Wrote verified_props to potential %s", job["potential_id"])
        except Exception as e:
            logger.warning("Failed to write verified_props to potential %s: %s", job["potential_id"], e)

        logger.info(
            "Verification %s completed: %s (grade %s, %dms)",
            job_id[:8], job.get("potential_name", "?"), overall, elapsed_ms,
        )

    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.error("Verification %s FAILED: %s", job_id[:8], e, exc_info=True)

        job["status"] = "failed"
        job["error_message"] = str(e)
        job["compute_time_ms"] = elapsed_ms
        job["completed_at"] = datetime.now(timezone.utc).isoformat()

        await _safe_update(job_id, {
            "status": "failed",
            "error_message": str(e),
            "compute_time_ms": elapsed_ms,
            "completed_at": job["completed_at"],
        })


async def _safe_update(job_id: str, data: dict) -> None:
    """Safely update Supabase, ignoring errors."""
    try:
        await db.update_verification(job_id, data)
    except Exception as e:
        logger.warning("Supabase update failed for %s: %s", job_id[:8], e)


def _guess_crystal(record: dict) -> str:
    """Guess crystal structure from potential metadata."""
    tags = record.get("system_tags", [])
    tag_str = " ".join(tags).upper() if tags else ""

    if "HCP" in tag_str:
        return "HCP"
    if "FCC" in tag_str:
        return "FCC"
    if "ORTHO" in tag_str:
        return "orthorhombic"

    # Default based on element
    elements = record.get("elements", [])
    if elements:
        el = elements[0]
        if el in ("Fe", "Mo", "Nb", "U"):
            return "BCC"
        if el == "Zr":
            return "HCP"

    return "BCC"


def _map_property_name(db_name: str) -> str | None:
    """Map DB property_name to our computation property name."""
    mapping = {
        "lattice_constant": "lattice_constant",
        "lattice_constant_a": "lattice_constant",
        "bulk_modulus": "bulk_modulus",
        "vacancy_formation_energy": "vacancy_formation_energy",
        "C11": "elastic_constants",  # elastic constants come as a group
        "C12": "elastic_constants",
        "C44": "elastic_constants",
    }
    return mapping.get(db_name)
