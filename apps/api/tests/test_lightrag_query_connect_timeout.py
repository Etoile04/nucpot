"""Tests for LightRAG connect timeout (NFM-3404 / NFM-3425 AC-2).

The backend httpx connect ceiling must fire ``httpx.ConnectError`` and surface
it as :class:`LightRAGClientError` within ``1.2 x connect_timeout`` budget,
with no retry. The connect ceiling itself is sourced from
``NFM_LIGHTRAG_QUERY_CONNECT_S`` (default 3s) per ADR-NFM-3404 §2.1.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Env-var contract: NFM_LIGHTRAG_QUERY_CONNECT_S
# ---------------------------------------------------------------------------


class TestConnectTimeoutEnvVar:
    """``NFM_LIGHTRAG_QUERY_CONNECT_S`` must drive the connect ceiling."""

    def test_env_var_overrides_hardcoded_5s(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting the env var changes the httpx.Timeout.connect value."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_CONNECT_S", "1.25")

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        assert transport_timeout.connect == pytest.approx(1.25)

    def test_env_var_propagates_to_transport_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity check: another value flows through cleanly."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_CONNECT_S", "4.5")

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        assert transport_timeout.connect == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# Connect-refused failure path (ADR §4 test 2)
# ---------------------------------------------------------------------------


class TestConnectRefusedFastFail:
    """``httpx.ConnectError`` on /query must surface within connect budget."""

    @pytest.mark.asyncio
    async def test_connect_error_surfaces_as_client_error_within_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mocked ConnectError → ``LightRAGClientError`` within budget envelope."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGClientError,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_CONNECT_S", "0.5")
        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", "5.0")

        client = LightRAGClient(host="localhost", port=9621)

        transport_timeout = client._http_client.timeout  # type: ignore[attr-defined]
        assert isinstance(transport_timeout, httpx.Timeout)
        connect_budget = transport_timeout.connect
        assert connect_budget is not None

        start = time.perf_counter()
        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(LightRAGClientError):
                await client.query(query="what is UO2?")
        elapsed = time.perf_counter() - start

        # Mock raises immediately, so the wall-clock ceiling is the connect envelope.
        assert elapsed <= connect_budget * 1.2

    @pytest.mark.asyncio
    async def test_no_retry_on_connect_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single ConnectError must NOT trigger a retry — exactly one POST."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
        )

        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_CONNECT_S", "0.5")
        monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", "5.0")

        client = LightRAGClient(host="localhost", port=9621)

        with patch.object(
            client._http_client,  # type: ignore[attr-defined]
            "post",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ) as mock_post:
            with pytest.raises(Exception):
                await client.query(query="what is UO2?")

        # AC-2: no silent retry on the query path.
        mock_post.assert_called_once()
