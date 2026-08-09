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

# ---------- Chunk ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractionChunk:
    """A unit of work flowing through the extraction pipeline.

    Frozen + slots = hashable, equality-stable, allocation-efficient, and
    immune to accidental downstream mutation. The leading underscore on
    ``_source_span`` is a soft "internal" hint: callers normally propagate
    spans through the pipeline but should not hand-edit byte offsets that
    no longer correspond to the originating document.
    """

    content: str
    chunk_type: str  # raw_text | section | entity | property | final | …
    _source_span: tuple[int, int]
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_chunk_id: str | None = None

    def __post_init__(self) -> None:
        span = self._source_span
        if not isinstance(span, tuple) or len(span) != 2:
            raise ValueError(
                f"_source_span must be a 2-tuple of ints, got {span!r}"
            )
        start, end = span
        if not isinstance(start, int) or not isinstance(end, int):
            raise TypeError(
                f"_source_span entries must be ints, got ({type(start).__name__}, "
                f"{type(end).__name__})"
            )
        if start < 0 or end < 0:
            raise ValueError(
                f"_source_span offsets must be non-negative, got {span!r}"
            )
        if start > end:
            raise ValueError(
                f"_source_span start ({start}) must be <= end ({end})"
            )


# ---------- Step -----------------------------------------------------------


class ExtractionStep(ABC):
    """Base contract for every pipeline step (raw_text → final).

    A step transforms one chunk into another. ``step_name`` is the stable
    identifier persisted in DB rows and surfaced in observability; keep it
    deterministic and free of PII. ``step_order`` is the integer that places
    the step in the pipeline DAG — used by the orchestrator to topologically
    order steps and to break ties among same-rank steps.
    """

    @property
    @abstractmethod
    def step_name(self) -> str: ...

    @property
    @abstractmethod
    def step_order(self) -> int: ...

    @abstractmethod
    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk: ...
