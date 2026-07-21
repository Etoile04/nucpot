"""Unit tests for nfm_db.services.source_service — list, get, create."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.source_service import (
    create_source,
    get_source,
    list_sources,
)


def _make_source_row(
    *,
    id: uuid.UUID | None = None,
    title: str = "Test Paper",
    year: int = 2024,
    source_type: str = "journal",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    data_source_authors: list | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = id or uuid.uuid4()
    row.title = title
    row.year = year
    row.source_type = source_type
    row.created_at = created_at or datetime(2025, 1, 1, tzinfo=timezone.utc)
    row.updated_at = updated_at or datetime(2025, 6, 1, tzinfo=timezone.utc)
    row.data_source_authors = data_source_authors or []
    return row


def _make_author_link(
    *,
    author_order: int = 0,
    is_corresponding: bool = False,
    author_name: str = "Author One",
) -> MagicMock:
    link = MagicMock()
    link.id = uuid.uuid4()
    link.data_source_id = uuid.uuid4()
    link.author_id = uuid.uuid4()
    link.author_order = author_order
    link.is_corresponding = is_corresponding
    link.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    link.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    author = MagicMock()
    author.name = author_name
    author.id = link.author_id
    link.author = author
    return link


class TestListSources:
    async def test_default_params(self) -> None:
        rows = [_make_source_row()]
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = rows
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        list_result = MagicMock()
        list_result.scalars.return_value = scalars_mock

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        with patch("nfm_db.services.source_service.DataSourceResponse") as mock_resp:
            mock_resp.model_validate.side_effect = lambda r: {"id": r.id}

            result = await list_sources(db)

        assert result.total == 1
        assert result.page == 1
        assert len(result.items) == 1

    async def test_year_filter(self) -> None:
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        result = await list_sources(db, year=2023)
        assert result.total == 0

    async def test_source_type_filter(self) -> None:
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        result = await list_sources(db, source_type="book")
        assert result.total == 0

    async def test_sort_by_title(self) -> None:
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        result = await list_sources(db, sort="title", order="asc")
        assert db.execute.call_count == 2

    async def test_pagination(self) -> None:
        count_result = MagicMock()
        count_result.scalar_one.return_value = 50
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        result = await list_sources(db, page=3, per_page=10)
        assert result.total == 50
        assert result.page == 3
        assert result.pages == 5  # ceil(50/10)

    async def test_pages_zero_when_total_zero(self) -> None:
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        result = await list_sources(db)
        assert result.pages == 0

    async def test_sort_by_year_desc(self) -> None:
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        list_result = MagicMock()
        list_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[count_result, list_result])

        result = await list_sources(db, sort="year", order="desc")
        assert db.execute.call_count == 2


class TestGetSource:
    async def test_found_with_authors(self) -> None:
        links = [
            _make_author_link(author_order=1, author_name="B Author"),
            _make_author_link(author_order=0, author_name="A Author"),
        ]
        row = _make_source_row(data_source_authors=links)

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        fake_source_resp = MagicMock()
        fake_source_resp.model_dump.return_value = {"id": row.id, "title": row.title}
        fake_detail = MagicMock()

        with patch("nfm_db.services.source_service.DataSourceResponse", return_value=fake_source_resp), \
             patch("nfm_db.services.source_service.DataSourceAuthorResponse"), \
             patch("nfm_db.services.source_service.AuthorResponse"), \
             patch("nfm_db.services.source_service.DataSourceDetailResponse", return_value=fake_detail):
            result = await get_source(db, row.id)

        assert result is fake_detail

    async def test_not_found_returns_none(self) -> None:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        result = await get_source(db, uuid.uuid4())
        assert result is None

    async def test_no_authors(self) -> None:
        row = _make_source_row(data_source_authors=[])
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        fake_source_resp = MagicMock()
        fake_source_resp.model_dump.return_value = {"id": row.id}
        fake_detail = MagicMock()

        with patch("nfm_db.services.source_service.DataSourceResponse", return_value=fake_source_resp), \
             patch("nfm_db.services.source_service.DataSourceDetailResponse", return_value=fake_detail):
            result = await get_source(db, row.id)

        assert result is fake_detail


class TestCreateSource:
    async def test_create_and_return(self) -> None:
        source = _make_source_row()
        data = MagicMock()
        data.model_dump.return_value = {"title": "New Paper", "year": 2025}

        db = AsyncMock()
        db.refresh = AsyncMock()

        with patch("nfm_db.services.source_service.DataSource") as mock_ds, \
             patch("nfm_db.services.source_service.DataSourceResponse") as mock_resp:
            mock_ds.return_value = source
            mock_resp.model_validate.return_value = {"id": source.id}

            result = await create_source(db, data)

        assert result["id"] == source.id
        db.add.assert_called_once_with(source)
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once_with(source)