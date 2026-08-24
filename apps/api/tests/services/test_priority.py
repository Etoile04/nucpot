"""Tests for nfm_db.services.priority (NFM-3575 / NFM-3548-A).

This is the foundation module for the Phase 5.3 priority scoring refactor.
The functions must be deterministic and pure — no I/O, no clock reads, no
randomness.  Sibling tasks consume ``score()`` and ``combine()``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from nfm_db.services.priority import (
    DEFAULT_WEIGHTS,
    atf,
    citation_frequency,
    combine,
    ontology_weight,
    score,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
WEIGHTS_FIXTURE = FIXTURES_DIR / "priority_weights.json"


# ---------------------------------------------------------------------------
# ontology_weight
# ---------------------------------------------------------------------------


class TestOntologyWeight:
    """``ontology_weight(ontology_version_id) -> float`` in [0, 1]."""

    def test_unknown_ontology_version_id_returns_zero(self) -> None:
        # Unknown ids must map to 0.0 — never raise or return > 1.0.
        assert ontology_weight(0) == 0.0
        assert ontology_weight(-1) == 0.0
        assert ontology_weight(99_999_999) == 0.0

    def test_zero_reference_count_returns_zero(self) -> None:
        # An ontology version registered with zero references weights 0.
        assert ontology_weight(1) == 0.0

    def test_mid_range_reference_count(self) -> None:
        # id=2 is registered with 10 refs (see _ONTOLOGY_REFS); max=30
        # ⇒ 10/30 = 0.333... rounded to 4 decimals.
        assert ontology_weight(2) == pytest.approx(0.3333, abs=1e-4)

    def test_max_reference_count_returns_one(self) -> None:
        # id=3 has 30 refs (the max in the fixture) ⇒ normalized to 1.0.
        assert ontology_weight(3) == 1.0

    def test_result_is_bounded_to_unit_interval(self) -> None:
        for vid in range(-2, 6):
            value = ontology_weight(vid)
            assert 0.0 <= value <= 1.0, f"ontology_weight({vid})={value}"


# ---------------------------------------------------------------------------
# atf
# ---------------------------------------------------------------------------


class TestAtf:
    """``atf(term) -> float`` in [0, 1] (log-scale clamp)."""

    def test_empty_term_returns_zero(self) -> None:
        assert atf("") == 0.0
        assert atf("   ") == 0.0

    def test_unknown_term_returns_zero(self) -> None:
        # Term not in any curated corpus ⇒ 0.0
        assert atf("zzznotacorporaword") == 0.0

    def test_zero_frequency_term_returns_zero(self) -> None:
        # ``water`` is in the fixture with frequency 0 ⇒ clamps to 0.
        assert atf("water") == 0.0

    def test_mid_range_frequency(self) -> None:
        # ``cell`` has frequency 100; corpus size is 10_000 ⇒
        # log(1+100)/log(1+10_000) = log(101)/log(10001).
        expected = math.log(1 + 100) / math.log(1 + 10_000)
        assert atf("cell") == pytest.approx(expected, abs=1e-6)

    def test_max_frequency_clamps_to_one(self) -> None:
        # ``dna`` saturates the corpus (frequency == 10_000) ⇒ 1.0.
        assert atf("dna") == 1.0

    def test_result_is_bounded_to_unit_interval(self) -> None:
        for term in ("", "water", "cell", "dna", "zzznotacorporaword"):
            value = atf(term)
            assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# citation_frequency
# ---------------------------------------------------------------------------


class TestCitationFrequency:
    """``citation_frequency(prop_id) -> float`` in [0, 1] (log-scale clamp)."""

    def test_unknown_prop_id_returns_zero(self) -> None:
        assert citation_frequency("") == 0.0
        assert citation_frequency("not:a:prop") == 0.0

    def test_zero_citation_count_returns_zero(self) -> None:
        # ``PROP:zero`` registered with 0 citations ⇒ 0.0
        assert citation_frequency("PROP:zero") == 0.0

    def test_mid_range_citation_count(self) -> None:
        # ``PROP:mid`` has 50 citations; max in fixture is 5000 ⇒
        # log(1+50)/log(1+5000).
        expected = math.log(1 + 50) / math.log(1 + 5000)
        assert citation_frequency("PROP:mid") == pytest.approx(expected, abs=1e-6)

    def test_max_citation_count_clamps_to_one(self) -> None:
        # ``PROP:max`` saturates (5000 citations) ⇒ 1.0
        assert citation_frequency("PROP:max") == 1.0

    def test_result_is_bounded_to_unit_interval(self) -> None:
        for prop in ("", "PROP:zero", "PROP:mid", "PROP:max", "nope"):
            value = citation_frequency(prop)
            assert 0.0 <= value <= 1.0


# ---------------------------------------------------------------------------
# combine
# ---------------------------------------------------------------------------


class TestCombine:
    """``combine(signals, weights=None) -> float`` dot-product."""

    def test_default_weights_match_spec(self) -> None:
        # Default weights per AC: w_ontology=0.4, w_atf=0.3, w_citation=0.3
        assert DEFAULT_WEIGHTS == {
            "ontology": 0.4,
            "atf": 0.3,
            "citation": 0.3,
        }

    def test_combine_uses_default_weights_when_none(self) -> None:
        signals = {"ontology": 1.0, "atf": 1.0, "citation": 1.0}
        # 0.4 + 0.3 + 0.3 = 1.0
        assert combine(signals) == pytest.approx(1.0)

    def test_combine_with_custom_weights_override(self) -> None:
        # Custom weights injected via the ``weights`` arg.
        custom = {"ontology": 1.0, "atf": 0.0, "citation": 0.0}
        signals = {"ontology": 0.5, "atf": 0.7, "citation": 0.9}
        # 1.0 * 0.5 = 0.5
        assert combine(signals, weights=custom) == pytest.approx(0.5)

    def test_combine_zero_signals_yields_zero(self) -> None:
        zero_signals = {"ontology": 0.0, "atf": 0.0, "citation": 0.0}
        assert combine(zero_signals) == 0.0

    def test_combine_ignores_unknown_signal_keys(self) -> None:
        # Extra keys must not contribute — only the three weighted keys do.
        signals = {
            "ontology": 1.0,
            "atf": 1.0,
            "citation": 1.0,
            "rogue": 1.0,
        }
        assert combine(signals) == pytest.approx(1.0)

    def test_combine_result_is_bounded_to_unit_interval(self) -> None:
        signals = {"ontology": 1.0, "atf": 1.0, "citation": 1.0}
        # With default weights the max possible score is 1.0.
        assert combine(signals) <= 1.0 + 1e-9


class TestCombineWeightsEnvOverride:
    """``combine`` picks up ``NFM_PRIORITY_WEIGHTS`` via the settings layer."""

    def test_env_override_loads_fixture_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = json.loads(WEIGHTS_FIXTURE.read_text())
        monkeypatch.setenv("NFM_PRIORITY_WEIGHTS", json.dumps(payload))
        import importlib

        from nfm_db.services import priority as priority_module

        importlib.reload(priority_module)
        try:
            signals = {"ontology": 1.0, "atf": 0.0, "citation": 0.0}
            # With the fixture's w_ontology=0.7 and the others 0, only
            # the ontology signal contributes.
            assert priority_module.combine(signals) == pytest.approx(0.7)
            assert priority_module.DEFAULT_WEIGHTS["ontology"] == 0.7
        finally:
            monkeypatch.delenv("NFM_PRIORITY_WEIGHTS", raising=False)
            importlib.reload(priority_module)


# ---------------------------------------------------------------------------
# score (integration of all three signals)
# ---------------------------------------------------------------------------


class TestScore:
    """End-to-end ``score(term, prop_id, ontology_version_id)``."""

    def test_score_is_pure_and_stable_across_1000_calls(self) -> None:
        first = score(term="cell", prop_id="PROP:mid", ontology_version_id=2)
        for _ in range(1000):
            assert score(term="cell", prop_id="PROP:mid", ontology_version_id=2) == first

    def test_score_with_zero_signals_is_zero(self) -> None:
        # water has freq 0 ⇒ atf=0; PROP:zero has 0 cites ⇒ 0;
        # ontology_version_id=1 has 0 refs ⇒ 0.
        assert score(term="water", prop_id="PROP:zero", ontology_version_id=1) == 0.0

    def test_score_with_max_signals_is_one(self) -> None:
        # dna saturates ⇒ atf=1; PROP:max ⇒ 1; ontology_version_id=3 ⇒ 1.
        assert score(term="dna", prop_id="PROP:max", ontology_version_id=3) == 1.0


# ---------------------------------------------------------------------------
# Determinism contract — applies to every signal function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_name,args",
    [
        ("ontology_weight", (2,)),
        ("atf", ("cell",)),
        ("citation_frequency", ("PROP:mid",)),
        ("combine", ({"ontology": 0.5, "atf": 0.5, "citation": 0.5},)),
        ("score", ("cell", "PROP:mid", 2)),
    ],
)
def test_determinism_contract(fn_name: str, args: tuple) -> None:
    """Same inputs must always produce the same output (AC #5)."""
    from nfm_db.services import priority as priority_module

    fn = getattr(priority_module, fn_name)
    first = fn(*args)
    for _ in range(1000):
        assert fn(*args) == first
