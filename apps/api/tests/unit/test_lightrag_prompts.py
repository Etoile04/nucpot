"""Unit tests for lightrag_prompts — NucMat ontology → LightRAG extraction prompts.

TDD RED phase: tests define required behavior before implementation.

Acceptance criteria (from NFM-750):
1. get_entity_extraction_prompt() returns system prompt with all 5 entity types
2. get_relation_extraction_prompt() returns prompt with all 10 relation types
3. build_lightrag_config() returns dict merging ontology prompts into LightRAG config
4. Prompts are bilingual (Chinese AND English)
5. Entity label templates preserved exactly from ontology
6. Ontology content loaded from tables, not hardcoded
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from nfm_db.services.lightrag_prompts import (
    build_lightrag_config,
    get_entity_extraction_prompt,
    get_relation_extraction_prompt,
)

# ---------------------------------------------------------------------------
# Stubs mimicking KEntityType / KRelationType ORM rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeEntityType:
    """Minimal stub matching KEntityType fields used by prompt builders."""

    name: str
    label_template: str | None = None
    required_properties: list[str] | None = None
    description: str | None = None


@dataclass(frozen=True)
class FakeRelationType:
    """Minimal stub matching KRelationType fields used by prompt builders."""

    name: str
    source_types: list[str] | None = None
    target_types: list[str] | None = None
    properties_schema: dict | None = None
    description: str | None = None


# ---------------------------------------------------------------------------
# Seed data matching migration 011 exactly
# ---------------------------------------------------------------------------

SEED_ENTITY_TYPES: list[FakeEntityType] = [
    FakeEntityType(
        name="Material",
        label_template="{name}",
        required_properties=["name", "chemical_formula"],
        description="A nuclear fuel material or compound",
    ),
    FakeEntityType(
        name="Property",
        label_template="{name} ({unit})",
        required_properties=["name", "unit"],
        description="A measurable physical or chemical property",
    ),
    FakeEntityType(
        name="Experiment",
        label_template="{name} ({year})",
        required_properties=["name", "method"],
        description="An experimental investigation or measurement",
    ),
    FakeEntityType(
        name="Condition",
        label_template="{name}: {value} {unit}",
        required_properties=["name", "value", "unit"],
        description="An experimental condition or parameter",
    ),
    FakeEntityType(
        name="Publication",
        label_template="{title} ({year})",
        required_properties=["title", "authors", "year"],
        description="A scientific publication or report",
    ),
]

SEED_RELATION_TYPES: list[FakeRelationType] = [
    FakeRelationType(
        name="hasProperty",
        source_types=["Material"],
        target_types=["Property"],
        properties_schema={
            "type": "object",
            "properties": {
                "value": {"type": ["number", "string"]},
                "unit": {"type": "string"},
                "temperature": {"type": ["number", "string"]},
                "notes": {"type": "string"},
            },
        },
        description="A material possesses a specific property",
    ),
    FakeRelationType(
        name="measuredIn",
        source_types=["Experiment"],
        target_types=["Material"],
        properties_schema={
            "type": "object",
            "properties": {
                "method": {"type": "string"},
                "equipment": {"type": "string"},
            },
        },
        description="An experiment measured a material",
    ),
    FakeRelationType(
        name="hasCondition",
        source_types=["Experiment"],
        target_types=["Condition"],
        properties_schema={
            "type": "object",
            "properties": {
                "value": {"type": ["number", "string"]},
                "unit": {"type": "string"},
            },
        },
        description="An experiment was performed under a condition",
    ),
    FakeRelationType(
        name="cites",
        source_types=["Publication"],
        target_types=["Publication"],
        properties_schema={
            "type": "object",
            "properties": {
                "context": {"type": "string"},
            },
        },
        description="A publication cites another publication",
    ),
    FakeRelationType(
        name="extractsFrom",
        source_types=["Experiment"],
        target_types=["Publication"],
        properties_schema={
            "type": "object",
            "properties": {
                "sections": {"type": "array", "items": {"type": "string"}},
            },
        },
        description="Data extracted from a publication during an experiment",
    ),
    FakeRelationType(
        name="relatedTo",
        source_types=["Material"],
        target_types=["Material"],
        properties_schema={
            "type": "object",
            "properties": {
                "relationship": {"type": "string"},
            },
        },
        description="Two materials are related",
    ),
    FakeRelationType(
        name="composedOf",
        source_types=["Material"],
        target_types=["Material"],
        properties_schema={
            "type": "object",
            "properties": {
                "fraction": {"type": "number"},
                "role": {"type": "string"},
            },
        },
        description="A material is composed of another material",
    ),
    FakeRelationType(
        name="produces",
        source_types=["Experiment"],
        target_types=["Material"],
        properties_schema={
            "type": "object",
            "properties": {
                "yield": {"type": "string"},
                "purity": {"type": "string"},
            },
        },
        description="An experiment produces a material",
    ),
    FakeRelationType(
        name="investigates",
        source_types=["Experiment"],
        target_types=["Property"],
        properties_schema={
            "type": "object",
            "properties": {
                "method": {"type": "string"},
            },
        },
        description="An experiment investigates a property",
    ),
    FakeRelationType(
        name="performedAt",
        source_types=["Experiment"],
        target_types=["Condition"],
        properties_schema={
            "type": "object",
            "properties": {
                "duration": {"type": "string"},
                "sequence": {"type": "integer"},
            },
        },
        description="An experiment was performed at a specific condition",
    ),
]

EXPECTED_ENTITY_NAMES = {
    "Material",
    "Property",
    "Experiment",
    "Condition",
    "Publication",
}

EXPECTED_RELATION_NAMES = {
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
}


# ---------------------------------------------------------------------------
# Tests: get_entity_extraction_prompt
# ---------------------------------------------------------------------------


class TestGetEntityExtractionPrompt:
    """Tests for get_entity_extraction_prompt()."""

    def test_returns_string(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert isinstance(prompt, str)

    @pytest.mark.parametrize("name", EXPECTED_ENTITY_NAMES)
    def test_contains_all_entity_types(self, name: str) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert name in prompt, f"Missing entity type '{name}' in prompt"

    def test_contains_material_label_template(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "{name}" in prompt, "Material label template '{name}' not preserved"

    def test_contains_property_label_template(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "{name} ({unit})" in prompt, "Property label template not preserved"

    def test_contains_experiment_label_template(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "{name} ({year})" in prompt, "Experiment label template not preserved"

    def test_contains_condition_label_template(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "{name}: {value} {unit}" in prompt, "Condition label template not preserved"

    def test_contains_publication_label_template(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "{title} ({year})" in prompt, "Publication label template not preserved"

    def test_contains_material_description(self) -> None:
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "nuclear fuel material" in prompt.lower() or "核燃料材料" in prompt

    def test_is_bilingual_chinese(self) -> None:
        """Prompt must contain Chinese terms for Chinese papers."""
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        # At least one Chinese term present
        assert any(
            term in prompt for term in ("材料", "性质", "实验", "条件", "出版物", "核燃料")
        ), "Prompt missing Chinese entity type terms"

    def test_is_bilingual_english(self) -> None:
        """Prompt must contain English terms."""
        prompt = get_entity_extraction_prompt(SEED_ENTITY_TYPES)
        assert "Material" in prompt
        assert "Property" in prompt

    def test_empty_list_returns_minimal_prompt(self) -> None:
        prompt = get_entity_extraction_prompt([])
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_entity_count_matches_input(self) -> None:
        extra = list(SEED_ENTITY_TYPES)
        prompt = get_entity_extraction_prompt(extra)
        for et in extra:
            assert et.name in prompt


# ---------------------------------------------------------------------------
# Tests: get_relation_extraction_prompt
# ---------------------------------------------------------------------------


class TestGetRelationExtractionPrompt:
    """Tests for get_relation_extraction_prompt()."""

    def test_returns_string(self) -> None:
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert isinstance(prompt, str)

    @pytest.mark.parametrize("name", EXPECTED_RELATION_NAMES)
    def test_contains_all_relation_types(self, name: str) -> None:
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert name in prompt, f"Missing relation type '{name}' in prompt"

    def test_has_property_source_target(self) -> None:
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert "Material" in prompt
        assert "Property" in prompt
        # hasProperty: Material → Property
        assert "hasProperty" in prompt

    def test_measured_in_source_target(self) -> None:
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert "measuredIn" in prompt
        assert "Experiment" in prompt

    def test_cites_source_target(self) -> None:
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert "cites" in prompt
        assert "Publication" in prompt

    def test_contains_properties_schema_reference(self) -> None:
        """Prompt should reference JSON Schema for relation properties."""
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert "JSON" in prompt or "json" in prompt or "schema" in prompt.lower()

    def test_is_bilingual_chinese(self) -> None:
        """Prompt must contain Chinese terms."""
        prompt = get_relation_extraction_prompt(SEED_RELATION_TYPES)
        assert any(
            term in prompt
            for term in (
                "关系",
                "属性",
                "材料",
                "测量",
                "引用",
                "实验",
                "核燃料",
            )
        ), "Prompt missing Chinese relation type terms"

    def test_empty_list_returns_minimal_prompt(self) -> None:
        prompt = get_relation_extraction_prompt([])
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_relation_count_matches_input(self) -> None:
        extra = list(SEED_RELATION_TYPES)
        prompt = get_relation_extraction_prompt(extra)
        for rt in extra:
            assert rt.name in prompt


# ---------------------------------------------------------------------------
# Tests: build_lightrag_config
# ---------------------------------------------------------------------------


class TestBuildLightRAGConfig:
    """Tests for build_lightrag_config()."""

    def test_returns_dict(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        assert isinstance(config, dict)

    def test_has_entity_types_guidance_key(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        assert "entity_types_guidance" in config

    def test_guidance_contains_all_entity_types(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        guidance = config["entity_types_guidance"]
        for name in EXPECTED_ENTITY_NAMES:
            assert name in guidance, f"Missing '{name}' in entity_types_guidance"

    def test_guidance_is_nonempty(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        assert len(config["entity_types_guidance"]) > 50

    def test_guidance_is_bilingual(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        guidance = config["entity_types_guidance"]
        # Contains at least one Chinese term
        assert any(term in guidance for term in ("材料", "性质", "实验", "条件", "出版物"))
        # Contains English term
        assert "Material" in guidance

    def test_has_relation_types_guidance_key(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        assert "relation_types_guidance" in config

    def test_relation_guidance_contains_all_relation_types(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        guidance = config["relation_types_guidance"]
        for name in EXPECTED_RELATION_NAMES:
            assert name in guidance, f"Missing '{name}' in relation_types_guidance"

    def test_relation_guidance_is_bilingual(self) -> None:
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        guidance = config["relation_types_guidance"]
        assert any(term in guidance for term in ("关系", "测量", "引用", "实验", "材料"))

    def test_empty_inputs_return_minimal_config(self) -> None:
        config = build_lightrag_config([], [])
        assert isinstance(config, dict)
        assert "entity_types_guidance" in config
        assert "relation_types_guidance" in config

    def test_config_is_mergeable_with_lightrag_addon_params(self) -> None:
        """Config dict can be spread into LightRAG addon_params."""
        config = build_lightrag_config(
            SEED_ENTITY_TYPES,
            SEED_RELATION_TYPES,
        )
        # Simulate merging with other addon params
        addon_params: dict[str, Any] = {"language": "English"}
        addon_params.update(config)
        assert addon_params["language"] == "English"
        assert "entity_types_guidance" in addon_params
