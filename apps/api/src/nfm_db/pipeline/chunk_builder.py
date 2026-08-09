"""ChunkBuilder pipeline step — NFM-2685.

Assembles final document-level ExtractionChunk output with complete
``source_span`` offset chain from all upstream steps.

This step conforms to the :class:`~nfm_db.pipeline.extraction_step.ExtractionStep`
Protocol: it reads ``upstream_chunks`` from the :class:`StepContext`,
groups them by ``parent_chunk_id``, concatenates their ``source_span``
offsets into a ``span_chain``, and emits one ``final`` chunk per
originating raw-text chunk.

Idempotent: the same input always produces the same output (including
deterministic ``chunk_id`` values derived from a UUID5 namespace).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from nfm_db.pipeline.extraction_step import (
    StepContext,
    StepResult,
)

__all__ = ["ChunkBuilder"]

# UUID5 namespace for deterministic chunk-id generation.
_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _deterministic_chunk_id(
    parent_chunk_id: str,
    span_chain: tuple[tuple[int, int], ...],
) -> str:
    """Derive a stable UUID5 from the parent id and span chain."""
    raw = f"{parent_chunk_id}:{span_chain}"
    return str(uuid.uuid5(_NAMESPACE, raw))


class ChunkBuilder:
    """Assemble final document-level chunks from upstream step outputs.

    Conforms to the :class:`ExtractionStep` Protocol without
    inheriting from any base class.  Groups upstream chunks by
    ``parent_chunk_id``, collects their ``source_span`` offsets into
    a ``span_chain``, and emits one ``final`` chunk per originating
    raw-text chunk.

    Attributes
    ----------
    step_type:
        Canonical step type (``"map"``) — this step maps/aggregates
        upstream results into final assembled form.
    input_keys:
        Context keys this step requires before execution.
    """

    step_type: str = "map"
    input_keys: tuple[str, ...] = ("upstream_chunks",)

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        """Build final chunks from upstream pipeline outputs.

        Parameters
        ----------
        context:
            Immutable pipeline context carrying ``upstream_chunks``.
        **kwargs:
            Unused; accepted for Protocol signature compatibility.

        Returns
        -------
        StepResult
            ``outputs["final_chunks"]`` carries the assembled final
            chunks; ``produced_keys`` is ``("final_chunks",)`` when
            there is output, empty otherwise.
        """
        upstream_chunks: list[dict[str, Any]] = context.get(
            "upstream_chunks", [],
        )

        if not upstream_chunks:
            return StepResult(
                produced_keys=(),
                outputs={"final_chunks": []},
            )

        # Group by parent_chunk_id, preserving insertion order.
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for chunk in upstream_chunks:
            parent_id = chunk.get("parent_chunk_id", "")
            groups[parent_id].append(chunk)

        # Sort parent keys for deterministic output ordering.
        final_chunks: list[dict[str, Any]] = []
        for parent_id in sorted(groups):
            group = groups[parent_id]
            span_chain = tuple(
                tuple(chunk["source_span"])
                for chunk in group
                if "source_span" in chunk
            )
            final_chunks.append({
                "chunk_id": _deterministic_chunk_id(parent_id, span_chain),
                "content": "\n".join(
                    chunk.get("content", "") for chunk in group
                ),
                "chunk_type": "final",
                "step_order": 5,
                "span_chain": list(span_chain),
                "parent_chunk_id": parent_id,
                "metadata": {
                    "upstream_count": len(group),
                    "upstream_types": sorted({
                        chunk.get("chunk_type", "unknown")
                        for chunk in group
                    }),
                },
            })

        return StepResult(
            produced_keys=("final_chunks",),
            outputs={"final_chunks": final_chunks},
        )
