"""Unit tests for ``services.source_dedup`` (NFM-4089 — F4 ingest bypass).

The helper is exercised through:

1. Pure normalisation functions (no DB) — exercised directly.
2. Lookup + create logic — exercised against the ``db_session`` fixture
   declared in ``apps/api/tests/conftest.py`` (SQLite in-memory).

These tests confirm the priority order (DOI > file_hash >
content_md-prefix) and that ``get_or_create_source`` returns
``(canonical, False)`` on hit, ``(candidate, True)`` on miss.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource
from nfm_db.services.source_dedup import (
    _normalise_doi,
    _normalise_file_hash,
    find_canonical_source,
    get_or_create_source,
)

# ---------------------------------------------------------------------------
# Pure normalisation helpers — no DB needed.
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_doi_collapses_whitespace(self):
        assert _normalise_doi(" 10.1234/abcd ") == "10.1234/abcd"

    def test_doi_collapses_literal_none(self):
        assert _normalise_doi("None") is None
        assert _normalise_doi("none") is None

    def test_doi_returns_none_for_empty(self):
        assert _normalise_doi("") is None
        assert _normalise_doi("   ") is None
        assert _normalise_doi(None) is None

    def test_file_hash_returns_none_for_empty(self):
        assert _normalise_file_hash(None) is None
        assert _normalise_file_hash("") is None
        assert _normalise_file_hash("   ") is None

    def test_file_hash_strips_whitespace(self):
        assert _normalise_file_hash("  abc123  ") == "abc123"


# ---------------------------------------------------------------------------
# Integration with the in-memory SQLite ``db_session`` fixture.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFindCanonicalSource:
    async def test_returns_none_for_empty_candidates(
        self, db_session: AsyncSession
    ):
        result = await find_canonical_source(db_session)
        assert result is None

    async def test_returns_none_when_no_match(
        self, db_session: AsyncSession
    ):
        result = await find_canonical_source(
            db_session, doi="10.1234/not-there"
        )
        assert result is None

    async def test_finds_by_doi(self, db_session: AsyncSession):
        canonical = DataSource(
            id=uuid.uuid4(),
            title="Canonical paper",
            doi="10.1234/abc",
            source_type="journal",
        )
        db_session.add(canonical)
        await db_session.flush()

        result = await find_canonical_source(
            db_session, doi="10.1234/abc"
        )
        assert result is not None
        assert result.id == canonical.id

    async def test_priority_doi_beats_file_hash(
        self, db_session: AsyncSession
    ):
        # Two sources: source A matches doi, source B matches file_hash.
        # When we ask for both, A (the DOI match) must win because DOI
        # has higher priority than file_hash.
        source_a = DataSource(
            id=uuid.uuid4(),
            title="DOI match",
            doi="10.1234/dup",
            file_hash=None,
            source_type="journal",
        )
        source_b = DataSource(
            id=uuid.uuid4(),
            title="Hash match",
            doi=None,
            file_hash="deadbeef",
            source_type="journal",
        )
        db_session.add_all([source_a, source_b])
        await db_session.flush()

        result = await find_canonical_source(
            db_session,
            doi="10.1234/dup",
            file_hash="deadbeef",
        )
        assert result is not None
        assert result.id == source_a.id

    async def test_priority_file_hash_beats_content_md(
        self, db_session: AsyncSession
    ):
        source_a = DataSource(
            id=uuid.uuid4(),
            title="Hash match",
            doi=None,
            file_hash="cafef00d",
            content_md=None,
            source_type="journal",
        )
        source_b = DataSource(
            id=uuid.uuid4(),
            title="Content match",
            doi=None,
            file_hash=None,
            content_md="same content fingerprint starts here...",
            source_type="journal",
        )
        db_session.add_all([source_a, source_b])
        await db_session.flush()

        result = await find_canonical_source(
            db_session,
            file_hash="cafef00d",
            content_md="same content fingerprint starts here...",
        )
        assert result is not None
        assert result.id == source_a.id


@pytest.mark.asyncio
class TestGetOrCreateSource:
    async def test_returns_existing_with_created_false(
        self, db_session: AsyncSession
    ):
        canonical = DataSource(
            id=uuid.uuid4(),
            title="Already here",
            doi="10.1234/existing",
            source_type="journal",
        )
        db_session.add(canonical)
        await db_session.flush()

        result, created = await get_or_create_source(
            db_session,
            doi="10.1234/existing",
            title="Different title (same DOI)",
        )
        assert created is False
        assert result.id == canonical.id

    async def test_creates_new_with_created_true(
        self, db_session: AsyncSession
    ):
        result, created = await get_or_create_source(
            db_session,
            doi="10.1234/new",
            title="Brand new paper",
        )
        assert created is True
        assert result.id is not None
        assert result.title == "Brand new paper"
        # The session is not auto-committed — caller must commit.
        # Re-querying without a commit should still see the row.
        assert (
            result in db_session.new
            or result in db_session.identity_map.values()
        )

    async def test_creates_without_doi(self, db_session: AsyncSession):
        """file_hash-only ingest path still works."""
        result, created = await get_or_create_source(
            db_session,
            title="No-DOI paper",
            file_hash="abcd1234",
        )
        assert created is True
        assert result.file_hash == "abcd1234"

    async def test_no_match_no_hash_returns_created(
        self, db_session: AsyncSession
    ):
        result, created = await get_or_create_source(
            db_session,
            title="Bypass candidate",
        )
        assert created is True
        # No dedup clues provided; helper still INSERTs a new row.
        assert result.title == "Bypass candidate"
