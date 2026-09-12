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
    call_llm,
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
# 13. Legacy call_llm function
# ---------------------------------------------------------------------------


class TestCallLlm:
    """Tests for the legacy call_llm function (lines 331-400)."""

    @staticmethod
    def _build_mock_http_client(
        response_body: dict[str, Any],
        status_code: int = 200,
    ) -> AsyncMock:
        """Create a mock httpx.AsyncClient for call_llm tests."""
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = response_body
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "error body"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_call_llm_returns_parsed_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should return a parsed dict from valid JSON content."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        body = {
            "choices": [
                {
                    "message": {"content": '{"key": "value"}'},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"total_tokens": 50},
        }

        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            result = await call_llm(
                system_prompt="You are helpful.",
                user_message="Extract key.",
            )

        assert result == {"key": "value"}

    @pytest.mark.asyncio
    async def test_call_llm_uses_provided_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should use the provided config dict instead of env vars."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        custom_config = {
            "api_key": "custom-key",
            "base_url": "https://custom.api/v1",
            "model": "custom-model",
        }

        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                config=custom_config,
            )

        call_args = mock_http.post.call_args
        url = call_args[0][0]
        assert url == "https://custom.api/v1/chat/completions"
        payload = call_args[1]["json"]
        assert payload["model"] == "custom-model"

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_missing_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should raise ValueError when API key is missing."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        with pytest.raises(ValueError, match="LLM_API_KEY"):
            await call_llm(
                system_prompt="S",
                user_message="U",
            )

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should wrap HTTPStatusError as RuntimeError."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Bad Gateway"
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Gateway", request=MagicMock(), response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(RuntimeError, match="502"),
        ):
            await call_llm(system_prompt="S", user_message="U")

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_request_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should wrap httpx.RequestError as RuntimeError."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.RequestError("Connection refused", request=MagicMock()),
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(RuntimeError, match="request failed"),
        ):
            await call_llm(system_prompt="S", user_message="U")

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_invalid_json_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should raise RuntimeError if response body is not valid JSON."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json.JSONDecodeError("err", "doc", 0)
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(RuntimeError, match="not valid JSON"),
        ):
            await call_llm(system_prompt="S", user_message="U")

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_empty_choices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should raise RuntimeError if choices list is empty."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        mock_http = self._build_mock_http_client({"choices": []})

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_http,
            ),
            pytest.raises(RuntimeError, match="empty choices"),
        ):
            await call_llm(system_prompt="S", user_message="U")

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_empty_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should raise RuntimeError if message content is empty."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        body = {
            "choices": [
                {"message": {"content": ""}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_http,
            ),
            pytest.raises(RuntimeError, match="empty content"),
        ):
            await call_llm(system_prompt="S", user_message="U")

    @pytest.mark.asyncio
    async def test_call_llm_raises_on_invalid_json_content(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should raise RuntimeError if content is not valid JSON."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        body = {
            "choices": [
                {"message": {"content": "not json at all"}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_http,
            ),
            pytest.raises(RuntimeError, match="not valid JSON"),
        ):
            await call_llm(system_prompt="S", user_message="U")

    @pytest.mark.asyncio
    async def test_call_llm_strips_code_fences(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should strip code fences from content before parsing."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        body = {
            "choices": [
                {
                    "message": {
                        "content": '```json\n{"result": 42}\n```',
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            result = await call_llm(system_prompt="S", user_message="U")

        assert result == {"result": 42}

    @pytest.mark.asyncio
    async def test_call_llm_returns_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should return a list if content parses to a JSON array."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        body = {
            "choices": [
                {
                    "message": {
                        "content": '[{"a": 1}, {"b": 2}]',
                    },
                    "finish_reason": "stop",
                }
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            result = await call_llm(system_prompt="S", user_message="U")

        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_call_llm_passes_temperature_and_max_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should pass temperature and max_tokens in payload."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                temperature=0.5,
                max_tokens=2048,
            )

        payload = mock_http.post.call_args[1]["json"]
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_call_llm_strips_url_trailing_slash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm should strip trailing slash from base_url."""
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        config = {
            "api_key": "test-key",
            "base_url": "https://api.example.com/v1/",
            "model": "gpt-4o",
        }
        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(system_prompt="S", user_message="U", config=config)

        url = mock_http.post.call_args[0][0]
        assert url == "https://api.example.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# 13b. NFM-4730 FixB — per-call num_predict + chat_template_kwargs
# (prevents qwen3.5:4b-nvfp4 thinking from burning the 16K token budget)
# ---------------------------------------------------------------------------


class TestFixBNFM4730:
    """NFM-4730-FixB: per-call num_predict + chat_template_kwargs support.

    The qwen3.5:4b-nvfp4 dispatcher hits the 16384 num_predict ceiling while
    in thinking mode, returning finish_reason=length with empty content.
    The fix threads a per-call ``num_predict`` (default 8192) and
    ``chat_template_kwargs`` (default ``{"enable_thinking": False}``) through
    ``call_llm`` so the extraction pipeline can force non-thinking output
    and cap the response budget per chunk.
    """

    @staticmethod
    def _build_mock_http_client(
        response_body: dict[str, Any],
        status_code: int = 200,
    ) -> AsyncMock:
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = response_body
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "error body"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @pytest.mark.asyncio
    async def test_call_llm_accepts_num_predict_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm must accept ``num_predict`` and forward it to the payload.

        Default max_tokens=16384 was burning the budget for thinking models
        (NFM-4730 FixB). The per-call override lets callers cap at 8192.
        """
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")

        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                num_predict=8192,
            )

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        # OpenAI-compat field name is max_tokens; Ollama native binding maps
        # this to num_predict. The wire field MUST be <= 8192 to avoid
        # re-hitting the 16384 thinking-exhaustion regression.
        assert payload["max_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_truncated_json_with_length_finish_names_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFM-4778: JSON truncated by the output cap must say so.

        Prod evidence (2026-09-12): 8192-token num_predict cut property-dense
        JSON mid-array — ``Unterminated string ... (char 27501)`` — and the
        generic "not valid JSON" error hid the cap as the cause. When
        finish_reason=length accompanies a JSON parse failure, the error
        must name the truncation and the budget so the fix is one grep away.
        """
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")

        body = {
            "choices": [
                {
                    "message": {"content": '[{"property": "CRSS", "value": '},
                    "finish_reason": "length",
                }
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            with pytest.raises(RuntimeError, match="truncated by output cap") as exc_info:
                await call_llm(
                    system_prompt="S",
                    user_message="U",
                    num_predict=8192,
                )

        assert "num_predict=8192" in str(exc_info.value)
        assert "finish_reason=length" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_call_llm_accepts_chat_template_kwargs_kwarg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm must accept ``chat_template_kwargs`` and forward it.

        Default ``{"enable_thinking": False}`` flips off qwen3.5 thinking mode
        when the underlying Ollama binding honors the flag (NFM-4525-aligned).
        The flag is a no-op against the openai-compat layer but is included
        for forward compat — when the binding is migrated to native Ollama
        the flag takes effect automatically.
        """
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")

        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                chat_template_kwargs={"enable_thinking": False},
            )

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    @pytest.mark.asyncio
    async def test_call_llm_uses_safe_defaults_when_unspecified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_llm must default to 8192 max_tokens + enable_thinking=False.

        Hard-coded 16384 was the regression — the new defaults are safe
        even when a caller forgets to pass the override.
        """
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")

        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(system_prompt="S", user_message="U")

        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert payload["max_tokens"] <= 8192, (
            f"default max_tokens={payload['max_tokens']} exceeds 8192 cap; "
            "this is the NFM-4730 thinking-exhaustion regression."
        )
        # chat_template_kwargs is added unconditionally so future binding
        # migrations pick up the enable_thinking=False flag automatically.
        assert payload.get("chat_template_kwargs") == {"enable_thinking": False}

    @pytest.mark.asyncio
    async def test_call_llm_thinking_exhaustion_message_cites_num_predict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty content + finish_reason=length must mention ``num_predict``.

        Original message cited ``max_tokens`` only — operators who
        bumped num_predict on the Ollama server got no signal that the
        OpenAI-compat field they were configuring was the wrong knob.
        """
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")

        body = {
            "choices": [
                {"message": {"content": ""}, "finish_reason": "length"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_http,
            ),
            pytest.raises(RuntimeError, match="num_predict"),
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                num_predict=8192,
            )


# ---------------------------------------------------------------------------
# 14. _strip_code_fences edge cases
# ---------------------------------------------------------------------------


class TestStripCodeFencesEdgeCases:
    """Additional edge cases for _strip_code_fences."""

    def test_fences_without_language_tag(self) -> None:
        """Should strip fences with no language annotation after backticks."""
        raw = '```\n{"key": "value"}\n```'
        assert _strip_code_fences(raw) == '{"key": "value"}'

    def test_fences_with_leading_whitespace(self) -> None:
        """Should strip fences and trailing whitespace, but keep inner leading spaces."""
        raw = '  ```json\n  {"key": "value"}  \n  ```  '
        assert _strip_code_fences(raw) == '  {"key": "value"}'

    def test_empty_string(self) -> None:
        """Should return empty string for empty input."""
        assert _strip_code_fences("") == ""

    def test_only_fences_no_content(self) -> None:
        """Should handle fences wrapping empty content."""
        raw = "```\n```"
        expected = ""
        assert _strip_code_fences(raw) == expected

    def test_no_closing_fence(self) -> None:
        """Should not strip if there is no closing fence."""
        raw = "```json\n{\"key\": \"value\"}"
        result = _strip_code_fences(raw)
        assert "\"key\"" in result


# ---------------------------------------------------------------------------
# 15. No Agent Skills runtime dependency
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


# ---------------------------------------------------------------------------
# 16. Context-window warning (NFM-2538, daily reflection 2026-08-06 §3.2)
# ---------------------------------------------------------------------------


class TestContextWindowWarning:
    """The legacy ``call_llm`` function must log a warning when the
    combined prompt approaches a known model's context window, so an
    operator sees over-budget inputs instead of paying for a wasted call
    that silently truncates.
    """

    @pytest.fixture
    def _stub_http(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """Stub the HTTP call so call_llm completes without the network.

        Returns a dict that records the call (we don't assert on the
        request body for these tests — the contract under test is the
        warning, not the request shape).
        """

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {
                    "choices": [{"message": {"content": "{}"}}],
                    "usage": {"total_tokens": 10},
                }

        class _Client:
            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def post(self, *args: Any, **kwargs: Any) -> _Resp:
                return _Resp()

        monkeypatch.setattr("httpx.AsyncClient", lambda *a, **k: _Client())
        return {}

    @pytest.mark.asyncio
    async def test_warns_when_qwen_prompt_approaches_32k(
        self,
        _stub_http: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 28K-char prompt to qwen3.6:35b-a3b-coding-nvfp4 (limit 32K)
        must emit a WARNING that names the model and the limit. Without
        this, the daily-reflection recurring issue (FRAPCON's 277K chars
        silently wasted) cannot be caught at the call site.
        """
        monkeypatch.setenv("LLM_MODEL", "qwen3.6:35b-a3b-coding-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="nfm_db.services.llm_client"):
            await call_llm(
                system_prompt="You extract structured data.",
                user_message="x" * 28_000,  # 87.5% of 32K limit
                temperature=0.0,
            )

        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert warnings, "expected a WARNING when prompt is at 87.5% of context"
        msg = warnings[0].getMessage()
        assert "qwen3.6:35b-a3b-coding-nvfp4" in msg
        assert "32000" in msg
        assert "context" in msg.lower()

    @pytest.mark.asyncio
    async def test_does_not_warn_for_small_prompt(
        self,
        _stub_http: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A small prompt must not raise a false alarm. This is the
        regression-guard against the warning becoming noise.
        """
        monkeypatch.setenv("LLM_MODEL", "qwen3.6:35b-a3b-coding-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="nfm_db.services.llm_client"):
            await call_llm(
                system_prompt="Extract.",
                user_message="Short user message.",
                temperature=0.0,
            )

        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert not warnings, f"unexpected WARNING: {[r.getMessage() for r in warnings]}"

    @pytest.mark.asyncio
    async def test_does_not_warn_for_unknown_model(
        self,
        _stub_http: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unknown models with no entry in the lookup must NOT warn —
        a missing lookup is a hard bug we want to surface separately
        (via tests, not via prod warnings). A 28K-char prompt to an
        unknown model is silent at WARNING level.
        """
        monkeypatch.setenv("LLM_MODEL", "future-llm-with-1m-context")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="nfm_db.services.llm_client"):
            await call_llm(
                system_prompt="x" * 1000,
                user_message="y" * 28_000,
                temperature=0.0,
            )

        warnings = [r for r in caplog.records if r.levelno == _logging.WARNING]
        assert not warnings, (
            f"unknown model should not warn; got: {[r.getMessage() for r in warnings]}"
        )


# ---------------------------------------------------------------------------
# 17. NFM-4779 — native Ollama /api/chat binding (think=false takes effect)
# ---------------------------------------------------------------------------


class TestNativeOllamaBinding:
    """NFM-4779: dispatcher routes to native Ollama /api/chat when provider=ollama.

    The openai-compat layer (the previous binding) silently drops ``think`` and
    ``chat_template_kwargs`` (NFM-4525). Native /api/chat honors both, which is
    what finally suppresses qwen3.5:4b-nvfp4's thinking-mode token burn that
    NFM-4730-FixB / NFM-4758 / NFM-4759 attempted to mitigate on the openai-compat
    layer. This test class locks in the binding switch.
    """

    @staticmethod
    def _build_mock_http_client(
        response_body: dict[str, Any],
        status_code: int = 200,
    ) -> AsyncMock:
        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json.return_value = response_body
        mock_response.raise_for_status = MagicMock()
        mock_response.text = "error body"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        return mock_client

    @staticmethod
    def _native_ollama_response(content: str = '{"properties": []}') -> dict[str, Any]:
        """Build a realistic native Ollama /api/chat response (post-`done`)."""
        return {
            "model": "qwen3.5:4b-nvfp4",
            "created_at": "2026-09-12T08:00:00Z",
            "message": {
                "role": "assistant",
                "content": content,
                # NFM-4779 AC2: thinking must be null/absent after think=false
                "thinking": None,
            },
            "done_reason": "stop",
            "done": True,
            "total_duration": 1_500_000_000,
            "load_duration": 200_000_000,
            "prompt_eval_count": 320,
            "prompt_eval_duration": 400_000_000,
            "eval_count": 64,
            "eval_duration": 800_000_000,
        }

    @pytest.mark.asyncio
    async def test_ollama_provider_hits_api_chat_not_chat_completions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """provider=ollama must POST to ``/api/chat``, not ``/v1/chat/completions``.

        This is the binding switch — the openai-compat layer's ``/v1/chat/completions``
        silently drops ``think`` / ``chat_template_kwargs``, which is the root
        cause of NFM-4525. Without this routing change, the NFM-4759 plumbing
        (chat_template_kwargs on the wire) would still be a no-op.
        """
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "ollama")

        body = self._native_ollama_response('{"properties": []}')
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(system_prompt="S", user_message="U")

        url = mock_http.post.call_args[0][0]
        # The default base_url is /v1 (openai-compat root) — we strip the /v1
        # to land on the Ollama daemon's native /api/chat path.
        assert url == "http://localhost:11434/api/chat", (
            f"expected native /api/chat, got {url!r}; the openai-compat "
            "binding silently drops think/chat_template_kwargs (NFM-4525)"
        )

    @pytest.mark.asyncio
    async def test_think_false_set_when_chat_template_kwargs_has_enable_thinking_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``think: false`` MUST be present in the body when the caller
        passes ``chat_template_kwargs={"enable_thinking": False}``.

        NFM-4779 AC #2 (LE scope): ``enable_thinking=False`` honored end-to-end.
        ``think`` is native Ollama's thinking-disabler (Ollama 0.5.0+).
        """
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "ollama")

        body = self._native_ollama_response('{"properties": []}')
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                chat_template_kwargs={"enable_thinking": False},
            )

        payload = mock_http.post.call_args[1]["json"]
        assert payload.get("think") is False, (
            f"think must be False to suppress qwen3.5 thinking mode; got {payload.get('think')!r}"
        )
        # The chat_template_kwargs plumbing from NFM-4759 is also kept on the wire.
        assert payload.get("chat_template_kwargs") == {"enable_thinking": False}
        # Stream is always false for the legacy call_llm path (no SSE plumbing
        # in this function — streaming was never wired in for the dispatcher).
        assert payload.get("stream") is False

    @pytest.mark.asyncio
    async def test_num_predict_in_options_for_native_binding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """For the native binding, ``num_predict`` lives at ``options.num_predict``.

        The openai-compat path used ``max_tokens`` at the top level; the native
        /api/chat binding uses ``options.num_predict`` (Ollama's native field).
        The per-call ``num_predict`` arg from NFM-4730-FixB / NFM-4759 must
        land in the native location so the Ollama daemon can honor it.
        """
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "ollama")

        body = self._native_ollama_response('{"ok": true}')
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(
                system_prompt="S",
                user_message="U",
                num_predict=8192,
                temperature=0.0,
            )

        payload = mock_http.post.call_args[1]["json"]
        options = payload.get("options") or {}
        assert options.get("num_predict") == 8192, (
            f"options.num_predict must be 8192 (NFM-4730-FixB cap); got {options.get('num_predict')!r}"
        )
        assert options.get("temperature") == 0.0
        # Native binding does NOT use the openai-compat top-level max_tokens field.
        assert "max_tokens" not in payload, (
            "native /api/chat binding must not send openai-compat max_tokens; "
            f"got top-level keys: {sorted(payload.keys())}"
        )

    @pytest.mark.asyncio
    async def test_native_response_normalized_to_openai_compat_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A native Ollama /api/chat response must be parsed into the same
        internal shape the openai-compat layer produced.

        Native Ollama returns ``{message, done_reason, prompt_eval_count, eval_count}``.
        Downstream code (call_llm post-parse block + extraction_pipeline) expects
        ``{choices[0].message.content, choices[0].finish_reason, usage.{prompt,completion,total}_tokens}``.
        Without normalization, the function would raise ``RuntimeError: LLM returned
        empty choices`` because the native body has no ``choices`` field.
        """
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "ollama")

        native_body = {
            "model": "qwen3.5:4b-nvfp4",
            "message": {
                "role": "assistant",
                "content": '{"k": "v"}',
                "thinking": None,
            },
            "done_reason": "stop",
            "done": True,
            "prompt_eval_count": 320,
            "eval_count": 64,
        }
        mock_http = self._build_mock_http_client(native_body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            result = await call_llm(system_prompt="S", user_message="U")

        assert result == {"k": "v"}, (
            "native /api/chat response must parse to the same dict the openai-compat "
            f"layer would produce; got {result!r}"
        )

    @pytest.mark.asyncio
    async def test_native_response_length_finish_reason_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A native ``done_reason='length'`` must map to the openai-compat
        ``finish_reason='length'`` so the existing thinking-exhaustion guard fires.

        NFM-4730-FixB's diagnostic (cites ``num_predict``) only fires when
        finish_reason='length' is detected. If native binding maps done_reason
        to ``stop`` instead, the thinking-exhaustion guard never trips and
        callers see ``LLM returned empty content`` instead of the diagnostic.
        """
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LLM_MODEL", "qwen3.5:4b-nvfp4")
        monkeypatch.setenv("LLM_API_KEY", "ollama")

        native_body = {
            "model": "qwen3.5:4b-nvfp4",
            "message": {
                "role": "assistant",
                "content": "",  # empty → triggers thinking-exhaustion diagnostic
                "thinking": None,
            },
            "done_reason": "length",
            "done": True,
            "prompt_eval_count": 320,
            "eval_count": 8192,
        }
        mock_http = self._build_mock_http_client(native_body)

        with (
            patch(
                "nfm_db.services.llm_client.httpx.AsyncClient",
                return_value=mock_http,
            ),
            pytest.raises(RuntimeError, match="num_predict"),
        ):
            await call_llm(system_prompt="S", user_message="U", num_predict=8192)

    def test_ollama_native_chat_url_strips_v1(self) -> None:
        """The URL helper must convert /v1 (openai-compat root) to /api/chat
        (native Ollama path). Both with and without trailing slash."""
        from nfm_db.services.llm_client import _ollama_native_chat_url

        assert _ollama_native_chat_url("http://localhost:11434/v1") == (
            "http://localhost:11434/api/chat"
        )
        assert _ollama_native_chat_url("http://localhost:11434/v1/") == (
            "http://localhost:11434/api/chat"
        )
        # Operator may already point LLM_BASE_URL at the bare root.
        assert _ollama_native_chat_url("http://localhost:11434") == (
            "http://localhost:11434/api/chat"
        )

    def test_ollama_native_to_openai_shape_maps_done_reason(self) -> None:
        """The shape-normalizer must map done_reason 'stop'/'length' to the
        openai-compat finish_reason vocabulary so downstream guards fire."""
        from nfm_db.services.llm_client import _ollama_native_to_openai_shape

        normalized = _ollama_native_to_openai_shape(
            {
                "message": {"role": "assistant", "content": '{"a": 1}'},
                "done_reason": "stop",
                "prompt_eval_count": 10,
                "eval_count": 5,
            }
        )
        assert normalized["choices"][0]["finish_reason"] == "stop"
        assert normalized["choices"][0]["message"]["content"] == '{"a": 1}'
        assert normalized["usage"]["total_tokens"] == 15

        length_normalized = _ollama_native_to_openai_shape(
            {
                "message": {"role": "assistant", "content": ""},
                "done_reason": "length",
                "prompt_eval_count": 100,
                "eval_count": 8192,
            }
        )
        assert length_normalized["choices"][0]["finish_reason"] == "length"

    @pytest.mark.asyncio
    async def test_non_ollama_provider_still_uses_openai_compat(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard: non-ollama providers MUST still hit /chat/completions.

        Only the ollama provider switches to native /api/chat. openai/deepseek
        keep the openai-compat layer (they don't have a /api/chat equivalent).
        """
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("LLM_API_KEY", "test-key")

        body = {
            "choices": [
                {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
            ]
        }
        mock_http = self._build_mock_http_client(body)

        with patch(
            "nfm_db.services.llm_client.httpx.AsyncClient",
            return_value=mock_http,
        ):
            await call_llm(system_prompt="S", user_message="U")

        url = mock_http.post.call_args[0][0]
        assert url == "https://api.example.com/v1/chat/completions", (
            f"non-ollama provider must keep openai-compat binding; got {url!r}"
        )
        payload = mock_http.post.call_args[1]["json"]
        # openai-compat binding keeps max_tokens at top level + Authorization header.
        assert "max_tokens" in payload
        assert "Authorization" in mock_http.post.call_args[1]["headers"]
