"""Extraction-to-DB Mapper service (NFM-700).

Transforms extraction pipeline JSON output (ExtractedProperty dicts)
into SQLAlchemy model instances and persists them to the database.

Mapping:
  source_doi / reference → DataSource (dedup by DOI)
  material_name / composition → Material + MaterialComposition (dedup by formula)
  property_category / property → PropertyType lookup
  value / unit / conditions → Dataset + PropertyMeasurement + MeasurementCondition

All operations run within a single DB transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    DataSource,
    Dataset,
    Material,
    MaterialComposition,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.schemas.extraction import ExtractedProperty

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappingResult:
    """Immutable result counts from the mapping operation."""

    created_sources: int = 0
    created_materials: int = 0
    created_datasets: int = 0
    created_measurements: int = 0
    skipped_duplicates: int = 0
    validation_errors: int = 0

    @property
    def total_created(self) -> int:
        return (
            self.created_sources
            + self.created_materials
            + self.created_datasets
            + self.created_measurements
        )


class MappingError(Exception):
    """Raised when mapping fails for a specific extraction item."""

    def __init__(self, message: str, *, item_index: int | None = None) -> None:
        self.item_index = item_index
        detail = f"[item {item_index}] " if item_index is not None else ""
        super().__init__(f"{detail}{message}")


# ---------------------------------------------------------------------------
# Internal grouping keys
# ---------------------------------------------------------------------------


def _source_key(item: ExtractedProperty) -> str:
    """Build a dedup key for DataSource from extraction fields."""
    doi = item.source_doi or ""
    ref = item.reference or ""
    src = item.source_file or ""
    return f"doi:{doi}|ref:{ref}|src:{src}"


def _material_key(item: ExtractedProperty) -> str:
    """Build a dedup key for Material from extraction fields."""
    name = (item.material_name or "").strip().lower()
    formula = (item.composition or "").strip().lower()
    return f"formula:{formula}|name:{name}"


def _dataset_key(source_key: str, material_key: str) -> str:
    """Composite key for Dataset dedup."""
    return f"{source_key}||{material_key}"


# ---------------------------------------------------------------------------
# Condition mapping
# ---------------------------------------------------------------------------

_CONDITION_KEY_MAP: dict[str, str] = {
    "temperature": "temperature",
    "temp": "temperature",
    "pressure": "pressure",
    "environment": "environment",
    "irradiation_dose": "irradiation_dose",
    "dose": "irradiation_dose",
}


def _build_condition_kwargs(
    conditions: dict[str, Any] | None,
) -> dict[str, Any]:
    """Map extraction conditions dict to MeasurementCondition field kwargs.

    Returns only the fields that have matching values in the conditions dict.
    """
    if not conditions:
        return {}

    mapped: dict[str, Any] = {}
    for src_key, db_key in _CONDITION_KEY_MAP.items():
        if src_key in conditions:
            val = conditions[src_key]
            if val is not None:
                mapped[db_key] = val

    # Capture any leftover as notes
    known_keys = set(_CONDITION_KEY_MAP.keys())
    extra_keys = [k for k in conditions if k not in known_keys and k != "notes"]
    if extra_keys:
        extra_parts = [f"{k}={conditions[k]}" for k in extra_keys]
        existing_notes = conditions.get("notes", "")
        parts = [existing_notes] + extra_parts if existing_notes else extra_parts
        mapped["notes"] = "; ".join(parts)

    return mapped


# ---------------------------------------------------------------------------
# PropertyType lookup
# ---------------------------------------------------------------------------


async def _lookup_property_type(
    db: AsyncSession,
    *,
    category_name: str | None,
    property_name: str,
) -> PropertyType | None:
    """Find PropertyType by category name + property name.

    Returns None if not found (caller should skip measurement).
    """
    if not category_name:
        return None

    stmt = (
        select(PropertyType)
        .join(PropertyCategory)
        .where(
            PropertyCategory.name == category_name,
            PropertyType.name == property_name,
        )
    )
    result = (await db.execute(stmt)).scalar_one_or_none()
    return result


# ---------------------------------------------------------------------------
# Core mapping function
# ---------------------------------------------------------------------------


async def map_and_persist(
    db: AsyncSession,
    extraction_output: list[dict[str, Any]],
) -> MappingResult:
    """Parse extraction output, validate, and persist to the database.

    Args:
        db: Active async database session (caller manages commit/rollback).
        extraction_output: List of raw dicts from the extraction pipeline.

    Returns:
        MappingResult with creation counts and error counts.

    All writes happen within a single transaction. If any item fails
    Pydantic validation, no records are written.
    """
    # --- Phase 1: Validate all items before any DB writes ---
    validated: list[ExtractedProperty] = []
    validation_error_count = 0

    for idx, raw in enumerate(extraction_output):
        try:
            validated.append(ExtractedProperty.model_validate(raw))
        except ValidationError:
            logger.warning("Validation failed for extraction item %d", idx)
            validation_error_count += 1

    if validation_error_count > 0:
        return MappingResult(validation_errors=validation_error_count)

    if not validated:
        return MappingResult()

    # --- Phase 2: Group and dedup ---
    # Track created entities by dedup key to avoid duplicate inserts
    source_map: dict[str, DataSource] = {}
    material_map: dict[str, Material] = {}
    dataset_map: dict[str, Dataset] = {}
    skipped = 0

    created_sources = 0
    created_materials = 0
    created_datasets = 0
    created_measurements = 0

    for item in validated:
        s_key = _source_key(item)
        m_key = _material_key(item)
        d_key = _dataset_key(s_key, m_key)

        # --- DataSource (find or create) ---
        if s_key not in source_map:
            doi = item.source_doi
            title = item.reference or item.source_file or "Unknown Source"

            if doi:
                existing = await _find_source_by_doi(db, doi)
                if existing:
                    source_map[s_key] = existing
                    skipped += 1
                else:
                    source = DataSource(
                        doi=doi,
                        title=title,
                        source_type="journal_article",
                    )
                    db.add(source)
                    await db.flush()
                    source_map[s_key] = source
                    created_sources += 1
            else:
                source = DataSource(
                    title=title,
                    source_type="other",
                )
                db.add(source)
                await db.flush()
                source_map[s_key] = source
                created_sources += 1

        source = source_map[s_key]

        # --- Material (find or create) ---
        if m_key not in material_map:
            material_name = item.material_name or "Unknown Material"
            formula = item.composition or item.material_name

            existing_mat = await _find_material_by_formula(db, formula)
            if existing_mat:
                material_map[m_key] = existing_mat
                skipped += 1
            else:
                material = Material(
                    name=material_name,
                    formula=formula,
                    is_active=True,
                )
                db.add(material)
                await db.flush()
                material_map[m_key] = material
                created_materials += 1

        material = material_map[m_key]

        # --- Dataset (find or create for this source+material pair) ---
        if d_key not in dataset_map:
            dataset_title = f"{material.name} - {source.title}"
            dataset = Dataset(
                material_id=material.id,
                source_id=source.id,
                title=dataset_title,
                is_verified=False,
            )
            db.add(dataset)
            await db.flush()
            dataset_map[d_key] = dataset
            created_datasets += 1

        dataset = dataset_map[d_key]

        # --- PropertyType lookup ---
        property_type = await _lookup_property_type(
            db,
            category_name=item.property_category,
            property_name=item.property,
        )
        if property_type is None:
            logger.debug(
                "Skipping unknown property: category=%s name=%s",
                item.property_category,
                item.property,
            )
            skipped += 1
            continue

        # --- PropertyMeasurement ---
        condition_kwargs = _build_condition_kwargs(item.conditions)
        measurement = PropertyMeasurement(
            dataset_id=dataset.id,
            property_type_id=property_type.id,
            value_scalar=_parse_float(item.value),
            uncertainty=item.uncertainty,
            notes=item.context,
            review_status="pending",
        )
        db.add(measurement)
        await db.flush()

        # --- MeasurementCondition ---
        if condition_kwargs:
            condition = MeasurementCondition(
                measurement_id=measurement.id,
                **condition_kwargs,
            )
            db.add(condition)

        created_measurements += 1

    # Commit all writes in a single transaction
    await db.commit()

    return MappingResult(
        created_sources=created_sources,
        created_materials=created_materials,
        created_datasets=created_datasets,
        created_measurements=created_measurements,
        skipped_duplicates=skipped,
        validation_errors=0,
    )


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


async def _find_source_by_doi(
    db: AsyncSession,
    doi: str,
) -> DataSource | None:
    """Find existing DataSource by DOI."""
    stmt = select(DataSource).where(DataSource.doi == doi)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_material_by_formula(
    db: AsyncSession,
    formula: str | None,
) -> Material | None:
    """Find existing Material by formula."""
    if not formula:
        return None
    stmt = select(Material).where(Material.formula == formula)
    return (await db.execute(stmt)).scalar_one_or_none()


def _parse_float(value: str) -> float | None:
    """Safely parse a string value to float. Returns None if not parseable."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
