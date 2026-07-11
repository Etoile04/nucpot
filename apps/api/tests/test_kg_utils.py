"""Tests for shared KG utilities (NFM-1246).

Verifies the canonical ``parse_aliases`` behaviour after deduplicating
three divergent implementations across kg.py, kg_re.py, and
kg_lightrag_sync.py.
"""

from __future__ import annotations

import json

import pytest

from nfm_db.services.kg_utils import parse_aliases


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestParseAliasesHappyPath:
    """Valid inputs produce the expected ``list[str]``."""

    def test_valid_json_string_list(self) -> None:
        raw = json.dumps(["Uranium Dioxide", "UO2 fuel"])
        assert parse_aliases(raw) == ["Uranium Dioxide", "UO2 fuel"]

    def test_empty_json_list(self) -> None:
        assert parse_aliases("[]") == []

    def test_single_item_list(self) -> None:
        assert parse_aliases(json.dumps(["UO2"])) == ["UO2"]


# ---------------------------------------------------------------------------
# Coercion — the canonical behaviour that was missing in api/v1/kg.py
# ---------------------------------------------------------------------------


class TestParseAliasesCoercion:
    """Non-string items are coerced to ``str`` (canonical behaviour)."""

    def test_integer_coerced_to_str(self) -> None:
        raw = json.dumps([42, "UO2"])
        assert parse_aliases(raw) == ["42", "UO2"]

    def test_float_coerced_to_str(self) -> None:
        raw = json.dumps([3.14])
        assert parse_aliases(raw) == ["3.14"]

    def test_bool_coerced_to_str(self) -> None:
        raw = json.dumps([True, False])
        assert parse_aliases(raw) == ["True", "False"]

    def test_none_element_coerced_to_str(self) -> None:
        raw = json.dumps(["UO2", None])
        assert parse_aliases(raw) == ["UO2", "None"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestParseAliasesEdgeCases:
    """Graceful handling of unexpected inputs."""

    def test_none_returns_empty_list(self) -> None:
        assert parse_aliases(None) == []

    def test_empty_string_returns_empty_list(self) -> None:
        assert parse_aliases("") == []

    def test_invalid_json_returns_empty_list(self) -> None:
        assert parse_aliases("not-json") == []

    def test_json_string_scalar_returns_empty_list(self) -> None:
        """A JSON string (not a list) should return []."""
        assert parse_aliases(json.dumps("just a string")) == []

    def test_json_dict_returns_empty_list(self) -> None:
        """A JSON object (not a list) should return []."""
        assert parse_aliases(json.dumps({"key": "value"})) == []

    def test_json_number_returns_empty_list(self) -> None:
        assert parse_aliases("42") == []
