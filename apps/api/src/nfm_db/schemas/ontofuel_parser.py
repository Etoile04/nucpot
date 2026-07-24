"""Pure parsing + validation functions for OntoFuel ontology JSON files (NFM-1820).

No DB writes — this is a read-only parsing layer that transforms JSON
into typed Pydantic models.
"""

from __future__ import annotations

import json
from pathlib import Path

from nfm_db.schemas.ontology import OntologyGraphResponse
from nfm_db.schemas.ontofuel_ontology import MaterialOntologyDocument


def parse_material_ontology(path: Path) -> MaterialOntologyDocument:
    """Parse material_ontology_enhanced.json into typed Pydantic models.

    Parameters
    ----------
    path:
        Filesystem path to a material_ontology_enhanced.json file.

    Returns
    -------
    MaterialOntologyDocument with all classes, properties, and individuals
    validated against their Pydantic schemas.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValidationError
        If the JSON structure does not match the expected schema.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    return MaterialOntologyDocument.model_validate(raw)


def parse_nvl_ontology(path: Path) -> OntologyGraphResponse:
    """Parse nvl_ontology_data.json (uses existing OntologyGraphResponse).

    The static viewer JSON predates the NFM-227 contract envelope and
    lacks ``corpus_id`` and ``source_digest``.  This function backfills
    those fields before validation so the legacy artifact round-trips
    through the contract model without modification to the schema.

    Parameters
    ----------
    path:
        Filesystem path to an nvl_ontology_data.json file.

    Returns
    -------
    OntologyGraphResponse validated against the NFM-227 contract schema.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    ValidationError
        If the JSON structure does not match the NFM-227 contract.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    # Backfill NFM-227 contract fields missing from the static artifact.
    raw.setdefault("corpus_id", "nvl-legacy")
    raw.setdefault(
        "source_digest",
        "0" * 16,  # sha256 short digest placeholder
    )

    return OntologyGraphResponse.model_validate(raw)


def validate_ontology_stats(
    doc: MaterialOntologyDocument,
) -> dict[str, int]:
    """Return counts: classes, individuals, object_props, data_props.

    Parameters
    ----------
    doc:
        A validated MaterialOntologyDocument.

    Returns
    -------
    A dict with keys ``classes``, ``individuals``,
    ``object_properties``, and ``data_properties``.
    """
    return {
        "classes": len(doc.classes),
        "individuals": len(doc.individuals),
        "object_properties": len(doc.object_properties),
        "data_properties": len(doc.datatype_properties),
    }
