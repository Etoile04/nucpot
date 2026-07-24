"""Unit tests for NucMat ontology → LightRAG extraction prompt builder.

NFM-750 — AC #2: Unit tests proving prompts contain all 5 entity types
and 10 relation types.

Entity Types (5):  Material, Property, Experiment, Condition, Publication
Relation Types (10): hasProperty, measuredIn, hasCondition, cites,
    extractsFrom, relatedTo, composedOf, produces, investigates, performedAt
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from nfm_db.services.lightrag_prompts import (
    EntityTypeRow,
    RelationTypeRow,
    build_lightrag_config,
    get_entity_extraction_prompt,
    get_relation_extraction_prompt,
)

# ---------------------------------------------------------------------------
# Immutable stubs that satisfy the Protocol interfaces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EntityType:
    """Test double implementing EntityTypeRow Protocol."""

    name: str
    label_template: str | None = None
    required_properties: list[str] | None = None
    description: str | None = None


@dataclass(frozen=True)
class _RelationType:
    """Test double implementing RelationTypeRow Protocol."""

    name: str
    source_types: list[str] | None = None
    target_types: list[str] | None = None
    properties_schema: dict | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

EXPECTED_ENTITY_NAMES = [
    "Material",
    "Property",
    "Experiment",
    "Condition",
    "Publication",
]

EXPECTED_RELATION_NAMES = [
    "hasProperty",
    "measuredIn",
    "hasCondition",
    "cites",
    "extractsFrom",
    "relatedTo",
    "composedOf",
    "produces",
    "investigates",
    "performedAt",
]


@pytest.fixture()
def entity_types() -> list[_EntityType]:
    """All 5 NucMat entity types with descriptions and templates."""
    return [
        _EntityType(
            name="Material",
            description="Nuclear fuel material or compound",
            label_template="{compound_formula}",
            required_properties=["composition", "crystal_structure"],
        ),
        _EntityType(
            name="Property",
            description="Measurable physical or chemical property",
            label_template="{property_name}={value} {unit}",
            required_properties=["value", "unit"],
        ),
        _EntityType(
            name="Experiment",
            description="Experimental study or measurement",
            label_template="{experiment_type} on {material}",
            required_properties=["method"],
        ),
        _EntityType(
            name="Condition",
            description="Experimental condition or parameter",
            label_template="{parameter}={value}",
            required_properties=["parameter", "value"],
        ),
        _EntityType(
            name="Publication",
            description="Scientific publication or report",
            label_template="{authors} ({year})",
            required_properties=["doi"],
        ),
    ]


@pytest.fixture()
def relation_types() -> list[_RelationType]:
    """All 10 NucMat relation types with source/target constraints."""
    return [
        _RelationType(
            name="hasProperty",
            description="Material has a measurable property",
            source_types=["Material"],
            target_types=["Property"],
            properties_schema={"value": {"type": "string"}, "unit": {"type": "string"}},
        ),
        _RelationType(
            name="measuredIn",
            description="Property measured in an experiment",
            source_types=["Property"],
            target_types=["Experiment"],
            properties_schema={"method": {"type": "string"}},
        ),
        _RelationType(
            name="hasCondition",
            description="Experiment performed under specific condition",
            source_types=["Experiment"],
            target_types=["Condition"],
            properties_schema={"value": {"type": "string"}},
        ),
        _RelationType(
            name="cites",
            description="Publication cites another publication",
            source_types=["Publication"],
            target_types=["Publication"],
            properties_schema=None,
        ),
        _RelationType(
            name="extractsFrom",
            description="Data extracted from a publication",
            source_types=None,
            target_types=["Publication"],
            properties_schema=None,
        ),
        _RelationType(
            name="relatedTo",
            description="Two materials are related",
            source_types=["Material"],
            target_types=["Material"],
            properties_schema=None,
        ),
        _RelationType(
            name="composedOf",
            description="Material composed of another material",
            source_types=["Material"],
            target_types=["Material"],
            properties_schema={"fraction": {"type": "number"}},
        ),
        _RelationType(
            name="produces",
            description="Experiment produces a material",
            source_types=["Experiment"],
            target_types=["Material"],
            properties_schema=None,
        ),
        _RelationType(
            name="investigates",
            description="Experiment investigates a property",
            source_types=["Experiment"],
            target_types=["Property"],
            properties_schema=None,
        ),
        _RelationType(
            name="performedAt",
            description="Experiment executed at a facility/condition",
            source_types=["Experiment"],
            target_types=["Condition"],
            properties_schema={"facility": {"type": "string"}},
        ),
    ]


# ---------------------------------------------------------------------------
# Tests: get_entity_extraction_prompt
# ---------------------------------------------------------------------------


class TestGetEntityExtractionPrompt:
    """Tests for entity extraction prompt builder."""

    def test_contains_all_five_entity_types(self, entity_types: list[_EntityType]) -> None:
        """AC #2: Prompt must name all 5 NucMat entity types."""
        prompt = get_entity_extraction_prompt(entity_types)
        for name in EXPECTED_ENTITY_NAMES:
            assert name in prompt, f"Entity type '{name}' missing from prompt"

    def test_empty_input_returns_degenerate_prompt(self) -> None:
        """Empty entity list returns a graceful fallback prompt."""
        prompt = get_entity_extraction_prompt([])
        assert "No entity types defined" in prompt
        assert "未定义实体类型" in prompt

    def test_bilingual_headers_present(self, entity_types: list[_EntityType]) -> None:
        """Prompt contains both English and Chinese instructions."""
        prompt = get_entity_extraction_prompt(entity_types)
        assert "Extract entities" in prompt
        assert "提取实体" in prompt

    def test_preserves_label_templates(self, entity_types: list[_EntityType]) -> None:
        """AC: Entity label templates must be preserved exactly."""
        prompt = get_entity_extraction_prompt(entity_types)
        assert "{compound_formula}" in prompt
        assert "{property_name}={value} {unit}" in prompt
        assert "{experiment_type} on {material}" in prompt

    def test_preserves_required_properties(self, entity_types: list[_EntityType]) -> None:
        """Required properties appear in prompt."""
        prompt = get_entity_extraction_prompt(entity_types)
        assert "composition" in prompt
        assert "crystal_structure" in prompt
        assert "method" in prompt

    def test_fallback_chinese_description_when_db_null(self) -> None:
        """Chinese fallback used when DB description is None."""
        stubs = [_EntityType(name="Material", description=None)]
        prompt = get_entity_extraction_prompt(stubs)
        assert "核燃料材料" in prompt


# ---------------------------------------------------------------------------
# Tests: get_relation_extraction_prompt
# ---------------------------------------------------------------------------


class TestGetRelationExtractionPrompt:
    """Tests for relation extraction prompt builder."""

    def test_contains_all_ten_relation_types(
        self, relation_types: list[_RelationType]
    ) -> None:
        """AC #2: Prompt must name all 10 NucMat relation types."""
        prompt = get_relation_extraction_prompt(relation_types)
        for name in EXPECTED_RELATION_NAMES:
            assert name in prompt, f"Relation type '{name}' missing from prompt"

    def test_empty_input_returns_degenerate_prompt(self) -> None:
        """Empty relation list returns a graceful fallback prompt."""
        prompt = get_relation_extraction_prompt([])
        assert "No relation types defined" in prompt

    def test_bilingual_instructions(self, relation_types: list[_RelationType]) -> None:
        """Prompt contains both English and Chinese instructions."""
        prompt = get_relation_extraction_prompt(relation_types)
        assert "Identify relations" in prompt
        assert "识别" in prompt

    def test_source_target_constraints_present(
        self, relation_types: list[_RelationType]
    ) -> None:
        """Source and target type constraints appear in prompt."""
        prompt = get_relation_extraction_prompt(relation_types)
        assert "Source types: Material" in prompt
        assert "Target types: Property" in prompt

    def test_properties_schema_serialized(
        self, relation_types: list[_RelationType]
    ) -> None:
        """JSON Schema properties are serialized into the prompt."""
        prompt = get_relation_extraction_prompt(relation_types)
        assert '"type": "string"' in prompt
        assert '"type": "number"' in prompt

    def test_null_schema_omitted_from_prompt(self) -> None:
        """Relations without schema do not show schema line."""
        stubs = [_RelationType(name="cites", properties_schema=None)]
        prompt = get_relation_extraction_prompt(stubs)
        assert "Properties JSON Schema" not in prompt

    def test_fallback_chinese_when_db_null(self) -> None:
        """Chinese fallback used when DB description is None."""
        stubs = [_RelationType(name="hasProperty", description=None)]
        prompt = get_relation_extraction_prompt(stubs)
        assert "材料具有某种性质" in prompt


# ---------------------------------------------------------------------------
# Tests: build_lightrag_config
# ---------------------------------------------------------------------------


class TestBuildLightragConfig:
    """Tests for the LightRAG config merger."""

    def test_returns_expected_keys(
        self, entity_types: list[_EntityType], relation_types: list[_RelationType]
    ) -> None:
        """Config dict has exactly the two addon_params keys."""
        config = build_lightrag_config(entity_types, relation_types)
        assert set(config.keys()) == {
            "entity_types_guidance",
            "relation_types_guidance",
        }

    def test_entity_guidance_contains_all_types(
        self, entity_types: list[_EntityType]
    ) -> None:
        """Entity guidance value contains all 5 entity types."""
        config = build_lightrag_config(entity_types, [])
        prompt = config["entity_types_guidance"]
        for name in EXPECTED_ENTITY_NAMES:
            assert name in prompt

    def test_relation_guidance_contains_all_types(
        self, relation_types: list[_RelationType]
    ) -> None:
        """Relation guidance value contains all 10 relation types."""
        config = build_lightrag_config([], relation_types)
        prompt = config["relation_types_guidance"]
        for name in EXPECTED_RELATION_NAMES:
            assert name in prompt

    def test_empty_inputs_produce_valid_config(self) -> None:
        """Empty inputs still produce a valid dict with both keys."""
        config = build_lightrag_config([], [])
        assert "entity_types_guidance" in config
        assert "relation_types_guidance" in config
        assert len(config) == 2

    def test_values_are_strings(
        self, entity_types: list[_EntityType], relation_types: list[_RelationType]
    ) -> None:
        """Both config values are strings (not None or other types)."""
        config = build_lightrag_config(entity_types, relation_types)
        assert isinstance(config["entity_types_guidance"], str)
        assert isinstance(config["relation_types_guidance"], str)


# ---------------------------------------------------------------------------
# Tests: Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """Verify test doubles satisfy the runtime-checkable Protocols."""

    def test_entity_stub_satisfies_protocol(self) -> None:
        assert isinstance(_EntityType("X"), EntityTypeRow)

    def test_relation_stub_satisfies_protocol(self) -> None:
        assert isinstance(_RelationType("X"), RelationTypeRow)
