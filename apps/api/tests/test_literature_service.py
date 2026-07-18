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

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
        build_result_sentinel = object()

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
                new=AsyncMock(return_value=build_result_sentinel),
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
                new=AsyncMock(return_value=object()),
            ),
        ):
            result = await lit_svc.process_literature(db_session, ds.id)

        mock_storage.assert_called_once()
        mock_parse.assert_called_once_with(mock_bytes)
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
                new=AsyncMock(return_value=object()),
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
# 5. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_missing_datasource_returns_skipped(self, db_session: AsyncSession) -> None:
        """No row with the given id → returns {'status': 'skipped'}, no exception."""
        random_id = uuid.uuid4()
        result = await lit_svc.process_literature(db_session, random_id)
        assert result["status"] == "skipped"
        assert result["reason"] == "not_found"

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
