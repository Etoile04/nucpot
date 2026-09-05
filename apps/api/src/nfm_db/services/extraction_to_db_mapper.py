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
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    MaterialAlias,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
    Unit,
)
from nfm_db.schemas.extraction import ExtractedProperty
from nfm_db.services.health_event_emitter import (
    EVENT_VALIDATION_DROP,
    SEVERITY_WARNING,
    build_context,
    emit_health_event,
)

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


#: NFM-4088 — guard against UUID-pattern ``title`` (root cause: prior
#: source's primary-key string was being copied into the new row's
#: ``title`` when extraction emitted a UUID instead of a reference).
#: Canonical 36-char UUID, case-insensitive, anchored on both ends.
_UUID_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


#: NFM-4088 — placeholder titles that the DOI-empty branch emits when
#: neither ``reference`` nor ``source_file`` is supplied.  These were
#: reused across distinct literature sources in production; the
#: dedup migration 070 collapses them.  The mapper still allows new
#: INSERTs under these titles (re-run compatibility) but prefers a
#: dedup hit on file_hash or content_md first.
_BORING_PLACEHOLDER_TITLES: frozenset[str] = frozenset(
    {"Unknown Source", "Unattributed source (no DOI)"}
)


#: NFM-4105 — canonical sentinel title for "truly unattributed" extractions
#: (no DOI, no reference, no source_file).  Distinct from the legacy
#: ``"Unattributed source (no DOI)"`` placeholder so a migration can later
#: quarantine / retire the legacy rows without touching the new sentinel.
#: All new "no provenance at all" extractions converge on a single row
#: via ``_get_or_create_unattributed_sentinel`` (advisory-locked).
_UNATTRIBUTED_SENTINEL_TITLE: str = "Unattributed (no source provenance)"

#: NFM-4105 — Postgres advisory-lock key for the sentinel-source
#: get-or-create path.  Computed once at import time from a stable
#: module-level hash so all workers agree on the same lock.
_UNATTRIBUTED_SENTINEL_LOCK_KEY: int = int(
    hashlib.sha256(b"nfm_db.extraction_to_db_mapper.unattributed_sentinel").hexdigest()[:8],
    16,
)


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
    try:
        cat = raw.get("property_category")
        if isinstance(cat, str) and cat not in _VALID_PROPERTY_CATEGORIES:
            return {**raw, "property_category": "other"}
        return raw
    except Exception as exc:
        # NFM-2241 C3 pattern #2: a coercion failure must leave a
        # trace. ``emit_health_event_sync`` is correct here because
        # ``_coerce_unknown_categories`` is invoked synchronously from
        # ``map_and_persist``; the emitter opens its own session.
        from nfm_db.services.health_event_emitter import (
            EVENT_CATEGORY_COERCION_FAIL,
            SEVERITY_WARNING,
            emit_health_event_sync,
        )
        from nfm_db.services.health_event_emitter import (
            build_context as _build_ctx,
        )

        emit_health_event_sync(
            event_type=EVENT_CATEGORY_COERCION_FAIL,
            severity=SEVERITY_WARNING,
            source_service="extraction_to_db_mapper",
            context=_build_ctx(exc, raw_repr=repr(raw)[:200]),
        )
        return raw


def _coerce_heuristic_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise heuristic_regex fallback payloads to the canonical shape.

    The ``heuristic_regex`` extraction path (used when the LLM is
    unavailable or returns nothing) emits a payload shape that the strict
    ``ExtractedProperty`` schema rejects:

    - field name is ``property_name`` instead of ``property`` (the schema
      marks ``property_name`` as ``deprecated=True`` and ``property`` is
      ``Field(...)`` required)
    - ``value`` is a ``float``/``int`` instead of the required ``str``

    Without coercion, every heuristic item raises ``ValidationError`` and
    the mapper silently drops the whole batch (the worker log shows
    ``extracted: 4`` but writes 0 rows — see NFM-3374 / NFM-3369-AC-β).

    This function:
    - Returns ``raw`` unchanged when no coercion is needed (cheap fast-path
      for the canonical LLM path).
    - Returns a shallow copy only when a field is actually transformed —
      never mutates the caller's dict (immutable contract).
    - Does NOT invent a ``property`` value when both ``property`` and
      ``property_name`` are absent; that case is genuinely invalid and
      must surface as a ``ValidationError`` so it is counted in
      ``validation_errors``.
    """
    if not isinstance(raw, dict):
        return raw

    coerced: dict[str, Any] = raw
    needs_copy = False

    # 1) property_name → property alias (only when property is absent)
    if not raw.get("property") and raw.get("property_name"):
        coerced = {**raw, "property": raw["property_name"]}
        needs_copy = True

    # 2) Numeric value → string value (heuristic emits raw floats/ints).
    #    bool is a subclass of int in Python — exclude it explicitly so a
    #    future caller passing ``value=True`` does not get stringified
    #    silently.
    value = coerced.get("value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        coerced = {**coerced, "value": str(value)}
        needs_copy = True

    return coerced if needs_copy else raw


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MappingResult:
    """Immutable result counts from the mapping operation.

    ``skipped_unknown_details`` is the structured capture list used by the
    NFM-4013 measurement harness to enumerate the LLM-only 14 property
    names that static gap analysis misses (heuristic_extractor vs
    031_seed_property_types.py). Each entry preserves enough context
    (``source_doi``, ``material_name``, ``sample_value``) for downstream
    classification on NFM-4008. The list is built inside
    :func:`map_and_persist` at the ``_lookup_property_type`` drop site.
    """

    created_sources: int = 0
    created_materials: int = 0
    created_datasets: int = 0
    created_measurements: int = 0
    reused_entities: int = 0
    skipped_duplicate_measurements: int = 0
    skipped_unknown_properties: int = 0
    # NFM-3919: items where BOTH material_name and composition are None
    # (e.g. an extractor that omits both fields) are rejected at the mapper
    # bottom line so the database is never polluted with fresh
    # ``name='Unknown Material'`` rows. Counted separately from
    # ``skipped_unknown_properties`` so operators can alert on the specific
    # "extractor schema-drift" signal.
    skipped_unknown_materials: int = 0
    # NFM-4312 (BUG-32): per-stage hits of the staged material
    # resolution ("formula" / "normalized_formula" / "alias" / "name").
    # Lets operators watch where ingest resolution lands — a spike in
    # "alias" means curators are steering, a flat zero across all stages
    # with rising ``created_materials`` means fragmentation is back.
    material_resolution_counts: dict[str, int] = field(default_factory=dict)
    validation_errors: int = 0
    # NFM-4013 / Path (a): capture list populated at the unknown-property
    # drop site. Each entry is a dict with the keys below — see
    # ``scripts/nfm-4012-unknown-property-enumeration.py`` for the consumer.
    skipped_unknown_details: list[dict[str, Any]] = field(default_factory=list)

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
            + self.skipped_unknown_materials
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
# Confidence → review_status mapping (NFM-3405 AC-3)
# ---------------------------------------------------------------------------

# PropertyMeasurement.review_status is the column from which
# property_service._derive_confidence reads to produce the per-measurement
# confidence surfaced by the API.  Map the LLM-emitted confidence literal
# ("high" | "medium" | "low") onto the corresponding review_status so the
# derived confidence varies per-property instead of being a flat 0.70
# (which is what "pending" produces).
_CONFIDENCE_TO_REVIEW_STATUS: dict[str, str] = {
    "high": "approved",
    "medium": "pending",
    "low": "flagged",
}


def _confidence_to_review_status(confidence: str | None) -> str:
    """Map an extraction confidence literal to a PropertyMeasurement.review_status.

    Unknown / missing confidence keeps the existing default ("pending") so
    downstream behaviour is unchanged for ambiguous inputs.
    """
    if not confidence:
        return "pending"
    return _CONFIDENCE_TO_REVIEW_STATUS.get(confidence.lower(), "pending")


# ---------------------------------------------------------------------------
# Unit resolution (NFM-3405 AC-2)
# ---------------------------------------------------------------------------


async def _resolve_unit(db: AsyncSession, symbol: str | None) -> Unit | None:
    """Find or create a Unit row matching ``symbol``.

    The extraction pipeline emits a unit *string* per property.  Previously
    the mapper dropped that string on the floor and PropertyMeasurement
    carried ``unit_id = NULL``, which made the API surface the placeholder
    "—".  This helper looks the Unit up by its unique symbol, or — when the
    symbol is brand new — creates a stub row so we never lose provenance.

    Returns ``None`` only if ``symbol`` is falsy / empty.
    """
    if not symbol or not symbol.strip():
        return None

    symbol = symbol.strip()
    stmt = select(Unit).where(Unit.symbol == symbol)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    # Brand-new symbol — create a stub Unit so the FK on PropertyMeasurement
    # is satisfied.  ``dimension`` is unknown at this stage; "unknown" is the
    # documented sentinel for "not yet classified".
    unit = Unit(
        name=symbol,
        symbol=symbol,
        dimension="unknown",
        description="Auto-created from extraction pipeline (NFM-3405).",
    )
    db.add(unit)
    await db.flush()
    return unit


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


def _normalize_category_slug(category_name: str | None) -> str | None:
    """Translate an OntoFuel category literal to a DB ``property_categories.slug``.

    Returns ``None`` if the literal is not recognised (including ``None``
    input from upstream ``ExtractedProperty.property_category``).
    """
    if category_name is None:
        return None
    return ONTOFUEL_CATEGORY_TO_SLUG.get(category_name)


async def _lookup_property_type(
    db: AsyncSession,
    *,
    category_name: str | None,
    property_name: str,
) -> PropertyType | None:
    """Find PropertyType by OntoFuel category literal + property name.

    Two-stage lookup (NFM-4019):

    1. **Strict lookup** — match ``(property_categories.slug, property_types.name)``
       against the OntoFuel category literal (normalised via
       :func:`_normalize_category_slug`). This is the canonical path and
       handles every property whose LLM-extracted category literal matches
       the seed.
    2. **Name-only fallback** — when the strict lookup misses AND the
       property name is unique across all categories, return that unique
       match. This resolves the 4 LLM-side category-context mismatches
       flagged by NFM-4019:
       - ``bulk_modulus``, ``lattice_constant``, ``thermal_conductivity``:
         LLM emits no category literal at all (stage 1 returns None
         immediately).
       - ``melting_point``: LLM emits ``category=thermal`` but the seed
         places it under ``physical``; strict lookup misses on the slug,
         and the fallback finds the single row under ``physical``.

    The fallback only succeeds when ``property_types.name`` matches
    **exactly one** row across all categories. Catalog gaps like
    ``elastic_constant`` (singular, addressed by NFM-4008 / 032_seed) and
    ``solubility_limit`` (addressed by NFM-4008 / 032_seed) remain absent
    from ``property_types`` entirely, so the fallback also misses and the
    mapper correctly drops them.

    Returns None if not found (caller should skip measurement).
    """
    if not property_name:
        return None

    # Stage 1: strict (slug, name) lookup.
    if category_name:
        category_slug = _normalize_category_slug(category_name)
        if category_slug is None:
            logger.debug(
                "Unknown OntoFuel category literal: %r; trying name-only fallback",
                category_name,
            )
        else:
            stmt = (
                select(PropertyType)
                .join(PropertyCategory)
                .where(
                    PropertyCategory.slug == category_slug,
                    PropertyType.name == property_name,
                )
            )
            result = (await db.execute(stmt)).scalar_one_or_none()
            if result is not None:
                return result
            logger.debug(
                "Strict lookup miss: category_slug=%s name=%s; trying name-only fallback",
                category_slug,
                property_name,
            )
    else:
        logger.debug(
            "No category literal; trying name-only fallback for name=%s",
            property_name,
        )

    # Stage 2: name-only fallback (NFM-4019).
    # Only succeed when the property name is unique across all categories.
    # This is safe because the seed canonicalises each (name, category)
    # pair, and the AC-1 v0.5.0 ontology expansion (NFM-4008) keeps each
    # canonical name under exactly one category.
    fallback_stmt = select(PropertyType).where(PropertyType.name == property_name)
    fallback_rows = (await db.execute(fallback_stmt)).scalars().all()
    if len(fallback_rows) == 1:
        resolved = fallback_rows[0]
        logger.info(
            "NFM-4019 fallback: resolved name=%s via name-only lookup to "
            "category_id=%s (raw_category=%r)",
            property_name,
            resolved.category_id,
            category_name,
        )
        return resolved

    if len(fallback_rows) > 1:
        logger.debug(
            "NFM-4019 fallback ambiguous: name=%s matches %d property_types "
            "rows; skipping (would need explicit category resolution)",
            property_name,
            len(fallback_rows),
        )
    return None


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
        # Preprocess: normalise heuristic_regex fallback shape (NFM-3374).
        # The heuristic emits ``property_name`` (alias for ``property``)
        # and a numeric ``value``; both are rejected by the strict
        # ExtractedProperty schema, so the whole batch would otherwise be
        # silently dropped.  This must run BEFORE _coerce_unknown_categories
        # so the alias is in place when Pydantic inspects the dict.
        raw = _coerce_heuristic_payload(raw)

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
        # NFM-2241 C3 pattern #3: a total-validation failure used to
        # vanish from the telemetry stream — the caller saw the count
        # in the MappingResult but the broader pipeline had no signal.
        # Emit before returning so an alert query can flag a literature
        # whose extraction output is entirely un-parseable.
        await emit_health_event(
            event_type=EVENT_VALIDATION_DROP,
            severity=SEVERITY_WARNING,
            source_service="extraction_to_db_mapper",
            context=build_context(
                None,
                validation_errors=validation_error_count,
                batch_size=len(extraction_output),
            ),
        )
        return MappingResult(validation_errors=validation_error_count)

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
    skipped_unknown_materials = 0  # NFM-3919
    # NFM-4312 — staged-resolution hit counters (see MappingResult).
    material_resolution_counts: dict[str, int] = {}
    # NFM-4013 / Path (a): accumulate structured unknown-property records so
    # ``MappingResult.skipped_unknown_details`` enumerates the LLM-only
    # names dropped by ``_lookup_property_type``.
    skipped_unknown_details: list[dict[str, Any]] = []

    for item in validated:
        s_key = _source_key(item)
        m_key = _material_key(item)
        d_key = _dataset_key(s_key, m_key)

        # --- NFM-3919 bottom-line guard: reject ONLY when both material
        # identity fields are absent. Rejecting on EITHER-missing was the
        # CR-1 bug (E2E QA 2026-09-01): LLM extraction_prompt.py:305-306
        # explicitly permits ``composition=None`` for materials where the
        # name itself carries the chemistry (e.g. SS316, Zr-2.5Nb, Inconel
        # 718). 78/131 prod ``materials`` rows currently have
        # ``name = formula`` from the legacy ``or material_name`` fallback
        # — the same pattern. The previous ``not (A and B)`` check
        # (= ``not A or not B``) would have silently dropped all of them.
        # Now restricted to both-None, with explicit ``is None`` so empty
        # strings are still accepted (extractor schema-drift guard).
        if item.material_name is None and item.composition is None:
            logger.warning(
                "Skipping extraction item with no material identity: "
                "material_name=%r composition=%r "
                "(NFM-3919 — extractor schema-drift guard)",
                item.material_name,
                item.composition,
            )
            skipped_unknown_materials += 1
            continue

        # --- DataSource (find or create) ---
        if s_key not in source_map:
            doi = item.source_doi
            # NFM-3405 AC-1: prefer the citation reference (Author, Title) so
            # the API surfaces a real literature label.  Only fall back to the
            # source filename (informative) and finally to the explicit
            # placeholder when the extraction genuinely supplied nothing.
            title = (
                item.reference
                or item.source_file
                or f"Unattributed source ({item.source_doi or 'no DOI'})"
            )

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
                # NFM-4088 — write-path guard for the DOI-empty branch.
                #
                # Root cause: prior runs copied a previous source's
                # primary-key UUID into the new row's ``title`` field
                # when extraction emitted no real reference.  This block:
                #
                #   1. Refuses INSERT when ``title`` matches the canonical
                #      36-char UUID pattern (defence-in-depth: the regex
                #      guards against re-introducing the regression that
                #      migration 070 just cleaned up).
                #   2. Dedups by exact ``title`` (handles the placeholder
                #      reuse case: ``"Unattributed source (no DOI)"``
                #      resolves to a single canonical row across reruns).
                #   3. Falls back to ``file_hash`` / ``content_md`` matching
                #      when the title-based lookup misses, matching the
                #      NFM-1486 PDF upload pipeline's identity model.
                #   4. Inserts a fresh row only when all 3 lookups miss.
                #
                # Behaviour on UUID title (AC-4): raise ``ValueError`` so
                # the caller's transaction aborts and the upstream batch
                # is dropped — the alternative (silent substitute) would
                # paper over a logic bug in the extraction pipeline.
                _reject_uuid_title(title)

                # NFM-4105 AC-1 — convergence for truly unattributed rows.
                #
                # The 58+ staging / 12 prod rows observed in the live
                # provenance graph (NFM-4105 AC-1) all share
                # ``doi IS NULL AND reference IS NULL AND source_file IS NULL``.
                # The prior title-based dedup could only collapse rows
                # within a single mapper invocation; concurrent extractions
                # in separate transactions each saw the lookup miss and
                # inserted a fresh placeholder row, multiplying the count.
                #
                # When the item carries ZERO provenance we route through
                # ``_get_or_create_unattributed_sentinel`` (advisory-locked
                # SELECT-then-INSERT) so every concurrent caller converges
                # on one canonical row instead of growing the pile.  The
                # sentinel row is mapped into ``source_map`` so the rest of
                # the loop body (Material / Dataset / Measurement) proceeds
                # against it exactly like any other source.
                if not _has_any_provenance(item):
                    sentinel, sentinel_created_now = (
                        await _get_or_create_unattributed_sentinel(db)
                    )
                    source_map[s_key] = sentinel
                    if sentinel_created_now:
                        created_sources += 1
                    else:
                        reused_entities += 1
                else:
                    existing = await _find_source_by_title(db, title)
                    if existing is None:
                        existing = await _find_source_by_content_md_prefix(
                            db, item.source_file
                        )

                    if existing:
                        source_map[s_key] = existing
                        reused_entities += 1
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
        # NFM-3919: the top-of-loop guard ensures both ``item.material_name``
        # and ``item.composition`` are truthy here, so we no longer fall back
        # to ``"Unknown Material"`` — that fallback was the root cause of the
        # pollution where every heuristic run inserted a fresh row.
        #
        # NFM-4312 (BUG-32): the single exact-formula probe above let prose
        # compositions ("amorphous UO2") spawn fragment rows on every run
        # while real measurements piled onto the sentinel.  Resolution is
        # now staged — see ``_resolve_existing_material`` — and each stage
        # hit is counted for observability.
        if m_key not in material_map:
            material_name = item.material_name
            formula = item.composition

            existing_mat, resolution_stage = await _resolve_existing_material(
                db, item
            )
            if existing_mat is not None:
                material_map[m_key] = existing_mat
                reused_entities += 1
                if resolution_stage is not None:
                    material_resolution_counts[resolution_stage] = (
                        material_resolution_counts.get(resolution_stage, 0) + 1
                    )
            else:
                material = Material(
                    name=material_name,
                    formula=formula,
                    crystal_structure=(
                        "amorphous"
                        if _detect_amorphous_phase(formula, material_name)
                        else None
                    ),
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
                (
                    await db.execute(
                        select(Dataset).where(
                            Dataset.material_id == material.id,
                            Dataset.source_id == source.id,
                        )
                    )
                )
                .scalars()
                .first()
            )
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
            # NFM-4013 / Path (a): record the drop with enough context for
            # the harness to bucket by (category_slug, raw_category,
            # property_name) and to fold sample values + source provenance
            # into the NFM-4008 classification table.
            category_slug = _normalize_category_slug(item.property_category)
            skipped_unknown_details.append(
                {
                    "category_slug": category_slug,
                    "raw_category": item.property_category,
                    "property_name": item.property,
                    "sample_value": item.value,
                    "source_doi": item.source_doi,
                    "source_file": item.source_file,
                    "material_name": item.material_name,
                }
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

        # NFM-3405 AC-2: resolve the extraction's unit string to a Unit FK
        # so the API surfaces a real symbol instead of the "—" placeholder.
        unit = await _resolve_unit(db, item.unit)

        # NFM-3405 AC-3: derive review_status from the per-property
        # confidence so the API's derived confidence varies per-property
        # instead of being a flat 0.70.
        review_status = _confidence_to_review_status(item.confidence)

        # NFM-2032 CR Finding #4: wrap the per-measurement INSERT in a
        # SAVEPOINT so a concurrent cross-request dedup race produces
        # IntegrityError without poisoning the outer transaction.
        try:
            async with db.begin_nested():
                measurement = PropertyMeasurement(
                    dataset_id=dataset.id,
                    property_type_id=property_type.id,
                    unit_id=unit.id if unit is not None else None,
                    uncertainty=item.uncertainty,
                    notes=item.context,
                    review_status=review_status,
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
        skipped_unknown_materials=skipped_unknown_materials,
        material_resolution_counts=material_resolution_counts,
        validation_errors=validation_error_count,
        skipped_unknown_details=skipped_unknown_details,
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


def _reject_uuid_title(title: str) -> None:
    """Raise ``ValueError`` when ``title`` is a 36-char UUID string.

    NFM-4088 AC-4 (write-path guard).

    The pre-fix DOI-empty branch (lines 709-717) silently inserted a
    row whose ``title`` was the primary-key UUID of another source.
    Migration 070 cleans the existing rows; this guard prevents the
    regression from re-emerging.  We refuse the INSERT rather than
    silently substitute because the only way the title can be a UUID
    is a logic bug in the upstream extraction chain — substituting
    a different label would mask that bug.
    """
    if _UUID_TITLE_PATTERN.match(title):
        raise ValueError(
            f"Refusing to create DataSource with UUID-pattern title={title!r}. "
            "The upstream extraction chain supplied a UUID instead of a "
            "literature reference; investigate the extractor before retrying."
        )


async def _find_source_by_title(
    db: AsyncSession,
    title: str,
) -> DataSource | None:
    """Find an existing DataSource by exact ``title`` equality.

    NFM-4088 AC-3 (write-path guard fallback 1).

    Returns at most one row; the existing ``data_sources`` table has
    no UNIQUE constraint on ``title`` (only ``doi``) so two rows may
    legitimately share a title in legacy states.  We use ``.first()``
    rather than ``scalar_one_or_none()`` to avoid raising
    ``MultipleResultsFound`` (mirrors the NFM-3919 dedup-by-formula
    pattern).
    """
    if not title:
        return None
    stmt = select(DataSource).where(DataSource.title == title).limit(1)
    return (await db.execute(stmt)).scalars().first()


async def _find_source_by_content_md_prefix(
    db: AsyncSession,
    source_file: str | None,
) -> DataSource | None:
    """Find an existing DataSource by ``source_file`` substring match.

    NFM-4088 AC-3 (write-path guard fallback 2).

    When ``source_file`` is a Markdown path the NFM-1486 PDF pipeline
    uploaded, the corresponding ``content_md`` column holds the parsed
    text and was uploaded under the same file.  We match by
    ``LIKE '%<basename>%'`` — exact equality is unreliable across
    absolute-vs-relative paths.

    Returns ``None`` when ``source_file`` is absent or no row matches.
    """
    if not source_file:
        return None
    basename = source_file.rstrip("/").split("/")[-1]
    if not basename or len(basename) < 4:
        return None
    stmt = (
        select(DataSource)
        .where(DataSource.content_md.is_not(None))
        .where(DataSource.content_md.like(f"%{basename}%"))
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _get_or_create_unattributed_sentinel(
    db: AsyncSession,
) -> tuple[DataSource, bool]:
    """Return the canonical ``Unattributed (no source provenance)`` row.

    NFM-4105 AC-1 (stop-the-bleed).

    Convergence guarantee: every concurrent extraction that has NO
    provenance at all (no DOI, no ``reference``, no ``source_file``,
    no ``file_hash``, no ``content_md``) reuses a single row instead
    of inserting a fresh placeholder row per call.

    Returns ``(source, created_now)`` where ``created_now`` is True iff
    this call inserted the sentinel row in the current transaction.
    Callers use the bool to update ``created_sources`` / ``reused_entities``
    counters accurately.

    Mechanism:
      1. Acquire ``pg_advisory_xact_lock`` (transaction-scoped).  Two
         concurrent mappers serialize on this lock so the
         SELECT-then-INSERT pair becomes atomic.
      2. ``SELECT … WHERE title = sentinel LIMIT 1``.  If a row exists,
         return it with ``created_now=False``.
      3. Otherwise INSERT one row with the canonical sentinel title and
         ``source_type='other'``.  Return it with ``created_now=True``.

    The advisory lock is released automatically at COMMIT/ROLLBACK,
    so a crashed worker cannot leave the lock held.

    Why not a UNIQUE constraint: ``data_sources`` has only
    ``uq_data_sources_doi`` (NFM-1486); adding a partial unique index
    on ``title`` would be a schema change outside the AC-1 scope.
    The advisory lock gives the same convergence guarantee without
    a migration.

    Legacy-placeholder reuse: if a row already exists with one of the
    legacy placeholder titles (``"Unattributed source (no DOI)"`` /
    ``"Unknown Source"``) the sentinel helper reuses it instead of
    creating a fresh row.  This keeps environments with legacy
    pollution on a single canonical row without waiting for the
    follow-up migration 070+ to quarantine the legacy rows.

    SQLite fallback: the test suite (and any SQLite-backed dev DB)
    does not implement ``pg_advisory_xact_lock``.  SQLite serializes
    writes at the connection level so the SELECT-then-INSERT race
    cannot occur — we skip the advisory lock entirely on SQLite and
    rely on the per-session visibility of the just-flushed row.
    """
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name != "sqlite":
        # 1. Serialize concurrent sentinel creation across sessions.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": _UNATTRIBUTED_SENTINEL_LOCK_KEY},
        )
    # 2. Look up an existing canonical row — prefer the new sentinel
    #    title, fall back to legacy placeholder titles so already-
    #    polluted environments converge on a single reused row.
    lookup_titles = (
        _UNATTRIBUTED_SENTINEL_TITLE,
        *_BORING_PLACEHOLDER_TITLES,
    )
    existing = (
        await db.execute(
            select(DataSource)
            .where(DataSource.title.in_(lookup_titles))
            .order_by(
                # New sentinel title ranks highest (1), legacy placeholders
                # second (0).  CASE returns an int SQL expression that
                # ``order_by(...).desc()`` can serialize.
                case(
                    (DataSource.title == _UNATTRIBUTED_SENTINEL_TITLE, 1),
                    else_=0,
                ).desc()
            )
            .limit(1)
        )
    ).scalars().first()
    if existing is not None:
        return existing, False
    # 3. Create the canonical sentinel row.
    source = DataSource(
        title=_UNATTRIBUTED_SENTINEL_TITLE,
        source_type="other",
    )
    db.add(source)
    await db.flush()
    logger.info(
        "Created Unattributed (no source provenance) sentinel DataSource "
        "(id=%s) — all subsequent DOI-empty / no-provenance extractions "
        "will reuse this row (NFM-4105 AC-1).",
        source.id,
    )
    return source, True


def _has_any_provenance(item: ExtractedProperty) -> bool:
    """True when the extracted item carries ANY identifying provenance.

    NFM-4105 AC-1 sub-classifier.

    Used to route the DOI-empty branch to one of two paths:
      * has provenance  → existing title / file_hash / content_md dedup
      * no provenance   → sentinel-row reuse (single canonical row)
    """
    return bool(
        item.source_doi
        or item.reference
        or item.source_file
    )


async def _find_material_by_formula(
    db: AsyncSession,
    formula: str | None,
) -> Material | None:
    """Find existing Material by formula.

    NFM-3919: tolerates duplicate ``formula`` rows that exist in the
    database from prior batches. ``scalar_one_or_none()`` would raise
    ``MultipleResultsFound`` and fail the entire ingest batch the moment
    a second row with the same formula was inserted (e.g. legacy
    ``Unknown Material`` pollution). We instead use ``.limit(1)`` plus
    ``scalars().first()`` so the lookup returns one row deterministically.
    """
    if not formula:
        return None
    stmt = select(Material).where(Material.formula == formula).limit(1)
    return (await db.execute(stmt)).scalars().first()


# NFM-4312 (BUG-32) — staged material resolution.
#
# Root cause of the empty material-property pages: the mapper resolved
# the extraction item's material via a single exact ``formula`` match on
# ``item.composition``.  The heuristic extractor passes prose phrases
# ("amorphous UO2", "UO2 (undoped and Cr-doped)") as both material_name
# and composition, so the exact match missed and every run spawned a
# fresh fragment material row (prod: 2x "amorphous UO2", 6x
# "Cr-doped UO2", formula strings like "U->15at%Mo") while the real
# measurements piled up on the "Unknown Material (canonical)" sentinel.
#
# The fix keeps association topology (measurement -> dataset.material_id)
# and widens *resolution* only, in conservative stages that never fold
# scientifically distinct phases together:
#
#   1. exact formula          — existing behaviour (fast path)
#   2. normalized formula     — case / whitespace / underscore / unicode
#                               subscript folding ("UO₂" == "uo2" == "UO2").
#                               Deliberately does NOT strip phase or doping
#                               qualifiers: "amorphous UO2" must never
#                               resolve onto the crystalline UO2 row.
#   3. material_aliases       — curated alias table (empty in prod today;
#                               gives curation a lever without code changes)
#   4. exact name             — re-runs emitting the same display name
#
# A new material is created only when every stage misses.  Each stage
# hit is counted in ``MappingResult`` so operators can watch where
# resolution lands.

#: Unicode subscript/superscript digits → ASCII, applied to the
#: *candidate* string only (the DB side stays as stored).
_SUBSCRIPT_FOLDS: dict[str, str] = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}

#: Phase qualifier detected on the CREATE path.  When the winning
#: composition/name carries it, the new material row records its phase
#: so fragment rows self-describe instead of arriving with a NULL
#: crystal_structure.
_AMORPHOUS_MARKERS: tuple[str, ...] = ("amorphous", "a-uo2", "amorph.")


def _normalize_formula_candidate(raw: str | None) -> str | None:
    """Fold a candidate formula for tolerant comparison.

    Casefolds, strips whitespace/underscores, and maps unicode
    subscript/superscript digits to ASCII.  Returns ``None`` for empty
    input.  This is intentionally *lossy* about typography only — never
    about chemistry (qualifiers are preserved verbatim).
    """
    if not raw:
        return None
    folded = raw.casefold()
    for sub, ascii_digit in _SUBSCRIPT_FOLDS.items():
        folded = folded.replace(sub, ascii_digit)
    compact = re.sub(r"[\s_]+", "", folded)
    return compact or None


async def _find_material_by_normalized_formula(
    db: AsyncSession,
    composition: str | None,
) -> Material | None:
    """Stage-2 lookup: typography-insensitive formula match.

    Compares the normalized candidate against SQL-side
    ``lower(replace(replace(formula, ' ', ''), '_', ''))``.  Handles the
    observed production variants ("UO2 " / "uo2" / "UO₂").  Strings that
    differ only by phase qualifiers ("amorphous UO2" vs "UO2") stay
    distinct on purpose.
    """
    normalized = _normalize_formula_candidate(composition)
    if not normalized:
        return None
    sql_side = func.lower(
        func.replace(
            func.replace(func.coalesce(Material.formula, ""), " ", ""),
            "_",
            "",
        )
    )
    stmt = (
        select(Material)
        .where(sql_side == normalized)
        .order_by(Material.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _find_material_by_alias(
    db: AsyncSession,
    item: ExtractedProperty,
) -> Material | None:
    """Stage-3 lookup: curated ``material_aliases`` rows.

    Tries the composition first, then the display name, so either field
    can carry the alias.  Deterministic (oldest alias row wins) to keep
    re-runs stable when a curator registers the same alias text twice
    under different types.
    """
    candidates = [
        c for c in (item.composition, item.material_name) if c
    ]
    for candidate in candidates:
        stmt = (
            select(Material)
            .join(MaterialAlias, MaterialAlias.material_id == Material.id)
            .where(MaterialAlias.alias_name == candidate)
            .order_by(MaterialAlias.created_at.asc())
            .limit(1)
        )
        hit = (await db.execute(stmt)).scalars().first()
        if hit is not None:
            return hit
    return None


async def _find_material_by_name(
    db: AsyncSession,
    name: str | None,
) -> Material | None:
    """Stage-4 lookup: exact display-name match (re-run stability)."""
    if not name:
        return None
    stmt = (
        select(Material)
        .where(Material.name == name)
        .order_by(Material.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _resolve_existing_material(
    db: AsyncSession,
    item: ExtractedProperty,
) -> tuple[Material | None, str | None]:
    """Run the staged resolution; return ``(material, stage)``.

    ``stage`` is ``None`` when nothing matched (caller creates).  The
    stages are ordered cheapest-and-safest first; the first hit wins so
    a curated alias can deliberately outrank a normalized-formula
    accident only by being reachable when earlier stages miss.
    """
    existing = await _find_material_by_formula(db, item.composition)
    if existing is not None:
        return existing, "formula"

    existing = await _find_material_by_normalized_formula(db, item.composition)
    if existing is not None:
        return existing, "normalized_formula"

    existing = await _find_material_by_alias(db, item)
    if existing is not None:
        return existing, "alias"

    existing = await _find_material_by_name(db, item.material_name)
    if existing is not None:
        return existing, "name"

    return None, None


def _detect_amorphous_phase(*strings: str | None) -> bool:
    """True when any identity string marks the material as amorphous."""
    joined = " ".join(s for s in strings if s).casefold()
    return any(marker in joined for marker in _AMORPHOUS_MARKERS)


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
