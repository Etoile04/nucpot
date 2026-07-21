"""Unit tests for property_service private helpers and list_material_properties (NFM-697/NFM-1067).

Covers: _format_measurement_value, _derive_confidence, _resolve_unit_symbol,
list_material_properties.

Uses mocked DB sessions -- no database required.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.services.property_service import (
    _derive_confidence,
    _format_measurement_value,
    _resolve_unit_symbol,
    list_material_properties,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_measurement(
    *,
    value_scalar=None,
    value_min=None,
    value_max=None,
    value_expression=None,
    value_list=None,
    value_text=None,
    review_status="pending",
    unit=None,
    property_type=None,
) -> MagicMock:
    """Create a mock PropertyMeasurement with specified value fields."""
    m = MagicMock()
    m.value_scalar = value_scalar
    m.value_min = value_min
    m.value_max = value_max
    m.value_expression = value_expression
    m.value_list = value_list
    m.value_text = value_text
    m.review_status = review_status
    m.unit = unit
    m.property_type = property_type
    return m


def _mock_unit(symbol: str) -> MagicMock:
    """Create a mock Unit with a symbol."""
    u = MagicMock()
    u.symbol = symbol
    return u


def _mock_property_type(*, default_unit=None) -> MagicMock:
    """Create a mock PropertyType with optional default unit."""
    pt = MagicMock()
    pt.default_unit = default_unit
    pt.name = "Thermal Conductivity"
    return pt


def _mock_db() -> AsyncMock:
    """Create a mock AsyncSession."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# _format_measurement_value
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatMeasurementValue:
    def test_expression_takes_priority(self):
        m = _make_measurement(
            value_expression="a + b*T",
            value_scalar=8.5,
        )
        assert _format_measurement_value(m) == "a + b*T"

    def test_range_format(self):
        m = _make_measurement(value_min=100.0, value_max=200.0)
        result = _format_measurement_value(m)
        assert "100.0" in result
        assert "200.0" in result
        assert "-" in result

    def test_scalar_format(self):
        m = _make_measurement(value_scalar=8.5)
        assert _format_measurement_value(m) == "8.5"

    def test_scalar_with_decimal(self):
        """Numeric(16,6) returns Decimal -- should convert to float."""
        m = _make_measurement(value_scalar=Decimal("5.680000"))
        result = _format_measurement_value(m)
        assert result == "5.68"

    def test_list_format(self):
        m = _make_measurement(value_list=[1.0, 2.0, 3.0])
        result = _format_measurement_value(m)
        assert result == "[1.0, 2.0, 3.0]"

    def test_text_format(self):
        m = _make_measurement(value_text="See Figure 3")
        assert _format_measurement_value(m) == "See Figure 3"

    def test_all_none_returns_dash(self):
        m = _make_measurement()
        assert _format_measurement_value(m) == "—"

    def test_integer_scalar(self):
        m = _make_measurement(value_scalar=42)
        assert _format_measurement_value(m) == "42.0"

    def test_zero_scalar(self):
        m = _make_measurement(value_scalar=0.0)
        assert _format_measurement_value(m) == "0.0"

    def test_negative_scalar(self):
        m = _make_measurement(value_scalar=-3.5)
        assert _format_measurement_value(m) == "-3.5"

    def test_range_with_decimals(self):
        m = _make_measurement(
            value_min=Decimal("100.500000"),
            value_max=Decimal("200.500000"),
        )
        result = _format_measurement_value(m)
        assert "100.5" in result
        assert "200.5" in result

    def test_single_list_item(self):
        m = _make_measurement(value_list=[99.9])
        assert _format_measurement_value(m) == "[99.9]"

    def test_empty_text_string(self):
        m = _make_measurement(value_text="")
        assert _format_measurement_value(m) == ""


# ---------------------------------------------------------------------------
# _derive_confidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeriveConfidence:
    def test_approved(self):
        m = _make_measurement(review_status="approved")
        assert _derive_confidence(m) == 0.95

    def test_verified(self):
        m = _make_measurement(review_status="verified")
        assert _derive_confidence(m) == 0.95

    def test_pending(self):
        m = _make_measurement(review_status="pending")
        assert _derive_confidence(m) == 0.7

    def test_flagged(self):
        m = _make_measurement(review_status="flagged")
        assert _derive_confidence(m) == 0.5

    def test_rejected(self):
        m = _make_measurement(review_status="rejected")
        assert _derive_confidence(m) == 0.3

    def test_none_status_defaults_to_pending(self):
        m = _make_measurement(review_status=None)
        assert _derive_confidence(m) == 0.7

    def test_unknown_status_defaults_to_0_5(self):
        m = _make_measurement(review_status="unknown_status")
        assert _derive_confidence(m) == 0.5

    def test_case_insensitive(self):
        m = _make_measurement(review_status="APPROVED")
        assert _derive_confidence(m) == 0.95

    def test_mixed_case(self):
        m = _make_measurement(review_status="Pending")
        assert _derive_confidence(m) == 0.7


# ---------------------------------------------------------------------------
# _resolve_unit_symbol
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveUnitSymbol:
    def test_measurement_unit_takes_priority(self):
        m = _make_measurement(
            unit=_mock_unit("W/(m·K)"),
            property_type=_mock_property_type(
                default_unit=_mock_unit("m/s"),
            ),
        )
        assert _resolve_unit_symbol(m) == "W/(m·K)"

    def test_falls_back_to_property_type_default_unit(self):
        m = _make_measurement(
            unit=None,
            property_type=_mock_property_type(
                default_unit=_mock_unit("GPa"),
            ),
        )
        assert _resolve_unit_symbol(m) == "GPa"

    def test_none_when_no_unit_anywhere(self):
        m = _make_measurement(
            unit=None,
            property_type=_mock_property_type(default_unit=None),
        )
        assert _resolve_unit_symbol(m) is None

    def test_none_when_property_type_is_none(self):
        m = _make_measurement(unit=None, property_type=None)
        assert _resolve_unit_symbol(m) is None

    def test_none_when_property_type_has_no_default(self):
        m = _make_measurement(
            unit=None,
            property_type=_mock_property_type(default_unit=None),
        )
        assert _resolve_unit_symbol(m) is None


# ---------------------------------------------------------------------------
# list_material_properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListMaterialProperties:
    async def test_returns_none_when_material_missing(self):
        db = _mock_db()
        missing_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)

        result = await list_material_properties(db, missing_id)

        assert result is None

    async def test_returns_empty_data_when_no_measurements(self):
        db = _mock_db()
        mat_id = uuid.uuid4()

        # Material exists
        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        # Count returns 0
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        scalars = MagicMock()
        scalars.all.return_value = []
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(db, mat_id)

        assert result is not None
        assert result.data == []
        assert result.meta.total == 0
        assert result.meta.page == 1

    async def test_returns_items_with_correct_shape(self):
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        # Create a mock measurement row
        mock_row = _make_measurement(
            value_scalar=Decimal("8.500000"),
            review_status="approved",
            unit=_mock_unit("W/(m·K)"),
            property_type=_mock_property_type(default_unit=_mock_unit("W/(m·K)")),
        )
        mock_row.id = uuid.uuid4()

        mock_dataset = MagicMock()
        mock_dataset.source = MagicMock()
        mock_dataset.source.title = "Smith et al."
        mock_row.dataset = mock_dataset
        mock_row.property_type.name = "Thermal Conductivity"

        scalars = MagicMock()
        scalars.all.return_value = [mock_row]
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, page=1, limit=50, sort="name", order="asc"
        )

        assert result is not None
        assert len(result.data) == 1
        item = result.data[0]
        assert item.name == "Thermal Conductivity"
        assert "8.5" in item.value
        assert item.unit == "W/(m·K)"
        assert item.source == "Smith et al."
        assert item.confidence == 0.95

    async def test_pagination_params_passed_to_meta(self):
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 25

        scalars = MagicMock()
        scalars.all.return_value = []
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, page=3, limit=10, sort="name", order="asc"
        )

        assert result is not None
        assert result.meta.page == 3
        assert result.meta.limit == 10
        assert result.meta.total == 25

    async def test_with_filter_parameter(self):
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        scalars = MagicMock()
        scalars.all.return_value = []
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, filter="thermal", sort="name", order="asc"
        )

        assert result is not None
        assert result.data == []

    async def test_sort_by_value(self):
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        scalars = MagicMock()
        scalars.all.return_value = []
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, sort="value", order="desc", page=1, limit=50
        )

        assert result is not None
        assert result.meta.total == 0

    async def test_unknown_sort_defaults_to_name(self):
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0

        scalars = MagicMock()
        scalars.all.return_value = []
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, sort="invalid_column", page=1, limit=50, order="asc"
        )

        assert result is not None

    async def test_measurement_without_property_type(self):
        """Property type can be None; name should be empty string."""
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        mock_row = _make_measurement(value_scalar=5.0, review_status="pending")
        mock_row.id = uuid.uuid4()
        mock_row.property_type = None
        mock_row.unit = None

        mock_dataset = MagicMock()
        mock_dataset.source = MagicMock()
        mock_dataset.source.title = "Paper"
        mock_row.dataset = mock_dataset

        scalars = MagicMock()
        scalars.all.return_value = [mock_row]
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, sort="created_at", order="asc", page=1, limit=50
        )

        assert result is not None
        assert len(result.data) == 1
        assert result.data[0].name == ""
        assert result.data[0].unit is None
        assert result.data[0].confidence == 0.7

    async def test_measurement_without_dataset(self):
        """Dataset or source can be None."""
        db = _mock_db()
        mat_id = uuid.uuid4()

        exists_result = MagicMock()
        exists_result.scalar_one_or_none.return_value = mat_id

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        mock_row = _make_measurement(
            value_scalar=10.0, review_status="flagged",
            property_type=_mock_property_type(default_unit=None),
        )
        mock_row.id = uuid.uuid4()
        mock_row.dataset = None

        scalars = MagicMock()
        scalars.all.return_value = [mock_row]
        data_result = MagicMock()
        data_result.scalars.return_value = scalars

        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return exists_result
            if call_count == 2:
                return count_result
            return data_result

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await list_material_properties(
            db, mat_id, sort="created_at", order="asc", page=1, limit=50
        )

        assert result is not None
        assert result.data[0].source == ""
        assert result.data[0].confidence == 0.5