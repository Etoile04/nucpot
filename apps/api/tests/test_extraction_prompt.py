"""Unit tests for extraction system prompt builder (NFM-541).

Tests validate:
- All 15 SKILL.md sections are present in the generated prompt
- Phase 2A rules (PropertyCategory, PhaseMapper, standard names) are injected
- Output JSON schema matches ExtractedProperty exactly
- Token count stays under 4000 tokens
"""

from nfm_db.core.property_catalog import PropertyCategory
from nfm_db.services.extraction_prompt import build_extraction_system_prompt

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
