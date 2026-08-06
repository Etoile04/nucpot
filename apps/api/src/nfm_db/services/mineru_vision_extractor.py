# SPDX-License-Identifier: Apache-2.0
"""MinerU + VLM multimodal extraction pipeline.

Architecture (NFM-1366 follow-up):

    PDF → MinerU (layout analysis + image extraction) → markdown + images dict
        → Markdown parser maps image refs to figure numbers (Fig. 1, Fig. 2, ...)
        → For each matched (image, caption) pair:
              VLM (minicpm-v4.5:8b) extracts structured data → verify accuracy
        → Conflict resolution against text-extracted properties

Why this replaces PageSplitter + FigureDetector:
    - PageSplitter renders the whole PDF page (1700x2200) and asks VLM for bboxes
      → 30% of bboxes are invalid (text-mixed coords), 50% JSON parse fails.
    - MinerU does layout analysis on the server side and returns
      pre-cropped images + markdown with `![]()` references linking them.
    - VLM only needs to read each small image and extract structured data
      (axes, series, table rows) — no bbox detection, no full-page analysis.
    - Result: ~50% of Landa 2011 figures extracted with HIGH accuracy,
      vs. ~0% in the previous PageSplitter-based pipeline.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compact JSON schema — minimize num_predict truncation risk
# ---------------------------------------------------------------------------

# Schema forces VLM to keep responses short. Wide schemas cause the model
# to ramble and hit num_predict limits, leaving malformed JSON.
_EXTRACT_PROMPT = """Analyze this scientific figure/table image. Output ONLY this JSON, no markdown fences:

For PLOTS: {"type":"plot","title":"...","x":"label (unit)","y":"label (unit)","series":["name1","name2"]}
For TABLES: {"type":"table","title":"...","headers":["c1","c2"],"rows":[["v1","v2"]]}
For MICROGRAPHS/DIAGRAMS: {"type":"micrograph","title":"...","description":"..."}

Keep strings under 80 chars. NO prose, NO markdown."""

_VERIFY_PROMPT = """Extracted data from this image:
{extracted}

Rate the extraction accuracy. Reply ONLY this JSON, no markdown:
{{"accuracy":"high|medium|low","issues":["issue1"]}}

Use "high" only if axes, units, and series names match the image.
"medium" if labels are roughly correct but with minor errors.
"low" if the extraction missed key elements or got them wrong."""


# ---------------------------------------------------------------------------
# Markdown → image references
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FigureRef:
    """A figure/table referenced in markdown with its extracted image bytes.

    Attributes:
        image_bytes: Raw image bytes (JPEG/PNG) extracted by MinerU.
        image_ref: Markdown image reference like ``images/abc123.jpg``.
        figure_numbers: Figure/table numbers in the surrounding text
            (e.g. ``['1', '2']`` for "Fig. 1 and Fig. 2").
        kind: Either ``"figure"``, ``"table"``, or ``"unknown"``. Inferred
            from the figure_numbers (Fig.X → figure, 表X → table).
        markdown_caption: Up to 200 chars of surrounding markdown context
            (figure caption + adjacent text). Useful for downstream prompts.
        page_hint: ``None`` currently — MinerU doesn't embed page numbers in
            image references; left as a placeholder for future layout.json
            cross-reference.
    """

    image_bytes: bytes
    image_ref: str
    figure_numbers: list[str] = field(default_factory=list)
    kind: str = "unknown"
    markdown_caption: str = ""
    page_hint: int | None = None


# Match figure/table number references in markdown captions:
#   - English: "Figure 1", "Fig. 1", "Fig 1"
#   - Chinese: "图 1"
# We anchor on either an image reference (`![..](images/..jpg)`) appearing
# within the same paragraph OR the figure number appearing nearby.
_FIG_NUM_RE = re.compile(r"(?:Fig(?:ure)?\.?|图)\s*(\d+[a-z]?)", re.IGNORECASE)
_TBL_NUM_RE = re.compile(r"Table\s*(\d+[a-z]?)|表\s*(\d+[a-z]?)", re.IGNORECASE)
_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def parse_figure_refs(markdown: str, images: dict[str, bytes]) -> list[FigureRef]:
    """Map markdown image references to FigureRef objects with figure numbers.

    Args:
        markdown: MinerU's full.md content (with ``images/...jpg`` refs).
        images: Mapping of image filename → bytes from MinerU's zip.

    Returns:
        One FigureRef per image found in the markdown, with associated
        figure/table numbers inferred from the same paragraph OR the next
        paragraph (figures usually have their caption immediately after
        the image, separated by a blank line).
    """
    refs: list[FigureRef] = []
    seen_images: set[str] = set()

    # Split into paragraphs once. We process each paragraph, but look at the
    # *next* paragraph as well to catch caption text that follows the image
    # on the next line.
    paragraphs = markdown.split("\n\n")

    for i, para in enumerate(paragraphs):
        img_match = _IMG_REF_RE.search(para)
        if not img_match:
            continue

        image_ref = img_match.group(2)
        image_name = image_ref.split("/")[-1]

        # Skip duplicates
        if image_name in seen_images:
            continue
        seen_images.add(image_name)

        image_bytes = images.get(image_name) or images.get(image_ref)
        if image_bytes is None:
            logger.warning("parse_figure_refs: no bytes for image %s", image_ref)
            continue

        # Combine this paragraph + next paragraph for figure-number search.
        # Caption text typically appears in the line directly after the image.
        next_para = paragraphs[i + 1] if i + 1 < len(paragraphs) else ""
        combined = para + "\n\n" + next_para

        fig_nums = _FIG_NUM_RE.findall(combined)
        tbl_nums_match = _TBL_NUM_RE.findall(combined)
        tbl_nums = [m[0] or m[1] for m in tbl_nums_match if m[0] or m[1]]

        # Prefer numbers from the caption paragraph (the one after the image)
        # since that's where "Figure 1:" usually appears.
        caption_text = next_para if next_para.strip() else para
        caption_nums_fig = _FIG_NUM_RE.findall(caption_text)
        caption_nums_tbl_match = _TBL_NUM_RE.findall(caption_text)
        caption_nums_tbl = [
            m[0] or m[1] for m in caption_nums_tbl_match if m[0] or m[1]
        ]

        if caption_nums_tbl and not caption_nums_fig:
            kind = "table"
            numbers = caption_nums_tbl
        elif caption_nums_fig:
            kind = "figure"
            numbers = caption_nums_fig
        elif tbl_nums and not fig_nums:
            kind = "table"
            numbers = tbl_nums
        elif fig_nums:
            kind = "figure"
            numbers = fig_nums
        else:
            kind = "unknown"
            numbers = []

        caption = " ".join(caption_text.split())[:200]

        refs.append(
            FigureRef(
                image_bytes=image_bytes,
                image_ref=image_ref,
                figure_numbers=numbers,
                kind=kind,
                markdown_caption=caption,
            )
        )

    return refs


# ---------------------------------------------------------------------------
# Robust JSON recovery for VLM output
# ---------------------------------------------------------------------------


def parse_vlm_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from VLM output with multi-strategy recovery.

    minicpm-v4.5:8b frequently outputs JSON with:
      - Trailing prose after the JSON object
      - Code fences (``json ... ``)
      - Truncated output (num_predict limit)
      - Malformed bbox arrays with prose inside

    Returns None if no usable JSON object can be extracted.
    """
    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: extract first balanced {...}
    m = re.search(r"\{[^{}]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # Strategy 4: greedy balanced parse, try each } from end
    start = text.find("{")
    if start >= 0:
        for end in range(len(text), start + 10, -1):
            if text[end - 1] == "}":
                try:
                    obj = json.loads(text[start:end])
                    # Look for any expected key
                    if any(k in obj for k in ("type", "accuracy", "title", "rows", "headers", "series")):
                        return obj
                except json.JSONDecodeError:
                    continue

    return None


# ---------------------------------------------------------------------------
# Image resize for VLM
# ---------------------------------------------------------------------------


def resize_for_vlm(image_bytes: bytes, max_dim: int = 1024) -> bytes:
    """Resize image so the longest side is at most max_dim pixels.

    Returns JPEG bytes (quality=85). Smaller payloads cut VLM latency
    and reduce the chance of output truncation.
    """
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def image_to_base64_jpeg(image_bytes: bytes, max_dim: int = 1024) -> str:
    """Resize and base64-encode an image for the VLM API call."""
    return base64.b64encode(resize_for_vlm(image_bytes, max_dim)).decode()


# ---------------------------------------------------------------------------
# VLM calls (extract + verify)
# ---------------------------------------------------------------------------


async def vlm_extract(
    client: Any,
    image_bytes: bytes,
    *,
    timeout: float = 180.0,
) -> tuple[dict[str, Any] | None, float, int]:
    """Call VLM to extract structured data from one image.

    Args:
        client: An OpenAI-compatible VLM client (httpx or openai SDK).
            Must expose ``async def chat.completions.create(**kwargs)``.
        image_bytes: Raw image bytes (any format PIL can read).
        timeout: HTTP timeout in seconds.

    Returns:
        (extracted_dict, elapsed_seconds, tokens_used). ``extracted_dict``
        may be None if JSON parsing failed after all recovery strategies.
    """
    img_b64 = image_to_base64_jpeg(image_bytes)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _EXTRACT_PROMPT},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                },
            ],
        }
    ]

    start = time.monotonic()
    try:
        # Try OpenAI-style client first (callable expects max_tokens kwarg)
        response = await client.chat.completions.create(
            model=_get_model_name(),
            messages=messages,
            max_tokens=1500,
            temperature=0.0,
            timeout=timeout,
        )
        content = response.choices[0].message.content
        tokens = response.usage.total_tokens if response.usage else 0
    except (AttributeError, TypeError):
        # Fall back to a plain function-style call: client(messages, max_tokens, timeout)
        # — used by our _vlm_call adapter which only takes messages + timeout.
        content = await client(messages, timeout=timeout)
        tokens = 0

    elapsed = time.monotonic() - start
    parsed = parse_vlm_json(content)
    return parsed, elapsed, tokens


async def vlm_verify(
    client: Any,
    image_bytes: bytes,
    extracted: dict[str, Any],
    *,
    timeout: float = 120.0,
) -> tuple[dict[str, Any] | None, float]:
    """Re-prompt VLM with the extracted data to verify accuracy.

    Returns (verification_dict, elapsed_seconds). The verification dict
    has keys ``accuracy`` (high/medium/low) and ``issues`` (list of strings).
    """
    img_b64 = image_to_base64_jpeg(image_bytes)
    extracted_str = json.dumps(extracted, ensure_ascii=False)

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _VERIFY_PROMPT.format(extracted=extracted_str),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                },
            ],
        }
    ]

    start = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=_get_model_name(),
            messages=messages,
            max_tokens=500,
            temperature=0.0,
            timeout=timeout,
        )
        content = response.choices[0].message.content
    except (AttributeError, TypeError):
        content = await client(messages, timeout=timeout)

    elapsed = time.monotonic() - start
    parsed = parse_vlm_json(content)
    return parsed, elapsed


def _get_model_name() -> str:
    """Read VLM_MODEL from env (set by docker-compose.prod.yml)."""
    import os

    return os.environ.get("VLM_MODEL", "minicpm-v4.5:8b")


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """Result of extracting structured data from one image.

    Attributes:
        figure_ref: The original FigureRef (image + caption).
        extracted: Structured data dict from VLM (or None on parse failure).
        verification: Accuracy assessment dict (or None on parse failure).
        extract_elapsed: Time spent on the extract call.
        verify_elapsed: Time spent on the verify call.
        extract_tokens: Tokens used in the extract call.
    """

    figure_ref: FigureRef
    extracted: dict[str, Any] | None
    verification: dict[str, Any] | None
    extract_elapsed: float = 0.0
    verify_elapsed: float = 0.0
    extract_tokens: int = 0


async def extract_figures_with_mineru(
    pdf_bytes: bytes,
    vlm_client: Any,
    *,
    mineru_client: Any | None = None,
    api_key: str | None = None,
    max_images: int = 20,
) -> list[ExtractionResult]:
    """Full MinerU + VLM extraction pipeline for one PDF.

    Args:
        pdf_bytes: Raw PDF bytes.
        vlm_client: OpenAI-compatible VLM client.
        mineru_client: Optional pre-configured MinerUClient. If None, a new
            one is created from api_key + MINERU_API_KEY env var.
        api_key: MinerU API key. Falls back to MINERU_API_KEY env var.
        max_images: Cap on images to process per PDF (cost control).

    Returns:
        One ExtractionResult per figure/table image found. ``extracted`` and
        ``verification`` may be None if the VLM call failed.

    Raises:
        RuntimeError: If MinerU parsing fails entirely (callers should
            treat this as a soft error and fall back to PyMuPDF + VLM).
    """
    from nfm_db.services.mineru_client import (
        MinerUClient,
    )

    if mineru_client is None:
        key = api_key or _get_mineru_api_key()
        if not key:
            raise RuntimeError("MINERU_API_KEY not set")
        mineru_client = MinerUClient(api_key=key)

    # Step 1: MinerU layout analysis
    logger.info("extract_figures_with_mineru: starting MinerU parse")
    mineru_result = await mineru_client.parse_pdf(pdf_bytes, return_zip=True)
    if not mineru_result.zip_bytes:
        raise RuntimeError("MinerU returned no zip")

    assets = MinerUClient.parse_zip_assets(mineru_result.zip_bytes)
    logger.info(
        "extract_figures_with_mineru: MinerU extracted %d images from %d-char markdown",
        len(assets.images),
        len(assets.markdown),
    )

    # Step 2: Parse markdown → figure references
    refs = parse_figure_refs(assets.markdown, assets.images)
    logger.info(
        "extract_figures_with_mineru: %d figures mapped to images", len(refs)
    )

    # Step 3: For each figure, VLM extract + verify
    results: list[ExtractionResult] = []
    for ref in refs[:max_images]:
        try:
            extracted, e_elapsed, e_tokens = await vlm_extract(
                vlm_client, ref.image_bytes
            )
            verification = None
            v_elapsed = 0.0
            if extracted is not None:
                verification, v_elapsed = await vlm_verify(
                    vlm_client, ref.image_bytes, extracted
                )
            results.append(
                ExtractionResult(
                    figure_ref=ref,
                    extracted=extracted,
                    verification=verification,
                    extract_elapsed=e_elapsed,
                    verify_elapsed=v_elapsed,
                    extract_tokens=e_tokens,
                )
            )
        except Exception as exc:
            logger.warning(
                "extract_figures_with_mineru: VLM failed for %s: %s",
                ref.image_ref,
                exc,
            )
            results.append(
                ExtractionResult(
                    figure_ref=ref, extracted=None, verification=None
                )
            )

    return results


def _get_mineru_api_key() -> str | None:
    """Read MINERU_API_KEY from environment."""
    import os

    return os.environ.get("MINERU_API_KEY")


# ---------------------------------------------------------------------------
# Conversion to the existing job.figures / job.tables schema
# ---------------------------------------------------------------------------


def to_job_figure(result: ExtractionResult, source_reference: str) -> dict[str, Any] | None:
    """Convert one ExtractionResult to the dict schema expected by job.figures.

    Returns None if extraction failed (so the caller can skip without
    polluting the job with empty entries).
    """
    if result.extracted is None:
        return None

    ext = result.extracted
    accuracy = (result.verification or {}).get("accuracy", "unknown")
    confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(accuracy, 0.5)

    return {
        "figure_type": ext.get("type", "unknown"),
        "title": ext.get("title", "")[:200],
        "x_axis": ext.get("x", ""),
        "y_axis": ext.get("y", ""),
        "series": ext.get("series", []),
        "headers": ext.get("headers", []),
        "rows": ext.get("rows", []),
        "description": ext.get("description", ""),
        "source": source_reference,
        "image_ref": result.figure_ref.image_ref,
        "figure_numbers": result.figure_ref.figure_numbers,
        "kind": result.figure_ref.kind,
        "confidence": confidence,
        "accuracy": accuracy,
        "issues": (result.verification or {}).get("issues", []),
        "provider": "ollama",
        "model": _get_model_name(),
        "extraction_time_ms": int(result.extract_elapsed * 1000),
        "verification_time_ms": int(result.verify_elapsed * 1000),
        "extraction_method": "mineru_vlm",
    }


__all__ = [
    "ExtractionResult",
    "FigureRef",
    "extract_figures_with_mineru",
    "image_to_base64_jpeg",
    "parse_figure_refs",
    "parse_vlm_json",
    "resize_for_vlm",
    "to_job_figure",
    "vlm_extract",
    "vlm_verify",
]
