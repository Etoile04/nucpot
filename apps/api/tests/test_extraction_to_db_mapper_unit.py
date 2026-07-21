"""Unit tests for extraction_to_db_mapper helper functions (NFM-700).

Covers: _source_key, _material_key, _dataset_key, _build_condition_kwargs,
_parse_float, _lookup_property_type, _find_source_by_doi, _find_material_by_formula.

Uses mocked DB sessions -- no database required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.extraction_to_db_mapper import (
    _build_condition_kwargs,
    _dataset_key,
    _find_material_by_formula,
    _find_source_by_doi,
    _lookup_property_type,
    _material_key,
    _parse_float,
    _source_key,
)


# ---------------------------------------------------------------------------
# _source_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSourceKey:
    def test_with_doi_only(self):
        item = MagicMock()
        item.source_doi = "10.1000/test"
        item.reference = None
        item.source_file = None
        assert _source_key(item) == "doi:10.1000/test|ref:|src:"

    def test_with_all_fields(self):
        item = MagicMock()
        item.source_doi = "10.1000/x"
        item.reference = "Smith et al."
        item.source_file = "paper.md"
        result = _source_key(item)
        assert "10.1000/x" in result
        assert "Smith et al." in result
        assert "paper.md" in result

    def test_empty_fields(self):
        item = MagicMock()
        item.source_doi = None
        item.reference = None
        item.source_file = None
        assert _source_key(item) == "doi:|ref:|src:"

    def test_dedup_same_inputs(self):
        item1 = MagicMock()
        item1.source_doi = "10.1000/dup"
        item1.reference = "Ref"
        item1.source_file = "f.md"
        item2 = MagicMock()
        item2.source_doi = "10.1000/dup"
        item2.reference = "Ref"
        item2.source_file = "f.md"
        assert _source_key(item1) == _source_key(item2)


# ---------------------------------------------------------------------------
# _material_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMaterialKey:
    def test_with_name_and_composition(self):
        item = MagicMock()
        item.material_name = "UO2"
        item.composition = "UO2"
        assert _material_key(item) == "formula:uo2|name:uo2"

    def test_case_insensitive(self):
        item = MagicMock()
        item.material_name = "  UO2 Fuel  "
        item.composition = "  UO2  "
        assert _material_key(item) == "formula:uo2|name:uo2 fuel"

    def test_none_fields(self):
        item = MagicMock()
        item.material_name = None
        item.composition = None
        assert _material_key(item) == "formula:|name:"

    def test_dedup_same_inputs(self):
        item1 = MagicMock()
        item1.material_name = "MOX"
        item1.composition = "U0.8Pu0.2O2"
        item2 = MagicMock()
        item2.material_name = "MOX"
        item2.composition = "U0.8Pu0.2O2"
        assert _material_key(item1) == _material_key(item2)


# ---------------------------------------------------------------------------
# _dataset_key
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDatasetKey:
    def test_combines_source_and_material_keys(self):
        result = _dataset_key("src_a", "mat_x")
        assert result == "src_a||mat_x"

    def test_different_pairs_different_keys(self):
        k1 = _dataset_key("src_a", "mat_x")
        k2 = _dataset_key("src_a", "mat_y")
        assert k1 != k2

    def test_same_pair_same_key(self):
        assert _dataset_key("a", "b") == _dataset_key("a", "b")


# ---------------------------------------------------------------------------
# _build_condition_kwargs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildConditionKwargs:
    def test_none_returns_empty(self):
        assert _build_condition_kwargs(None) == {}

    def test_empty_dict_returns_empty(self):
        assert _build_condition_kwargs({}) == {}

    def test_maps_temperature(self):
        result = _build_condition_kwargs({"temperature": 1000})
        assert result == {"temperature": 1000}

    def test_maps_pressure(self):
        result = _build_condition_kwargs({"pressure": 0.1})
        assert result == {"pressure": 0.1}

    def test_maps_environment(self):
        result = _build_condition_kwargs({"environment": "argon"})
        assert result == {"environment": "argon"}

    def test_maps_irradiation_dose_via_dose(self):
        result = _build_condition_kwargs({"dose": 1.5})
        assert result == {"irradiation_dose": 1.5}

    def test_maps_irradiation_dose_directly(self):
        result = _build_condition_kwargs({"irradiation_dose": 2.0})
        assert result == {"irradiation_dose": 2.0}

    def test_maps_temp_alias(self):
        result = _build_condition_kwargs({"temp": 500})
        assert result == {"temperature": 500}

    def test_skips_none_values(self):
        result = _build_condition_kwargs({"temperature": None})
        assert result == {}

    def test_multiple_conditions(self):
        result = _build_condition_kwargs({
            "temperature": 1000,
            "pressure": 0.1,
            "environment": "He",
        })
        assert result["temperature"] == 1000
        assert result["pressure"] == 0.1
        assert result["environment"] == "He"

    def test_extra_keys_go_to_notes(self):
        result = _build_condition_kwargs({
            "temperature": 500,
            "unknown_param": 42,
        })
        assert result["temperature"] == 500
        assert "notes" in result
        assert "unknown_param=42" in result["notes"]

    def test_extra_keys_appended_to_existing_notes(self):
        result = _build_condition_kwargs({
            "temperature": 500,
            "notes": "Original note",
            "extra": "value",
        })
        assert "Original note" in result["notes"]
        assert "extra=value" in result["notes"]

    def test_notes_key_not_duplicated_in_extra(self):
        result = _build_condition_kwargs({
            "notes": "just a note",
        })
        # notes is a known key that gets captured, but it's in the
        # condition key map? No -- notes is excluded from extra_keys.
        # Since "notes" is not in _CONDITION_KEY_MAP, it stays in
        # extra_keys only if it's not "notes".
        assert result == {}

    def test_unknown_param_without_temperature(self):
        result = _build_condition_kwargs({"custom_field": "abc"})
        assert result == {"notes": "custom_field=abc"}


# ---------------------------------------------------------------------------
# _parse_float
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseFloat:
    def test_valid_integer_string(self):
        assert _parse_float("42") == 42.0

    def test_valid_float_string(self):
        assert _parse_float("3.14") == pytest.approx(3.14)

    def test_negative_number(self):
        assert _parse_float("-5.5") == pytest.approx(-5.5)

    def test_scientific_notation(self):
        assert _parse_float("1.5e-3") == pytest.approx(0.0015)

    def test_non_numeric_string(self):
        assert _parse_float("N/A") is None

    def test_empty_string(self):
        assert _parse_float("") is None

    def test_none_input(self):
        assert _parse_float(None) is None

    def test_integer_type_input(self):
        assert _parse_float(42) == 42.0

    def test_float_type_input(self):
        assert _parse_float(3.14) == pytest.approx(3.14)


# ---------------------------------------------------------------------------
# _lookup_property_type
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLookupPropertyType:
    async def test_returns_none_when_no_category(self):
        db = AsyncMock()
        result = await _lookup_property_type(
            db, category_name=None, property_name="conductivity"
        )
        assert result is None

    async def test_returns_none_when_empty_category(self):
        db = AsyncMock()
        result = await _lookup_property_type(
            db, category_name="", property_name="conductivity"
        )
        assert result is None

    async def test_returns_property_type_when_found(self):
        db = AsyncMock()
        mock_pt = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_pt
        db.execute = AsyncMock(return_value=mock_result)

        result = await _lookup_property_type(
            db, category_name="thermal", property_name="conductivity"
        )
        assert result is mock_pt

    async def test_returns_none_when_not_found(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        result = await _lookup_property_type(
            db, category_name="nonexistent", property_name="ghost"
        )
        assert result is None


# ---------------------------------------------------------------------------
# _find_source_by_doi
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindSourceByDoi:
    async def test_returns_source_when_found(self):
        db = AsyncMock()
        mock_source = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_source
        db.execute = AsyncMock(return_value=mock_result)

        result = await _find_source_by_doi(db, "10.1000/test")
        assert result is mock_source
        db.execute.assert_awaited_once()

    async def test_returns_none_when_not_found(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        result = await _find_source_by_doi(db, "10.1000/missing")
        assert result is None


# ---------------------------------------------------------------------------
# _find_material_by_formula
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindMaterialByFormula:
    async def test_returns_material_when_found(self):
        db = AsyncMock()
        mock_material = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_material
        db.execute = AsyncMock(return_value=mock_result)

        result = await _find_material_by_formula(db, "UO2")
        assert result is mock_material

    async def test_returns_none_when_not_found(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        result = await _find_material_by_formula(db, "NONEXISTENT")
        assert result is None

    async def test_returns_none_for_none_formula(self):
        db = AsyncMock()

        result = await _find_material_by_formula(db, None)
        assert result is None
        db.execute.assert_not_awaited()

    async def test_returns_none_for_empty_formula(self):
        db = AsyncMock()

        result = await _find_material_by_formula(db, "")
        assert result is None
        db.execute.assert_not_awaited()