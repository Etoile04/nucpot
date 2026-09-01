"""KG → staging bridge for extracted literature (NFM-3478 Layer B).

Real extraction produces ``kg_nodes``/``kg_edges`` via
``GraphBuilder.build_from_extraction``, but the ontology viewer reads
``_ref_gap_fill_staging`` (NFM-227 contract).  Before this bridge those
were two disjoint graphs: extracted papers were invisible to the viewer
even though the pipeline itself worked (Layer B gap #2 of NFM-3478).

The bridge maps Property nodes (with their Material + Condition context)
into staging rows so a freshly extracted paper appears in the viewer as
its own corpus.  It is deliberately conservative:

* only Property nodes with a numeric ``value`` are bridged;
* unit aliases are normalized (e.g. ``Å`` → ``angstrom``) so range
  validation and downstream property maps keep working;
* Chinese labels are slugified to stable English property names
  (viewer contract requires ``[A-Za-z0-9._-]`` corpus ids and the
  property names feed ``_KIND_COLORS``-style lookups);
* dedup uses the SAME ``compute_dedup_hash`` 5-field key as the v4
  pipeline, so re-running the bridge (or re-processing the same paper)
  is idempotent rather than duplicating rows;
* rows land with ``status='pending'`` — human review stays mandatory
  (NFM-54 review workflow), the bridge never auto-approves.

Element-system normalization: a Material node label like ``U₂Mo`` becomes
``U-Mo`` (hyphenated element system, consistent with demo rows);
``U``/``UO2`` pass through.  Conditions attached to a property via
``relatedTo`` edges are parsed into temperature/method columns where the
condition key is recognised (``temp_C`` → temperature in K,
``simulation_method`` → method); unrecognised conditions are appended to
``context`` so no information is silently dropped.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.kg import KGEdge, KGNode
from nfm_db.models.property import Dataset, PropertyMeasurement
from nfm_db.models.ref_gap_fill import Confidence, RefGapFillStaging, StagingStatus
from nfm_db.services.quality_gate import compute_dedup_hash

logger = logging.getLogger(__name__)

# --- label → canonical property slug -------------------------------------
# Chinese property labels are what qwen emits for materials-science papers.
# Map the ones the extractor produces to the slugs already used by demo
# rows / property maps; anything unmapped falls through to a pinyin-less
# ASCII slug so it still round-trips (unknown ≠ dropped).
_PROPERTY_SLUGS: dict[str, str] = {
    "体积模量": "bulk_modulus",
    "晶格参数a": "lattice_constant_a",
    "晶格参数b": "lattice_constant_b",
    "晶格参数c": "lattice_constant_c",
    "形成能": "formation_energy",
    "混合焓": "mixing_enthalpy",
    "相分数": "composition_range",
    "相变温度": "phase_transition_temperature",
    "相变体积变化": "phase_transition_volume_change",
    "相变潜热": "phase_transition_latent_heat",
    "相变类型": "phase_transition_type",
    "相平衡线斜率": "phase_boundary_slope",
    "相稳定温度下限": "phase_stability_lower_temp",
    "熔点": "melting_point",
    "Clausius-Clapeyron斜率": "phase_boundary_slope",
    "弹性常数": "elastic_constants",
}

_UNIT_ALIASES: dict[str, str] = {
    "Å": "angstrom",
    "Å³": "angstrom3",
    "A": "angstrom",
    "GPa": "GPa",
    "K/GPa": "K/GPa",
    "eV": "eV",
    "eV/atom": "eV/atom",
    "K": "K",
}

# Element-system canonicalization: unicode subscripts → plain formula.
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")

_CONDITION_TEMP_RE = re.compile(r"^(temp_C|temp_K|temperature)$")
_CONDITION_PRESSURE_RE = re.compile(r"^(pressure_MPa|pressure_GPa|pressure)$")
_CELSIUS_TO_K = 273.15


def _canonical_element_system(label: str) -> str:
    """``U₂Mo`` → ``U-Mo``; ``UO2``/``U3Si2`` pass through as formulas."""
    # Subscript digits (₂) are alloy notation, not stoichiometry: hyphenate.
    # ASCII digits mean a real formula (UO2, U3Si2) — keep intact.
    has_subscript = any(ch in "₀₁₂₃₄₅₆₇₈₉" for ch in label)
    plain = label.translate(_SUBSCRIPTS)
    if has_subscript:
        parts = re.findall(r"[A-Z][a-z]?", plain)
        if len(parts) > 1:
            return "-".join(parts)
        return plain
    return plain


def _material_label(material: Any) -> str:
    """ADR-010 D5: derive a canonicalization-friendly label for a Material.

    The PropertyMeasurement path doesn't have a KGNode label to feed
    ``_canonical_element_system`` directly, so we lift a label from the
    Material's ``formula`` (preferred — already canonical chemistry) or fall
    back to ``name``.  No silent drop: every Material becomes a label.
    """
    formula = getattr(material, "formula", None)
    if isinstance(formula, str) and formula.strip():
        return formula
    name = getattr(material, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    return "unknown"


def _slugify(text: str) -> str:
    """ASCII slug safe for CORPUS_ID_RE / property-name lookups."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")
    return text or "unknown"


# Reason "unknown" is the documented fallback, not empty string.


def _parse_numeric(raw: Any) -> tuple[float, float | None] | None:
    """Extract (value, uncertainty) from LLM strings like ``110±10``."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw), None
    s = str(raw).strip()
    m = re.match(r"^-?\d+(?:\.\d+)?(?:[±\s]+-?\d+(?:\.\d+)?)?$", s)
    if not m:
        # try to salvage a leading number from prose
        m2 = re.match(r"^(-?\d+(?:\.\d+)?)(?:[±\s]+(\d+(?:\.\d+)?))?", s)
        if not m2:
            nonlocal_match = re.match(r"(-?\d+(?:\.\d+)?)", s)
            if nonlocal_match:
                return float(nonlocal_match.group(1)), None
            return None
        s = m2.group(0)
    parts = re.split(r"[±\s]+", s)
    try:
        value = float(parts[0])
        unc = float(parts[1]) if len(parts) > 1 else None
        return value, unc
    except ValueError:
        return None


def _confidence_from_kg(score: float) -> Confidence:
    if score >= 0.8:
        return Confidence.HIGH
    if score >= 0.5:
        return Confidence.MEDIUM
    return Confidence.LOW


# ADR-010 D4: surface the same 3-level Confidence for the property_measurements
# loop, derived from the row's review_status (a reviewer-set enum, not a
# numeric score).  Unknown / NULL values fall back to MEDIUM so the bridge
# never drops a measurement on a reviewer-typing edge case.
_PM_CONFIDENCE_BY_STATUS: dict[str, Confidence] = {
    "approved": Confidence.HIGH,
    "pending": Confidence.MEDIUM,
    "rejected": Confidence.LOW,
}


def confidence_from_property_measurement(review_status: str | None) -> Confidence:
    """ADR-010 D4: map ``PropertyMeasurement.review_status`` → Confidence.

    Approved reviews are HIGH, rejected are LOW, everything else (pending,
    NULL, unknown, needs_revision) falls back to MEDIUM.
    """
    if review_status is None:
        return Confidence.MEDIUM
    return _PM_CONFIDENCE_BY_STATUS.get(review_status, Confidence.MEDIUM)


_CONFIDENCE_RANK = {
    Confidence.HIGH: 3,
    Confidence.MEDIUM: 2,
    Confidence.LOW: 1,
}


async def bridge_kg_to_staging(
    db: AsyncSession,
    source_id: Any,
    corpus_id: str,
    source_doi: str | None = None,
) -> int:
    """Map Property nodes of one extraction source into staging rows.

    Returns the number of staging rows written (0 when the source has no
    numeric properties or everything dedupes away — both are success).
    Must be called inside the caller's transaction; commits are left to
    the caller so the bridge composes with process_literature's Step 6.
    """
    # ``compute_dedup_hash`` expects str fields; the source_id may arrive
    # as UUID from callers, normalise before passing it on.
    source_id = str(source_id) if source_id is not None else None
    # KG schema declares source_id as uuid.UUID; compare with a UUID object
    # so we work across dialects (sqlite ignores type, pg enforces).
    from uuid import UUID as _UUID

    source_uuid = _UUID(source_id) if source_id is not None else None
    nodes = (
        (await db.execute(select(KGNode).where(KGNode.source_id == source_uuid))).scalars().all()
    )
    edges = (
        (await db.execute(select(KGEdge).where(KGEdge.source_id == source_uuid))).scalars().all()
    )
    by_id = {n.id: n for n in nodes}

    # Material ↔ Property adjacency via hasProperty edges.
    mat_props: dict[Any, list[KGNode]] = {}
    prop_conditions: dict[Any, list[KGNode]] = {}
    for e in edges:
        src, tgt = by_id.get(e.source_node_id), by_id.get(e.target_node_id)
        if src is None or tgt is None:
            continue
        if e.relation_type == "hasProperty":
            if src.node_type == "Material" and tgt.node_type == "Property":
                mat_props.setdefault(src.id, []).append(tgt)
        elif e.relation_type in ("relatedTo", "hasCondition"):
            # conditions hang off properties (either direction).  B2' emits
            # scoped hasCondition edges; legacy graphs only had relatedTo.
            if src.node_type == "Property" and tgt.node_type == "Condition":
                prop_conditions.setdefault(src.id, []).append(tgt)
            elif src.node_type == "Condition" and tgt.node_type == "Property":
                prop_conditions.setdefault(tgt.id, []).append(src)

    existing = set(
        (
            await db.execute(
                select(RefGapFillStaging.dedup_hash).where(RefGapFillStaging.source == corpus_id)
            )
        )
        .scalars()
        .all()
    )

    # NFM-3478 B2': re-running the bridge for a source regenerates that
    # source's rows.  compute_dedup_hash includes the method field, so a
    # rerun that fills in temperature/method (after the scoped hasCondition
    # edges land) would produce different hashes and duplicate rows under
    # the old skip-if-hash-exists logic.  Delete this source's rows first
    # so the corpus stays unique per (element_system, property) measurement.
    from sqlalchemy import delete as _delete

    await db.execute(_delete(RefGapFillStaging).where(RefGapFillStaging.source == corpus_id))

    # The pre-delete scan fed the old skip-if-exists logic; after the
    # delete those hashes are gone from the table. Keep only the
    # within-run guard (same run writing the same 5-field key twice).
    existing.clear()

    written = 0
    # NFM-3478 B2'+: alias labels (e.g. 相平衡线斜率 and Clausius-Clapeyron斜率)
    # canonicalize to the same slug with the same value.  Collapse them into
    # one staging row per (material, slug, value): first node wins the row,
    # later aliases merge their conditions (method prefers non-empty, context
    # parts union).
    merged_rows: dict[tuple[str, str, float | str], dict[str, Any]] = {}
    for mat_id, props in mat_props.items():
        mat = by_id[mat_id]
        element_system = _canonical_element_system(mat.label)
        for prop in props:
            parsed = _parse_numeric(prop.properties.get("value"))
            if parsed is None:
                continue
            value, unc = parsed
            unit = _UNIT_ALIASES.get(
                str(prop.properties.get("unit", "")), _slugify(str(prop.properties.get("unit", "")))
            )
            property_name = _PROPERTY_SLUGS.get(prop.label, _slugify(prop.label))
            if not property_name or property_name == "unknown":
                # Unmapped non-ASCII label with no ASCII fallback: skip
                # rather than writing a meaningless "unknown" row.
                logger.warning(
                    "bridge_kg_to_staging: skipping Property '%s' (no slug mapping)",
                    prop.label,
                )
                continue

            temperature: float | None = None
            method: str | None = None
            context_parts: list[str] = []
            for cond in prop_conditions.get(prop.id, []):
                key = str(cond.properties.get("condition_key", ""))
                val = str(cond.properties.get("condition_value", ""))
                if _CONDITION_TEMP_RE.match(key):
                    try:
                        t = float(val)
                        temperature = t + _CELSIUS_TO_K if key == "temp_C" else t
                    except ValueError:
                        context_parts.append(f"{key}={val}")
                elif _CONDITION_PRESSURE_RE.match(key):
                    context_parts.append(f"{key}={val}")
                elif key == "simulation_method":
                    method = val
                else:
                    context_parts.append(f"{key}={val}")

            dedup_key = (element_system, property_name, value)
            if dedup_key in merged_rows:
                row = merged_rows[dedup_key]
                if row["method"] is None and method:
                    row["method"] = method
                if row["temperature"] is None and temperature is not None:
                    row["temperature"] = temperature
                for part in context_parts:
                    if part not in row["context_parts"]:
                        row["context_parts"].append(part)
                continue
            merged_rows[dedup_key] = {
                "element_system": element_system,
                "property_name": property_name,
                "value": value,
                "unit": unit,
                "method": method,
                "uncertainty": unc,
                "temperature": temperature,
                "confidence": _confidence_from_kg(prop.confidence),
                "context_parts": list(context_parts),
                "source_surface": "kg_nodes",
                "kg_node_uuid": str(prop.id) if prop.id is not None else None,
                "property_measurement_uuid": None,
            }

    # ADR-010 D1: second loop — PropertyMeasurement rows for the same
    # ``source_uuid``, joined through Dataset → DataSource → Material and
    # Dataset → PropertyType → Unit.  Shared ``merged_rows`` dict so the two
    # surfaces collapse on the same dedup key (D6: both loops live in the
    # same function).  See ADR-010 §3 for why the union closes Layer B gap.
    from sqlalchemy.orm import selectinload

    property_measurements = (
        (
            await db.execute(
                select(PropertyMeasurement)
                .join(PropertyMeasurement.dataset)
                .where(PropertyMeasurement.dataset.has(source_id=source_uuid))
                .options(
                    # NFM-4038: the loop below reads
                    # ``pm.dataset.material``; chained selectinload
                    # fetches the Material row in a second IN-query
                    # rather than triggering an implicit lazy load
                    # inside the async greenlet (asyncpg raises
                    # ``MissingGreenlet`` on the sync IO).  The outer
                    # ``pm.property_type.name`` / ``pm.unit.symbol``
                    # accesses are scalar columns on already-loaded
                    # rows and do not need eager loading.
                    selectinload(PropertyMeasurement.dataset).selectinload(Dataset.material),
                    selectinload(PropertyMeasurement.property_type),
                    selectinload(PropertyMeasurement.unit),
                )
            )
        )
        .scalars()
        .all()
    )

    for pm in property_measurements:
        material = pm.dataset.material if pm.dataset is not None else None
        if material is None:
            # Dataset → Material is NOT NULL; defensive guard for legacy rows.
            continue
        element_system = _canonical_element_system(_material_label(material))

        property_name = _PROPERTY_SLUGS.get(pm.property_type.name, _slugify(pm.property_type.name))
        if not property_name or property_name == "unknown":
            logger.warning(
                "bridge_kg_to_staging: skipping PropertyMeasurement '%s' (no slug)",
                pm.property_type.name,
            )
            continue

        unit_symbol = pm.unit.symbol if pm.unit is not None else ""
        unit = _UNIT_ALIASES.get(unit_symbol, _slugify(unit_symbol))

        # ADR-010 D3: value-class routing.  Only ``value_scalar`` produces a
        # numeric staging row; ``value_expression``/``value_text`` are
        # appended to context so the information isn't silently dropped.
        if pm.value_scalar is None:
            for text_field in (pm.value_expression, pm.value_text):
                if text_field:
                    # Surface PM text-only via the first matching row's
                    # context (creating a sentinel entry if no scalar row
                    # exists yet so the text isn't lost).  Then continue
                    # — V3: no numeric row.
                    text_key = (element_system, property_name, "__pm_text__")
                    text_row = merged_rows.get(text_key)
                    if text_row is None:
                        merged_rows[text_key] = {
                            "element_system": element_system,
                            "property_name": property_name,
                            "value": None,
                            "unit": unit,
                            "method": pm.method or None,
                            "uncertainty": None,
                            "temperature": None,
                            "confidence": confidence_from_property_measurement(pm.review_status),
                            "context_parts": [f"value_expression={text_field}"],
                            "source_surface": "property_measurements",
                            "kg_node_uuid": None,
                            "property_measurement_uuid": str(pm.id) if pm.id is not None else None,
                        }
                    else:
                        text_row["context_parts"].append(f"value_expression={text_field}")
            logger.info(
                "bridge.pm.expression_only",
                extra={
                    "event": "bridge.pm.expression_only",
                    "element_system": element_system,
                    "property_name": property_name,
                    "property_measurement_uuid": str(pm.id) if pm.id is not None else None,
                    "review_status": pm.review_status,
                    "value_expression": pm.value_expression,
                    "value_text": pm.value_text,
                },
            )
            continue

        value = float(pm.value_scalar)
        uncertainty = float(pm.uncertainty) if pm.uncertainty is not None else None
        pm_confidence = confidence_from_property_measurement(pm.review_status)

        dedup_key = (element_system, property_name, value)
        existing_row = merged_rows.get(dedup_key)
        pm_uuid = str(pm.id) if pm.id is not None else None
        if existing_row is not None:
            # ADR-010 D4: cross-surface collapse.  Always fill in fields
            # the kg_nodes row left None (method / unit / uncertainty /
            # temperature) from the PropertyMeasurement, then compare
            # confidence — higher wins.  Emit ONE structured log entry per
            # collapse regardless of who wins (D4 spec — the collapse is
            # the observed event, not the winner).
            kg_uuid = existing_row.get("kg_node_uuid")
            winner_surface = existing_row["source_surface"]
            winner_confidence = existing_row["confidence"]
            # Non-confidence field merge — never lose information across
            # surfaces just because kg had a sparser condition graph.
            if existing_row.get("method") is None and pm.method:
                existing_row["method"] = pm.method
            if existing_row.get("unit") in (None, "", "unknown") and unit:
                existing_row["unit"] = unit
            if existing_row.get("uncertainty") is None and uncertainty is not None:
                existing_row["uncertainty"] = uncertainty
            if existing_row.get("property_measurement_uuid") is None and pm_uuid is not None:
                existing_row["property_measurement_uuid"] = pm_uuid
            if _CONFIDENCE_RANK[pm_confidence] > _CONFIDENCE_RANK[winner_confidence]:
                # PropertyMeasurement wins — overwrite confidence-bearing fields.
                existing_row["confidence"] = pm_confidence
                existing_row["unit"] = unit
                existing_row["uncertainty"] = (
                    uncertainty if uncertainty is not None else existing_row["uncertainty"]
                )
                existing_row["property_measurement_uuid"] = pm_uuid
                existing_row["source_surface"] = "property_measurements"
                winner_surface = "property_measurements"
                winner_confidence = pm_confidence
            logger.info(
                "bridge.dedup.collapse",
                extra={
                    "event": "bridge.dedup.collapse",
                    "hash": compute_dedup_hash(
                        element_system, None, property_name, existing_row["method"], corpus_id
                    ),
                    "element_system": element_system,
                    "property_name": property_name,
                    "value": value,
                    "kg_node_uuid": kg_uuid,
                    "property_measurement_uuid": pm_uuid,
                    "confidence_winner": winner_surface,
                    "confidence_value": winner_confidence.value,
                },
            )
            continue
        merged_rows[dedup_key] = {
            "element_system": element_system,
            "property_name": property_name,
            "value": value,
            "unit": unit,
            "method": pm.method or None,
            "uncertainty": uncertainty,
            "temperature": None,
            "confidence": pm_confidence,
            "context_parts": [],
            "source_surface": "property_measurements",
            "kg_node_uuid": None,
            "property_measurement_uuid": pm_uuid,
        }

    for row in merged_rows.values():
        # ADR-010 V3: text-only sentinel rows (value_scalar IS NULL) live in
        # merged_rows so their context is not lost, but never produce a
        # numeric staging row.
        if row["value"] is None:
            continue
        dedup = compute_dedup_hash(
            row["element_system"], None, row["property_name"], row["method"], corpus_id
        )
        if dedup in existing:
            continue
        existing.add(dedup)
        db.add(
            RefGapFillStaging(
                element_system=row["element_system"],
                phase=None,
                property_name=row["property_name"],
                value=row["value"],
                unit=row["unit"],
                method=row["method"],
                source=corpus_id,
                source_doi=source_doi,
                uncertainty=row["uncertainty"],
                temperature=row["temperature"],
                confidence=row["confidence"],
                status=StagingStatus.PENDING,
                dedup_hash=dedup,
                range_validated=True,
                source_file=f"kg:{source_id}",
                context="; ".join(row["context_parts"]) or None,
            )
        )
        written += 1

    logger.info(
        "bridge_kg_to_staging: source_id=%s corpus=%s wrote %d staging rows",
        source_id,
        corpus_id,
        written,
    )
    return written
