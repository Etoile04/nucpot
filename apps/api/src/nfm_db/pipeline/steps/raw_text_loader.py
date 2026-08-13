"""RawTextLoader — Step 1 of the V2 extraction pipeline (NFM-2681).

Loads raw document text from the pipeline context, normalises it (BOM
stripping, line-ending canonicalisation, trailing whitespace removal),
and produces the normalised text as a ``raw_text`` context key for
downstream steps.

Protocol conformance
--------------------
This class structurally satisfies the :class:`ExtractionStep` Protocol
without inheriting from any base class:

- ``step_type = "chunk"`` — the canonical type for text loading steps.
- ``input_keys`` — declares which :class:`StepContext` values are
  required (the orchestrator validates presence before dispatch).
- ``execute(context, **kwargs) -> StepResult`` — the async step
  entry point.

Normalisation rules
------------------
1. **BOM:** Strip ``U+FEFF`` from the very start of the text only.
2. **Line endings:** ``\\r\\n`` → ``\\n`` and ``\\r`` → ``\\n``.
3. **Trailing whitespace:** Strip trailing spaces/tabs from every line.

Idempotency
----------
Because the step is stateless (all input comes from the immutable
:class:`StepContext` and no external state is read or written), running
it twice on the same context always yields the same result.

See Also
--------
- :mod:`nfm_db.pipeline.extraction_step` — the Protocol + data classes.
- :data:`nfm_db.models.extraction_step.EXTRACTION_STEP_TYPES`
"""

from __future__ import annotations

from typing import Any

from nfm_db.pipeline.extraction_step import (
    StepContext,
    StepResult,
)

__all__ = ["RawTextLoader"]

_UTF8_BOM = "﻿"


class RawTextLoader:
    """Load and normalise raw document text into a pipeline-ready chunk.

    Satisfies the :class:`~nfm_db.pipeline.extraction_step.ExtractionStep`
    Protocol by structural conformance (no base-class inheritance).
    """

    step_type: str = "chunk"
    input_keys: tuple[str, ...] = ("raw_content",)

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        """Normalise raw text and return it via StepResult.

        Parameters
        ----------
        context:
            Immutable pipeline context. Must contain ``raw_content``.
        **kwargs:
            Reserved for future orchestrator extensions (e.g. ``session``).

        Returns
        -------
        StepResult
            ``produced_keys=("raw_text",)`` with the normalised text in
            ``outputs["raw_text"]`` and step metadata in
            ``outputs["metadata"]``.
        """
        raw_content: str = context.get("raw_content", "")
        normalised = self._normalize(raw_content)

        metadata: dict[str, Any] = {
            "chunk_type": "raw_text",
            "step_order": 1,
            "source_span": (0, len(normalised)),
            "document_id": context.get("document_id"),
        }

        return StepResult(
            produced_keys=("raw_text",),
            outputs={
                "raw_text": normalised,
                "metadata": metadata,
            },
        )

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalise raw text: strip BOM, canonicalise line endings, trim trailing whitespace."""
        # 1. Strip UTF-8 BOM from the very start.
        if text.startswith(_UTF8_BOM):
            text = text[len(_UTF8_BOM):]

        # 2. Normalise line endings: \r\n → \n, then remaining \r → \n.
        #    Order matters: handle \r\n first so lone \r doesn't leave orphan \n.
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 3. Trim trailing whitespace (spaces and tabs) from every line.
        lines = [line.rstrip(" \t") for line in text.split("\n")]
        return "\n".join(lines)
