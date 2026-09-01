"""Tests for the NFM-4089 AC2 dedup helper.

Covers :func:`nfm_db.services.source_service.get_or_create_source` and the
``_find_source_by_*`` lookup helpers it sits on top of.

These tests run against the shared ``db_session`` fixture (SQLite in-memory).
The dedup logic is intentionally DB-agnostic so no Postgres-specific
features are exercised here.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import DataSource
from nfm_db.services.source_service import (
    _content_fingerprint,
    _find_source_by_content_fingerprint,
    _find_source_by_doi,
    _find_source_by_file_hash,
    get_or_create_source,
)

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_source(
    db: AsyncSession,
    *,
    doi: str | None = None,
    title: str = "Paper A",
    source_type: str = "journal_article",
    file_hash: str | None = None,
    content_md: str | None = None,
) -> DataSource:
    payload: dict = dict(title=title, source_type=source_type)
    if doi is not None:
        payload["doi"] = doi
    if file_hash is not None:
        payload["file_hash"] = file_hash
    if content_md is not None:
        payload["content_md"] = content_md
    source = DataSource(**payload)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


# ---------------------------------------------------------------------------
# _find_source_by_doi
# ---------------------------------------------------------------------------


class TestFindSourceByDoi:
    @pytest.mark.asyncio
    async def test_returns_existing_row(self, db_session: AsyncSession):
        seeded = await _seed_source(db_session, doi="10.1000/abc", title="Existing")
        found = await _find_source_by_doi(db_session, "10.1000/abc")
        assert found is not None
        assert found.id == seeded.id
        assert found.title == "Existing"

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self, db_session: AsyncSession):
        found = await _find_source_by_doi(db_session, "10.1000/does-not-exist")
        assert found is None

    @pytest.mark.asyncio
    async def test_does_not_match_other_doi(self, db_session: AsyncSession):
        await _seed_source(db_session, doi="10.1000/abc")
        found = await _find_source_by_doi(db_session, "10.1000/def")
        assert found is None


# ---------------------------------------------------------------------------
# _find_source_by_file_hash
# ---------------------------------------------------------------------------


class TestFindSourceByFileHash:
    @pytest.mark.asyncio
    async def test_returns_any_matching_row(self, db_session: AsyncSession):
        old = await _seed_source(
            db_session, title="Old", file_hash="abc123" * 10 + "abcdef"
        )
        new = await _seed_source(
            db_session, title="New", file_hash="abc123" * 10 + "abcdef"
        )
        found = await _find_source_by_file_hash(db_session, "abc123" * 10 + "abcdef")
        assert found is not None
        assert found.id in {old.id, new.id}

    @pytest.mark.asyncio
    async def test_returns_none_when_no_match(self, db_session: AsyncSession):
        await _seed_source(db_session, file_hash="a" * 64)
        found = await _find_source_by_file_hash(db_session, "b" * 64)
        assert found is None


# ---------------------------------------------------------------------------
# _content_fingerprint / _find_source_by_content_fingerprint
# ---------------------------------------------------------------------------


class TestContentFingerprint:
    def test_stable_for_same_text(self):
        a = _content_fingerprint("hello world")
        b = _content_fingerprint("hello world")
        assert a == b
        assert len(a) == 64  # SHA-256 hex

    def test_differs_for_different_text(self):
        a = _content_fingerprint("alpha")
        b = _content_fingerprint("beta")
        assert a != b

    def test_caps_to_first_64kb(self):
        # The prefix must itself exceed the cap (65 536 bytes per
        # ``_CONTENT_FINGERPRINT_CAP_BYTES`` in source_service) so the
        # differing tail bytes are guaranteed to lie past the sampled window.
        prefix = "x" * (65_536 + 1024)
        long_a = prefix + "a" * 200_000
        long_b = prefix + "b" * 200_000
        # First 64 KB match → fingerprint must match even though the strings differ.
        assert _content_fingerprint(long_a) == _content_fingerprint(long_b)

    @pytest.mark.asyncio
    async def test_finds_row_with_matching_content(self, db_session: AsyncSession):
        seeded = await _seed_source(
            db_session,
            title="Manuscript",
            content_md="the quick brown fox jumps over the lazy dog",
        )
        found = await _find_source_by_content_fingerprint(
            db_session, "the quick brown fox jumps over the lazy dog"
        )
        assert found is not None
        assert found.id == seeded.id

    @pytest.mark.asyncio
    async def test_returns_none_for_unrelated_content(self, db_session: AsyncSession):
        await _seed_source(db_session, content_md="original prose here")
        found = await _find_source_by_content_fingerprint(
            db_session, "completely different document body"
        )
        assert found is None

    @pytest.mark.asyncio
    async def test_empty_content_returns_none(self, db_session: AsyncSession):
        await _seed_source(db_session, content_md="real content")
        found = await _find_source_by_content_fingerprint(db_session, "")
        assert found is None


# ---------------------------------------------------------------------------
# get_or_create_source — DOI branch
# ---------------------------------------------------------------------------


class TestGetOrCreateByDoi:
    @pytest.mark.asyncio
    async def test_creates_new_when_doi_unknown(self, db_session: AsyncSession):
        source, created = await get_or_create_source(
            db_session,
            title="Fresh",
            doi="10.1000/new",
            source_type="journal_article",
        )
        assert created is True
        assert source.title == "Fresh"
        assert source.doi == "10.1000/new"
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_returns_existing_when_doi_known(self, db_session: AsyncSession):
        seeded = await _seed_source(
            db_session, doi="10.1000/known", title="Original"
        )
        source, created = await get_or_create_source(
            db_session,
            title="Attempted duplicate",
            doi="10.1000/known",
            source_type="journal_article",
        )
        assert created is False
        assert source.id == seeded.id
        # Title is NOT overwritten — the dedup contract returns existing as-is.
        assert source.title == "Original"

    @pytest.mark.asyncio
    async def test_extra_fields_applied_only_on_insert(self, db_session: AsyncSession):
        # First call inserts with the extras.
        first, first_created = await get_or_create_source(
            db_session,
            title="Paper",
            doi="10.1000/extra",
            source_type="journal_article",
            fields={"journal": "Nature"},
        )
        assert first_created is True
        assert first.journal == "Nature"
        await db_session.commit()

        # Second call with different extras — should NOT mutate the row.
        second, second_created = await get_or_create_source(
            db_session,
            title="Paper",
            doi="10.1000/extra",
            source_type="journal_article",
            fields={"journal": "Science"},
        )
        assert second_created is False
        assert second.id == first.id
        assert second.journal == "Nature"


# ---------------------------------------------------------------------------
# get_or_create_source — file_hash branch
# ---------------------------------------------------------------------------


class TestGetOrCreateByFileHash:
    @pytest.mark.asyncio
    async def test_creates_new_when_no_doi_and_no_match(self, db_session: AsyncSession):
        source, created = await get_or_create_source(
            db_session,
            title="Uploaded Paper",
            file_hash="f" * 64,
            source_type="journal_article",
        )
        assert created is True
        assert source.file_hash == "f" * 64
        await db_session.commit()

    @pytest.mark.asyncio
    async def test_returns_existing_when_file_hash_known(self, db_session: AsyncSession):
        seeded = await _seed_source(
            db_session, title="Prior", file_hash="a" * 64
        )
        source, created = await get_or_create_source(
            db_session,
            title="Re-upload",
            file_hash="a" * 64,
            source_type="journal_article",
        )
        assert created is False
        assert source.id == seeded.id


# ---------------------------------------------------------------------------
# get_or_create_source — content_md branch
# ---------------------------------------------------------------------------


class TestGetOrCreateByContent:
    @pytest.mark.asyncio
    async def test_falls_through_to_fingerprint(self, db_session: AsyncSession):
        seeded = await _seed_source(
            db_session,
            title="Manuscript",
            content_md="fixed body text that we expect to see again",
        )
        source, created = await get_or_create_source(
            db_session,
            title="Manuscript (re-ingest)",
            file_hash="b" * 64,
            content_md="fixed body text that we expect to see again",
        )
        assert created is False
        assert source.id == seeded.id


# ---------------------------------------------------------------------------
# get_or_create_source — priority / bypass closure
# ---------------------------------------------------------------------------


class TestGetOrCreatePriority:
    @pytest.mark.asyncio
    async def test_doi_takes_priority_over_file_hash(self, db_session: AsyncSession):
        # Two existing rows: one matched by DOI, one matched by file_hash.
        doi_row = await _seed_source(
            db_session, doi="10.1000/priority", title="Via DOI"
        )
        hash_row = await _seed_source(
            db_session,
            title="Via Hash",
            file_hash="c" * 64,
        )

        # Caller knows only the DOI — helper should return doi_row, not hash_row.
        source, created = await get_or_create_source(
            db_session,
            title="Re-ingest",
            doi="10.1000/priority",
            file_hash="c" * 64,
        )
        assert created is False
        assert source.id == doi_row.id
        assert source.id != hash_row.id

    @pytest.mark.asyncio
    async def test_no_keys_at_all_still_inserts(self, db_session: AsyncSession):
        # Last-resort path: nothing to dedup against.
        source, created = await get_or_create_source(
            db_session,
            title="Nothing to match against",
        )
        assert created is True
        # The row is queued via ``db.add`` but the PK is only materialised at
        # flush time.  Confirm the helper returns the queued instance; the
        # id is generated on commit/flush elsewhere.
        assert source.title == "Nothing to match against"
        await db_session.commit()
        await db_session.refresh(source)
        assert source.id is not None

    @pytest.mark.asyncio
    async def test_fields_does_not_clobber_dedup_keys(self, db_session: AsyncSession):
        # Caller attempts to overwrite ``doi`` via ``fields``.  Helper must
        # silently drop those keys (see get_or_create_source docstring).
        source, created = await get_or_create_source(
            db_session,
            title="Override attempt",
            doi="10.1000/keep",
            fields={"doi": "10.1000/discarded", "title": "Also discarded"},
        )
        assert created is True
        assert source.doi == "10.1000/keep"
        assert source.title == "Override attempt"
        await db_session.commit()


# ---------------------------------------------------------------------------
# End-to-end idempotency simulation (NFM-4084 root-cause regression test)
# ---------------------------------------------------------------------------


class TestBypassClosureRegression:
    """Simulate the NFM-4084 '14 UUID-titled rows' scenario.

    Pre-fix behaviour (NFM-4084 finding):
      - extraction mapper's no-DOI branch created a new ``DataSource`` on
        every run because no dedup key was available.
      - the new row had ``title`` equal to the raw ``source_file`` path
        (or worse, the literal UUID string the upstream extractor emitted).

    Post-fix expectation (NFM-4089 AC2):
      - When DOI is missing but ``source_file`` (used as a fingerprint
        proxy) is supplied, repeat invocations return the original row.
    """

    @pytest.mark.asyncio
    async def test_repeated_extraction_does_not_create_duplicates(
        self, db_session: AsyncSession
    ):
        item = {
            "source_doi": None,
            "source_file": "data/papers/Owen2023/page_03.md",
            "reference": "Owen et al., Activation energies in U-10Mo",
        }

        # First extraction batch — no existing source, so a new row is created.
        first, first_created = await get_or_create_source(
            db_session,
            title=item["reference"],
            doi=item["source_doi"],
            file_hash=None,
            content_md=item["source_file"],
            source_type="other",
        )
        assert first_created is True
        await db_session.commit()

        # Second extraction batch for the same paper — must dedup.
        second, second_created = await get_or_create_source(
            db_session,
            title=item["reference"],
            doi=item["source_doi"],
            file_hash=None,
            content_md=item["source_file"],
            source_type="other",
        )
        assert second_created is False
        assert second.id == first.id
