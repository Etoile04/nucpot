"""Tests for ExtractionOrchestrator default track_id threading (NFM-3596 / NFM-3543-B).

When the orchestrator runs without an explicit ``track_id``, every persisted
``ExtractionStep`` row must still carry a non-null, distinct ``track_id`` — the
``server_default=gen_random_uuid()`` plus Python ``default=uuid.uuid4`` from
Sibling A (NFM-3595) supply a value automatically.

These tests verify that the orchestrator's ``_execute_step`` path actually
relies on the model default rather than dropping the column entirely: rows are
non-null, distinct, and stable for each fresh step.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from nfm_db.models.extraction_job import ExtractionJob
from nfm_db.models.extraction_step import ExtractionStep
from nfm_db.services.extraction_orchestrator import ExtractionOrchestrator


async def _create_job(
    *, session: Any, source_reference: str = "doi:10.1234/track-default",
) -> ExtractionJob:
    job = ExtractionJob(
        source_reference=source_reference,
        source_type="doi",
    )
    session.add(job)
    await session.flush()
    return job


def _fake_ontofuel_extract(
    source_reference: str,
    source_type: str,
    element_systems: list[str] | None = None,
    db: Any = None,
) -> list[dict[str, Any]]:
    return [
        {
            "property_name": "lattice_constant",
            "value": 5.47,
            "element_system": "UO2",
            "confidence": "high",
        },
    ]


class TestDefaultTrackId:
    """A default-orchestrated run produces step rows with non-null, distinct track_ids."""

    @pytest.mark.asyncio
    async def test_default_run_assigns_non_null_track_ids(
        self, db_session: Any,
    ) -> None:
        """AC: every step row produced by a default run carries a non-null track_id."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=_fake_ontofuel_extract,
        ):
            await orchestrator.run(
                content="Hello world. A second paragraph.",
                source_type=job.source_type,
            )

        stmt = (
            select(ExtractionStep)
            .where(ExtractionStep.job_id == job.id)
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) >= 1, "expected at least one step row from run()"
        for row in rows:
            assert row.track_id is not None, (
                f"step_type={row.step_type} has null track_id — "
                "default did not propagate"
            )
            assert isinstance(row.track_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_default_run_assigns_distinct_track_ids(
        self, db_session: Any,
    ) -> None:
        """AC: different rows receive different track_ids via the server default."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=_fake_ontofuel_extract,
        ):
            await orchestrator.run(
                content="Distinct track id test content.",
                source_type=job.source_type,
            )

        stmt = (
            select(ExtractionStep)
            .where(ExtractionStep.job_id == job.id)
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        track_ids = [row.track_id for row in rows]
        assert len(track_ids) >= 2, (
            "run() must produce multiple steps to verify distinctness"
        )
        assert len(set(track_ids)) == len(track_ids), (
            f"track_ids should all be distinct, got {track_ids}"
        )
