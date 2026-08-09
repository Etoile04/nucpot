"""Unit tests for ExtractionChunk V2 contract (NFM-2687).

Covers the V2 model additions:
  * ``step_name`` column (nullable, V1 compat)
  * ``source_span_hash`` for upsert idempotency
  * ``token_estimate`` (V2 token count, distinct from V1 ``token_count``)
  * ``metadata_`` JSON
  * ``_source_span`` property + ``validate_source_span`` helper
  * Idempotent ``upsert_by_span_hash`` classmethod (async, AsyncSession)
  * Partial unique index on (job_id, step_name, source_span_hash)
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import JSON, select
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.models import Base, ExtractionJob
from nfm_db.models.extraction_chunk import (
    ExtractionChunk,
    SourceSpan,
    SourceSpanValidationError,
    compute_source_span_hash,
    validate_source_span,
)


def _sqlite_compat_create_all(sync_conn, metadata) -> None:
    """Create all tables on a SQLite connection, swapping JSONB for JSON.

    Mirrors the conftest's ``_replace_jsonb`` helper so this test file
    can stand alone without depending on the conftest's private
    helpers. Callable shape matches ``conn.run_sync``'s expectation.
    """
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
    metadata.create_all(sync_conn)


# ---------------------------------------------------------------------------
# SourceSpan dataclass
# ---------------------------------------------------------------------------


class TestSourceSpanDataclass:
    def test_valid_with_section_id(self) -> None:
        span = SourceSpan(start_offset=0, end_offset=10, section_id="intro")
        assert span.to_dict() == {
            "start_offset": 0,
            "end_offset": 10,
            "section_id": "intro",
        }

    def test_section_id_optional_defaults_to_none(self) -> None:
        span = SourceSpan(start_offset=5, end_offset=15)
        assert span.section_id is None
        assert span.to_dict() == {
            "start_offset": 5,
            "end_offset": 15,
            "section_id": None,
        }

    def test_negative_start_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError):
            SourceSpan(start_offset=-1, end_offset=10)

    def test_negative_end_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError):
            SourceSpan(start_offset=0, end_offset=-1)

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError):
            SourceSpan(start_offset=10, end_offset=5)

    def test_zero_offsets_accepted(self) -> None:
        span = SourceSpan(start_offset=0, end_offset=0, section_id=None)
        assert span.start_offset == 0
        assert span.end_offset == 0


# ---------------------------------------------------------------------------
# validate_source_span
# ---------------------------------------------------------------------------


class TestValidateSourceSpan:
    def test_none_passes_through(self) -> None:
        assert validate_source_span(None) is None

    def test_valid_dict_returned_unchanged(self) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": "x"}
        assert validate_source_span(span) is span

    def test_valid_dict_without_section_id(self) -> None:
        span = {"start_offset": 100, "end_offset": 200}
        assert validate_source_span(span) is span

    def test_missing_start_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError, match="start_offset"):
            validate_source_span({"end_offset": 10})

    def test_missing_end_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError, match="end_offset"):
            validate_source_span({"start_offset": 0})

    def test_negative_start_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError, match="non-negative"):
            validate_source_span(
                {"start_offset": -1, "end_offset": 10, "section_id": None}
            )

    def test_negative_end_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError, match="non-negative"):
            validate_source_span(
                {"start_offset": 0, "end_offset": -1, "section_id": None}
            )

    def test_string_start_offset_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError):
            validate_source_span(
                {"start_offset": "0", "end_offset": 10, "section_id": None}
            )

    def test_bool_start_offset_rejected(self) -> None:
        # bool subclasses int, but we don't want True/False to be valid offsets.
        with pytest.raises(SourceSpanValidationError):
            validate_source_span(
                {"start_offset": True, "end_offset": 10, "section_id": None}
            )

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError):
            validate_source_span(
                {"start_offset": 10, "end_offset": 5, "section_id": None}
            )

    def test_section_id_wrong_type_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError, match="section_id"):
            validate_source_span(
                {"start_offset": 0, "end_offset": 10, "section_id": 42}
            )

    def test_non_dict_rejected(self) -> None:
        with pytest.raises(SourceSpanValidationError, match="dict"):
            validate_source_span("not a dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# compute_source_span_hash
# ---------------------------------------------------------------------------


class TestComputeSourceSpanHash:
    def test_deterministic(self) -> None:
        job_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        h1 = compute_source_span_hash(job_id, "chunk", span)
        h2 = compute_source_span_hash(job_id, "chunk", span)
        assert h1 == h2

    def test_different_step_name_different_hash(self) -> None:
        job_id = uuid.uuid4()
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        h1 = compute_source_span_hash(job_id, "chunk", span)
        h2 = compute_source_span_hash(job_id, "extract", span)
        assert h1 != h2

    def test_different_job_id_different_hash(self) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        h1 = compute_source_span_hash(uuid.uuid4(), "chunk", span)
        h2 = compute_source_span_hash(uuid.uuid4(), "chunk", span)
        assert h1 != h2

    def test_different_span_different_hash(self) -> None:
        job_id = uuid.uuid4()
        h1 = compute_source_span_hash(
            job_id, "chunk", {"start_offset": 0, "end_offset": 10, "section_id": None}
        )
        h2 = compute_source_span_hash(
            job_id, "chunk", {"start_offset": 0, "end_offset": 20, "section_id": None}
        )
        assert h1 != h2

    def test_returns_64_char_lowercase_hex(self) -> None:
        h = compute_source_span_hash(
            uuid.uuid4(),
            "chunk",
            {"start_offset": 0, "end_offset": 10, "section_id": None},
        )
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_section_id_different_hash(self) -> None:
        job_id = uuid.uuid4()
        h1 = compute_source_span_hash(
            job_id, "chunk", {"start_offset": 0, "end_offset": 10, "section_id": "a"}
        )
        h2 = compute_source_span_hash(
            job_id, "chunk", {"start_offset": 0, "end_offset": 10, "section_id": "b"}
        )
        assert h1 != h2


# ---------------------------------------------------------------------------
# Model CRUD & upsert (in-memory SQLite, AsyncSession — matches the app)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def session() -> AsyncSession:
    """Async in-memory SQLite session that mirrors the app's real session type.

    The app exposes only ``AsyncSession`` (``nfm_db.database.get_db``); the
    previous sync ``Session`` fixture let ``upsert_by_span_hash``'s sync
    ``session.query()`` call pass tests but crash in production. This
    fixture exercises the same shape that ships.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_sqlite_compat_create_all, Base.metadata)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as s:
        try:
            yield s
        finally:
            pass
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
async def job(session: AsyncSession) -> ExtractionJob:
    job = ExtractionJob(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        source_reference="test-doc",
        source_type="file",
        status="completed",
    )
    session.add(job)
    await session.commit()
    return job


class TestCreate:
    async def test_create_minimal_v2_chunk(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        chunk = ExtractionChunk(
            job_id=job.id,
            step_name="chunk",
            content="hello world",
            source_span={"start_offset": 0, "end_offset": 11, "section_id": None},
            source_span_hash="abc123",
            chunk_index=0,
        )
        session.add(chunk)
        await session.commit()

        loaded = await session.get(ExtractionChunk, chunk.id)
        assert loaded is not None
        assert loaded.content == "hello world"
        assert loaded.step_name == "chunk"
        assert loaded.source_span == {
            "start_offset": 0,
            "end_offset": 11,
            "section_id": None,
        }
        assert loaded.source_span_hash == "abc123"
        assert loaded.chunk_index == 0
        assert loaded.token_estimate is None
        assert loaded.metadata_ is None
        assert loaded.token_count is None  # V1 column still nullable

    async def test_create_with_token_estimate_and_metadata(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        meta = {"model": "claude-opus-4.8", "prompt_version": 3}
        chunk = ExtractionChunk(
            job_id=job.id,
            step_name="extract",
            content="extracted text",
            source_span={
                "start_offset": 100,
                "end_offset": 200,
                "section_id": "results",
            },
            source_span_hash="def456",
            chunk_index=1,
            token_estimate=50,
            metadata_=meta,
        )
        session.add(chunk)
        await session.commit()

        loaded = await session.get(ExtractionChunk, chunk.id)
        assert loaded is not None
        assert loaded.token_estimate == 50
        assert loaded.metadata_ == meta
        assert loaded.step_name == "extract"

    async def test_v1_chunk_without_step_name_still_insertable(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        """V1 chunker rows lack step_name / source_span_hash / metadata_
        and must remain insertable for backward compatibility."""
        chunk = ExtractionChunk(
            job_id=job.id,
            content="legacy chunk",
            source_span={"start": 0, "end": 12},  # V1 schema
            chunk_index=0,
            token_count=3,  # V1 column
        )
        session.add(chunk)
        await session.commit()
        loaded = await session.get(ExtractionChunk, chunk.id)
        assert loaded is not None
        assert loaded.step_name is None
        assert loaded.source_span_hash is None
        assert loaded.token_estimate is None
        assert loaded.token_count == 3


class TestSourceSpanProperty:
    async def test_setter_validates_v2_schema(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        chunk = ExtractionChunk(
            job_id=job.id,
            step_name="chunk",
            content="x",
            chunk_index=0,
            source_span_hash="h",
        )
        # Setting via `_source_span` should validate against the V2 schema.
        chunk._source_span = {"start_offset": 0, "end_offset": 5, "section_id": "s"}
        assert chunk.source_span == {
            "start_offset": 0,
            "end_offset": 5,
            "section_id": "s",
        }

    async def test_setter_rejects_negative_offset(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        chunk = ExtractionChunk(
            job_id=job.id,
            step_name="chunk",
            content="x",
            chunk_index=0,
            source_span_hash="h",
        )
        with pytest.raises(SourceSpanValidationError):
            chunk._source_span = {
                "start_offset": -1,
                "end_offset": 5,
                "section_id": None,
            }


class TestUpsertBySpanHash:
    async def test_first_call_creates(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        chunk = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="first",
            source_span=span,
            chunk_index=0,
        )
        await session.commit()

        assert chunk.id is not None
        assert chunk.content == "first"
        assert chunk.source_span == span
        assert chunk.source_span_hash is not None
        assert len(chunk.source_span_hash) == 64

    async def test_second_call_with_same_triple_returns_same_row(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        first = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="first",
            source_span=span,
            chunk_index=0,
        )
        await session.commit()
        first_id = first.id

        # A second call with the same (job_id, step_name, source_span)
        # must return the existing row — even if the caller passed
        # different content / chunk_index that would have been the
        # write payload on a fresh insert.
        second = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="DIFFERENT BODY",
            source_span=span,
            chunk_index=99,
        )
        await session.commit()

        assert second.id == first_id
        assert second.content == "first"
        assert second.chunk_index == 0

    async def test_different_span_creates_new_row(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        first = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="A",
            source_span={
                "start_offset": 0,
                "end_offset": 5,
                "section_id": None,
            },
            chunk_index=0,
        )
        await session.commit()

        second = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="B",
            source_span={
                "start_offset": 5,
                "end_offset": 10,
                "section_id": None,
            },
            chunk_index=1,
        )
        await session.commit()

        assert first.id != second.id
        assert first.content == "A"
        assert second.content == "B"

    async def test_different_step_name_creates_new_row(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        first = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="A",
            source_span=span,
            chunk_index=0,
        )
        await session.commit()

        second = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="extract",
            content="B",
            source_span=span,
            chunk_index=0,
        )
        await session.commit()

        assert first.id != second.id

    async def test_upsert_rejects_negative_start_offset(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        with pytest.raises(SourceSpanValidationError):
            await ExtractionChunk.upsert_by_span_hash(
                session,
                job_id=job.id,
                step_name="chunk",
                content="x",
                source_span={
                    "start_offset": -1,
                    "end_offset": 10,
                    "section_id": None,
                },
                chunk_index=0,
            )

    async def test_upsert_rejects_negative_end_offset(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        with pytest.raises(SourceSpanValidationError):
            await ExtractionChunk.upsert_by_span_hash(
                session,
                job_id=job.id,
                step_name="chunk",
                content="x",
                source_span={
                    "start_offset": 0,
                    "end_offset": -1,
                    "section_id": None,
                },
                chunk_index=0,
            )

    async def test_upsert_passes_through_token_estimate_and_metadata(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        meta = {"prompt": "v1"}
        chunk = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="extract",
            content="body",
            source_span=span,
            chunk_index=0,
            token_estimate=42,
            metadata_=meta,
        )
        await session.commit()
        assert chunk.token_estimate == 42
        assert chunk.metadata_ == meta

    async def test_upsert_returns_queryable_row(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        chunk = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="x",
            source_span=span,
            chunk_index=0,
        )
        await session.commit()

        result = await session.execute(
            select(ExtractionChunk).where(
                ExtractionChunk.job_id == job.id,
                ExtractionChunk.step_name == "chunk",
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].id == chunk.id


# ---------------------------------------------------------------------------
# AsyncSession shape regression test (NFM-2741 / Code Reviewer P1 fix)
# ---------------------------------------------------------------------------


class TestUpsertRunsAgainstAsyncSession:
    """P1 regression: ``upsert_by_span_hash`` must run against the same
    AsyncSession type the application uses. The pre-fix implementation
    called ``session.query(...)`` which raises
    ``AttributeError: 'AsyncSession' object has no attribute 'query'``
    on the real session.
    """

    async def test_works_against_real_async_session_factory(
        self, session: AsyncSession, job: ExtractionJob
    ) -> None:
        # Sanity: the session handed to us by the fixture is exactly
        # the AsyncSession class the production session factory uses.
        assert isinstance(session, AsyncSession)

        span = {"start_offset": 0, "end_offset": 10, "section_id": None}
        chunk = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="async-shape",
            source_span=span,
            chunk_index=0,
        )
        await session.commit()
        await session.refresh(chunk)
        assert chunk.id is not None
        assert chunk.source_span == span
        # Idempotent on the same triple — caller-supplied content is ignored.
        again = await ExtractionChunk.upsert_by_span_hash(
            session,
            job_id=job.id,
            step_name="chunk",
            content="ignored",
            source_span=span,
            chunk_index=99,
        )
        assert again.id == chunk.id

    async def test_no_session_query_call(self) -> None:
        """Static guard: the method body must not call ``session.query``.

        The original defect was a sync ``session.query(cls)`` call inside
        an otherwise async-friendly classmethod. We re-check the source
        here so a future refactor cannot reintroduce the same shape.
        """
        import inspect

        from nfm_db.models import extraction_chunk as _ec

        source = inspect.getsource(_ec.ExtractionChunk.upsert_by_span_hash)
        assert "session.query(" not in source, (
            "upsert_by_span_hash must use async session.execute(select(...))"
        )
