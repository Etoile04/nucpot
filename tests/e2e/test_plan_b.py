"""Plan B E2E tests (AC-6): 1 hub + 2 resource nodes minimal topology.

If the full 5-node simulation encounters infrastructure limits,
Plan B validates the same core sync and partition logic at reduced scale.
"""

from __future__ import annotations

from typing import Any

import pytest

from nfm_node_client.offline_queue import OperationType
from nfm_node_client.vector_clock import ClockComparison, VectorClock

from .conftest import (
    SEED_HUB_ID,
    PLAN_B_RESOURCE_IDS,
    ScenarioReport,
    make_local_operation,
    make_remote_record,
)
from .network_partition import PartitionConfig, PartitionSimulator


# ===================================================================
# AC-6 Plan B: 1 hub + 2 resource nodes
# ===================================================================


class TestAC6PlanBNodeRegistration:
    """AC-6 Plan B: Two resource nodes register under hub."""

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_two_resource_nodes_register(
        self, async_client, seed_plan_b_nodes,
    ) -> None:
        """Both Plan B nodes register and appear in listing."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        data = resp.json()["data"]
        plan_b_names = {n["name"] for n in data["items"] if n["name"].startswith("plan-b-")}
        assert plan_b_names == {"plan-b-alpha", "plan-b-beta"}

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_plan_b_nodes_have_distinct_endpoints(
        self, async_client, seed_plan_b_nodes,
    ) -> None:
        """Plan B nodes have distinct API endpoints."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        items = resp.json()["data"]["items"]
        plan_b = [n for n in items if n["name"].startswith("plan-b-")]
        endpoints = {n["api_endpoint"] for n in plan_b}
        assert len(endpoints) == 2


class TestAC6PlanBSync:
    """AC-6 Plan B: Offline->reconnect sync with 2 nodes."""

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_offline_queue_and_sync(
        self, plan_b_sync_engines, plan_b_offline_queues, report_collector,
    ) -> None:
        """Plan B node queues operations offline, syncs on reconnect."""
        engine = plan_b_sync_engines[0]
        queue = plan_b_offline_queues[0]

        for i in range(5):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="data",
                entity_id=f"plan-b-entity-{i}",
                payload={"index": i},
            ))

        assert queue.size() == 5

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(PLAN_B_RESOURCE_IDS[0])),
        )
        sim.inject()

        result = await engine.incremental_sync()
        assert result.pushed == 5
        assert queue.size() == 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-6 Plan B: Offline queue -> sync on reconnect",
            acceptance_criteria="AC-6",
            passed=True,
            details={"pushed": result.pushed, "remaining": queue.size()},
        ))


class TestAC6PlanBPartition:
    """AC-6 Plan B: Network partition and recovery with 2 nodes."""

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_partition_blocks_fetch(
        self, plan_b_sync_engines, report_collector,
    ) -> None:
        """Partitioned Plan B node cannot fetch remote records."""
        engine = plan_b_sync_engines[1]

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(PLAN_B_RESOURCE_IDS[1])),
            remote_records=[
                make_remote_record(
                    entity_id="pb-remote-1",
                    source_node="hub",
                    counter=1,
                ),
            ],
        )
        sim.inject()
        sim.partition()

        result = await engine.full_sync()
        assert result.pulled == 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-6 Plan B: Partition blocks fetch",
            acceptance_criteria="AC-6",
            passed=True,
            details={"pulled": result.pulled},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_recovery_after_partition(
        self, plan_b_sync_engines, plan_b_offline_queues, report_collector,
    ) -> None:
        """Plan B node recovers from partition and syncs queued data."""
        engine = plan_b_sync_engines[0]
        queue = plan_b_offline_queues[0]

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(PLAN_B_RESOURCE_IDS[0])),
        )
        sim.inject()
        sim.partition(drop_push=True)

        for i in range(8):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="material",
                entity_id=f"pb-partition-mat-{i}",
            ))

        assert queue.size() == 8

        sim.reconnect()
        result = await engine.incremental_sync()
        assert result.pushed == 8
        assert queue.size() == 0
        assert result.success

        report_collector.add_report(ScenarioReport(
            scenario="AC-6 Plan B: Recovery after partition",
            acceptance_criteria="AC-6",
            passed=True,
            details={
                "queued": 8,
                "synced": result.pushed,
                "remaining": queue.size(),
                "success": result.success,
            },
        ))


class TestAC6PlanBConflictResolution:
    """AC-6 Plan B: Conflict resolution with vector clocks."""

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_vector_clock_concurrent_and_lww(
        self, report_collector,
    ) -> None:
        """Plan B: concurrent clocks detected, merge advances counters."""
        clock_a = VectorClock(node_id="pb-alpha", clocks={"pb-alpha": 2}, timestamp=5.0)
        clock_b = VectorClock(node_id="pb-beta", clocks={"pb-beta": 2}, timestamp=7.0)

        assert clock_a.compare(clock_b) == ClockComparison.CONCURRENT

        merged = clock_a.merge(clock_b)
        assert merged.clocks == {"pb-alpha": 2, "pb-beta": 2}

        report_collector.add_report(ScenarioReport(
            scenario="AC-6 Plan B: Vector clock conflict detection + merge",
            acceptance_criteria="AC-6",
            passed=True,
            details={"comparison": "concurrent", "merged": merged.clocks},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac6
    @pytest.mark.plan_b
    async def test_manual_merge_flagging_plan_b(
        self, report_collector,
    ) -> None:
        """Plan B: concurrent conflicts flagged for manual merge."""
        from nfm_node_client.conflict_resolver import ConflictResolver
        from nfm_node_client.vector_clock import VectorClock

        resolver = ConflictResolver()

        local_clock = VectorClock(node_id="pb-alpha", clocks={"pb-alpha": 1}, timestamp=3.0)
        remote_clock = VectorClock(node_id="pb-beta", clocks={"pb-beta": 1}, timestamp=4.0)

        conflicts = resolver.detect(
            entity_id="shared-pb-entity",
            local_clock=local_clock,
            remote_clock=remote_clock,
            local_data={"value": "alpha-version"},
            remote_data={"value": "beta-version"},
        )

        assert len(conflicts) == 1
        unresolved = resolver.flag_manual_merge(conflicts)
        assert len(unresolved) == 1

        report_collector.add_report(ScenarioReport(
            scenario="AC-6 Plan B: Manual merge flagging",
            acceptance_criteria="AC-6",
            passed=True,
            details={
                "conflicts": len(conflicts),
                "unresolved": len(unresolved),
            },
        ))
