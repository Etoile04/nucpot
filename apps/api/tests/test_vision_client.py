"""Tests for VLM vision client service (NFM-851).

Covers: provider configuration, image encoding, prompt construction,
API calls with retry, provider abstraction, error handling.
Uses mocked HTTP to avoid real API calls.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nfm_db.services.vision_client import (
    VisionClient,
    VisionClientError,
    VisionProvider,
    _detect_mime_type,
    _strip_code_fences,
    build_plot_extraction_prompt,
    build_table_extraction_prompt,
    encode_image_base64,
    is_vlm_configured,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_response(content: dict[str, Any]) -> dict[str, Any]:
    """Build a mock OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    }


def _sample_png_bytes() -> bytes:
    """Minimal valid PNG bytes (1x1 transparent pixel)."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n\xb4\x15\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ---------------------------------------------------------------------------
# Tests: Image encoding
# ---------------------------------------------------------------------------


class TestEncodeImageBase64:
    """Tests for the encode_image_base64 utility."""

    def test_encodes_bytes_to_base64_string(self) -> None:
        """Should return a base64-encoded string of the input bytes."""
        data = b"hello world"
        result = encode_image_base64(data)
        assert result == base64.b64encode(data).decode("ascii")

    def test_encodes_png_bytes(self) -> None:
        """Should correctly encode a minimal PNG."""
        png = _sample_png_bytes()
        result = encode_image_base64(png)
        decoded = base64.b64decode(result)
        assert decoded == png

    def test_empty_bytes_returns_empty_string(self) -> None:
        """Empty input should produce empty base64 output."""
        result = encode_image_base64(b"")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: Prompt construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    """Tests for prompt builder functions."""

    def test_build_plot_extraction_prompt(self) -> None:
        """Plot prompt should mention axes, series, and nuclear materials."""
        prompt = build_plot_extraction_prompt()
        assert "plot" in prompt.lower() or "chart" in prompt.lower()
        assert "axis" in prompt.lower()
        assert "series" in prompt.lower()
        assert "json" in prompt.lower()

    def test_build_table_extraction_prompt(self) -> None:
        """Table prompt should mention headers, rows, and cells."""
        prompt = build_table_extraction_prompt()
        assert "table" in prompt.lower()
        assert "header" in prompt.lower()
        assert "row" in prompt.lower()
        assert "json" in prompt.lower()

    def test_plot_prompt_returns_string(self) -> None:
        """Prompt should be a non-empty string."""
        result = build_plot_extraction_prompt()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_table_prompt_returns_string(self) -> None:
        """Prompt should be a non-empty string."""
        result = build_table_extraction_prompt()
        assert isinstance(result, str)
        assert len(result) > 50


# ---------------------------------------------------------------------------
# Tests: Provider enum
# ---------------------------------------------------------------------------


class TestVisionProvider:
    """Tests for the VisionProvider enum."""

    def test_openai_provider_exists(self) -> None:
        assert VisionProvider.OPENAI.value == "openai"

    def test_ollama_provider_exists(self) -> None:
        assert VisionProvider.OLLAMA.value == "ollama"

    def test_local_provider_exists(self) -> None:
        assert VisionProvider.LOCAL.value == "local"


# ---------------------------------------------------------------------------
# Tests: VisionClient initialization
# ---------------------------------------------------------------------------


class TestVisionClientInit:
    """Tests for VisionClient configuration."""

    def test_default_provider_is_openai(self) -> None:
        """Should default to OpenAI provider."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient()
            assert client.provider == "openai"

    def test_custom_provider(self) -> None:
        """Should accept a custom provider string."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient(provider="ollama")
            assert client.provider == "ollama"

    def test_raises_without_api_key(self) -> None:
        """Should raise ValueError when no API key is set."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="VLM_API_KEY"):
                VisionClient()

    def test_uses_env_var_for_api_key(self) -> None:
        """Should read API key from environment."""
        with patch.dict("os.environ", {"VLM_API_KEY": "my-key-123"}):
            client = VisionClient()
            assert client.api_key == "my-key-123"

    def test_custom_model(self) -> None:
        """Should accept a custom model name."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient(model="gpt-4o-2024-08-06")
            assert client.model == "gpt-4o-2024-08-06"

    def test_custom_base_url(self) -> None:
        """Should accept a custom base URL."""
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient(base_url="http://localhost:11434/v1")
            assert client.base_url == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# Tests: VisionClient.extract
# ---------------------------------------------------------------------------


class TestVisionClientExtract:
    """Tests for the main extract method."""

    @pytest.mark.asyncio
    async def test_returns_parsed_dict_from_vlm(self) -> None:
        """Should parse VLM response JSON into a dict."""
        expected = {
            "title": "UO2 Thermal Conductivity",
            "plot_type": "line",
            "x_axis": {"label": "Temperature", "unit": "K", "values": [300, 400, 500]},
        }

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient()

        mock_response = _make_chat_response(expected)

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await client.extract(
                image_data=_sample_png_bytes(),
                prompt="Extract plot data",
            )

        assert result == expected

    @pytest.mark.asyncio
    async def test_includes_image_in_request(self) -> None:
        """Should include base64-encoded image in the API call."""
        mock_response = _make_chat_response({"title": "test"})
        call_args: list[dict[str, Any]] = []

        async def capture_call(**kwargs: Any) -> dict[str, Any]:
            call_args.append(kwargs)
            return mock_response

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient()

        with patch.object(client, "_call_provider", new_callable=AsyncMock, side_effect=capture_call):
            await client.extract(image_data=_sample_png_bytes(), prompt="Extract")

        assert len(call_args) == 1
        assert "image_base64" in call_args[0]
        assert call_args[0]["image_base64"] == encode_image_base64(_sample_png_bytes())

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json_response(self) -> None:
        """Should raise VisionClientError when VLM returns non-JSON."""
        bad_response = {
            "id": "chatcmpl-test",
            "choices": [
                {"message": {"role": "assistant", "content": "Not valid JSON"}}
            ],
        }

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient()

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, return_value=bad_response
        ):
            with pytest.raises(VisionClientError, match="invalid JSON"):
                await client.extract(image_data=_sample_png_bytes(), prompt="Extract")

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self) -> None:
        """Should raise VisionClientError when VLM returns empty content."""
        empty_response = {
            "choices": [
                {"message": {"role": "assistant", "content": ""}}
            ]
        }

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient()

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, return_value=empty_response
        ):
            with pytest.raises(VisionClientError, match="empty"):
                await client.extract(image_data=_sample_png_bytes(), prompt="Extract")


# ---------------------------------------------------------------------------
# Tests: Retry logic
# ---------------------------------------------------------------------------


class TestVisionClientRetry:
    """Tests for retry and backoff behavior."""

    @pytest.mark.asyncio
    async def test_retries_on_429(self) -> None:
        """Should retry on rate-limit (429) errors."""
        expected = {"title": "retry-test"}
        mock_response = _make_chat_response(expected)

        call_count = 0

        async def flaky_call(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = 429
                raise httpx.HTTPStatusError("Rate limited", request=MagicMock(), response=resp)
            return mock_response

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient(max_retries=3)

        with patch.object(client, "_call_provider", new_callable=AsyncMock, side_effect=flaky_call):
            result = await client.extract(image_data=_sample_png_bytes(), prompt="Extract")

        assert result == expected
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self) -> None:
        """Should raise after exhausting retries."""
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 500

        async def always_fail(**kwargs: Any) -> dict[str, Any]:
            raise httpx.HTTPStatusError("Server error", request=MagicMock(), response=resp)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient(max_retries=2)

        with patch.object(client, "_call_provider", new_callable=AsyncMock, side_effect=always_fail):
            with pytest.raises(httpx.HTTPStatusError):
                await client.extract(image_data=_sample_png_bytes(), prompt="Extract")


# ---------------------------------------------------------------------------
# Tests: is_vlm_configured
# ---------------------------------------------------------------------------


class TestIsVlmConfigured:
    """Tests for the configuration check utility."""

    def test_returns_true_when_key_set(self) -> None:
        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            assert is_vlm_configured() is True

    def test_returns_false_when_key_missing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert is_vlm_configured() is False

    def test_returns_false_when_key_empty(self) -> None:
        with patch.dict("os.environ", {"VLM_API_KEY": ""}):
            assert is_vlm_configured() is False


# ---------------------------------------------------------------------------
# Tests: MIME type detection
# ---------------------------------------------------------------------------


class TestDetectMimeType:
    """Tests for the _detect_mime_type utility."""

    def test_detects_png(self) -> None:
        assert _detect_mime_type(_sample_png_bytes()) == "image/png"

    def test_detects_jpeg(self) -> None:
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        assert _detect_mime_type(jpeg_header) == "image/jpeg"

    def test_detects_gif(self) -> None:
        gif_header = b"GIF89a" + b"\x00" * 20
        assert _detect_mime_type(gif_header) == "image/gif"

    def test_detects_webp(self) -> None:
        webp_header = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        assert _detect_mime_type(webp_header) == "image/webp"

    def test_unknown_falls_back_to_png(self) -> None:
        unknown = b"\x00\x01\x02\x03" * 10
        assert _detect_mime_type(unknown) == "image/png"


# ---------------------------------------------------------------------------
# Tests: Code fence stripping
# ---------------------------------------------------------------------------


class TestStripCodeFences:
    """Tests for the _strip_code_fences utility."""

    def test_strips_json_code_fence(self) -> None:
        text = '```json\n{"title": "test"}\n```'
        assert _strip_code_fences(text) == '{"title": "test"}'

    def test_strips_plain_code_fence(self) -> None:
        text = '```\n{"data": [1, 2]}\n```'
        assert _strip_code_fences(text) == '{"data": [1, 2]}'

    def test_passes_through_plain_json(self) -> None:
        text = '{"title": "test"}'
        assert _strip_code_fences(text) == '{"title": "test"}'

    def test_strips_fence_with_language_tag(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = _strip_code_fences(text)
        assert result == '{"key": "value"}'
        assert not result.startswith("```")


# ---------------------------------------------------------------------------
# Tests: Non-dict JSON response
# ---------------------------------------------------------------------------


class TestNonDictResponse:
    """Tests for non-dict JSON responses from VLM."""

    @pytest.mark.asyncio
    async def test_raises_on_list_response(self) -> None:
        """Should raise VisionClientError when VLM returns a JSON list."""
        list_response = {
            "id": "chatcmpl-test",
            "choices": [
                {"message": {"role": "assistant", "content": "[1, 2, 3]"}}
            ],
        }

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient()

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, return_value=list_response
        ):
            with pytest.raises(VisionClientError, match="non-dict"):
                await client.extract(image_data=_sample_png_bytes(), prompt="Extract")


# ---------------------------------------------------------------------------
# Tests: _call_provider via HTTP
# ---------------------------------------------------------------------------


class TestCallProvider:
    """Tests for the _call_provider HTTP method."""

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self) -> None:
        """Should send proper headers and payload to the VLM endpoint."""
        expected = {"title": "http-test"}
        mock_response = _make_chat_response(expected)

        with patch.dict("os.environ", {"VLM_API_KEY": "test-key"}):
            client = VisionClient(base_url="http://localhost:9999/v1")

        mock_http_response = MagicMock()
        mock_http_response.json.return_value = mock_response
        mock_http_response.raise_for_status = MagicMock()

        with patch("nfm_db.services.vision_client.httpx.AsyncClient") as mock_client_cls:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_instance.post = AsyncMock(return_value=mock_http_response)
            mock_client_cls.return_value = mock_instance

            result = await client._call_provider(
                prompt="Extract data",
                system_prompt="You are a helpful assistant",
                image_base64="abc123",
                mime_type="image/png",
                temperature=0.0,
                max_tokens=4096,
            )

        assert result["choices"][0]["message"]["content"] == json.dumps(expected)
        mock_instance.post.assert_called_once()
