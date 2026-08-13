"""Unit tests for seed_service (NFM-701).

Tests for:
- BatchSeedService data models (SeedItemStatus, BatchProgress)
- start_batch with mocked extraction/mapper
- get_batch_status real-time progress tracking
- asyncio concurrency via semaphore
- Per-DOI retry with exponential backoff (max 3 attempts)
- In-memory batch store isolation
- Immutable status update patterns

Conventions:
- Clear _batch_store before/after each test via fixture teardown
- Mock extraction_pipeline.ontofuel_extract and mapper Protocol
- See ADR-T5 Test Architecture
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from nfm_db.services.seed_service import (
    BatchProgress,
    SeedItemStatus,
    SeedStatus,
    _batch_store,
    get_batch_status,
    start_batch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_batch_store():
    """Clear _batch_store before and after each test."""
    _batch_store.clear()
    yield
    _batch_store.clear()


@pytest.fixture
def mock_extract():
    """Mock ontofuel_extract that returns sample properties."""
    return AsyncMock(
        return_value=[
            {
                "element_system": "UO2",
                "phase": "FCC",
                "property_name": "lattice_constant",
                "value": 5.47,
                "unit": "angstrom",
                "method": "DFT",
                "source": "10.1016/test",
            },
        ],
    )


@pytest.fixture
def mock_mapper():
    """Mock map_and_persist that returns success result."""
    return AsyncMock(return_value={"status": "persisted", "count": 1})


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestSeedItemStatus:
    """Tests for SeedItemStatus dataclass."""

    def test_create_with_defaults(self):
        item = SeedItemStatus(doi="10.1016/test")
        assert item.doi == "10.1016/test"
        assert item.status == SeedStatus.PENDING
        assert item.retry_count == 0
        assert item.error_message is None
        assert item.created_at is not None

    def test_create_with_all_fields(self):
        now = datetime.now(UTC)
        item = SeedItemStatus(
            doi="10.1016/test",
            status=SeedStatus.FAILED,
            error_message="LLM timeout",
            retry_count=2,
            created_at=now,
        )
        assert item.status == SeedStatus.FAILED
        assert item.error_message == "LLM timeout"
        assert item.retry_count == 2

    def test_immutability_frozen(self):
        item = SeedItemStatus(doi="10.1016/test")
        with pytest.raises(AttributeError):
            item.doi = "other"  # type: ignore[misc]

    def test_with_status_returns_new_instance(self):
        item = SeedItemStatus(doi="10.1016/test")
        updated = item.with_status(
            status=SeedStatus.EXTRACTING,
        )
        assert updated.status == SeedStatus.EXTRACTING
        assert item.status == SeedStatus.PENDING  # original unchanged

    def test_with_error_returns_new_instance(self):
        item = SeedItemStatus(doi="10.1016/test")
        updated = item.with_error(
            status=SeedStatus.FAILED,
            error_message="timeout",
            retry_count=1,
        )
        assert updated.status == SeedStatus.FAILED
        assert updated.error_message == "timeout"
        assert updated.retry_count == 1
        assert item.retry_count == 0  # original unchanged


class TestBatchProgress:
    """Tests for BatchProgress dataclass."""

    def test_create_initial_state(self):
        progress = BatchProgress(
            batch_id="test-batch",
            total=5,
            in_progress=5,
        )
        assert progress.batch_id == "test-batch"
        assert progress.total == 5
        assert progress.completed == 0
        assert progress.failed == 0
        assert progress.in_progress == 5
        assert progress.errors == []

    def test_update_completed_increments(self):
        progress = BatchProgress(batch_id="b", total=3, in_progress=3)
        updated = progress.increment_completed()
        assert updated.completed == 1
        assert updated.in_progress == 2
        assert progress.completed == 0  # original unchanged

    def test_update_failed_increments(self):
        progress = BatchProgress(batch_id="b", total=3, in_progress=3)
        updated = progress.increment_failed("DOI failed: timeout")
        assert updated.failed == 1
        assert updated.in_progress == 2
        assert "DOI failed: timeout" in updated.errors
        assert progress.failed == 0

    def test_is_finished(self):
        progress = BatchProgress(batch_id="b", total=2, in_progress=2)
        assert not progress.is_finished()

        done = progress.increment_completed().increment_completed()
        assert done.is_finished()
        assert done.in_progress == 0

    def test_is_finished_with_all_failed(self):
        progress = BatchProgress(batch_id="b", total=2, in_progress=2)
        done = progress.increment_failed("e1").increment_failed("e2")
        assert done.is_finished()


# ---------------------------------------------------------------------------
# start_batch tests
# ---------------------------------------------------------------------------


class TestStartBatch:
    """Tests for the start_batch orchestration function."""

    @pytest.mark.asyncio
    async def test_returns_batch_id(self, mock_extract, mock_mapper):
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/test.doi"],
                concurrency=1,
            )
        assert batch_id is not None
        assert isinstance(batch_id, str)

    @pytest.mark.asyncio
    async def test_tracks_single_doi_completion(self, mock_extract, mock_mapper):
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/test.doi"],
                concurrency=1,
            )

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.total == 1
        assert progress.completed == 1
        assert progress.failed == 0

    @pytest.mark.asyncio
    async def test_tracks_multiple_dois(self, mock_extract, mock_mapper):
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/a", "10.1016/b", "10.1016/c"],
                concurrency=2,
            )

        progress = get_batch_status(batch_id)
        assert progress.total == 3
        assert progress.completed == 3

    @pytest.mark.asyncio
    async def test_extraction_failure_marks_item_failed(self, mock_mapper):
        failing_extract = AsyncMock(return_value=[])
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                failing_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/fail"],
                concurrency=1,
            )

        progress = get_batch_status(batch_id)
        assert progress.failed == 1
        assert progress.completed == 0

    @pytest.mark.asyncio
    async def test_mapper_failure_marks_item_failed(self, mock_extract):
        failing_mapper = AsyncMock(
            map_and_persist=AsyncMock(
                side_effect=RuntimeError("DB connection lost"),
            ),
        )
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=failing_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/mapper-fail"],
                concurrency=1,
            )

        progress = get_batch_status(batch_id)
        assert progress.failed == 1
        assert progress.completed == 0

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self, mock_mapper):
        """Extraction fails twice, succeeds on third attempt."""
        call_count = 0

        async def _flaky_extract(source_reference: str, source_type: str, **_kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Transient LLM error")
            return [
                {
                    "element_system": "UO2",
                    "property_name": "lattice_constant",
                    "value": 5.47,
                    "unit": "angstrom",
                    "source": source_reference,
                },
            ]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                side_effect=_flaky_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/flaky"],
                concurrency=1,
            )

        progress = get_batch_status(batch_id)
        assert progress.completed == 1
        assert progress.failed == 0

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_marks_failed(self, mock_mapper):
        """Extraction fails all 3 attempts — item marked failed."""
        always_fails = AsyncMock(side_effect=RuntimeError("Persistent error"))
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                always_fails,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/permanent-fail"],
                concurrency=1,
            )

        progress = get_batch_status(batch_id)
        assert progress.failed == 1
        assert progress.completed == 0

    @pytest.mark.asyncio
    async def test_empty_doi_list_returns_immediately(self):
        batch_id = await start_batch(dois=[], concurrency=3)
        assert batch_id is not None
        progress = get_batch_status(batch_id)
        assert progress.total == 0
        assert progress.is_finished()


# ---------------------------------------------------------------------------
# get_batch_status tests
# ---------------------------------------------------------------------------


class TestGetBatchStatus:
    """Tests for the get_batch_status query function."""

    def test_returns_none_for_unknown_batch(self):
        result = get_batch_status("nonexistent-batch")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_progress_after_batch_starts(self, mock_extract, mock_mapper):
        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=["10.1016/test"],
                concurrency=1,
            )

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.batch_id == batch_id
        assert isinstance(progress.started_at, datetime)
        assert isinstance(progress.updated_at, datetime)


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Tests that verify concurrency limiting works correctly."""

    @pytest.mark.asyncio
    async def test_respects_concurrency_limit(self, mock_mapper):
        """Verify that concurrent tasks don't exceed the semaphore limit."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def _slow_extract(source_reference, source_type, **_kw):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.05)
            async with lock:
                current_concurrent -= 1
            return [
                {
                    "property_name": "test",
                    "value": 1.0,
                    "source": source_reference,
                },
            ]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                new_callable=AsyncMock,
                side_effect=_slow_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                return_value=mock_mapper,
            ),
        ):
            batch_id = await start_batch(
                dois=[f"10.1016/d{i}" for i in range(6)],
                concurrency=2,
            )

        progress = get_batch_status(batch_id)
        assert progress.completed == 6
        assert max_concurrent <= 2
