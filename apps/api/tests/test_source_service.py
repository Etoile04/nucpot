"""Tests for the source service layer (NFM-698).

Covers: list_sources, get_source, create_source.
Tests use the db_session fixture from conftest.py (SQLite in-memory).
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import Author, DataSource, DataSourceAuthor
from nfm_db.schemas.source import DataSourceCreate, DataSourceUpdate, DataSourceDetailResponse
from nfm_db.services.source_service import (
    create_source,
    get_source,
    list_sources,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_source_counter = 0


async def _seed_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    title="Paper A",
    source_type="journal_article",
    year=2020,
    journal="J. Nucl. Mater.",
    **overrides,
) -> DataSource:
    global _source_counter
    _source_counter += 1
    if doi is None:
        doi = f"10.1000/test-{_source_counter}"
    defaults = dict(
        doi=doi,
        title=title,
        source_type=source_type,
        year=year,
        journal=journal,
    )
    defaults.update(overrides)
    source = DataSource(**defaults)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def _seed_author(
    db: AsyncSession,
    *,
    full_name="Alice",
    last_name="Alice",
    **overrides,
) -> Author:
    defaults = dict(full_name=full_name, last_name=last_name)
    defaults.update(overrides)
    author = Author(**defaults)
    db.add(author)
    await db.commit()
    await db.refresh(author)
    return author


async def _link_author(
    db: AsyncSession,
    source_id: uuid.UUID,
    author_id: uuid.UUID,
    author_order: int = 1,
    is_corresponding: bool = False,
) -> DataSourceAuthor:
    link = DataSourceAuthor(
        data_source_id=source_id,
        author_id=author_id,
        author_order=author_order,
        is_corresponding=is_corresponding,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)
    return link


# ============================================================
# list_sources
# ============================================================


class TestListSources:
    """Tests for list_sources service method."""

    @pytest.mark.asyncio
    async def test_list_returns_all_sources(self, db_session: AsyncSession) -> None:
        await _seed_source(db_session, title="Paper 1")
        await _seed_source(db_session, title="Paper 2")

        result = await list_sources(db_session, page=1, per_page=20)

        assert result.total == 2
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_list_paginates_correctly(self, db_session: AsyncSession) -> None:
        for i in range(5):
            await _seed_source(db_session, title=f"Paper {i}")

        page1 = await list_sources(db_session, page=1, per_page=2)
        assert page1.total == 5
        assert page1.pages == 3  # ceil(5/2)
        assert len(page1.items) == 2

        page3 = await list_sources(db_session, page=3, per_page=2)
        assert len(page3.items) == 1

    @pytest.mark.asyncio
    async def test_list_filters_by_year(self, db_session: AsyncSession) -> None:
        await _seed_source(db_session, title="Old", year=2019)
        await _seed_source(db_session, title="New", year=2021)

        result = await list_sources(db_session, page=1, per_page=20, year=2021)

        assert result.total == 1
        assert result.items[0].title == "New"

    @pytest.mark.asyncio
    async def test_list_filters_by_source_type(self, db_session: AsyncSession) -> None:
        await _seed_source(db_session, title="Journal", source_type="journal_article")
        await _seed_source(db_session, title="Book", source_type="book")

        result = await list_sources(
            db_session, page=1, per_page=20, source_type="book"
        )

        assert result.total == 1
        assert result.items[0].source_type == "book"

    @pytest.mark.asyncio
    async def test_list_sorts_by_title_asc(self, db_session: AsyncSession) -> None:
        await _seed_source(db_session, title="Charlie")
        await _seed_source(db_session, title="Alpha")
        await _seed_source(db_session, title="Bravo")

        result = await list_sources(
            db_session, page=1, per_page=20, sort="title", order="asc"
        )

        titles = [s.title for s in result.items]
        assert titles == ["Alpha", "Bravo", "Charlie"]

    @pytest.mark.asyncio
    async def test_list_sorts_by_title_desc(self, db_session: AsyncSession) -> None:
        await _seed_source(db_session, title="Alpha")
        await _seed_source(db_session, title="Bravo")

        result = await list_sources(
            db_session, page=1, per_page=20, sort="title", order="desc"
        )

        titles = [s.title for s in result.items]
        assert titles == ["Bravo", "Alpha"]

    @pytest.mark.asyncio
    async def test_list_empty_database(self, db_session: AsyncSession) -> None:
        result = await list_sources(db_session, page=1, per_page=20)

        assert result.total == 0
        assert result.items == []
        assert result.pages == 0

    @pytest.mark.asyncio
    async def test_list_combined_year_and_type_filter(
        self, db_session: AsyncSession,
    ) -> None:
        await _seed_source(
            db_session, title="Match", year=2020, source_type="journal_article"
        )
        await _seed_source(
            db_session, title="Wrong Year", year=2019, source_type="journal_article"
        )
        await _seed_source(
            db_session, title="Wrong Type", year=2020, source_type="book"
        )

        result = await list_sources(
            db_session, page=1, per_page=20, year=2020, source_type="journal_article"
        )

        assert result.total == 1
        assert result.items[0].title == "Match"


# ============================================================
# get_source
# ============================================================


class TestGetSource:
    """Tests for get_source service method."""

    @pytest.mark.asyncio
    async def test_get_returns_source(self, db_session: AsyncSession) -> None:
        source = await _seed_source(db_session, title="Found")

        result = await get_source(db_session, source.id)

        assert result is not None
        assert result.title == "Found"
        assert result.doi == source.doi

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, db_session: AsyncSession) -> None:
        result = await get_source(db_session, uuid.uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_get_includes_authors_ordered(
        self, db_session: AsyncSession,
    ) -> None:
        source = await _seed_source(db_session, title="Authored Paper")
        author1 = await _seed_author(db_session, full_name="Bob B", last_name="B")
        author2 = await _seed_author(db_session, full_name="Alice A", last_name="A")

        # Insert in reverse order
        await _link_author(db_session, source.id, author1.id, author_order=2)
        await _link_author(db_session, source.id, author2.id, author_order=1)

        result = await get_source(db_session, source.id)

        assert result is not None
        assert len(result.authors) == 2
        # Authors should be sorted by author_order ASC
        assert result.authors[0].author_order == 1
        assert result.authors[0].author.last_name == "A"
        assert result.authors[1].author_order == 2
        assert result.authors[1].author.last_name == "B"

    @pytest.mark.asyncio
    async def test_get_without_authors(
        self, db_session: AsyncSession,
    ) -> None:
        source = await _seed_source(db_session, title="No Authors")

        result = await get_source(db_session, source.id)

        assert result is not None
        assert result.authors == []


# ============================================================
# create_source
# ============================================================


class TestCreateSource:
    """Tests for create_source service method."""

    @pytest.mark.asyncio
    async def test_create_with_doi(self, db_session: AsyncSession) -> None:
        data = DataSourceCreate(
            doi="10.1016/j.jnucmat.2020.152300",
            title="Thermal conductivity of UO2",
            source_type="journal_article",
            year=2020,
            journal="J. Nucl. Mater.",
        )

        result = await create_source(db_session, data)

        assert result.title == "Thermal conductivity of UO2"
        assert result.doi == "10.1016/j.jnucmat.2020.152300"
        assert result.source_type == "journal_article"
        assert result.year == 2020
        assert result.id is not None

    @pytest.mark.asyncio
    async def test_create_without_doi(self, db_session: AsyncSession) -> None:
        data = DataSourceCreate(
            title="Internal Report",
            source_type="report",
        )

        result = await create_source(db_session, data)

        assert result.title == "Internal Report"
        assert result.doi is None
        assert result.source_type == "report"

    @pytest.mark.asyncio
    async def test_create_persists_to_db(self, db_session: AsyncSession) -> None:
        data = DataSourceCreate(
            title="Persistent Paper",
            source_type="conference_paper",
        )

        result = await create_source(db_session, data)

        # Verify it can be fetched back
        fetched = await get_source(db_session, result.id)
        assert fetched is not None
        assert fetched.title == "Persistent Paper"
