"""Tests for OntoFuel ontology JSON to Pydantic model parsing (NFM-1820).

TDD RED phase: these tests define the contract for ontofuel_ontology models
and ontofuel_parser functions.

Acceptance criteria counts from source data:
- material_ontology_enhanced.json: 139 classes, 755 individuals,
  162 object properties, 279 datatype properties
- nvl_ontology_data.json: 927 nodes, 1061 relationships

Tests are ordered: parsing -> validation -> edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

# Source data paths (relative to project root)
ONTOFUEL_JSON = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "nfm_db"
    / "data"
    / "material_ontology_enhanced.json"
)
NVL_JSON = (
    Path(__file__).resolve().parent.parent.parent
    / "web"
    / "public"
    / "ontology-viewer"
    / "data"
    / "nvl_ontology_data.json"
)

from nfm_db.schemas.ontofuel_ontology import (
    DataProperty,
    MaterialOntologyDocument,
    ObjectProperty,
    OntologyClass,
    OntologyIndividual,
    OntologyMetadata,
    RdfsLabel,
)
from nfm_db.schemas.ontofuel_parser import (
    parse_material_ontology,
    parse_nvl_ontology,
    validate_ontology_stats,
)
from nfm_db.schemas.ontology import OntologyGraphResponse


@pytest.fixture

def material_doc() -> MaterialOntologyDocument:
    return parse_material_ontology(ONTOFUEL_JSON)


@pytest.fixture
def nvl_doc() -> OntologyGraphResponse:
    return parse_nvl_ontology(NVL_JSON)


# ── Material Ontology Parsing ───────────────────────────────────────────


class TestParseMaterialOntology:
    """parse_material_ontology returns a validated MaterialOntologyDocument."""

    def test_returns_typed_document(self, material_doc: MaterialOntologyDocument):
        assert isinstance(material_doc, MaterialOntologyDocument)

    def test_metadata_parsed(self, material_doc: MaterialOntologyDocument):
        meta = material_doc.metadata
        assert meta.name == "Imported Ontology"
        assert meta.version == "1.10.5.1"
        assert meta.source != ""

    def test_class_count(self, material_doc: MaterialOntologyDocument):
        assert len(material_doc.classes) == 139

    def test_individual_count(self, material_doc: MaterialOntologyDocument):
        assert len(material_doc.individuals) == 755

    def test_object_property_count(self, material_doc: MaterialOntologyDocument):
        assert len(material_doc.object_properties) == 162

    def test_datatype_property_count(self, material_doc: MaterialOntologyDocument):
        assert len(material_doc.datatype_properties) == 279

    def test_class_has_required_fields(self, material_doc: MaterialOntologyDocument):
        first = next(iter(material_doc.classes.values()))
        assert first.uri.startswith("http://")
        assert first.type == "owl:Class"
        assert isinstance(first.rdfs_label, list)

    def test_object_property_has_domain_range(
        self, material_doc: MaterialOntologyDocument,
    ):
        has_fiber = material_doc.object_properties.get("hasFiber")
        if has_fiber:
            assert has_fiber.domain == "CompositeMaterial"
            assert has_fiber.range == "SiCFiber"

    def test_individual_extra_properties_preserved(
        self, material_doc: MaterialOntologyDocument,
    ):
        individual = material_doc.individuals.get("U10Zr_VacancyDiffusion")
        assert individual is not None
        assert individual.uri.startswith("http://")
        # Extra heterogeneous data properties preserved via extra="allow"
        extra = individual.model_extra
        assert "preExponentialFactor" in extra
        assert "migrationEnergy" in extra

    def test_individual_with_list_type_preserved(
        self, material_doc: MaterialOntologyDocument,
    ):
        individual = material_doc.individuals.get("UTiNbTaFeMo_2024")
        assert individual is not None
        # type field can be str, list, or None — must not raise


# ── NVL Ontology Parsing ─────────────────────────────────────────────


class TestParseNvlOntology:
    """parse_nvl_ontology returns a validated OntologyGraphResponse."""

    def test_returns_typed_response(self, nvl_doc: OntologyGraphResponse):
        assert isinstance(nvl_doc, OntologyGraphResponse)

    def test_node_count(self, nvl_doc: OntologyGraphResponse):
        assert len(nvl_doc.nodes) == 927

    def test_relationship_count(self, nvl_doc: OntologyGraphResponse):
        assert len(nvl_doc.relationships) == 1061

    def test_schema_version_present(self, nvl_doc: OntologyGraphResponse):
        assert nvl_doc.schema_version != ""


# ── Validation Stats ───────────────────────────────────────────────


class TestValidateOntologyStats:
    """validate_ontology_stats returns correct counts."""

    def test_stats_counts(self, material_doc: MaterialOntologyDocument):
        stats = validate_ontology_stats(material_doc)
        assert stats == {
            "classes": 139,
            "individuals": 755,
            "object_properties": 162,
            "data_properties": 279,
        }


# ── Validation Errors ───────────────────────────────────────────────


class TestValidationErrors:
    """Missing required fields raise ValidationError."""

    def test_missing_uri_raises(self):
        with pytest.raises(ValidationError):
            OntologyClass(uri="", type="owl:Class")

    def test_missing_type_raises(self):
        with pytest.raises(ValidationError):
            OntologyClass(
                uri="http://example.org#Test",
                type="",
            )

    def test_metadata_missing_name_raises(self):
        with pytest.raises(ValidationError):
            OntologyMetadata(
                name="",
                version="1.0",
                namespace="http://example.com",
                created="2026-01-01T00:00:00",
                modified="2026-01-01T00:00:00",
                source="test",
            )

    def test_individual_allows_extra(self):
        ind = OntologyIndividual(
            uri="http://example.org#TestInd",
            type="http://example.org#TestClass",
            custom_field="custom_value",
            numeric_field=42,
        )
        assert ind.model_extra["custom_field"] == "custom_value"
        assert ind.model_extra["numeric_field"] == 42
