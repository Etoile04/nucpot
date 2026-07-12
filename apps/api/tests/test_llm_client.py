"""Unit tests for the LLM client service (NFM-540).

Tests acceptance criteria:
- LLMClient.extract_structured(prompt, system_prompt, schema) -> dict
- Temperature=0, seed fixed for reproducibility
- Response caching: same (prompt_hash, model) -> cached result
- Retry logic with exponential backoff (max 3 retries)
- Timeout configuration (default 60s)
- Logging for all calls (prompt hash, tokens used, latency)
- JSON schema validation on response
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from nfm_db.services.llm_client import (
    LLMClient,
    LLMResponse,
    _compute_cache_key,
    _get_config,
    _strip_code_fences,
    _validate_json_schema,
    is_llm_configured,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set minimal required environment variables for LLM client."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("LLM_API_KEY", "test-key-123")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")


@pytest.fixture
def client(mock_env: None) -> LLMClient:
    """Create an LLMClient with env vars and a fresh cache."""
    return LLMClient()


def _mock_openai_response(content: dict[str, Any]) -> dict[str, Any]:
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
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


# ---------------------------------------------------------------------------
# 1. extract_structured returns parsed dict
# ---------------------------------------------------------------------------


class TestExtractStructured:
    """Tests for the extract_structured method."""

    @pytest.mark.asyncio
    async def test_returns_parsed_dict_from_valid_response(self, client: LLMClient) -> None:
        """extract_structured should return a parsed dict from a valid LLM response."""
        expected = {"element": "U", "property": "density", "value": 19.1}
        mock_body = _mock_openai_response(expected)

        with patch.object(client, "_call_provider", new_callable=AsyncMock, return_value=mock_body):
            result = await client.extract_structured(
                prompt="Extract properties from this text.",
                schema={"type": "object", "properties": {"element": {"type": "string"}}},
            )

        assert result == expected

    @pytest.mark.asyncio
    async def test_passes_system_prompt_to_provider(self, client: LLMClient) -> None:
        """extract_structured should forward the system prompt."""
        system_prompt = "You are a materials scientist."
        mock_body = _mock_openai_response({"ok": True})

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, return_value=mock_body
        ) as mock_call:
            await client.extract_structured(
                prompt="Extract data.",
                system_prompt=system_prompt,
                schema={"type": "object"},
            )

            call_kwargs = mock_call.call_args
            assert call_kwargs[1]["system_prompt"] == system_prompt

    @pytest.mark.asyncio
    async def test_defaults_system_prompt_to_extraction_role(self, client: LLMClient) -> None:
        """When no system_prompt given, use a sensible default."""
        mock_body = _mock_openai_response({"ok": True})

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, return_value=mock_body
        ) as mock_call:
            await client.extract_structured(
                prompt="Extract data.",
                schema={"type": "object"},
            )

            call_kwargs = mock_call.call_args
            assert call_kwargs[1]["system_prompt"] is not None


# ---------------------------------------------------------------------------
# 2. Temperature=0 and seed fixed for reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Tests that ensure deterministic LLM calls."""

    @pytest.mark.asyncio
    async def test_temperature_zero_in_request(self, client: LLMClient) -> None:
        """The request payload should include temperature=0."""
        mock_body = _mock_openai_response({"data": True})

        with patch.object(
            client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_body,
        ) as mock_call:
            await client.extract_structured(
                prompt="Extract.",
                schema={"type": "object"},
            )

            _, kwargs = mock_call.call_args
            assert kwargs["temperature"] == 0

    @pytest.mark.asyncio
    async def test_seed_is_fixed(self, client: LLMClient) -> None:
        """The request should include a fixed seed parameter."""
        mock_body = _mock_openai_response({"data": True})

        with patch.object(
            client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_body,
        ) as mock_call:
            await client.extract_structured(
                prompt="Extract.",
                schema={"type": "object"},
            )

            _, kwargs = mock_call.call_args
            assert "seed" in kwargs
            assert kwargs["seed"] > 0


# ---------------------------------------------------------------------------
# 3. Response caching with deduplication
# ---------------------------------------------------------------------------


class TestCaching:
    """Tests for response caching by (prompt_hash, model, schema)."""

    def test_cache_key_is_deterministic(self) -> None:
        """Same inputs should produce the same cache key."""
        k1 = _compute_cache_key("prompt", "system", "model")
        k2 = _compute_cache_key("prompt", "system", "model")
        assert k1 == k2

    def test_cache_key_differs_by_prompt(self) -> None:
        """Different prompts should produce different cache keys."""
        k1 = _compute_cache_key("prompt A", "system", "model")
        k2 = _compute_cache_key("prompt B", "system", "model")
        assert k1 != k2

    def test_cache_key_differs_by_schema(self) -> None:
        """Different schemas should produce different cache keys."""
        k1 = _compute_cache_key("prompt", "system", "model", {"type": "object"})
        k2 = _compute_cache_key("prompt", "system", "model", {"type": "object", "required": ["a"]})
        assert k1 != k2

    def test_cache_key_is_sha256_hex(self) -> None:
        """Cache key should be a 64-char hex string (SHA-256)."""
        key = _compute_cache_key("p", "s", "m")
        assert len(key) == 64
        int(key, 16)  # valid hex

    @pytest.mark.asyncio
    async def test_identical_prompt_returns_cached_result(self, client: LLMClient) -> None:
        """Second call with same prompt+model should return cached result without HTTP call."""
        expected = {"cached": True}
        mock_body = _mock_openai_response(expected)

        with patch.object(
            client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_body,
        ) as mock_call:
            # First call — hits the provider
            result1 = await client.extract_structured(
                prompt="Same prompt.",
                schema={"type": "object"},
            )
            assert mock_call.call_count == 1

            # Second call — should use cache
            result2 = await client.extract_structured(
                prompt="Same prompt.",
                schema={"type": "object"},
            )
            assert mock_call.call_count == 1  # no additional call

        assert result1 == expected
        assert result2 == expected

    @pytest.mark.asyncio
    async def test_different_prompt_bypasses_cache(self, client: LLMClient) -> None:
        """Different prompt should trigger a new provider call."""
        mock_body = _mock_openai_response({"ok": True})

        with patch.object(
            client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_body,
        ) as mock_call:
            await client.extract_structured(
                prompt="First prompt.",
                schema={"type": "object"},
            )
            await client.extract_structured(
                prompt="Different prompt.",
                schema={"type": "object"},
            )

            assert mock_call.call_count == 2

    @pytest.mark.asyncio
    async def test_different_system_prompt_bypasses_cache(self, client: LLMClient) -> None:
        """Same prompt but different system_prompt should bypass cache."""
        mock_body = _mock_openai_response({"ok": True})

        with patch.object(
            client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_body,
        ) as mock_call:
            await client.extract_structured(
                prompt="Same.",
                system_prompt="Role A",
                schema={"type": "object"},
            )
            await client.extract_structured(
                prompt="Same.",
                system_prompt="Role B",
                schema={"type": "object"},
            )

            assert mock_call.call_count == 2

    @pytest.mark.asyncio
    async def test_different_schema_bypasses_cache(self, client: LLMClient) -> None:
        """Same prompt and system_prompt but different schema should bypass cache."""
        mock_body = _mock_openai_response({"ok": True})

        with patch.object(
            client,
            "_call_provider",
            new_callable=AsyncMock,
            return_value=mock_body,
        ) as mock_call:
            await client.extract_structured(
                prompt="Same.",
                schema={"type": "object"},
            )
            await client.extract_structured(
                prompt="Same.",
                schema={"type": "object", "required": ["ok"]},
            )

            assert mock_call.call_count == 2


# ---------------------------------------------------------------------------
# 4. Retry logic with exponential backoff (max 3 retries)
# ---------------------------------------------------------------------------


class TestRetry:
    """Tests for retry behavior on transient errors."""

    @pytest.mark.asyncio
    async def test_retries_on_http_500(self, client: LLMClient) -> None:
        """Should retry on server errors and eventually succeed."""
        call_count = 0

        async def _fail_then_succeed(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                mock_resp = MagicMock(spec=httpx.Response)
                mock_resp.status_code = 500
                mock_resp.text = "Internal Server Error"
                err = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_resp)
                raise err
            return _mock_openai_response({"retried": True})

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, side_effect=_fail_then_succeed
        ):
            result = await client.extract_structured(
                prompt="Retry me.",
                schema={"type": "object"},
            )

        assert result == {"retried": True}

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, client: LLMClient) -> None:
        """Should raise after exhausting max retries (3 attempts total)."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_resp.text = "Server Error"

        def _always_fail(**kwargs: Any) -> dict[str, Any]:
            err = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_resp)
            raise err

        with (
            patch.object(
                client, "_call_provider", new_callable=AsyncMock, side_effect=_always_fail
            ),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client.extract_structured(
                prompt="Always fails.",
                schema={"type": "object"},
            )

    @pytest.mark.asyncio
    async def test_retries_on_429_rate_limit(self, client: LLMClient) -> None:
        """Should retry on 429 Too Many Requests and eventually succeed."""
        call_count = 0

        async def _rate_limit_then_ok(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                mock_resp = MagicMock(spec=httpx.Response)
                mock_resp.status_code = 429
                mock_resp.text = "Too Many Requests"
                err = httpx.HTTPStatusError("Rate Limited", request=MagicMock(), response=mock_resp)
                raise err
            return _mock_openai_response({"ok": True})

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, side_effect=_rate_limit_then_ok
        ):
            result = await client.extract_structured(
                prompt="Rate limited.",
                schema={"type": "object"},
            )

        assert result == {"ok": True}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_exponential_backoff_delays(self, client: LLMClient) -> None:
        """Retries should use exponential backoff with increasing delays."""
        timestamps: list[float] = []
        call_count = 0

        async def _record_time(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            timestamps.append(time.monotonic())
            if call_count < 3:
                mock_resp = MagicMock(spec=httpx.Response)
                mock_resp.status_code = 500
                mock_resp.text = "Server Error"
                err = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_resp)
                raise err
            return _mock_openai_response({"done": True})

        with patch.object(
            client, "_call_provider", new_callable=AsyncMock, side_effect=_record_time
        ):
            await client.extract_structured(
                prompt="Measure delays.",
                schema={"type": "object"},
            )

        assert len(timestamps) == 3
        # Second delay should be longer than first
        delay1 = timestamps[1] - timestamps[0]
        delay2 = timestamps[2] - timestamps[1]
        assert delay2 >= delay1


# ---------------------------------------------------------------------------
# 5. Timeout configuration (default 60s)
# ---------------------------------------------------------------------------


class TestTimeout:
    """Tests for timeout handling."""

    def test_default_timeout_is_60(self, client: LLMClient) -> None:
        """Default timeout should be 60 seconds."""
        assert client.timeout == 60.0

    def test_custom_timeout_via_init(self, mock_env: None) -> None:
        """Timeout should be configurable via constructor."""
        custom_client = LLMClient(timeout=30.0)
        assert custom_client.timeout == 30.0


# ---------------------------------------------------------------------------
# 6. Logging for all calls
# ---------------------------------------------------------------------------


class TestLogging:
    """Tests that verify logging behavior."""

    @pytest.mark.asyncio
    async def test_logs_call_details(self, client: LLMClient) -> None:
        """Should log prompt hash, tokens used, and latency."""
        mock_body = _mock_openai_response({"logged": True})

        with (
            patch.object(client, "_call_provider", new_callable=AsyncMock, return_value=mock_body),
            patch("nfm_db.services.llm_client.logger") as mock_logger,
        ):
            await client.extract_structured(
                prompt="Log this.",
                schema={"type": "object"},
            )

            # logger.info should have been called
            info_calls = [c for c in mock_logger.info.call_args_list]
            assert len(info_calls) >= 1
            # Check that tokens and latency are in log output
            logged_text = str(info_calls[0])
            assert "token" in logged_text.lower() or "usage" in logged_text.lower()


# ---------------------------------------------------------------------------
# 7. JSON schema validation on response
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Tests for JSON schema validation of LLM responses."""

    @pytest.mark.asyncio
    async def test_valid_response_passes_schema_validation(self, client: LLMClient) -> None:
        """A response conforming to the schema should be returned as-is."""
        schema = {
            "type": "object",
            "required": ["name", "value"],
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "number"},
            },
        }
        valid_data = {"name": "Uranium", "value": 238.0}
        mock_body = _mock_openai_response(valid_data)

        with patch.object(client, "_call_provider", new_callable=AsyncMock, return_value=mock_body):
            result = await client.extract_structured(
                prompt="Extract element.",
                schema=schema,
            )

        assert result == valid_data

    @pytest.mark.asyncio
    async def test_invalid_schema_raises_value_error(self, client: LLMClient) -> None:
        """A response not matching the schema should raise ValueError."""
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        invalid_data = {"wrong_field": 42}  # missing required "name"
        mock_body = _mock_openai_response(invalid_data)

        with (
            patch.object(client, "_call_provider", new_callable=AsyncMock, return_value=mock_body),
            pytest.raises(ValueError, match="Schema validation"),
        ):
            await client.extract_structured(
                prompt="Extract element.",
                schema=schema,
            )

    @pytest.mark.asyncio
    async def test_wrong_type_field_raises_value_error(self, client: LLMClient) -> None:
        """A number field with a string value should raise ValueError."""
        schema = {
            "type": "object",
            "properties": {
                "atomic_number": {"type": "number"},
            },
        }
        bad_data = {"atomic_number": "not-a-number"}
        mock_body = _mock_openai_response(bad_data)

        with (
            patch.object(client, "_call_provider", new_callable=AsyncMock, return_value=mock_body),
            pytest.raises(ValueError, match="expected number"),
        ):
            await client.extract_structured(
                prompt="Extract atomic number.",
                schema=schema,
            )

    def test_schema_validation_rejects_non_dict(self) -> None:
        """_validate_json_schema should reject non-dict data for type=object schema."""
        with pytest.raises(ValueError, match="expected object"):
            _validate_json_schema(
                data="not a dict",
                schema={"type": "object"},
            )

    def test_schema_validation_rejects_wrong_string_type(self) -> None:
        """_validate_json_schema should reject a number where string is expected."""
        with pytest.raises(ValueError, match="expected string"):
            _validate_json_schema(
                data={"name": 42},
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            )

    @pytest.mark.asyncio
    async def test_non_json_response_raises_value_error(self, client: LLMClient) -> None:
        """If the LLM returns non-JSON content, should raise ValueError."""
        bad_content = _mock_openai_response({"valid": True})
        bad_content["choices"][0]["message"]["content"] = "Not JSON at all!"

        with (
            patch.object(
                client, "_call_provider", new_callable=AsyncMock, return_value=bad_content
            ),
            pytest.raises(ValueError, match="JSON"),
        ):
            await client.extract_structured(
                prompt="Parse this.",
                schema={"type": "object"},
            )

    @pytest.mark.asyncio
    async def test_non_dict_json_response_raises_value_error(self, client: LLMClient) -> None:
        """If the LLM returns valid JSON but not a dict (e.g. a list), raise ValueError."""
        bad_content = _mock_openai_response({"valid": True})
        bad_content["choices"][0]["message"]["content"] = json.dumps([1, 2, 3])

        with (
            patch.object(
                client, "_call_provider", new_callable=AsyncMock, return_value=bad_content
            ),
            pytest.raises(ValueError, match="non-dict"),
        ):
            await client.extract_structured(
                prompt="Parse this.",
                schema={"type": "object"},
            )


# ---------------------------------------------------------------------------
# 8. LLMResponse dataclass
# ---------------------------------------------------------------------------


class TestLLMResponse:
    """Tests for the LLMResponse dataclass."""

    def test_response_has_expected_fields(self) -> None:
        """LLMResponse should carry content, usage, and latency_ms."""
        resp = LLMResponse(
            content={"key": "value"},
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            latency_ms=150.0,
        )
        assert resp.content == {"key": "value"}
        assert resp.usage["total_tokens"] == 15
        assert resp.latency_ms == 150.0


# ---------------------------------------------------------------------------
# 9. Environment variable configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    """Tests for environment-based configuration."""

    def test_reads_env_vars(self, mock_env: None) -> None:
        """LLMClient should read config from environment variables."""
        c = LLMClient()
        assert c.provider == "openai"
        assert c.model == "gpt-4o-mini"

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing LLM_API_KEY should raise ValueError at init."""
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")

        with pytest.raises(ValueError, match="LLM_API_KEY"):
            LLMClient()

    def test_base_url_defaults_if_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LLM_BASE_URL should fall back to a provider default if missing."""
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("LLM_API_KEY", "test-key")

        # Don't set LLM_BASE_URL at all
        monkeypatch.delenv("LLM_BASE_URL", raising=False)

        c = LLMClient()
        assert c.base_url.startswith("https://")


# ---------------------------------------------------------------------------
# 10. _call_provider HTTP layer
# ---------------------------------------------------------------------------


class TestCallProvider:
    """Tests for the _call_provider HTTP method."""

    @staticmethod
    def _build_mock_http_client(response_body: dict[str, Any]) -> AsyncMock:
        """Create a mock httpx.AsyncClient that returns the given response body."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_body
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_posts_to_correct_endpoint(self, client: LLMClient) -> None:
        """Should POST to /chat/completions on the configured base URL."""
        mock_http = self._build_mock_http_client(_mock_openai_response({"ok": True}))

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await client._call_provider(
                prompt="Test prompt.",
                system_prompt="Test system.",
                temperature=0,
                seed=42,
            )

            mock_http.post.assert_called_once()
            url = mock_http.post.call_args[0][0]
            assert url == "https://api.example.com/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_sends_correct_headers(self, client: LLMClient) -> None:
        """Should include Authorization Bearer and Content-Type headers."""
        mock_http = self._build_mock_http_client(_mock_openai_response({"ok": True}))

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await client._call_provider(
                prompt="P",
                system_prompt="S",
                temperature=0,
                seed=42,
            )

            headers = mock_http.post.call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer test-key-123"
            assert headers["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_sends_correct_payload(self, client: LLMClient) -> None:
        """Payload should include model, messages, temperature, seed."""
        mock_http = self._build_mock_http_client(_mock_openai_response({"ok": True}))

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await client._call_provider(
                prompt="Extract data.",
                system_prompt="Be precise.",
                temperature=0,
                seed=42,
            )

            payload = mock_http.post.call_args[1]["json"]
            assert payload["model"] == "gpt-4o-mini"
            assert len(payload["messages"]) == 2
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][1]["role"] == "user"
            assert payload["temperature"] == 0
            assert payload["seed"] == 42

    @pytest.mark.asyncio
    async def test_returns_response_body_with_latency(self, client: LLMClient) -> None:
        """Should return the response JSON body with _latency_ms injected."""
        mock_body = _mock_openai_response({"ok": True})
        mock_http = self._build_mock_http_client(mock_body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            result = await client._call_provider(
                prompt="P",
                system_prompt="S",
                temperature=0,
                seed=42,
            )

            assert "choices" in result
            assert "_latency_ms" in result
            assert result["_latency_ms"] >= 0


# ---------------------------------------------------------------------------
# 11. 4xx errors raised immediately (no retry)
# ---------------------------------------------------------------------------


class TestNonRetryableErrors:
    """Tests for non-retryable (4xx) error handling."""

    @pytest.mark.asyncio
    async def test_400_raised_immediately(self, client: LLMClient) -> None:
        """400 Bad Request should be raised on first attempt, no retries."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        def _raise_400(**kwargs: Any) -> dict[str, Any]:
            raise httpx.HTTPStatusError(
                "Bad Request",
                request=MagicMock(),
                response=mock_resp,
            )

        with (
            patch.object(client, "_call_provider", new_callable=AsyncMock, side_effect=_raise_400),
            pytest.raises(httpx.HTTPStatusError) as exc_info,
        ):
            await client.extract_structured(
                prompt="Bad request test.",
                schema={"type": "object"},
            )

        # The error should be from attempt 1, no retries
        assert exc_info.value.response.status_code == 400

    @pytest.mark.asyncio
    async def test_401_raised_immediately(self, client: LLMClient) -> None:
        """401 Unauthorized should be raised on first attempt, no retries."""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401

        def _raise_401(**kwargs: Any) -> dict[str, Any]:
            raise httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=mock_resp,
            )

        call_count = 0

        async def _counted_401(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=mock_resp,
            )

        with (
            patch.object(
                client, "_call_provider", new_callable=AsyncMock, side_effect=_counted_401
            ),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client.extract_structured(
                prompt="Auth test.",
                schema={"type": "object"},
            )

        # Should only have been called once (no retries for 4xx)
        assert call_count == 1


# ---------------------------------------------------------------------------
# 12. Legacy backward-compat functions
# ---------------------------------------------------------------------------


class TestLegacyFunctions:
    """Tests for legacy backward-compatibility functions."""

    def test_get_config_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_config should read LLM config from environment."""
        monkeypatch.setenv("LLM_API_KEY", "key-123")
        monkeypatch.setenv("LLM_BASE_URL", "https://custom.api/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        cfg = _get_config()
        assert cfg["api_key"] == "key-123"
        assert cfg["base_url"] == "https://custom.api/v1"
        assert cfg["model"] == "gpt-4o"

    def test_strip_code_fences_removes_wrapping(self) -> None:
        """_strip_code_fences should remove ```json ... ``` wrapping."""
        raw = '```json\n{"key": "value"}\n```'
        assert _strip_code_fences(raw) == '{"key": "value"}'

    def test_strip_code_fences_plain_json_unchanged(self) -> None:
        """_strip_code_fences should leave plain JSON unchanged."""
        raw = '{"key": "value"}'
        assert _strip_code_fences(raw) == raw

    def test_strip_code_fences_with_language_tag(self) -> None:
        """Should handle fences with language tags like ```python."""
        raw = '```\n{"key": "value"}\n```'
        assert _strip_code_fences(raw) == '{"key": "value"}'

    def test_is_llm_configured_returns_true_with_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """is_llm_configured should return True when API key is set."""
        monkeypatch.setenv("LLM_API_KEY", "has-key")
        assert is_llm_configured() is True

    def test_is_llm_configured_returns_false_without_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """is_llm_configured should return False when API key is not set."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        assert is_llm_configured() is False


# ---------------------------------------------------------------------------
# 13. No Agent Skills runtime dependency
# ---------------------------------------------------------------------------


class TestNoAgentDependency:
    """Verify the module does not import agent skills."""

    def test_no_anthropic_sdk_import(self) -> None:
        """The llm_client module should not import anthropic or openai SDKs."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "src" / "nfm_db" / "services" / "llm_client.py"
        )
        text = source.read_text(encoding="utf-8")
        assert "import anthropic" not in text
        assert "from anthropic" not in text
        assert "import openai" not in text
        assert "from openai" not in text
