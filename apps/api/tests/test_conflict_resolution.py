"""Tests for nfm_db.services.conflict_resolution (NFM-839 B3.2)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nfm_db.models.conflict import ResolutionStrategy
from nfm_db.services.conflict_resolution import (
    Candidate,
    ConflictResolver,
    _as_candidates,
    _build_result,
)


def _make_entry(
    value: float = 100.0,
    source_id: str = "src-1",
    confidence: float = 0.9,
    extracted_at: str | datetime = "2025-01-01T00:00:00+00:00",
) -> dict:
    if isinstance(extracted_at, datetime):
        extracted_at = extracted_at.isoformat()
    return {
        "value": value,
        "source_id": source_id,
        "confidence": confidence,
        "extracted_at": extracted_at,
    }


class TestCandidate:
    def test_value_property(self) -> None:
        c = Candidate(_make_entry(value=42.5))
        assert c.value == 42.5

    def test_source_id_property(self) -> None:
        c = Candidate(_make_entry(source_id="abc"))
        assert c.source_id == "abc"

    def test_confidence_property(self) -> None:
        c = Candidate(_make_entry(confidence=0.75))
        assert c.confidence == 0.75

    def test_extracted_at_from_string(self) -> None:
        c = Candidate(_make_entry(extracted_at="2025-06-15T12:00:00+00:00"))
        assert isinstance(c.extracted_at, datetime)

    def test_extracted_at_from_datetime(self) -> None:
        dt = datetime(2025, 6, 15, 12, 0, tzinfo=UTC)
        c = Candidate(_make_entry(extracted_at=dt))
        assert c.extracted_at == dt

    def test_extracted_at_invalid_type_raises(self) -> None:
        c = Candidate({"value": 1, "source_id": "s", "confidence": 0.5, "extracted_at": 42})
        with pytest.raises(ValueError, match="extracted_at must be"):
            _ = c.extracted_at

    def test_frozen(self) -> None:
        c = Candidate(_make_entry())
        with pytest.raises(AttributeError):
            c.entry = {}  # type: ignore[misc]


class TestHelpers:
    def test_as_candidates_converts(self) -> None:
        entries = [_make_entry(value=1), _make_entry(value=2)]
        result = _as_candidates(entries)
        assert len(result) == 2
        assert result[0].value == 1

    def test_build_result_structure(self) -> None:
        c = Candidate(_make_entry(value=99.0, source_id="s1", confidence=0.8))
        result = _build_result(c, "newest", "test reason")
        assert result["value"] == 99.0
        assert result["resolution_reason"] == "test reason"
        assert result["source_id"] == "s1"
        assert result["confidence"] == 0.8
        assert result["strategy"] == "newest"


class TestConflictResolver:
    def setup_method(self) -> None:
        self.resolver = ConflictResolver()

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="empty candidate list"):
            self.resolver.resolve([], strategy=ResolutionStrategy.NEWEST)

    def test_manual_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Manual strategy"):
            self.resolver.resolve([_make_entry()], strategy=ResolutionStrategy.MANUAL)

    def test_newest_picks_latest(self) -> None:
        entries = [
            _make_entry(value=10, extracted_at="2025-01-01T00:00:00+00:00"),
            _make_entry(value=20, extracted_at="2025-06-01T00:00:00+00:00"),
            _make_entry(value=30, extracted_at="2025-03-01T00:00:00+00:00"),
        ]
        result = self.resolver.resolve(entries, strategy=ResolutionStrategy.NEWEST)
        assert result["value"] == 20
        assert result["strategy"] == "newest"

    def test_newest_reason_includes_timestamp(self) -> None:
        entries = [_make_entry(extracted_at="2025-01-01T00:00:00+00:00")]
        result = self.resolver.resolve(entries, strategy=ResolutionStrategy.NEWEST)
        assert "2025-01" in result["resolution_reason"]

    def test_confidence_picks_highest(self) -> None:
        entries = [
            _make_entry(value=10, confidence=0.5),
            _make_entry(value=20, confidence=0.99),
            _make_entry(value=30, confidence=0.7),
        ]
        result = self.resolver.resolve(entries, strategy=ResolutionStrategy.CONFIDENCE)
        assert result["value"] == 20
        assert result["strategy"] == "confidence"

    def test_confidence_reason_includes_score(self) -> None:
        entries = [_make_entry(confidence=0.95)]
        result = self.resolver.resolve(entries, strategy=ResolutionStrategy.CONFIDENCE)
        assert "0.950" in result["resolution_reason"]

    def test_consensus_picks_mode(self) -> None:
        entries = [
            _make_entry(value=100, confidence=0.3),
            _make_entry(value=100, confidence=0.5),
            _make_entry(value=200, confidence=0.9),
        ]
        result = self.resolver.resolve(entries, strategy=ResolutionStrategy.CONSENSUS)
        assert result["value"] == 100
        assert "freq=2" in result["resolution_reason"]

    def test_consensus_tie_breaks_by_confidence(self) -> None:
        entries = [
            _make_entry(value=100, confidence=0.3),
            _make_entry(value=200, confidence=0.9),
        ]
        result = self.resolver.resolve(entries, strategy=ResolutionStrategy.CONSENSUS)
        assert result["value"] == 200
        assert "tie broken by confidence" in result["resolution_reason"]

    def test_single_candidate_any_strategy(self) -> None:
        entry = _make_entry(value=42)
        for strat in (ResolutionStrategy.NEWEST, ResolutionStrategy.CONFIDENCE, ResolutionStrategy.CONSENSUS):
            result = self.resolver.resolve([entry], strategy=strat)
            assert result["value"] == 42
