"""Skill-output → property_measurements row adapter (NFM-4547).

Implements G1 spec §3.2 (skill output 13 fields → row-level 20 fields) and
§2.6 (段落级溯源 ``source_span`` 注入 + 过渡期启发式降级).

The adapter runs between the LLM JSON response and the downstream DB
mapper (``extraction_to_db_mapper.map_and_persist``). It:

* Re-keys the 13 skill-output fields onto the canonical ``ExtractedProperty``
  schema (NFM-1979 AC-4) — only the 7 canonical ``property_category``
  literals are emitted by upstream skills, so the category crosswalk is
  bounded.
* Computes ``source_span`` heuristically when upstream does not emit it
  (which is the case today for ``nuclear-property-extraction-v4``). The
  heuristic uses a char-window scan around the property name/value
  keyword with optional file/page context. When the upstream skill
  begins emitting ``source_span`` natively the heuristic is bypassed.
* Synthesises server-side fields: ``dedupe_key``, ``dataset_id``,
  ``dataset_version_id``, ``extraction_skill_version``. The mapper fills
  in the FK columns from those; the adapter does not own DB writes.

The adapter is pure (no DB access) — that keeps the recall regression
test fast and deterministic and lets the integration owner slot it into
``ontofuel_extract`` without a session. Service callers pass an
``AdapterContext`` carrying the resolved dataset / source references.

Public API:
    adapt_skill_output(records, ctx) -> list[AdaptedPropertyMeasurement]
    AdapterContext
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


__all__ = [
    "AdaptedPropertyMeasurement",
    "AdapterContext",
    "adapt_skill_output",
    "compute_dedupe_key",
    "derive_source_span",
    "parse_value_to_numeric",
]


# ---------------------------------------------------------------------------
# Constants — bounded category crosswalk
# ---------------------------------------------------------------------------


#: OntoFuel / skill output ``property_category`` literal → DB
#: ``property_categories.slug``. Mirrors ``ONTOFUEL_CATEGORY_TO_SLUG`` in
#: ``extraction_to_db_mapper`` so the adapter does not need DB lookups.
SKILL_CATEGORY_TO_SLUG: dict[str, str] = {
    "mechanical": "mechanical",
    "thermal": "thermal",
    "physical": "physical",
    "nuclear": "nuclear",
    # fallbacks
    "diffusion": "physical",
    "irradiation": "nuclear",
    "corrosion": "physical",     # out-of-catalog → fallback per ADR-016 §2.3
    "other": "thermal",          # least-bad default
}


#: Confident literal → numeric probability mapping for the
#: ``confidence`` column on ``property_measurements`` (§5 of the G1
#: spec; <0.7 → review_status='pending').
_CONFIDENT_LITERAL_TO_PROB: dict[str, float] = {
    "high": 0.95,
    "medium": 0.75,
    "low": 0.5,
}


#: High-frequency condition keys that the spec (§2.4) pulls out of the
#: JSONB into fixed columns. The remaining keys stay inside
#: ``conditions`` JSONB for forward compatibility.
_HIGH_FREQ_CONDITION_KEYS: frozenset[str] = frozenset(
    {"simulation_method", "model_name", "temp_K", "pressure_GPa", "method"}
)


#: Char-window half-width used for the ``source_span`` heuristic when the
#: upstream skill does not emit ``source_span``. ADR-016 §2.6 calls for
#: "char window + keyword anchor" until the upstream version adds the
# structured field. 240 chars ≈ a short paragraph.
_SOURCE_SPAN_HALF_WINDOW: int = 240


# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterContext:
    """Per-call context the adapter needs.

    Carries the dataset / source FKs that the ingestion layer has
    resolved, plus the pin / version strings stamped onto every
    adapted row. The adapter does NOT hit the DB.
    """

    dataset_id: str
    dataset_version_id: str
    source_id: str
    extraction_skill_version: str
    # File content for the ``source_span`` heuristic. ``None`` disables
    # span inference (rows still get ``source_span=None``, which the
    # downstream mapper persists as JSON null).
    file_content: str | None = None
    # Optional page index hint from the ingestion layer; flows into
    # ``source_span.page`` when present.
    page_number: int | None = None


@dataclass(frozen=True)
class AdaptedPropertyMeasurement:
    """One adapted candidate row, ready for the DB mapper.

    Field names mirror the ``ExtractedProperty`` schema so the mapper
    can validate against the same Pydantic model. The 7 newly-added
    fields (source_span, dedupe_key, dataset_id, dataset_version_id,
    extraction_skill_version, value_numeric, value_expression) live
    alongside the 13 the skill emitted.
    """

    # Original 13 — straight from skill output
    source_file: str | None
    material_name: str | None
    composition: str | None
    phase: str | None
    element: str | None
    property_category: str | None
    property: str
    value: str
    unit: str | None
    conditions: dict[str, Any] | None
    context: str | None
    confidence: float       # 0.0-1.0 -- normalised from literal
    reference: str | None
    # Newly synthesised
    source_span: dict[str, Any] | None
    dedupe_key: str
    dataset_id: str
    dataset_version_id: str
    extraction_skill_version: str
    value_numeric: float | None
    value_text: str | None
    value_expression: str | None
    review_status: str      # 6-state enum per G1 spec §3.4
    # Projection of high-frequency condition keys (sim_method, temp_K,
    # ...) onto the fixed MeasurementCondition columns (§2.4). Stored
    # alongside the JSONB tail for the mapper's convenience.
    fixed_condition_columns: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_NUMERIC_PARTS_RE = re.compile(
    r"""
    [-+]?                      # optional sign
    (?:
        \d+\.\d+               # decimal
      | \d+                    # integer
    )
    (?:[eE][-+]?\d+)?          # optional exponent
    """,
    re.VERBOSE,
)


def parse_value_to_numeric(value: Any) -> tuple[float | None, str | None]:
    """Route ``value`` to ``value_numeric`` or ``value_text``.

    Per G1 spec §3.1, exactly one of ``value_numeric`` /
    ``value_text`` / ``value_expression`` must be non-null. The
    adapter chooses by type:

    * ``value`` parses as a finite number → ``value_numeric``, value
      preserved as ``value_text`` for provenance.
    * otherwise → ``value_text`` only, ``value_numeric`` = ``None``.

    Returns ``(numeric, text)``.
    """
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None

    matches = _NUMERIC_PARTS_RE.findall(text)
    if len(matches) == 1 and matches[0] == text:
        try:
            return float(text), text
        except ValueError:
            return None, text
    # Compound ("3 to 4") or non-numeric ("high") → text only.
    return None, text


def compute_dedupe_key(
    *,
    dataset_id: str,
    property_type_id: str,
    source_id: str,
    value_hash: str,
) -> str:
    """Compose the row-level dedupe key (G1 spec §3.1).

    Format: ``<dataset_id>|<property_type_id>|<source_id>|<value_hash>``.
    The composite key plus the unique index on
    ``property_measurements(dedupe_key)`` gives upsert behaviour
    without an extra DB roundtrip.
    """
    return f"{dataset_id}|{property_type_id}|{source_id}|{value_hash}"


def _stable_value_hash(value_numeric: float | None, value_text: str | None) -> str:
    """SHA1 hex digest of a stable stringification of the value.

    Used as the last component of ``dedupe_key``. We digest on the
    textual form so ``"3.0"`` and ``"3"`` hash distinctly (skill
    outputs frequently emit strings like ``"3"`` and ``"3.0"`` for
    the same measurement).
    """
    payload = f"n={value_numeric!r}|t={value_text!r}".encode()
    return hashlib.sha1(payload).hexdigest()


# Stable per-record UUID-ish surrogate used in lieu of a real
# ``property_type_id`` lookup inside this pure adapter. The downstream
# mapper resolves the surrogate to a real PK; the dedupe key uses the
# surrogate so the adapter is hermetic.
_PROPERTY_NAME_TO_SURROGATE_ID: dict[str, str] = {}


def _property_type_id_surrogate(property_name: str) -> str:
    """Hash-based surrogate for ``property_type_id``.

    Stable across runs (so the dedupe key is reproducible) but not a
    real DB PK — the mapper swaps it for a real lookup or
    "create-on-miss" path under the out-of-catalog escape hatch
    (§2.3 of ADR-016).
    """
    if property_name not in _PROPERTY_NAME_TO_SURROGATE_ID:
        digest = hashlib.sha1(property_name.encode("utf-8")).hexdigest()
        _PROPERTY_NAME_TO_SURROGATE_ID[property_name] = digest[:32]
    return _PROPERTY_NAME_TO_SURROGATE_ID[property_name]


def derive_source_span(
    *,
    skill_record: Mapping[str, Any],
    file_content: str | None,
    page_number: int | None,
) -> dict[str, Any] | None:
    """Infer ``source_span`` for a skill record (ADR-016 §2.6 fallback).

    The upstream ``nuclear-property-extraction-v4`` does not emit
    ``source_span`` today, so the adapter runs a char-window + keyword
    anchor search over ``file_content``. Once the upstream version
    that emits ``source_span`` natively is pinned the caller passes
    the pre-computed span as ``skill_record["source_span"]`` and this
    function is bypassed.

    Returns ``None`` when no content is provided — the downstream
    mapper persists JSON null and the recall regression test still
    passes (AC-1 is on numeric recall, not on span coverage).
    """
    explicit = skill_record.get("source_span")
    if explicit:
        return dict(explicit)

    if not file_content:
        return None

    property_name = str(skill_record.get("property") or "").strip()
    value_text = str(skill_record.get("value") or "").strip()

    # Keyword anchor: prefer property name; fall back to value text.
    anchor = property_name or value_text
    if not anchor:
        return None

    # Locate the first occurrence of the anchor (case-insensitive, but
    # the exact value substring takes priority). For short anchors
    # (<4 chars) we require a word-boundary match to avoid false hits.
    needle = anchor.lower()
    if len(needle) < 4:
        pattern = re.compile(r"\b" + re.escape(needle) + r"\b", re.IGNORECASE)
        match = pattern.search(file_content)
    else:
        match = re.search(re.escape(needle), file_content, re.IGNORECASE)

    if match is None:
        return None

    centre = match.start()
    start = max(0, centre - _SOURCE_SPAN_HALF_WINDOW)
    end = min(len(file_content), centre + len(anchor) + _SOURCE_SPAN_HALF_WINDOW)
    snippet = file_content[start:end]
    snippet_hash = hashlib.sha1(snippet.encode("utf-8")).hexdigest()

    return {
        "file": skill_record.get("source_file"),
        "page": page_number,
        "char_start": start,
        "char_end": end,
        "snippet_hash": snippet_hash,
    }


def _split_conditions(
    conditions: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split *conditions* into (high_freq_columns, leftover_jsonb).

    Per §2.4 of ADR-016, the high-frequency keys are projected into
    fixed columns for indexability; the rest stay in the JSONB.
    """
    if not conditions:
        return {}, {}
    fixed = {k: v for k, v in conditions.items() if k in _HIGH_FREQ_CONDITION_KEYS}
    leftover = {k: v for k, v in conditions.items() if k not in _HIGH_FREQ_CONDITION_KEYS}
    return fixed, leftover


def _normalise_review_status(
    *,
    confidence: float,
    skill_record: Mapping[str, Any],
) -> str:
    """Map (confidence + out_of_catalog flag) → ``review_status``.

    Implements G1 spec §3.4 + §5:

    * ``confidence < 0.7`` → ``"pending"`` (auto-flag).
    * Out-of-catalog flag in ``skill_record["out_of_catalog"]`` →
      ``"pending"`` per §2.3 of ADR-016.
    * Otherwise → ``"pending"`` is also the default initial state;
      domain_expert actions move rows to confirmed/invalid/etc.
    """
    if confidence < 0.7:
        return "pending"
    if bool(skill_record.get("out_of_catalog")):
        return "pending"
    return "pending"


def adapt_skill_record(
    skill_record: Mapping[str, Any],
    ctx: AdapterContext,
) -> AdaptedPropertyMeasurement:
    """Adapt a single skill-output record into a candidate row.

    See module docstring for the 13→20 mapping rationale.
    """
    property_category_raw = skill_record.get("property_category")
    property_category_slug = (
        SKILL_CATEGORY_TO_SLUG.get(str(property_category_raw))
        if property_category_raw is not None
        else None
    )

    value_numeric, value_text = parse_value_to_numeric(skill_record.get("value"))
    confidence_literal = str(skill_record.get("confidence") or "medium").lower()
    confidence = _CONFIDENT_LITERAL_TO_PROB.get(confidence_literal, 0.7)

    source_span = derive_source_span(
        skill_record=skill_record,
        file_content=ctx.file_content,
        page_number=ctx.page_number,
    )

    fixed_conditions, jsonb_conditions = _split_conditions(
        skill_record.get("conditions") or {}
    )
    # phase → conditions.phase (§2.5 of ADR-016).
    phase = skill_record.get("phase")
    if phase is not None:
        jsonb_conditions = {**jsonb_conditions, "phase": phase}

    # value_expression: when the value parses to a math expression
    # (contains an operator other than a digit) we keep it as a
    # expression rather than coercing to a number.
    value_expression: str | None = None
    raw_value = skill_record.get("value")
    if isinstance(raw_value, str) and value_numeric is None and value_text is not None:
        if any(op in raw_value for op in ("^", "exp", "sqrt", "log", "**")):
            value_expression = value_text
            value_text = None  # expressions live alone

    property_name = str(skill_record.get("property") or "").strip()
    if not property_name:
        # Adapter never produces empty property names — would break
        # the DB mapper. Surface as a structured warning instead of
        # silently coercing.
        logger.warning(
            "adapt_skill_record: skill record missing 'property' field — "
            "marking review_status='invalid' for downstream triage "
            "(material=%s)",
            skill_record.get("material_name"),
        )
        property_name = "(unknown)"
        confidence = 0.0

    property_type_id = _property_type_id_surrogate(property_name)
    value_hash = _stable_value_hash(value_numeric, value_text)
    dedupe_key = compute_dedupe_key(
        dataset_id=ctx.dataset_id,
        property_type_id=property_type_id,
        source_id=ctx.source_id,
        value_hash=value_hash,
    )

    review_status = _normalise_review_status(
        confidence=confidence,
        skill_record=skill_record,
    )
    if property_name == "(unknown)":
        review_status = "invalid"

    return AdaptedPropertyMeasurement(
        source_file=skill_record.get("source_file"),
        material_name=skill_record.get("material_name"),
        composition=skill_record.get("composition"),
        phase=phase,
        element=skill_record.get("element"),
        property_category=property_category_slug,
        property=property_name,
        value=str(raw_value) if raw_value is not None else "",
        unit=skill_record.get("unit"),
        conditions=jsonb_conditions or None,
        context=skill_record.get("context"),
        confidence=confidence,
        reference=skill_record.get("reference"),
        source_span=source_span,
        dedupe_key=dedupe_key,
        dataset_id=ctx.dataset_id,
        dataset_version_id=ctx.dataset_version_id,
        extraction_skill_version=ctx.extraction_skill_version,
        value_numeric=value_numeric,
        value_text=value_text,
        value_expression=value_expression,
        review_status=review_status,
        # Stash fixed-column projection alongside conditions for the
        # mapper. Not part of the canonical row but useful in audit
        # logs.
        fixed_condition_columns=fixed_conditions,
    )


def adapt_skill_output(
    records: Iterable[Mapping[str, Any]],
    ctx: AdapterContext,
) -> list[AdaptedPropertyMeasurement]:
    """Adapt an iterable of skill-output records."""
    adapted: list[AdaptedPropertyMeasurement] = []
    for record in records:
        if not isinstance(record, Mapping):
            logger.warning(
                "adapt_skill_output: dropping non-mapping record %r",
                record,
            )
            continue
        adapted.append(adapt_skill_record(record, ctx))
    return adapted
