"""Reusable LLM client service for NFMD structured output extraction (NFM-540).

Provides:
- ``LLMClient`` class with caching, retry, schema validation, structured output
- ``LLMResponse`` frozen dataclass for response metadata
- Legacy ``call_llm`` and ``is_llm_configured`` retained for backward compat

Configuration via environment variables:
  LLM_PROVIDER  - provider identifier (default: "openai")
  LLM_MODEL     - model name (default: "gpt-4o-mini")
  LLM_API_KEY   - API key (required)
  LLM_BASE_URL  - API base URL (optional, provider-specific default)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 60.0
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0  # seconds, doubles each retry
_FIXED_SEED = 42  # fixed seed for reproducibility

_PROVIDER_DEFAULTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "ollama": "http://localhost:11434/v1",
}

_DEFAULT_SYSTEM_PROMPT = (
    "You are a structured data extraction assistant. "
    "Respond ONLY with valid JSON matching the requested schema. "
    "No markdown, no explanation — raw JSON only."
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResponse:
    """Structured result from an LLM call."""

    content: dict[str, Any]
    usage: dict[str, Any]
    latency_ms: float


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def _compute_cache_key(
    prompt: str,
    system_prompt: str,
    model: str,
    schema: dict[str, Any] | None = None,
) -> str:
    """Compute a deterministic cache key from prompt components and model."""
    parts = [system_prompt, prompt, model]
    if schema is not None:
        parts.append(json.dumps(schema, sort_keys=True))
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Schema validation (lightweight, covers NFMD subset)
# ---------------------------------------------------------------------------


def _validate_json_schema(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    """Validate *data* against a JSON schema.

    Covers the subset of JSON Schema used in NFMD: type, required, properties.
    Raises ``ValueError`` on mismatch.
    """
    schema_type = schema.get("type")
    if schema_type == "object" and not isinstance(data, dict):
        raise ValueError(
            f"Schema validation failed: expected object, got {type(data).__name__}"
        )

    required = schema.get("required", [])
    for field in required:
        if field not in data:
            raise ValueError(
                f"Schema validation failed: missing required field '{field}'"
            )

    properties = schema.get("properties", {})
    for key, prop_schema in properties.items():
        if key in data:
            expected_type = prop_schema.get("type")
            if expected_type == "string" and not isinstance(data[key], str):
                raise ValueError(
                    f"Schema validation failed: field '{key}' expected string, "
                    f"got {type(data[key]).__name__}"
                )
            if expected_type == "number" and not isinstance(data[key], (int, float)):
                raise ValueError(
                    f"Schema validation failed: field '{key}' expected number, "
                    f"got {type(data[key]).__name__}"
                )


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class LLMClient:
    """Async LLM client with caching, retry, and structured output extraction.

    Usage::

        client = LLMClient()  # reads config from env vars
        result = await client.extract_structured(
            prompt="Extract the element name and atomic number.",
            schema={"type": "object", "required": ["element", "number"],
                    "properties": {"element": {"type": "string"},
                                   "number": {"type": "number"}}},
        )
    """

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        seed: int = _FIXED_SEED,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self.provider = provider or os.environ.get("LLM_PROVIDER", "openai")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "LLM_BASE_URL",
            _PROVIDER_DEFAULTS.get(self.provider, "https://api.openai.com/v1"),
        )
        self.timeout = timeout
        self.seed = seed
        self.max_retries = max_retries

        if not self.api_key:
            raise ValueError("LLM_API_KEY is required but was not set")

        self._cache: dict[str, dict[str, Any]] = {}

    async def extract_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """Send a prompt to the LLM and return structured JSON data.

        Args:
            prompt: The user prompt for extraction.
            schema: JSON schema to validate the response against.
            system_prompt: Optional system prompt. Defaults to extraction role.

        Returns:
            Parsed dict from the LLM response.

        Raises:
            ValueError: If the response is not valid JSON or fails schema validation.
            httpx.HTTPStatusError: After exhausting retries on server errors.
        """
        effective_system = system_prompt or _DEFAULT_SYSTEM_PROMPT
        cache_key = _compute_cache_key(prompt, effective_system, self.model, schema)

        # Check cache
        if cache_key in self._cache:
            logger.info("Cache hit for prompt hash %s", cache_key[:12])
            return self._cache[cache_key]

        # Call provider with retry
        response = await self._call_with_retry(
            prompt=prompt,
            system_prompt=effective_system,
            temperature=0,
            seed=self.seed,
        )

        # Parse content
        raw_content = response["choices"][0]["message"]["content"]
        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"LLM returned non-dict JSON: {type(data).__name__}"
            )

        # Schema validation
        _validate_json_schema(data, schema)

        # Cache and log
        self._cache[cache_key] = data
        usage = response.get("usage", {})
        logger.info(
            "LLM call completed: prompt_hash=%s, tokens=%s, latency=%.1fms",
            cache_key[:12],
            usage.get("total_tokens", "N/A"),
            response.get("_latency_ms", "N/A"),
        )

        return data

    async def _call_with_retry(
        self,
        *,
        prompt: str,
        system_prompt: str,
        temperature: float,
        seed: int,
    ) -> dict[str, Any]:
        """Call the LLM provider with retry and exponential backoff."""
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._call_provider(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    seed=seed,
                )
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                is_retryable = (
                    exc.response.status_code >= 500
                    or exc.response.status_code == 429
                )
                if not is_retryable or attempt == self.max_retries:
                    raise
                backoff = _BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    self.max_retries,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)

        # Should not reach here, but satisfy the type checker
        raise last_exc  # type: ignore[misc]

    async def _call_provider(
        self,
        *,
        prompt: str,
        system_prompt: str,
        temperature: float,
        seed: int,
    ) -> dict[str, Any]:
        """Execute the actual HTTP call to the LLM provider.

        Returns the parsed JSON response body.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "seed": seed,
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=self.timeout) as http_client:
            response = await http_client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )

        response.raise_for_status()
        body = response.json()
        return {**body, "_latency_ms": (time.monotonic() - start) * 1000}


# ---------------------------------------------------------------------------
# Legacy API (backward compatibility)
# ---------------------------------------------------------------------------


def _get_config() -> dict[str, str]:
    """Read LLM configuration from environment variables."""
    return {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        "model": os.environ.get("LLM_MODEL", "gpt-4o"),
    }


async def call_llm(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    config: dict[str, str] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Send a chat completion request and parse the JSON response (legacy API)."""
    cfg = config or _get_config()

    if not cfg["api_key"]:
        raise ValueError("LLM_API_KEY environment variable is not set")

    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    logger.info(
        "LLM request: model=%s, system_prompt_len=%d, user_message_len=%d",
        cfg["model"],
        len(system_prompt),
        len(user_message),
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("LLM HTTP error: %s %s", exc.response.status_code, exc.response.text)
        raise RuntimeError(f"LLM API returned {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.error("LLM request error: %s", exc)
        raise RuntimeError(f"LLM request failed: {exc}") from exc

    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM response is not valid JSON: {exc}") from exc

    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError("LLM returned empty choices")

    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("LLM returned empty content")

    cleaned = _strip_code_fences(content)

    try:
        result: dict[str, Any] | list[dict[str, Any]] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("LLM response content is not valid JSON: %s", cleaned[:500])
        raise RuntimeError(f"LLM response content is not valid JSON: {exc}") from exc

    logger.info("LLM response parsed successfully (type=%s)", type(result).__name__)
    return result


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from LLM response content."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    return stripped


def is_llm_configured() -> bool:
    """Check if LLM API key is configured in environment."""
    return bool(os.environ.get("LLM_API_KEY"))
