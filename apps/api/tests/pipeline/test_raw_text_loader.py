"""Unit tests for the RawTextLoader extraction step — NFM-2681.

Covers:

- Protocol conformance (satisfies :class:`ExtractionStep`).
- BOM stripping from UTF-8 encoded text.
- Line-ending normalisation (``\\r\\n``, ``\\r``, mixed).
- Empty / whitespace-only document handling.
- Idempotency — two calls on identical input yield identical output.
- Metadata: ``chunk_type``, ``step_order``, ``source_span``, ``document_id``.
- No coupling to ``trigger_extraction()`` — uses only new Pipeline types.

See Also
--------
- :mod:`nfm_db.pipeline.extraction_step` — the Protocol definition.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from nfm_db.pipeline.extraction_step import (
    ExtractionStep,
    StepContext,
    is_extraction_step,
)
from nfm_db.pipeline.steps.raw_text_loader import RawTextLoader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_JOB_ID = uuid.uuid4()


def _make_context(raw_content: str = "", document_id: str | None = None) -> StepContext:
    """Build a StepContext pre-loaded with raw document text."""
    values: dict[str, Any] = {"raw_content": raw_content}
    if document_id is not None:
        values["document_id"] = document_id
    return StepContext(job_id=_JOB_ID, values=values)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRawTextLoaderProtocolConformance:
    """RawTextLoader must structurally satisfy the ExtractionStep Protocol."""

    def test_satisfies_extraction_step_protocol(self) -> None:
        loader = RawTextLoader()
        assert is_extraction_step(loader) is True
        assert isinstance(loader, ExtractionStep)

    def test_step_type_is_chunk(self) -> None:
        loader = RawTextLoader()
        assert loader.step_type == "chunk"

    def test_input_keys_includes_raw_content(self) -> None:
        loader = RawTextLoader()
        assert "raw_content" in loader.input_keys


# ---------------------------------------------------------------------------
# BOM stripping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBOMStripping:
    """RawTextLoader strips the UTF-8 BOM (U+FEFF) from the start of text."""

    @pytest.mark.asyncio
    async def test_bom_is_stripped(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="﻿Hello world")
        result = await loader.execute(ctx)

        raw_text = result.outputs["raw_text"]
        assert not raw_text.startswith("﻿")
        assert raw_text == "Hello world"

    @pytest.mark.asyncio
    async def test_bom_not_stripped_when_mid_content(self) -> None:
        """BOM only stripped at the very start, not if it appears mid-text."""
        loader = RawTextLoader()
        ctx = _make_context(raw_content="Hello﻿world")
        result = await loader.execute(ctx)

        assert result.outputs["raw_text"] == "Hello﻿world"


# ---------------------------------------------------------------------------
# Line-ending normalisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLineEndingNormalisation:
    """All line endings are normalised to ``\\n``."""

    @pytest.mark.asyncio
    async def test_crlf_to_lf(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="line one\r\nline two\r\n")
        result = await loader.execute(ctx)

        assert "\r" not in result.outputs["raw_text"]
        # Trailing newline preserved — it is a line terminator, not trailing whitespace.
        assert result.outputs["raw_text"] == "line one\nline two\n"

    @pytest.mark.asyncio
    async def test_cr_to_lf(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="line one\rline two\r")
        result = await loader.execute(ctx)

        assert "\r" not in result.outputs["raw_text"]
        # Trailing newline preserved — it is a line terminator, not trailing whitespace.
        assert result.outputs["raw_text"] == "line one\nline two\n"

    @pytest.mark.asyncio
    async def test_mixed_endings(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="a\r\nb\rc\nd")
        result = await loader.execute(ctx)

        assert "\r" not in result.outputs["raw_text"]
        assert result.outputs["raw_text"] == "a\nb\nc\nd"

    @pytest.mark.asyncio
    async def test_trailing_whitespace_per_line_stripped(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="hello   \nworld\t\nend")
        result = await loader.execute(ctx)

        assert result.outputs["raw_text"] == "hello\nworld\nend"


# ---------------------------------------------------------------------------
# Empty / whitespace-only document
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyDocument:
    """Empty and whitespace-only inputs produce empty normalised text."""

    @pytest.mark.asyncio
    async def test_empty_string(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="")
        result = await loader.execute(ctx)

        assert result.outputs["raw_text"] == ""
        assert result.outputs["metadata"]["source_span"] == (0, 0)

    @pytest.mark.asyncio
    async def test_whitespace_only(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="   \n\t  \n  ")
        result = await loader.execute(ctx)

        # After stripping trailing whitespace per line, whitespace-only
        # lines become empty, but the text is not fully collapsed.
        expected = "\n\n"
        assert result.outputs["raw_text"] == expected

    @pytest.mark.asyncio
    async def test_missing_raw_content_defaults_empty(self) -> None:
        """When context has no 'raw_content', step defaults to empty string."""
        loader = RawTextLoader()
        ctx = StepContext(job_id=_JOB_ID, values={})
        result = await loader.execute(ctx)

        assert result.outputs["raw_text"] == ""


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    """Running the loader twice on identical input yields identical output."""

    @pytest.mark.asyncio
    async def test_idempotent_on_same_input(self) -> None:
        loader = RawTextLoader()
        raw = "﻿Line one\r\nLine two\rLine three  "
        ctx1 = _make_context(raw_content=raw, document_id="doc-123")
        ctx2 = _make_context(raw_content=raw, document_id="doc-123")

        result1 = await loader.execute(ctx1)
        result2 = await loader.execute(ctx2)

        assert result1.outputs["raw_text"] == result2.outputs["raw_text"]
        assert result1.outputs["metadata"] == result2.outputs["metadata"]
        assert result1.produced_keys == result2.produced_keys

    @pytest.mark.asyncio
    async def test_already_normalised_text_remains_same(self) -> None:
        """Text that is already normalised stays unchanged (idempotency)."""
        loader = RawTextLoader()
        normalised = "Hello\nWorld\n"
        ctx = _make_context(raw_content=normalised)

        result1 = await loader.execute(ctx)
        result2 = await loader.execute(ctx)

        assert result1.outputs["raw_text"] == normalised
        assert result1.outputs["raw_text"] == result2.outputs["raw_text"]


# ---------------------------------------------------------------------------
# Metadata and output shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetadataAndOutputShape:
    """StepResult carries correct metadata fields."""

    @pytest.mark.asyncio
    async def test_produced_keys_includes_raw_text(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="hello")
        result = await loader.execute(ctx)

        assert "raw_text" in result.produced_keys

    @pytest.mark.asyncio
    async def test_metadata_chunk_type(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="hello")
        result = await loader.execute(ctx)

        assert result.outputs["metadata"]["chunk_type"] == "raw_text"

    @pytest.mark.asyncio
    async def test_metadata_step_order(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="hello")
        result = await loader.execute(ctx)

        assert result.outputs["metadata"]["step_order"] == 1

    @pytest.mark.asyncio
    async def test_metadata_source_span(self) -> None:
        loader = RawTextLoader()
        text = "Hello world"
        ctx = _make_context(raw_content=text)
        result = await loader.execute(ctx)

        span = result.outputs["metadata"]["source_span"]
        assert span == (0, len(text))

    @pytest.mark.asyncio
    async def test_metadata_document_id_preserved(self) -> None:
        loader = RawTextLoader()
        doc_id = "doc-abc-456"
        ctx = _make_context(raw_content="hello", document_id=doc_id)
        result = await loader.execute(ctx)

        assert result.outputs["metadata"]["document_id"] == doc_id

    @pytest.mark.asyncio
    async def test_metadata_document_id_none_when_absent(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="hello")
        result = await loader.execute(ctx)

        assert result.outputs["metadata"]["document_id"] is None

    @pytest.mark.asyncio
    async def test_skipped_is_false(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="hello")
        result = await loader.execute(ctx)

        assert result.skipped is False


# ---------------------------------------------------------------------------
# Context immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextImmutability:
    """RawTextLoader MUST NOT mutate the input StepContext."""

    @pytest.mark.asyncio
    async def test_context_unchanged_after_execute(self) -> None:
        loader = RawTextLoader()
        ctx = _make_context(raw_content="﻿Hello\r\nWorld")
        original_values = dict(ctx.values)

        await loader.execute(ctx)

        assert ctx.values == original_values
