"""Canonical :class:`ExtractionStep` Protocol — NFM-2698 / NFM-2676-A.

This module defines the contract every pipeline step must satisfy in
the V2 strangler-fig pipeline decomposition.  It is the *foundation*
that the five concrete step implementations (NFM-2677) will conform
to; the orchestrator (:class:`nfm_db.services.extraction_orchestrator.ExtractionOrchestrator`)
remains the runtime driver that wires the steps together.

Why a Protocol (and not an ABC)
-------------------------------

A ``typing.Protocol`` keeps the contract structural: any class that
declares ``step_type`` + ``input_keys`` + an ``async execute`` method
satisfies :class:`ExtractionStep` without inheriting from anything in
this module.  That lets concrete step implementations stay
self-contained (no extra base class) and lets tests build small ad-hoc
step classes that match the shape.

Immutability contract
---------------------

Steps are **stateless** — all mutable state lives in the
:class:`StepContext` passed to :meth:`ExtractionStep.execute`.
Implementations MUST NOT mutate ``context``; instead, populate the
new context returned in :class:`StepResult`.  This guarantees
idempotency (a step with the same ``input_hash`` produces the same
output) and supports the orchestrator's skip-detection via
:func:`nfm_db.services.extraction_orchestrator.compute_input_hash`.

See Also
--------

- :data:`nfm_db.models.extraction_step.EXTRACTION_STEP_TYPES` — the
  five canonical step types (``chunk``, ``extract``, ``map``,
  ``quality_gate``, ``gap_scan``).
- :class:`nfm_db.services.extraction_orchestrator.ExtractionOrchestrator`
  — the runtime orchestrator that drives step execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nfm_db.models.extraction_step import EXTRACTION_STEP_TYPES

__all__ = [
    "ExtractionStep",
    "StepContext",
    "StepResult",
    "is_extraction_step",
    "validate_step_type",
]


@runtime_checkable
class ExtractionStep(Protocol):
    """The structural contract every pipeline step must satisfy.

    Attributes
    ----------
    step_type:
        One of :data:`nfm_db.models.extraction_step.EXTRACTION_STEP_TYPES`.
        Used by the orchestrator to dispatch and by the
        ``extraction_steps`` table to identify the row.
    input_keys:
        Tuple of :class:`StepContext` value keys this step requires.
        The orchestrator validates that all required keys are present
        before invoking :meth:`execute` and refuses to run a step with
        missing inputs (so a mis-wired pipeline fails fast at the
        orchestration boundary, not deep inside a step).

    Methods
    -------
    execute(context, **kwargs):
        Run the step.  Implementations MUST NOT mutate ``context``;
        they return a :class:`StepResult` whose ``produced_keys`` lists
        the new context entries to merge.
    """

    step_type: str
    input_keys: tuple[str, ...]

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        """Run the step against the supplied (immutable) context."""
        ...


@dataclass(frozen=True)
class StepContext:
    """Immutable, shared context passed between pipeline steps.

    Frozen so accidental mutation raises immediately and so steps
    always see the same view of upstream state.  The orchestrator
    produces a new :class:`StepContext` (via :meth:`with_value`) each
    time a step reports a produced key.

    Attributes
    ----------
    job_id:
        The parent :class:`ExtractionJob` UUID — propagated to every
        step so it can tag persisted rows.
    values:
        Typed payload map carrying step outputs between phases.  Use
        :meth:`get` for safe lookup and :meth:`with_value` to extend.
    """

    job_id: Any
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Return ``values[key]`` or ``default`` if absent."""
        return self.values.get(key, default)

    def with_value(self, key: str, value: Any) -> StepContext:
        """Return a new :class:`StepContext` with ``key=value`` added.

        The original context is left untouched.  This is the only way
        steps are allowed to extend the shared state — direct mutation
        of ``self.values`` raises :class:`dataclasses.FrozenInstanceError`.
        """
        new_values = {**self.values, key: value}
        return StepContext(job_id=self.job_id, values=new_values)

    def has(self, key: str) -> bool:
        """Return True iff ``key`` is present in ``values``."""
        return key in self.values


@dataclass(frozen=True)
class StepResult:
    """The output contract of a single :meth:`ExtractionStep.execute` call.

    Attributes
    ----------
    produced_keys:
        Tuple of :class:`StepContext` keys this step populated.  The
        orchestrator uses this to know which entries from the step's
        private state to merge into the running pipeline context.
    outputs:
        Optional mapping for side-channel artifacts (row counts,
        gap tuples, error messages) that the step wants to expose for
        operator inspection or ``extraction_steps.metadata_`` but does
        NOT want to propagate as :class:`StepContext` values.
    skipped:
        Indicates this step was a no-op (e.g. orchestrator's
        skip-detection found a matching prior run with the same
        ``input_hash``).  When ``True``, the orchestrator honours the
        skip without overwriting the persisted step row's status.
    """

    produced_keys: tuple[str, ...] = ()
    outputs: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False


def validate_step_type(step_type: str) -> None:
    """Raise :class:`ValueError` if ``step_type`` is not a canonical pipeline step.

    Centralises the canonical-step-types check so step implementations
    and orchestrator dispatch share one source of truth (the
    ``EXTRACTION_STEP_TYPES`` tuple in
    :mod:`nfm_db.models.extraction_step`).
    """
    if step_type not in EXTRACTION_STEP_TYPES:
        raise ValueError(
            f"Unknown step_type {step_type!r}; "
            f"must be one of {EXTRACTION_STEP_TYPES}"
        )


def is_extraction_step(obj: object) -> bool:
    """Return ``True`` iff ``obj`` structurally satisfies :class:`ExtractionStep`.

    Because :class:`ExtractionStep` is a ``runtime_checkable`` Protocol,
    this checks the *shape* (``step_type`` attribute + ``input_keys``
    attribute + ``execute`` callable) — not an explicit base class.
    Useful for adapters that accept arbitrary step objects (e.g. a
    future DI container).
    """
    return isinstance(obj, ExtractionStep)
