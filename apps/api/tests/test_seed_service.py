"""Unit tests for seed_service.py (NFM-701).

Tests for the batch seed pipeline service:
- start_batch() with mocked extraction + mapper
- get_batch_status() real-time progress
- asyncio.TaskGroup + semaphore concurrency limiting
- Retry logic (up to 3 attempts with exponential backoff)
- Batch progress tracking (total/completed/failed/in_progress)
- Immutable data patterns (SeedItemStatus, BatchProgress)
- Edge cases: empty DOI list, all failures, partial failures

Conventions:
- Clear _batch_store before/after each test via fixture teardown
- Mock extraction_pipeline and extraction_to_db_mapper
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.services.seed_service import (
    MAX_RETRY_ATTEMPTS,
    BatchProgress,
    SeedItemStatus,
    SeedStatus,
    _batch_store,
    _generate_batch_id,
    _get_mapper,
    _process_single_doi,
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
def sample_properties() -> list[dict]:
    """Reusable sample extracted property dicts."""
    return [
        {
            "material_name": "UO2",
            "composition": "UO2",
            "property_category": "thermophysical",
            "property": "thermal_conductivity",
            "value": "7.5",
            "unit": "W/(m·K)",
            "source_doi": "10.1016/j.jnucmat.2020.01.001",
            "conditions": {"temperature": "800"},
        },
    ]


# ---------------------------------------------------------------------------
# Tests: Immutable data models
# ---------------------------------------------------------------------------


class TestSeedItemStatus:
    """Tests for the immutable SeedItemStatus dataclass."""

    def test_default_status_is_pending(self) -> None:
        item = SeedItemStatus(doi="10.1016/test")
        assert item.status == SeedStatus.PENDING
        assert item.error_message is None
        assert item.retry_count == 0

    def test_with_status_creates_new_object(self) -> None:
        original = SeedItemStatus(doi="10.1016/test")
        updated = original.with_status(SeedStatus.EXTRACTING)
        assert original.status == SeedStatus.PENDING  # unchanged
        assert updated.status == SeedStatus.EXTRACTING
        assert updated.updated_at >= original.created_at

    def test_with_error_creates_new_object(self) -> None:
        original = SeedItemStatus(doi="10.1016/test")
        updated = original.with_error(
            status=SeedStatus.FAILED,
            error_message="Connection timeout",
            retry_count=2,
        )
        assert original.retry_count == 0  # unchanged
        assert updated.status == SeedStatus.FAILED
        assert updated.error_message == "Connection timeout"
        assert updated.retry_count == 2

    def test_frozen_prevents_mutation(self) -> None:
        item = SeedItemStatus(doi="10.1016/test")
        with pytest.raises(AttributeError):
            item.status = SeedStatus.DONE  # type: ignore[misc]

    def test_created_at_has_timezone(self) -> None:
        item = SeedItemStatus(doi="10.1016/test")
        assert item.created_at.tzinfo is not None


class TestBatchProgress:
    """Tests for the immutable BatchProgress dataclass."""

    def test_initial_progress(self) -> None:
        progress = BatchProgress(batch_id="abc", total=5)
        assert progress.total == 5
        assert progress.completed == 0
        assert progress.failed == 0
        assert progress.in_progress == 0
        assert progress.errors == []

    def test_increment_completed(self) -> None:
        original = BatchProgress(batch_id="abc", total=5, in_progress=3, completed=1)
        updated = original.increment_completed()
        assert original.completed == 1  # unchanged
        assert updated.completed == 2
        assert updated.in_progress == 2

    def test_increment_failed(self) -> None:
        original = BatchProgress(batch_id="abc", total=5, in_progress=3, failed=0)
        updated = original.increment_failed("10.1016/test: timeout")
        assert original.failed == 0  # unchanged
        assert updated.failed == 1
        assert updated.in_progress == 2
        assert "10.1016/test: timeout" in updated.errors

    def test_in_progress_floor_is_zero(self) -> None:
        progress = BatchProgress(batch_id="abc", total=5, in_progress=0)
        updated = progress.increment_completed()
        assert updated.in_progress == 0  # should not go negative

    def test_is_finished_false_when_items_remain(self) -> None:
        progress = BatchProgress(batch_id="abc", total=5, completed=2, failed=1)
        assert progress.is_finished() is False

    def test_is_finished_true_when_all_processed(self) -> None:
        progress = BatchProgress(batch_id="abc", total=5, completed=3, failed=2)
        assert progress.is_finished() is True

    def test_frozen_prevents_mutation(self) -> None:
        progress = BatchProgress(batch_id="abc", total=5)
        with pytest.raises(AttributeError):
            progress.completed = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: Utility functions
# ---------------------------------------------------------------------------


class TestGenerateBatchId:
    """Tests for _generate_batch_id."""

    def test_returns_valid_uuid_string(self) -> None:
        batch_id = _generate_batch_id()
        uuid.UUID(batch_id)  # raises ValueError if invalid
        assert isinstance(batch_id, str)

    def test_returns_unique_ids(self) -> None:
        ids = {_generate_batch_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Tests: _get_mapper
# ---------------------------------------------------------------------------


class TestGetMapper:
    """Tests for _get_mapper mapper loading."""

    async def test_returns_none_when_mapper_not_available(self) -> None:
        """When extraction_to_db_mapper import fails, _get_mapper returns None."""
        real_import = __import__

        def _mock_import(name: str, *args: object, **kwargs: object):
            if name == "nfm_db.services.extraction_to_db_mapper":
                raise ImportError("NFM-700 pending")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_import):
            result = await _get_mapper()
        assert result is None

    async def test_returns_mapper_when_available(self) -> None:
        """When extraction_to_db_mapper exists, _get_mapper returns it."""
        # The module exists in this codebase, so it returns the function
        result = await _get_mapper()
        assert result is not None


# ---------------------------------------------------------------------------
# Tests: _process_single_doi
# ---------------------------------------------------------------------------


class TestProcessSingleDoi:
    """Tests for the single DOI processing with retry."""

    async def test_successful_extraction_and_persist(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Happy path: extract succeeds, mapper persists."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        mock_extract = AsyncMock(return_value=sample_properties)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        with (
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
        ):
            await _process_single_doi("10.1016/test", semaphore, progress_ref)

        assert progress_ref[0].completed == 1
        assert progress_ref[0].failed == 0
        mock_extract.assert_called_once_with(
            source_reference="10.1016/test",
            source_type="doi",
        )
        mock_mapper_instance.map_and_persist.assert_called_once_with(
            sample_properties, "10.1016/test"
        )

    async def test_extraction_fails_all_retries(self) -> None:
        """All retries exhausted -> item marked as failed."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        mock_extract = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        with (
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
        ):
            await _process_single_doi("10.1016/fail", semaphore, progress_ref)

        assert progress_ref[0].completed == 0
        assert progress_ref[0].failed == 1
        assert len(progress_ref[0].errors) == 1
        assert "10.1016/fail" in progress_ref[0].errors[0]
        assert mock_extract.call_count == MAX_RETRY_ATTEMPTS

    async def test_retry_succeeds_on_second_attempt(
        self,
        sample_properties: list[dict],
    ) -> None:
        """First attempt fails, second succeeds."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        mock_extract = AsyncMock(
            side_effect=[
                RuntimeError("Transient error"),
                sample_properties,
            ]
        )
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        with (
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
        ):
            await _process_single_doi("10.1016/retry", semaphore, progress_ref)

        assert progress_ref[0].completed == 1
        assert progress_ref[0].failed == 0
        assert mock_extract.call_count == 2

    async def test_empty_extraction_result_marks_failed(self) -> None:
        """Empty properties list is treated as a failure."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        mock_extract = AsyncMock(return_value=[])

        with (
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
        ):
            await _process_single_doi("10.1016/empty", semaphore, progress_ref)

        assert progress_ref[0].completed == 0
        assert progress_ref[0].failed == 1

    async def test_mapper_unavailable_still_completes(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Extraction succeeds but mapper is None (NFM-700 pending)."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        mock_extract = AsyncMock(return_value=sample_properties)

        with (
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
        ):
            await _process_single_doi("10.1016/nomapper", semaphore, progress_ref)

        assert progress_ref[0].completed == 1


# ---------------------------------------------------------------------------
# Tests: start_batch
# ---------------------------------------------------------------------------


class TestStartBatch:
    """Tests for the public start_batch API."""

    async def test_empty_doi_list_returns_batch_id(self) -> None:
        """Empty DOI list should return a batch_id with 0 total."""
        batch_id = await start_batch([])
        assert batch_id is not None
        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.total == 0
        assert progress.is_finished()

    async def test_single_doi_success(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Single DOI processes successfully."""
        mock_extract = AsyncMock(return_value=sample_properties)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            batch_id = await start_batch(["10.1016/single"])

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.total == 1
        assert progress.completed == 1
        assert progress.failed == 0
        assert progress.is_finished()

    async def test_multiple_dois_all_success(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Multiple DOIs all process successfully."""
        mock_extract = AsyncMock(return_value=sample_properties)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        dois = [f"10.1016/test_{i}" for i in range(5)]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            batch_id = await start_batch(dois, concurrency=3)

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.total == 5
        assert progress.completed == 5
        assert progress.failed == 0
        assert progress.is_finished()
        assert mock_extract.call_count == 5

    async def test_partial_failures_tracked(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Mix of successes and failures tracked correctly."""
        call_count = 0

        async def _extract_side_effect(*args: object, **kwargs: object) -> list[dict]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return sample_properties
            raise RuntimeError("Persistent failure")

        mock_extract = AsyncMock(side_effect=_extract_side_effect)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        dois = [f"10.1016/mix_{i}" for i in range(3)]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            batch_id = await start_batch(dois, concurrency=1)

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.total == 3
        assert progress.completed == 2
        assert progress.failed == 1
        assert progress.is_finished()

    async def test_concurrency_limited_by_semaphore(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Verify semaphore limits concurrent extraction calls."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def _tracked_extract(*args: object, **kwargs: object) -> list[dict]:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.01)  # simulate work
            async with lock:
                current_concurrent -= 1
            return sample_properties

        mock_extract = AsyncMock(side_effect=_tracked_extract)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        dois = [f"10.1016/sem_{i}" for i in range(6)]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            await start_batch(dois, concurrency=2)

        assert max_concurrent <= 2

    async def test_return_value_is_string_batch_id(
        self,
        sample_properties: list[dict],
    ) -> None:
        """start_batch returns a string batch_id."""
        mock_extract = AsyncMock(return_value=sample_properties)

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            batch_id = await start_batch(["10.1016/type"])

        assert isinstance(batch_id, str)
        # Should be parseable as UUID
        uuid.UUID(batch_id)


# ---------------------------------------------------------------------------
# Tests: get_batch_status
# ---------------------------------------------------------------------------


class TestGetBatchStatus:
    """Tests for get_batch_status progress query."""

    async def test_returns_none_for_unknown_batch(self) -> None:
        result = get_batch_status("nonexistent-batch-id")
        assert result is None

    async def test_returns_progress_for_known_batch(
        self,
        sample_properties: list[dict],
    ) -> None:
        mock_extract = AsyncMock(return_value=sample_properties)

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            batch_id = await start_batch(["10.1016/status"])

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.batch_id == batch_id
        assert progress.total == 1
        assert progress.completed == 1

    async def test_progress_updated_after_batch_completes(
        self,
        sample_properties: list[dict],
    ) -> None:
        """Progress in _batch_store reflects final state."""
        mock_extract = AsyncMock(return_value=sample_properties)
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        dois = [f"10.1016/final_{i}" for i in range(3)]

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            batch_id = await start_batch(dois)

        progress = get_batch_status(batch_id)
        assert progress is not None
        assert progress.total == 3
        assert progress.completed + progress.failed == 3
        assert progress.in_progress == 0


# ---------------------------------------------------------------------------
# Tests: Retry with exponential backoff
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    """Tests for retry behavior with exponential backoff."""

    async def test_backoff_delays_increase(self) -> None:
        """Verify backoff delays increase exponentially."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        sleep_calls: list[float] = []

        original_sleep = asyncio.sleep

        async def _mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            await original_sleep(0)  # don't actually wait in tests

        mock_extract = AsyncMock(side_effect=RuntimeError("fail"))
        mock_mapper_instance = MagicMock()
        mock_mapper_instance.map_and_persist = AsyncMock()

        with (
            patch("asyncio.sleep", _mock_sleep),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            await _process_single_doi("10.1016/backoff", semaphore, progress_ref)

        # Should have slept twice (after attempt 1 and attempt 2)
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 1.0  # 2^0 * BASE
        assert sleep_calls[1] == 2.0  # 2^1 * BASE

    async def test_no_backoff_on_first_attempt(self) -> None:
        """3 attempts -> 2 backoffs (between attempts, not after last)."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        sleep_calls: list[float] = []
        _real_sleep = asyncio.sleep

        async def _mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            # No-op: don't actually sleep in tests
            await _real_sleep(0)

        mock_extract = AsyncMock(
            side_effect=[RuntimeError("fail"), RuntimeError("fail"), RuntimeError("fail")]
        )

        with (
            patch("asyncio.sleep", _mock_sleep),
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await _process_single_doi("10.1016/nobackoff", semaphore, progress_ref)

        # 3 attempts, 2 backoffs (between attempts)
        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# Tests: Mapper integration
# ---------------------------------------------------------------------------


class TestMapperIntegration:
    """Tests for mapper integration edge cases."""

    async def test_mapper_persist_error_triggers_retry(
        self,
        sample_properties: list[dict],
    ) -> None:
        """If mapper fails, the DOI should be retried."""
        progress = BatchProgress(batch_id="test", total=1, in_progress=1)
        progress_ref = [progress]
        semaphore = asyncio.Semaphore(3)

        mock_extract = AsyncMock(return_value=sample_properties)
        mock_mapper_instance = MagicMock()
        # Mapper fails on first call, succeeds on second
        mock_mapper_instance.map_and_persist = AsyncMock(
            side_effect=[RuntimeError("DB error"), None]
        )

        with (
            patch(
                "nfm_db.services.extraction_pipeline.ontofuel_extract",
                mock_extract,
            ),
            patch(
                "nfm_db.services.seed_service._get_mapper",
                new_callable=AsyncMock,
                return_value=mock_mapper_instance,
            ),
        ):
            await _process_single_doi("10.1016/mappererr", semaphore, progress_ref)

        assert progress_ref[0].completed == 1
        assert progress_ref[0].failed == 0
