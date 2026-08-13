"""Unit tests for PhaseMapper — three-step inference + 20+ standard phase mapping.

TDD RED phase: these tests define the required behavior of PhaseMapper
before any implementation exists.

Coverage:
- Step 1: Direct alias→canonical mapping
- Step 2: Material-based phase inference
- Step 3: Context clue extraction
- LaTeX cleaning
- None returns when inference fails
- 20+ standard mapping coverage
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nfm_db.core.phase_rules import PhaseMapper

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PHASE_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent / "src" / "nfm_db" / "config" / "phase_mapping.json"
)


@pytest.fixture
def phase_mapper() -> PhaseMapper:
    """PhaseMapper loaded from the real JSON config."""
    return PhaseMapper.from_config(_PHASE_MAPPING_PATH)


@pytest.fixture
def phase_mapper_from_dict() -> PhaseMapper:
    """PhaseMapper loaded from an inline dict (for isolation)."""
    config = {
        "mappings": [
            {"canonical": "alpha", "category": "matrix", "aliases": ["α", "alpha", "alpha phase"]},
            {"canonical": "beta", "category": "matrix", "aliases": ["β", "beta"]},
            {"canonical": "delta-hydride", "category": "hydride", "aliases": ["δ-hydride"]},
        ],
        "laTeX_patterns": [
            {"pattern": r"$\\alpha$", "replacement": "alpha"},
            {"pattern": r"$\\delta$", "replacement": "delta"},
        ],
        "not_phase": ["beta-quenched", "cold-worked"],
    }
    return PhaseMapper.from_dict(config)


# ---------------------------------------------------------------------------
# Step 1: Direct mapping (alias→canonical)
# ---------------------------------------------------------------------------


class TestStep1DirectMapping:
    """Step 1: raw_phase found in standard mapping table → return canonical."""

    def test_ascii_alias_returns_canonical(self, phase_mapper: PhaseMapper) -> None:
        """'alpha' → 'alpha' (self-mapping)."""
        assert phase_mapper.infer_phase("alpha", None, None) == "alpha"

    def test_greek_alpha_returns_canonical(self, phase_mapper: PhaseMapper) -> None:
        """'α' → 'alpha'."""
        assert phase_mapper.infer_phase("α", None, None) == "alpha"

    def test_alpha_phase_phrase(self, phase_mapper: PhaseMapper) -> None:
        """'alpha phase' → 'alpha'."""
        assert phase_mapper.infer_phase("alpha phase", None, None) == "alpha"

    def test_greek_alpha_zr_returns_canonical(self, phase_mapper: PhaseMapper) -> None:
        """'α-Zr' → 'alpha'."""
        assert phase_mapper.infer_phase("α-Zr", None, None) == "alpha"

    def test_greek_beta_returns_canonical(self, phase_mapper: PhaseMapper) -> None:
        """'β' → 'beta'."""
        assert phase_mapper.infer_phase("β", None, None) == "beta"

    def test_alpha_prime_alias(self, phase_mapper: PhaseMapper) -> None:
        """'alpha-prime' → 'alpha-prime'."""
        assert phase_mapper.infer_phase("alpha-prime", None, None) == "alpha-prime"

    def test_martensite_alias(self, phase_mapper: PhaseMapper) -> None:
        """'martensite' → 'alpha-prime'."""
        assert phase_mapper.infer_phase("martensite", None, None) == "alpha-prime"

    def test_omega_alias(self, phase_mapper: PhaseMapper) -> None:
        """'ω' → 'omega'."""
        assert phase_mapper.infer_phase("ω", None, None) == "omega"

    def test_two_phase_region_alias(self, phase_mapper: PhaseMapper) -> None:
        """'two-phase region' → 'alpha+beta'."""
        assert phase_mapper.infer_phase("two-phase region", None, None) == "alpha+beta"

    def test_oxide_alias(self, phase_mapper: PhaseMapper) -> None:
        """'ZrO2' → 'oxide'."""
        assert phase_mapper.infer_phase("ZrO2", None, None) == "oxide"

    def test_monoclinic_oxide_alias(self, phase_mapper: PhaseMapper) -> None:
        """'m-ZrO2' → 'monoclinic-oxide'."""
        assert phase_mapper.infer_phase("m-ZrO2", None, None) == "monoclinic-oxide"

    def test_tetragonal_oxide_alias(self, phase_mapper: PhaseMapper) -> None:
        """'t-ZrO2' → 'tetragonal-oxide'."""
        assert phase_mapper.infer_phase("t-ZrO2", None, None) == "tetragonal-oxide"

    def test_delta_hydride_alias(self, phase_mapper: PhaseMapper) -> None:
        """'δ-hydride' → 'delta-hydride'."""
        assert phase_mapper.infer_phase("δ-hydride", None, None) == "delta-hydride"

    def test_gamma_hydride_alias(self, phase_mapper: PhaseMapper) -> None:
        """'γ-hydride' → 'gamma-hydride'."""
        assert phase_mapper.infer_phase("γ-hydride", None, None) == "gamma-hydride"

    def test_epsilon_hydride_alias(self, phase_mapper: PhaseMapper) -> None:
        """'ε-hydride' → 'epsilon-hydride'."""
        assert phase_mapper.infer_phase("ε-hydride", None, None) == "epsilon-hydride"

    def test_hydride_generic_alias(self, phase_mapper: PhaseMapper) -> None:
        """'hydride rim' → 'hydride'."""
        assert phase_mapper.infer_phase("hydride rim", None, None) == "hydride"

    def test_laves_phase_alias(self, phase_mapper: PhaseMapper) -> None:
        """'Laves phase' → 'Zr(Fe,Cr)2'."""
        assert phase_mapper.infer_phase("Laves phase", None, None) == "Zr(Fe,Cr)2"

    def test_nb_precipitate_alias(self, phase_mapper: PhaseMapper) -> None:
        """'Nb-rich precipitate' → 'beta-Nb'."""
        assert phase_mapper.infer_phase("Nb-rich precipitate", None, None) == "beta-Nb"

    def test_spp_alias(self, phase_mapper: PhaseMapper) -> None:
        """'second phase particle' → 'SPP'."""
        assert phase_mapper.infer_phase("second phase particle", None, None) == "SPP"

    def test_zr4fe_subscript_alias(self, phase_mapper: PhaseMapper) -> None:
        """'Zr₄Fe' → 'Zr4Fe'."""
        assert phase_mapper.infer_phase("Zr₄Fe", None, None) == "Zr4Fe"

    def test_oxygen_stabilized_alpha_alias(self, phase_mapper: PhaseMapper) -> None:
        """'α(O)' → 'alpha-O'."""
        assert phase_mapper.infer_phase("α(O)", None, None) == "alpha-O"

    def test_case_insensitive_matching(self, phase_mapper: PhaseMapper) -> None:
        """Mapping is case-insensitive."""
        assert phase_mapper.infer_phase("Alpha Phase", None, None) == "alpha"
        assert phase_mapper.infer_phase("BETA", None, None) == "beta"


# ---------------------------------------------------------------------------
# Step 1: NOT-phase rejection
# ---------------------------------------------------------------------------


class TestNotPhaseRejection:
    """Step 1 edge cases: processing states and other non-phase terms return None."""

    def test_beta_quenched_is_not_phase(self, phase_mapper: PhaseMapper) -> None:
        """'beta-quenched' is a process, not a phase → None."""
        assert phase_mapper.infer_phase("beta-quenched", None, None) is None

    def test_cold_worked_is_not_phase(self, phase_mapper: PhaseMapper) -> None:
        """'cold-worked' is a process, not a phase → None."""
        assert phase_mapper.infer_phase("cold-worked", None, None) is None

    def test_specimen_is_not_phase(self, phase_mapper: PhaseMapper) -> None:
        """'Zircaloy-4 specimen' is a material, not a phase → None."""
        assert phase_mapper.infer_phase("Zircaloy-4 specimen", None, None) is None


# ---------------------------------------------------------------------------
# Step 2: Material-based inference
# ---------------------------------------------------------------------------


class TestStep2MaterialInference:
    """Step 2: phase is empty but material implies a phase."""

    def test_known_material_with_no_phase_returns_none(self, phase_mapper: PhaseMapper) -> None:
        """Material name alone doesn't imply a phase → None."""
        assert phase_mapper.infer_phase(None, "Zircaloy-4", None) is None

    def test_known_material_with_no_phase_no_context(self, phase_mapper: PhaseMapper) -> None:
        """Without context, cannot infer → None (don't guess)."""
        assert phase_mapper.infer_phase(None, "Zr-2.5Nb", None) is None


# ---------------------------------------------------------------------------
# Step 3: Context clue extraction
# ---------------------------------------------------------------------------


class TestStep3ContextClues:
    """Step 3: extract phase keywords from context text."""

    def test_context_with_alpha_matrix(self, phase_mapper: PhaseMapper) -> None:
        """Context mentioning 'alpha matrix' infers alpha."""
        assert (
            phase_mapper.infer_phase(None, None, "The alpha matrix of Zircaloy-4 was examined")
            == "alpha"
        )

    def test_context_with_oxide_layer(self, phase_mapper: PhaseMapper) -> None:
        """Context mentioning 'oxide layer' infers oxide."""
        assert (
            phase_mapper.infer_phase(None, None, "The oxide layer thickness was measured at 2 μm")
            == "oxide"
        )

    def test_context_with_delta_hydride(self, phase_mapper: PhaseMapper) -> None:
        """Context mentioning 'δ-hydride' infers delta-hydride."""
        assert (
            phase_mapper.infer_phase(None, None, "The δ-hydride precipitates were observed")
            == "delta-hydride"
        )

    def test_context_with_no_phase_keywords_returns_none(self, phase_mapper: PhaseMapper) -> None:
        """Context without phase keywords → None."""
        assert (
            phase_mapper.infer_phase(None, None, "The sample was heated to 500°C for 1 hour")
            is None
        )

    def test_context_step3_only_when_step1_and_2_fail(self, phase_mapper: PhaseMapper) -> None:
        """Step 3 is skipped if Step 1 already found a match."""
        assert (
            phase_mapper.infer_phase("beta", "Zircaloy-4", "The alpha matrix was examined")
            == "beta"
        )


# ---------------------------------------------------------------------------
# LaTeX cleaning
# ---------------------------------------------------------------------------


class TestLaTeXCleaning:
    """LaTeX patterns in raw_phase should be cleaned before mapping."""

    def test_dollar_alpha_cleaned(self, phase_mapper: PhaseMapper) -> None:
        """'$\\alpha$' is cleaned to 'alpha' then mapped."""
        assert phase_mapper.infer_phase(r"$\alpha$", None, None) == "alpha"

    def test_dollar_beta_cleaned(self, phase_mapper: PhaseMapper) -> None:
        """'$\\beta$' is cleaned to 'beta' then mapped."""
        assert phase_mapper.infer_phase(r"$\beta$", None, None) == "beta"

    def test_dollar_delta_in_hydride_context(self, phase_mapper: PhaseMapper) -> None:
        """'$\\delta$-hydride' → LaTeX cleaned → 'delta-hydride'."""
        result = phase_mapper.infer_phase(r"$\delta$-hydride", None, None)
        assert result == "delta-hydride"


# ---------------------------------------------------------------------------
# None returns (no guessing)
# ---------------------------------------------------------------------------


class TestNoneReturns:
    """When inference cannot determine a phase, return None — never guess."""

    def test_all_none_inputs(self, phase_mapper: PhaseMapper) -> None:
        """All None inputs → None."""
        assert phase_mapper.infer_phase(None, None, None) is None

    def test_empty_string_phase(self, phase_mapper: PhaseMapper) -> None:
        """Empty string raw_phase → None."""
        assert phase_mapper.infer_phase("", None, None) is None

    def test_whitespace_only_phase(self, phase_mapper: PhaseMapper) -> None:
        """Whitespace-only raw_phase → None."""
        assert phase_mapper.infer_phase("   ", None, None) is None

    def test_unknown_phase_string(self, phase_mapper: PhaseMapper) -> None:
        """Unknown phase string → None."""
        assert phase_mapper.infer_phase("quantum-phase", None, None) is None

    def test_burnup_not_a_phase(self, phase_mapper: PhaseMapper) -> None:
        """'burnup' is not a phase → None."""
        assert phase_mapper.infer_phase("burnup", None, None) is None


# ---------------------------------------------------------------------------
# Coverage: 20+ standard mappings
# ---------------------------------------------------------------------------


class TestMappingCoverage:
    """Verify that the config provides 20+ canonical phase mappings."""

    def test_at_least_20_canonical_phases(self, phase_mapper: PhaseMapper) -> None:
        """Config must define at least 20 canonical phases."""
        assert len(phase_mapper.canonical_phases) >= 20

    def test_all_canonical_phases_return_self(self, phase_mapper: PhaseMapper) -> None:
        """Every canonical phase returns itself when used as input."""
        for phase in phase_mapper.canonical_phases:
            result = phase_mapper.infer_phase(phase, None, None)
            assert result == phase, f"Canonical '{phase}' should map to itself, got '{result}'"


# ---------------------------------------------------------------------------
# from_config and from_dict constructors
# ---------------------------------------------------------------------------


class TestConstructors:
    """Test PhaseMapper construction methods."""

    def test_from_config_file(self) -> None:
        """PhaseMapper.from_config loads from JSON file."""
        mapper = PhaseMapper.from_config(_PHASE_MAPPING_PATH)
        assert len(mapper.canonical_phases) >= 20

    def test_from_dict(self, phase_mapper_from_dict: PhaseMapper) -> None:
        """PhaseMapper.from_dict loads from inline dict."""
        assert phase_mapper_from_dict.infer_phase("α", None, None) == "alpha"

    def test_from_config_missing_file_raises(self) -> None:
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            PhaseMapper.from_config(Path("/nonexistent/path/phase_mapping.json"))

    def test_from_dict_empty_mappings(self) -> None:
        """Empty mappings dict creates mapper that always returns None."""
        mapper = PhaseMapper.from_dict({"mappings": [], "laTeX_patterns": [], "not_phase": []})
        assert mapper.infer_phase("alpha", None, None) is None


# ---------------------------------------------------------------------------
# Immutability: infer_phase does not mutate inputs
# ---------------------------------------------------------------------------


class TestImmutability:
    """infer_phase must not mutate any input arguments."""

    def test_raw_phase_not_mutated(self, phase_mapper: PhaseMapper) -> None:
        """raw_phase string is not mutated."""
        original = "alpha phase"
        phase_mapper.infer_phase(original, "Zr", "context text")
        assert original == "alpha phase"
