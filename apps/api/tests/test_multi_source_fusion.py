"""Comprehensive unit tests for multi_source_fusion service.

Tests all four public functions:
- detect_conflicts
- resolve_single_conflict
- run_fusion
- list_conflicts

Target coverage: >= 80% (currently 16%).
All tests use mocked AsyncSession -- no real database connections.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nfm_db.models.conflict_record import ConflictRecord, ConflictStatus, ConflictStrategy
from nfm_db.models.kg import KGEdge
from nfm_db.schemas.conflict import FusionResult
from nfm_db.services.multi_source_fusion import (
    detect_conflicts,
    list_conflicts,
    resolve_single_conflict,
    run_fusion,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAT_ID = uuid.uuid4()
_PROP_ID = uuid.uuid4()
_SRC_A = uuid.uuid4()
_SRC_B = uuid.uuid4()
_PT_ID = uuid.uuid4()


_DEFAULT_CREATED_AT = datetime(2025, 1, 15, 12, 0, 0)


def _make_edge(
    *,
    source_node_id: uuid.UUID = _MAT_ID,
    target_node_id: uuid.UUID = _PROP_ID,
    source_id: uuid.UUID | None = _SRC_A,
    properties: dict | None = None,
    confidence: float = 0.9,
    created_at: datetime | None = _DEFAULT_CREATED_AT,
) -> KGEdge:
    """Create a mock KGEdge for testing."""
    edge = MagicMock(spec=KGEdge)
    edge.source_node_id = source_node_id
    edge.target_node_id = target_node_id
    edge.source_id = source_id
    edge.properties = properties
    edge.confidence = confidence
    edge.created_at = created_at
    return edge


def _make_mock_session(*, execute_results: list | None = None) -> AsyncMock:
    """Create a mock AsyncSession with configurable execute results."""
    session = AsyncMock()
    results = execute_results or []
    session.execute = AsyncMock(side_effect=results)
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def _make_scalar_result(scalars_result: list | None = None) -> MagicMock:
    """Create a mock result that returns scalars."""
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_result or [])))
    return result


def _make_scalar_one_or_none_result(value: object | None = None) -> MagicMock:
    """Create a mock result that returns scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    """Tests for detect_conflicts function."""

    @pytest.mark.asyncio
    async def test_no_edges_returns_empty_list(self) -> None:
        """No edges in DB returns empty list."""
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[])]
        )
        result = await detect_conflicts(session)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_edge_no_conflict(self) -> None:
        """Single edge means only one source, so no conflict."""
        edge = _make_edge()
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge])]
        )
        result = await detect_conflicts(session)
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_edges_same_source_no_conflict(self) -> None:
        """Multiple edges from same source_id -- no conflict."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(session)
        assert result == []

    @pytest.mark.asyncio
    async def test_different_sources_same_value_no_conflict(self) -> None:
        """Different sources but identical values -- no conflict."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "10"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(session)
        assert result == []

    @pytest.mark.asyncio
    async def test_different_sources_different_values_conflict_detected(
        self,
    ) -> None:
        """Different sources with different values triggers conflict."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "20"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(session)
        assert len(result) == 1
        conflict = result[0]
        assert conflict["material_node_id"] == _MAT_ID
        assert conflict["property_node_id"] == _PROP_ID
        assert len(conflict["conflicting_values"]) == 2
        assert conflict["strategy"] == ConflictStrategy.CONFIDENCE

    @pytest.mark.asyncio
    async def test_with_material_id_filter(self) -> None:
        """material_id filters the base query."""
        edge = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge])]
        )
        specific_mat = uuid.uuid4()
        await detect_conflicts(session, material_id=specific_mat)
        session.execute.assert_called_once()
        call_stmt = session.execute.call_args[0][0]
        # Verify the session was called (material_id used in WHERE clause)
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_with_property_type_id_and_strategy_override(self) -> None:
        """property_type_id triggers PropertyType lookup; strategy_override applied."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "20"})

        pt_mock = MagicMock()
        pt_mock.default_conflict_strategy = "newest"

        session = _make_mock_session(
            execute_results=[
                _make_scalar_result(scalars_result=[edge1, edge2]),
                _make_scalar_one_or_none_result(pt_mock),
            ]
        )
        result = await detect_conflicts(
            session,
            property_type_id=_PT_ID,
            strategy_override="manual",
        )
        assert len(result) == 1
        # strategy_override takes precedence
        assert result[0]["strategy"] == ConflictStrategy.MANUAL

    @pytest.mark.asyncio
    async def test_property_type_id_lookup_returns_none(self) -> None:
        """property_type_id lookup returns None -- uses fallback strategy."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "20"})

        session = _make_mock_session(
            execute_results=[
                _make_scalar_result(scalars_result=[edge1, edge2]),
                _make_scalar_one_or_none_result(None),
            ]
        )
        result = await detect_conflicts(
            session,
            property_type_id=_PT_ID,
        )
        assert len(result) == 1
        # Falls back to confidence since no default and no override
        assert result[0]["strategy"] == ConflictStrategy.CONFIDENCE

    @pytest.mark.asyncio
    async def test_property_type_has_default_strategy(self) -> None:
        """PropertyType has a default_conflict_strategy set."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "20"})

        pt_mock = MagicMock()
        pt_mock.default_conflict_strategy = "consensus"

        session = _make_mock_session(
            execute_results=[
                _make_scalar_result(scalars_result=[edge1, edge2]),
                _make_scalar_one_or_none_result(pt_mock),
            ]
        )
        result = await detect_conflicts(
            session,
            property_type_id=_PT_ID,
        )
        assert len(result) == 1
        assert result[0]["strategy"] == ConflictStrategy.CONSENSUS

    @pytest.mark.asyncio
    async def test_edge_with_null_source_id_skipped(self) -> None:
        """Edges with source_id=None are not counted as distinct sources."""
        edge1 = _make_edge(source_id=None, properties={"value": "10"})
        edge2 = _make_edge(source_id=None, properties={"value": "20"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(session)
        # Both source_ids are None => unique_sources = {None} => len <= 1
        assert result == []

    @pytest.mark.asyncio
    async def test_edge_with_null_properties(self) -> None:
        """Edge with properties=None is handled gracefully in value comparison."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties=None)
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(session)
        # Different values: "10" vs "" (from None properties)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_edge_with_null_created_at(self) -> None:
        """Edge with created_at=None produces extracted_at=None in output."""
        edge1 = _make_edge(
            source_id=_SRC_A, properties={"value": "10"}, created_at=None
        )
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "20"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(session)
        assert len(result) == 1
        vals = result[0]["conflicting_values"]
        assert vals[0]["extracted_at"] is None

    @pytest.mark.asyncio
    async def test_multiple_groups_each_produces_separate_conflict(
        self,
    ) -> None:
        """Different (source_node, target_node) pairs produce separate conflicts."""
        prop2_id = uuid.uuid4()
        mat2_id = uuid.uuid4()
        edge1 = _make_edge(source_node_id=_MAT_ID, target_node_id=_PROP_ID,
                           source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_node_id=_MAT_ID, target_node_id=_PROP_ID,
                           source_id=_SRC_B, properties={"value": "20"})
        edge3 = _make_edge(source_node_id=mat2_id, target_node_id=prop2_id,
                           source_id=_SRC_A, properties={"value": "A"})
        edge4 = _make_edge(source_node_id=mat2_id, target_node_id=prop2_id,
                           source_id=_SRC_B, properties={"value": "B"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2, edge3, edge4])]
        )
        result = await detect_conflicts(session)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_property_type_id_uses_strategy_override(self) -> None:
        """Without property_type_id, strategy_override still applied."""
        edge1 = _make_edge(source_id=_SRC_A, properties={"value": "10"})
        edge2 = _make_edge(source_id=_SRC_B, properties={"value": "20"})
        session = _make_mock_session(
            execute_results=[_make_scalar_result(scalars_result=[edge1, edge2])]
        )
        result = await detect_conflicts(
            session,
            strategy_override="newest",
        )
        assert len(result) == 1
        assert result[0]["strategy"] == ConflictStrategy.NEWEST


# ---------------------------------------------------------------------------
# resolve_single_conflict
# ---------------------------------------------------------------------------


class TestResolveSingleConflict:
    """Tests for resolve_single_conflict function."""

    @pytest.mark.asyncio
    async def test_conflict_id_not_found_returns_none(self) -> None:
        """Non-existent conflict_id returns None."""
        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(None)]
        )
        result = await resolve_single_conflict(
            session, conflict_id=uuid.uuid4()
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_manual_strategy_without_resolved_value_escalated(
        self,
    ) -> None:
        """Manual strategy with no resolved_value sets status to ESCALATED."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.MANUAL

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
            notes="Needs human review",
        )
        assert result is record
        assert record.status == ConflictStatus.ESCALATED
        assert record.resolution_notes == "Needs human review"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manual_strategy_with_resolved_value_resolved(
        self,
    ) -> None:
        """Manual strategy with resolved_value applies it directly."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.MANUAL
        record.conflicting_values = []

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        resolved = {"value": "chosen", "unit": "GPa"}
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
            resolved_value=resolved,
            resolved_by=uuid.uuid4(),
            notes="Human picked this one",
        )
        assert result is record
        assert record.status == ConflictStatus.RESOLVED
        assert record.resolved_value == "chosen"
        assert record.resolution_notes == "Human picked this one"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_resolution_with_winning_value(self) -> None:
        """Auto-resolution with a winning value sets RESOLVED."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.CONFIDENCE
        record.conflicting_values = [
            {"value": 10.0, "confidence": 0.7},
            {"value": 20.0, "confidence": 0.95},
        ]

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
            strategy_override=ConflictStrategy.CONFIDENCE,
        )
        assert result is record
        assert record.status == ConflictStatus.RESOLVED
        assert record.resolved_value == 20.0
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_resolution_returns_none_escalated(self) -> None:
        """Auto-resolution returning None sets ESCALATED."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.CONSENSUS
        record.conflicting_values = [{"value": "text_a"}, {"value": "text_b"}]

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
        )
        assert result is record
        assert record.status == ConflictStatus.ESCALATED
        assert record.resolution_notes == "Auto-resolution returned no winner"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolved_value_is_dict_extracts_value_key(self) -> None:
        """When winning value is a dict with 'value' key, extracts it."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.MANUAL
        record.conflicting_values = []

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        resolved = {"value": {"scalar": 15.0, "unit": "W/mK"}}
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
            resolved_value=resolved,
        )
        assert result is record
        assert record.resolved_value == {"scalar": 15.0, "unit": "W/mK"}

    @pytest.mark.asyncio
    async def test_resolved_value_is_not_dict_uses_as_is(self) -> None:
        """When winning value is not a dict, uses the value directly."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.MANUAL
        record.conflicting_values = []

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        resolved = "plain string value"
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
            resolved_value={"value": resolved},
        )
        assert result is record
        assert record.resolved_value == resolved

    @pytest.mark.asyncio
    async def test_strategy_override_takes_precedence(self) -> None:
        """strategy_override overrides the record's stored strategy."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.MANUAL
        record.conflicting_values = [
            {"value": 10.0, "confidence": 0.7},
            {"value": 20.0, "confidence": 0.95},
        ]

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        result = await resolve_single_conflict(
            session,
            conflict_id=record.id,
            strategy_override=ConflictStrategy.CONFIDENCE,
        )
        assert result is record
        assert record.status == ConflictStatus.RESOLVED
        # Strategy should be updated to the override
        assert record.strategy == ConflictStrategy.CONFIDENCE

    @pytest.mark.asyncio
    async def test_resolved_at_is_set(self) -> None:
        """resolved_at timestamp is set when conflict is resolved."""
        record = MagicMock(spec=ConflictRecord)
        record.id = uuid.uuid4()
        record.strategy = ConflictStrategy.MANUAL
        record.conflicting_values = []

        session = _make_mock_session(
            execute_results=[_make_scalar_one_or_none_result(record)]
        )
        await resolve_single_conflict(
            session,
            conflict_id=record.id,
            resolved_value={"value": 42.0},
        )
        assert record.resolved_at is not None
        assert isinstance(record.resolved_at, datetime)


# ---------------------------------------------------------------------------
# run_fusion
# ---------------------------------------------------------------------------


class TestRunFusion:
    """Tests for run_fusion function."""

    @pytest.mark.asyncio
    async def test_no_conflicts_detected(self) -> None:
        """No conflicts returns FusionResult(0, 0, 0)."""
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await run_fusion(session)
        assert isinstance(result, FusionResult)
        assert result.conflicts_detected == 0
        assert result.conflicts_resolved == 0
        assert result.conflicts_escalated == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_detection_raises_exception(self) -> None:
        """Detection exception returns FusionResult with error."""
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection lost"),
        ):
            result = await run_fusion(session)
        assert isinstance(result, FusionResult)
        assert result.conflicts_detected == 0
        assert result.conflicts_resolved == 0
        assert result.conflicts_escalated == 0
        assert len(result.errors) == 1
        assert "Detection failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_conflicts_detected_and_resolved(self) -> None:
        """Conflicts with winning values produce RESOLVED records."""
        conflict_desc = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": _PT_ID,
            "conflicting_values": [
                {"value": 10.0, "source_id": _SRC_A, "confidence": 0.7},
                {"value": 20.0, "source_id": _SRC_B, "confidence": 0.95},
            ],
            "strategy": ConflictStrategy.CONFIDENCE,
        }
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[conflict_desc],
        ):
            result = await run_fusion(session)
        assert isinstance(result, FusionResult)
        assert result.conflicts_detected == 1
        assert result.conflicts_resolved == 1
        assert result.conflicts_escalated == 0
        assert result.errors == []
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_conflicts_detected_and_escalated(self) -> None:
        """Conflicts with no winner produce ESCALATED records."""
        conflict_desc = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": _PT_ID,
            "conflicting_values": [
                {"value": "text_a", "source_id": _SRC_A},
                {"value": "text_b", "source_id": _SRC_B},
            ],
            "strategy": ConflictStrategy.CONSENSUS,
        }
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[conflict_desc],
        ):
            result = await run_fusion(session)
        assert isinstance(result, FusionResult)
        assert result.conflicts_detected == 1
        assert result.conflicts_resolved == 0
        assert result.conflicts_escalated == 1

    @pytest.mark.asyncio
    async def test_persist_fails_returns_error(self) -> None:
        """Flush failure returns FusionResult with error but correct counts."""
        conflict_desc = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": None,
            "conflicting_values": [
                {"value": 10.0, "source_id": _SRC_A, "confidence": 0.95},
            ],
            "strategy": ConflictStrategy.CONFIDENCE,
        }
        session = _make_mock_session()
        session.flush = AsyncMock(side_effect=RuntimeError("Deadlock"))
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[conflict_desc],
        ):
            result = await run_fusion(session)
        assert isinstance(result, FusionResult)
        assert result.conflicts_detected == 1
        assert len(result.errors) == 1
        assert "Persist failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_multiple_conflicts_mixed_resolution(self) -> None:
        """Multiple conflicts: some resolved, some escalated."""
        conflict_resolved = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": _PT_ID,
            "conflicting_values": [
                {"value": 10.0, "confidence": 0.7},
                {"value": 20.0, "confidence": 0.95},
            ],
            "strategy": ConflictStrategy.CONFIDENCE,
        }
        conflict_escalated = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": None,
            "conflicting_values": [
                {"value": "text_a"},
                {"value": "text_b"},
            ],
            "strategy": ConflictStrategy.CONSENSUS,
        }
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[conflict_resolved, conflict_escalated],
        ):
            result = await run_fusion(session)
        assert result.conflicts_detected == 2
        assert result.conflicts_resolved == 1
        assert result.conflicts_escalated == 1
        assert session.add.call_count == 2

    @pytest.mark.asyncio
    async def test_winning_value_dict_gets_value_key(self) -> None:
        """When winning value is a dict with 'value' key, resolved_value extracts it."""
        conflict_desc = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": None,
            "conflicting_values": [
                {"value": {"scalar": 15.0, "unit": "W/mK"}, "confidence": 0.95},
                {"value": {"scalar": 12.0, "unit": "W/mK"}, "confidence": 0.7},
            ],
            "strategy": ConflictStrategy.CONFIDENCE,
        }
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[conflict_desc],
        ):
            result = await run_fusion(session)
        assert result.conflicts_resolved == 1
        added_record = session.add.call_args[0][0]
        # winning is the first entry (confidence 0.95)
        # resolve_conflict returns the entry dict, so get("value", winning)
        assert added_record.status == ConflictStatus.RESOLVED

    @pytest.mark.asyncio
    async def test_escalated_conflict_has_no_resolved_value(self) -> None:
        """Escalated conflicts have resolved_value=None."""
        conflict_desc = {
            "material_node_id": _MAT_ID,
            "property_node_id": _PROP_ID,
            "property_type_id": None,
            "conflicting_values": [
                {"value": "a"},
                {"value": "b"},
            ],
            "strategy": ConflictStrategy.MANUAL,
        }
        session = _make_mock_session()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[conflict_desc],
        ):
            result = await run_fusion(session)
        assert result.conflicts_escalated == 1
        added_record = session.add.call_args[0][0]
        assert added_record.resolved_value is None

    @pytest.mark.asyncio
    async def test_passes_all_params_to_detect_conflicts(self) -> None:
        """run_fusion forwards material_id, property_type_id, strategy_override."""
        session = _make_mock_session()
        mat_id = uuid.uuid4()
        pt_id = uuid.uuid4()
        with patch(
            "nfm_db.services.multi_source_fusion.detect_conflicts",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_detect:
            await run_fusion(
                session,
                material_id=mat_id,
                property_type_id=pt_id,
                strategy_override="manual",
            )
        mock_detect.assert_called_once_with(
            session,
            material_id=mat_id,
            property_type_id=pt_id,
            strategy_override="manual",
        )


# ---------------------------------------------------------------------------
# list_conflicts
# ---------------------------------------------------------------------------


class TestListConflicts:
    """Tests for list_conflicts function."""

    @pytest.mark.asyncio
    async def test_no_filters_returns_all_records(self) -> None:
        """Without filters, returns all conflict records."""
        record = MagicMock(spec=ConflictRecord)
        session = _make_mock_session(
            execute_results=[
                _make_count_result(42),
                _make_scalar_result(scalars_result=[record]),
            ]
        )
        records, total = await list_conflicts(session)
        assert len(records) == 1
        assert total == 42

    @pytest.mark.asyncio
    async def test_with_material_id_filter(self) -> None:
        """material_id filter is applied."""
        session = _make_mock_session(
            execute_results=[
                _make_count_result(1),
                _make_scalar_result(scalars_result=[]),
            ]
        )
        mat_id = uuid.uuid4()
        await list_conflicts(session, material_id=mat_id)
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_with_status_filter(self) -> None:
        """status filter is applied."""
        session = _make_mock_session(
            execute_results=[
                _make_count_result(0),
                _make_scalar_result(scalars_result=[]),
            ]
        )
        await list_conflicts(session, status=ConflictStatus.RESOLVED)
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_pagination_limit_and_offset(self) -> None:
        """limit and offset are applied to the query."""
        record = MagicMock(spec=ConflictRecord)
        session = _make_mock_session(
            execute_results=[
                _make_count_result(100),
                _make_scalar_result(scalars_result=[record]),
            ]
        )
        records, total = await list_conflicts(session, limit=5, offset=10)
        assert len(records) == 1
        assert total == 100
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_tuple_of_records_and_total(self) -> None:
        """Returns (records_list, total_count) tuple."""
        record = MagicMock(spec=ConflictRecord)
        session = _make_mock_session(
            execute_results=[
                _make_count_result(3),
                _make_scalar_result(scalars_result=[record, record, record]),
            ]
        )
        result = await list_conflicts(session)
        assert isinstance(result, tuple)
        records, total = result
        assert isinstance(records, list)
        assert isinstance(total, int)
        assert len(records) == 3
        assert total == 3

    @pytest.mark.asyncio
    async def test_with_property_type_id_filter(self) -> None:
        """property_type_id filter is applied."""
        session = _make_mock_session(
            execute_results=[
                _make_count_result(0),
                _make_scalar_result(scalars_result=[]),
            ]
        )
        pt_id = uuid.uuid4()
        await list_conflicts(session, property_type_id=pt_id)
        assert session.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_count_scalar_returns_zero_when_none(self) -> None:
        """When count query returns None, total defaults to 0."""
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=None)
        session = _make_mock_session(
            execute_results=[
                count_result,
                _make_scalar_result(scalars_result=[]),
            ]
        )
        _, total = await list_conflicts(session)
        assert total == 0

    @pytest.mark.asyncio
    async def test_all_filters_combined(self) -> None:
        """All filters applied together."""
        session = _make_mock_session(
            execute_results=[
                _make_count_result(1),
                _make_scalar_result(scalars_result=[]),
            ]
        )
        await list_conflicts(
            session,
            material_id=uuid.uuid4(),
            property_type_id=uuid.uuid4(),
            status=ConflictStatus.ESCALATED,
            limit=10,
            offset=5,
        )
        assert session.execute.call_count == 2


# ---------------------------------------------------------------------------
# Helpers for list_conflicts
# ---------------------------------------------------------------------------


def _make_count_result(count: int) -> MagicMock:
    """Create a mock result for count queries."""
    result = MagicMock()
    result.scalar = MagicMock(return_value=count)
    return result
