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

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.schemas.extraction import ExtractedProperty

logger = logging.getLogger(__name__)


# Unique-constraint violation fingerprints that the mapper must treat as a
# cross-request duplicate (rather than a real DB error).  The composite
# UNIQUE INDEXes introduced by migration 032 turn any concurrent-insert
# race into an IntegrityError; the dedup race must NOT surface as a 500.
# Match by both the wrapped pgcode (Postgres) and a substring over the
# message text for portability across SQLite and Postgres.
_DEDUP_CONFLICT_FRAGMENTS: tuple[str, ...] = (
    "uq_pm_dedup",
    "uq_datasets_source_material",
    "unique constraint",
    "unique_violation",
    "UNIQUE constraint failed",
)


def _is_dedup_conflict(exc: IntegrityError) -> bool:
    """True if the IntegrityError came from a 5-tuple unique violation."""
    msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
    return any(frag.lower() in msg for frag in _DEDUP_CONFLICT_FRAGMENTS)


#: Pydantic Literal allowed values for ExtractedProperty.property_category.
#: Anything else (including non-ASCII) is coerced to "other" before validation.
_VALID_PROPERTY_CATEGORIES: frozenset[str] = frozenset(
    {"mechanical", "thermal", "physical", "diffusion", "irradiation", "nuclear", "other"}
)


def _coerce_unknown_categories(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce non-Literal ``property_category`` values to ``"other"``.

    The OntoFuel LLM sometimes returns Chinese (or otherwise localized)
    category strings (e.g. ``"比热容"``) instead of the 7 English Literal
    values.  Without this, every item in the batch raises ValidationError
    and the batch is silently dropped.  With this, the value falls through
    as ``"other"`` and the downstream PropertyCategory catalog can map it
    back to the correct Chinese category at persist time.
    """
    if not isinstance(raw, dict):
        return raw
    cat = raw.get("property_category")
    if isinstance(cat, str) and cat not in _VALID_PROPERTY_CATEGORIES:
        return {**raw, "property_category": "other"}
    return raw


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
    reused_entities: int = 0
    skipped_duplicate_measurements: int = 0
    skipped_unknown_properties: int = 0
    validation_errors: int = 0

    @property
    def total_created(self) -> int:
        return (
            self.created_sources
            + self.created_materials
            + self.created_datasets
            + self.created_measurements
        )

    @property
    def skipped_duplicates(self) -> int:
        """Backward-compat alias: sum of all skip reasons."""
        return (
            self.reused_entities
            + self.skipped_duplicate_measurements
            + self.skipped_unknown_properties
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
# Measurement dedup (NFM-1981 AC-2)
# ---------------------------------------------------------------------------
# CPO Decision 1: Dedup key = (material_name, property_name, source_reference,
#   conditions_hash, measurement_method).
#   - conditions_hash included because different temperature/pressure under
#     the same property are *different* measurements (e.g. U-10Mo thermal
#     conductivity at 25°C vs 400°C).
#   - measurement_method included to distinguish techniques (tensile vs
#     nanoindentation, etc.).
# CPO Decision 2: Strategy C (keep all) — only skip when the 5-tuple is
#   identical.  Different method/conditions/material/property → separate
#   measurements.  Materials-science correctness over storage minimization.
# ---------------------------------------------------------------------------


def _conditions_hash(conditions: dict[str, Any] | None) -> str:
    """Stable SHA1 hex digest of a conditions dict.

    Uses ``sort_keys=True`` so key order does not affect the hash.
    ``None`` and empty ``{}`` both hash to the same value ("no conditions").

    SHA1 chosen over MD5: 160-bit collision resistance is more than
    sufficient for dedup keys and avoids known MD5 collision classes.
    """
    if not conditions:
        return hashlib.sha1(b"{}").hexdigest()
    serialised = json.dumps(conditions, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialised.encode("utf-8")).hexdigest()


def _measurement_dedup_key(item: ExtractedProperty) -> str:
    """Build the 5-tuple dedup key for a PropertyMeasurement.

    Key components:
    1. material_name (normalised: lowercased, stripped)
    2. property name (from the ``property`` field)
    3. source_reference (DOI or reference or source_file)
    4. conditions_hash (stable SHA1 of the conditions dict)
    5. measurement_method (from the ``method`` field, or empty string)
    """
    material = (item.material_name or "").strip().lower()
    prop = item.property
    source_ref = item.source_doi or item.reference or item.source_file or ""
    cond_h = _conditions_hash(item.conditions)
    method = (item.method or "").strip()
    return f"{material}|{prop}|{source_ref}|{cond_h}|{method}"


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
        parts = [existing_notes, *extra_parts] if existing_notes else extra_parts
        mapped["notes"] = "; ".join(parts)

    return mapped


# ---------------------------------------------------------------------------
# PropertyType lookup
# ---------------------------------------------------------------------------

# OntoFuel emits lowercase English literals (PropertyCategoryLiteral in
# schemas/extraction.py).  The DB ``property_categories`` table stores
# English ``name`` (e.g. "Thermal properties") and a short ``slug``
# (e.g. "thermal").  The four OntoFuel literals that have a matching DB
# slug map 1:1.  The remaining three fall back to broader categories.
ONTOFUEL_CATEGORY_TO_SLUG: dict[str, str] = {
    "mechanical": "mechanical",
    "thermal": "thermal",
    "physical": "physical",
    "nuclear": "nuclear",
    # --- fallbacks (no dedicated DB category yet) ---
    "diffusion": "physical",
    "irradiation": "nuclear",
    "other": "thermal",  # least-bad default; revisit when 'other' category is added
}


def _normalize_category_slug(category_name: str) -> str | None:
    """Translate an OntoFuel category literal to a DB ``property_categories.slug``.

    Returns ``None`` if the literal is not recognised.
    """
    return ONTOFUEL_CATEGORY_TO_SLUG.get(category_name)


async def _lookup_property_type(
    db: AsyncSession,
    *,
    category_name: str | None,
    property_name: str,
) -> PropertyType | None:
    """Find PropertyType by OntoFuel category literal + property name.

    Normalises the OntoFuel category literal to a DB ``slug`` via
    ``_normalize_category_slug`` before querying.

    Returns None if not found (caller should skip measurement).
    """
    if not category_name:
        return None

    category_slug = _normalize_category_slug(category_name)
    if category_slug is None:
        logger.debug(
            "Unknown OntoFuel category literal: %r", category_name,
        )
        return None

    stmt = (
        select(PropertyType)
        .join(PropertyCategory)
        .where(
            PropertyCategory.slug == category_slug,
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
        # Preprocess: coerce unknown property_category values (e.g. Chinese
        # LLM outputs like "比热容") to "other" instead of dropping the
        # entire item. The downstream PropertyCategory catalog translates
        # "other" to the correct Chinese category at persist time.
        raw = _coerce_unknown_categories(raw)

        try:
            validated.append(ExtractedProperty.model_validate(raw))
        except ValidationError as exc:
            # Log full validation details so silent schema drift is debuggable
            # (was previously only a count — NFM-1984/1985 lesson).
            logger.warning(
                "Validation failed for extraction item %d: %s",
                idx,
                exc.json(include_url=False)[:500],
            )
            logger.debug(
                "Validation failed item %d raw payload: %s",
                idx,
                json.dumps(raw, default=str)[:500],
            )
            validation_error_count += 1

        # Partial-success: keep validating the rest even when one fails.
        # Previously a single ValidationError caused the entire literature's
        # batch to be discarded — fixed per NFM-1984/1985 silent failure.
        # Items that DO validate still get persisted below.

    if validation_error_count > 0:
        logger.warning(
            "Extraction batch had %d/%d validation failures — partial persistence",
            validation_error_count,
            len(extraction_output),
        )

    if not validated:
        return MappingResult()

    # --- Phase 2: Group and dedup ---
    # Track created entities by dedup key to avoid duplicate inserts
    source_map: dict[str, DataSource] = {}
    material_map: dict[str, Material] = {}
    dataset_map: dict[str, Dataset] = {}
    # NFM-1981 AC-2: track seen 5-tuple keys to skip exact duplicates
    seen_measurement_keys: set[str] = set()

    created_sources = 0
    created_materials = 0
    created_datasets = 0
    created_measurements = 0
    reused_entities = 0
    skipped_duplicate_measurements = 0
    skipped_unknown_properties = 0

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
                    reused_entities += 1
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
                reused_entities += 1
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
            existing_dataset = (
                await db.execute(
                    select(Dataset).where(
                        Dataset.material_id == material.id,
                        Dataset.source_id == source.id,
                    )
                )
            ).scalars().first()
            if existing_dataset is not None:
                # Cross-request hit: same source+material already has a
                # dataset.  Reuse it so the 5-tuple dedup keys line up.
                dataset_map[d_key] = existing_dataset
                reused_entities += 1
            else:
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
            skipped_unknown_properties += 1
            continue

        # --- PropertyMeasurement (NFM-1981 AC-2: 5-tuple dedup) ---
        meas_key = _measurement_dedup_key(item)
        if meas_key in seen_measurement_keys:
            logger.debug("Skipping duplicate measurement: %s", meas_key)
            skipped_duplicate_measurements += 1
            continue
        seen_measurement_keys.add(meas_key)

        condition_kwargs = _build_condition_kwargs(item.conditions)
        value_kwargs, fallback_raw = _measurement_value_kwargs(item.value)
        if fallback_raw is not None:
            # NFM-1979 AC-4: float parse failed; persist raw string + warn, do
            # NOT crash the batch.
            logger.warning(
                "Could not parse value %r as float for property %s; "
                "storing raw string in value_text.",
                fallback_raw,
                item.property,
            )

        # NFM-2032 / NFM-2013 AC-4: hash the conditions dict so the DB
        # UNIQUE INDEX uq_pm_dedup on (dataset_id, property_type_id,
        # conditions_hash, method) can detect cross-request duplicates.
        cond_h = _conditions_hash(item.conditions)
        method_str = (item.method or "").strip() or ""

        # NFM-2032 CR Finding #4: wrap the per-measurement INSERT in a
        # SAVEPOINT so a concurrent cross-request dedup race produces
        # IntegrityError without poisoning the outer transaction.
        try:
            async with db.begin_nested():
                measurement = PropertyMeasurement(
                    dataset_id=dataset.id,
                    property_type_id=property_type.id,
                    uncertainty=item.uncertainty,
                    notes=item.context,
                    review_status="pending",
                    conditions_hash=cond_h,
                    method=method_str,
                    **value_kwargs,
                )
                db.add(measurement)
                await db.flush()
        except IntegrityError as exc:
            if _is_dedup_conflict(exc):
                # Concurrent or sequential cross-request duplicate.
                # Roll back this SAVEPOINT and count as skipped.
                logger.debug(
                    "Dedup conflict on (dataset=%s, prop_type=%s, "
                    "conditions_hash=%s, method=%s): %s",
                    dataset.id,
                    property_type.id,
                    cond_h,
                    method_str,
                    exc,
                )
                skipped_duplicate_measurements += 1
                continue
            # Real DB error — propagate.
            raise

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
        reused_entities=reused_entities,
        skipped_duplicate_measurements=skipped_duplicate_measurements,
        skipped_unknown_properties=skipped_unknown_properties,
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
    """Safely parse a string value to float.

    Returns None if not parseable. Callers must decide what to do with None
    (e.g., fall back to ``value_text`` so the batch is not lost).
    """
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _measurement_value_kwargs(raw_value: str) -> tuple[dict[str, Any], str | None]:
    """Build PropertyMeasurement value kwargs from a raw string ``value``.

    Returns a tuple of (kwargs_for_measurement, raw_value_if_fallback).

    Behavior (NFM-1979 AC-4):
    - On successful float parse: ``value_scalar`` is set, raw_value is None.
    - On parse failure: ``value_text`` is set to the original raw string
      (preserving precision/range like ``"3 to 4"``) and the raw_value is
      returned so the caller can log a WARNING. The batch is never aborted.
    """
    parsed = _parse_float(raw_value)
    if parsed is not None:
        return {"value_scalar": parsed}, None
    return {"value_text": raw_value}, raw_value
