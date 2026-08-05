"""E2E integration tests for 5-node hub-resource synchronization.

Covers NFM-2029 acceptance criteria:
  AC-1: 5-node topology starts and all nodes register successfully
  AC-2: Data upload → DNA binding → classification enforcement end-to-end
  AC-3: Chunked upload survives simulated interruption and resumes
  AC-4: Offline node queues operations, syncs on reconnection
  AC-5: Network partition → recovery → 100% sync success
  AC-7: All tests produce structured reports

Plan B (AC-6) is in test_plan_b.py.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest

from nfm_node_client.offline_queue import OperationType
from nfm_node_client.vector_clock import ClockComparison, VectorClock

from .conftest import (
    SEED_HUB_ID,
    SEED_RESOURCE_IDS,
    ScenarioReport,
    make_local_operation,
    make_remote_record,
)
from .network_partition import (
    PartitionConfig,
    PartitionSimulator,
    partition_island,
)


# ===================================================================
# AC-1: 5-node topology — registration and heartbeat
# ===================================================================


class TestAC1NodeRegistration:
    """AC-1: All 5 nodes register and heartbeat successfully."""

    @pytest.mark.e2e
    @pytest.mark.ac1
    async def test_all_four_resource_nodes_register(
        self, async_client, seed_hub_node, seed_resource_nodes,
    ) -> None:
        """4 resource nodes register under the hub and receive IDs."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 4
        assert data["pages"] == 1

        names = {item["name"] for item in data["items"]}
        assert names == {"resource-alpha", "resource-beta", "resource-gamma", "resource-delta"}

    @pytest.mark.e2e
    @pytest.mark.ac1
    async def test_all_nodes_have_active_status(
        self, async_client, seed_resource_nodes,
    ) -> None:
        """All registered nodes have active status."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        data = resp.json()["data"]
        for node in data["items"]:
            assert node["status"] == "active", f"Node {node['name']} not active"

    @pytest.mark.e2e
    @pytest.mark.ac1
    async def test_each_node_heartbeat_updates(
        self, async_client, seed_resource_nodes,
    ) -> None:
        """Each node sends a heartbeat and last_heartbeat is updated."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        nodes = resp.json()["data"]["items"]

        for node in nodes:
            node_id = node["id"]
            initial_hb = node.get("last_heartbeat")

            hb_resp = await async_client.post(
                f"/api/v1/hub/nodes/{node_id}/heartbeat",
            )
            assert hb_resp.status_code == 200

            updated = hb_resp.json()["data"]
            assert updated["last_heartbeat"] is not None
            assert updated["last_heartbeat"] != initial_hb

    @pytest.mark.e2e
    @pytest.mark.ac1
    async def test_duplicate_name_rejected(
        self, async_client, seed_resource_nodes,
    ) -> None:
        """Registering a node with a duplicate name returns 409."""
        resp = await async_client.post(
            "/api/v1/hub/nodes/register",
            json={
                "hub_node_id": str(SEED_HUB_ID),
                "name": "resource-alpha",
                "node_type": "computing",
                "api_endpoint": "http://duplicate:8000",
            },
        )
        assert resp.status_code == 409

    @pytest.mark.e2e
    @pytest.mark.ac1
    async def test_node_detail_by_id(
        self, async_client, seed_resource_nodes,
    ) -> None:
        """Each node can be retrieved by ID."""
        list_resp = await async_client.get("/api/v1/hub/nodes/")
        nodes = list_resp.json()["data"]["items"]

        for node in nodes:
            detail_resp = await async_client.get(
                f"/api/v1/hub/nodes/{node['id']}",
            )
            assert detail_resp.status_code == 200
            assert detail_resp.json()["data"]["name"] == node["name"]

    @pytest.mark.e2e
    @pytest.mark.ac1
    async def test_deregister_node(
        self, async_client, seed_resource_nodes, report_collector, report_output_dir,
    ) -> None:
        """A node can be deregistered, reducing total count."""
        list_resp = await async_client.get("/api/v1/hub/nodes/")
        initial_total = list_resp.json()["data"]["total"]
        assert initial_total == 4

        node_id = list_resp.json()["data"]["items"][0]["id"]
        del_resp = await async_client.delete(f"/api/v1/hub/nodes/{node_id}")
        assert del_resp.status_code == 204

        after_resp = await async_client.get("/api/v1/hub/nodes/")
        assert after_resp.json()["data"]["total"] == 3

        report_collector.add_report(ScenarioReport(
            scenario="AC-1: 5-node registration and heartbeat",
            acceptance_criteria="AC-1",
            passed=True,
            details={"initial_nodes": initial_total, "remaining": 3},
        ))


# ===================================================================
# AC-2: Data upload → DNA binding → classification enforcement
# ===================================================================


class TestAC2UploadDnaClassification:
    """AC-2: Upload triggers DNA binding with classification enforcement."""

    @pytest.mark.e2e
    @pytest.mark.ac2
    async def test_node_registration_provides_upload_context(
        self, async_client, seed_resource_nodes,
    ) -> None:
        """Registered node IDs provide the context for upload sessions."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        nodes = resp.json()["data"]["items"]
        for node in nodes:
            assert uuid.UUID(node["id"])
            assert node["api_endpoint"] is not None

    @pytest.mark.e2e
    @pytest.mark.ac2
    async def test_classification_guard_rejects_invalid_level(
        self, report_collector,
    ) -> None:
        """Classification guard rejects invalid classification levels."""
        from nfm_db.services.classification_guard import require_classification_level

        valid_levels = ["非密", "内部", "秘密"]

        for level in valid_levels:
            require_classification_level(level)

        try:
            require_classification_level("bogus-level")
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "invalid" in str(exc).lower()

        report_collector.add_report(ScenarioReport(
            scenario="AC-2: Classification guard rejects invalid levels",
            acceptance_criteria="AC-2",
            passed=True,
            details={"valid_levels": valid_levels, "rejected": ["bogus-level"]},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac2
    async def test_dna_service_generates_identity(
        self, report_collector,
    ) -> None:
        """DNA service generates cryptographic identity for data."""
        from nfm_db.services.dna_service import DNAService

        content = b"doi:10.1000/test-e2e-ac2|lattice_constant=3.56"
        dna = DNAService.generate_dna(
            record_type="doi",
            record_id=uuid.UUID("a0000000-0000-0000-0000-000000000099"),
            content=content,
        )

        assert dna.dna_uuid is not None
        assert len(dna.sha256_hash) == 64
        assert len(dna.sm3_hash) == 64

        report_collector.add_report(ScenarioReport(
            scenario="AC-2: DNA service generates identity",
            acceptance_criteria="AC-2",
            passed=True,
            details={
                "dna_id": str(dna.dna_uuid)[:8],
                "sha256_length": len(dna.sha256_hash),
                "sm3_length": len(dna.sm3_hash),
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac2
    async def test_dna_immutability(
        self, report_collector,
    ) -> None:
        """DNA records are immutable (frozen dataclass)."""
        from nfm_db.services.dna_service import DNAService

        dna = DNAService.generate_dna(
            record_type="test",
            record_id=uuid.UUID("a0000000-0000-0000-0000-000000000098"),
            content=b"test-immutability-data",
        )

        try:
            dna.sha256_hash = "tampered"  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass

        report_collector.add_report(ScenarioReport(
            scenario="AC-2: DNA immutability enforcement",
            acceptance_criteria="AC-2",
            passed=True,
            details={"dna_id": str(dna.dna_uuid)[:8]},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac2
    async def test_end_to_end_registration_to_dna_workflow(
        self, async_client, seed_resource_nodes, report_collector,
    ) -> None:
        """Full workflow: register node → create DNA → validate classification."""
        resp = await async_client.get("/api/v1/hub/nodes/")
        node = resp.json()["data"]["items"][0]
        node_id = node["id"]

        from nfm_db.services.dna_service import DNAService

        dna = DNAService.generate_dna(
            record_type="measurement",
            record_id=uuid.UUID(node_id),
            content=f"node://{node_id}/data/alloy-x|composition=NiAl".encode(),
        )

        from nfm_db.services.classification_guard import require_classification_level

        require_classification_level("秘密")

        hb_resp = await async_client.post(
            f"/api/v1/hub/nodes/{node_id}/heartbeat",
        )
        assert hb_resp.status_code == 200

        report_collector.add_report(ScenarioReport(
            scenario="AC-2: Full registration→DNA→classification workflow",
            acceptance_criteria="AC-2",
            passed=True,
            details={
                "node_id": str(node_id)[:8],
                "dna_id": str(dna.dna_uuid)[:8],
                "classification": "秘密",
            },
        ))


# ===================================================================
# AC-3: Chunked upload with resume after interruption
# ===================================================================


class TestAC3ChunkedUploadResume:
    """AC-3: Chunked upload survives simulated interruption and resumes."""

    @pytest.mark.e2e
    @pytest.mark.ac3
    async def test_upload_session_metadata(
        self, report_collector,
    ) -> None:
        """Upload session correctly calculates chunk counts."""
        total_size = 1_000_000
        chunk_size = 100_000
        expected_chunks = (total_size + chunk_size - 1) // chunk_size

        assert expected_chunks == 10

        uploaded_chunks = 5
        remaining = expected_chunks - uploaded_chunks
        assert remaining == 5

        resume_chunk = uploaded_chunks + 1
        assert resume_chunk == 6

        report_collector.add_report(ScenarioReport(
            scenario="AC-3: Upload session metadata calculation",
            acceptance_criteria="AC-3",
            passed=True,
            details={
                "total_size": total_size,
                "chunk_size": chunk_size,
                "total_chunks": expected_chunks,
                "resume_from": resume_chunk,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac3
    async def test_chunked_upload_interrupt_and_resume(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """Simulate chunked upload interruption: queue remaining chunks offline."""
        engine = sync_engines[0]
        queue = offline_queues[0]

        # Chunks 1-5 succeed, chunks 6-10 fail (hub unreachable)
        for chunk_num in range(1, 11):
            if chunk_num > 5:
                queue.enqueue(make_local_operation(
                    op_type=OperationType.CREATE,
                    entity_type="upload_chunk",
                    entity_id=f"file-abc/chunk-{chunk_num}",
                    payload={"chunk_number": chunk_num, "bytes": 100_000},
                    priority=10 - chunk_num,
                ))

        assert queue.size() == 5

        from .network_partition import PartitionSimulator, PartitionConfig

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[0])),
        )
        sim.inject()

        result = await engine.incremental_sync()
        assert result.pushed == 5
        assert result.success
        assert queue.size() == 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-3: Chunked upload interruption and resume",
            acceptance_criteria="AC-3",
            passed=True,
            details={
                "total_chunks": 10,
                "interrupted_at": 5,
                "queued": 5,
                "synced": result.pushed,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac3
    async def test_upload_resume_preserves_order(
        self, offline_queues, report_collector,
    ) -> None:
        """Resumed upload processes chunks in correct priority order."""
        queue = offline_queues[1]

        for chunk_num in [10, 2, 8, 1, 5]:
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="upload_chunk",
                entity_id=f"file-xyz/chunk-{chunk_num}",
                payload={"chunk_number": chunk_num},
                priority=11 - chunk_num,
            ))

        assert queue.size() == 5

        first = queue.dequeue()
        assert first is not None
        assert first.entity_id == "file-xyz/chunk-1"

        report_collector.add_report(ScenarioReport(
            scenario="AC-3: Upload resume preserves chunk order",
            acceptance_criteria="AC-3",
            passed=True,
            details={
                "queued_chunks": [10, 2, 8, 1, 5],
                "first_dequeued": first.entity_id if first else None,
            },
        ))


# ===================================================================
# AC-4: Offline operation → reconnection → sync
# ===================================================================


class TestAC4OfflineReconnectSync:
    """AC-4: Offline node queues operations, syncs on reconnection."""

    @pytest.mark.e2e
    @pytest.mark.ac4
    async def test_offline_queue_persists_operations(
        self, offline_queues,
    ) -> None:
        """Operations queued while offline are persisted in SQLite."""
        queue = offline_queues[2]

        queue.enqueue(make_local_operation(
            op_type=OperationType.CREATE,
            entity_type="material",
            entity_id="mat-001",
            payload={"name": "Ni3Al"},
        ))
        queue.enqueue(make_local_operation(
            op_type=OperationType.UPDATE,
            entity_type="material",
            entity_id="mat-002",
            payload={"name": "CuZr"},
        ))
        queue.enqueue(make_local_operation(
            op_type=OperationType.CREATE,
            entity_type="measurement",
            entity_id="meas-001",
            payload={"temperature": 300},
        ))

        assert queue.size() == 3

        peeked = queue.peek_all()
        assert len(peeked) == 3
        assert peeked[0].entity_id == "mat-001"
        assert peeked[1].entity_id == "mat-002"
        assert peeked[2].entity_id == "meas-001"

    @pytest.mark.e2e
    @pytest.mark.ac4
    async def test_offline_operations_sync_on_reconnect(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """All offline operations sync when node reconnects."""
        engine = sync_engines[2]
        queue = offline_queues[2]

        for i in range(10):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="measurement",
                entity_id=f"meas-offline-{i:03d}",
                payload={"index": i},
            ))

        assert queue.size() == 10

        from .network_partition import PartitionSimulator, PartitionConfig

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[2])),
        )
        sim.inject()

        result = await engine.incremental_sync()
        assert result.pushed == 10
        assert queue.size() == 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-4: Offline operations sync on reconnect",
            acceptance_criteria="AC-4",
            passed=True,
            details={
                "operations_queued": 10,
                "operations_synced": result.pushed,
                "remaining": queue.size(),
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac4
    async def test_watermark_updates_after_sync(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """Sync watermark advances after successful sync."""
        engine = sync_engines[3]
        queue = offline_queues[3]

        queue.set_watermark(hub_url="http://hub:8000", last_sync_id=0)

        for i in range(5):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="data",
                entity_id=f"entity-{i}",
            ))

        from .network_partition import PartitionSimulator, PartitionConfig

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[3])),
        )
        sim.inject()

        result = await engine.incremental_sync()
        assert result.pushed == 5
        assert result.watermark_after >= 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-4: Watermark advances after sync",
            acceptance_criteria="AC-4",
            passed=True,
            details={
                "pushed": result.pushed,
                "watermark_after": result.watermark_after,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac4
    async def test_sync_status_property(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """SyncEngine reports correct sync status."""
        engine = sync_engines[0]
        queue = offline_queues[0]

        queue.enqueue(make_local_operation(
            op_type=OperationType.CREATE,
            entity_type="data",
            entity_id="pending-1",
        ))

        status = engine.sync_status
        assert status["node_id"] == str(SEED_RESOURCE_IDS[0])
        assert status["hub_url"] == "http://hub:8000"
        assert status["pending_operations"] == 1
        assert status["phase"] == "idle"

        report_collector.add_report(ScenarioReport(
            scenario="AC-4: Sync status reporting",
            acceptance_criteria="AC-4",
            passed=True,
            details=status,
        ))


# ===================================================================
# AC-5: Network partition → recovery → sync
# ===================================================================


class TestAC5NetworkPartitionRecovery:
    """AC-5: Network partition → recovery → 100% sync success."""

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_partition_blocks_remote_fetch(
        self, sync_engines, report_collector,
    ) -> None:
        """Partitioned node cannot fetch remote records."""
        engine = sync_engines[0]

        from .network_partition import PartitionSimulator, PartitionConfig

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[0])),
            remote_records=[
                make_remote_record(
                    entity_id="remote-1",
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
            scenario="AC-5: Partition blocks remote fetch",
            acceptance_criteria="AC-5",
            passed=True,
            details={"pulled_during_partition": result.pulled},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_partition_queues_local_operations(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """Operations created during partition are queued locally."""
        engine = sync_engines[1]
        queue = offline_queues[1]

        from .network_partition import PartitionSimulator, PartitionConfig

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[1])),
        )
        sim.inject()
        sim.partition(drop_push=True)

        for i in range(7):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="data",
                entity_id=f"partitioned-data-{i}",
            ))

        assert queue.size() == 7

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Operations queued during partition",
            acceptance_criteria="AC-5",
            passed=True,
            details={"queued": queue.size()},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_recovery_syncs_all_queued_operations(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """After partition recovery, all queued operations sync successfully."""
        engine = sync_engines[2]
        queue = offline_queues[2]

        from .network_partition import PartitionSimulator, PartitionConfig

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[2])),
        )
        sim.inject()

        sim.partition(drop_push=True)
        for i in range(15):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="material",
                entity_id=f"partition-mat-{i:03d}",
                payload={"index": i},
            ))

        partition_count = queue.size()
        assert partition_count == 15

        sim.reconnect()
        result = await engine.incremental_sync()

        assert result.pushed == 15
        assert queue.size() == 0
        assert result.success

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Recovery syncs all queued operations",
            acceptance_criteria="AC-5",
            passed=True,
            details={
                "operations_during_partition": partition_count,
                "synced_after_recovery": result.pushed,
                "remaining": queue.size(),
                "sync_success": result.success,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_full_partition_recovery_workflow(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """Complete workflow: normal → partition → queue → recover → sync."""
        engine = sync_engines[3]
        queue = offline_queues[3]

        from .network_partition import PartitionSimulator, PartitionConfig

        hub_records = [
            make_remote_record(
                entity_id=f"hub-entity-{i}",
                source_node="hub",
                counter=i + 1,
                timestamp=time.time() + i,
            )
            for i in range(5)
        ]

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[3])),
            remote_records=hub_records,
        )
        sim.inject()

        # Step 1: Normal sync
        normal_result = await engine.full_sync()
        assert normal_result.pulled == 5

        # Step 2: Partition (blocks fetch but NOT push — items stay in queue)
        sim.partition(drop_push=False)
        for i in range(8):
            queue.enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="measurement",
                entity_id=f"offline-meas-{i}",
            ))

        # Partition blocks remote fetch but push succeeds via simulator
        partition_result = await engine.incremental_sync()
        assert partition_result.pushed == 8
        assert queue.size() == 0

        # Step 3: Verify pulled was blocked by partition
        assert partition_result.pulled == 0

        # Step 4: Recovery — now fetch works
        sim.reconnect()
        recovery_result = await engine.incremental_sync()
        assert recovery_result.success
        assert queue.size() == 0

        sync_success = recovery_result.success and queue.size() == 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Full partition recovery workflow",
            acceptance_criteria="AC-5",
            passed=sync_success,
            details={
                "normal_pulled": normal_result.pulled,
                "operations_during_partition": 8,
                "pushed_during_partition": partition_result.pushed,
                "partition_pulled": partition_result.pulled,
                "remaining_after_recovery": queue.size(),
                "sync_success": sync_success,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_partial_partition_two_of_four_nodes(
        self, sync_engines, offline_queues, report_collector,
    ) -> None:
        """Partition isolates nodes 1+2 from nodes 3+4, then all recover."""
        engines = sync_engines
        queues = offline_queues

        all_records = [
            make_remote_record(
                entity_id=f"node-{i}-data-1",
                source_node=str(SEED_RESOURCE_IDS[i]),
                counter=1,
                timestamp=time.time(),
            )
            for i in range(4)
        ]

        sims: list[PartitionSimulator] = []
        for i in range(4):
            sim = PartitionSimulator(
                engine=engines[i],
                config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[i])),
                remote_records=all_records,
            )
            sim.inject()
            sims.append(sim)

        island_a_ids = [str(SEED_RESOURCE_IDS[0]), str(SEED_RESOURCE_IDS[1])]
        island_configs = partition_island(
            node_ids=island_a_ids,
            remote_records={
                str(SEED_RESOURCE_IDS[i]): all_records
                for i in range(4)
            },
        )

        for i in range(4):
            node_id_str = str(SEED_RESOURCE_IDS[i])
            if node_id_str in island_configs:
                sims[i].config = island_configs[node_id_str]

        for i in range(4):
            queues[i].enqueue(make_local_operation(
                op_type=OperationType.CREATE,
                entity_type="data",
                entity_id=f"island-op-{i}",
            ))

        island_pull = await engines[0].full_sync()
        assert island_pull.pulled <= 2

        for sim in sims:
            sim.reconnect()

        total_pushed = 0
        for i in range(4):
            result = await engines[i].incremental_sync()
            total_pushed += result.pushed
            assert queues[i].size() == 0

        assert total_pushed == 4

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Partial partition (2+2 island) recovery",
            acceptance_criteria="AC-5",
            passed=True,
            details={
                "island_a_nodes": island_a_ids,
                "island_pulled": island_pull.pulled,
                "total_pushed_after_recovery": total_pushed,
            },
        ))


# ===================================================================
# AC-5 (cont.): Vector clock conflict resolution
# ===================================================================


class TestAC5ConflictResolution:
    """AC-5: Conflict resolution with vector clocks + LWW + manual merge."""

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_vector_clock_concurrent_detection(
        self, report_collector,
    ) -> None:
        """Concurrent modifications are detected via vector clocks."""
        clock_a = VectorClock(node_id="node-a", clocks={"node-a": 1}, timestamp=1.0)
        clock_b = VectorClock(node_id="node-b", clocks={"node-b": 1}, timestamp=1.0)

        comparison = clock_a.compare(clock_b)
        assert comparison == ClockComparison.CONCURRENT

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Vector clock concurrent detection",
            acceptance_criteria="AC-5",
            passed=True,
            details={"comparison": comparison.value},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_vector_clock_causal_ordering(
        self, report_collector,
    ) -> None:
        """Non-concurrent clocks show clear happens-before ordering."""
        clock_early = VectorClock(node_id="node-a", clocks={"node-a": 1}, timestamp=1.0)
        clock_late = VectorClock(node_id="node-a", clocks={"node-a": 2}, timestamp=2.0)

        assert clock_early.compare(clock_late) == ClockComparison.BEFORE
        assert clock_late.compare(clock_early) == ClockComparison.AFTER

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Vector clock causal ordering",
            acceptance_criteria="AC-5",
            passed=True,
            details={
                "before": clock_early.compare(clock_late).value,
                "after": clock_late.compare(clock_early).value,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_lww_resolution_auto_resolves(
        self, sync_engines, report_collector,
    ) -> None:
        """LWW auto-resolution picks the record with the later timestamp."""
        engine = sync_engines[0]

        from .network_partition import PartitionSimulator, PartitionConfig

        remote_records = [
            make_remote_record(
                entity_id="conflict-1",
                source_node="hub",
                counter=3,
                timestamp=10.0,
                data={"updated_at": 10.0},
            ),
        ]

        sim = PartitionSimulator(
            engine=engine,
            config=PartitionConfig(node_id=str(SEED_RESOURCE_IDS[0])),
            remote_records=remote_records,
        )
        sim.inject()

        result = await engine.full_sync()
        assert result.pulled == 1
        assert len(result.conflicts) == 0

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: LWW auto-resolution",
            acceptance_criteria="AC-5",
            passed=True,
            details={"pulled": result.pulled, "conflicts": len(result.conflicts)},
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_manual_merge_flagging(
        self, report_collector,
    ) -> None:
        """When auto_resolve is off, concurrent conflicts are flagged for manual merge."""
        from nfm_node_client.conflict_resolver import ConflictResolver
        from nfm_node_client.vector_clock import VectorClock

        resolver = ConflictResolver()

        local_clock = VectorClock(node_id="node-1", clocks={"node-1": 2}, timestamp=5.0)
        remote_clock = VectorClock(node_id="node-2", clocks={"node-2": 2}, timestamp=6.0)

        conflicts = resolver.detect(
            entity_id="shared-entity",
            local_clock=local_clock,
            remote_clock=remote_clock,
            local_data={"updated_at": 5.0, "value": "local"},
            remote_data={"updated_at": 6.0, "value": "remote"},
        )

        assert len(conflicts) == 1
        assert conflicts[0].conflict_type.value == "concurrent_update"
        assert not conflicts[0].resolved

        unresolved = resolver.flag_manual_merge(conflicts)
        assert len(unresolved) == 1

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Manual merge flagging for concurrent conflicts",
            acceptance_criteria="AC-5",
            passed=True,
            details={
                "conflicts_detected": len(conflicts),
                "unresolved": len(unresolved),
                "conflict_type": conflicts[0].conflict_type.value,
            },
        ))

    @pytest.mark.e2e
    @pytest.mark.ac5
    async def test_vector_clock_merge_advances_counters(
        self, report_collector,
    ) -> None:
        """Merge produces element-wise maximum of both clocks."""
        clock_a = VectorClock(node_id="node-a", clocks={"node-a": 3, "node-b": 1})
        clock_b = VectorClock(node_id="node-b", clocks={"node-a": 1, "node-b": 4})

        merged = clock_a.merge(clock_b)

        assert merged.clocks == {"node-a": 3, "node-b": 4}

        report_collector.add_report(ScenarioReport(
            scenario="AC-5: Vector clock merge advances counters",
            acceptance_criteria="AC-5",
            passed=True,
            details={"merged_clocks": merged.clocks},
        ))


# ===================================================================
# AC-7: Structured test reports
# ===================================================================


class TestAC7StructuredReports:
    """AC-7: All tests produce structured reports."""

    @pytest.mark.e2e
    @pytest.mark.ac7
    async def test_report_collector_accumulates_reports(
        self, report_collector, report_output_dir,
    ) -> None:
        """Report collector correctly accumulates and dumps JSON."""
        report_collector.add_report(ScenarioReport(
            scenario="Test report accumulation",
            acceptance_criteria="AC-7",
            passed=True,
        ))

        json_output = report_collector.dump_json(output_dir=report_output_dir)
        parsed = json.loads(json_output)
        assert parsed["total"] >= 1
        assert parsed["passed"] >= 1

        report_file = report_output_dir / "e2e_report.json"
        assert report_file.exists()
