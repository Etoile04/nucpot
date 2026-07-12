"""VLM vision client service for figure content extraction (NFM-851).

Provides:
- ``VisionClient`` class with provider abstraction (OpenAI, Ollama, local)
- Base64 image encoding for multimodal API calls
- Prompt templates for plot/chart and table extraction
- Retry with exponential backoff on transient errors

Configuration via environment variables:
  VLM_PROVIDER  - provider identifier (default: "openai")
  VLM_MODEL     - model name (default: "gpt-4o")
  VLM_API_KEY   - API key (required for remote providers)
  VLM_BASE_URL  - API base URL (optional, provider-specific default)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from enum import StrEnum
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 120.0  # VLM calls are slower due to image payloads
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0

_PROVIDER_DEFAULTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "local": "http://localhost:11434/v1",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class VisionClientError(Exception):
    """Raised when VLM extraction fails."""


# ---------------------------------------------------------------------------
# Provider enum
# ---------------------------------------------------------------------------


class VisionProvider(StrEnum):
    """Supported VLM providers."""

    OPENAI = "openai"
    OLLAMA = "ollama"
    LOCAL = "local"


# ---------------------------------------------------------------------------
# Image encoding
# ---------------------------------------------------------------------------


def encode_image_base64(image_data: bytes) -> str:
    """Encode raw image bytes to a base64 string for API transmission.

    Args:
        image_data: Raw image bytes (PNG, JPEG, etc.).

    Returns:
        Base64-encoded string of the image data.
    """
    return base64.b64encode(image_data).decode("ascii")


def _detect_mime_type(image_data: bytes) -> str:
    """Detect MIME type from image bytes header.

    Falls back to image/png for unknown formats.
    """
    if image_data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if image_data[:4] == b"GIF8":
        return "image/gif"
    if image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


def build_plot_extraction_prompt() -> str:
    """Build the system/user prompt for plot/chart data extraction.

    Returns a detailed prompt instructing the VLM to extract structured
    data from scientific plot images, including axes, series, and legend.
    """
    return (
        "You are a scientific data extraction assistant specializing in "
        "nuclear materials research plots and charts.\n\n"
        "Analyze the provided plot image and extract structured data:\n"
        "1. Title and plot type (line, scatter, bar, heatmap, contour)\n"
        "2. X-axis: label, unit, scale type (linear/log), tick values\n"
        "3. Y-axis: label, unit, scale type (linear/log), tick values\n"
        "4. Y2-axis (if present): label, unit, scale, tick values\n"
        "5. Data series: name, y-values at each x position, color, marker style\n"
        "6. Legend entries (verbatim text)\n"
        "7. Text annotations within the plot area\n"
        "8. Your confidence in the extraction (0.0-1.0)\n\n"
        "Important:\n"
        "- Extract numeric values exactly as shown on axes\n"
        "- If axis values are not visible, interpolate from tick marks\n"
        "- For log-scale axes, note the scale type explicitly\n"
        "- Respond ONLY with valid JSON matching the expected schema\n"
        "- No markdown, no explanation — raw JSON only"
    )


def build_table_extraction_prompt() -> str:
    """Build the system/user prompt for table structure extraction.

    Returns a detailed prompt instructing the VLM to extract structured
    data from scientific table images, including headers, rows, and cells.
    """
    return (
        "You are a scientific data extraction assistant specializing in "
        "nuclear materials research tables.\n\n"
        "Analyze the provided table image and extract structured data:\n"
        "1. Table title or caption\n"
        "2. Column headers (exact text, preserving order)\n"
        "3. Sub-headers if the table uses multi-row headers\n"
        "4. All data rows with cell values (exact text)\n"
        "5. Merged cells (row_span, col_span) if detected\n"
        "6. Footnotes or notes below the table\n"
        "7. Total column count and data row count\n"
        "8. Your confidence in the extraction (0.0-1.0)\n\n"
        "Important:\n"
        "- Preserve exact cell text including units, symbols, and superscripts\n"
        "- Mark merged cells with appropriate row_span and col_span\n"
        "- If a cell spans multiple columns, repeat the value or mark the span\n"
        "- Respond ONLY with valid JSON matching the expected schema\n"
        "- No markdown, no explanation — raw JSON only"
    )


# ---------------------------------------------------------------------------
# Configuration check
# ---------------------------------------------------------------------------


def is_vlm_configured() -> bool:
    """Check if VLM API key is configured in environment."""
    return bool(os.environ.get("VLM_API_KEY"))


# ---------------------------------------------------------------------------
# VisionClient
# ---------------------------------------------------------------------------


class VisionClient:
    """Async VLM client with provider abstraction, retry, and image support.

    Supports OpenAI-compatible vision APIs (GPT-4o, Ollama, etc.)
    that accept base64-encoded images in the messages payload.

    Usage::

        client = VisionClient()
        result = await client.extract(
            image_data=png_bytes,
            prompt="Extract the plot data.",
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
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        self.provider = provider or os.environ.get("VLM_PROVIDER", "openai")
        self.model = model or os.environ.get("VLM_MODEL", "gpt-4o")
        self.api_key = api_key or os.environ.get("VLM_API_KEY", "")
        self.base_url = base_url or os.environ.get(
            "VLM_BASE_URL",
            _PROVIDER_DEFAULTS.get(self.provider, "https://api.openai.com/v1"),
        )
        self.timeout = timeout
        self.max_retries = max_retries

        if not self.api_key and self.provider == "openai":
            raise ValueError(
                "VLM_API_KEY is required for remote VLM providers but was not set"
            )

    async def extract(
        self,
        *,
        image_data: bytes,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Send an image + prompt to the VLM and return structured JSON data.

        Args:
            image_data: Raw image bytes (PNG, JPEG, etc.).
            prompt: The user prompt describing what to extract.
            system_prompt: Optional system prompt. Uses plot/table defaults if None.
            temperature: Sampling temperature (0 = deterministic).
            max_tokens: Maximum tokens in the response.

        Returns:
            Parsed dict from the VLM response.

        Raises:
            VisionClientError: If response is not valid JSON or is empty.
            httpx.HTTPStatusError: After exhausting retries on server errors.
        """
        effective_system = system_prompt or build_plot_extraction_prompt()
        image_b64 = encode_image_base64(image_data)
        mime_type = _detect_mime_type(image_data)

        response = await self._call_with_retry(
            prompt=prompt,
            system_prompt=effective_system,
            image_base64=image_b64,
            mime_type=mime_type,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        raw_content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not raw_content:
            raise VisionClientError("VLM returned empty content")

        cleaned = _strip_code_fences(raw_content)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise VisionClientError(
                f"VLM returned invalid JSON: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise VisionClientError(
                f"VLM returned non-dict JSON: {type(data).__name__}"
            )

        return data

    async def _call_with_retry(
        self,
        *,
        prompt: str,
        system_prompt: str,
        image_base64: str,
        mime_type: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Call the VLM provider with retry and exponential backoff."""
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._call_provider(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    image_base64=image_base64,
                    mime_type=mime_type,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    raise VisionClientError(
                        f"VLM request failed after {attempt} retries: {exc}"
                    ) from exc
                backoff = _BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "VLM request error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    self.max_retries,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                is_retryable = (
                    exc.response.status_code >= 500
                    or exc.response.status_code == 429
                )
                if not is_retryable or attempt == self.max_retries:
                    raise VisionClientError(
                        f"VLM HTTP error {exc.response.status_code}: {exc}"
                    ) from exc
                backoff = _BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    "VLM call failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt,
                    self.max_retries,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)

        raise last_exc  # type: ignore[misc]

    async def _call_provider(
        self,
        *,
        prompt: str,
        system_prompt: str,
        image_base64: str,
        mime_type: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Execute the HTTP call to the VLM provider.

        Sends a multimodal message with the image as a base64 data URL
        in the user message content.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        image_url = f"data:{mime_type};base64,{image_base64}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                        },
                    ],
                },
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
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
        elapsed_ms = (time.monotonic() - start) * 1000

        logger.info(
            "VLM call completed: model=%s, latency=%.0fms, tokens=%s",
            self.model,
            elapsed_ms,
            body.get("usage", {}).get("total_tokens", "N/A"),
        )

        return cast(dict[str, Any], body)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from VLM response content."""
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
    return stripped
