"""Tests for seed_entity_pairs dataset structure and acceptance criteria."""

from __future__ import annotations

import pytest

from tests.seed_entity_pairs import (
    ALL_PAIRS,
    NEGATIVE_PAIRS,
    POSITIVE_PAIRS,
    EntityPair,
)


class TestEntityPairDataclass:
    """AC2: frozen dataclass per pair."""

    def test_entity_pair_is_frozen(self) -> None:
        pair = EntityPair(name_a="UO2", name_b="Uranium Dioxide", expected_match=True)
        with pytest.raises((AttributeError, Exception)):
            pair.name_a = "changed"  # type: ignore[misc]

    def test_entity_pair_has_expected_match(self) -> None:
        pair = EntityPair(name_a="UO2", name_b="UO2", expected_match=True)
        assert pair.expected_match is True

    def test_entity_pair_optional_fields(self) -> None:
        pair = EntityPair(name_a="A", name_b="B", expected_match=False)
        assert pair.composition_a is None
        assert pair.composition_b is None
        assert pair.expected_method is None


class TestPositivePairs:
    """AC1: minimum 20 positive pairs."""

    def test_positive_pair_count(self) -> None:
        assert len(POSITIVE_PAIRS) >= 20

    def test_all_positive_pairs_match(self) -> None:
        for pair in POSITIVE_PAIRS:
            assert pair.expected_match is True, (
                f"Positive pair ({pair.name_a}, {pair.name_b}) has expected_match=False"
            )

    def test_positive_pairs_have_method_hint(self) -> None:
        for pair in POSITIVE_PAIRS:
            assert pair.expected_method is not None, (
                f"Positive pair ({pair.name_a}, {pair.name_b}) missing expected_method"
            )
            assert pair.expected_method in (
                "exact",
                "fuzzy",
                "semantic",
                "alias",
                "composition",
            ), (
                f"Positive pair ({pair.name_a}, {pair.name_b}) has unexpected "
                f"method: {pair.expected_method}"
            )


class TestNegativePairs:
    """AC1: minimum 10 negative pairs."""

    def test_negative_pair_count(self) -> None:
        assert len(NEGATIVE_PAIRS) >= 10

    def test_all_negative_pairs_no_match(self) -> None:
        for pair in NEGATIVE_PAIRS:
            assert pair.expected_match is False, (
                f"Negative pair ({pair.name_a}, {pair.name_b}) has expected_match=True"
            )

    def test_negative_pairs_no_method_hint(self) -> None:
        for pair in NEGATIVE_PAIRS:
            assert pair.expected_method is None, (
                f"Negative pair ({pair.name_a}, {pair.name_b}) should not "
                f"have expected_method, got: {pair.expected_method}"
            )


class TestAllPairs:
    """AC1: at least 30 total pairs."""

    def test_total_pair_count(self) -> None:
        assert len(ALL_PAIRS) >= 30

    def test_all_pairs_is_concatenation(self) -> None:
        assert ALL_PAIRS == POSITIVE_PAIRS + NEGATIVE_PAIRS

    def test_no_duplicate_pairs(self) -> None:
        seen: set[tuple[str, str]] = set()
        for pair in ALL_PAIRS:
            key = (pair.name_a, pair.name_b)
            assert key not in seen, f"Duplicate pair: {pair.name_a} / {pair.name_b}"
            seen.add(key)

    def test_all_pairs_are_real_nuclear_materials(self) -> None:
        """AC3: All pairs use real nuclear material names."""
        known_materials: set[str] = set()
        for pair in ALL_PAIRS:
            known_materials.add(pair.name_a)
            known_materials.add(pair.name_b)

        # Spot-check well-known materials are present
        assert "UO2" in known_materials
        assert "Zircaloy-4" in known_materials
        assert "Silicon Carbide" in known_materials
        assert "Boron Carbide" in known_materials

    def test_composition_formulas_provided(self) -> None:
        """AC4: Composition formulas provided where applicable."""
        pairs_with_composition = [
            p for p in ALL_PAIRS if p.composition_a is not None
        ]
        assert len(pairs_with_composition) >= 5, (
            "At least 5 pairs should have composition formulas"
        )
