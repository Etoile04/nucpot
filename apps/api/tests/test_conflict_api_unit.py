"""Unit tests for nfm_db.api.v1.conflict endpoints and helpers."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from nfm_db.api.v1.conflict import (
    VALID_STRATEGIES,
    _auto_resolve,
    _enrich_conflict,
)


def _make_record(
    *,
    material_id: uuid.UUID | None = None,
    property_type_id: uuid.UUID | None = None,
    source_values: list[dict] | None = None,
    resolution: str | None = None,
    resolved_value: str | None = None,
    resolved_at: datetime | None = None,
) -> MagicMock:
    record = MagicMock()
    record.id = uuid.uuid4()
    record.material_id = material_id or uuid.uuid4()
    record.property_type_id = property_type_id or uuid.uuid4()
    record.source_values = source_values or []
    record.resolution = resolution
    record.resolved_value = resolved_value
    record.resolved_at = resolved_at
    record.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return record


class TestAutoResolve:
    async def test_empty_values_returns_empty(self) -> None:
        record = _make_record(source_values=[])
        result = await _auto_resolve(record, "confidence")
        assert result == {}

    async def test_confidence_picks_highest(self) -> None:
        record = _make_record(source_values=[
            {"value": 10.0, "confidence": 0.5},
            {"value": 20.0, "confidence": 0.9},
            {"value": 15.0, "confidence": 0.7},
        ])
        result = await _auto_resolve(record, "confidence")
        assert result == {"value": 20.0, "confidence": 0.9}

    async def test_confidence_missing_confidence_defaults_zero(self) -> None:
        record = _make_record(source_values=[
            {"value": 10.0},
            {"value": 20.0, "confidence": 0.8},
        ])
        result = await _auto_resolve(record, "confidence")
        assert result["value"] == 20.0
        assert result["confidence"] == 0.8

    async def test_newest_picks_last(self) -> None:
        record = _make_record(source_values=[
            {"value": 10.0, "confidence": 0.9, "source_id": uuid.uuid4()},
            {"value": 20.0, "confidence": 0.5, "source_id": uuid.uuid4()},
            {"value": 15.0, "confidence": 0.7, "source_id": uuid.uuid4()},
        ])
        result = await _auto_resolve(record, "newest")
        assert result == {"value": 15.0, "confidence": 0.7}

    async def test_consensus_numeric_average(self) -> None:
        record = _make_record(source_values=[
            {"value": 10.0},
            {"value": 20.0},
            {"value": 30.0},
        ])
        result = await _auto_resolve(record, "consensus")
        assert result == {"value": 20.0}

    async def test_consensus_non_numeric_returns_first(self) -> None:
        record = _make_record(source_values=[
            {"value": "UO2"},
            {"value": "UO2"},
        ])
        result = await _auto_resolve(record, "consensus")
        # Non-numeric path returns values[0].get("value", {}) — just the value
        assert result == "UO2"

    async def test_consensus_mixed_numeric_and_non_numeric(self) -> None:
        record = _make_record(source_values=[
            {"value": "text"},
            {"value": 10.0},
            {"value": 20.0},
        ])
        result = await _auto_resolve(record, "consensus")
        assert result == {"value": 15.0}

    async def test_manual_returns_empty(self) -> None:
        record = _make_record(source_values=[{"value": 42}])
        result = await _auto_resolve(record, "manual")
        assert result == {}

    async def test_unknown_strategy_returns_empty(self) -> None:
        record = _make_record(source_values=[{"value": 42}])
        result = await _auto_resolve(record, "unknown")
        assert result == {}

    async def test_consensus_single_value(self) -> None:
        record = _make_record(source_values=[{"value": 42.0}])
        result = await _auto_resolve(record, "consensus")
        assert result == {"value": 42.0}


class TestEnrichConflict:
    async def test_enriches_with_material_and_property_type(self) -> None:
        record = _make_record()
        material = MagicMock()
        material.name = "UO2"
        prop_type = MagicMock()
        prop_type.name = "melting_point"
        source_id = uuid.uuid4()
        ds = MagicMock()
        ds.title = "Paper 1"
        record.source_values = [{"source_id": source_id, "value": 3138.0, "confidence": 0.95}]

        call_log: list[tuple] = []

        async def fake_get(model, pk):
            call_log.append((model.__name__, pk))
            if model.__name__ == "Material":
                return material
            if model.__name__ == "PropertyType":
                return prop_type
            if model.__name__ == "DataSource":
                return ds
            return None

        db = MagicMock()
        db.get = fake_get

        result = await _enrich_conflict(record, db)

        assert result.material_name == "UO2"
        assert result.property_type == "melting_point"
        assert len(result.source_values) == 1
        assert result.source_values[0].source_title == "Paper 1"
        assert result.source_values[0].value == 3138.0
        assert result.source_values[0].confidence == 0.95

    async def test_missing_source_id_no_lookup(self) -> None:
        record = _make_record()
        material = MagicMock()
        material.name = "UO2"
        prop_type = MagicMock()
        prop_type.name = "mp"
        record.source_values = [{"value": 100.0}]

        async def fake_get(model, pk):
            if model.__name__ == "Material":
                return material
            if model.__name__ == "PropertyType":
                return prop_type
            return None

        db = MagicMock()
        db.get = fake_get

        result = await _enrich_conflict(record, db)
        assert result.material_name == "UO2"
        assert result.source_values[0].source_title is None

    async def test_material_not_found(self) -> None:
        record = _make_record()

        async def fake_get(model, pk):
            return None

        db = MagicMock()
        db.get = fake_get

        result = await _enrich_conflict(record, db)
        assert result.material_name is None
        assert result.property_type is None


class TestValidStrategies:
    def test_contains_expected_strategies(self) -> None:
        assert "newest" in VALID_STRATEGIES
        assert "confidence" in VALID_STRATEGIES
        assert "consensus" in VALID_STRATEGIES
        assert "manual" in VALID_STRATEGIES

    def test_is_set(self) -> None:
        assert isinstance(VALID_STRATEGIES, set)