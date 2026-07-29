"""Unit tests for the MinerU client (NFM-MINERU-1).

Covers settings helpers, dataclass shape, exception classes, the
sync ``parse_pdf_to_markdown`` dispatcher guard, and the full async
``MinerUClient.parse_pdf`` happy path with all four HTTP interactions
mocked (file-urls/batch, OSS PUT, extract-results/batch poll, full_zip
download). Also exercises the fallback path where pycurl is unavailable
and zip bytes are served via ``httpx``.

The goal is to raise the line coverage of :mod:`nfm_db.services.mineru_client`
above the 80% pytest-cov threshold — the file is 640 lines and shipped
without tests, which drops the suite-wide coverage to ~78% and trips
``--cov-fail-under=80``.
"""

from __future__ import annotations

import importlib.util as _ilu
import io
import os
import zipfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nfm_db.services import mineru_client as mc
from nfm_db.services.mineru_client import (
    MAX_FILE_SIZE_BYTES,
    MinerUAPIError,
    MinerUClient,
    MinerUConfigError,
    MinerUError,
    MinerUResult,
    MinerUTimeoutError,
    _env_bool,
    _env_str,
    _PollResult,
    mineru_api_key,
    mineru_enabled,
    parse_pdf_to_markdown,
)

# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


class TestEnvBool:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MINERU_ENABLED", raising=False)
        assert _env_bool("MINERU_ENABLED", True) is True
        assert _env_bool("MINERU_ENABLED", False) is False

    @pytest.mark.parametrize("truthy", ["true", "1", "yes", "on", "TRUE", " Yes "])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, truthy: str) -> None:
        monkeypatch.setenv("MINERU_ENABLED", truthy)
        assert _env_bool("MINERU_ENABLED", False) is True

    @pytest.mark.parametrize("falsy", ["false", "0", "no", "off", "random"])
    def test_falsy_values(self, monkeypatch: pytest.MonkeyPatch, falsy: str) -> None:
        monkeypatch.setenv("MINERU_ENABLED", falsy)
        assert _env_bool("MINERU_ENABLED", True) is False


class TestEnvStr:
    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("X", raising=False)
        assert _env_str("X", "fallback") == "fallback"

    def test_stripped_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "  value  ")
        assert _env_str("X", "fallback") == "value"

    def test_empty_value_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "   ")
        assert _env_str("X", "fallback") == "fallback"


class TestMineruEnabled:
    def test_defaults_to_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MINERU_ENABLED", raising=False)
        assert mineru_enabled() is True


class TestMineruApiKey:
    def test_prefers_mineru_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ensure the .env-loading walk does not leak the host's real
        # MINERU_API_KEY into these tests.
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        monkeypatch.setenv("MINERU_API_KEY", "primary")
        monkeypatch.setenv("MinerU_API_KEY", "alt-case")
        monkeypatch.setenv("NFM_MINERU_API_KEY", "with-prefix")
        assert mineru_api_key() == "primary"

    def test_falls_back_to_alt_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        monkeypatch.setenv("MinerU_API_KEY", "alt-case")
        assert mineru_api_key() == "alt-case"

    def test_falls_back_to_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        monkeypatch.delenv("MINERU_API_KEY", raising=False)
        monkeypatch.delenv("MinerU_API_KEY", raising=False)
        monkeypatch.setenv("NFM_MINERU_API_KEY", "with-prefix")
        assert mineru_api_key() == "with-prefix"

    def test_returns_none_when_all_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        for var in ("MINERU_API_KEY", "MinerU_API_KEY", "NFM_MINERU_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert mineru_api_key() is None


class TestDotenvLoader:
    def test_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        # No filesystem lookups; calling twice must be a no-op.
        mc._load_dotenv_into_environ()
        mc._load_dotenv_into_environ()
        assert os.environ.get("_MINERU_DOTENV_LOADED") == "1"

    def test_marks_loaded_when_no_env_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        # Force the loader to scan from tmp_path (which has no .env).
        monkeypatch.delenv("_MINERU_DOTENV_LOADED", raising=False)
        # The loader walks Path(__file__).resolve().parents — patch the
        # module's __file__ to point into tmp_path so no real .env files
        # are loaded by the parent walk.
        fake_init = tmp_path / "package" / "__init__.py"
        fake_init.parent.mkdir(parents=True, exist_ok=True)
        fake_init.write_text("", encoding="utf-8")
        with patch.object(mc, "__file__", str(fake_init)):
            mc._load_dotenv_into_environ()
        # Idempotency flag is set even when no files were found.
        assert os.environ.get("_MINERU_DOTENV_LOADED") == "1"


# ---------------------------------------------------------------------------
# Exceptions & dataclasses
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_inheritance(self) -> None:
        assert issubclass(MinerUConfigError, MinerUError)
        assert issubclass(MinerUAPIError, MinerUError)
        assert issubclass(MinerUTimeoutError, MinerUError)

    def test_config_error_can_be_raised(self) -> None:
        with pytest.raises(MinerUError):
            raise MinerUConfigError("missing key")


class TestMinerUResult:
    def test_default_fallback_flag(self) -> None:
        r = MinerUResult(markdown="md", task_id="t", state="done")
        assert r.used_fallback is False
        assert r.pages is None
        assert r.elapsed_seconds == 0.0


class TestPollResult:
    def test_construction(self) -> None:
        p = _PollResult(state="done", full_zip_url="https://x", err_msg=None, pages=5)
        assert p.state == "done"
        assert p.full_zip_url == "https://x"
        assert p.pages == 5


# ---------------------------------------------------------------------------
# MinerUClient.__init__
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Skip the .env auto-load so the host's real key does not leak in.
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        for var in ("MINERU_API_KEY", "MinerU_API_KEY", "NFM_MINERU_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(MinerUConfigError):
            MinerUClient()

    def test_mineru_api_key_lookup_used_when_no_explicit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("_MINERU_DOTENV_LOADED", "1")
        monkeypatch.setenv("MINERU_API_KEY", "env-token")
        monkeypatch.delenv("MinerU_API_KEY", raising=False)
        monkeypatch.delenv("NFM_MINERU_API_KEY", raising=False)
        c = MinerUClient()
        assert c.api_key == "env-token"

    def test_explicit_api_key(self) -> None:
        c = MinerUClient(api_key="sk-test")
        assert c.api_key == "sk-test"

    def test_env_driven_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINERU_API_BASE", "https://mineru.example")
        monkeypatch.setenv("MINERU_MODEL_VERSION", "pipeline")
        monkeypatch.setenv("MINERU_LANGUAGE", "en")
        monkeypatch.setenv("MINERU_ENABLE_FORMULA", "false")
        monkeypatch.setenv("MINERU_ENABLE_TABLE", "false")
        monkeypatch.setenv("MINERU_IS_OCR", "true")
        monkeypatch.setenv("MINERU_POLL_INTERVAL", "1.5")
        monkeypatch.setenv("MINERU_TIMEOUT_SECONDS", "30")
        c = MinerUClient(api_key="k")
        assert c.api_base == "https://mineru.example"
        assert c.model_version == "pipeline"
        assert c.language == "en"
        assert c.enable_formula is False
        assert c.enable_table is False
        assert c.is_ocr is True
        assert c.poll_interval == 1.5
        assert c.timeout_seconds == 30.0

    def test_auth_headers(self) -> None:
        c = MinerUClient(api_key="token")
        assert c._auth_headers() == {
            "Authorization": "Bearer token",
            "Accept": "*/*",
        }


# ---------------------------------------------------------------------------
# Static helpers on the client
# ---------------------------------------------------------------------------


class TestShouldBypassProxy:
    """Proxy bypass logic for MinerU CDN / OSS hosts."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://cdn-mineru.openxlab.org.cn/pdf/x.zip",
            "https://foo.openxlab.org.cn/x.zip",
            "https://bucket.oss-cn-beijing.aliyuncs.com/upload",
            "https://mineru.net/api/v4/file-urls/batch",
        ],
    )
    def test_bypasses_cdn_and_oss(self, url: str) -> None:
        assert mc._should_bypass_proxy(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/file.zip",
            "https://github.com/repo",
            "http://localhost:8000",
        ],
    )
    def test_does_not_bypass_other_hosts(self, url: str) -> None:
        assert mc._should_bypass_proxy(url) is False


class TestRaiseForStatus:
    def test_passes_on_2xx(self) -> None:
        resp = MagicMock(spec=httpx.Response, status_code=200)
        MinerUClient._raise_for_status(resp, "test")  # no raise

    def test_raises_on_4xx(self) -> None:
        resp = MagicMock(spec=httpx.Response, status_code=400, text="bad")
        with pytest.raises(MinerUAPIError):
            MinerUClient._raise_for_status(resp, "test")

    def test_raises_on_5xx(self) -> None:
        resp = MagicMock(spec=httpx.Response, status_code=500, text="server")
        with pytest.raises(MinerUAPIError):
            MinerUClient._raise_for_status(resp, "test")


class TestRaiseForCode:
    def test_passes_on_zero_code(self) -> None:
        MinerUClient._raise_for_code({"code": 0, "msg": "ok"}, "test")
        MinerUClient._raise_for_code({"code": "0", "msg": "ok"}, "test")

    def test_raises_on_non_zero_code(self) -> None:
        with pytest.raises(MinerUAPIError):
            MinerUClient._raise_for_code(
                {"code": 401, "msg": "unauthorized"}, "test"
            )

    def test_raises_on_missing_code(self) -> None:
        with pytest.raises(MinerUAPIError):
            MinerUClient._raise_for_code({"msg": "no code"}, "test")


# ---------------------------------------------------------------------------
# Size validation
# ---------------------------------------------------------------------------


class TestSizeValidation:
    def test_oversized_pdf_rejected(self) -> None:
        c = MinerUClient(api_key="k")
        # Build a PDF-sized payload (just past the limit)
        big = b"%PDF-1.4\n" + b"x" * (MAX_FILE_SIZE_BYTES + 1)
        with pytest.raises(MinerUConfigError):
            import asyncio

            asyncio.run(c.parse_pdf(big))


# ---------------------------------------------------------------------------
# Sync dispatcher guard
# ---------------------------------------------------------------------------


class TestParsePdfToMarkdown:
    def test_disabled_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MINERU_ENABLED", "false")
        monkeypatch.setenv("MINERU_API_KEY", "k")
        with pytest.raises(MinerUConfigError):
            parse_pdf_to_markdown(b"%PDF-1.4\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(markdown: str = "# Hello\n\nbody text\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("paper/full.md", markdown)
        zf.writestr("paper/layout.json", "{}")
    return buf.getvalue()


def _mock_response(json_body: dict[str, Any] | None = None, content: bytes = b"") -> httpx.Response:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    if json_body is not None:
        resp.json.return_value = json_body
        resp.text = str(json_body)
    else:
        resp.content = content
        resp.text = content.decode("utf-8", errors="replace")
    return resp


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, *, zip_bytes: bytes) -> AsyncMock:
    """Patch the AsyncClient used inside parse_pdf.

    Returns the mock client — its ``post``/``get``/``put`` are AsyncMocks
    pre-configured to simulate the four-step happy path:
        1. POST file-urls/batch → { data: { batch_id, file_urls: [...] } }
        2. PUT  to OSS          → 200
        3. GET  extract-results → { data: { extract_result: [{ state: "done", full_zip_url }] } }
        4. GET  full_zip_url    → bytes (the zip)
    """

    apply_resp = _mock_response(
        {
            "code": 0,
            "msg": "ok",
            "data": {
                "batch_id": "batch-abc",
                "file_urls": ["https://oss.example/put?sig=xyz"],
            },
        }
    )
    upload_resp = _mock_response()
    poll_resp = _mock_response(
        {
            "code": 0,
            "msg": "ok",
            "data": {
                "extract_result": [
                    {
                        "file_name": "paper.pdf",
                        "state": "done",
                        "full_zip_url": "https://cdn.example/result.zip",
                        "extract_progress": {"total_pages": 7},
                    }
                ]
            },
        }
    )
    zip_resp = _mock_response(content=zip_bytes)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=apply_resp)
    mock_client.put = AsyncMock(return_value=upload_resp)
    mock_client.get = AsyncMock(side_effect=[poll_resp, zip_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)
    return mock_client


# ---------------------------------------------------------------------------
# Async parse_pdf happy path
# ---------------------------------------------------------------------------


class TestParsePdfAsyncHappy:
    def test_full_flow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        markdown = "# Title\n\nSome parsed content with a $formula$ here.\n"
        zip_bytes = _make_zip(markdown)
        _patch_async_client(monkeypatch, zip_bytes=zip_bytes)

        client = MinerUClient(
            api_key="k",
            poll_interval=0.01,
            timeout_seconds=2.0,
        )
        import asyncio

        result = asyncio.run(client.parse_pdf(b"%PDF-1.4\nsmall body"))
        assert isinstance(result, MinerUResult)
        assert result.markdown == markdown
        assert result.task_id == "batch-abc"
        assert result.state == "done"
        assert result.pages == 7

    def test_is_ocr_propagates_to_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MINERU_IS_OCR", "true")
        zip_bytes = _make_zip()
        mock_client = _patch_async_client(monkeypatch, zip_bytes=zip_bytes)

        client = MinerUClient(api_key="k", poll_interval=0.01, timeout_seconds=2.0)
        import asyncio

        asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

        # First POST is to file-urls/batch
        post_call = mock_client.post.call_args
        payload = post_call.kwargs["json"]
        assert payload["files"][0]["is_ocr"] is True


# ---------------------------------------------------------------------------
# Async failure paths
# ---------------------------------------------------------------------------


class TestParsePdfFailures:
    def test_upload_non_2xx_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-abc",
                    "file_urls": ["https://oss.example/put"],
                },
            }
        )
        upload_resp = MagicMock(spec=httpx.Response, status_code=403, text="denied")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=apply_resp)
        mock_client.put = AsyncMock(return_value=upload_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k")
        import asyncio

        with pytest.raises(MinerUAPIError, match="OSS"):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_poll_returns_failed_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "b1",
                    "file_urls": ["https://oss/put"],
                },
            }
        )
        upload_resp = _mock_response()
        poll_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "extract_result": [
                        {
                            "file_name": "p.pdf",
                            "state": "failed",
                            "err_msg": "ocr crashed",
                        }
                    ]
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=apply_resp)
        mock_client.put = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k", poll_interval=0.01, timeout_seconds=1.0)
        import asyncio

        with pytest.raises(MinerUAPIError, match="ocr crashed"):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_done_without_zip_url_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "b2",
                    "file_urls": ["https://oss/put"],
                },
            }
        )
        upload_resp = _mock_response()
        poll_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "extract_result": [
                        {"file_name": "p.pdf", "state": "done", "full_zip_url": None}
                    ]
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=apply_resp)
        mock_client.put = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k", poll_interval=0.01, timeout_seconds=1.0)
        import asyncio

        with pytest.raises(MinerUAPIError, match="no full_zip_url"):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_poll_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "b3",
                    "file_urls": ["https://oss/put"],
                },
            }
        )
        upload_resp = _mock_response()
        poll_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "extract_result": [
                        {"file_name": "p.pdf", "state": "running"}
                    ]
                },
            }
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=apply_resp)
        mock_client.put = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(
            api_key="k", poll_interval=0.01, timeout_seconds=0.05
        )
        import asyncio

        with pytest.raises(MinerUTimeoutError):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_post_bad_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_resp = MagicMock(spec=httpx.Response, status_code=500, text="oops")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=bad_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k")
        import asyncio

        with pytest.raises(MinerUAPIError):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_post_bad_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_resp = _mock_response({"code": 401, "msg": "unauthorized"})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=bad_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k")
        import asyncio

        with pytest.raises(MinerUAPIError, match="unauthorized"):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_bad_zip_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "b4",
                    "file_urls": ["https://oss/put"],
                },
            }
        )
        upload_resp = _mock_response()
        poll_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "extract_result": [
                        {
                            "file_name": "p.pdf",
                            "state": "done",
                            "full_zip_url": "https://cdn/result.zip",
                        }
                    ]
                },
            }
        )
        bad_zip = MagicMock(spec=httpx.Response)
        bad_zip.status_code = 200
        bad_zip.content = b"not a zip"
        bad_zip.text = "not a zip"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=apply_resp)
        mock_client.put = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(side_effect=[poll_resp, bad_zip])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k", poll_interval=0.01, timeout_seconds=1.0)
        import asyncio

        with pytest.raises(MinerUAPIError, match="non-zip"):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))

    def test_zip_without_full_md_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("paper/layout.json", "{}")
        zip_bytes = buf.getvalue()

        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "b5",
                    "file_urls": ["https://oss/put"],
                },
            }
        )
        upload_resp = _mock_response()
        poll_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "extract_result": [
                        {
                            "file_name": "p.pdf",
                            "state": "done",
                            "full_zip_url": "https://cdn/result.zip",
                        }
                    ]
                },
            }
        )
        zip_resp = _mock_response(content=zip_bytes)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=apply_resp)
        mock_client.put = AsyncMock(return_value=upload_resp)
        mock_client.get = AsyncMock(side_effect=[poll_resp, zip_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        monkeypatch.setattr(mc.httpx, "AsyncClient", lambda *args, **kwargs: mock_client)

        client = MinerUClient(api_key="k", poll_interval=0.01, timeout_seconds=1.0)
        import asyncio

        with pytest.raises(MinerUAPIError, match="no full.md"):
            asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))


# ---------------------------------------------------------------------------
# Zip fallback (no pycurl)
# ---------------------------------------------------------------------------


class TestZipFallback:
    def test_uses_httpx_when_pycurl_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        markdown = "# Fallback\n"
        zip_bytes = _make_zip(markdown)

        apply_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "b6",
                    "file_urls": ["https://oss/put"],
                },
            }
        )
        upload_resp = _mock_response()
        poll_resp = _mock_response(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "extract_result": [
                        {
                            "file_name": "p.pdf",
                            "state": "done",
                            "full_zip_url": "https://cdn/result.zip",
                        }
                    ]
                },
            }
        )

        zip_async_client = AsyncMock()
        zip_resp = MagicMock(spec=httpx.Response)
        zip_resp.status_code = 200
        zip_resp.content = zip_bytes
        zip_async_client.get = AsyncMock(return_value=zip_resp)
        zip_async_client.__aenter__ = AsyncMock(return_value=zip_async_client)
        zip_async_client.__aexit__ = AsyncMock(return_value=None)

        outer_client = AsyncMock()
        outer_client.post = AsyncMock(return_value=apply_resp)
        outer_client.put = AsyncMock(return_value=upload_resp)
        outer_client.get = AsyncMock(side_effect=[poll_resp])
        outer_client.__aenter__ = AsyncMock(return_value=outer_client)
        outer_client.__aexit__ = AsyncMock(return_value=None)

        # First AsyncClient (outer) is used for the four endpoints;
        # the second AsyncClient (inside _fetch_zip_bytes fallback) is
        # the one we return zip bytes from. Switch which one httpx returns
        # based on call order.
        clients = iter([outer_client, zip_async_client])
        monkeypatch.setattr(
            mc.httpx, "AsyncClient", lambda *args, **kwargs: next(clients)
        )

        # Pretend pycurl is not installed by patching
        # `importlib.util.find_spec` on the stdlib module itself —
        # the production code calls it via `importlib.util.find_spec`
        # (looked up by attribute in the inner function's enclosing
        # scope), so patching the module attribute affects the call.
        monkeypatch.setattr(
            _ilu, "find_spec", lambda name: None if name == "pycurl" else _ilu.find_spec(name)
        )

        client = MinerUClient(api_key="k", poll_interval=0.01, timeout_seconds=2.0)
        import asyncio

        result = asyncio.run(client.parse_pdf(b"%PDF-1.4\nbody"))
        assert result.markdown == markdown


# ---------------------------------------------------------------------------
# parse_zip_assets + remap_image_paths
# ---------------------------------------------------------------------------


def _make_zip_with_images(
    images: dict[str, bytes],
    full_md: str = "# title\n\n![](images/abc.jpg)\n",
    layout_bytes: bytes | None = None,
    include_origin_pdf: bool = True,
) -> bytes:
    """Build a synthetic MinerU-style zip in memory."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("full.md", full_md)
        if include_origin_pdf:
            zf.writestr("ee9c895b-2249-4286-a547-cb72ab9ee278_origin.pdf", b"%PDF-1.4\n")
        for name, data in images.items():
            zf.writestr(f"images/{name}", data)
        if layout_bytes is not None:
            zf.writestr("layout.json", layout_bytes)
    return buf.getvalue()


class TestParseZipAssets:
    """Tests for MinerUClient.parse_zip_assets (image persistence enablement)."""

    def test_extracts_markdown_and_images(self) -> None:
        images = {"abc.jpg": b"fake-abc-jpg", "def.png": b"fake-def-png"}
        zip_bytes = _make_zip_with_images(images)
        assets = MinerUClient.parse_zip_assets(zip_bytes)
        assert len(assets.images) == 2
        assert assets.images["abc.jpg"] == b"fake-abc-jpg"
        assert assets.images["def.png"] == b"fake-def-png"
        assert assets.markdown.startswith("# title")
        assert "abc.jpg" in assets.media_root or assets.media_root  # non-empty when present
        assert assets.layout_json is None

    def test_layout_json_is_optional(self) -> None:
        layout = b'{"page": 1}'
        zip_bytes = _make_zip_with_images(
            {"x.jpg": b"x"}, layout_bytes=layout
        )
        assets = MinerUClient.parse_zip_assets(zip_bytes)
        assert assets.layout_json == layout

    def test_no_images_returns_empty_dict(self) -> None:
        zip_bytes = _make_zip_with_images({})
        assets = MinerUClient.parse_zip_assets(zip_bytes)
        assert assets.images == {}
        assert assets.markdown  # full.md still parsed

    def test_malformed_zip_raises(self) -> None:
        with pytest.raises(MinerUAPIError, match="non-zip body"):
            MinerUClient.parse_zip_assets(b"not a zip")

    def test_missing_full_md_raises(self) -> None:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("junk.txt", "no markdown here")
        with pytest.raises(MinerUAPIError, match="no full.md"):
            MinerUClient.parse_zip_assets(buf.getvalue())


class TestMinUZipAssetsRemap:
    """Tests for MinerUZipAssets.remap_image_paths."""

    def test_remap_substitutes_image_paths(self) -> None:
        zip_bytes = _make_zip_with_images(
            {"abc.jpg": b"fake"},
            full_md="before ![](images/abc.jpg) after",
        )
        assets = MinerUClient.parse_zip_assets(zip_bytes)
        remapped = assets.remap_image_paths("data_sources/ds-1/images")
        assert remapped == "before ![](data_sources/ds-1/images/abc.jpg) after"
        # The remap string still contains the substring images/abc.jpg
        # (as part of the new path), so we only confirm the original
        # images/abc.jpg segment is no longer present at the start
        # of the image link.
        assert remapped.startswith("before ![](data_sources/ds-1/images/abc.jpg)")

    def test_remap_no_images_is_identity(self) -> None:
        zip_bytes = _make_zip_with_images({})
        assets = MinerUClient.parse_zip_assets(zip_bytes)
        original = assets.markdown
        assert assets.remap_image_paths("anything") == original

    def test_remap_multiple_distinct_hashes(self) -> None:
        zip_bytes = _make_zip_with_images(
            {"a.jpg": b"a", "b.jpg": b"b", "c.jpg": b"c"},
            full_md="![](images/a.jpg) ![](images/b.jpg) ![](images/c.jpg)",
        )
        assets = MinerUClient.parse_zip_assets(zip_bytes)
        remapped = assets.remap_image_paths("ds_root")
        for h in ("a.jpg", "b.jpg", "c.jpg"):
            assert f"ds_root/{h}" in remapped
