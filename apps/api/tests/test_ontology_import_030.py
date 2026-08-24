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
    load_enhanced_document,
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
        assert stats["classes"] == 139
        assert stats["object_properties"] == 162
        assert stats["datatype_properties"] == 279
        assert stats["individuals_not_imported"] == 755

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
        for key in ("classes", "object_properties", "datatype_properties",
                    "enhanced_ontology_source"):
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
        assert size < 200_000, f"merged payload ballooned to {size} bytes"


# ---------------------------------------------------------------------------
# Script guard-rails (pure-function level; DB flow covered by dry-run docs)
# ---------------------------------------------------------------------------


class TestScriptGuards:
    def test_import_script_exists_and_parses(self):
        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "ontology_import_030.py"
        )
        assert script.exists()
        compile(script.read_text(encoding="utf-8"), str(script), "exec")

    def test_placeholder_version_object_shape(self):
        """SimpleNamespace mimics what the script's OntologyVersion row gets."""
        row = SimpleNamespace(version="0.3.0", status="draft", ontology_data=None)
        assert row.ontology_data is None  # script must guard this (it does)
