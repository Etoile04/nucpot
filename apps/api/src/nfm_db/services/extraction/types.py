"""Core data model and protocol for the strangler-fig extraction pipeline.

NFM-2679 / NFM-2677-B1 — clean-slate types. The dataclass/ABC below are the
ground-truth contracts that subsequent pipeline steps (B2, B3, …) build on.
They are intentionally minimal: every step takes an ``ExtractionChunk`` in
and emits one out, with byte-offset provenance preserved by value, not by
mutation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Allowed chunk-type classifications (NFM-2679 spec).
CHUNK_TYPES: tuple[str, ...] = (
    "raw_text",
    "section",
    "entity",
    "property",
    "final",
)


@dataclass(frozen=True, slots=True)
class ExtractionChunk:
    """A single text chunk flowing through the extraction pipeline.

    Frozen + slotted so chunks are immutable values — chains of steps must
    produce new chunks, never mutate the input. Equality is value-based
    (``dataclass(eq=True)`` is the default) so dedup by content+provenance
    works without identity tricks.

    The leading underscore on ``_source_span`` mirrors the spec: provenance
    is an internal anchor (file byte offsets), not part of the chunk's
    external identity.
    """

    content: str
    chunk_type: str
    _source_span: tuple[int, int]
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_chunk_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self._source_span, tuple):
            raise ValueError(
                f"_source_span must be a 2-tuple, got {type(self._source_span).__name__}"
            )
        if len(self._source_span) != 2:
            raise ValueError(
                f"_source_span must have arity 2, got {len(self._source_span)}"
            )
        start, end = self._source_span
        # bool is a subclass of int — exclude explicitly so True/False can't sneak through.
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or isinstance(start, bool)
            or isinstance(end, bool)
        ):
            raise ValueError(
                f"_source_span offsets must be int, got ({type(start).__name__}, {type(end).__name__})"
            )
        if start < 0 or end < 0:
            raise ValueError(
                f"_source_span offsets must be non-negative, got ({start}, {end})"
            )
        if start > end:
            raise ValueError(
                f"_source_span start ({start}) must be <= end ({end})"
            )


class ExtractionStep(ABC):
    """Abstract base class for a single pipeline step (NFM-2679 / NFM-2677-B1).

    Each concrete step takes an ``ExtractionChunk`` in and emits one out.
    Subclasses must implement ``step_name``, ``step_order``, and ``execute``.
    """

    @property
    @abstractmethod
    def step_name(self) -> str:
        """Stable identifier for this step (used by orchestrator routing)."""

    @property
    @abstractmethod
    def step_order(self) -> int:
        """Relative ordering within the pipeline (0..N)."""

    @abstractmethod
    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
        """Run the step on *input_chunk* and return the resulting chunk.

        Implementations must be idempotent: re-running on the same input
        (with the same ``_source_span``) must yield the same output.
        """
