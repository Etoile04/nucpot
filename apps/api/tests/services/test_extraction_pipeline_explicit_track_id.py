"""Tests for ExtractionOrchestrator explicit track_id threading (NFM-3596 / NFM-3543-B).

When the rerun endpoint (Sibling D) re-attaches to an existing logical track, the
orchestrator must honour an explicit ``track_id`` argument and propagate it to
every ``ExtractionStep`` it persists — both the running/completed rows and the
skipped rows.

This test confirms the orchestrator's ``run(track_id=<uuid>)`` keyword reaches
both step-construction sites (the "skipped" path at line 206 and the
"running/completed" path at line 223 in ``extraction_orchestrator.py``).
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
    *, session: Any, source_reference: str = "doi:10.1234/track-explicit",
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


class TestExplicitTrackId:
    """An orchestrator run with an explicit ``track_id`` propagates it to every step."""

    @pytest.mark.asyncio
    async def test_explicit_track_id_propagates_to_all_steps(
        self, db_session: Any,
    ) -> None:
        """AC: every step row produced by an explicit track_id run equals that UUID."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        explicit_track = uuid.uuid4()

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=_fake_ontofuel_extract,
        ):
            await orchestrator.run(
                content="Explicit track id test content.",
                source_type=job.source_type,
                track_id=explicit_track,
            )

        stmt = (
            select(ExtractionStep)
            .where(ExtractionStep.job_id == job.id)
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        assert len(rows) >= 1, "expected at least one step row from run()"
        for row in rows:
            assert row.track_id == explicit_track, (
                f"step_type={row.step_type} got track_id={row.track_id!s}, "
                f"expected {explicit_track!s}"
            )

    @pytest.mark.asyncio
    async def test_explicit_track_id_does_not_get_defaulted(
        self, db_session: Any,
    ) -> None:
        """AC: explicit track_id is not silently replaced by the server default."""
        job = await _create_job(session=db_session)
        orchestrator = ExtractionOrchestrator(db_session, job)

        # Use a fixed UUID we can verify is the one that wins.
        explicit_track = uuid.UUID("00000000-0000-0000-0000-000000000abc")

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=_fake_ontofuel_extract,
        ):
            await orchestrator.run(
                content="Anti-default test.",
                source_type=job.source_type,
                track_id=explicit_track,
            )

        stmt = (
            select(ExtractionStep)
            .where(ExtractionStep.job_id == job.id)
        )
        rows = (await db_session.execute(stmt)).scalars().all()
        for row in rows:
            # Must equal exactly the explicit UUID — not a fresh gen_random_uuid().
            assert row.track_id == explicit_track
