"""Unit tests for the canonical ExtractionStep Protocol — NFM-2698.

Covers:

- :class:`ExtractionStep` is a ``runtime_checkable`` Protocol — any
  object with the right shape (no inheritance) satisfies it.
- :class:`StepContext` is frozen — mutation raises
  ``FrozenInstanceError``; ``with_value`` returns a new instance.
- :class:`StepResult` exposes ``produced_keys``, ``outputs``, ``skipped``.
- :func:`validate_step_type` rejects unknown step types and accepts
  every canonical type from ``EXTRACTION_STEP_TYPES``.
- :func:`is_extraction_step` distinguishes conforming from
  non-conforming objects by shape only.

These tests are intentionally *structural*: they exercise the
contract, not the orchestrator.  The flag-verification tests live in
``apps/api/tests/services/test_extraction_v2_flag_verification.py``.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES
from nfm_db.pipeline.extraction_step import (
    ExtractionStep,
    StepContext,
    StepResult,
    is_extraction_step,
    validate_step_type,
)

# ---------------------------------------------------------------------------
# Helpers — small concrete step shapes for protocol conformance tests
# ---------------------------------------------------------------------------


class _ConformingStep:
    """Minimal object that satisfies the ExtractionStep Protocol shape."""

    step_type = "chunk"
    input_keys: tuple[str, ...] = ("source_reference",)

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        return StepResult(produced_keys=("chunks",), outputs={"count": 3})


class _MissingExecuteStep:
    """Has step_type + input_keys but no execute method."""

    step_type = "extract"
    input_keys: tuple[str, ...] = ()


class _MissingStepTypeStep:
    """Has input_keys + execute but no step_type attribute."""

    input_keys: tuple[str, ...] = ()

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        return StepResult()


class _MissingInputKeysStep:
    """Has step_type + execute but no input_keys attribute."""

    step_type = "map"

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        return StepResult()


# ---------------------------------------------------------------------------
# Protocol conformance (runtime_checkable)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionStepProtocolConformance:
    """Verify ``isinstance`` checks against the runtime_checkable Protocol."""

    def test_conforming_object_passes_isinstance(self) -> None:
        step = _ConformingStep()
        assert isinstance(step, ExtractionStep)
        assert is_extraction_step(step) is True

    def test_missing_execute_fails_isinstance(self) -> None:
        # Missing the execute callable — should NOT satisfy Protocol.
        assert isinstance(_MissingExecuteStep(), ExtractionStep) is False
        assert is_extraction_step(_MissingExecuteStep()) is False

    def test_missing_step_type_fails_isinstance(self) -> None:
        assert isinstance(_MissingStepTypeStep(), ExtractionStep) is False
        assert is_extraction_step(_MissingStepTypeStep()) is False

    def test_missing_input_keys_fails_isinstance(self) -> None:
        assert isinstance(_MissingInputKeysStep(), ExtractionStep) is False
        assert is_extraction_step(_MissingInputKeysStep()) is False

    def test_non_step_object_fails_isinstance(self) -> None:
        assert is_extraction_step("not a step") is False
        assert is_extraction_step(42) is False
        assert is_extraction_step(None) is False
        assert is_extraction_step({"step_type": "chunk"}) is False


# ---------------------------------------------------------------------------
# StepContext — frozen, with_value returns a new instance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepContext:
    """StepContext is frozen; extension goes through ``with_value``."""

    def test_default_values_empty_dict(self) -> None:
        ctx = StepContext(job_id="job-1")
        assert ctx.job_id == "job-1"
        assert ctx.values == {}

    def test_get_returns_default_when_missing(self) -> None:
        ctx = StepContext(job_id="job-1")
        assert ctx.get("missing") is None
        assert ctx.get("missing", "fallback") == "fallback"

    def test_get_returns_value_when_present(self) -> None:
        ctx = StepContext(job_id="job-1", values={"chunks": [1, 2, 3]})
        assert ctx.get("chunks") == [1, 2, 3]

    def test_with_value_returns_new_instance(self) -> None:
        ctx = StepContext(job_id="job-1")
        new_ctx = ctx.with_value("chunks", [1, 2, 3])
        # Original is unchanged.
        assert ctx.values == {}
        # New instance carries the value.
        assert new_ctx.values == {"chunks": [1, 2, 3]}
        # Distinct object identity (immutability guarantee).
        assert new_ctx is not ctx

    def test_with_value_chains(self) -> None:
        ctx = StepContext(job_id="job-1")
        ctx2 = ctx.with_value("chunks", [1])
        ctx3 = ctx2.with_value("extractions", [{"k": "v"}])
        assert ctx3.values == {
            "chunks": [1],
            "extractions": [{"k": "v"}],
        }
        # Intermediate contexts are unaffected by later mutations.
        assert ctx2.values == {"chunks": [1]}

    def test_has_reports_key_presence(self) -> None:
        ctx = StepContext(job_id="job-1", values={"a": 1})
        assert ctx.has("a") is True
        assert ctx.has("missing") is False

    def test_direct_attribute_assignment_raises_frozen_error(self) -> None:
        ctx = StepContext(job_id="job-1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.job_id = "other"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.values = {"chunks": [1]}  # type: ignore[misc]

    def test_values_dict_inner_mutation_is_a_known_limitation(self) -> None:
        """``@dataclass(frozen=True)`` only blocks attribute assignment.

        Deep mutation of the mutable ``values`` dict is NOT prevented
        by the frozen decorator — Python's dataclass machinery has no
        hook for nested mutation.  The :class:`StepContext` contract
        therefore relies on convention: callers MUST extend context via
        :meth:`StepContext.with_value` rather than mutating the inner
        dict in place.  This test documents the limitation so a future
        change to wrap ``values`` in a read-only proxy can be detected.
        """
        ctx = StepContext(job_id="job-1")
        # No FrozenInstanceError — the dataclass only guards attribute access.
        ctx.values["chunks"] = [1]  # type: ignore[index]
        # But ``with_value`` still produces a fresh, structurally-correct
        # context, so the contract is upheld by convention.
        replacement = ctx.with_value("chunks", [2])
        assert ctx.values == {"chunks": [1]}  # original mutated (caveat)
        assert replacement.values == {"chunks": [2]}  # via with_value


# ---------------------------------------------------------------------------
# StepResult — produced_keys / outputs / skipped
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepResult:
    """StepResult exposes the three documented fields with correct defaults."""

    def test_defaults(self) -> None:
        result = StepResult()
        assert result.produced_keys == ()
        assert result.outputs == {}
        assert result.skipped is False

    def test_produced_keys_is_tuple(self) -> None:
        # Tuple (not list) so callers can't mutate post-hoc.
        result = StepResult(produced_keys=("a", "b"))
        assert isinstance(result.produced_keys, tuple)
        assert result.produced_keys == ("a", "b")

    def test_outputs_default_factory_is_independent(self) -> None:
        # Two StepResults share no mutable outputs dict.
        a = StepResult()
        b = StepResult()
        assert a.outputs is not b.outputs

    def test_skipped_flag_propagates(self) -> None:
        result = StepResult(produced_keys=(), outputs={}, skipped=True)
        assert result.skipped is True

    def test_step_result_is_frozen(self) -> None:
        result = StepResult(produced_keys=("a",))
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.skipped = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# validate_step_type — guards against non-canonical step types
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateStepType:
    """``validate_step_type`` accepts every canonical type, rejects others."""

    @pytest.mark.parametrize("step_type", list(EXTRACTION_STEP_TYPES))
    def test_canonical_types_pass(self, step_type: str) -> None:
        # Should not raise.
        validate_step_type(step_type)

    @pytest.mark.parametrize(
        "bad_type",
        ["", "Chunk", "EXTRACT", "raw_text_loader", "rag", "  chunk  "],
    )
    def test_non_canonical_types_raise(self, bad_type: str) -> None:
        with pytest.raises(ValueError, match="Unknown step_type"):
            validate_step_type(bad_type)

    def test_error_message_lists_canonical_types(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_step_type("bogus")
        # All five canonical types appear in the error message.
        msg = str(excinfo.value)
        for canonical in EXTRACTION_STEP_TYPES:
            assert canonical in msg


# ---------------------------------------------------------------------------
# Integration: a conforming step can execute against a StepContext
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConformingStepExecution:
    """A conforming step's ``execute`` runs against a real StepContext."""

    @pytest.mark.asyncio
    async def test_step_reports_produced_keys_and_outputs(self) -> None:
        step = _ConformingStep()
        ctx = StepContext(job_id="job-1")

        result = await step.execute(ctx, source_reference="src.pdf")

        assert result.produced_keys == ("chunks",)
        assert result.outputs == {"count": 3}
        assert result.skipped is False

    @pytest.mark.asyncio
    async def test_step_does_not_mutate_context(self) -> None:
        step = _ConformingStep()
        ctx = StepContext(job_id="job-1")

        # Capture the context identity; ensure it is untouched.
        original_values = dict(ctx.values)
        await step.execute(ctx)

        assert ctx.values == original_values
        assert ctx.values == {}  # still empty — step did not write back

    @pytest.mark.asyncio
    async def test_mock_session_is_accepted_via_kwargs(self) -> None:
        """Steps that take a session-like kwarg accept AsyncMock session."""
        step = _ConformingStep()
        ctx = StepContext(job_id="job-1")
        mock_session = AsyncMock()

        # Should not raise even though the step ignores the session.
        result = await step.execute(ctx, session=mock_session)

        assert isinstance(result, StepResult)
