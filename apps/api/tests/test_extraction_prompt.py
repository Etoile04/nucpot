"""Unit tests for extraction system prompt builder (NFM-541).

Tests validate:
- All 15 SKILL.md sections are present in the generated prompt
- Phase 2A rules (PropertyCategory, PhaseMapper, standard names) are injected
- Output JSON schema matches ExtractedProperty exactly
- Token count stays under 4000 tokens
- Ontology-driven extraction prompt (NFM-2639)
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

import pytest

from nfm_db.core.property_catalog import PropertyCategory
from nfm_db.services.extraction_prompt import (
    ONTOLOGY_CONTEXT_BUDGET_CHARS,
    build_extraction_system_prompt,
    build_ontology_extraction_prompt,
)

# ---------------------------------------------------------------------------
# Lightweight stand-in for OntologyVersion (avoids DB in unit tests)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeOntologyVersion:
    """Mimics OntologyVersion.ontology_data access pattern."""

    ontology_data: dict[str, Any] | None = dataclass_field(default_factory=dict)


# ---------------------------------------------------------------------------
# Shared fixtures for ontology prompt tests
# ---------------------------------------------------------------------------

_MINIMAL_ONTOLOGY: dict[str, Any] = {
    "entity_types": [
        {
            "name": "NuclearFuel",
            "description": "Base class for nuclear fuels",
            "required_properties": ["name", "composition"],
        },
    ],
    "relation_types": [
        {
            "name": "contains",
            "source_types": ["Material"],
            "target_types": ["Element"],
            "description": "Material composition relationship",
        },
    ],
}


def _large_ontology(n_entity_types: int = 50) -> dict[str, Any]:
    """Build an ontology payload that exceeds the context budget."""
    entity_types = []
    for i in range(n_entity_types):
        entity_types.append(
            {
                "name": f"EntityType_{i:03d}",
                "description": f"A verbose description for entity type {i} " * 5,
                "required_properties": [f"prop_{j}" for j in range(10)],
            }
        )
    relation_types = [
        {
            "name": f"Relation_{i:03d}",
            "source_types": [f"EntityType_{j:03d}" for j in range(3)],
            "target_types": [f"EntityType_{j:03d}" for j in range(3, 6)],
            "description": f"Relation description {i}",
        }
        for i in range(20)
    ]
    return {"entity_types": entity_types, "relation_types": relation_types}

# ---------------------------------------------------------------------------
# Section presence tests (§0 through §15)
# ---------------------------------------------------------------------------


class TestSkillSectionsPresent:
    """Validate that all 15 SKILL.md core sections are covered."""

    def setup_method(self) -> None:
        self.prompt = build_extraction_system_prompt()

    # §0: LLM semantic extraction (not regex)
    def test_section0_llm_semantic_extraction(self) -> None:
        assert "LLM" in self.prompt or "语义" in self.prompt
        assert "正则" in self.prompt or "regex" in self.prompt.lower()

    # §1: Data source specification
    def test_section1_data_source(self) -> None:
        assert "md_output" in self.prompt or "source_file" in self.prompt

    # §2: Reference rules (phase_rules, property_catalog, extraction_rules)
    def test_section2_reference_rules(self) -> None:
        assert "phase_rules" in self.prompt or "物相" in self.prompt
        assert "property_catalog" in self.prompt or "性能" in self.prompt

    # §3: Core extraction object definition
    def test_section3_core_extraction_object(self) -> None:
        assert "核材料" in self.prompt or "nuclear material" in self.prompt.lower()

    # §4: 11 property categories (9 core + 2 supporting)
    def test_section4_property_categories(self) -> None:
        for cat in PropertyCategory:
            assert cat.value in self.prompt, f"Missing category: {cat.value}"

    # §5: 13 output fields with fixed order
    def test_section5_output_fields(self) -> None:
        expected_fields = [
            "source_file",
            "material_name",
            "composition",
            "phase",
            "element",
            "property_category",
            "property",
            "value",
            "unit",
            "conditions",
            "context",
            "confidence",
            "reference",
        ]
        for field in expected_fields:
            assert field in self.prompt, f"Missing output field: {field}"

    # §6: Field type definitions and constraints
    def test_section6_field_types(self) -> None:
        # Prompt describes value as string (may use Chinese "字符串" or English)
        assert "string" in self.prompt or "字符串" in self.prompt
        assert "null" in self.prompt
        # conditions must be object
        assert "conditions" in self.prompt

    # §7: material_name/composition rules
    def test_section7_material_composition_rules(self) -> None:
        assert "material_name" in self.prompt
        assert "composition" in self.prompt
        # Key rule: don't fabricate composition
        assert "补全" in self.prompt or "fabricat" in self.prompt.lower() or "外部" in self.prompt

    # §8: conditions rules (condition_type + optional fields)
    def test_section8_conditions_rules(self) -> None:
        assert "condition_type" in self.prompt
        condition_values = [
            "experimental",
            "simulation",
            "service",
            "processing",
        ]
        for cv in condition_values:
            assert cv in self.prompt, f"Missing condition_type value: {cv}"

    # §9: Full-text scanning requirement
    def test_section9_full_text_scanning(self) -> None:
        scanning_keywords = ["全文", "扫描", "Abstract", "表格", "图注"]
        found = any(kw in self.prompt for kw in scanning_keywords)
        assert found, "Missing full-text scanning requirement"

    # §10: Multi-paper file handling
    def test_section10_multi_paper(self) -> None:
        assert (
            "REFERENCE" in self.prompt or "多论文" in self.prompt or "multi" in self.prompt.lower()
        )

    # §11: Extraction priority (4 levels)
    def test_section11_extraction_priority(self) -> None:
        priority_keywords = ["优先", "priority", "核心"]
        found = any(kw in self.prompt for kw in priority_keywords)
        assert found, "Missing extraction priority"

    # §12: Exclusion rules (7 categories)
    def test_section12_exclusion_rules(self) -> None:
        exclusion_keywords = [
            "不抽取",
            "排除",
            "exclude",
            "二手引用",
            "孤立数值",
            "定性描述",
        ]
        found = any(kw in self.prompt for kw in exclusion_keywords)
        assert found, "Missing exclusion rules"

    # §13: Output example
    def test_section13_output_example(self) -> None:
        assert "Zr" in self.prompt  # Zircaloy example from SKILL.md
        assert "氧化膜厚度" in self.prompt or "μ" in self.prompt

    # §15: Self-check gate (10 items)
    def test_section15_self_check_gate(self) -> None:
        check_keywords = [
            "自检",
            "检查",
            "self-check",
            "confidence",
        ]
        found = any(kw in self.prompt for kw in check_keywords)
        assert found, "Missing self-check gate"


# ---------------------------------------------------------------------------
# Phase 2A injection tests
# ---------------------------------------------------------------------------


class TestPhase2AInjection:
    """Validate that Phase 2A rules are injected from code, not hardcoded."""

    def setup_method(self) -> None:
        self.prompt = build_extraction_system_prompt()

    def test_all_property_categories_injected(self) -> None:
        """All 11 PropertyCategory enum values must appear."""
        for cat in PropertyCategory:
            assert cat.value in self.prompt, f"PropertyCategory.{cat.name} not injected"

    def test_standard_property_names_injected(self) -> None:
        """At least some standard property names from config must appear."""
        # Pick a representative subset — not all 100+ aliases
        representative = [
            "密度",
            "比热容",
            "热导率",
            "杨氏模量",
            "屈服强度",
            "蠕变应变",
            "氧化膜厚度",
            "硬度",
        ]
        for name in representative:
            assert name in self.prompt, f"Standard property '{name}' not injected"

    def test_phase_keywords_in_prompt(self) -> None:
        """Prompt template references canonical phase names (alpha/beta/etc.)."""
        assert "alpha" in self.prompt.lower() or "α" in self.prompt


# ---------------------------------------------------------------------------
# Schema alignment test
# ---------------------------------------------------------------------------


class TestSchemaAlignment:
    """Validate output JSON schema matches ExtractedProperty."""

    def setup_method(self) -> None:
        self.prompt = build_extraction_system_prompt()

    def test_required_fields_present(self) -> None:
        """property and value are required (no default in Pydantic model)."""
        assert "property" in self.prompt
        assert '"value"' in self.prompt or "'value'" in self.prompt
        assert '"unit"' in self.prompt or "'unit'" in self.prompt

    def test_optional_fields_nullable(self) -> None:
        """Optional fields must indicate nullable (null/default)."""
        assert "null" in self.prompt

    def test_confidence_values(self) -> None:
        """Confidence must be high/medium/low."""
        assert "high" in self.prompt
        assert "medium" in self.prompt
        assert "low" in self.prompt


# ---------------------------------------------------------------------------
# Token count test
# ---------------------------------------------------------------------------


class TestTokenBudget:
    """Validate prompt stays under 4000 tokens."""

    def test_token_count_under_limit(self) -> None:
        prompt = build_extraction_system_prompt()
        # Rough token estimate: ~4 chars per token for mixed CJK/English
        token_estimate = len(prompt) / 3.0  # CJK-heavy, ~3 chars/token
        assert token_estimate < 4000, (
            f"Prompt too large: ~{token_estimate:.0f} tokens ({len(prompt)} chars)"
        )


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


class TestPromptStructure:
    """Validate prompt structure and JSON mode specification."""

    def setup_method(self) -> None:
        self.prompt = build_extraction_system_prompt()

    def test_returns_string(self) -> None:
        assert isinstance(self.prompt, str)
        assert len(self.prompt) > 500, "Prompt suspiciously short"

    def test_json_mode_specified(self) -> None:
        assert "JSON" in self.prompt

    def test_has_output_example(self) -> None:
        """Must contain a concrete JSON example."""
        assert "{" in self.prompt and "}" in self.prompt
        # Should have at least one string value in quotes
        assert '"' in self.prompt


# ---------------------------------------------------------------------------
# Ontology-driven extraction prompt tests (NFM-2639)
# ---------------------------------------------------------------------------


class TestBuildOntologyExtractionPrompt:
    """Validate build_ontology_extraction_prompt() against AC."""

    def test_returns_string(self) -> None:
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        assert isinstance(prompt, str)
        assert len(prompt) > 500

    def test_contains_entity_types(self) -> None:
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        assert "NuclearFuel" in prompt

    def test_contains_relation_types(self) -> None:
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        assert "contains" in prompt

    def test_ontology_context_block_injected(self) -> None:
        """Ontology context block must appear between output format and field rules."""
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        # Verify the ontology section heading exists
        assert "本体" in prompt or "Ontology" in prompt

    def test_fallback_still_works(self) -> None:
        """Existing build_extraction_system_prompt() must remain unchanged."""
        prompt = build_extraction_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 500
        # Must contain key sections from original template
        assert "property_category" in prompt
        assert "JSON" in prompt

    def test_exported_in_all(self) -> None:
        """build_ontology_extraction_prompt must be importable."""
        from nfm_db.services.extraction_prompt import __all__

        assert "build_ontology_extraction_prompt" in __all__

    def test_entity_descriptions_included(self) -> None:
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        assert "Base class for nuclear fuels" in prompt

    def test_required_properties_included(self) -> None:
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        assert "name" in prompt
        assert "composition" in prompt

    def test_relation_constraints_included(self) -> None:
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        assert "Material" in prompt
        assert "Element" in prompt


class TestOntologyContextBudget:
    """Validate token budget enforcement and truncation."""

    def test_budget_constant_is_positive(self) -> None:
        assert ONTOLOGY_CONTEXT_BUDGET_CHARS > 0

    def test_truncation_when_over_budget(self, caplog: pytest.LogCaptureFixture) -> None:
        """Large ontology must be truncated and log a warning."""
        large_data = _large_ontology(n_entity_types=50)
        ov = _FakeOntologyVersion(ontology_data=large_data)
        prompt = build_ontology_extraction_prompt(ov)

        # Must still return a valid prompt
        assert isinstance(prompt, str)
        assert len(prompt) > 500

        # A warning about truncation must be logged
        truncation_warnings = [
            r for r in caplog.records if "truncat" in r.message.lower()
        ]
        assert len(truncation_warnings) >= 1, (
            "Expected a truncation warning for oversized ontology data"
        )

    def test_entity_names_survive_truncation(self) -> None:
        """Even when truncated, entity type names must be present."""
        large_data = _large_ontology(n_entity_types=50)
        ov = _FakeOntologyVersion(ontology_data=large_data)
        prompt = build_ontology_extraction_prompt(ov)
        # At least the first few entity names should survive
        assert "EntityType_000" in prompt

    def test_context_block_within_budget(self) -> None:
        """Minimal ontology context must fit within budget."""
        ov = _FakeOntologyVersion(ontology_data=_MINIMAL_ONTOLOGY)
        prompt = build_ontology_extraction_prompt(ov)
        # The ontology context block portion should be within budget
        # We can't easily isolate it, but the full prompt should be reasonable
        token_estimate = len(prompt) / 3.0
        assert token_estimate < 6000, (
            f"Prompt too large: ~{token_estimate:.0f} tokens ({len(prompt)} chars)"
        )


class TestOntologyExtractionNullSafety:
    """Edge cases for ontology data."""

    def test_none_ontology_data(self) -> None:
        """None ontology_data should produce prompt without ontology section."""
        ov = _FakeOntologyVersion(ontology_data=None)
        prompt = build_ontology_extraction_prompt(ov)
        assert isinstance(prompt, str)
        # Should still have the base prompt sections
        assert "property_category" in prompt

    def test_empty_ontology_data(self) -> None:
        """Empty ontology_data should produce prompt without ontology section."""
        ov = _FakeOntologyVersion(ontology_data={})
        prompt = build_ontology_extraction_prompt(ov)
        assert isinstance(prompt, str)
        assert "property_category" in prompt

    def test_missing_entity_types_key(self) -> None:
        """Missing entity_types key should not crash."""
        ov = _FakeOntologyVersion(ontology_data={"relation_types": []})
        prompt = build_ontology_extraction_prompt(ov)
        assert isinstance(prompt, str)

    def test_missing_relation_types_key(self) -> None:
        """Missing relation_types key should not crash."""
        ov = _FakeOntologyVersion(ontology_data={"entity_types": []})
        prompt = build_ontology_extraction_prompt(ov)
        assert isinstance(prompt, str)


# ---------------------------------------------------------------------------
# NFM-3004: Ontology-driven categories + standard names tests
# ---------------------------------------------------------------------------


class TestOntologyDrivenCategoriesBlock:
    """AC-1: categories_block generated from ontology entity_types."""

    def test_categories_from_ontology_entity_types(self) -> None:
        """When ontology_data has entity_types, categories must come from them."""
        ontology_data = {
            "entity_types": [
                {"name": "NuclearFuel", "required_properties": ["name"]},
                {"name": "Cladding", "required_properties": ["material"]},
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)

        # Entity type names must appear in the categories section
        assert "NuclearFuel" in prompt
        assert "Cladding" in prompt

    def test_categories_not_from_property_category_enum(self) -> None:
        """Categories block should NOT contain hardcoded enum values when ontology has entity_types."""
        ontology_data = {
            "entity_types": [
                {"name": "CustomMaterial", "required_properties": ["density"]},
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)

        # The prompt should contain CustomMaterial
        assert "CustomMaterial" in prompt
        # The categories section should be entity type names, not PropertyCategory values
        categories_section_start = prompt.find("## Property Categories")
        if categories_section_start != -1:
            categories_section_end = prompt.find("\n\n", categories_section_start)
            if categories_section_end == -1:
                categories_section_end = len(prompt)
            categories_section = prompt[categories_section_start:categories_section_end]
            # PropertyCategory enum has "腐蚀" but our ontology doesn't
            assert "腐蚀" not in categories_section

    def test_deduplicated_entity_type_names(self) -> None:
        """Duplicate entity type names must be deduplicated."""
        ontology_data = {
            "entity_types": [
                {"name": "Fuel", "required_properties": ["a"]},
                {"name": "Fuel", "required_properties": ["b"]},
                {"name": "Cladding", "required_properties": ["c"]},
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)
        # "Fuel" should appear exactly once in categories section
        categories_start = prompt.find("## Property Categories")
        categories_end = prompt.find("\n## Standard Property Names", categories_start)
        section = prompt[categories_start:categories_end]
        assert section.count("Fuel") == 1


class TestOntologyDrivenStandardNamesBlock:
    """AC-2: standard_names_block generated from ontology required_properties."""

    def test_standard_names_from_ontology_properties(self) -> None:
        """When ontology_data has required_properties, names must come from them."""
        ontology_data = {
            "entity_types": [
                {
                    "name": "NuclearFuel",
                    "required_properties": ["thermal_conductivity", "density", "melting_point"],
                },
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)

        assert "thermal_conductivity" in prompt
        assert "density" in prompt
        assert "melting_point" in prompt

    def test_standard_names_not_from_hardcoded_dict(self) -> None:
        """Standard names should come from ontology, not STANDARD_PROPERTIES."""
        ontology_data = {
            "entity_types": [
                {
                    "name": "CustomType",
                    "required_properties": ["custom_prop_x"],
                },
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)

        assert "custom_prop_x" in prompt
        # 标准名称 from STANDARD_PROPERTIES like "密度" should NOT appear
        # in the standard names section (it's not in our ontology)
        sn_start = prompt.find("## Standard Property Names")
        sn_end = prompt.find("\n## 字段规则", sn_start)
        if sn_start != -1 and sn_end != -1:
            section = prompt[sn_start:sn_end]
            assert "密度" not in section

    def test_deduplicated_property_names_across_entity_types(self) -> None:
        """Property names shared across entity types must be deduplicated."""
        ontology_data = {
            "entity_types": [
                {"name": "TypeA", "required_properties": ["temp", "pressure"]},
                {"name": "TypeB", "required_properties": ["temp", "stress"]},
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)

        sn_start = prompt.find("## Standard Property Names")
        sn_end = prompt.find("\n## 字段规则", sn_start)
        section = prompt[sn_start:sn_end]
        assert section.count("temp") == 1
        assert "pressure" in section
        assert "stress" in section

    def test_sorted_property_names(self) -> None:
        """Property names must be sorted for determinism."""
        ontology_data = {
            "entity_types": [
                {
                    "name": "TypeA",
                    "required_properties": ["zebra", "alpha", "mid"],
                },
            ],
        }
        ov = _FakeOntologyVersion(ontology_data=ontology_data)
        prompt = build_ontology_extraction_prompt(ov)

        sn_start = prompt.find("## Standard Property Names")
        sn_end = prompt.find("\n## 字段规则", sn_start)
        section = prompt[sn_start:sn_end]
        alpha_pos = section.find("- alpha")
        mid_pos = section.find("- mid")
        zebra_pos = section.find("- zebra")
        assert alpha_pos < mid_pos < zebra_pos


class TestOntologyDrivenFallback:
    """AC-4: Fallback to hardcoded when no ontology."""

    def test_none_ontology_uses_hardcoded_categories(self) -> None:
        """None ontology_data should fall back to PropertyCategory enum."""
        ov = _FakeOntologyVersion(ontology_data=None)
        prompt = build_ontology_extraction_prompt(ov)

        for cat in PropertyCategory:
            assert cat.value in prompt, f"Missing fallback category: {cat.value}"

    def test_empty_ontology_uses_hardcoded_categories(self) -> None:
        """Empty ontology_data should fall back to PropertyCategory enum."""
        ov = _FakeOntologyVersion(ontology_data={})
        prompt = build_ontology_extraction_prompt(ov)

        for cat in PropertyCategory:
            assert cat.value in prompt, f"Missing fallback category: {cat.value}"

    def test_none_ontology_uses_hardcoded_standard_names(self) -> None:
        """None ontology_data should fall back to STANDARD_PROPERTIES."""
        ov = _FakeOntologyVersion(ontology_data=None)
        prompt = build_ontology_extraction_prompt(ov)

        assert "密度" in prompt
        assert "热导率" in prompt

    def test_empty_entity_types_uses_hardcoded_categories(self) -> None:
        """Empty entity_types list should fall back to hardcoded categories."""
        ov = _FakeOntologyVersion(ontology_data={"entity_types": []})
        prompt = build_ontology_extraction_prompt(ov)

        for cat in PropertyCategory:
            assert cat.value in prompt, f"Missing fallback category: {cat.value}"

    def test_empty_required_properties_uses_hardcoded_names(self) -> None:
        """Entity types with no required_properties should fall back to hardcoded names."""
        ov = _FakeOntologyVersion(ontology_data={
            "entity_types": [{"name": "TypeA", "required_properties": []}]
        })
        prompt = build_ontology_extraction_prompt(ov)

        assert "密度" in prompt


class TestV1PathUntouched:
    """AC-3: V1 build_extraction_system_prompt() continues using hardcoded helpers."""

    def test_v1_uses_hardcoded_categories(self) -> None:
        prompt = build_extraction_system_prompt()
        for cat in PropertyCategory:
            assert cat.value in prompt, f"V1 missing category: {cat.value}"

    def test_v1_uses_hardcoded_standard_names(self) -> None:
        prompt = build_extraction_system_prompt()
        assert "密度" in prompt
        assert "热导率" in prompt
        assert "杨氏模量" in prompt

    def test_v1_no_ontology_context(self) -> None:
        prompt = build_extraction_system_prompt()
        assert "本体" not in prompt and "Ontology" not in prompt


class TestOntologyDrivenImportPresence:
    """AC-5: Imports of PropertyCategory/STANDARD_PROPERTIES remain (used by V1/fallback)."""

    def test_property_category_import_still_present(self) -> None:
        """PropertyCategory must still be importable (V1 path uses it)."""
        from nfm_db.core.property_catalog import PropertyCategory as PC
        assert len(list(PC)) >= 11

    def test_standard_properties_import_still_present(self) -> None:
        """STANDARD_PROPERTIES must still be importable (V1 path uses it)."""
        from nfm_db.core.property_catalog import STANDARD_PROPERTIES as SP
        assert len(SP) > 0
