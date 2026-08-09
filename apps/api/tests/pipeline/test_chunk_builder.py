"""Unit tests for ChunkBuilder pipeline step — NFM-2685.

Covers:

1. Span chain correctly preserves all upstream spans
2. Multiple final chunks per document (grouped by parent_chunk_id)
3. Empty upstream → empty output
4. Idempotency (deterministic UUIDs for same input)
5. Protocol conformance
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from nfm_db.pipeline.chunk_builder import ChunkBuilder
from nfm_db.pipeline.extraction_step import (
    ExtractionStep,
    StepContext,
    StepResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_upstream_chunk(
    *,
    chunk_id: str | None = None,
    content: str = "upstream content",
    source_span: tuple[int, int] = (0, 18),
    parent_chunk_id: str = "raw-1",
    chunk_type: str = "section",
    step_order: int = 1,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a lightweight upstream-chunk dict for testing."""
    return {
        "chunk_id": chunk_id or str(uuid.uuid4()),
        "content": content,
        "source_span": source_span,
        "parent_chunk_id": parent_chunk_id,
        "chunk_type": chunk_type,
        "step_order": step_order,
        "metadata": metadata or {},
    }


# ---------------------------------------------------------------------------
# 1. Span chain correctly preserves all upstream spans
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpanChainPreservation:
    """AC: Complete span chain preserved in metadata."""

    @pytest.mark.asyncio
    async def test_single_upstream_chunk_produces_single_span(self) -> None:
        builder = ChunkBuilder()
        upstream = [_make_upstream_chunk(source_span=(10, 50))]
        ctx = StepContext(job_id="job-1", values={"upstream_chunks": upstream})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert len(final_chunks) == 1
        assert final_chunks[0]["chunk_type"] == "final"
        assert final_chunks[0]["step_order"] == 5
        assert final_chunks[0]["span_chain"] == [(10, 50)]

    @pytest.mark.asyncio
    async def test_multiple_upstreams_combined_span_chain(self) -> None:
        builder = ChunkBuilder()
        upstream = [
            _make_upstream_chunk(
                source_span=(0, 100), parent_chunk_id="raw-1",
                content="first",
            ),
            _make_upstream_chunk(
                source_span=(100, 250), parent_chunk_id="raw-1",
                content="second",
            ),
            _make_upstream_chunk(
                source_span=(250, 400), parent_chunk_id="raw-1",
                content="third",
            ),
        ]
        ctx = StepContext(job_id="job-1", values={"upstream_chunks": upstream})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert len(final_chunks) == 1
        assert final_chunks[0]["span_chain"] == [
            (0, 100), (100, 250), (250, 400),
        ]
        assert final_chunks[0]["parent_chunk_id"] == "raw-1"

    @pytest.mark.asyncio
    async def test_chunk_missing_source_span_skipped_in_chain(self) -> None:
        """Chunks without source_span do not contribute to span_chain."""
        builder = ChunkBuilder()
        upstream = [
            _make_upstream_chunk(source_span=(0, 50)),
            {
                "chunk_id": str(uuid.uuid4()),
                "content": "no span",
                "parent_chunk_id": "raw-1",
                "chunk_type": "entity",
                "step_order": 2,
                "metadata": {},
            },
        ]
        ctx = StepContext(job_id="job-1", values={"upstream_chunks": upstream})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert len(final_chunks) == 1
        assert final_chunks[0]["span_chain"] == [(0, 50)]


# ---------------------------------------------------------------------------
# 2. Multiple final chunks per document
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultipleFinalChunks:
    """AC: Upstream chunks from different parents produce separate finals."""

    @pytest.mark.asyncio
    async def test_different_parents_produce_separate_finals(self) -> None:
        builder = ChunkBuilder()
        upstream = [
            _make_upstream_chunk(
                source_span=(0, 100), parent_chunk_id="raw-A",
                content="A-first",
            ),
            _make_upstream_chunk(
                source_span=(100, 200), parent_chunk_id="raw-A",
                content="A-second",
            ),
            _make_upstream_chunk(
                source_span=(0, 150), parent_chunk_id="raw-B",
                content="B-only",
            ),
        ]
        ctx = StepContext(job_id="job-1", values={"upstream_chunks": upstream})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert len(final_chunks) == 2

        by_parent = {c["parent_chunk_id"]: c for c in final_chunks}
        assert "raw-A" in by_parent
        assert "raw-B" in by_parent
        assert by_parent["raw-A"]["span_chain"] == [(0, 100), (100, 200)]
        assert by_parent["raw-B"]["span_chain"] == [(0, 150)]

    @pytest.mark.asyncio
    async def test_final_chunk_content_concatenates_upstream(self) -> None:
        builder = ChunkBuilder()
        upstream = [
            _make_upstream_chunk(
                content="alpha", parent_chunk_id="raw-1",
                source_span=(0, 5),
            ),
            _make_upstream_chunk(
                content="beta", parent_chunk_id="raw-1",
                source_span=(5, 9),
            ),
        ]
        ctx = StepContext(job_id="job-1", values={"upstream_chunks": upstream})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert len(final_chunks) == 1
        assert final_chunks[0]["content"] == "alpha\nbeta"


# ---------------------------------------------------------------------------
# 3. Empty upstream → empty output
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyUpstream:
    """AC: Empty upstream produces empty output."""

    @pytest.mark.asyncio
    async def test_empty_list_produces_no_final_chunks(self) -> None:
        builder = ChunkBuilder()
        ctx = StepContext(job_id="job-1", values={"upstream_chunks": []})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert final_chunks == []
        assert result.produced_keys == ()

    @pytest.mark.asyncio
    async def test_missing_key_produces_no_final_chunks(self) -> None:
        builder = ChunkBuilder()
        ctx = StepContext(job_id="job-1", values={})

        result = await builder.execute(ctx)

        final_chunks = result.outputs.get("final_chunks", [])
        assert final_chunks == []


# ---------------------------------------------------------------------------
# 4. Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    """AC: Same input always produces the same output (deterministic)."""

    @pytest.mark.asyncio
    async def test_identical_inputs_produce_identical_outputs(self) -> None:
        builder = ChunkBuilder()
        fixed_id_a = str(uuid.uuid4())
        fixed_id_b = str(uuid.uuid4())

        def _upstream():
            return [
                _make_upstream_chunk(
                    chunk_id=fixed_id_a,
                    source_span=(0, 50),
                    parent_chunk_id="raw-1",
                ),
                _make_upstream_chunk(
                    chunk_id=fixed_id_b,
                    source_span=(50, 120),
                    parent_chunk_id="raw-1",
                ),
            ]

        result1 = await builder.execute(
            StepContext(job_id="job-1", values={"upstream_chunks": _upstream()}),
        )
        result2 = await builder.execute(
            StepContext(job_id="job-1", values={"upstream_chunks": _upstream()}),
        )

        assert result1.outputs["final_chunks"] == result2.outputs["final_chunks"]

    @pytest.mark.asyncio
    async def test_context_not_mutated_after_execute(self) -> None:
        builder = ChunkBuilder()
        upstream = [_make_upstream_chunk()]
        original_values = {"upstream_chunks": upstream}
        ctx = StepContext(job_id="job-1", values=original_values)

        await builder.execute(ctx)

        assert ctx.values == original_values

    @pytest.mark.asyncio
    async def test_chunk_id_is_deterministic(self) -> None:
        """Same parent_chunk_id + same span_chain → same chunk_id."""
        builder = ChunkBuilder()

        def _upstream():
            return [
                _make_upstream_chunk(
                    source_span=(0, 100),
                    parent_chunk_id="raw-1",
                ),
                _make_upstream_chunk(
                    source_span=(100, 200),
                    parent_chunk_id="raw-1",
                ),
            ]

        result1 = await builder.execute(
            StepContext(job_id="j", values={"upstream_chunks": _upstream()}),
        )
        result2 = await builder.execute(
            StepContext(job_id="j", values={"upstream_chunks": _upstream()}),
        )

        ids_1 = [c["chunk_id"] for c in result1.outputs["final_chunks"]]
        ids_2 = [c["chunk_id"] for c in result2.outputs["final_chunks"]]
        assert ids_1 == ids_2


# ---------------------------------------------------------------------------
# 5. Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProtocolConformance:
    """ChunkBuilder structurally satisfies the ExtractionStep Protocol."""

    def test_satisfies_isinstance(self) -> None:
        builder = ChunkBuilder()
        assert isinstance(builder, ExtractionStep)

    def test_step_type_is_canonical(self) -> None:
        builder = ChunkBuilder()
        assert builder.step_type in (
            "chunk", "extract", "map", "quality_gate", "gap_scan",
        )

    def test_input_keys_is_tuple(self) -> None:
        builder = ChunkBuilder()
        assert isinstance(builder.input_keys, tuple)

    def test_step_result_produced_keys_is_tuple(self) -> None:
        """StepResult.produced_keys must be a tuple (immutability)."""
        assert isinstance(StepResult().produced_keys, tuple)
