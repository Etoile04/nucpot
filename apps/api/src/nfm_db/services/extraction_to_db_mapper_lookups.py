"""Entity-resolution helpers for the Extraction-to-DB Mapper (NFM-4620 split).

Companion to :mod:`nfm_db.services.extraction_to_db_mapper`. Holds every
helper that resolves an :class:`ExtractedProperty` (or one of its
fields) to an existing persistent row, or creates the canonical
placeholder when nothing exists:

* Source resolution — DOI / title / content_md-prefix lookup plus the
  NFM-4105 ``Unattributed (no source provenance)`` sentinel helper
  (advisory-locked get-or-create).
* Material resolution — staged lookup (NFM-3919 / NFM-4312): exact
  formula, normalized formula, alias, exact name.
* Property classification — phase detection, float parsing, value
  kwargs builder for measurement persistence.

The body of :func:`extraction_to_db_mapper.map_and_persist` is the
sole caller of these helpers; module-level re-exports in the parent
module keep existing test imports working.

Split history: this module was carved out of
``extraction_to_db_mapper.py`` in NFM-4620 to bring the parent file
from 1761 lines to ≤1500 (the CI ``File size guard`` ceiling).
The split is a pure move/extract — no behavior change.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource, Material, MaterialAlias
from nfm_db.schemas.extraction import ExtractedProperty

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: NFM-4088 — guard against UUID-pattern ``title`` (root cause: prior
#: source's primary-key string was being copied into the new row's
#: ``title`` when extraction emitted a UUID instead of a reference).
#: Canonical 36-char UUID, case-insensitive, anchored on both ends.
_UUID_TITLE_PATTERN: re.Pattern[str] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _title_label_from_source_file(source_file: str | None) -> str | None:
    """Return *source_file* when it is a usable title label, else ``None``.

    NFM-4794 — a UUID-shaped ``source_file`` is a datasource primary key
    stamped in by an upstream back-fill (id-driven extraction passes
    ``str(ds.id)`` as the post-process reference), not a filename or
    citation. Promoting it would create a ``DataSource`` whose ``title``
    is a UUID — exactly what the NFM-4088 guard refuses. Return ``None``
    so callers' fallback chains proceed to the placeholder / sentinel.
    """
    if not source_file:
        return None
    if _UUID_TITLE_PATTERN.match(source_file):
        return None
    return source_file


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
#:
#: NFM-4620 (file-split): the source string is **byte-identical** to
#: the pre-split value, so the lock key does not change when this
#: module is imported. Pinning this is load-bearing — Postgres
#: ``pg_advisory_xact_lock`` keys are int32 (NFM-4105 contract), and
#: changing the source string would shift the key into the >2**31
#: range, breaking both the ``int32 < 2**31`` test guard and any
#: worker that already holds the old key in a long-lived connection.
_UNATTRIBUTED_SENTINEL_LOCK_KEY: int = int(
    hashlib.sha256(b"nfm_db.extraction_to_db_mapper.unattributed_sentinel").hexdigest()[:8],
    16,
)


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
#: crystal_structure.  The short "a-uo2" form is matched separately as
#: a standalone token so phase-prefixed identities ("alpha-UO2",
#: "beta-UO2", "Na-UO2") do not trip it.
_AMORPHOUS_MARKERS: tuple[str, ...] = ("amorphous", "amorph.")
_AMORPHOUS_TOKEN_PATTERN: re.Pattern[str] = re.compile(r"\ba-uo2\b")


# ---------------------------------------------------------------------------
# Source resolution
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

    NFM-4794: a UUID-shaped ``source_file`` is a datasource id, not
    provenance — an upstream back-fill made it always non-empty for
    id-driven extraction, which kept this classifier (and therefore the
    sentinel path) permanently False. Exclude it so genuinely
    unattributed items converge on the sentinel row.
    """
    return bool(
        item.source_doi
        or item.reference
        or _title_label_from_source_file(item.source_file)
    )


# ---------------------------------------------------------------------------
# Material resolution
# ---------------------------------------------------------------------------


async def _find_material_by_formula(
    db: AsyncSession,
    formula: str | None,
) -> Material | None:
    """Find an active Material by exact formula.

    NFM-3919: tolerates duplicate ``formula`` rows that exist in the
    database from prior batches. ``scalar_one_or_none()`` would raise
    ``MultipleResultsFound`` and fail the entire ingest batch the moment
    a second row with the same formula was inserted (e.g. legacy
    ``Unknown Material`` pollution). We instead use ``.limit(1)`` plus
    ``scalars().first()`` so the lookup returns one row deterministically
    (oldest-first via ``ORDER BY created_at``).

    NFM-4312 CR-R2: only ``is_active`` rows are eligible.  A retired
    duplicate keeps its formula as audit residue after a merge; letting
    stage-1 hit it would re-attach new datasets to the hidden fragment.
    """
    if not formula:
        return None
    stmt = (
        select(Material)
        .where(Material.formula == formula)
        .where(Material.is_active.is_(True))
        .order_by(Material.created_at.asc())
        .limit(1)
    )
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
#
# CR-R2 invariant: retired rows (``is_active=False``) are invisible to
# every stage.  A retired duplicate is "merged audit residue" — its
# datasets have been moved onto the canonical row and its (source,
# material) slot is empty, so nothing downstream would catch a re-attach.
# New data always lands on an active (canonical) row, or creates a fresh
# active one.


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
    distinct on purpose.  Active rows only (CR-R2) — a retired fragment
    must never absorb new data through a typography fold.
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
        .where(Material.is_active.is_(True))
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
    under different types.  Active rows only (CR-R2) — a stale alias
    left pointing at a merged-away fragment must not resurrect it.
    """
    candidates = [
        c for c in (item.composition, item.material_name) if c
    ]
    for candidate in candidates:
        stmt = (
            select(Material)
            .join(MaterialAlias, MaterialAlias.material_id == Material.id)
            .where(MaterialAlias.alias_name == candidate)
            .where(Material.is_active.is_(True))
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
    """Stage-4 lookup: exact display-name match (re-run stability).

    Active rows only (CR-R2) — the name stage must not short-circuit
    resolution onto a retired fragment that happens to keep the only
    exact display name.
    """
    if not name:
        return None
    stmt = (
        select(Material)
        .where(Material.name == name)
        .where(Material.is_active.is_(True))
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


# ---------------------------------------------------------------------------
# Phase + value helpers (used by map_and_persist; sit here for cohesion
# with the material-resolution helpers that classify the candidate)
# ---------------------------------------------------------------------------


def _detect_amorphous_phase(*strings: str | None) -> bool:
    """True when any identity string marks the material as amorphous."""
    joined = " ".join(s for s in strings if s).casefold()
    if any(marker in joined for marker in _AMORPHOUS_MARKERS):
        return True
    return _AMORPHOUS_TOKEN_PATTERN.search(joined) is not None


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
