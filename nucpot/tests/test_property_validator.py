"""Tests for nucpot.utils.property_validator.validate_property_name."""

import pytest

from nucpot.utils.property_validator import validate_property_name


class TestDefaultAllowlist:
    """Valid names from the built-in default allowlist."""

    @pytest.mark.parametrize(
        "name",
        [
            "density",
            "thermal_conductivity",
            "melting_point",
            "tensile_strength",
            "youngs_modulus",
            "poissons_ratio",
            "specific_heat",
        ],
    )
    def test_default_allowlist_accepts_known_names(self, name: str) -> None:
        assert validate_property_name(name) is True


class TestCaseInsensitive:
    """Case-insensitive matching: normalises to lowercase before checking."""

    @pytest.mark.parametrize(
        "name",
        ["Density", "THERMAL_CONDUCTIVITY", "Melting_Point", "Youngs_Modulus"],
    )
    def test_case_insensitive_matching(self, name: str) -> None:
        assert validate_property_name(name) is True


class TestCustomAllowlist:
    """A caller-supplied allowlist overrides the default."""

    def test_custom_allowlist_accepts_member(self) -> None:
        custom = {"custom_prop", "another_prop"}
        assert validate_property_name("custom_prop", allowlist=custom) is True

    def test_custom_allowlist_rejects_non_member(self) -> None:
        custom = {"custom_prop"}
        assert validate_property_name("density", allowlist=custom) is False


class TestEmptyStringRejection:
    """Empty string must raise ValueError."""

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            validate_property_name("")


class TestNonStringInputRejection:
    """Non-string input must raise ValueError."""

    @pytest.mark.parametrize("bad_input", [None, 42, 3.14, True, [], {}])
    def test_non_string_raises(self, bad_input: object) -> None:
        with pytest.raises(ValueError, match="must be a string"):
            validate_property_name(bad_input)  # type: ignore[arg-type]


class TestNameNotInAnyAllowlist:
    """A valid string that isn't in any allowlist returns False."""

    def test_unknown_name_returns_false(self) -> None:
        assert validate_property_name("unknown_property_xyz") is False
