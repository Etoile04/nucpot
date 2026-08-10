"""Direct tests for :mod:`nfm_db.services.re_extraction_worker` (NFM-2781 CR2).

The re-extraction worker is exercised end-to-end via Celery + the
orchestrator, but the worker logic itself was at 0% coverage before
this suite.  These tests patch the orchestrator so we can verify
status transitions, error handling, and the public ``process_*``
functions without needing the full extraction pipeline.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Corpus,
    ExtractionJob,
    OntologyVersion,
    ReExtractionQueue,
    User,
)
from nfm_db.models.user import BlogRole
from nfm_db.services.re_extraction_worker import (
    process_re_extraction_queue,
    process_single_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        username=f"seed_{uuid.uuid4().hex[:8]}",
        email=f"seed_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password="hashed",
        blog_role=BlogRole.DOMAIN_EXPERT,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_ontology(session: AsyncSession) -> OntologyVersion:
    user = await _seed_user(session)
    ov = OntologyVersion(
        version="1.0.0",
        status="published",
        created_by=user.id,
        ontology_data={"entity_types": [], "relation_types": []},
    )
    session.add(ov)
    await session.flush()
    return ov


async def _seed_corpus(session: AsyncSession, *, slug: str = "uo2") -> Corpus:
    corpus = Corpus(corpus_id=slug, name=f"Corpus {slug}")
    session.add(corpus)
    await session.flush()
    return corpus


async def _seed_queue_entry(
    session: AsyncSession,
    *,
    corpus: Corpus,
    ontology: OntologyVersion,
    status: str = "pending",
) -> ReExtractionQueue:
    user = await _seed_user(session)
    entry = ReExtractionQueue(
        corpus_id=corpus.id,
        ontology_version_id=ontology.id,
        triggered_by=user.id,
        status=status,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry)
    return entry


def _fake_orchestrator_run(*, status: str = "completed") -> AsyncMock:
    """Build a fake orchestrator ``run()`` result."""
    result = MagicMock()
    result.status = status
    result.error_message = "boom" if status == "failed" else None
    return AsyncMock(return_value=result)


class TestProcessReExtractionQueue:
    """Tests for :func:`process_re_extraction_queue`."""

    async def test_empty_queue_returns_zero_summary(
        self,
        db_session: AsyncSession,
    ) -> None:
        """An empty queue returns processed=0/completed=0/failed=0."""
        summary = await process_re_extraction_queue(db_session)
        assert summary == {"processed": 0, "completed": 0, "failed": 0}

    async def test_vacuous_completion_when_corpus_has_no_sources(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A queue entry whose corpus has no prior ExtractionJobs vacuously completes."""
        corpus = await _seed_corpus(db_session)
        ontology = await _seed_ontology(db_session)
        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
        )

        summary = await process_re_extraction_queue(db_session)

        assert summary["processed"] == 1
        assert summary["completed"] == 1
        assert summary["failed"] == 0
        await db_session.refresh(entry)
        assert entry.status == "completed"

    async def test_corpus_not_found_marks_entry_failed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A queue entry whose corpus has been deleted is marked failed.

        We patch the worker's corpus-lookup to return None so the
        test doesn't have to fight the SQL FK constraint that prevents
        inserting an entry referencing a non-existent corpus row.
        """
        corpus = await _seed_corpus(db_session)
        ontology = await _seed_ontology(db_session)
        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
        )

        # Patch the helper that fetches the corpus row.
        async def _no_corpus(session, entry_obj):
            raise ValueError(
                f"Corpus '{entry_obj.corpus_id}' not found for entry "
                f"{entry_obj.id}.",
            )

        with patch(
            "nfm_db.services.re_extraction_worker._run_extraction_for_entry",
            side_effect=_no_corpus,
        ):
            summary = await process_re_extraction_queue(db_session)

        assert summary["processed"] == 1
        assert summary["failed"] == 1
        await db_session.refresh(entry)
        assert entry.status == "failed"
        assert entry.error_message is not None
        assert "Corpus" in entry.error_message

    async def test_ontology_not_found_marks_entry_failed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A queue entry whose ontology has been deleted is marked failed.

        We patch ``_run_extraction_for_entry`` to raise the
        ontology-not-found ``ValueError`` so we don't need to fight
        the SQL FK constraint that prevents inserting an entry with
        a non-existent ontology_version_id.
        """
        corpus = await _seed_corpus(db_session)
        ontology = await _seed_ontology(db_session)
        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
        )

        async def _no_ontology(session, entry_obj):
            raise ValueError(
                f"OntologyVersion '{entry_obj.ontology_version_id}' not "
                f"found for entry {entry_obj.id}.",
            )

        with patch(
            "nfm_db.services.re_extraction_worker._run_extraction_for_entry",
            side_effect=_no_ontology,
        ):
            summary = await process_re_extraction_queue(db_session)

        assert summary["processed"] == 1
        assert summary["failed"] == 1
        await db_session.refresh(entry)
        assert entry.status == "failed"

    async def test_orchestrator_failure_propagates_to_entry_failed(
        self,
        db_session: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """If the orchestrator returns failed status, the entry is marked failed."""
        corpus = await _seed_corpus(db_session, slug="uo2")
        ontology = await _seed_ontology(db_session)
        # Seed an existing ExtractionJob so the worker has a source to run on.
        local = tmp_path / "doc.txt"
        local.write_text("hello")
        prior_job = ExtractionJob(
            source_reference=str(local),
            source_type="file",
            corpus_id="uo2",
            status="completed",
        )
        db_session.add(prior_job)
        await db_session.flush()

        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
        )

        with patch(
            "nfm_db.services.re_extraction_worker.ExtractionOrchestrator",
        ) as orch_cls:
            orch = MagicMock()
            orch.run = _fake_orchestrator_run(status="failed")
            orch_cls.return_value = orch
            summary = await process_re_extraction_queue(db_session)

        assert summary["processed"] == 1
        assert summary["failed"] == 1
        await db_session.refresh(entry)
        assert entry.status == "failed"
        assert entry.error_message is not None
        assert "boom" in entry.error_message

    async def test_orchestrator_success_marks_completed(
        self,
        db_session: AsyncSession,
        tmp_path: Path,
    ) -> None:
        """A successful orchestrator run marks the entry completed."""
        corpus = await _seed_corpus(db_session, slug="uo2")
        ontology = await _seed_ontology(db_session)
        local = tmp_path / "doc.txt"
        local.write_text("hello")
        prior_job = ExtractionJob(
            source_reference=str(local),
            source_type="file",
            corpus_id="uo2",
            status="completed",
        )
        db_session.add(prior_job)
        await db_session.flush()

        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
        )

        with patch(
            "nfm_db.services.re_extraction_worker.ExtractionOrchestrator",
        ) as orch_cls:
            orch = MagicMock()
            orch.run = _fake_orchestrator_run(status="completed")
            orch_cls.return_value = orch
            summary = await process_re_extraction_queue(db_session)

        assert summary["processed"] == 1
        assert summary["completed"] == 1
        await db_session.refresh(entry)
        assert entry.status == "completed"


class TestProcessSingleEntry:
    """Tests for :func:`process_single_entry` (manual trigger endpoint)."""

    async def test_entry_not_found_raises(
        self,
        db_session: AsyncSession,
    ) -> None:
        """An unknown entry id raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await process_single_entry(db_session, uuid.uuid4())

    async def test_entry_wrong_status_raises(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A running/completed entry cannot be reprocessed."""
        corpus = await _seed_corpus(db_session)
        ontology = await _seed_ontology(db_session)
        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
            status="running",
        )
        with pytest.raises(ValueError, match="Cannot process entry"):
            await process_single_entry(db_session, entry.id)

    async def test_failed_entry_can_be_reprocessed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """An entry in 'failed' status can be reprocessed via the manual trigger."""
        corpus = await _seed_corpus(db_session)
        ontology = await _seed_ontology(db_session)
        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
            status="failed",
        )

        result = await process_single_entry(db_session, entry.id)
        assert result.status == "completed"

    async def test_pending_entry_can_be_processed(
        self,
        db_session: AsyncSession,
    ) -> None:
        """A 'pending' entry is processed normally."""
        corpus = await _seed_corpus(db_session)
        ontology = await _seed_ontology(db_session)
        entry = await _seed_queue_entry(
            db_session,
            corpus=corpus,
            ontology=ontology,
        )

        result = await process_single_entry(db_session, entry.id)
        assert result.status == "completed"


class TestTryReadContent:
    """Tests for the private ``_try_read_content`` helper."""

    def test_url_returns_none(self) -> None:
        """HTTP/HTTPS source references are not read."""
        from nfm_db.services.re_extraction_worker import _try_read_content

        assert _try_read_content("https://example.com/paper.pdf") is None
        assert _try_read_content("http://example.com/paper.pdf") is None

    def test_doi_returns_none(self) -> None:
        """DOI references are not read as files."""
        from nfm_db.services.re_extraction_worker import _try_read_content

        assert _try_read_content("doi:10.1234/abc") is None
        assert _try_read_content("DOI:10.1234/abc") is None

    def test_empty_returns_none(self) -> None:
        """Empty source references return None."""
        from nfm_db.services.re_extraction_worker import _try_read_content

        assert _try_read_content("") is None

    def test_missing_local_file_returns_none(self) -> None:
        """A path that doesn't exist returns None (not raises)."""
        from nfm_db.services.re_extraction_worker import _try_read_content

        assert _try_read_content("/nonexistent/path/file.txt") is None

    def test_existing_local_file_returns_content(self, tmp_path: Path) -> None:
        """A readable local file is read and returned as a string."""
        from nfm_db.services.re_extraction_worker import _try_read_content

        target = tmp_path / "doc.txt"
        target.write_text("hello world")

        assert _try_read_content(str(target)) == "hello world"
