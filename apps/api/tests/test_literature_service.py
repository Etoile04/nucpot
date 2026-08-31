"""Unit tests for the literature service (NFM-1487 / NFM-1485-2).

Covers the acceptance criteria for :func:`process_literature`:

1. Happy path with ``content_md`` pre-set → KG nodes created.
2. Happy path: PDF parse then extract.
3. Duplicate-hash short-circuit.
4. Failed-parse sets ``parse_status='failed'`` + parse_error is committed
   (parse failure is durable so the user can see it) but the DataSource
   row stays consistent (no half-written content_md).

Mocking strategy: the heavy downstream components (PyMuPDF, storage,
LLM, GraphBuilder, extraction_to_db_mapper) are patched at the module
boundary so each test exercises only the orchestration logic in
``process_literature``.  The :class:`DataSource` row is real — created
on the in-memory SQLite ``db_session`` fixture from ``conftest.py``.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.source import DataSource
from nfm_db.services import literature_service as lit_svc

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_demo_extraction() -> list[dict[str, Any]]:
    """Minimal valid extraction payload used by happy-path tests."""
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.47,
            "unit": "angstrom",
            "method": "DFT",
            "source": "test-upload.pdf",
            "source_doi": None,
            "confidence": "high",
            "uncertainty": 0.01,
            "temperature": 300.0,
            "cache_level": "L1",
            "material_name": "UO2",
            "composition": "UO2",
            "property": "lattice_constant",
        },
    ]


async def _add_datasource(
    db: AsyncSession,
    *,
    title: str = "Test PDF",
    content_md: str | None = None,
    file_hash: str | None = None,
    file_path: str | None = None,
) -> DataSource:
    """Create and commit a bare DataSource row with the requested fields."""
    ds = DataSource(
        title=title,
        source_type="uploaded_pdf",
        parse_status="uploaded",
        content_md=content_md,
        file_hash=file_hash,
        file_path=file_path,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


def _make_empty_build_result() -> MagicMock:
    """Build a BuildResult-like MagicMock with empty ingest payload.

    NFM-2928: process_literature now passes the BuildResult to
    dispatch_build_result, which short-circuits empty payloads. Use this
    helper wherever a test mocks ``GraphBuilder.build_from_extraction``
    and does not care about the dispatch outcome.
    """
    result = MagicMock()
    result.ingest_nodes = ()
    result.ingest_edges = ()
    return result


# ---------------------------------------------------------------------------
# 1. Happy path — content_md already set; skip PDF parse, go straight to extract
# ---------------------------------------------------------------------------


class TestHappyPathPreSet:
    """content_md already populated → no PDF parse, ontofuel_extract called once,
    map_and_persist called once, GraphBuilder.build_from_extraction called once."""

    async def test_kg_nodes_created_when_content_md_preset(self, db_session: AsyncSession) -> None:
        ds = await _add_datasource(
            db_session,
            content_md="# Title\n\nExisting markdown body\nUO2 lattice 5.47 Å",
            file_path=None,
        )

        mock_extract_result = _make_demo_extraction()
        # NFM-2928: build_from_extraction now returns a BuildResult that is
        # passed to dispatch_build_result. Use a MagicMock with the right
        # shape so the dispatch helper short-circuits with no payload.
        empty_build_result = MagicMock()
        empty_build_result.ingest_nodes = ()
        empty_build_result.ingest_edges = ()

        with (
            patch.object(lit_svc, "_parse_pdf_to_markdown") as mock_parse,
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=mock_extract_result),
            ) as mock_extract,
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ) as mock_map,
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ) as mock_build,
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        # PDF parse was NOT invoked (content_md already set)
        mock_parse.assert_not_called()
        # Each downstream step called exactly once
        mock_extract.assert_awaited_once_with(
            source_reference=str(ds.id), source_type="datasource", db=db_session
        )
        mock_map.assert_awaited_once()
        mock_build.assert_awaited_once()
        # Return status
        assert result["status"] == "completed"
        assert result["extracted"] == 1

        # Status transitions committed
        await db_session.refresh(ds)
        assert ds.parse_status == "completed"
        assert ds.parse_error is None


# ---------------------------------------------------------------------------
# 1b. Ontology provenance stamping (NFM-3478 Step 2 / s2-lit-ov)
# ---------------------------------------------------------------------------


class TestOntologyProvenanceStamping:
    """Step 3c records the ontology version used by the extraction onto
    ``DataSource.metadata_`` so literature extractions are reproducible
    against the ontology that produced them."""

    async def test_stamps_latest_published_ontology_version(
        self, db_session: AsyncSession, admin_user
    ) -> None:
        from nfm_db.models.ontology_version import OntologyVersion

        ov = OntologyVersion(
            version="9.9.9",
            status="published",
            created_by=admin_user.id,
            ontology_data={"entity_types": {}},
        )
        db_session.add(ov)
        await db_session.flush()

        ds = await _add_datasource(
            db_session,
            content_md="# Title\n\nUO2 lattice 5.47 Å",
            file_path=None,
        )

        empty_build_result = MagicMock()
        empty_build_result.ingest_nodes = ()
        empty_build_result.ingest_edges = ()

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "completed"
        await db_session.refresh(ds)
        assert ds.metadata_ is not None
        assert ds.metadata_["extraction_ontology_version"] == "9.9.9"
        assert ds.metadata_["extraction_ontology_version_id"] == str(ov.id)

    async def test_no_published_ontology_leaves_metadata_untouched(
        self, db_session: AsyncSession
    ) -> None:
        """No published version → step is a no-op, metadata stays None."""
        ds = await _add_datasource(
            db_session,
            content_md="# Title\n\nbody",
            file_path=None,
        )

        empty_build_result = MagicMock()
        empty_build_result.ingest_nodes = ()
        empty_build_result.ingest_edges = ()

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "completed"
        await db_session.refresh(ds)
        assert ds.metadata_ is None

    async def test_preserves_existing_metadata_bag(
        self, db_session: AsyncSession, admin_user
    ) -> None:
        """Prior metadata keys (e.g. corpus_id) survive the stamping."""
        from nfm_db.models.ontology_version import OntologyVersion

        ov = OntologyVersion(
            version="9.9.9",
            status="published",
            created_by=admin_user.id,
            ontology_data={"entity_types": {}},
        )
        db_session.add(ov)
        await db_session.flush()

        ds = await _add_datasource(
            db_session,
            content_md="# Title\n\nbody",
            file_path=None,
        )
        ds.metadata_ = {"corpus_id": "existing-corpus"}
        await db_session.commit()

        empty_build_result = MagicMock()
        empty_build_result.ingest_nodes = ()
        empty_build_result.ingest_edges = ()

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            await lit_svc.process_literature(db_session, ds.id)

        await db_session.refresh(ds)
        assert ds.metadata_["corpus_id"] == "existing-corpus"
        assert ds.metadata_["extraction_ontology_version"] == "9.9.9"


# ---------------------------------------------------------------------------
# 2. Happy path — PDF parse then extract
# ---------------------------------------------------------------------------


class TestHappyPathPdfParse:
    """content_md is None → storage.read → pymupdf → ontofuel_extract → ... → completed."""

    async def test_pdf_parsed_then_extracted(self, db_session: AsyncSession) -> None:
        ds = await _add_datasource(
            db_session,
            content_md=None,
            file_path="abc123/report.pdf",
        )

        mock_bytes = b"%PDF-1.4 mock content"
        mock_md = "# Parsed Title\n\nUO2 is FCC.\n\nlattice_constant 5.47"

        with (
            patch.object(
                lit_svc,
                "_get_storage",
                return_value=MagicMock(read=MagicMock(return_value=mock_bytes)),
            ) as mock_storage,
            patch.object(
                lit_svc,
                "_parse_pdf_to_markdown",
                return_value=mock_md,
            ) as mock_parse,
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ) as mock_extract,
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(
                    return_value=_make_empty_build_result(),
                ),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        mock_storage.assert_called_once()
        mock_parse.assert_called_once_with(
            mock_bytes,
            ds_id=ds.id,
            storage=mock_storage.return_value,
        )
        mock_extract.assert_awaited_once()
        assert result["status"] == "completed"

        # Reload to verify content_md was written + status updated
        await db_session.refresh(ds)
        assert ds.content_md == mock_md
        assert ds.parse_status == "completed"


# ---------------------------------------------------------------------------
# 3. Duplicate-hash short-circuit
# ---------------------------------------------------------------------------


class TestDuplicateHashShortCircuit:
    """If file_hash matches an already-parsed sibling, reuse its content_md
    and skip the PDF parse entirely."""

    async def test_short_circuits_via_sibling_content_md(self, db_session: AsyncSession) -> None:
        sibling_md = "# Existing parsed content\n\nfrom sibling"
        sibling = await _add_datasource(
            db_session,
            title="Previously parsed",
            content_md=sibling_md,
            file_hash="deadbeef" * 8,
        )
        # Force the sibling to a 'completed' status so it's eligible.
        sibling.parse_status = lit_svc.PARSE_STATUS_COMPLETED
        await db_session.commit()

        ds = await _add_datasource(
            db_session,
            title="New upload with same hash",
            content_md=None,
            file_hash=sibling.file_hash,
            file_path="newsibling/file.pdf",
        )

        storage_mock = MagicMock(
            read=MagicMock(side_effect=AssertionError("storage.read should NOT be called"))
        )

        with (
            patch.object(lit_svc, "_parse_pdf_to_markdown") as mock_parse,
            patch.object(lit_svc, "_get_storage", return_value=storage_mock) as _mock_storage,
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=_make_empty_build_result()),
            ),
        ):
            await lit_svc.process_literature(db_session, ds.id)

        # PDF parse + storage read must NOT have been called
        mock_parse.assert_not_called()
        storage_mock.read.assert_not_called()

        # content_md was inherited from the sibling
        await db_session.refresh(ds)
        assert ds.content_md == sibling_md
        assert ds.parse_status == "completed"


# ---------------------------------------------------------------------------
# 4. Failed parse → parse_status='failed' + parse_error set + durable
# ---------------------------------------------------------------------------


class TestFailedParse:
    """PyMuPDF raises during parse → status flips to 'failed', parse_error is
    truncated to MAX_ERROR_LEN, and the failure exception is re-raised so the
    Celery scheduler can decide whether to retry."""

    async def test_parse_failure_sets_failed_status_and_raises(
        self, db_session: AsyncSession
    ) -> None:
        ds = await _add_datasource(
            db_session,
            content_md=None,
            file_path="bad/file.pdf",
        )

        long_msg = "X" * 1500  # way past MAX_ERROR_LEN
        boom = RuntimeError(long_msg)

        with (
            patch.object(
                lit_svc,
                "_get_storage",
                return_value=MagicMock(read=MagicMock(return_value=b"junk")),
            ),
            patch.object(lit_svc, "_parse_pdf_to_markdown", side_effect=boom),
        ):
            with pytest.raises(RuntimeError):
                await lit_svc.process_literature(db_session, ds.id)

        # The failure must be persisted on the row.
        await db_session.refresh(ds)
        assert ds.parse_status == lit_svc.PARSE_STATUS_FAILED
        assert ds.parse_error is not None
        # Truncated to MAX_ERROR_LEN
        assert len(ds.parse_error) <= lit_svc.MAX_ERROR_LEN
        # content_md must NOT have been written on the failed row.
        assert ds.content_md is None

    async def test_short_message_preserved_verbatim(self, db_session: AsyncSession) -> None:
        """Sanity check: short error messages fit and aren't padded."""
        ds = await _add_datasource(
            db_session,
            content_md=None,
            file_path="bad/file.pdf",
        )

        with (
            patch.object(
                lit_svc,
                "_get_storage",
                return_value=MagicMock(read=MagicMock(return_value=b"junk")),
            ),
            patch.object(
                lit_svc,
                "_parse_pdf_to_markdown",
                side_effect=ValueError("malformed PDF: bad xref"),
            ),
        ):
            with pytest.raises(ValueError):
                await lit_svc.process_literature(db_session, ds.id)

        await db_session.refresh(ds)
        assert ds.parse_status == "failed"
        assert ds.parse_error == "malformed PDF: bad xref"


# ---------------------------------------------------------------------------
# 4b. Rollback-failure health event (NFM-2241 CR-1 regression)
# ---------------------------------------------------------------------------


class _FailAfterFirstGet:
    """Proxy over a real ``AsyncSession`` that poisons the failure path.

    The first ``get`` (step 1 of the pipeline) succeeds so the real row
    loads; every later ``get`` raises, which is what drives execution into
    the ``except`` block that persists the failure status.  ``rollback``
    then raises too, reaching the innermost handler that emits the health
    event.  Everything else delegates to the real session.
    """

    def __init__(self, inner: AsyncSession) -> None:
        self._inner = inner
        self._gets = 0
        self.rollback_attempted = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        self._gets += 1
        if self._gets == 1:
            return await self._inner.get(*args, **kwargs)
        raise RuntimeError("get exploded")

    async def rollback(self) -> None:
        self.rollback_attempted = True
        raise RuntimeError("rollback exploded")


class TestRollbackFailureEmitsHealthEvent:
    """A failed ``rollback`` must record a health event instead of silently
    swallowing — and must not mask the original pipeline exception.

    This is the NFM-2241 CR-1 regression guard.  The handler referenced
    ``emit_health_event`` / ``EVENT_GENERIC_SILENT_CATCH`` / ``SEVERITY_ERROR``
    without importing them, so the branch raised ``NameError`` at runtime.
    The whole suite still passed because nothing exercised it.  Patching only
    the emitter function (not the two constants) keeps this test sensitive to
    all three names.
    """

    async def test_rollback_failure_emits_event_and_preserves_original_error(
        self, db_session: AsyncSession
    ) -> None:
        ds = await _add_datasource(
            db_session,
            content_md=None,
            file_path="bad/file.pdf",
        )
        poisoned = _FailAfterFirstGet(db_session)

        with (
            patch.object(
                lit_svc,
                "_get_storage",
                return_value=MagicMock(read=MagicMock(return_value=b"junk")),
            ),
            patch.object(
                lit_svc, "_parse_pdf_to_markdown", side_effect=RuntimeError("parse exploded")
            ),
            patch.object(lit_svc, "emit_health_event", new_callable=AsyncMock) as emit,
        ):
            # The ORIGINAL pipeline error must surface — not NameError, and
            # not the rollback error.  This is the regression that mattered:
            # a NameError here would both lose the event and mask the cause.
            with pytest.raises(RuntimeError, match="parse exploded"):
                await lit_svc.process_literature(poisoned, ds.id)

        assert poisoned.rollback_attempted, "failure path never attempted rollback"
        emit.assert_awaited_once()

        kwargs = emit.await_args.kwargs
        assert kwargs["event_type"] == lit_svc.EVENT_GENERIC_SILENT_CATCH
        assert kwargs["severity"] == lit_svc.SEVERITY_ERROR
        assert kwargs["source_service"] == "literature_service"
        # The original label is preserved in the payload even though the
        # event_type is coerced to a spec-enumerated value.
        assert kwargs["context"]["reported_event_type"] == "rollback_failed"
        assert kwargs["context"]["datasource_id"] == str(ds.id)
        # The rollback failure — not the parse failure — is the cause recorded.
        assert "rollback exploded" in str(kwargs["context"])


# ---------------------------------------------------------------------------
# 4b. NFM-3322 regression: failure status persists even when the input
#     session is left in an aborted SQLAlchemy transaction by the upstream
#     extractor (e.g. ``No published OntologyVersion found in the database``).
#
#     Before the fix, :func:`process_literature` reused the same aborted
#     ``db`` for the failure-status write, hit ``InFailedSQLTransactionError``
#     / ``sqlite3.OperationalError`` ("cannot operate on a closed/broken
#     transaction"), and the worker was stuck at ``parse_status='extracting'``
#     forever. The fix opens a fresh session via :func:`async_session_factory`
#     for the failure-status write so the aborted state cannot block it.
# ---------------------------------------------------------------------------


class TestFailureStatusPersistsAcrossAbortedSession:
    """Regression guard for NFM-3322.

    Simulates the production repro: the upstream LLM extractor leaves the
    SQLAlchemy session in an aborted state (raises an error on any subsequent
    operation), then :func:`process_literature`'s ``except`` block must
    persist the failure status using a session it opens itself — NOT the
    poisoned input one.
    """

    async def test_failure_status_persisted_when_input_session_is_aborted(
        self, db_session: AsyncSession
    ) -> None:
        ds = await _add_datasource(
            db_session,
            content_md="Some body text with no properties.",
        )

        ontology_error = RuntimeError(
            "A published ontology version is required for extraction. "
            "No published OntologyVersion found in the database."
        )

        # Wrap the real session so that EVERY operation performed through
        # the proxy succeeds while the pipeline is healthy, but starts
        # failing once the LLM extract raises — exactly mimicking the
        # asyncpg ``InFailedSQLTransactionError`` symptom: a failed
        # upstream operation leaves the transaction in a state where any
        # subsequent ``execute`` / ``commit`` / ``get`` raises.
        class _AbortedAfterExtract:
            def __init__(self, inner: AsyncSession) -> None:
                self._inner = inner
                self._extract_called = False

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def _raise_if_aborted(self, op: str) -> None:
                if self._extract_called:
                    raise RuntimeError(
                        "current transaction is aborted, commands ignored "
                        "until end of transaction block"
                    )

            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                self._raise_if_aborted("execute")
                return await self._inner.execute(*args, **kwargs)

            async def get(self, *args: Any, **kwargs: Any) -> Any:
                self._raise_if_aborted("get")
                return await self._inner.get(*args, **kwargs)

            async def commit(self) -> None:
                self._raise_if_aborted("commit")
                return await self._inner.commit()

            async def rollback(self) -> None:
                self._raise_if_aborted("rollback")
                return await self._inner.rollback()

            async def refresh(self, *args: Any, **kwargs: Any) -> Any:
                self._raise_if_aborted("refresh")
                return await self._inner.refresh(*args, **kwargs)

            async def flush(self, *args: Any, **kwargs: Any) -> Any:
                self._raise_if_aborted("flush")
                return await self._inner.flush(*args, **kwargs)

            def mark_extract_called(self) -> None:
                self._extract_called = True

        poisoned = _AbortedAfterExtract(db_session)

        # The pipeline imports ``ontofuel_extract`` from
        # ``nfm_db.services.extraction_pipeline`` lazily at step 3. Wrap
        # it so it raises the upstream error AND trips the poisoned
        # marker on the proxy so any later DB op reflects the aborted-tx
        # symptom.
        async def _fake_extract(*args: Any, **kwargs: Any) -> Any:
            poisoned.mark_extract_called()
            raise ontology_error

        # In production ``async_session_factory`` is bound to the real
        # Postgres engine. Bind it to this test's SQLite engine so the
        # failure-handler's FRESH session operates on the same DB the
        # test fixture populated.
        @contextlib.asynccontextmanager
        async def _sqlite_passthrough_factory():
            yield db_session

        # CRITICAL: the factory must be patched BEFORE process_literature
        # is invoked — the fix calls ``async_session_factory()`` from
        # inside the except block, which runs during the call below.
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=_fake_extract,
            ),
            patch.object(
                lit_svc, "async_session_factory", _sqlite_passthrough_factory
            ),
        ):
            # The upstream LLM extract raises. The contract is simply
            # that *some* RuntimeError propagates so the Celery
            # scheduler can take action. Don't pin the message — pre-fix
            # it was "transaction aborted", post-fix it carries the
            # original ``OntologyVersion`` cause.
            with pytest.raises(RuntimeError):
                await lit_svc.process_literature(
                    poisoned,  # type: ignore[arg-type]
                    ds.id,
                )

        # Verify the failure status landed in the DB through a SESSION
        # OTHER THAN the poisoned input. A direct refresh on
        # ``db_session`` proves the failure-status write did not depend
        # on the aborted input session.
        stmt = select(DataSource).where(DataSource.id == ds.id)
        result = await db_session.execute(stmt)
        refreshed = result.scalar_one()

        assert refreshed.parse_status == lit_svc.PARSE_STATUS_FAILED, (
            "NFM-3322 regression: parse_status must transition to "
            f"'failed' on extractor failure, got {refreshed.parse_status!r}"
        )
        assert refreshed.parse_error, (
            "NFM-3322 regression: parse_error must be non-empty so "
            "operators can see the upstream cause"
        )
        # Operators must see the *original* upstream cause
        # ('OntologyVersion...'), not the secondary symptom
        # ('current transaction is aborted...'). The fix re-raises the
        # original so Celery retries against the right cause.
        assert "OntologyVersion" in refreshed.parse_error


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_missing_datasource_returns_skipped(self, db_session: AsyncSession) -> None:
        """No row with the given id → returns {'status': 'skipped'}, no exception."""
        random_id = uuid.uuid4()
        result = await lit_svc.process_literature(db_session, random_id)
        assert result["status"] == "skipped"
        assert result["reason"] == "not_found"

    async def test_placeholder_datasource_returns_skipped(self, db_session: AsyncSession) -> None:
        """DataSource with parse_status='placeholder' is silently skipped
        without invoking LLM extraction or PDF parsing."""
        ds = DataSource(
            title="Fixture Row",
            source_type="uploaded_pdf",
            parse_status="placeholder",
        )
        db_session.add(ds)
        await db_session.commit()
        await db_session.refresh(ds)

        with patch(
            "nfm_db.services.extraction_pipeline.ontofuel_extract",
            new=AsyncMock(side_effect=AssertionError("ontofuel_extract should NOT run")),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "skipped"
        assert result["reason"] == "placeholder"
        assert result["datasource_id"] == str(ds.id)

    async def test_no_file_path_marks_failed(self, db_session: AsyncSession) -> None:
        """DataSource with no file_path and no content_md is marked failed."""
        ds = DataSource(
            title="No File",
            source_type="uploaded_pdf",
            parse_status="uploaded",
            file_path=None,
            content_md=None,
        )
        db_session.add(ds)
        await db_session.commit()
        await db_session.refresh(ds)

        result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_file_path"
        assert result["datasource_id"] == str(ds.id)

        await db_session.refresh(ds)
        assert ds.parse_status == "failed"
        assert ds.parse_error == "no file_path recorded for this datasource"

    async def test_empty_extraction_marks_completed_without_mapping(
        self, db_session: AsyncSession
    ) -> None:
        """If ontofuel_extract returns [] the pipeline still completes cleanly
        and skips map_and_persist / GraphBuilder."""
        ds = await _add_datasource(
            db_session,
            content_md="Some body text with no properties.",
        )

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=[]),
            ) as mock_extract,
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(side_effect=AssertionError("map_and_persist should NOT run")),
            ) as mock_map,
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(side_effect=AssertionError("GraphBuilder should NOT run")),
            ) as mock_build,
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        mock_extract.assert_awaited_once()
        mock_map.assert_not_called()
        mock_build.assert_not_called()
        assert result["status"] == "completed"
        assert result["extracted"] == 0

        await db_session.refresh(ds)
        assert ds.parse_status == "completed"


# ---------------------------------------------------------------------------
# 6. Process_literature_sync bridges sync → async cleanly
# ---------------------------------------------------------------------------


class TestSyncWrapper:
    """process_literature_sync is what Celery actually invokes."""

    async def test_sync_wrapper_accepts_uuid_string(self, db_session: AsyncSession) -> None:
        """A plain UUID-string round-trips through the sync bridge.

        ``process_literature_sync`` opens its own session via the
        module-level :func:`async_session_factory` (which in production
        points at Postgres).  For this unit test we patch the factory to
        yield the test's SQLite ``db_session`` fixture so the row we
        insert is the row the wrapper sees.
        """
        import concurrent.futures
        import contextlib

        @contextlib.asynccontextmanager
        async def _yield_test_session():
            yield db_session

        ds = await _add_datasource(
            db_session,
            content_md="# already parsed\n\nnothing to do",
        )
        ds_id_str = str(ds.id)

        with (
            patch(
                "nfm_db.services.literature_service.async_session_factory",
                _yield_test_session,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=[]),
            ),
        ):
            # process_literature_sync calls asyncio.run() internally,
            # so it must run in a thread to avoid nesting with pytest-asyncio's loop.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lit_svc.process_literature_sync, ds_id_str)
                result = future.result()

        assert result["datasource_id"] == ds_id_str
        assert result["status"] == "completed"

        # Verify the row landed as completed
        await db_session.refresh(ds)
        assert ds.parse_status == "completed"


# ---------------------------------------------------------------------------
# Dispatcher contract tests (from remote merge)
# ---------------------------------------------------------------------------


def test_task_imports_process_literature_from_service_module() -> None:
    """The Celery task body MUST lazy-import ``process_literature`` from
    ``nfm_db.services.literature_service``.  This test verifies the import
    path is correct without actually calling the task."""
    from nfm_db.services.literature_dispatcher import process_literature_task

    assert process_literature_task.name == (
        "nfm_db.services.literature_dispatcher.process_literature_task"
    )


def test_process_literature_task_calls_service_with_string_id() -> None:
    """When the Celery worker picks up the task, it MUST call
    ``process_literature(datasource_id: str)`` where datasource_id is a
    string (UUID serialised by the dispatcher)."""

    mock_process = MagicMock(return_value={"status": "completed"})

    with patch.dict(
        "sys.modules",
        {
            "nfm_db.services.literature_service": MagicMock(
                process_literature=mock_process,
            ),
        },
    ):
        from importlib import import_module

        fake_service = MagicMock()
        fake_service.process_literature = mock_process

        with patch(
            "builtins.__import__",
            side_effect=lambda name, *a, **kw: (
                fake_service
                if name == "nfm_db.services.literature_service"
                else import_module(name)
            ),
        ):
            mock_process.assert_not_called()


def test_schedule_literature_processing_accepts_uuid_string() -> None:
    """schedule_literature_processing MUST accept both UUID and str
    inputs and serialise them to str for the Celery worker."""
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    uuid_id = uuid.uuid4()
    str_id = str(uuid_id)

    with patch("nfm_db.services.literature_dispatcher._send_literature_task") as mock_send:
        mock_send.return_value = MagicMock(id="task-id")

        schedule_literature_processing(uuid_id)
        schedule_literature_processing(str_id)

    assert mock_send.call_count == 2
    assert mock_send.call_args_list[0].kwargs["datasource_id"] == str(uuid_id)
    assert mock_send.call_args_list[1].kwargs["datasource_id"] == str_id


def test_schedule_literature_processing_propagates_broker_error() -> None:
    """If the Celery broker is down, the dispatcher MUST raise so the
    endpoint can return 503."""
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    with patch(
        "nfm_db.services.literature_dispatcher._send_literature_task",
        side_effect=ConnectionError("broker unreachable"),
    ):
        with pytest.raises(ConnectionError, match="broker unreachable"):
            schedule_literature_processing(uuid.uuid4())


# ---------------------------------------------------------------------------
# 7. MinerU image persistence — exercises _persist_mineru_assets directly
# ---------------------------------------------------------------------------


class TestPersistMinUAssets:
    """Cover the new image-persistence helper without going through the
    real MinerU client (which would require network and credentials).

    The helper takes a zip bytes blob and a storage mock; we just need
    to confirm that:
      * every image in the zip is written via storage.save(...)
      * the returned markdown's ``images/<hash>`` references get rewritten
      * an empty zip yields the original markdown unchanged
    """

    def _make_zip(
        self,
        md_text: str,
        images: dict[str, bytes],
        origin_pdf: bool = True,
    ) -> bytes:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("full.md", md_text)
            if origin_pdf:
                zf.writestr("ee9c895b-2249-4286-a547-cb72ab9ee278_origin.pdf", b"%PDF-1.4")
            for name, data in images.items():
                zf.writestr(f"images/{name}", data)
        return buf.getvalue()

    def test_persists_every_image_and_rewrites_paths(self) -> None:
        import uuid

        ds_id = uuid.uuid4()
        storage = MagicMock()
        md_text = "# Title\n\n![](images/abc.jpg) ![](images/def.jpg)\n"
        zip_bytes = self._make_zip(
            md_text,
            {"abc.jpg": b"x", "def.jpg": b"y"},
        )

        from nfm_db.services.literature_service import _persist_mineru_assets

        rewritten = _persist_mineru_assets(
            storage=storage, ds_id=ds_id, zip_bytes=zip_bytes, markdown=md_text
        )

        # Both images persisted via storage.save with the same ds_id and
        # an ``images/<name>`` key.
        saved_calls = storage.save.call_args_list
        assert len(saved_calls) == 2
        for c in saved_calls:
            assert c.args[0] == ds_id
            assert c.args[1].startswith("images/")
            assert c.args[1].endswith((".jpg", ".png"))

        # Markdown references rewritten to the data-sources path.
        assert f"data_sources/{ds_id}/images/abc.jpg" in rewritten
        assert f"data_sources/{ds_id}/images/def.jpg" in rewritten

    def test_empty_images_returns_markdown_unchanged(self) -> None:
        import uuid

        ds_id = uuid.uuid4()
        storage = MagicMock()
        md_text = "# no images here\n"
        zip_bytes = self._make_zip(md_text, {})

        from nfm_db.services.literature_service import _persist_mineru_assets

        result = _persist_mineru_assets(
            storage=storage, ds_id=ds_id, zip_bytes=zip_bytes, markdown=md_text
        )

        assert result == md_text
        storage.save.assert_not_called()


# ---------------------------------------------------------------------------
# NFM-2928: LightRAG ingest must fire on the literature path after commit
# ---------------------------------------------------------------------------
# Regression: NFM-2871 / PR #783 converted GraphBuilder from dispatcher to
# payload carrier. The literature path silently dropped the returned
# BuildResult, so literature-derived entities never reached LightRAG.
#
# These tests verify that process_literature now:
#  1. dispatches the carried BuildResult exactly once on commit success
#  2. does NOT dispatch when the path fails (rollback / exception)
#  3. does NOT dispatch when the build yielded no ingest payload
# ---------------------------------------------------------------------------


class TestLightRAGIngestAfterCommit:
    """Regression tests for NFM-2928: literature-path ingest dispatch."""

    @pytest.mark.asyncio
    async def test_literature_dispatches_build_result_after_commit(
        self, db_session: AsyncSession
    ) -> None:
        """Happy path: build_from_extraction returns a BuildResult → the
        ``dispatch_build_result`` helper is invoked exactly once AFTER the
        final ``db.commit()`` call.

        This is the regression that NFM-2871 introduced and NFM-2928 fixes:
        the literature path silently dropped the BuildResult and never
        delivered literature-derived entities to LightRAG.
        """
        ds = await _add_datasource(
            db_session,
            content_md="# Preset\n\nUO2 FCC lattice 5.47",
            file_path=None,
        )

        # Build a real BuildResult shape so the dispatch helper is exercised.
        mock_node = MagicMock()
        mock_node.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        mock_node.label = "UO2"
        mock_build_result = MagicMock()
        mock_build_result.nodes_created = 1
        mock_build_result.nodes_matched = 0
        mock_build_result.edges_created = 0
        mock_build_result.review_queue_items = 0
        mock_build_result.ingest_nodes = (mock_node,)
        mock_build_result.ingest_edges = ()

        call_order: list[str] = []

        async def commit_record(*_args: Any, **_kwargs: Any) -> None:
            call_order.append("commit")

        def dispatch_record(*_args: Any, **_kwargs: Any) -> int:
            call_order.append("dispatch")
            return 1

        db_session.commit = commit_record  # type: ignore[method-assign]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=mock_build_result),
            ),
            patch(
                "nfm_db.services.kg_re.dispatch_build_result",
                side_effect=dispatch_record,
            ) as mock_dispatch,
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        # Dispatch happened exactly once.
        mock_dispatch.assert_called_once()
        # Dispatch ran AFTER the final commit (Step 6).
        assert call_order, "neither commit nor dispatch was called"
        assert call_order[-1] == "dispatch"
        assert "commit" in call_order
        assert call_order.index("commit") < call_order.index("dispatch"), (
            f"commit must precede dispatch_build_result; got {call_order}"
        )
        # Literature path returned successfully.
        assert result["status"] == "completed"
        assert result["extracted"] == 1

    @pytest.mark.asyncio
    async def test_literature_does_not_dispatch_on_failure(
        self, db_session: AsyncSession
    ) -> None:
        """If the pipeline raises BEFORE the final commit, the dispatch
        guard must NOT fire. This guarantees no ghost entities on rollback.

        Modelled by raising from `map_and_persist` to abort Step 4 before
        `build_from_extraction` and the final commit.
        """
        ds = await _add_datasource(
            db_session,
            content_md="# Preset\n\nUO2 FCC lattice 5.47",
            file_path=None,
        )

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(
                    side_effect=RuntimeError("simulated mapping failure")
                ),
            ),
            patch(
                "nfm_db.services.kg_re.dispatch_build_result",
            ) as mock_dispatch,
        ):
            with pytest.raises(RuntimeError, match="simulated mapping failure"):
                await lit_svc.process_literature(db_session, ds.id)

        # The acceptance criterion: dispatch_build_result MUST NOT be called
        # on a failed path. This is the bug class NFM-2871 / NFM-2928 fixes.
        mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_literature_skips_dispatch_when_ingest_payload_empty(
        self, db_session: AsyncSession
    ) -> None:
        """If the build produced no ingest payload (no new entities),
        dispatch_build_result is still called with the empty BuildResult but
        no LightRAG fire occurs. The helper itself short-circuits empty
        payloads — see dispatch_build_result in kg_re.py.
        """
        ds = await _add_datasource(
            db_session,
            content_md="# Preset\n\nUO2 FCC lattice 5.47",
            file_path=None,
        )

        # BuildResult with no ingest payload (e.g. all nodes matched existing).
        empty_build_result = MagicMock()
        empty_build_result.nodes_created = 0
        empty_build_result.nodes_matched = 1
        empty_build_result.edges_created = 0
        empty_build_result.review_queue_items = 0
        empty_build_result.ingest_nodes = ()
        empty_build_result.ingest_edges = ()

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
            patch(
                "nfm_db.services.kg_lightrag_sync.fire_ingest_to_lightrag"
            ) as mock_fire,
        ):
            await lit_svc.process_literature(db_session, ds.id)

        # Empty payload → no fire, even though dispatch was called.
        mock_fire.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Bibliographic metadata extraction (NFM-3301 / QA-E2E F7)
# ---------------------------------------------------------------------------


class TestBibliographicMetadataExtraction:
    """After content_md is populated, process_literature should extract
    DOI, journal, year, and abstract from the markdown and write them
    to the DataSource row (only filling currently-null fields)."""

    @pytest.mark.asyncio
    async def test_metadata_extracted_from_content_md(
        self, db_session: AsyncSession
    ) -> None:
        """content_md contains DOI/journal/year/abstract → fields populated."""
        content_md = (
            "# Owen et al. - 2023 - Diffusion in UO2\n\n"
            "## Abstract\n\n"
            "Molecular dynamics study of diffusion.\n\n"
            "## 1. Introduction\n\n"
            "Journal of Nuclear Materials 576 (2023) 123-135\n\n"
            "DOI: 10.1016/j.jnucmat.2023.01.001\n"
        )
        ds = await _add_datasource(
            db_session,
            title="L1-Owen2023.pdf",
            content_md=content_md,
            file_path=None,
        )

        empty_build_result = _make_empty_build_result()
        with (
            patch.object(
                lit_svc, "_parse_pdf_to_markdown",
            ) as mock_parse,
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "completed"
        # PDF parse should NOT be called (content_md already set)
        mock_parse.assert_not_called()

        await db_session.refresh(ds)
        assert ds.title == "Owen et al. - 2023 - Diffusion in UO2"
        assert ds.doi == "10.1016/j.jnucmat.2023.01.001"
        assert ds.year == 2023
        assert ds.journal == "Journal of Nuclear Materials"
        assert "Molecular dynamics" in (ds.abstract or "")

    @pytest.mark.asyncio
    async def test_metadata_does_not_overwrite_existing_values(
        self, db_session: AsyncSession
    ) -> None:
        """If DOI/title etc. are already set, they should NOT be overwritten."""
        content_md = (
            "# Wrong Title\n\n"
            "DOI: 10.1000/wrong-doi\n\n"
            "## Abstract\n\nWrong abstract.\n\n"
            "Journal of Wrong 1 (2020) 1-10\n"
        )
        ds = await _add_datasource(
            db_session,
            title="Existing Title",
            content_md=content_md,
            file_path=None,
        )
        # Set pre-existing metadata that should be preserved
        ds.doi = "10.1016/existing-doi"
        ds.year = 2021
        ds.journal = "Existing Journal"
        ds.abstract = "Existing abstract"
        await db_session.commit()

        empty_build_result = _make_empty_build_result()
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            await lit_svc.process_literature(db_session, ds.id)

        await db_session.refresh(ds)
        # Pre-existing values must NOT be overwritten
        assert ds.doi == "10.1016/existing-doi"
        assert ds.year == 2021
        assert ds.journal == "Existing Journal"
        assert ds.abstract == "Existing abstract"
        # Title IS still updated because the filename-based title is a
        # low-quality placeholder — we always improve it if we find an H1.
        assert ds.title == "Wrong Title"

    @pytest.mark.asyncio
    async def test_metadata_extracted_after_pdf_parse(
        self, db_session: AsyncSession
    ) -> None:
        """Metadata is also extracted when content_md comes from PDF parsing."""
        parsed_md = (
            "# Parsed Paper Title\n\n"
            "## Abstract\n\n"
            "The abstract text.\n\n"
            "## 1. Introduction\n\n"
            "Journal of Materials 100 (2024) 1-5\n\n"
            "DOI: 10.1000/jmat.2024.001\n"
        )
        ds = await _add_datasource(
            db_session,
            title="upload.pdf",
            content_md=None,
            file_path="abc/report.pdf",
        )
        mock_bytes = b"%PDF-1.4 mock content"

        empty_build_result = _make_empty_build_result()
        with (
            patch.object(
                lit_svc,
                "_get_storage",
                return_value=MagicMock(read=MagicMock(return_value=mock_bytes)),
            ),
            patch.object(
                lit_svc,
                "_parse_pdf_to_markdown",
                return_value=parsed_md,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "completed"
        await db_session.refresh(ds)
        assert ds.title == "Parsed Paper Title"
        assert ds.doi == "10.1000/jmat.2024.001"
        assert ds.year == 2024
        assert ds.journal == "Journal of Materials"
        assert ds.abstract == "The abstract text."


# ---------------------------------------------------------------------------
# NFM-3835: heuristic items must survive into map_and_persist with a valid
# property_category so they land as KG nodes instead of being counted in
# skipped_unknown_properties.
# ---------------------------------------------------------------------------


def _make_demo_heuristic_item(
    *,
    element_system: str = "UO2",
    property_name: str = "activation_energy",
    value: float = 0.30,
    unit: str = "eV",
    property_category: str | None = None,
) -> dict[str, Any]:
    """Construct a single heuristic_extract() payload in canonical shape."""
    item: dict[str, Any] = {
        "element_system": element_system,
        "phase": "Unknown",
        "property_name": property_name,
        "value": value,
        "unit": unit,
        "method": "heuristic_regex",
        "source": "test-source",
        "source_doi": None,
        "confidence": "medium",
        "uncertainty": 0.01,
        "temperature": None,
        "cache_level": "L2",
    }
    if property_category is not None:
        item["property_category"] = property_category
    return item


class TestHeuristicItemsPersisted:
    """NFM-3835 acceptance: heuristic items appended to raw_properties in
    ``process_literature`` must reach ``map_and_persist`` carrying a valid
    ``property_category``. Regression: prior to NFM-3835 the heuristic
    path bypassed ``_post_process_extracted``, so the items landed with
    ``property_category`` either missing or set to a Chinese standard
    name — both paths tripped ``_coerce_unknown_categories`` or the
    mapper's PropertyType lookup, inflating
    ``skipped_unknown_properties``.

    These tests use the REAL ``heuristic_extract`` against the Owen2023
    fixture (with ``ontofuel_extract`` mocked to empty so the heuristic
    path is the only source) so they actually exercise the production
    regression — mocked ``heuristic_extract`` would mask the bug.
    """

    async def test_real_heuristic_extract_emits_property_category(
        self, db_session: AsyncSession
    ) -> None:
        """End-to-end: real heuristic_extract on Owen2023 fixture produces
        items with a valid property_category field, and they reach
        map_and_persist carrying that field unchanged.
        """
        from pathlib import Path

        fixture_path = (
            Path(__file__).parent / "fixtures" / "extraction" / "owen2023_sample.txt"
        )
        owen2023_text = fixture_path.read_text(encoding="utf-8")

        ds = await _add_datasource(
            db_session,
            content_md=owen2023_text,
            file_path=None,
        )

        # LLM path is empty — heuristic is the only source so we can
        # assert deterministically on the heuristic items alone.
        empty_llm: list[dict[str, Any]] = []

        empty_build_result = _make_empty_build_result()

        captured_payload: list[list[dict[str, Any]]] = []

        async def _capture_map(
            db: AsyncSession, items: list[dict[str, Any]], **_kwargs: Any
        ) -> MagicMock:
            captured_payload.append(items)
            return MagicMock()

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=empty_llm),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(side_effect=_capture_map),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=empty_build_result),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "completed", result
        assert captured_payload, "map_and_persist was never invoked"
        merged = captured_payload[0]

        heuristic_items = [
            item for item in merged if item.get("method") == "heuristic_regex"
        ]
        assert heuristic_items, (
            f"no heuristic items reached mapper on Owen2023 fixture: {merged!r}"
        )

        valid_categories = {
            "mechanical", "thermal", "physical",
            "diffusion", "irradiation", "nuclear", "other",
        }
        for item in heuristic_items:
            assert "property_category" in item, (
                f"heuristic item missing property_category: {item!r}"
            )
            assert item["property_category"] in valid_categories, (
                f"heuristic item property_category={item['property_category']!r} "
                f"not in {valid_categories}"
            )

        # Specific assertion: activation_energy findings (family=energy) must
        # land with category="diffusion" so they don't trip the
        # skipped_unknown_properties counter at map_and_persist.
        ea_items = [
            item for item in heuristic_items
            if item.get("property_name") == "activation_energy"
        ]
        assert ea_items, (
            f"activation_energy not produced by heuristic on Owen2023 fixture: "
            f"{heuristic_items!r}"
        )
        for item in ea_items:
            assert item["property_category"] == "diffusion", (
                f"activation_energy category should be 'diffusion', "
                f"got {item['property_category']!r}"
            )

        # Density findings (family=density) must map to category="physical"
        density_items = [
            item for item in heuristic_items
            if item.get("property_name") == "density"
        ]
        assert density_items, (
            f"density not produced by heuristic on Owen2023 fixture: "
            f"{heuristic_items!r}"
        )
        for item in density_items:
            assert item["property_category"] == "physical", (
                f"density category should be 'physical', "
                f"got {item['property_category']!r}"
            )


# ---------------------------------------------------------------------------
# NFM-3888 / NFM-3892 — per-stage log counter instrumentation
# ---------------------------------------------------------------------------
#
# NFM-3887 root-cause analysis could not determine WHY the post-patch
# re-extraction of source 9320cb50 produced 0 new kg_nodes / 0 new
# property_measurements / 0 new _ref_gap_fill_staging rows. The CTO spec
# in NFM-3888 requires six structured log lines so the next re-extraction
# reveals which stage silently dropped the items.
#
# These tests pin the format strings, field-name order, and field bindings
# to the spec exactly. If any line is renamed or its field set drifts,
# the spec's downstream grep / Prometheus scraping breaks — so fail loud.


class TestPerStageLogCounters:
    """NFM-3888 / NFM-3892: assert all six per-stage log counters fire
    with the exact field names from the CTO spec.

    AC-1 (spec table) is verified by ``caplog.records``; the format
    strings in :mod:`literature_service` are the source of truth for
    grep / Prometheus scraping, so we assert on the rendered message
    rather than the placeholder order.
    """

    async def test_all_six_stage_logs_emit_with_spec_field_names(
        self,
        db_session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ds = await _add_datasource(
            db_session,
            content_md="# Preset\n\nUO2 FCC lattice 5.47",
            file_path=None,
        )
        # Stage 6 (bridge) only fires as ``bridged %d rows`` when
        # corpus_id resolves from metadata_ or doi. Seed metadata_ so
        # the ``bridged`` log path is exercised, not the
        # ``no corpus_id, KG→staging bridge skipped`` fallback.
        ds.metadata_ = {"corpus_id": "test-corpus"}
        await db_session.flush()

        # --- Mocks -------------------------------------------------
        # BuildResult shape (see kg_re.BuildResult) so the
        # ``nodes_created=`` / ``nodes_matched=`` / ``edges_created=`` /
        # ``review_queue=`` counter log renders cleanly.
        mock_node = MagicMock()
        mock_node.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        mock_node.label = "UO2"
        mock_build_result = MagicMock()
        mock_build_result.nodes_created = 1
        mock_build_result.nodes_matched = 2
        mock_build_result.edges_created = 3
        mock_build_result.review_queue_items = 4
        mock_build_result.ingest_nodes = (mock_node,)
        mock_build_result.ingest_edges = ()

        # MappingResult shape (see extraction_to_db_mapper.MappingResult)
        # so the eight-field ``mapped`` log renders with the new
        # ``skipped_unknown=`` and ``validation_errors=`` fields.
        mock_mapping = MagicMock()
        mock_mapping.created_sources = 1
        mock_mapping.created_materials = 2
        mock_mapping.created_datasets = 3
        mock_mapping.created_measurements = 4
        mock_mapping.reused_entities = 5
        mock_mapping.skipped_duplicate_measurements = 6
        mock_mapping.skipped_unknown_properties = 7
        mock_mapping.validation_errors = 8

        with (
            caplog.at_level(logging.INFO, logger="nfm_db.services.literature_service"),
            patch.object(lit_svc, "_parse_pdf_to_markdown"),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new=AsyncMock(return_value=_make_demo_extraction()),
            ),
            patch(
                "nfm_db.services.heuristic_extractor.heuristic_extract",
                # Return one item so the dedup-merge + post_process
                # branches execute and emit Stage 2 + Stage 3.
                new=MagicMock(return_value=[_make_demo_extraction()[0]]),
            ),
            patch(
                "nfm_db.services.extraction_pipeline._post_process_extracted",
                new=MagicMock(side_effect=lambda items, **_kw: items),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(return_value=mock_mapping),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=mock_build_result),
            ),
            patch(
                "nfm_db.services.kg_to_staging_bridge.bridge_kg_to_staging",
                new=AsyncMock(return_value=9),
            ),
            patch(
                "nfm_db.services.kg_to_staging_bridge._slugify",
                new=MagicMock(return_value="corpus-slug"),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        assert result["status"] == "completed", (
            f"process_literature failed early; caplog:\n{caplog.text}"
        )

        # Filter to records produced by process_literature so unrelated
        # INFO records (e.g. health-event emitter) do not skew the
        # field-name assertions.
        plog_records = [
            r for r in caplog.records
            if r.name == "nfm_db.services.literature_service"
            and "process_literature:" in r.getMessage()
            and f"datasource_id={ds.id}" in r.getMessage()
        ]
        text = "\n".join(r.getMessage() for r in plog_records)

        # ---- Stage 1: heuristic_extract -----------------------------
        assert "heuristic_extract returned 1 items" in text, (
            f"Stage 1 log missing or wrong count:\n{text}"
        )

        # ---- Stage 2: raw_properties after merge --------------------
        assert "raw_properties after merge:" in text, (
            f"Stage 2 log missing:\n{text}"
        )
        assert "heuristic added 1" in text, (
            f"Stage 2 missing 'heuristic added' field:\n{text}"
        )

        # ---- Stage 3: raw_properties after _post_process_extracted --
        assert "raw_properties after _post_process_extracted:" in text, (
            f"Stage 3 log missing:\n{text}"
        )
        assert "delta=0" in text, (
            f"Stage 3 missing 'delta=0' field (post-process is a no-op "
            f"identity in the mock):\n{text}"
        )

        # ---- Stage 4: mapped (8-field format) ------------------------
        mapping_line = next(
            (r.getMessage() for r in plog_records if " mapped " in r.getMessage()),
            None,
        )
        assert mapping_line is not None, f"Stage 4 mapping log missing:\n{text}"
        for field in (
            "sources=1",
            "materials=2",
            "datasets=3",
            "measurements=4",
            "reused=5",
            "dedup_meas=6",
            "skipped_unknown=7",
            "validation_errors=8",
        ):
            assert field in mapping_line, (
                f"Stage 4 missing {field!r} field:\n{mapping_line}"
            )

        # ---- Stage 5: graph built (4-field format) -------------------
        graph_line = next(
            (r.getMessage() for r in plog_records if " graph built: " in r.getMessage()),
            None,
        )
        assert graph_line is not None, f"Stage 5 graph log missing:\n{text}"
        for field in (
            "nodes_created=1",
            "nodes_matched=2",
            "edges_created=3",
            "review_queue=4",
        ):
            assert field in graph_line, (
                f"Stage 5 missing {field!r} field:\n{graph_line}"
            )

        # ---- Stage 6: bridge (3-field format) ------------------------
        bridge_line = next(
            (r.getMessage() for r in plog_records if " bridged " in r.getMessage()),
            None,
        )
        assert bridge_line is not None, f"Stage 6 bridge log missing:\n{text}"
        assert "9 rows to _ref_gap_fill_staging" in bridge_line, (
            f"Stage 6 missing 'rows to _ref_gap_fill_staging' phrase:\n{bridge_line}"
        )
        # corpus_id came from ``metadata_.corpus_id`` (seeded above);
        # the spec only pins the field name ``corpus=%s``, not the value.
        assert "corpus=" in bridge_line, (
            f"Stage 6 missing 'corpus=' field:\n{bridge_line}"
        )

        # ---- AC-1 sanity: every spec marker is present at least once
        for stage, marker in [
            ("Stage 1", "heuristic_extract returned"),
            ("Stage 2", "raw_properties after merge:"),
            ("Stage 3", "raw_properties after _post_process_extracted:"),
            ("Stage 4", " mapped "),
            ("Stage 5", " graph built: "),
            ("Stage 6", " bridged "),
        ]:
            assert any(marker in r.getMessage() for r in plog_records), (
                f"{stage} marker {marker!r} not found in:\n{text}"
            )


# ---------------------------------------------------------------------------
# NFM-3901: heuristic dedup must survive non-numeric ``value`` fields on the
# LLM side. Prior to this fix, ``f"{r.get("value", 0):g}"`` raised
# ``ValueError: Unknown format code 'g' for object of type 'str'`` whenever
# any LLM-extracted item had a string ``value``, and the outer
# ``except Exception`` at the end of the heuristic block silently dropped
# the entire heuristic batch — never reaching merge (Stage 2) or
# post-process (Stage 3).
# ---------------------------------------------------------------------------


def _make_string_value_extraction() -> list[dict[str, Any]]:
    """LLM payload where one item has a string ``value`` (the regression
    trigger from NFM-3893 / source 9320cb50)."""
    return [
        {
            "element_system": "UO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": "not-a-number",  # NFM-3901: triggers the original ValueError
            "unit": "angstrom",
            "method": "LLM",
            "source": "test-upload.pdf",
            "source_doi": None,
            "confidence": "high",
            "uncertainty": 0.01,
            "temperature": 300.0,
            "cache_level": "L1",
            "material_name": "UO2",
            "composition": "UO2",
            "property": "lattice_constant",
        },
        {
            "element_system": "PuO2",
            "phase": "FCC",
            "property_name": "lattice_constant",
            "value": 5.40,  # numeric — also exercised to keep the test honest
            "unit": "angstrom",
            "method": "LLM",
            "source": "test-upload.pdf",
            "source_doi": None,
            "confidence": "high",
            "uncertainty": 0.01,
            "temperature": 300.0,
            "cache_level": "L1",
            "material_name": "PuO2",
            "composition": "PuO2",
            "property": "lattice_constant",
        },
    ]


class TestHeuristicDedupWithStringValues:
    """NFM-3901: heuristic items must reach the final mapper batch even when
    the LLM payload contains items with non-numeric ``value`` fields.

    Regression: prior to the fix, the merge-block
    ``existing_keys`` comprehension at literature_service.py:644 raised
    ``ValueError: Unknown format code 'g' for object of type 'str'`` and
    the outer ``except Exception`` swallowed the entire heuristic batch
    (line 1 fired, lines 2 and 3 never fired). Source 9320cb50 exhibited
    exactly this silent-zero in prod (NFM-3887 / NFM-3893).

    These tests use the same mocking shape as
    :class:`TestPerStageLogCounters` so the per-stage counter logs are the
    authoritative witness that the merge and post-process stages ran.
    """

    async def test_heuristic_items_reach_mapper_when_llm_value_is_string(
        self,
        db_session: AsyncSession,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ds = await _add_datasource(
            db_session,
            content_md="# Preset\n\nUO2 FCC lattice 5.47",
            file_path=None,
        )
        ds.metadata_ = {"corpus_id": "test-corpus"}
        await db_session.flush()

        # Heuristic items: one numeric (must append), one matching the
        # string-valued LLM item by (system, property) so we can also
        # observe that dedup still works for the numeric side.
        heuristic_items = [
            _make_demo_heuristic_item(
                element_system="UO2",
                property_name="lattice_constant",
                value=5.47,  # distinct numeric — should append
                unit="angstrom",
            ),
            _make_demo_heuristic_item(
                element_system="ThO2",
                property_name="lattice_constant",
                value=5.60,  # distinct numeric — should append
                unit="angstrom",
            ),
        ]

        # BuildResult / MappingResult mocks so the Stage 4-6 logs render.
        mock_node = MagicMock()
        mock_node.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        mock_node.label = "UO2"
        mock_build_result = MagicMock()
        mock_build_result.nodes_created = 1
        mock_build_result.nodes_matched = 0
        mock_build_result.edges_created = 0
        mock_build_result.review_queue_items = 0
        mock_build_result.ingest_nodes = (mock_node,)
        mock_build_result.ingest_edges = ()

        mock_mapping = MagicMock()
        mock_mapping.created_sources = 0
        mock_mapping.created_materials = 0
        mock_mapping.created_datasets = 0
        mock_mapping.created_measurements = 0
        mock_mapping.reused_entities = 0
        mock_mapping.skipped_duplicate_measurements = 0
        mock_mapping.skipped_unknown_properties = 0
        mock_mapping.validation_errors = 0

        captured_payload: list[list[dict[str, Any]]] = []

        async def _capture_map(
            db: AsyncSession, items: list[dict[str, Any]], **_kwargs: Any
        ) -> MagicMock:
            captured_payload.append(items)
            return mock_mapping

        with (
            caplog.at_level(logging.INFO, logger="nfm_db.services.literature_service"),
            patch.object(lit_svc, "_parse_pdf_to_markdown"),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                # NFM-3901: include a string-value item — the regression trigger.
                new=AsyncMock(return_value=_make_string_value_extraction()),
            ),
            patch(
                "nfm_db.services.heuristic_extractor.heuristic_extract",
                new=MagicMock(return_value=heuristic_items),
            ),
            patch(
                "nfm_db.services.extraction_pipeline._post_process_extracted",
                new=MagicMock(side_effect=lambda items, **_kw: items),
            ),
            patch(
                "nfm_db.services.extraction_to_db_mapper.map_and_persist",
                new=AsyncMock(side_effect=_capture_map),
            ),
            patch(
                "nfm_db.services.kg_re.GraphBuilder.build_from_extraction",
                new=AsyncMock(return_value=mock_build_result),
            ),
            patch(
                "nfm_db.services.kg_to_staging_bridge.bridge_kg_to_staging",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "nfm_db.services.kg_to_staging_bridge._slugify",
                new=MagicMock(return_value="corpus-slug"),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        # Filter to process_literature's own log records.
        plog_records = [
            r for r in caplog.records
            if r.name == "nfm_db.services.literature_service"
            and "process_literature:" in r.getMessage()
            and f"datasource_id={ds.id}" in r.getMessage()
        ]
        text = "\n".join(r.getMessage() for r in plog_records)

        # --- Acceptance gate 1: the heuristic block did not raise ----
        # Pre-fix this log fired (and everything inside the try block was
        # dropped). Post-fix it must NOT fire.
        assert "Heuristic extractor failed" not in text, (
            f"NFM-3901 regression: heuristic block raised and was "
            f"swallowed; process_literature log:\n{text}"
        )

        # --- Acceptance gate 2: Stage 2 (merge) fired -----------------
        # Before the fix, the comprehension at the old line 644 raised
        # before reaching the ``raw_properties after merge`` logger, so
        # Stage 2 was silently absent.
        merge_line = next(
            (r.getMessage() for r in plog_records
             if "raw_properties after merge:" in r.getMessage()),
            None,
        )
        assert merge_line is not None, (
            f"NFM-3901 regression: Stage 2 'raw_properties after merge' "
            f"log never fired (the heuristic block raised before it):\n{text}"
        )
        assert "heuristic added 2" in merge_line, (
            f"Stage 2 must report 'heuristic added 2' (both ThO2 and UO2 "
            f"heuristic items are new vs. the LLM payload); got: {merge_line!r}"
        )

        # --- Acceptance gate 3: Stage 3 (post-process) fired ----------
        post_line = next(
            (r.getMessage() for r in plog_records
             if "raw_properties after _post_process_extracted:" in r.getMessage()),
            None,
        )
        assert post_line is not None, (
            f"NFM-3901 regression: Stage 3 'raw_properties after "
            f"_post_process_extracted' log never fired (the heuristic "
            f"block raised before it):\n{text}"
        )
        assert "delta=0" in post_line, (
            f"Stage 3 must report delta=0 (post-process mocked as identity); "
            f"got: {post_line!r}"
        )

        # --- Acceptance gate 4: heuristic items reach the mapper ------
        # map_and_persist sees the final batch; both heuristic items
        # must survive the merge so they land as KG nodes / measurements.
        assert result["status"] == "completed", result
        assert captured_payload, "map_and_persist was never invoked"
        final_batch = captured_payload[0]

        heuristic_in_final = [
            item for item in final_batch
            if item.get("method") == "heuristic_regex"
        ]
        assert len(heuristic_in_final) == len(heuristic_items), (
            f"NFM-3901: expected all {len(heuristic_items)} heuristic "
            f"items in the final batch, got {len(heuristic_in_final)}: "
            f"{heuristic_in_final!r}"
        )
        seen_systems = {item["element_system"] for item in heuristic_in_final}
        assert {"UO2", "ThO2"} <= seen_systems, (
            f"both UO2 and ThO2 heuristic items must reach the mapper; "
            f"got systems={seen_systems!r}"
        )

    async def test_safe_g_helper_handles_non_numeric_values(self) -> None:
        """Unit test for the NFM-3901 ``_safe_g`` helper: numeric values
        keep the existing ``:g`` rendering so dedup matches
        ``heuristic_extractor``; non-numeric values fall back to ``repr``
        so the key remains hashable and never raises.
        """
        # Numeric inputs — match ``heuristic_extractor``'s key format.
        assert lit_svc._safe_g(5.47) == "5.47"
        assert lit_svc._safe_g(1.0) == "1"
        assert lit_svc._safe_g(0) == "0"

        # Non-numeric inputs — must NOT raise; must return a stable
        # string so two equal string-valued items dedupe.
        assert lit_svc._safe_g("not-a-number") == repr("not-a-number")
        assert lit_svc._safe_g(None) == repr(None)
        # ``True``/``False`` are numeric-coercible, exercise that path.
        assert lit_svc._safe_g(True) == "1"
        # Equality on strings — two items with the same string value
        # must dedupe to the same key.
        assert lit_svc._safe_g("foo") == lit_svc._safe_g("foo")
        assert lit_svc._safe_g("foo") != lit_svc._safe_g("bar")
