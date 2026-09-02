"""Enhanced-ontology import transform (NFM-3478 前置治理 Step 1).

Pure, DB-free transform that merges the enhanced material ontology
(``material_ontology_enhanced.json``, NFM-1820) into an
``ontology_versions.ontology_data`` payload as an **additive classes
layer** — the first step towards a single source of truth for both the
extraction prompt and the viewer.

Design decisions
----------------
1. **Additive only.** The merged payload keeps every base key (0.2.0's
   ``entity_types`` / ``relation_types`` / ``property_categories``) and
   appends new top-level keys: ``classes``, ``object_properties``,
   ``datatype_properties``, ``enhanced_ontology_source``. The extraction
   prompt builder reads only the base keys, so prompts are
   byte-identical before/after the merge (asserted by tests).
2. **Slender projection.** Classes/properties are projected to the
   fields downstream consumers need (uri/label/comment/parent, domain,
   range) instead of the raw RDF records — full fidelity for schema
   purposes, ~94KB instead of the raw multi-MB blob.
3. **Individuals projected separately.** Individuals (755) are instance
   data projected via ``build_individuals_layer`` as a new top-level
   ``individuals`` key.  They are excluded from the extraction prompt
   (AC-4: prompt budget covers only methods/properties directory).

Public API:
    load_enhanced_document / build_enhanced_layer / merge_ontology_data
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nfm_db.schemas.ontofuel_ontology import MaterialOntologyDocument

__all__ = [
    "ENHANCED_JSON_PATH",
    "build_enhanced_layer",
    "build_individuals_layer",
    "load_enhanced_document",
    "materialize_entity_type_properties",
    "merge_ontology_data",
]

# Canonical location of the enhanced ontology (ships inside the image).
ENHANCED_JSON_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "material_ontology_enhanced.json"
)


def load_enhanced_document(
    path: Path | None = None,
) -> MaterialOntologyDocument:
    """Load + validate the enhanced ontology JSON via the NFM-1820 parser.

    Raises ``FileNotFoundError`` / ``pydantic.ValidationError`` as-is so
    callers (script + tests) see the raw failure.
    """
    raw = json.loads((path or ENHANCED_JSON_PATH).read_text(encoding="utf-8"))
    return MaterialOntologyDocument.model_validate(raw)


def _first_label(labels: list[Any]) -> str:
    """Return the first ``rdfs:label`` value, or '' when absent."""
    for entry in labels:
        value = getattr(entry, "value", None) or (
            entry.get("value") if isinstance(entry, dict) else None
        )
        if value:
            return str(value)
    return ""


# -- Individuals normalisation (NFM-3716) -----------------------------------


def _extract_short_type(raw_type: Any) -> str:
    """Parse a type value to a short class name.

    Handles:
    - IRI string: ``http://…#VacancyDiffusion`` → ``VacancyDiffusion``
    - rdf:type array: ``[{"uri": "http://…#DiffusionCoefficient"}]``
    - Short name passthrough: ``MaterialProperty``
    - None / empty → ``""``
    """
    if not raw_type:
        return ""
    if isinstance(raw_type, str):
        return raw_type.rsplit("#", 1)[-1] if "#" in raw_type else raw_type
    if isinstance(raw_type, list) and raw_type:
        first = raw_type[0]
        uri = first.get("uri", "") if isinstance(first, dict) else str(first)
        return uri.rsplit("#", 1)[-1] if "#" in uri else uri
    return str(raw_type)


def _extract_label(individual: dict[str, Any]) -> str:
    """Extract label from rdfs:label (list or string) or label field."""
    raw = individual.get("rdfs:label") or individual.get("label")
    if isinstance(raw, list):
        for entry in raw:
            value = getattr(entry, "value", None) or (
                entry.get("value") if isinstance(entry, dict) else None
            )
            if value:
                return str(value)
        return ""
    return str(raw) if raw else ""


def _extract_description(individual: dict[str, Any]) -> str:
    """Extract description from description or rdfs:comment."""
    return str(individual.get("description") or individual.get("rdfs:comment") or "")


def _extract_source(individual: dict[str, Any]) -> str:
    """Extract literature source as a flat string."""
    raw = individual.get("source")
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        parts: list[str] = []
        if raw.get("chapter"):
            parts.append(f"Ch {raw['chapter']}")
        if raw.get("pages"):
            parts.append(f"pp {raw['pages']}")
        if raw.get("text_excerpt"):
            parts.append(raw["text_excerpt"][:200])
        return "; ".join(parts) if parts else ""
    return ""


def _extract_numeric_properties(
    individual: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract numeric-valued properties into a uniform list.

    Each entry: ``{"name": str, "value": Any, "unit": str, "source": str, "confidence": str}``.
    Covers:
    - Shape A: flat numeric fields (``preExponentialFactor``) and
      sub-dicts with average/max/min/value
    - Shape B: ``properties`` dict or list with nested value/unit/source
    - Shape C (RDF): ``hasValue``, ``hasUnit``, ``hasConfidence``, ``hasSource``
    """
    result: list[dict[str, Any]] = []
    _SKIP_KEYS = frozenset(
        {
            "uri",
            "type",
            "label",
            "rdfs:label",
            "description",
            "source",
            "properties",
            "operatesIn",
            "validFor",
            "rdf:type",
            "rdfs:comment",
        }
    )
    # Shape A: flat fields / sub-dicts with numeric structure
    for key, val in individual.items():
        if key in _SKIP_KEYS:
            continue
        if isinstance(val, (int, float)):
            result.append({"name": key, "value": val, "unit": "", "source": "", "confidence": ""})
        elif isinstance(val, dict):
            numeric_val = val.get("average") or val.get("max") or val.get("min") or val.get("value")
            if numeric_val is not None:
                result.append(
                    {
                        "name": key,
                        "value": numeric_val,
                        "unit": str(val.get("unit", "")),
                        "source": str(val.get("source", "")),
                        "confidence": str(val.get("confidence", "")),
                    }
                )

    # Shape B: properties dict/list
    props = individual.get("properties")
    if isinstance(props, dict):
        for pname, pval in props.items():
            if isinstance(pval, dict):
                pval_value = pval.get("value")
                if pval_value is not None and any(c.isdigit() for c in str(pval_value)):
                    result.append(
                        {
                            "name": pname,
                            "value": pval_value,
                            "unit": str(pval.get("unit", "")),
                            "source": str(pval.get("source", "")),
                            "confidence": str(pval.get("confidence", "")),
                        }
                    )
    elif isinstance(props, list):
        for prop in props:
            if isinstance(prop, dict):
                prop_value = prop.get("value")
                if prop_value is not None and any(c.isdigit() for c in str(prop_value)):
                    result.append(
                        {
                            "name": prop.get("name", ""),
                            "value": prop_value,
                            "unit": str(prop.get("unit", "")),
                            "source": str(prop.get("source", "")),
                            "confidence": str(prop.get("confidence", "")),
                        }
                    )

    # Shape C (RDF): hasValue/hasUnit/hasConfidence/hasSource
    has_value = individual.get("hasValue")
    if has_value:
        actual_value = (
            has_value[0].get("value")
            if isinstance(has_value, list)
            else has_value.get("value")
            if isinstance(has_value, dict)
            else has_value
        )
        actual_unit = _rdf_field_value(individual.get("hasUnit"))
        actual_confidence = _rdf_field_value(individual.get("hasConfidence"))
        actual_source = _rdf_field_value(individual.get("hasSource"))
        result.append(
            {
                "name": "value",
                "value": actual_value,
                "unit": str(actual_unit),
                "source": str(actual_source),
                "confidence": str(actual_confidence),
            }
        )

    return result


def _rdf_field_value(field: Any) -> Any:
    """Extract the value from an RDF-typed field (list-of-dicts or dict)."""
    if not field:
        return ""
    if isinstance(field, list) and field:
        return field[0].get("value", "") if isinstance(field[0], dict) else field[0]
    if isinstance(field, dict):
        return field.get("value", "")
    return field


def _is_empty_shell(individual: dict[str, Any]) -> bool:
    """Return True if the individual has no description, no source, and no numeric properties.

    Empty shells (e.g. ``owl:NamedIndividual`` with no data) are dropped.
    """
    if _extract_description(individual):
        return False
    if _extract_source(individual):
        return False
    if _extract_numeric_properties(individual):
        return False
    # Shape C RDF fields
    for key in ("hasValue", "hasSource", "hasConfidence"):
        if individual.get(key):
            return False
    return True


def build_individuals_layer(
    doc: MaterialOntologyDocument,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Project and normalize individuals from the validated document.

    Returns a tuple of ``(individuals_dict, counts)`` where:

    - ``individuals_dict`` maps key → normalized record with keys:
      ``iri``, ``type``, ``label``, ``description``, ``properties``, ``source``
    - ``counts`` has ``total``, ``imported``, ``empty_shells_dropped``,
      ``with_numeric_values``

    Three source shapes are normalized:

    - **Shape A** (OWL): ``uri``, IRI ``type``, ``rdfs:label``, flat numeric fields
    - **Shape B** (textbook): short ``type``, ``label``, ``description``,
      nested ``properties`` dict/list
    - **Shape C** (RDF): ``rdf:type`` array, ``rdfs:label`` array,
      ``hasValue``/``hasUnit``/``hasConfidence``/``hasSource``
    """
    raw_individuals = doc.individuals
    individuals: dict[str, dict[str, Any]] = {}
    empty_shells = 0
    with_numeric = 0
    missing_type = 0

    for key, ind in raw_individuals.items():
        ind_dict = ind.model_dump() if hasattr(ind, "model_dump") else dict(ind)

        if _is_empty_shell(ind_dict):
            empty_shells += 1
            continue

        short_type = _extract_short_type(ind_dict.get("type"))
        if not short_type:
            # Shape C: rdf:type instead of type
            rdf_type = ind_dict.get("rdf:type")
            short_type = _extract_short_type(rdf_type)
            if not short_type:
                missing_type += 1

        numeric_props = _extract_numeric_properties(ind_dict)
        if numeric_props:
            with_numeric += 1

        individuals[key] = {
            "iri": ind_dict.get("uri", ""),
            "type": short_type,
            "label": _extract_label(ind_dict),
            "description": _extract_description(ind_dict),
            "properties": numeric_props,
            "source": _extract_source(ind_dict),
        }

    counts = {
        "total": len(raw_individuals),
        "imported": len(individuals),
        "empty_shells_dropped": empty_shells,
        "with_numeric_values": with_numeric,
        "missing_type_imported": missing_type,
    }
    return individuals, counts


def build_enhanced_layer(doc: MaterialOntologyDocument) -> dict[str, Any]:
    """Project the validated document into the additive ontology layer.

    Returns a dict with keys ``classes`` / ``object_properties`` /
    ``datatype_properties`` / ``enhanced_ontology_source``.
    """
    classes: dict[str, dict[str, Any]] = {}
    for name, cls in doc.classes.items():
        classes[name] = {
            "uri": cls.uri,
            "label": _first_label(cls.rdfs_label),
            "comment": cls.rdfs_comment or cls.comment or "",
            "parent": cls.parent,
        }

    def _norm_ref(ref: Any) -> Any:
        """Normalise domain/range refs to a list of class-name strings."""
        if ref is None:
            return None
        if isinstance(ref, str):
            return [ref]
        if isinstance(ref, list):
            out: list[str] = index_ref_list(ref)
            return [x for x in out if x]
        return [str(ref)]

    def index_ref_list(ref: list[Any]) -> list[str]:
        out: list[str] = []
        for item in ref:
            if isinstance(item, dict):
                uri = item.get("uri", "")
                # Keep the local name after '#'.
                out.append(uri.rsplit("#", 1)[-1] if uri else "")
            else:
                out.append(str(item))
        return out

    object_properties: dict[str, dict[str, Any]] = {}
    for name, prop in doc.object_properties.items():
        object_properties[name] = {
            "uri": prop.uri,
            "comment": prop.rdfs_comment or "",
            "domain": _norm_ref(prop.domain),
            "range": _norm_ref(prop.range),
        }

    datatype_properties: dict[str, dict[str, Any]] = {}
    for dname, dprop in doc.datatype_properties.items():
        datatype_properties[dname] = {
            "uri": dprop.uri,
            "comment": dprop.rdfs_comment or "",
            "domain": _norm_ref(dprop.domain),
        }

    individuals, ind_counts = build_individuals_layer(doc)

    return {
        "classes": classes,
        "object_properties": object_properties,
        "datatype_properties": datatype_properties,
        "individuals": individuals,
        "enhanced_ontology_source": {
            "file": "material_ontology_enhanced.json",
            "name": doc.metadata.name,
            "version": doc.metadata.version,
            "namespace": doc.metadata.namespace,
            "counts": {
                "classes": len(classes),
                "object_properties": len(object_properties),
                "datatype_properties": len(datatype_properties),
                "individuals_total": ind_counts["total"],
                "individuals_imported": ind_counts["imported"],
                "individuals_empty_dropped": ind_counts["empty_shells_dropped"],
                "individuals_with_values": ind_counts["with_numeric_values"],
                "individuals_missing_type": ind_counts["missing_type_imported"],
            },
        },
    }


def merge_ontology_data(base: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    """Merge the additive layer into a base ``ontology_data`` payload.

    Returns a NEW dict; *base* is never mutated.

    Raises
    ------
    ValueError
        If the base already carries a ``classes`` key (re-import guard —
        importing twice would silently overwrite curated classes).
    """
    enhanced_keys = ("classes", "enhanced_ontology_source", "individuals")
    present = [k for k in enhanced_keys if k in base]
    if present:
        raise ValueError(
            "Base ontology_data already contains an enhanced layer "
            f"(keys present: {present}). "
            "Re-importing would overwrite it — inspect the draft first."
        )
    merged = dict(base)
    merged.update(layer)
    return merged


# ---------------------------------------------------------------------------
# BUG-22 Path B — entity_types[].properties materialization (NFM-3874 / C-S3)
# ---------------------------------------------------------------------------
#
# Path A (in gap_scanner.extract_expected_pairs) is the runtime stop-gap:
# when entity_types[].properties is empty the scanner falls back to
# enumerating ontology_data['datatype_properties'] keys. Path B is the
# schema-aware proper fix: at upload time, populate
# entity_types[].properties from datatype_properties so future scans work
# via the canonical path without runtime fallbacks.
#
# Per C-D2 (NFM-3833) the B-line work runs *parallel* to the A-line fix
# and is gated by ``force=True`` (or the
# ``NFM_ONTOLOGY_MATERIALIZE_ENTITY_TYPE_PROPERTIES`` env var) so existing
# imports stay byte-identical until operators opt in.
_MATERIALIZE_ENV = "NFM_ONTOLOGY_MATERIALIZE_ENTITY_TYPE_PROPERTIES"


def _materialize_enabled(force: bool) -> bool:
    """Return True when the materialization opt-in is active.

    Either ``force=True`` (explicit per-call) or the env var set to a
    truthy value (``1``, ``true``, ``yes``) counts as opt-in. Default is
    off: existing imports must remain byte-identical until the A-line
    rollout is complete and operators flip the switch.
    """
    if force:
        return True
    raw = os.environ.get(_MATERIALIZE_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def materialize_entity_type_properties(
    merged: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Path B: populate ``entity_types[].properties`` from
    ``datatype_properties`` keys (NFM-3874 / C-S3).

    Behaviour:

    * Default-off (gated by ``force=True`` or env var) so existing
      imports stay byte-identical until operators opt in.
    * Mutates ``merged`` in place AND returns it (chainable).  Callers
      that need an immutable copy must ``copy.deepcopy(merged)`` first.
    * For each entity_type whose ``properties`` key is missing/empty,
      populate it with the sorted list of ``datatype_properties`` keys.
      Entity_types that already declare properties are left untouched.
    * If ``datatype_properties`` is missing/empty, the function is a
      no-op (returns ``merged`` unchanged).

    Why "first entity_type"?  v0.4.0's entity_types list has 4 entries
    (Material, Property, Condition, Experiment) and the enhanced layer
    does not carry per-property domain metadata (all ``domain=None``),
    so we cannot reliably distribute each ``datatype_property`` to its
    declared domain.  Distributing to *every* entity_type would inflate
    ``total_expected`` 4x and create phantom gaps.  The "first with
    declared properties" heuristic — or, here, the first entity_type
    when none declare — preserves the existing "Material is the primary
    target" expectation while making the count non-vacuous.
    """
    if not _materialize_enabled(force):
        return merged

    entity_types = merged.get("entity_types")
    if not isinstance(entity_types, list) or not entity_types:
        return merged

    dtp = merged.get("datatype_properties")
    if not isinstance(dtp, dict) or not dtp:
        return merged

    property_names = sorted(name.strip() for name in dtp if isinstance(name, str) and name.strip())
    if not property_names:
        return merged

    for entity_type_dict in entity_types:
        if not isinstance(entity_type_dict, dict):
            continue
        existing = entity_type_dict.get("properties")
        if existing:  # truthy list/dict → declared, leave alone
            continue
        entity_type_dict["properties"] = list(property_names)

    return merged
