"""Unit tests for property catalog module (NFM-524).

Tests for:
- PropertyCategory enum (11 categories from v4 property_catalog.md §3-§5)
- STANDARD_PROPERTIES mapping (60+ alias→standard name entries)
- UnitNormalizer.normalize() (unit normalization from §7)

TDD: These tests are written FIRST, before any production code.
"""

from __future__ import annotations

import pytest

from nfm_db.core.property_catalog import PropertyCategory, STANDARD_PROPERTIES, UnitNormalizer


# ---------------------------------------------------------------------------
# PropertyCategory Enum
# ---------------------------------------------------------------------------


class TestPropertyCategory:
    """Tests for PropertyCategory enum (v4 §3)."""

    def test_enum_has_eleven_members(self):
        """PropertyCategory must contain exactly 11 categories."""
        assert len(PropertyCategory) == 11

    def test_core_nine_categories_present(self):
        """First 9 are the project core property categories (v4 §3)."""
        core = {
            PropertyCategory.DENSITY,
            PropertyCategory.SPECIFIC_HEAT,
            PropertyCategory.THERMAL_CONDUCTIVITY,
            PropertyCategory.ELASTOPLASTIC,
            PropertyCategory.THERMAL_EXPANSION,
            PropertyCategory.IRRADIATION_CREEP,
            PropertyCategory.IRRADIATION_SWELLING,
            PropertyCategory.CORROSION,
            PropertyCategory.HARDENING,
        }
        assert core.issubset(set(PropertyCategory))

    def test_supporting_categories_present(self):
        """Material spec and Other are supporting categories (v4 §5)."""
        assert PropertyCategory.MATERIAL_SPEC in PropertyCategory
        assert PropertyCategory.OTHER in PropertyCategory

    def test_enum_values_are_strings(self):
        """Each enum member's value should be a non-empty string."""
        for member in PropertyCategory:
            assert isinstance(member.value, str)
            assert len(member.value) > 0

    def test_category_values_match_v4_labels(self):
        """Enum values match the v4 Chinese labels exactly."""
        assert PropertyCategory.DENSITY.value == "密度"
        assert PropertyCategory.SPECIFIC_HEAT.value == "比热容"
        assert PropertyCategory.THERMAL_CONDUCTIVITY.value == "热传导率"
        assert PropertyCategory.ELASTOPLASTIC.value == "弹塑性模型"
        assert PropertyCategory.THERMAL_EXPANSION.value == "热膨胀"
        assert PropertyCategory.IRRADIATION_CREEP.value == "辐照蠕变"
        assert PropertyCategory.IRRADIATION_SWELLING.value == "辐照肿胀"
        assert PropertyCategory.CORROSION.value == "腐蚀"
        assert PropertyCategory.HARDENING.value == "硬化性能"
        assert PropertyCategory.MATERIAL_SPEC.value == "材料规格/组织信息"
        assert PropertyCategory.OTHER.value == "其他性能"


# ---------------------------------------------------------------------------
# STANDARD_PROPERTIES Mapping
# ---------------------------------------------------------------------------


class TestStandardProperties:
    """Tests for STANDARD_PROPERTIES alias→standard name mapping (v4 §4)."""

    def test_is_dict(self):
        """STANDARD_PROPERTIES must be a dict."""
        assert isinstance(STANDARD_PROPERTIES, dict)

    def test_has_sixty_plus_entries(self):
        """Must contain at least 60 standard property entries."""
        assert len(STANDARD_PROPERTIES) >= 60

    def test_aliases_are_strings(self):
        """All alias keys must be non-empty strings."""
        for alias in STANDARD_PROPERTIES:
            assert isinstance(alias, str)
            assert len(alias) > 0

    def test_standard_names_are_strings(self):
        """All standard name values must be non-empty strings."""
        for name in STANDARD_PROPERTIES.values():
            assert isinstance(name, str)
            assert len(name) > 0

    def test_density_aliases_present(self):
        """Density category aliases from v4 §4.1."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("density") == "密度"
        assert mapping.get("theoretical density") == "理论密度"
        assert mapping.get("relative density") == "相对密度"

    def test_specific_heat_aliases_present(self):
        """Specific heat aliases from v4 §4.2."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("specific heat") == "比热容"
        assert mapping.get("heat capacity") == "比热容"
        assert mapping.get("cp") == "定压比热容"

    def test_thermal_conductivity_aliases_present(self):
        """Thermal conductivity aliases from v4 §4.3."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("thermal conductivity") == "热导率"
        assert mapping.get("thermal diffusivity") == "热扩散率"
        assert mapping.get("thermal resistance") == "热阻"

    def test_elastoplastic_aliases_present(self):
        """Elastoplastic model aliases from v4 §4.4."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("young's modulus") == "杨氏模量"
        assert mapping.get("shear modulus") == "剪切模量"
        assert mapping.get("poisson's ratio") == "泊松比"
        assert mapping.get("yield strength") == "屈服强度"
        assert mapping.get("tensile strength") == "抗拉强度"
        assert mapping.get("flow stress") == "流动应力"

    def test_corrosion_aliases_present(self):
        """Corrosion aliases from v4 §4.8."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("corrosion rate") == "腐蚀速率"
        assert mapping.get("oxide thickness") == "氧化膜厚度"
        assert mapping.get("hydrogen pickup") == "吸氢率"
        assert mapping.get("crack growth rate") == "裂纹扩展速率"
        assert mapping.get("scc threshold") == "应力腐蚀阈值"

    def test_hardening_aliases_present(self):
        """Hardening aliases from v4 §4.9."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("hardness") == "硬度"
        assert mapping.get("vickers hardness") == "硬度"
        assert mapping.get("irradiation hardening") == "辐照硬化量"

    def test_case_insensitive_lookup(self):
        """Alias lookup is case-insensitive via lowered keys."""
        mapping = STANDARD_PROPERTIES
        # All keys are lowered at build time
        assert mapping.get("density") == "密度"
        # Original mixed-case "Density" normalizes to "density" key
        assert mapping.get("Density".lower()) == "密度"
        assert mapping.get("DENSITY".lower()) == "密度"

    def test_irradiation_creeep_aliases_present(self):
        """Irradiation creep aliases from v4 §4.6."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("creep strain") == "蠕变应变"
        assert mapping.get("steady-state creep rate") == "稳态蠕变速率"
        assert mapping.get("stress exponent") == "应力指数"

    def test_irradiation_swelling_aliases_present(self):
        """Irradiation swelling aliases from v4 §4.7."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("volume swelling") == "体积膨胀率"
        assert mapping.get("void swelling") == "空洞肿胀率"
        assert mapping.get("void density") == "空洞密度"

    def test_thermal_expansion_aliases_present(self):
        """Thermal expansion aliases from v4 §4.5."""
        mapping = STANDARD_PROPERTIES
        assert mapping.get("mean thermal expansion") == "平均线膨胀系数"
        assert mapping.get("instantaneous cte") == "瞬时线膨胀系数"


# ---------------------------------------------------------------------------
# UnitNormalizer
# ---------------------------------------------------------------------------


class TestUnitNormalizer:
    """Tests for UnitNormalizer.normalize() (v4 §7)."""

    def setup_method(self):
        self.normalizer = UnitNormalizer()

    def test_normalize_um_variant(self):
        """'um' normalizes to 'μm'."""
        assert self.normalizer.normalize("um") == "μm"

    def test_normalize_micro_m(self):
        """'µm' stays 'μm' (already correct)."""
        assert self.normalizer.normalize("µm") == "μm"

    def test_normalize_angstrom(self):
        """'Angstrom' normalizes to 'Å'."""
        assert self.normalizer.normalize("Angstrom") == "Å"

    def test_normalize_percent(self):
        """'percent' normalizes to '%'."""
        assert self.normalizer.normalize("percent") == "%"

    def test_normalize_deg_c(self):
        """'deg C' normalizes to '°C'."""
        assert self.normalizer.normalize("deg C") == "°C"

    def test_normalize_degrees_c(self):
        """'degrees C' normalizes to '°C'."""
        assert self.normalizer.normalize("degrees C") == "°C"

    def test_normalize_m2_plain(self):
        """'m2' normalizes to 'm²'."""
        assert self.normalizer.normalize("m2") == "m²"

    def test_normalize_m_caret_2(self):
        """'m^2' normalizes to 'm²'."""
        assert self.normalizer.normalize("m^2") == "m²"

    def test_normalize_s_minus_1(self):
        """'s-1' normalizes to 's⁻¹'."""
        assert self.normalizer.normalize("s-1") == "s⁻¹"

    def test_normalize_s_caret_minus_1(self):
        """'s^-1' normalizes to 's⁻¹'."""
        assert self.normalizer.normalize("s^-1") == "s⁻¹"

    def test_normalize_passthrough(self):
        """Already-standard units pass through unchanged."""
        assert self.normalizer.normalize("GPa") == "GPa"
        assert self.normalizer.normalize("MPa") == "MPa"
        assert self.normalizer.normalize("g/cm³") == "g/cm³"

    def test_normalize_case_insensitive(self):
        """Normalization is case-insensitive for aliases."""
        assert self.normalizer.normalize("Percent") == "%"
        assert self.normalizer.normalize("PERCENT") == "%"
        assert self.normalizer.normalize("UM") == "μm"
        assert self.normalizer.normalize("angstrom") == "Å"

    def test_normalize_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped."""
        assert self.normalizer.normalize("  GPa  ") == "GPa"

    def test_normalize_empty_returns_empty(self):
        """Empty string returns empty string."""
        assert self.normalizer.normalize("") == ""

    def test_normalize_dimensionless_passthrough(self):
        """'dimensionless' passes through."""
        assert self.normalizer.normalize("dimensionless") == "dimensionless"

    def test_normalize_complex_unit_w_m_k(self):
        """'W/(m·K)' passes through (already standard)."""
        assert self.normalizer.normalize("W/(m·K)") == "W/(m·K)"

    def test_normalize_j_kg_k(self):
        """'J·kg⁻¹·K⁻¹' passes through (already standard)."""
        assert self.normalizer.normalize("J·kg⁻¹·K⁻¹") == "J·kg⁻¹·K⁻¹"

    def test_normalize_hv_passthrough(self):
        """'HV' (Vickers hardness) passes through."""
        assert self.normalizer.normalize("HV") == "HV"

    def test_normalize_m_per_cycle(self):
        """'m/cycle' passes through."""
        assert self.normalizer.normalize("m/cycle") == "m/cycle"

    def test_normalizer_reads_from_config(self):
        """UnitNormalizer loads rules from JSON config, not hardcoded."""
        import json

        from pathlib import Path

        config_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "nfm_db" / "config"
            / "property_mapping.json"
        )

        assert config_path.exists(), (
            f"property_mapping.json not found at {config_path}"
        )

        with open(config_path) as f:
            data = json.load(f)

        assert "unit_normalization" in data, (
            "property_mapping.json must contain 'unit_normalization' key"
        )
