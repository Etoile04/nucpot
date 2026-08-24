"""Tests for the NFM_PRIORITY_V2_ENABLED A/B flag wiring (NFM-3577).

These tests verify the dispatcher in :mod:`nfm_db.services.extraction_pipeline`
selects between the legacy inline heuristic (``V2=False``) and the
canonical ``priority.combine`` formula (``V2=True``) without changing
behaviour for either path.

The legacy inline heuristic and the new ``priority.score`` formula are
designed to produce *identical* output for identical inputs — the test
corpus below pins that equivalence so a future drift in either side is
caught at CI.

NOTE: the ``priority`` module is part of Sibling A (NFM-3575) which is
not yet merged to origin/main.  We provide a minimal in-test stub of
``priority`` via :func:`_install_priority_stub` when the canonical
module is unavailable so the test runs on this branch in isolation
and on the integration branch after Sibling A merges.
"""

from __future__ import annotations

import importlib
import math
import os
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Synthetic reference data — mirrors priority.py so the test stays
# independent of the Sibling A branch state.
# ---------------------------------------------------------------------------

_ONTOLOGY_REFS: dict[int, int] = {1: 0, 2: 10, 3: 30, 4: 5}
_MAX_ONTOLOGY_REFS: int = max(_ONTOLOGY_REFS.values())

_TERM_FREQ: dict[str, int] = {"water": 0, "cell": 100, "dna": 10_000, "gene": 500}
_CORPUS_SIZE: int = 10_000

_CITATION_COUNTS: dict[str, int] = {"PROP:zero": 0, "PROP:mid": 50, "PROP:max": 5000, "PROP:low": 5}
_MAX_CITATIONS: int = max(_CITATION_COUNTS.values())

_DEFAULT_WEIGHTS: dict[str, float] = {"ontology": 0.4, "atf": 0.3, "citation": 0.3}


# Known corpus: (term, prop_id, ontology_version_id, expected_score)
# Each ``expected_score`` is computed from the formula:
#   score = 0.4 * ontology_weight(id) + 0.3 * atf(term) + 0.3 * citation_frequency(prop_id)
_KNOWN_CORPUS: list[tuple[str, str, int, float]] = [
    # ontology_version_id=3 (max refs=30 → 1.0), term="dna" (10000 → 1.0), prop="PROP:max" (5000 → 1.0)
    # 0.4 * 1.0 + 0.3 * 1.0 + 0.3 * 1.0 = 1.0
    ("dna", "PROP:max", 3, 1.0),
    # ontology=1 (0 refs → 0.0), term="water" (0 → 0.0), prop="PROP:zero" (0 → 0.0) → 0.0
    ("water", "PROP:zero", 1, 0.0),
    # ontology=2 (10 refs / 30 max = 0.3333), term="cell" (100 → log(101)/log(10001) ≈ 0.5011),
    # prop="PROP:mid" (50 → log(51)/log(5001) ≈ 0.4343)
    # score ≈ 0.4*0.3333 + 0.3*0.5011 + 0.3*0.4343 ≈ 0.1333 + 0.1503 + 0.1303 ≈ 0.4140
    ("cell", "PROP:mid", 2, 0.4 * (10 / 30) + 0.3 * (math.log(101) / math.log(10001)) + 0.3 * (math.log(51) / math.log(5001))),
    # ontology=4 (5/30 ≈ 0.1667), term="gene" (500 → log(501)/log(10001) ≈ 0.6761),
    # prop="PROP:low" (5 → log(6)/log(5001) ≈ 0.2634)
    # ≈ 0.4*0.1667 + 0.3*0.6761 + 0.3*0.2634 ≈ 0.0667 + 0.2028 + 0.0790 ≈ 0.3485
    ("gene", "PROP:low", 4, 0.4 * (5 / 30) + 0.3 * (math.log(501) / math.log(10001)) + 0.3 * (math.log(6) / math.log(5001))),
]


def _install_priority_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a minimal ``nfm_db.services.priority`` stub.

    The stub mirrors the canonical ``score`` formula so the
    ``V2=True`` path of the dispatcher can be exercised on this branch
    before Sibling A (NFM-3575) lands.  Once Sibling A is merged the
    canonical module shadows the stub via ``del sys.modules`` first.
    """

    if "nfm_db.services.priority" in sys.modules and not getattr(
        sys.modules["nfm_db.services.priority"], "_test_stub", False
    ):
        # Canonical module is already importable; nothing to stub.
        return

    def _ontology_weight(ontology_version_id: int) -> float:
        refs = _ONTOLOGY_REFS.get(int(ontology_version_id), 0)
        if refs <= 0 or _MAX_ONTOLOGY_REFS <= 0:
            return 0.0
        return min(1.0, refs / _MAX_ONTOLOGY_REFS)

    def _atf(term: str) -> float:
        if not term:
            return 0.0
        cleaned = term.strip().lower()
        if not cleaned:
            return 0.0
        freq = _TERM_FREQ.get(cleaned, 0)
        if freq <= 0:
            return 0.0
        return min(1.0, math.log(1 + freq) / math.log(1 + _CORPUS_SIZE))

    def _citation_frequency(prop_id: str) -> float:
        if not prop_id:
            return 0.0
        count = _CITATION_COUNTS.get(prop_id, 0)
        if count <= 0:
            return 0.0
        return min(1.0, math.log(1 + count) / math.log(1 + _MAX_CITATIONS))

    def _combine(signals: dict[str, float], weights: dict[str, float] | None = None) -> float:
        effective = weights if weights is not None else _DEFAULT_WEIGHTS
        total = 0.0
        for key in ("ontology", "atf", "citation"):
            total += float(effective.get(key, 0.0)) * float(signals.get(key, 0.0))
        return max(0.0, min(1.0, total))

    def _score(term: str, prop_id: str, ontology_version_id: int) -> float:
        return _combine(
            {
                "ontology": _ontology_weight(ontology_version_id),
                "atf": _atf(term),
                "citation": _citation_frequency(prop_id),
            }
        )

    stub = types.ModuleType("nfm_db.services.priority")
    stub.ontology_weight = _ontology_weight
    stub.atf = _atf
    stub.citation_frequency = _citation_frequency
    stub.combine = _combine
    stub.score = _score
    stub.DEFAULT_WEIGHTS = _DEFAULT_WEIGHTS
    stub._test_stub = True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nfm_db.services.priority", stub)


@pytest.fixture(autouse=True)
def _ensure_priority_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure ``nfm_db.services.priority`` is importable for every test."""
    _install_priority_stub(monkeypatch)


# ---------------------------------------------------------------------------
# Dispatcher surface — what the test imports from extraction_pipeline.
# ---------------------------------------------------------------------------

def _import_dispatcher() -> Any:
    """Import the priority dispatcher from extraction_pipeline.

    Returns the module so tests can call ``module._legacy_priority_heuristic``
    and ``module._priority_score`` directly.
    """
    # Force a fresh import so env-var mutations in tests take effect.
    sys.modules.pop("nfm_db.services.extraction_pipeline", None)
    return importlib.import_module("nfm_db.services.extraction_pipeline")


# ---------------------------------------------------------------------------
# AC #5: Config flag default = False (no behaviour change on deploy
# without explicit flip).
# ---------------------------------------------------------------------------


def test_config_priority_v2_default_is_false() -> None:
    """Settings.priority_v2_enabled defaults to False (NFM-3577 AC #5)."""
    # Clear any leakage from earlier tests.
    saved = os.environ.pop("NFM_PRIORITY_V2_ENABLED", None)
    try:
        from nfm_db.config import Settings

        settings = Settings()
        assert settings.priority_v2_enabled is False, (
            f"Default flag must be False for safe rollout, got {settings.priority_v2_enabled!r}"
        )
    finally:
        if saved is not None:
            os.environ["NFM_PRIORITY_V2_ENABLED"] = saved


def test_config_priority_v2_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings.priority_v2_enabled reads NFM_PRIORITY_V2_ENABLED env var."""
    from nfm_db.config import Settings

    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "1")
    assert Settings().priority_v2_enabled is True

    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "true")
    assert Settings().priority_v2_enabled is True

    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "0")
    assert Settings().priority_v2_enabled is False

    monkeypatch.delenv("NFM_PRIORITY_V2_ENABLED", raising=False)
    assert Settings().priority_v2_enabled is False


# ---------------------------------------------------------------------------
# AC #1: V2=False path — extraction_pipeline uses the existing heuristic,
# preserved verbatim, no behaviour change.
# ---------------------------------------------------------------------------


def test_v2_false_uses_legacy_heuristic_for_known_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #1 + AC #3: V2=False path computes the legacy inline score for
    every entry in the known corpus and the result matches the
    manually-derived expected value to 1e-9.
    """
    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "0")
    module = _import_dispatcher()

    for term, prop_id, ontology_version_id, expected in _KNOWN_CORPUS:
        actual = module._priority_score(term, prop_id, ontology_version_id)
        assert actual == pytest.approx(expected, abs=1e-9), (
            f"V2=False legacy mismatch for ({term!r}, {prop_id!r}, {ontology_version_id}): "
            f"expected {expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# AC #2: V2=True path — extraction_pipeline calls priority.combine(...)
# with the configured weights.
# ---------------------------------------------------------------------------


def test_v2_true_uses_priority_combine_for_known_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #2 + AC #4: V2=True path delegates to priority.score and the
    result matches the canonical weighted-sum formula.
    """
    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "1")
    module = _import_dispatcher()
    priority = sys.modules["nfm_db.services.priority"]

    for term, prop_id, ontology_version_id, expected in _KNOWN_CORPUS:
        actual = module._priority_score(term, prop_id, ontology_version_id)
        canonical = priority.score(term, prop_id, ontology_version_id)
        assert actual == pytest.approx(canonical, abs=1e-9), (
            f"V2=True dispatcher diverged from priority.score for "
            f"({term!r}, {prop_id!r}, {ontology_version_id})"
        )
        assert actual == pytest.approx(expected, abs=1e-9), (
            f"V2=True expected mismatch for ({term!r}, {prop_id!r}, {ontology_version_id}): "
            f"expected {expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# AC #3 (second clause): both paths produce identical output for the
# known input corpus — proves equivalence during staged rollout.
# ---------------------------------------------------------------------------


def test_v2_false_output_equals_v2_true_output_for_known_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #3: legacy heuristic and priority.score agree to 1e-9 for every
    corpus entry. This is the staged-rollout equivalence contract.
    """
    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "0")
    module = _import_dispatcher()
    legacy_results: dict[tuple[str, str, int], float] = {
        (term, prop_id, ontology_version_id): module._priority_score(
            term, prop_id, ontology_version_id
        )
        for term, prop_id, ontology_version_id, _ in _KNOWN_CORPUS
    }

    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "1")
    module = _import_dispatcher()
    priority = sys.modules["nfm_db.services.priority"]
    for term, prop_id, ontology_version_id, _ in _KNOWN_CORPUS:
        new_result = module._priority_score(term, prop_id, ontology_version_id)
        legacy = legacy_results[(term, prop_id, ontology_version_id)]
        assert new_result == pytest.approx(legacy, abs=1e-9), (
            f"V2=True/V2=False divergence for ({term!r}, {prop_id!r}, {ontology_version_id}): "
            f"legacy={legacy}, new={new_result}"
        )
        canonical = priority.score(term, prop_id, ontology_version_id)
        assert new_result == pytest.approx(canonical, abs=1e-9)


# ---------------------------------------------------------------------------
# Determinism: same inputs always produce the same float (priority.score
# AC; mirrored here for the dispatcher surface).
# ---------------------------------------------------------------------------


def test_v2_dispatcher_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated calls with identical inputs return identical floats."""
    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "1")
    module = _import_dispatcher()
    first = module._priority_score("dna", "PROP:max", 3)
    for _ in range(5):
        assert module._priority_score("dna", "PROP:max", 3) == first


def test_v2_dispatcher_handles_unknown_ontology_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown ontology_version_id returns 0 for the ontology signal but
    still routes atf + citation through priority.score.
    Score = 0.4 * 0 + 0.3 * 1.0 + 0.3 * 1.0 = 0.6 (matches priority.ontology_weight).
    """
    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "1")
    module = _import_dispatcher()
    # dna → atf = 1.0; PROP:max → citation = 1.0; unknown ontology → 0.0
    # 0.4 * 0 + 0.3 * 1.0 + 0.3 * 1.0 = 0.6
    assert module._priority_score("dna", "PROP:max", 9999) == pytest.approx(0.6, abs=1e-9)


def test_v2_dispatcher_handles_empty_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty term returns 0 for the atf signal but still routes ontology +
    citation through priority.score.
    Score = 0.4 * 1.0 + 0.3 * 0 + 0.3 * 1.0 = 0.7 (matches priority.atf).
    """
    monkeypatch.setenv("NFM_PRIORITY_V2_ENABLED", "1")
    module = _import_dispatcher()
    # ontology_version_id=3 → ontology = 1.0; "" → atf = 0; PROP:max → citation = 1.0
    # 0.4 * 1.0 + 0.3 * 0 + 0.3 * 1.0 = 0.7
    assert module._priority_score("", "PROP:max", 3) == pytest.approx(0.7, abs=1e-9)
