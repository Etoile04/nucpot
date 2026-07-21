"""Unit tests for nfm_db.services.providers.local — LocalPotentialProvider."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.providers.base import PotentialFilters
from nfm_db.services.providers.local import LocalPotentialProvider


def _make_potential_row(
    *,
    id: uuid.UUID | None = None,
    name: str = "test_pot",
    display_name: str = "Test Potential",
    description: str = "A test potential",
    type: str = "MEAM",
    status: str = "published",
    elements: list[str] | None = None,
    updated_at: str = "2025-06-01T00:00:00",
) -> MagicMock:
    row = MagicMock()
    row.id = id or uuid.uuid4()
    row.name = name
    row.display_name = display_name
    row.description = description
    row.type = type
    row.status = status
    row.elements = elements
    row.updated_at = updated_at
    return row


class TestListSummaries:
    async def test_no_filters_returns_all_published(self) -> None:
        rows = [_make_potential_row(), _make_potential_row(name="pot2")]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        with patch("nfm_db.services.providers.local.PotentialSummary") as mock_summary:
            mock_summary.model_validate.side_effect = lambda r: r
            results = await provider.list_summaries(PotentialFilters())

        assert len(results) == 2

    async def test_type_filter(self) -> None:
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        await provider.list_summaries(PotentialFilters(type_filter="MEAM"))

        assert db.execute.call_count == 1

    async def test_query_filter(self) -> None:
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        await provider.list_summaries(PotentialFilters(query="uranium"))

        assert db.execute.call_count == 1

    async def test_element_filter_post_query(self) -> None:
        rows = [
            _make_potential_row(elements=["U", "O"]),
            _make_potential_row(elements=["Zr", "Nb"]),
            _make_potential_row(elements=["U", "Zr"]),
        ]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        with patch("nfm_db.services.providers.local.PotentialSummary") as mock_summary:
            mock_summary.model_validate.side_effect = lambda r: r
            results = await provider.list_summaries(
                PotentialFilters(elements=["U", "O"])
            )

        assert len(results) == 2

    async def test_element_filter_empty_strings_ignored(self) -> None:
        rows = [_make_potential_row(elements=["U"])]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        with patch("nfm_db.services.providers.local.PotentialSummary") as mock_summary:
            mock_summary.model_validate.side_effect = lambda r: r
            results = await provider.list_summaries(
                PotentialFilters(elements=["", "U", " "])
            )

        assert len(results) == 1

    async def test_sort_by_name(self) -> None:
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        await provider.list_summaries(PotentialFilters(sort="name"))

        assert db.execute.call_count == 1

    async def test_sort_by_type(self) -> None:
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        await provider.list_summaries(PotentialFilters(sort="type"))

        assert db.execute.call_count == 1

    async def test_elements_none_on_row(self) -> None:
        row = _make_potential_row(elements=None)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        with patch("nfm_db.services.providers.local.PotentialSummary") as mock_summary:
            mock_summary.model_validate.side_effect = lambda r: r
            results = await provider.list_summaries(
                PotentialFilters(elements=["Fe"])
            )

        assert len(results) == 0

    async def test_no_elements_filter_skips_post_filter(self) -> None:
        rows = [_make_potential_row(), _make_potential_row()]
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        with patch("nfm_db.services.providers.local.PotentialSummary") as mock_summary:
            mock_summary.model_validate.side_effect = lambda r: r
            results = await provider.list_summaries(PotentialFilters())

        assert len(results) == 2


class TestGetDetail:
    async def test_found_and_published(self) -> None:
        row = _make_potential_row()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        with patch("nfm_db.services.providers.local.PotentialDetail") as mock_detail:
            mock_detail.model_validate.return_value = {"id": row.id}
            result = await provider.get_detail(row.id)

        assert result is not None
        assert result["id"] == row.id

    async def test_not_found_returns_none(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        provider = LocalPotentialProvider(db)
        result = await provider.get_detail(uuid.uuid4())

        assert result is None