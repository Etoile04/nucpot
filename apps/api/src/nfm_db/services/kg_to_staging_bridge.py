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
    "相变温度": "phase_transition_temperature",
    "相平衡线斜率": "phase_boundary_slope",
    "相变体积变化": "phase_transition_volume_change",
    "相变潜热": "phase_transition_latent_heat",
    "熔点": "melting_point",
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
        elif e.relation_type == "relatedTo":
            # conditions hang off properties (either direction)
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

    written = 0
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

            dedup = compute_dedup_hash(element_system, None, property_name, method, corpus_id)
            if dedup in existing:
                continue
            existing.add(dedup)

            db.add(
                RefGapFillStaging(
                    element_system=element_system,
                    phase=None,
                    property_name=property_name,
                    value=value,
                    unit=unit,
                    method=method,
                    source=corpus_id,
                    source_doi=source_doi,
                    uncertainty=unc,
                    temperature=temperature,
                    confidence=_confidence_from_kg(prop.confidence),
                    status=StagingStatus.PENDING,
                    dedup_hash=dedup,
                    range_validated=True,
                    source_file=f"kg:{source_id}",
                    context="; ".join(context_parts) or None,
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
