"""Tests for conflict resolution engine strategies (NFM-861).

Covers:
- All 4 resolution strategies (newest, confidence, consensus, manual)
- Edge cases (empty entries, single entry, ties)
- Strategy validation
- get_strategy_for_property_type precedence logic
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nfm_db.models.conflict import ResolutionStrategy as ConflictStrategy
from nfm_db.services.conflict_resolver import (
    get_strategy_for_property_type,
    resolve_confidence,
    resolve_conflict,
    resolve_consensus,
    resolve_manual,
    resolve_newest,
    validate_strategy,
)

now = datetime(2026, 1, 15, 12, 0, 0)
later = datetime(2026, 1, 16, 12, 0, 0)
earliest = datetime(2026, 1, 14, 12, 0, 0)


def _make_entries(count: int = 2, **overrides: object) -> list[dict]:
    """Create test entries with default values.

    Each entry gets: value, source_id (UUID), confidence, extracted_at.
    """
    import uuid

    entries: list[dict] = []
    base_confidence = 0.8
    base_time = now

    for i in range(count):
        entry = {
            "value": {"value_scalar": 10.0 + i, "unit": "W/mK"},
            "source_id": uuid.uuid4(),
            "confidence": base_confidence - (i * 0.1),
            "extracted_at": base_time + timedelta(hours=i),
        }
        entries.append(entry)

    if overrides:
        for key, val in overrides.items():
            entries[0][key] = val

    return entries


# ============================================================
# Strategy Validation
# ============================================================


class TestValidateStrategy:
    """Strategy string validation tests."""

    def test_valid_newest(self) -> None:
        assert validate_strategy("newest") == "newest"

    def test_valid_confidence(self) -> None:
        assert validate_strategy("confidence") == "confidence"

    def test_valid_consensus(self) -> None:
        assert validate_strategy("consensus") == "consensus"

    def test_valid_manual(self) -> None:
        assert validate_strategy("manual") == "manual"

    def test_invalid_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            validate_strategy("invalid")

    def test_empty_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown conflict strategy"):
            validate_strategy("")


# ============================================================
# Strategy: newest
# ============================================================


class TestResolveNewest:
    """newest strategy tests."""

    def test_picks_most_recent(self) -> None:
        entries = _make_entries(3)
        entries[2]["extracted_at"] = later
        entries[1]["extracted_at"] = earliest
        result = resolve_newest(entries)
        assert result is not None
        assert result["extracted_at"] == later

    def test_empty_returns_none(self) -> None:
        assert resolve_newest([]) is None

    def test_single_entry_returns_it(self) -> None:
        entries = _make_entries(1)
        result = resolve_newest(entries)
        assert result is not None
        assert result["confidence"] == 0.8

    def test_missing_timestamps_first_wins(self) -> None:
        entries = _make_entries(2)
        entries[0]["extracted_at"] = None
        entries[1]["extracted_at"] = None
        result = resolve_newest(entries)
        assert result is not None
        assert result["value"]["value_scalar"] == 10.0

    def test_string_timestamps_parsed(self) -> None:
        entries = _make_entries(2)
        entries[0]["extracted_at"] = "2026-01-14T12:00:00"
        entries[1]["extracted_at"] = "2026-01-16T12:00:00"
        result = resolve_newest(entries)
        assert result is not None
        assert result["extracted_at"] == "2026-01-16T12:00:00"

    def test_invalid_string_timestamp_graceful(self) -> None:
        entries = _make_entries(2)
        entries[0]["extracted_at"] = "not-a-date"
        entries[1]["extracted_at"] = "2026-01-16T12:00:00"
        result = resolve_newest(entries)
        assert result is not None
        assert result["extracted_at"] == "2026-01-16T12:00:00"


# ============================================================
# Strategy: confidence
# ============================================================


class TestResolveConfidence:
    """confidence strategy tests."""

    def test_picks_highest_confidence(self) -> None:
        entries = _make_entries(3)
        entries[0]["confidence"] = 0.5
        entries[1]["confidence"] = 0.95
        entries[2]["confidence"] = 0.7
        result = resolve_confidence(entries)
        assert result is not None
        assert result["confidence"] == 0.95

    def test_empty_returns_none(self) -> None:
        assert resolve_confidence([]) is None

    def test_single_entry_returns_it(self) -> None:
        entries = _make_entries(1)
        result = resolve_confidence(entries)
        assert result is not None

    def test_tie_breaks_by_recency(self) -> None:
        entries = _make_entries(2)
        entries[0]["confidence"] = 0.9
        entries[1]["confidence"] = 0.9
        entries[0]["extracted_at"] = now
        entries[1]["extracted_at"] = later
        result = resolve_confidence(entries)
        assert result is not None
        assert result["extracted_at"] == later

    def test_zero_confidence_accepted(self) -> None:
        entries = _make_entries(1)
        entries[0]["confidence"] = 0.0
        result = resolve_confidence(entries)
        assert result is not None


# ============================================================
# Strategy: consensus
# ============================================================


class TestResolveConsensus:
    """consensus strategy tests."""

    def test_single_value_returns_it(self) -> None:
        entries = _make_entries(1)
        result = resolve_consensus(entries)
        assert result is not None
        assert result["value"]["value_scalar"] == 10.0

    def test_empty_returns_none(self) -> None:
        assert resolve_consensus([]) is None

    def test_two_values_picks_closest_to_mean(self) -> None:
        entries = _make_entries(2)
        entries[0]["value"] = {"value_scalar": 10.0, "unit": "W/mK"}
        entries[1]["value"] = {"value_scalar": 12.0, "unit": "W/mK"}
        result = resolve_consensus(entries)
        assert result is not None
        scalar = result["value"]["value_scalar"]
        assert scalar in (10.0, 12.0)

    def test_outlier_filtered(self) -> None:
        """IQR outlier detection filters extreme values."""
        entries = [
            {
                "value": {"value_scalar": 10.0, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
            {
                "value": {"value_scalar": 10.5, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
            {
                "value": {"value_scalar": 100.0, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
        ]
        result = resolve_consensus(entries)
        assert result is not None
        scalar = result["value"]["value_scalar"]
        assert scalar in (10.0, 10.5)

    def test_non_numeric_returns_none(self) -> None:
        entries = [
            {
                "value": {"text": "some text"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
        ]
        assert resolve_consensus(entries) is None

    def test_mixed_numeric_and_text(self) -> None:
        entries = [
            {
                "value": {"value_scalar": 10.0, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
            {
                "value": {"text": "unknown"},
                "source_id": None,
                "confidence": 0.7,
                "extracted_at": now,
            },
        ]
        result = resolve_consensus(entries)
        assert result is not None
        assert result["value"]["value_scalar"] == 10.0

    def test_three_values_uses_iqr_filter(self) -> None:
        entries = [
            {
                "value": {"value_scalar": 8.0, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
            {
                "value": {"value_scalar": 10.0, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
            {
                "value": {"value_scalar": 11.0, "unit": "W/mK"},
                "source_id": None,
                "confidence": 0.8,
                "extracted_at": now,
            },
        ]
        result = resolve_consensus(entries)
        assert result is not None
        scalar = result["value"]["value_scalar"]
        assert scalar in (8.0, 10.0, 11.0)


# ============================================================
# Strategy: manual
# ============================================================


class TestResolveManual:
    """manual strategy tests."""

    def test_always_returns_none(self) -> None:
        entries = _make_entries(3)
        assert resolve_manual(entries) is None

    def test_empty_also_returns_none(self) -> None:
        assert resolve_manual([]) is None


# ============================================================
# Dispatch: resolve_conflict
# ============================================================


class TestResolveConflict:
    """resolve_conflict dispatch tests."""

    def test_dispatches_newest(self) -> None:
        entries = _make_entries(2)
        result = resolve_conflict(entries, "newest")
        assert result is not None

    def test_dispatches_confidence(self) -> None:
        entries = _make_entries(2)
        result = resolve_conflict(entries, "confidence")
        assert result is not None

    def test_dispatches_consensus(self) -> None:
        entries = _make_entries(2)
        result = resolve_conflict(entries, "consensus")
        assert result is not None

    def test_dispatches_manual_returns_none(self) -> None:
        entries = _make_entries(2)
        assert resolve_conflict(entries, "manual") is None

    def test_invalid_strategy_raises(self) -> None:
        entries = _make_entries(2)
        with pytest.raises(ValueError):
            resolve_conflict(entries, "invalid_strategy")

    def test_empty_entries_returns_none(self) -> None:
        assert resolve_conflict([], "newest") is None


# ============================================================
# get_strategy_for_property_type
# ============================================================


class TestGetStrategyForPropertyType:
    """Strategy precedence logic tests."""

    def test_override_takes_precedence(self) -> None:
        result = get_strategy_for_property_type("newest", override="manual")
        assert result == "manual"

    def test_default_used_when_no_override(self) -> None:
        result = get_strategy_for_property_type("newest")
        assert result == "newest"

    def test_fallback_to_confidence(self) -> None:
        result = get_strategy_for_property_type(None)
        assert result == "confidence"

    def test_invalid_override_raises(self) -> None:
        with pytest.raises(ValueError):
            get_strategy_for_property_type("newest", override="bad")

    def test_invalid_default_raises(self) -> None:
        with pytest.raises(ValueError):
            get_strategy_for_property_type("bad_strategy")

    def test_both_none_gives_confidence(self) -> None:
        result = get_strategy_for_property_type(None, None)
        assert result == ConflictStrategy.CONFIDENCE
