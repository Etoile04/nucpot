"""Batch import/export service for materials, properties, reference_values (NFM-1085).

Provides CSV/JSON parsing, row validation, summary generation, and
export serialization for the batch API endpoints.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from nfm_db.schemas.batch import (
    BatchRowError,
    PropertyMeasurementRow,
    ReferenceValueRow,
)
from nfm_db.schemas.material import MaterialCreate

logger = logging.getLogger(__name__)

# Configurable limits via environment variables
BATCH_IMPORT_MAX_ROWS = int(os.environ.get("BATCH_IMPORT_MAX_ROWS", "10000"))
BATCH_IMPORT_MAX_SIZE_MB = int(os.environ.get("BATCH_IMPORT_MAX_SIZE_MB", "10"))

# CSV column → MaterialCreate field mapping
_CSV_COLUMN_MAP: dict[str, str] = {
    "name": "name",
    "formula": "formula",
    "crystal_structure": "crystal_structure",
    "category_id": "category_id",
    "description": "description",
    "is_active": "is_active",
}

# CSV columns for reference value import
_REF_CSV_COLUMNS = [
    "element_system",
    "phase",
    "property_name",
    "value",
    "unit",
    "method",
    "source",
    "source_doi",
    "uncertainty",
    "temperature",
]

# CSV columns for property measurement import
_PROP_CSV_COLUMNS = [
    "dataset_id",
    "property_type_id",
    "value_scalar",
    "value_min",
    "value_max",
    "value_expression",
    "value_list",
    "value_text",
    "uncertainty",
    "unit_id",
    "notes",
]


# ── CSV/JSON Parsing ───────────────────────────────────────────────


def parse_csv_content(content: bytes) -> list[dict[str, str]]:
    """Parse CSV bytes into a list of row dicts with raw string values.

    Handles UTF-8 BOM, empty rows, and trailing whitespace.
    """
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for row in reader:
        if not any(v for v in row.values() if v is not None and v.strip()):
            continue
        cleaned = {k: v.strip() if v else v for k, v in row.items()}
        rows.append(cleaned)
    return rows


def parse_json_content(content: bytes) -> list[dict[str, Any]]:
    """Parse JSON bytes into a list of row dicts.

    Raises ValueError if content is not a JSON array.
    """
    text = content.decode("utf-8")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("JSON content must be an array of objects")
    return data


# ── Material Row Validation ─────────────────────────────────────────


def _coerce_material_row(
    row: dict[str, Any],
) -> tuple[dict[str, Any] | None, BatchRowError | None, int | None]:
    """Convert a raw row dict to a MaterialCreate-compatible dict.

    Returns (mapped_dict, None, None) on success,
    or (None, error, row_idx) on failure.
    """
    mapped: dict[str, Any] = {}
    row_idx = row.get("_row")

    for csv_col, field_name in _CSV_COLUMN_MAP.items():
        value = None
        for key in row:
            if key.strip().lower() == csv_col:
                value = row[key]
                break
        if value is None or str(value).strip() == "":
            continue

        str_val = str(value).strip()

        if field_name == "is_active":
            lower = str_val.lower()
            if lower in ("true", "1", "yes"):
                mapped[field_name] = True
            elif lower in ("false", "0", "no"):
                mapped[field_name] = False
            else:
                return (
                    None,
                    BatchRowError(
                        row=row_idx or 0,
                        field=csv_col,
                        message=f"Invalid boolean value: '{value}'",
                    ),
                    row_idx,
                )
        elif field_name == "category_id":
            mapped[field_name] = str_val
        else:
            mapped[field_name] = str_val

    if "name" not in mapped:
        return (
            None,
            BatchRowError(
                row=row_idx or 0,
                field="name",
                message="Missing required field 'name'",
            ),
            row_idx,
        )

    return mapped, None, row_idx


def validate_material_row(
    row: dict[str, Any],
    row_index: int | None = None,
) -> tuple[MaterialCreate | None, BatchRowError | None]:
    """Validate a row dict against MaterialCreate schema.

    Returns (validated_model, None) on success, (None, error) on failure.
    """
    row_with_idx = {**row, "_row": row_index}
    mapped, coerce_err, _ = _coerce_material_row(row_with_idx)
    if coerce_err or mapped is None:
        return None, coerce_err

    try:
        return MaterialCreate(**mapped), None
    except ValidationError as exc:
        first_err = exc.errors()[0]
        field = str(first_err.get("loc", ["unknown"])[-1])
        msg = first_err.get("msg", "Validation error")
        return None, BatchRowError(row=row_index or 0, field=field, message=msg)


# ── Reference Value Row Validation ─────────────────────────────────


def validate_reference_value_row(
    row: dict[str, Any],
    row_index: int,
) -> tuple[ReferenceValueRow | None, BatchRowError | None]:
    """Validate a row dict against ReferenceValueRow schema.

    Returns (validated_model, None) on success, (None, error) on failure.
    """
    try:
        return ReferenceValueRow(**row), None
    except ValidationError as exc:
        first_err = exc.errors()[0]
        field = str(first_err.get("loc", ["unknown"])[-1])
        msg = first_err.get("msg", "Validation error")
        return None, BatchRowError(row=row_index, field=field, message=msg)


# ── Property Measurement Row Validation ────────────────────────────


def validate_property_row(
    row: dict[str, Any],
    row_index: int,
) -> tuple[PropertyMeasurementRow | None, BatchRowError | None]:
    """Validate a row dict against PropertyMeasurementRow schema.

    Returns (validated_model, None) on success, (None, error) on failure.
    """
    try:
        return PropertyMeasurementRow(**row), None
    except ValidationError as exc:
        first_err = exc.errors()[0]
        field = str(first_err.get("loc", ["unknown"])[-1])
        msg = first_err.get("msg", "Validation error")
        return None, BatchRowError(row=row_index, field=field, message=msg)


# ── Export Serialization ──────────────────────────────────────────

# Columns for each entity's CSV export
_MATERIAL_CSV_COLUMNS = [
    "id",
    "name",
    "formula",
    "crystal_structure",
    "category_id",
    "description",
    "is_active",
]

_REF_VALUE_CSV_COLUMNS = [
    "element_system",
    "phase",
    "property_name",
    "value",
    "unit",
    "method",
    "source",
    "source_doi",
    "uncertainty",
    "temperature",
]

_PROPERTY_CSV_COLUMNS = [
    "id",
    "dataset_id",
    "property_type_id",
    "value_scalar",
    "value_min",
    "value_max",
    "value_expression",
    "value_list",
    "value_text",
    "uncertainty",
    "unit_id",
    "notes",
]


def serialize_to_csv(
    rows: list[dict[str, Any]],
    columns: list[str],
) -> str:
    """Serialize a list of row dicts to CSV string with given columns."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def serialize_to_json(rows: list[dict[str, Any]]) -> str:
    """Serialize a list of row dicts to JSON string."""
    return json.dumps(rows, ensure_ascii=False, indent=2)


def serialize_materials_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize material rows to CSV."""
    return serialize_to_csv(rows, _MATERIAL_CSV_COLUMNS)


def serialize_materials_to_json(rows: list[dict[str, Any]]) -> str:
    """Serialize material rows to JSON."""
    return serialize_to_json(rows)


def serialize_properties_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize property measurement rows to CSV."""
    return serialize_to_csv(rows, _PROPERTY_CSV_COLUMNS)


def serialize_properties_to_json(rows: list[dict[str, Any]]) -> str:
    """Serialize property measurement rows to JSON."""
    return serialize_to_json(rows)


def serialize_reference_values_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize reference value rows to CSV."""
    return serialize_to_csv(rows, _REF_VALUE_CSV_COLUMNS)


def serialize_reference_values_to_json(rows: list[dict[str, Any]]) -> str:
    """Serialize reference value rows to JSON."""
    return serialize_to_json(rows)


# ── Export Filename Generation ──────────────────────────────────────


def generate_export_filename(
    entity: str,
    fmt: str,
    timestamp: datetime | None = None,
) -> str:
    """Generate a filename for batch export.

    Format: {entity}_YYYYMMDD.{fmt}
    """
    if timestamp is None:
        timestamp = datetime.now(tz=None)
    date_str = timestamp.strftime("%Y%m%d")
    return f"{entity}_{date_str}.{fmt}"
