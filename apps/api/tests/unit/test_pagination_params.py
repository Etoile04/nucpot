"""Unit tests for PaginationParams validation and offset calculation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nfm_db.schemas.common import PaginationParams


class TestPaginationParamsDefaults:
    """Verify default values match existing API behavior."""

    def test_default_page_is_one(self) -> None:
        params = PaginationParams()
        assert params.page == 1

    def test_default_per_page_is_twenty(self) -> None:
        params = PaginationParams()
        assert params.per_page == 20


class TestPaginationParamsOffset:
    """Verify offset property computes (page - 1) * per_page."""

    @pytest.mark.parametrize(
        ("page", "per_page", "expected_offset"),
        [
            (1, 20, 0),
            (2, 20, 20),
            (3, 20, 40),
            (1, 10, 0),
            (5, 50, 200),
        ],
    )
    def test_offset_calculation(
        self, page: int, per_page: int, expected_offset: int
    ) -> None:
        params = PaginationParams(page=page, per_page=per_page)
        assert params.offset == expected_offset


class TestPaginationParamsValidation:
    """Verify Pydantic validation constraints."""

    def test_page_ge_one_raises_on_zero(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

    def test_page_ge_one_raises_on_negative(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page=-1)

    def test_per_page_le_one_hundred_raises_on_101(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(per_page=101)

    def test_per_page_ge_one_raises_on_zero(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(per_page=0)

    def test_per_page_ge_one_raises_on_negative(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(per_page=-1)

    def test_valid_boundary_values(self) -> None:
        params = PaginationParams(page=1, per_page=100)
        assert params.page == 1
        assert params.per_page == 100
