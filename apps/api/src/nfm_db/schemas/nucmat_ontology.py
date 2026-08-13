"""NucMat ontology type definitions and extraction mapping (NFM-859 B2.5).

Defines the canonical entity types, relation types, and confidence thresholds
for the nuclear materials knowledge graph. Provides the mapping layer between
raw extraction output and KG ontology types.

Entity types (6): Material, Property, Experiment, Condition, Publication, Measurement.
Relation types (13): hasProperty, measuredIn, relatedTo, cites, hasCondition,
  publishedIn, containsData, synthesizedBy, alloyOf, irradiatedIn,
  testedAt, references, derivedFrom.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_THRESHOLD: float = 0.6
HIGH_CONFIDENCE_THRESHOLD: float = 0.9


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------


class EntityType(StrEnum):
    """Canonical entity types in the NucMat knowledge graph."""

    MATERIAL = "Material"
    PROPERTY = "Property"
    EXPERIMENT = "Experiment"
    CONDITION = "Condition"
    PUBLICATION = "Publication"
    MEASUREMENT = "Measurement"


# ---------------------------------------------------------------------------
# Relation types
# ---------------------------------------------------------------------------


class RelationType(StrEnum):
    """Canonical relation types in the NucMat knowledge graph."""

    HAS_PROPERTY = "hasProperty"
    MEASURED_IN = "measuredIn"
    RELATED_TO = "relatedTo"
    CITES = "cites"
    HAS_CONDITION = "hasCondition"
    PUBLISHED_IN = "publishedIn"
    CONTAINS_DATA = "containsData"
    SYNTHESIZED_BY = "synthesizedBy"
    ALLOY_OF = "alloyOf"
    IRRADIATED_IN = "irradiatedIn"
    TESTED_AT = "testedAt"
    REFERENCES = "references"
    DERIVED_FROM = "derivedFrom"


# ---------------------------------------------------------------------------
# Extraction-to-ontology mapping
# ---------------------------------------------------------------------------

# Maps common extraction category labels to canonical entity types.
EXTRACTION_ENTITY_MAP: dict[str, EntityType] = {
    "material": EntityType.MATERIAL,
    "compound": EntityType.MATERIAL,
    "element": EntityType.MATERIAL,
    "alloy": EntityType.MATERIAL,
    "property": EntityType.PROPERTY,
    "thermal_property": EntityType.PROPERTY,
    "mechanical_property": EntityType.PROPERTY,
    "experiment": EntityType.EXPERIMENT,
    "irradiation": EntityType.EXPERIMENT,
    "test": EntityType.EXPERIMENT,
    "condition": EntityType.CONDITION,
    "temperature": EntityType.CONDITION,
    "pressure": EntityType.CONDITION,
    "atmosphere": EntityType.CONDITION,
    "publication": EntityType.PUBLICATION,
    "paper": EntityType.PUBLICATION,
    "reference": EntityType.PUBLICATION,
    "measurement": EntityType.MEASUREMENT,
    "data_point": EntityType.MEASUREMENT,
    "observation": EntityType.MEASUREMENT,
}

# Maps common extraction relation labels to canonical relation types.
EXTRACTION_RELATION_MAP: dict[str, RelationType] = {
    "has_property": RelationType.HAS_PROPERTY,
    "hasProperty": RelationType.HAS_PROPERTY,
    "measured_in": RelationType.MEASURED_IN,
    "measuredIn": RelationType.MEASURED_IN,
    "published_in": RelationType.PUBLISHED_IN,
    "publishedIn": RelationType.PUBLISHED_IN,
    "contains_data": RelationType.CONTAINS_DATA,
    "containsData": RelationType.CONTAINS_DATA,
    "synthesized_by": RelationType.SYNTHESIZED_BY,
    "synthesizedBy": RelationType.SYNTHESIZED_BY,
    "alloy_of": RelationType.ALLOY_OF,
    "alloyOf": RelationType.ALLOY_OF,
    "irradiated_in": RelationType.IRRADIATED_IN,
    "irradiatedIn": RelationType.IRRADIATED_IN,
    "tested_at": RelationType.TESTED_AT,
    "testedAt": RelationType.TESTED_AT,
    "references": RelationType.REFERENCES,
    "cites": RelationType.CITES,
    "derived_from": RelationType.DERIVED_FROM,
    "derivedFrom": RelationType.DERIVED_FROM,
    "related_to": RelationType.RELATED_TO,
    "relatedTo": RelationType.RELATED_TO,
    "has_condition": RelationType.HAS_CONDITION,
    "hasCondition": RelationType.HAS_CONDITION,
}


def map_extraction_entity(category: str) -> EntityType | None:
    """Map an extraction category string to a canonical EntityType.

    Returns None if no mapping exists (unknown categories should be
    flagged for review rather than silently dropped).
    """
    return EXTRACTION_ENTITY_MAP.get(category.lower())


def map_extraction_relation(relation: str) -> RelationType | None:
    """Map an extraction relation string to a canonical RelationType.

    Returns None if no mapping exists (unknown relations should be
    flagged for review rather than silently dropped).
    """
    return EXTRACTION_RELATION_MAP.get(relation)


def build_node_properties(
    *,
    entity_type: EntityType,
    raw_data: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON properties blob for a KGNode from raw extraction data.

    Filters and normalizes extraction output into the ontology's property
    schema for the given entity type.
    """
    match entity_type:
        case EntityType.MATERIAL:
            return _build_material_properties(raw_data)
        case EntityType.PROPERTY:
            return _build_property_properties(raw_data)
        case EntityType.EXPERIMENT:
            return _build_experiment_properties(raw_data)
        case EntityType.CONDITION:
            return _build_condition_properties(raw_data)
        case EntityType.PUBLICATION:
            return _build_publication_properties(raw_data)
        case EntityType.MEASUREMENT:
            return _build_measurement_properties(raw_data)
        case _:
            return dict(raw_data)


def _build_material_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Extract material-specific properties from raw data."""
    keys_of_interest = {
        "chemical_formula",
        "crystal_structure",
        "molecular_weight",
        "density",
        "composition",
        "phase",
        "microstructure",
    }
    return {k: v for k, v in data.items() if k in keys_of_interest}


def _build_property_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Extract property-specific properties from raw data."""
    keys_of_interest = {
        "value",
        "unit",
        "temperature",
        "uncertainty",
        "property_name",
        "direction",
    }
    return {k: v for k, v in data.items() if k in keys_of_interest}


def _build_experiment_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Extract experiment-specific properties from raw data."""
    keys_of_interest = {
        "experiment_type",
        "facility",
        "duration",
        "irradiation_dose",
        "flux",
    }
    return {k: v for k, v in data.items() if k in keys_of_interest}


def _build_condition_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Extract condition-specific properties from raw data."""
    keys_of_interest = {
        "temperature",
        "pressure",
        "atmosphere",
        "environment",
        "time",
    }
    return {k: v for k, v in data.items() if k in keys_of_interest}


def _build_publication_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Extract publication-specific properties from raw data."""
    keys_of_interest = {
        "doi",
        "authors",
        "year",
        "journal",
        "title",
        "volume",
    }
    return {k: v for k, v in data.items() if k in keys_of_interest}


def _build_measurement_properties(data: dict[str, Any]) -> dict[str, Any]:
    """Extract measurement-specific properties from raw data."""
    keys_of_interest = {
        "value",
        "unit",
        "uncertainty",
        "technique",
        "sample_id",
        "measured_at",
    }
    return {k: v for k, v in data.items() if k in keys_of_interest}
