"""Tests for LightRAG query timeout (NFM-3404 / NFM-3425 AC-2).

The backend httpx read budget must fire ``httpx.ReadTimeout`` and surface it
as :class:`LightRAGClientError` within ``1.2 x query_timeout`` wall-clock, with
no retry. The query budget itself is sourced from
``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` (default 12s) per ADR-NFM-3404 §2.1.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Env-var contract: NFM_LIGHTRAG_QUERY_TIMEOUT_S
# ---------------------------------------------------------------------------


class TestQueryTimeoutEnvVar:
    """``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` must drive the read budget."""

    def test_env_var_overrides_module_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting the env var changes ``query_timeout`` to that value."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", "7.5")

        client = LightRAGClient(host="localhost", port=9621)

        assert client.query_timeout == pytest.approx(7.5)

    def test_env_var_propagates_to_transport_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The httpx transport ``read`` phase must equal the env value."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", "11.0")

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        assert transport_timeout.read == pytest.approx(11.0)

    def test_no_env_var_falls_back_to_module_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the env var, ``query_timeout`` falls back to the constant."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _DEFAULT_QUERY_TIMEOUT,
            LightRAGClient,
        )

        monkeypatch.delenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", raising=False)

        client = LightRAGClient(host="localhost", port=9621)

        assert client.query_timeout == _DEFAULT_QUERY_TIMEOUT


# ---------------------------------------------------------------------------
# Read-timeout failure path (ADR §4 test 1)
# ---------------------------------------------------------------------------


class TestReadTimeoutFastFail:
    """``httpx.ReadTimeout`` on /query must surface within budget, no retry."""

    @pytest.mark.asyncio
    async def test_read_timeout_surfaces_as_client_error_within_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mocked ReadTimeout → ``LightRAGClientError`` in <= 1.2 x query_timeout."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        # Tight query budget so the 1.2x envelope is observable on the test wall clock.
        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", "1.0")

        client = LightRAGClient(host="localhost", port=9621)

        start = time.perf_counter()
        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("Timed out"),
        ):
            with pytest.raises(LightRAGClientError):
                await client.query(query="what is UO2?")
        elapsed = time.perf_counter() - start

        # Mock raises immediately, so elapsed should be far below the envelope.
        # Use the env-derived budget, not the historical 8s constant.
        assert elapsed <= client.query_timeout * 1.2

    @pytest.mark.asyncio
    async def test_no_retry_on_read_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single ReadTimeout must NOT trigger a retry — exactly one POST."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", "1.0")

        client = LightRAGClient(host="localhost", port=9621)

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ReadTimeout("Timed out"),
        ) as mock_post:
            with pytest.raises(Exception):
                await client.query(query="what is UO2?")

        # AC-2: no silent retry on the query path.
        mock_post.assert_called_once()
