"""Weighted priority scoring (NFM-3575 / NFM-3548-A).

Phase 5.3 priority scoring refactor — foundation module consumed by
NFM-3548-B (call site) and NFM-3548-D (diff report).

The public surface is intentionally pure: every function returns a
deterministic score in [0, 1] with no I/O, no clock reads, and no
random calls.  This is required by AC #2 and the determinism test
(``tests/services/test_priority.py::test_determinism_contract``).

Scoring formula
---------------

``score(term, prop_id, ontology_version_id)`` is a weighted dot product of
three signals, each normalized to [0, 1]::

    score = w_ontology * ontology_weight(ontology_version_id)
          + w_atf      * atf(term)
          + w_citation * citation_frequency(prop_id)

Default weights (overridable via ``NFM_PRIORITY_WEIGHTS``):

    w_ontology = 0.4
    w_atf      = 0.3
    w_citation = 0.3
"""

from __future__ import annotations

import json
import math
import os
from typing import Mapping


# ---------------------------------------------------------------------------
# Default weights — overridable via NFM_PRIORITY_WEIGHTS (JSON env var).
# ---------------------------------------------------------------------------


def _resolve_default_weights() -> dict[str, float]:
    """Resolve the module-level default weight table.

    Reads ``NFM_PRIORITY_WEIGHTS`` (a JSON object with the three keys
    ``ontology``, ``atf``, ``citation``) and falls back to the spec
    defaults when unset or malformed.  Called once at import time so
    that downstream tests can mutate ``os.environ`` and reload the
    module to exercise overrides deterministically.
    """
    raw = os.environ.get("NFM_PRIORITY_WEIGHTS")
    defaults = {"ontology": 0.4, "atf": 0.3, "citation": 0.3}
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return defaults
    if not isinstance(parsed, Mapping):
        return defaults
    for key in defaults:
        value = parsed.get(key, defaults[key])
        try:
            defaults[key] = float(value)
        except (TypeError, ValueError):
            defaults[key] = 0.0
    return defaults


DEFAULT_WEIGHTS: dict[str, float] = _resolve_default_weights()


# ---------------------------------------------------------------------------
# Synthetic reference data — replaced by real backends in NFM-3548-C.
# ---------------------------------------------------------------------------
#
# These constants intentionally mirror the values the unit tests assert
# against.  They are not source-of-truth statistics — they exist only so
# the foundation module can be shipped and tested before the ontology,
# corpus, and citation backends (NFM-3548-C) are wired in.  Once C is
# shipped the backends are injected via module-level setters or a
# dedicated module (not yet introduced here).

_ONTOLOGY_REFS: dict[int, int] = {
    1: 0,    # registered, no refs ⇒ 0.0
    2: 10,   # mid-range ⇒ 10/30 ≈ 0.3333
    3: 30,   # max ⇒ 1.0
    4: 5,    # below max ⇒ 5/30 ≈ 0.1667
}

_MAX_ONTOLOGY_REFS: int = max(_ONTOLOGY_REFS.values())

_TERM_FREQ: dict[str, int] = {
    "water": 0,
    "cell": 100,
    "dna": 10_000,
    "gene": 500,
}

_CORPUS_SIZE: int = 10_000

_CITATION_COUNTS: dict[str, int] = {
    "PROP:zero": 0,
    "PROP:mid": 50,
    "PROP:max": 5000,
    "PROP:low": 5,
}

_MAX_CITATIONS: int = max(_CITATION_COUNTS.values())


# ---------------------------------------------------------------------------
# Per-signal functions
# ---------------------------------------------------------------------------


def ontology_weight(ontology_version_id: int) -> float:
    """Normalize the reference count for ``ontology_version_id`` to [0, 1].

    Newer + more-referenced ontology types weigh more.  Unknown IDs
    return 0.0 so callers can rely on the bound without an extra
    ``is_known`` branch.
    """
    refs = _ONTOLOGY_REFS.get(int(ontology_version_id), 0)
    if refs <= 0 or _MAX_ONTOLOGY_REFS <= 0:
        return 0.0
    return min(1.0, refs / _MAX_ONTOLOGY_REFS)


def atf(term: str) -> float:
    """Average term frequency across curated corpora, normalized to [0, 1].

    Log-scale clamp at the corpus size avoids the long-tail where a
    single term dominates.  Unknown or empty terms return 0.0.
    """
    if not term:
        return 0.0
    cleaned = term.strip().lower()
    if not cleaned:
        return 0.0
    freq = _TERM_FREQ.get(cleaned, 0)
    if freq <= 0:
        return 0.0
    return min(1.0, math.log(1 + freq) / math.log(1 + _CORPUS_SIZE))


def citation_frequency(prop_id: str) -> float:
    """Number of papers citing ``prop_id``, normalized to [0, 1].

    Log-scale clamp mirrors :func:`atf` so that the three signals share
    the same response shape.  Unknown props return 0.0.
    """
    if not prop_id:
        return 0.0
    count = _CITATION_COUNTS.get(prop_id, 0)
    if count <= 0:
        return 0.0
    return min(1.0, math.log(1 + count) / math.log(1 + _MAX_CITATIONS))


# ---------------------------------------------------------------------------
# Combine
# ---------------------------------------------------------------------------


def combine(
    signals: Mapping[str, float],
    weights: Mapping[str, float] | None = None,
) -> float:
    """Dot-product the three weighted signals and return a score in [0, 1].

    ``signals`` keys must be drawn from ``{"ontology", "atf", "citation"}``.
    Unknown keys are ignored so callers can pass a superset without
    filtering.  ``weights`` defaults to :data:`DEFAULT_WEIGHTS` (resolved
    from ``NFM_PRIORITY_WEIGHTS`` at import time).
    """
    effective_weights = weights if weights is not None else DEFAULT_WEIGHTS
    total = 0.0
    for key in ("ontology", "atf", "citation"):
        weight = float(effective_weights.get(key, 0.0))
        signal = float(signals.get(key, 0.0))
        total += weight * signal
    # Defensive clamp even though math guarantees it for the spec weights.
    if total < 0.0:
        return 0.0
    if total > 1.0:
        return 1.0
    return total


# ---------------------------------------------------------------------------
# score — public integration entry point
# ---------------------------------------------------------------------------


def score(term: str, prop_id: str, ontology_version_id: int) -> float:
    """End-to-end weighted score for ``(term, prop_id, ontology_version_id)``.

    Composition is deterministic and pure: identical inputs always
    return the same float.
    """
    signals = {
        "ontology": ontology_weight(ontology_version_id),
        "atf": atf(term),
        "citation": citation_frequency(prop_id),
    }
    return combine(signals)


__all__ = [
    "DEFAULT_WEIGHTS",
    "atf",
    "citation_frequency",
    "combine",
    "ontology_weight",
    "score",
]