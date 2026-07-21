"""Unit tests for nfm_db.services.providers.composite.CompositeProvider."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.services.providers.base import PotentialFilters
from nfm_db.services.providers.composite import CompositeProvider


class TestListSummaries:
    async def test_merges_local_and_openkim(self) -> None:
        local = AsyncMock()
        openkim = AsyncMock()
        local_rows = [MagicMock(name="local1")]
        openkim_rows = [MagicMock(name="okim1")]
        local.list_summaries.return_value = local_rows
        openkim.list_summaries.return_value = openkim_rows

        provider = CompositeProvider(local, openkim)
        result = await provider.list_summaries(PotentialFilters())

        assert len(result) == 2
        assert result[0] is local_rows[0]
        assert result[1] is openkim_rows[0]

    async def test_openkim_failure_returns_local_only(self) -> None:
        local = AsyncMock()
        openkim = AsyncMock()
        local.list_summaries.return_value = [MagicMock()]
        openkim.list_summaries.side_effect = RuntimeError("KIM down")

        provider = CompositeProvider(local, openkim)
        result = await provider.list_summaries(PotentialFilters())

        assert len(result) == 1


class TestGetDetail:
    async def test_local_hit(self) -> None:
        detail = MagicMock(name="local_detail")
        local = AsyncMock()
        openkim = AsyncMock()
        local.get_detail.return_value = detail

        provider = CompositeProvider(local, openkim)
        result = await provider.get_detail(uuid.uuid4())

        assert result is detail
        openkim.get_detail.assert_not_awaited()

    async def test_falls_through_to_openkim(self) -> None:
        okim_detail = MagicMock(name="okim_detail")
        local = AsyncMock()
        openkim = AsyncMock()
        local.get_detail.return_value = None
        openkim.get_detail.return_value = okim_detail

        provider = CompositeProvider(local, openkim)
        result = await provider.get_detail(uuid.uuid4())

        assert result is okim_detail

    async def test_both_missing_returns_none(self) -> None:
        local = AsyncMock()
        openkim = AsyncMock()
        local.get_detail.return_value = None
        openkim.get_detail.return_value = None

        provider = CompositeProvider(local, openkim)
        result = await provider.get_detail(uuid.uuid4())

        assert result is None

    async def test_openkim_exception_returns_none(self) -> None:
        local = AsyncMock()
        openkim = AsyncMock()
        local.get_detail.return_value = None
        openkim.get_detail.side_effect = ConnectionError("timeout")

        provider = CompositeProvider(local, openkim)
        result = await provider.get_detail(uuid.uuid4())

        assert result is None