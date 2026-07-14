"""Batch import/export API endpoints (NFM-1085).

Unified batch operations for materials, properties, and reference_values.

Import (POST):
  /api/v1/materials/import        — CSV or JSON file
  /api/v1/properties/import       — CSV or JSON file
  /api/v1/reference-values/import  — CSV or JSON file

Export (GET):
  /api/v1/materials/export?format=csv|json
  /api/v1/properties/export?format=csv|json
  /api/v1/reference-values/export?format=csv|json
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import get_current_active_user
from nfm_db.database import get_db
from nfm_db.models.material import Material
from nfm_db.models.property import PropertyMeasurement
from nfm_db.models.ref_gap_fill import RefGapFillStaging
from nfm_db.models.user import User
from nfm_db.schemas.batch import (
    BatchImportResult,
    BatchRowError,
    ReferenceValueRow,
)
from nfm_db.services.batch_service import (
    BATCH_IMPORT_MAX_ROWS,
    BATCH_IMPORT_MAX_SIZE_MB,
    generate_export_filename,
    parse_csv_content,
    parse_json_content,
    serialize_materials_to_csv,
    serialize_materials_to_json,
    serialize_properties_to_csv,
    serialize_properties_to_json,
    serialize_reference_values_to_csv,
    serialize_reference_values_to_json,
    validate_material_row,
    validate_property_row,
    validate_reference_value_row,
)

logger = logging.getLogger(__name__)


def _generate_dedup_hash(row: ReferenceValueRow) -> str:
    """Generate a deterministic dedup hash for a reference value row."""
    key = (
        f"{row.element_system}|{row.phase or ''}|{row.property_name}"
        f"|{row.value}|{row.unit}|{row.source}"
    )
    return hashlib.sha256(key.encode()).hexdigest()


# Batch routers (mounted at /api/v1 in main.py alongside entity routers)
materials_router = APIRouter(tags=["材料管理"])
properties_router = APIRouter(tags=["属性管理"])
reference_values_router = APIRouter(tags=["参考值管理"])


# ── Helpers ─────────────────────────────────────────────────────────


def _parse_rows(content: bytes, filename: str) -> list[dict[str, Any]]:
    """Parse CSV or JSON content based on file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "json":
        return parse_json_content(content)
    if ext == "csv":
        return parse_csv_content(content)
    raise ValueError(f"Unsupported file type: .{ext}. Use .csv or .json")


def _measurement_to_dict(m: PropertyMeasurement) -> dict[str, Any]:
    """Convert a PropertyMeasurement ORM object to a flat dict for export."""
    return {
        "id": str(m.id),
        "dataset_id": str(m.dataset_id) if m.dataset_id else None,
        "property_type_id": (str(m.property_type_id) if m.property_type_id else None),
        "value_scalar": m.value_scalar,
        "value_min": m.value_min,
        "value_max": m.value_max,
        "value_expression": m.value_expression,
        "value_list": m.value_list,
        "value_text": m.value_text,
        "uncertainty": m.uncertainty,
        "unit_id": str(m.unit_id) if m.unit_id else None,
        "notes": m.notes,
    }


def _ref_value_to_dict(rv: RefGapFillStaging) -> dict[str, Any]:
    """Convert a RefGapFillStaging ORM object to a flat dict for export."""
    return {
        "element_system": rv.element_system,
        "phase": rv.phase,
        "property_name": rv.property_name,
        "value": rv.value,
        "unit": rv.unit,
        "method": rv.method,
        "source": rv.source,
        "source_doi": rv.source_doi,
        "uncertainty": rv.uncertainty,
        "temperature": rv.temperature,
    }


def _build_export_response(
    body: str,
    format: str,
    entity: str,
) -> PlainTextResponse:
    """Build a PlainTextResponse with Content-Disposition header."""
    media_type = "text/csv" if format == "csv" else "application/json"
    filename = generate_export_filename(entity, format)
    return PlainTextResponse(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _flush_row(db: AsyncSession, row_index: int) -> bool:
    """Flush the current pending row; roll back on IntegrityError.

    Returns True if the row was persisted, False if skipped due to a
    database integrity error (e.g. duplicate).
    """
    try:
        await db.flush()
        return True
    except IntegrityError:
        await db.rollback()
        return False


# ── Material Import ───────────────────────────────────────────────


@materials_router.post("/materials/import", response_model=BatchImportResult)
async def import_materials(
    request: Request,
    file: UploadFile = File(..., description="CSV or JSON file"),
    db: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(get_current_active_user)] = ...,
) -> BatchImportResult:
    """Bulk-import materials from a CSV or JSON file.

    Valid rows are created; invalid rows are reported in ``errors``.
    Individual DB integrity errors (e.g. duplicates) are skipped per-row.
    """
    filename = file.filename or "unknown.csv"
    content = await file.read()
    max_bytes = BATCH_IMPORT_MAX_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds {BATCH_IMPORT_MAX_SIZE_MB}MB limit",
        )

    try:
        raw_rows = _parse_rows(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if len(raw_rows) > BATCH_IMPORT_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Batch exceeds {BATCH_IMPORT_MAX_ROWS} row limit",
        )

    imported = 0
    errors: list[BatchRowError] = []

    for i, raw_row in enumerate(raw_rows, start=1):
        validated, err = validate_material_row(raw_row, row_index=i)
        if err or validated is None:
            if err:
                errors.append(err)
            continue

        mat = Material(**validated.model_dump())
        db.add(mat)
        if await _flush_row(db, i):
            imported += 1
        else:
            errors.append(
                BatchRowError(
                    row=i,
                    field="db",
                    message="Database integrity error (possible duplicate)",
                )
            )

    await db.commit()

    return BatchImportResult(imported=imported, failed=len(errors), errors=errors)


# ── Material Export ──────────────────────────────────────────────


@materials_router.get("/materials/export")
async def export_materials(
    format: str = Query(..., pattern="^(csv|json)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(get_current_active_user)] = ...,
) -> PlainTextResponse:
    """Export materials as CSV or JSON with Content-Disposition."""
    offset = (page - 1) * per_page
    stmt = select(Material).offset(offset).limit(per_page)
    result = await db.execute(stmt)
    materials = result.scalars().all()

    rows = [
        {
            "id": str(m.id),
            "name": m.name,
            "formula": m.formula,
            "crystal_structure": m.crystal_structure,
            "category_id": str(m.category_id) if m.category_id else None,
            "description": m.description,
            "is_active": m.is_active,
        }
        for m in materials
    ]

    if format == "csv":
        body = serialize_materials_to_csv(rows)
    else:
        body = serialize_materials_to_json(rows)

    return _build_export_response(body, format, "materials")


# ── Property Import ───────────────────────────────────────────────


@properties_router.post("/properties/import", response_model=BatchImportResult)
async def import_properties(
    request: Request,
    file: UploadFile = File(..., description="CSV or JSON file"),
    db: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(get_current_active_user)] = ...,
) -> BatchImportResult:
    """Bulk-import property measurements from a CSV or JSON file.

    Individual DB integrity errors are skipped per-row.
    """
    filename = file.filename or "unknown.csv"
    content = await file.read()
    max_bytes = BATCH_IMPORT_MAX_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds {BATCH_IMPORT_MAX_SIZE_MB}MB limit",
        )

    try:
        raw_rows = _parse_rows(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if len(raw_rows) > BATCH_IMPORT_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Batch exceeds {BATCH_IMPORT_MAX_ROWS} row limit",
        )

    imported = 0
    errors: list[BatchRowError] = []

    for i, raw_row in enumerate(raw_rows, start=1):
        validated, err = validate_property_row(raw_row, row_index=i)
        if err or validated is None:
            if err:
                errors.append(err)
            continue

        pm = PropertyMeasurement(**validated.model_dump(exclude_unset=True))
        db.add(pm)
        if await _flush_row(db, i):
            imported += 1
        else:
            errors.append(
                BatchRowError(
                    row=i,
                    field="db",
                    message="Database integrity error (possible duplicate)",
                )
            )

    await db.commit()

    return BatchImportResult(imported=imported, failed=len(errors), errors=errors)


# ── Property Export ───────────────────────────────────────────────


@properties_router.get("/properties/export")
async def export_properties(
    format: str = Query(..., pattern="^(csv|json)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(get_current_active_user)] = ...,
) -> PlainTextResponse:
    """Export property measurements as CSV or JSON with Content-Disposition."""
    offset = (page - 1) * per_page
    stmt = select(PropertyMeasurement).offset(offset).limit(per_page)
    result = await db.execute(stmt)
    measurements = result.scalars().all()

    rows = [_measurement_to_dict(m) for m in measurements]

    if format == "csv":
        body = serialize_properties_to_csv(rows)
    else:
        body = serialize_properties_to_json(rows)

    return _build_export_response(body, format, "properties")


# ── Reference Value Import ───────────────────────────────────────


@reference_values_router.post(
    "/reference-values/import", response_model=BatchImportResult
)
async def import_reference_values(
    request: Request,
    file: UploadFile = File(..., description="CSV or JSON file"),
    db: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(get_current_active_user)] = ...,
) -> BatchImportResult:
    """Bulk-import reference values from a CSV or JSON file into staging.

    Individual DB integrity errors are skipped per-row.
    """
    filename = file.filename or "unknown.csv"
    content = await file.read()
    max_bytes = BATCH_IMPORT_MAX_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File size exceeds {BATCH_IMPORT_MAX_SIZE_MB}MB limit",
        )

    try:
        raw_rows = _parse_rows(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if len(raw_rows) > BATCH_IMPORT_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Batch exceeds {BATCH_IMPORT_MAX_ROWS} row limit",
        )

    imported = 0
    errors: list[BatchRowError] = []

    for i, raw_row in enumerate(raw_rows, start=1):
        validated, err = validate_reference_value_row(raw_row, row_index=i)
        if err or validated is None:
            if err:
                errors.append(err)
            continue

        rv_data = validated.model_dump(exclude_unset=True)
        rv_data["dedup_hash"] = _generate_dedup_hash(validated)
        rv = RefGapFillStaging(**rv_data)
        db.add(rv)
        if await _flush_row(db, i):
            imported += 1
        else:
            errors.append(
                BatchRowError(
                    row=i,
                    field="db",
                    message="Database integrity error (possible duplicate)",
                )
            )

    await db.commit()

    return BatchImportResult(imported=imported, failed=len(errors), errors=errors)


# ── Reference Value Export ───────────────────────────────────────


@reference_values_router.get("/reference-values/export")
async def export_reference_values(
    format: str = Query(..., pattern="^(csv|json)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(1000, ge=1, le=10000),
    db: AsyncSession = Depends(get_db),
    _current_user: Annotated[User, Depends(get_current_active_user)] = ...,
) -> PlainTextResponse:
    """Export reference values as CSV or JSON with Content-Disposition."""
    offset = (page - 1) * per_page
    stmt = select(RefGapFillStaging).offset(offset).limit(per_page)
    result = await db.execute(stmt)
    ref_values = result.scalars().all()

    rows = [_ref_value_to_dict(rv) for rv in ref_values]

    if format == "csv":
        body = serialize_reference_values_to_csv(rows)
    else:
        body = serialize_reference_values_to_json(rows)

    return _build_export_response(body, format, "reference_values")
