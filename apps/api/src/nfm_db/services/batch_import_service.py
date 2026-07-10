"""Service layer for batch CSV/JSON material import (NFM-1141).

Handles file parsing, validation, and upsert logic for bulk material
data ingestion.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Material
from nfm_db.schemas.material import BatchImportResult, BatchRowError, MaterialCreate

logger = logging.getLogger(__name__)

# Configurable via environment variable
BATCH_IMPORT_MAX_SIZE_MB = int(os.environ.get("BATCH_IMPORT_MAX_SIZE_MB", "10"))

# Per-IP concurrency lock: max 1 batch import at a time per client
_import_locks: dict[str, asyncio.Semaphore] = {}
_locks_mutex = asyncio.Lock()


async def get_import_lock(client_ip: str) -> asyncio.Semaphore:
    """Get or create a per-IP semaphore for batch import concurrency control.

    Returns a semaphore with capacity 1, ensuring only one batch import
    runs at a time per client IP.
    """
    async with _locks_mutex:
        if client_ip not in _import_locks:
            _import_locks[client_ip] = asyncio.Semaphore(1)
        return _import_locks[client_ip]

# CSV column → MaterialCreate field mapping
_CSV_COLUMN_MAP: dict[str, str] = {
    "name": "name",
    "formula": "formula",
    "crystal_structure": "crystal_structure",
    "category_id": "category_id",
    "description": "description",
    "is_active": "is_active",
}


def _parse_csv_content(content: bytes) -> list[dict[str, str]]:
    """Parse CSV bytes into a list of row dicts with raw string values.

    Handles UTF-8 BOM, empty rows, and trailing whitespace.
    """
    text = content.decode("utf-8-sig")  # utf-8-sig strips BOM automatically
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        # Skip entirely empty rows (all values blank/None)
        if not any(v for v in row.values() if v is not None and v.strip()):
            continue
        # Strip leading/trailing whitespace from every value
        cleaned = {k: v.strip() if v else v for k, v in row.items()}
        rows.append(cleaned)
    return rows


def _parse_json_content(content: bytes) -> list[dict[str, Any]]:
    """Parse JSON bytes into a list of material dicts."""
    text = content.decode("utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("JSON content must be an array of material objects")
    return data


def _row_to_material_create(
    row: dict[str, Any],
    row_index: int,
) -> tuple[dict[str, Any] | None, BatchRowError | None]:
    """Convert a raw row dict to a MaterialCreate-compatible dict.

    Returns (validated_data, None) on success, or (None, error) on failure.
    """
    mapped: dict[str, Any] = {}
    for csv_col, field_name in _CSV_COLUMN_MAP.items():
        # Case-insensitive column lookup
        value = None
        for key in row:
            if key.strip().lower() == csv_col:
                value = row[key]
                break
        if value is None or str(value).strip() == "":
            continue

        str_val = str(value).strip()

        # Type coercion
        if field_name == "is_active":
            lower = str_val.lower()
            if lower in ("true", "1", "yes"):
                mapped[field_name] = True
            elif lower in ("false", "0", "no"):
                mapped[field_name] = False
            else:
                return None, BatchRowError(
                    row=row_index,
                    field=csv_col,
                    message=f"Invalid boolean value: '{value}'",
                )
        elif field_name == "category_id":
            # UUID validation — let Pydantic handle it below
            mapped[field_name] = str_val
        else:
            mapped[field_name] = str_val

    # 'name' is required
    if "name" not in mapped:
        return None, BatchRowError(
            row=row_index,
            field="name",
            message="Missing required field 'name'",
        )

    return mapped, None


def _validate_row(
    data: dict[str, Any],
    row_index: int,
) -> tuple[MaterialCreate | None, BatchRowError | None]:
    """Validate a row dict against MaterialCreate schema."""
    try:
        return MaterialCreate(**data), None
    except ValidationError as exc:
        first_err = exc.errors()[0]
        field = str(first_err.get("loc", ["unknown"])[-1])
        msg = first_err.get("msg", "Validation error")
        return None, BatchRowError(row=row_index, field=field, message=msg)


async def _find_existing_material(
    db: AsyncSession,
    name: str,
    formula: str | None,
) -> Material | None:
    """Find an existing material by name+formula for upsert deduplication."""
    stmt = select(Material).where(Material.name == name)
    if formula is not None:
        stmt = stmt.where(Material.formula == formula)
    else:
        stmt = stmt.where(Material.formula.is_(None))
    result = (await db.execute(stmt)).scalar_one_or_none()
    return result


async def batch_import_materials(
    db: AsyncSession,
    *,
    content: bytes,
    filename: str,
) -> BatchImportResult:
    """Parse a CSV or JSON file and import/upsert materials.

    Returns a BatchImportResult with counts and per-row error details.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "json":
        raw_rows = _parse_json_content(content)
    elif ext == "csv":
        raw_rows = _parse_csv_content(content)
    else:
        raise ValueError(f"Unsupported file type: .{ext}. Use .csv or .json")

    imported = 0
    errors: list[BatchRowError] = []

    for i, raw_row in enumerate(raw_rows, start=2):  # Row 1 is header for CSV
        row_index = i if ext == "csv" else (i - 1)

        # Convert raw row → typed dict
        mapped, parse_err = _row_to_material_create(raw_row, row_index)
        if parse_err:
            errors.append(parse_err)
            continue

        # Validate against Pydantic schema
        validated, val_err = _validate_row(mapped, row_index)
        if val_err:
            errors.append(val_err)
            continue

        # Upsert: find existing by name+formula
        existing = await _find_existing_material(
            db, validated.name, validated.formula
        )
        if existing is not None:
            # Update existing record with new values
            updates = validated.model_dump(exclude_unset=True)
            for key, value in updates.items():
                setattr(existing, key, value)
            db.add(existing)
        else:
            # Create new record
            mat = Material(**validated.model_dump())
            db.add(mat)

        imported += 1

    # Single commit for the entire batch
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    failed = len(errors)
    logger.info(
        "Batch import complete: %d imported, %d failed",
        imported,
        failed,
    )
    return BatchImportResult(imported=imported, failed=failed, errors=errors)
