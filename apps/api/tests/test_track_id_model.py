"""Tests for the ``track_id`` column on ``extraction_steps`` (NFM-3595).

NFM-3543-A adds a NOT NULL UUID ``track_id`` column to ``extraction_steps``
with a ``gen_random_uuid()`` server default.  These tests assert the
column is auto-populated for fresh inserts (mirroring the Postgres
``server_default`` via the Python-side ``default=uuid.uuid4`` shim)
and that explicit values are honored on override.

Both tests run against the in-memory SQLite ``db_session`` fixture from
``conftest.py``.  The schema is created via ``Base.metadata.create_all``
so the ORM-declared ``server_default=func.gen_random_uuid()`` is wired
into the table definition even though SQLite will not execute it; the
Python-side ``default=uuid.uuid4`` is what fills the column on insert.
The Postgres-only ``server_default`` behavior is covered by the
``pg_session`` probe (skipped unless ``NFM_TEST_DATABASE_URL`` is set).
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep

_UUID4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


async def _seed_job(session: AsyncSession) -> ExtractionJob:
    """Persist a parent ExtractionJob so ExtractionStep's FK resolves."""
    job = ExtractionJob(source_reference="track-id-test", source_type="internal_id")
    session.add(job)
    await session.flush()
    return job


class TestExtractionStepTrackId:
    """Acceptance tests for the ``track_id`` column."""

    @pytest.mark.asyncio
    async def test_freshly_inserted_step_has_non_null_uuid4_track_id(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A new ``ExtractionStep`` row gets a non-null UUID4 ``track_id`` automatically."""
        job = await _seed_job(db_session)

        step = ExtractionStep(
            job_id=job.id,
            step_type="chunk",
            status="pending",
        )
        db_session.add(step)
        await db_session.commit()
        await db_session.refresh(step)

        assert step.track_id is not None, (
            "track_id must be auto-populated by the model default on insert"
        )
        assert isinstance(step.track_id, uuid.UUID), (
            f"track_id must be a uuid.UUID, got {type(step.track_id).__name__}"
        )
        assert _UUID4_PATTERN.match(str(step.track_id)), (
            f"track_id {step.track_id!s} is not a UUID4"
        )

    @pytest.mark.asyncio
    async def test_explicit_track_id_overrides_default_and_persists(
        self,
        db_session: AsyncSession,
    ) -> None:
        """An explicit ``track_id=`` is persisted instead of the auto-generated one."""
        job = await _seed_job(db_session)

        explicit = uuid.uuid4()
        step = ExtractionStep(
            job_id=job.id,
            step_type="extract",
            status="pending",
            track_id=explicit,
        )
        db_session.add(step)
        await db_session.commit()
        await db_session.refresh(step)

        assert step.track_id == explicit, (
            f"explicit track_id {explicit!s} must persist; got {step.track_id!s}"
        )
        assert _UUID4_PATTERN.match(str(step.track_id)), (
            f"track_id {step.track_id!s} is not a UUID4"
        )


class TestExtractionStepTrackIdIndex:
    """The ``ix_extraction_steps_track_id`` index is declared on the table."""

    def test_index_declared_on_table(self) -> None:
        """``__table_args__`` carries the matching ``Index`` object."""
        indexes = ExtractionStep.__table__.indexes
        names = {idx.name for idx in indexes}
        assert "ix_extraction_steps_track_id" in names, (
            f"missing ix_extraction_steps_track_id in declared indexes: {names}"
        )
        index = next(i for i in indexes if i.name == "ix_extraction_steps_track_id")
        column_names = {col.name for col in index.columns}
        assert column_names == {"track_id"}, (
            f"ix_extraction_steps_track_id must cover only track_id; got {column_names}"
        )
