"""Unit tests for PaginationParams dependency (NFM-1074).

Unified pagination query model that replaces inline page/per_page Query params
across all paginated endpoints.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.schemas.common import PaginationParams


class TestPaginationParamsDefaults:
    """Verify default values match the acceptance criteria."""

    def test_default_page_is_one(self) -> None:
        params = PaginationParams()
        assert params.page == 1

    def test_default_per_page_is_twenty(self) -> None:
        params = PaginationParams()
        assert params.per_page == 20

    def test_default_offset_is_zero(self) -> None:
        params = PaginationParams()
        assert params.offset == 0


class TestPaginationParamsOffset:
    """Verify the computed offset property."""

    @pytest.mark.parametrize(
        ("page", "per_page", "expected"),
        [
            (1, 20, 0),
            (2, 20, 20),
            (3, 10, 20),
            (5, 50, 200),
            (1, 1, 0),
            (100, 100, 9900),
        ],
    )
    def test_offset_calculation(
        self, page: int, per_page: int, expected: int
    ) -> None:
        params = PaginationParams(page=page, per_page=per_page)
        assert params.offset == expected


class TestPaginationParamsValidation:
    """Verify field constraints."""

    def test_page_minimum_is_one(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

    def test_page_accepts_one(self) -> None:
        params = PaginationParams(page=1)
        assert params.page == 1

    def test_per_page_minimum_is_one(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(per_page=0)

    def test_per_page_maximum_is_100(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(per_page=101)

    def test_per_page_accepts_100(self) -> None:
        params = PaginationParams(per_page=100)
        assert params.per_page == 100

    def test_per_page_accepts_1(self) -> None:
        params = PaginationParams(per_page=1)
        assert params.per_page == 1


class TestPaginationParamsPages:
    """Verify the computed pages method."""

    def test_pages_with_zero_total(self) -> None:
        params = PaginationParams()
        assert params.pages(total=0) == 0

    def test_pages_with_exact_division(self) -> None:
        params = PaginationParams(page=1, per_page=20)
        assert params.pages(total=100) == 5

    def test_pages_with_remainder(self) -> None:
        params = PaginationParams(page=1, per_page=20)
        assert params.pages(total=101) == 6

    def test_pages_with_one_item(self) -> None:
        params = PaginationParams()
        assert params.pages(total=1) == 1


class TestPaginationParamsChineseDescriptions:
    """Verify Chinese field descriptions appear in JSON schema (OpenAPI)."""

    def test_page_has_chinese_description(self) -> None:
        schema = PaginationParams.model_json_schema()
        assert schema["properties"]["page"]["description"] == "页码"

    def test_per_page_has_chinese_description(self) -> None:
        schema = PaginationParams.model_json_schema()
        assert schema["properties"]["per_page"]["description"] == "每页数量"
