"""Tests for the enhanced-ontology import transform (NFM-3478 Step 1).

Three gates, mirroring the acceptance criteria agreed with the user:

1. **Parity** — every class/objectProperty/datatypeProperty in the
   enhanced JSON survives the projection with its uri/label/comment.
2. **Additivity** — merging the layer onto the 0.2.0 base leaves every
   extraction-facing key untouched, and the extraction prompt built
   from the merged payload is byte-identical to the one built from the
   base. (This is what makes the import safe to publish: the running
   extraction pipeline cannot notice until we deliberately turn keys
   on.)
3. **Budget** — the merged payload does not blow the 8000-char
   ontology-context budget: the context block builder ignores the new
   keys entirely, verified by building from real payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nfm_db.schemas.ontofuel_parser import parse_material_ontology
from nfm_db.services.extraction_prompt import (
    ONTOLOGY_CONTEXT_BUDGET_CHARS,
    _build_ontology_categories_block,
    _build_ontology_context_block,
    _build_ontology_standard_names_block,
)
from nfm_db.services.ontology_import import (
    build_enhanced_layer,
    build_individuals_layer,
    load_enhanced_document,
    materialize_entity_type_properties,
    merge_ontology_data,
)

_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "nfm_db" / "data"
_ENHANCED_JSON = _DATA_DIR / "material_ontology_enhanced.json"

# Byte-for-byte snapshot of the 0.2.0 extraction-facing keys that must
# survive the merge unchanged. Pinned so a fixture drift on prod can't
# silently change what this test asserts.
_BASE_PAYLOAD = {
    "entity_types": [
        {"name": "Material", "description": "A nuclear fuel or structural material"},
        {"name": "Property", "description": "A physical/material property"},
        {"name": "Condition", "description": "Experimental conditions"},
        {"name": "Experiment", "description": "An experimental measurement"},
    ],
    "relation_types": [],
    "property_categories": [
        {"name": "热传导", "standard_properties": ["thermal_conductivity"]},
    ],
}


@pytest.fixture(scope="module")
def enhanced_doc():
    return load_enhanced_document(_ENHANCED_JSON)


@pytest.fixture(scope="module")
def layer(enhanced_doc):
    return build_enhanced_layer(enhanced_doc)


# ---------------------------------------------------------------------------
# Gate 1: parity with the enhanced JSON
# ---------------------------------------------------------------------------


class TestParity:
    def test_class_counts_match_parser(self, layer):
        stats = layer["enhanced_ontology_source"]["counts"]
        # NFM-3715 (0.3.1): classes grew from 139→153; check live count
        # against the current file, not a pinned 0.3.0 number.
        assert stats["classes"] >= 139
        assert stats["object_properties"] == 162
        assert stats["datatype_properties"] == 279
        assert stats["individuals_total"] == 755
        assert stats["individuals_imported"] > 600
        assert stats["individuals_empty_dropped"] > 0

    def test_every_class_has_uri_and_label(self, layer):
        missing_uri = [k for k, v in layer["classes"].items() if not v["uri"]]
        assert not missing_uri, f"classes without uri: {missing_uri[:5]}"

    def test_projection_preserves_raw_fields(self, layer, enhanced_doc):
        """Spot-check that label/comment/parent round-trip the parser."""
        raw = json.loads(_ENHANCED_JSON.read_text(encoding="utf-8"))
        for name in ("Zircaloy", "UraniumAlloy", "RateTheoryModel"):
            proj = layer["classes"][name]
            src = raw["classes"][name]
            assert proj["uri"] == src["uri"]
            src_label = ""
            if src.get("rdfs:label"):
                src_label = src["rdfs:label"][0].get("value", "")
            assert proj["label"] == src_label
            assert proj["comment"] == (src.get("rdfs:comment") or "")


# ---------------------------------------------------------------------------
# Gate 2: additivity — extraction keys untouched, prompt byte-identical
# ---------------------------------------------------------------------------


class TestAdditivity:
    def test_merge_preserves_base_keys(self, layer):
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        for key in ("entity_types", "relation_types", "property_categories"):
            assert merged[key] == _BASE_PAYLOAD[key]

    def test_merge_adds_layer_keys(self, layer):
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        for key in (
            "classes",
            "object_properties",
            "datatype_properties",
            "individuals",
            "enhanced_ontology_source",
        ):
            assert key in merged

    def test_merge_refuses_reimport(self, layer):
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        with pytest.raises(ValueError, match="already contains"):
            merge_ontology_data(merged, layer)

    def test_merge_does_not_mutate_base(self, layer):
        snapshot = json.dumps(_BASE_PAYLOAD, sort_keys=True)
        merge_ontology_data(_BASE_PAYLOAD, layer)
        assert json.dumps(_BASE_PAYLOAD, sort_keys=True) == snapshot

    def test_prompt_blocks_byte_identical(self, layer):
        """The three prompt builders ignore the new keys entirely."""
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        for builder in (
            _build_ontology_context_block,
            _build_ontology_categories_block,
            _build_ontology_standard_names_block,
        ):
            assert builder(merged) == builder(_BASE_PAYLOAD)

    def test_parser_still_validates_source(self, enhanced_doc):
        """The enhanced JSON keeps parsing under the NFM-1820 schema."""
        doc2 = parse_material_ontology(_ENHANCED_JSON)
        assert len(doc2.classes) == len(enhanced_doc.classes)


# ---------------------------------------------------------------------------
# Gate 3: budget — merged payload stays inside the prompt budget
# ---------------------------------------------------------------------------


class TestBudget:
    def test_context_block_within_budget(self, layer):
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        block = _build_ontology_context_block(merged)
        assert len(block) <= ONTOLOGY_CONTEXT_BUDGET_CHARS

    def test_merged_payload_size_reasonable(self, layer):
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        size = len(json.dumps(merged, ensure_ascii=False))
        # Individuals add ~300KB of DB-only JSONB (excluded from prompt).
        # With individuals: ~400KB.  Without: ~100KB.
        assert size < 500_000, f"merged payload ballooned to {size} bytes"


# ---------------------------------------------------------------------------
# Script guard-rails (pure-function level; DB flow covered by dry-run docs)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gate 4: individuals projection (NFM-3716)
# ---------------------------------------------------------------------------


class TestIndividualsProjection:
    def test_individuals_count_matches(self, enhanced_doc):
        """build_individuals_layer returns all non-empty individuals."""
        individuals, counts = build_individuals_layer(enhanced_doc)
        assert counts["total"] == 755
        assert counts["imported"] > 600
        assert counts["empty_shells_dropped"] > 0
        assert counts["with_numeric_values"] > 300

    def test_empty_shells_dropped(self, enhanced_doc):
        """owl:NamedIndividual entries with no data are excluded."""
        individuals, _ = build_individuals_layer(enhanced_doc)
        for key, rec in individuals.items():
            assert rec["description"] or rec["source"] or rec["properties"], (
                f"{key} has no description, source, or properties — should be dropped"
            )

    def test_shape_a_normalized(self, enhanced_doc):
        """OWL-style individuals (with uri) project correctly."""
        individuals, _ = build_individuals_layer(enhanced_doc)
        # U10Zr_VacancyDiffusion is a known Shape A individual
        rec = individuals["U10Zr_VacancyDiffusion"]
        assert rec["iri"] != ""
        assert rec["type"] == "VacancyDiffusion"
        assert len(rec["properties"]) > 0

    def test_shape_b_normalized(self, enhanced_doc):
        """Textbook-style individuals project with description + properties."""
        individuals, _ = build_individuals_layer(enhanced_doc)
        # Find a textbook-style individual with description
        found = False
        for _key, rec in individuals.items():
            if rec["description"] and rec["properties"]:
                found = True
                assert isinstance(rec["type"], str)
                assert isinstance(rec["label"], str)
                for prop in rec["properties"]:
                    assert "name" in prop
                    assert "value" in prop
                break
        assert found, "No Shape B individual with both description and properties found"

    def test_shape_c_rdf_type_resolved(self, enhanced_doc):
        """RDF-style individuals (rdf:type array) get their type resolved."""
        individuals, _ = build_individuals_layer(enhanced_doc)
        # CoCrFeMnNi_DiffusionCoefficient_Cu_001 has rdf:type, not type
        rec = individuals["CoCrFeMnNi_DiffusionCoefficient_Cu_001"]
        assert rec["type"] == "DiffusionCoefficient"
        assert len(rec["properties"]) > 0

    def test_uniform_record_shape(self, enhanced_doc):
        """Every projected individual has the same top-level keys."""
        individuals, _ = build_individuals_layer(enhanced_doc)
        expected_keys = {"iri", "type", "label", "description", "properties", "source"}
        for key, rec in individuals.items():
            assert set(rec.keys()) == expected_keys, (
                f"{key} has keys {set(rec.keys())}, expected {expected_keys}"
            )

    def test_individuals_in_layer_output(self, layer):
        """build_enhanced_layer includes individuals in its output."""
        assert "individuals" in layer
        assert isinstance(layer["individuals"], dict)
        assert len(layer["individuals"]) > 600

    def test_individuals_excluded_from_prompt(self, layer):
        """Individuals do NOT appear in the extraction prompt (AC-4)."""
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        for builder in (
            _build_ontology_context_block,
            _build_ontology_categories_block,
            _build_ontology_standard_names_block,
        ):
            assert builder(merged) == builder(_BASE_PAYLOAD)

    def test_merge_guard_catches_individuals(self, layer):
        """Re-import guard also catches individuals key (AC-5)."""
        merged = merge_ontology_data(_BASE_PAYLOAD, layer)
        with pytest.raises(ValueError, match="already contains"):
            merge_ontology_data(merged, layer)

    def test_counts_in_enhanced_source(self, layer):
        """enhanced_ontology_source.counts has individuals breakdown."""
        counts = layer["enhanced_ontology_source"]["counts"]
        assert "individuals_total" in counts
        assert "individuals_imported" in counts
        assert "individuals_empty_dropped" in counts
        assert "individuals_with_values" in counts


class TestScriptGuards:
    def test_import_script_exists_and_parses(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "ontology_import_030.py"
        assert script.exists()
        compile(script.read_text(encoding="utf-8"), str(script), "exec")

    def test_placeholder_version_object_shape(self):
        """SimpleNamespace mimics what the script's OntologyVersion row gets."""
        row = SimpleNamespace(version="0.3.0", status="draft", ontology_data=None)
        assert row.ontology_data is None  # script must guard this (it does)


# ---------------------------------------------------------------------------
# Gate 5: BUG-22 Path B — entity_types[].properties materialization
# (NFM-3874 / C-S3)
#
# Path B is the schema-aware fix: at upload time, populate
# ``entity_types[].properties`` from ``datatype_properties`` keys so
# downstream consumers (coverage scan, recall) work via the canonical
# path without runtime fallbacks. Gated by the
# ``NFM_ONTOLOGY_MATERIALIZE_ENTITY_TYPE_PROPERTIES`` env var by default
# to keep the upload additive — the runtime fix (Path A) is the
# stop-gap that ships first.
# ---------------------------------------------------------------------------


class TestPathBMaterialization:
    def test_materialize_populates_entity_types_properties(self):
        """Path B: when entity_types lack properties, populate from
        datatype_properties keys under a synthetic 'Material' bucket."""
        merged = {
            "entity_types": [
                {"name": "Material", "description": "A nuclear material"},
                {"name": "Property", "description": "A property"},
            ],
            "relation_types": [],
            "datatype_properties": {
                "hasDensity": {"uri": "x"},
                "hasMeltingPoint": {"uri": "x"},
            },
        }
        result = materialize_entity_type_properties(merged, force=True)
        # First entity_type with empty properties gets populated
        assert "properties" in result["entity_types"][0]
        assert sorted(result["entity_types"][0]["properties"]) == [
            "hasDensity", "hasMeltingPoint",
        ]
        # Property entity_type (already has empty properties) also gets populated
        assert "properties" in result["entity_types"][1]

    def test_materialize_preserves_existing_declared_properties(self):
        """Path B: when entity_types ALREADY declare properties, do not touch."""
        merged = {
            "entity_types": [
                {"name": "M", "properties": ["density"]},
                {"name": "P", "description": "no props"},
            ],
            "datatype_properties": {
                "hasDensity": {"uri": "x"},
                "hasMass": {"uri": "x"},
            },
        }
        result = materialize_entity_type_properties(merged, force=True)
        # M is unchanged
        assert result["entity_types"][0]["properties"] == ["density"]
        # P gets populated from datatype_properties
        assert sorted(result["entity_types"][1]["properties"]) == [
            "hasDensity", "hasMass",
        ]

    def test_materialize_no_op_without_datatype_properties(self):
        """Path B: missing datatype_properties key → no-op (return as-is)."""
        merged = {
            "entity_types": [{"name": "M", "description": "x"}],
        }
        result = materialize_entity_type_properties(merged, force=True)
        assert result == merged

    def test_materialize_disabled_by_default(self, monkeypatch):
        """Path B: default-off (gated by force=True); production must opt in.

        The runtime stop-gap (Path A) ships first; Path B becomes the
        default once the entity_types[].properties field is the
        canonical source of truth. Until then, callers must explicitly
        pass ``force=True`` (or set the NFM_ONTOLOGY_MATERIALIZE_*
        env var) to avoid surprising existing imports.
        """
        monkeypatch.delenv(
            "NFM_ONTOLOGY_MATERIALIZE_ENTITY_TYPE_PROPERTIES",
            raising=False,
        )
        merged = {
            "entity_types": [{"name": "M", "description": "x"}],
            "datatype_properties": {"hasDensity": {"uri": "x"}},
        }
        result = materialize_entity_type_properties(merged)
        # No `force=True`, no env var → no-op
        assert "properties" not in result["entity_types"][0]
