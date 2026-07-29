"""Tests for NFM-2013 hotfix: silent ingestion failure fixes.

Three fixes:
1. call_llm() retry on 502/429/5xx (llm_client.py)
2. mineru_enabled() auto-detect API key (mineru_client.py)
3. GET /extraction/ingest/{job_id}/status endpoint (extraction.py)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nfm_db.services.llm_client import call_llm

# ===================================================================
# Task 1: call_llm retry on transient server errors
# ===================================================================


class TestCallLlmRetry:
    """NFM-2013 Task 1: verify call_llm retries on 502/429/5xx."""

    @staticmethod
    def _make_502_response() -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = "Bad Gateway"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Gateway", request=MagicMock(), response=mock_resp,
        )
        return mock_resp

    @staticmethod
    def _make_success_response(content: dict) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {"message": {"content": json.dumps(content)}, "finish_reason": "stop"}
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    @pytest.mark.asyncio
    async def test_call_llm_retries_on_502_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should retry on 502 and succeed on subsequent attempt."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        call_count = 0

        async def _post(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return self._make_502_response()
            return self._make_success_response({"result": "ok"})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("nfm_db.services.llm_client.httpx.AsyncClient", return_value=mock_client),
            patch("nfm_db.services.llm_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await call_llm(system_prompt="S", user_message="U")

        assert result == {"result": "ok"}
        assert call_count == 3  # 2 failures + 1 success

    @staticmethod
    def _make_429_response() -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Too Many Requests"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Too Many Requests", request=MagicMock(), response=mock_resp,
        )
        return mock_resp

    @pytest.mark.asyncio
    async def test_call_llm_retries_on_429_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should retry on 429 rate-limit errors."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        call_count = 0

        async def _post(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return self._make_429_response()
            return self._make_success_response({"data": 1})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("nfm_db.services.llm_client.httpx.AsyncClient", return_value=mock_client),
            patch("nfm_db.services.llm_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await call_llm(system_prompt="S", user_message="U")

        assert result == {"data": 1}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_call_llm_exhausts_retries_on_persistent_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should raise RuntimeError after 3 failed retries."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        fail_resp = self._make_502_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=fail_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("nfm_db.services.llm_client.httpx.AsyncClient", return_value=mock_client),
            patch("nfm_db.services.llm_client.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(RuntimeError, match="502"),
        ):
            await call_llm(system_prompt="S", user_message="U")

        assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_call_llm_no_retry_on_4xx_client_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should NOT retry on 4xx errors (except 429)."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        bad_req_resp = MagicMock()
        bad_req_resp.status_code = 400
        bad_req_resp.text = "Bad Request"
        bad_req_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=bad_req_resp,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=bad_req_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("nfm_db.services.llm_client.httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RuntimeError, match="400"),
        ):
            await call_llm(system_prompt="S", user_message="U")

        assert mock_client.post.call_count == 1

    @pytest.mark.asyncio
    async def test_call_llm_retries_on_connection_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should retry on httpx.RequestError (connection failure)."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        call_count = 0

        async def _post(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.RequestError("Connection refused", request=MagicMock())
            return self._make_success_response({"ok": True})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_post)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("nfm_db.services.llm_client.httpx.AsyncClient", return_value=mock_client),
            patch("nfm_db.services.llm_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await call_llm(system_prompt="S", user_message="U")

        assert result == {"ok": True}
        assert call_count == 2


# ===================================================================
# Task 2: mineru_enabled() auto-detect API key
# ===================================================================


class TestMineruAutoDetect:
    """NFM-2013 Task 2: mineru_enabled() returns False when no API key."""

    def test_mineru_explicitly_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MINERU_ENABLED=false should disable MinerU regardless of API key."""
        monkeypatch.setenv("MINERU_ENABLED", "false")
        monkeypatch.setenv("MINERU_API_KEY", "has-key")

        import importlib

        import nfm_db.services.mineru_client as mcm
        importlib.reload(mcm)

        assert not mcm.mineru_enabled()

    def test_mineru_explicitly_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MINERU_ENABLED=true should enable MinerU even without API key."""
        monkeypatch.setenv("MINERU_ENABLED", "true")
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        monkeypatch.delenv("MinerU_API_KEY", raising=False)
        monkeypatch.delenv("NFM_MINERU_API_KEY", raising=False)

        import importlib

        import nfm_db.services.mineru_client as mcm
        importlib.reload(mcm)

        assert mcm.mineru_enabled()

    def test_mineru_no_key_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When MINERU_ENABLED is not set and no API key exists, return False."""
        monkeypatch.delenv("MINERU_ENABLED", raising=False)
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        monkeypatch.delenv("MinerU_API_KEY", raising=False)
        monkeypatch.delenv("NFM_MINERU_API_KEY", raising=False)

        import importlib

        import nfm_db.services.mineru_client as mcm
        importlib.reload(mcm)

        # Patch _load_dotenv so .env file doesn't re-inject a key
        with patch.object(mcm, "_load_dotenv_into_environ"):
            assert not mcm.mineru_enabled()

    @pytest.mark.parametrize("key_var", ["MINERU_API_KEY", "MinerU_API_KEY", "NFM_MINERU_API_KEY"])
    def test_mineru_with_key_returns_true(self, monkeypatch: pytest.MonkeyPatch, key_var: str) -> None:
        """When any MINERU API key env var is set, return True."""
        monkeypatch.delenv("MINERU_ENABLED", raising=False)
        for v in ("MINERU_API_KEY", "MinerU_API_KEY", "NFM_MINERU_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv(key_var, "some-key-value")

        import importlib

        import nfm_db.services.mineru_client as mcm
        importlib.reload(mcm)

        assert mcm.mineru_enabled()


# ===================================================================
# Task 3: GET /extraction/ingest/{job_id}/status
# ===================================================================


class TestIngestStatusEndpoint:
    """NFM-2013 Task 3: ingest job status endpoint with Celery fallback."""

    @pytest.mark.asyncio
    async def test_ingest_status_celery_pending(self) -> None:
        """GET /extraction/ingest/{id}/status returns 'pending' for PENDING Celery task."""
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.result = None

        mock_celery = MagicMock()
        mock_celery.AsyncResult.return_value = mock_result

        with (
            patch("nfm_db.api.v1.extraction.get_job", return_value=None),
            patch("nfm_db.api.v1.extraction.celery_app", mock_celery),
        ):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status("test-job-id")

        assert resp["success"] is True
        assert resp["data"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_ingest_status_celery_success(self) -> None:
        """GET /extraction/ingest/{id}/status returns 'completed' for SUCCESS."""
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_result.result = None

        mock_celery = MagicMock()
        mock_celery.AsyncResult.return_value = mock_result

        with (
            patch("nfm_db.api.v1.extraction.get_job", return_value=None),
            patch("nfm_db.api.v1.extraction.celery_app", mock_celery),
        ):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status("test-job-id")

        assert resp["data"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_ingest_status_celery_failure_with_message(self) -> None:
        """GET /extraction/ingest/{id}/status returns error_message for FAILURE."""
        mock_result = MagicMock()
        mock_result.state = "FAILURE"
        mock_result.result = RuntimeError("LLM 502 after retries")

        mock_celery = MagicMock()
        mock_celery.AsyncResult.return_value = mock_result

        with (
            patch("nfm_db.api.v1.extraction.get_job", return_value=None),
            patch("nfm_db.api.v1.extraction.celery_app", mock_celery),
        ):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status("test-job-id")

        assert resp["data"]["status"] == "failed"
        assert "LLM 502" in resp["data"]["error_message"]

    @pytest.mark.asyncio
    async def test_ingest_status_falls_back_to_in_memory(self) -> None:
        """Endpoint returns in-memory job data when available."""
        from uuid import uuid4

        from nfm_db.services.extraction_pipeline import ExtractionJob, JobStatus

        job_id_str = str(uuid4())
        job = ExtractionJob(
            job_id=job_id_str,
            source_reference="doi:10.1234/test",
            source_type="doi",
            status=JobStatus.COMPLETED,
        )

        with patch("nfm_db.api.v1.extraction.get_job", return_value=job):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status(job_id_str)

        assert resp["data"]["status"] == "completed"
        assert resp["data"]["source_reference"] == "doi:10.1234/test"

    @pytest.mark.asyncio
    async def test_ingest_status_404_when_not_found(self) -> None:
        """Endpoint returns 404 when job is not in-memory or Celery."""
        from fastapi import HTTPException

        mock_celery = MagicMock()
        mock_celery.AsyncResult.side_effect = Exception("broker unreachable")

        with (
            patch("nfm_db.api.v1.extraction.get_job", return_value=None),
            patch("nfm_db.api.v1.extraction.celery_app", mock_celery),
            pytest.raises(HTTPException, match="not found"),
        ):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            await get_ingest_job_status("nonexistent-job")

    @pytest.mark.asyncio
    async def test_legacy_status_celery_fallback(self) -> None:
        """GET /extraction/status/{id} also falls back to Celery (NFM-2013)."""
        from uuid import uuid4

        mock_result = MagicMock()
        mock_result.state = "STARTED"
        mock_result.result = None

        mock_celery = MagicMock()
        mock_celery.AsyncResult.return_value = mock_result

        with (
            patch("nfm_db.api.v1.extraction.get_job", return_value=None),
            patch("nfm_db.api.v1.extraction.celery_app", mock_celery),
        ):
            from nfm_db.api.v1.extraction import get_extraction_status

            resp = await get_extraction_status(uuid4())

        assert resp["data"]["status"] == "processing"

    @pytest.mark.asyncio
    async def test_ingest_status_celery_retry_state(self) -> None:
        """RETRY Celery state maps to 'processing'."""
        mock_result = MagicMock()
        mock_result.state = "RETRY"
        mock_result.result = None

        mock_celery = MagicMock()
        mock_celery.AsyncResult.return_value = mock_result

        with (
            patch("nfm_db.api.v1.extraction.get_job", return_value=None),
            patch("nfm_db.api.v1.extraction.celery_app", mock_celery),
        ):
            from nfm_db.api.v1.extraction import get_ingest_job_status

            resp = await get_ingest_job_status("retry-job")

        assert resp["data"]["status"] == "processing"
