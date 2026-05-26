"""NucPot Verification Service — FastAPI application."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import db
import models as schemas
from workers.lattice import compute_lattice_constant
from workers.elastic import compute_elastic_constants
from workers.vacancy import compute_vacancy_formation_energy
from runners.ase_runner import get_calculator


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB tables exist
    db.ensure_tables()
    yield


app = FastAPI(title="NucPot Verification Service", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "nucpot-verify"}


@app.post("/api/verify/{potential_id}", response_model=schemas.VerificationOut)
def trigger_verification(potential_id: str):
    """Trigger a verification run for a potential."""
    # 1. Fetch potential metadata from DB
    potential = db.get_potential(potential_id)
    if not potential:
        raise HTTPException(404, f"Potential {potential_id} not found")

    verification_id = str(uuid.uuid4())
    started_at = time.time()

    # 2. Create verification record
    db.insert_verification(verification_id, potential_id, status="running")

    results: dict = {}
    errors: list[str] = []
    elements = potential.get("elements", [])

    try:
        # 3. Get calculator for this potential
        calc = get_calculator(potential)

        # 4. Compute properties (lattice constant first, simplest)
        for element in elements:
            ref_values = db.get_reference_values(element)
            ref_lattice = next(
                (r for r in ref_values if r["property"] == "lattice_constant"), None
            )

            if ref_lattice:
                try:
                    computed = compute_lattice_constant(element, calc, ref_lattice["phase"])
                    results["lattice_constant"] = _grade_property(computed, ref_lattice)
                except Exception as exc:
                    errors.append(f"lattice_constant({element}): {exc}")

            # Elastic constants
            for prop in ("C11", "C12", "C44"):
                ref_c = next((r for r in ref_values if r["property"] == prop), None)
                if ref_c:
                    try:
                        computed = compute_elastic_constants(element, calc, ref_c["phase"])
                        if prop in computed:
                            results[prop] = _grade_property(computed[prop], ref_c)
                    except Exception as exc:
                        errors.append(f"{prop}({element}): {exc}")

            # Vacancy formation energy
            ref_vac = next(
                (r for r in ref_values if r["property"] == "vacancy_formation_energy"), None
            )
            if ref_vac:
                try:
                    computed_vac = compute_vacancy_formation_energy(element, calc, ref_vac["phase"])
                    results["vacancy_formation_energy"] = _grade_property(computed_vac, ref_vac)
                except Exception as exc:
                    errors.append(f"vacancy_formation_energy({element}): {exc}")

        # 5. Overall grade = worst individual grade
        grades = [v.get("grade", "F") for v in results.values()]
        overall = _worst_grade(grades) if grades else "F"
        elapsed = int(time.time() - started_at)

        # 6. Update verification record
        db.update_verification(
            verification_id,
            status="completed",
            results=results,
            overall_grade=overall,
            summary=f"Verified {len(results)} properties. Overall grade: {overall}",
            compute_time=elapsed,
        )

    except Exception as exc:
        elapsed = int(time.time() - started_at)
        db.update_verification(
            verification_id,
            status="failed",
            error_log=str(exc),
            compute_time=elapsed,
        )
        raise HTTPException(500, f"Verification failed: {exc}")

    record = db.get_verification(verification_id)
    return schemas.VerificationOut(**record)


@app.get("/api/verify/{verification_id}/status", response_model=schemas.VerificationOut)
def get_status(verification_id: str):
    record = db.get_verification(verification_id)
    if not record:
        raise HTTPException(404, "Verification not found")
    return schemas.VerificationOut(**record)


@app.get("/api/verify/{verification_id}/results", response_model=schemas.VerificationOut)
def get_results(verification_id: str):
    return get_status(verification_id)


@app.get("/api/verify/potential/{potential_id}/latest", response_model=Optional[schemas.VerificationOut])
def get_latest(potential_id: str):
    record = db.get_latest_verification(potential_id)
    if not record:
        raise HTTPException(404, "No verification found for this potential")
    return schemas.VerificationOut(**record)


@app.get("/api/reference/{element_system}")
def get_reference(element_system: str):
    refs = db.get_reference_values(element_system)
    if not refs:
        raise HTTPException(404, f"No reference values for {element_system}")
    return refs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GRADE_THRESHOLDS = [
    ("A", 0.02),
    ("B", 0.05),
    ("C", 0.10),
    ("D", 0.20),
    ("F", float("inf")),
]


def _grade_property(computed: float, ref: dict) -> dict:
    """Grade a computed property against reference."""
    ref_value = ref["value"]
    if ref_value == 0:
        error_pct = 0.0 if computed == 0 else float("inf")
    else:
        error_pct = abs(computed - ref_value) / abs(ref_value) * 100

    grade = "F"
    for g, threshold in GRADE_THRESHOLDS:
        if error_pct < threshold * 100:
            grade = g
            break

    return {
        "value": round(computed, 6),
        "unit": ref.get("unit", ""),
        "reference": ref_value,
        "error_pct": round(error_pct, 4),
        "grade": grade,
    }


def _worst_grade(grades: list[str]) -> str:
    """Return the worst grade in a list."""
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
    return max(grades, key=lambda g: order.get(g, 4))
