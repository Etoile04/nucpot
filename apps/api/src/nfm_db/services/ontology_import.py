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
3. **Individuals are NOT imported.** Individuals (755) are instance
   data, not schema; counts are recorded in
   ``enhanced_ontology_source.counts`` so parity is still checkable.

Public API:
    load_enhanced_document / build_enhanced_layer / merge_ontology_data
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nfm_db.schemas.ontofuel_ontology import MaterialOntologyDocument

__all__ = [
    "ENHANCED_JSON_PATH",
    "build_enhanced_layer",
    "load_enhanced_document",
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
    for name, prop in doc.datatype_properties.items():
        datatype_properties[name] = {
            "uri": prop.uri,
            "comment": prop.rdfs_comment or "",
            "domain": _norm_ref(prop.domain),
        }

    return {
        "classes": classes,
        "object_properties": object_properties,
        "datatype_properties": datatype_properties,
        "enhanced_ontology_source": {
            "file": "material_ontology_enhanced.json",
            "name": doc.metadata.name,
            "version": doc.metadata.version,
            "namespace": doc.metadata.namespace,
            "counts": {
                "classes": len(classes),
                "object_properties": len(object_properties),
                "datatype_properties": len(datatype_properties),
                # Instance data, intentionally not imported (see module docstring).
                "individuals_not_imported": len(doc.individuals),
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
    if "classes" in base or "enhanced_ontology_source" in base:
        raise ValueError(
            "Base ontology_data already contains an enhanced layer "
            f"(keys present: {[k for k in ('classes', 'enhanced_ontology_source') if k in base]}). "
            "Re-importing would overwrite it — inspect the draft first."
        )
    merged = dict(base)
    merged.update(layer)
    return merged
