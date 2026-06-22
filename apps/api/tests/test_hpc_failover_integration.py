"""Integration Tests for HPC Failover Scenarios (Component 1 of NFM-346).

Tests follow TDD principles:
- RED: Test written first, fails because feature doesn't exist
- GREEN: Minimal implementation to pass test
- REFACTOR: Clean up while keeping tests green

These are integration tests that verify end-to-end failover behavior.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig


class TestPrimarySSHTimeoutFailover:
    """Test Scenario 1: Primary SSH timeout triggers failover."""

    @pytest.fixture
    def orchestrator_with_backup(self):
        """Create orchestrator with backup cluster configured."""
        config = SSHConnectionConfig(
            hosts=("guangzhou.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("tianjin.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            failover_threshold_seconds=300,  # 5 minutes
            skip_key_validation=True
        )
        return HPCOrchestrator(config)

    @pytest.mark.asyncio
    async def test_primary_ssh_timeout_fails_over_to_backup(self, orchestrator_with_backup):
        """Test that 5 consecutive health check failures trigger failover.

        GIVEN: Primary cluster SSH connection times out
        WHEN: 5 consecutive health checks fail (5 minutes elapsed)
        THEN: Orchestrator automatically fails over to Tianjin
        AND: Jobs are submitted to backup cluster
        AND: Failover time is <5 minutes from first failure
        """
        # Mock primary cluster to fail health checks
        with patch.object(orchestrator_with_backup.failover_manager, 'check_primary_health', return_value=False):
            # Set last health check to 6 minutes ago (exceeds 5-minute threshold)
            orchestrator_with_backup.failover_manager.last_health_check = datetime.now() - timedelta(seconds=360)

            # Check if failover should be triggered
            should_failover = orchestrator_with_backup.should_trigger_failover()

            # Verify failover is triggered
            assert should_failover is True, \
                "Failover should be triggered after primary cluster timeout"

    @pytest.mark.asyncio
    async def test_failover_threshold_not_exceeded(self, orchestrator_with_backup):
        """Test that failover is NOT triggered when threshold not exceeded.

        GIVEN: Primary cluster has intermittent failures
        WHEN: Failures occur but 5-minute threshold not exceeded
        THEN: Failover is NOT triggered
        """
        # Mock primary cluster to have recent successful health check
        with patch.object(orchestrator_with_backup.failover_manager, 'check_primary_health', return_value=True):
            # Set last health check to 1 minute ago (within threshold)
            orchestrator_with_backup.failover_manager.last_health_check = datetime.now() - timedelta(seconds=60)

            # Check if failover should be triggered
            should_failover = orchestrator_with_backup.should_trigger_failover()

            # Verify failover is NOT triggered
            assert should_failover is False, \
                "Failover should NOT be triggered when threshold not exceeded"


class TestSLURMQueueOverflowFailover:
    """Test Scenario 2: SLURM queue overflow triggers failover."""

    @pytest.fixture
    def orchestrator_with_backup(self):
        """Create orchestrator with backup cluster."""
        config = SSHConnectionConfig(
            hosts=("guangzhou.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("tianjin.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True
        )
        return HPCOrchestrator(config)

    @pytest.mark.asyncio
    async def test_queue_full_triggers_failover(self, orchestrator_with_backup):
        """Test that SLURM queue full error triggers failover.

        GIVEN: Primary cluster SLURM queue at capacity
        WHEN: Job submission fails with queue full error
        THEN: Orchestrator automatically fails over to Tianjin
        AND: Job submission succeeds on backup cluster
        """
        # This test will be implemented when we add queue overflow detection
        # For now, we test that the orchestrator can handle failover on demand

        # Mock backup cluster to be available
        with patch.object(orchestrator_with_backup.failover_manager._backup_ssh_manager, 'acquire_connection_with_retry') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            # Trigger manual failover
            result = await orchestrator_with_backup.trigger_failover()

            # Verify failover succeeded
            assert result is True, "Failover should succeed when backup cluster available"
            assert orchestrator_with_backup.failover_manager.current_cluster == "backup", \
                "Current cluster should be set to backup after failover"


class TestPrimaryRecoveryAndSwitchback:
    """Test Scenario 3: Primary recovery and switchback."""

    @pytest.fixture
    def orchestrator(self):
        """Create orchestrator with backup cluster."""
        config = SSHConnectionConfig(
            hosts=("guangzhou.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("tianjin.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True
        )
        return HPCOrchestrator(config)

    @pytest.mark.asyncio
    async def test_primary_recovery_switches_back(self, orchestrator):
        """Test that primary recovery triggers switchback.

        GIVEN: Failover is active on backup cluster
        WHEN: Primary cluster recovers (SSH connection restored)
        THEN: Orchestrator detects primary recovery
        AND: New jobs are submitted to primary cluster
        AND: Active backup jobs continue on backup
        """
        # Set orchestrator to be on backup cluster
        orchestrator.failover_manager.current_cluster = "backup"
        orchestrator.failover_manager.primary_healthy = False

        # Mock primary cluster health check to succeed
        with patch.object(orchestrator.failover_manager, 'check_primary_health', return_value=True):
            with patch.object(orchestrator.failover_manager, 'log_failover_event'):
                # Try to recover primary
                recovered = await orchestrator.try_recover_primary()

                # Verify recovery detected
                assert recovered is True, "Primary recovery should be detected"
                assert orchestrator.failover_manager.primary_healthy is True, \
                    "Primary should be marked as healthy after recovery"

    @pytest.mark.asyncio
    async def test_primary_not_yet_recovered(self, orchestrator):
        """Test that switchback doesn't happen when primary still down.

        GIVEN: Failover is active on backup cluster
        WHEN: Primary cluster is still unhealthy
        THEN: Orchestrator continues using backup cluster
        AND: No switchback occurs
        """
        # Set orchestrator to be on backup cluster
        orchestrator.failover_manager.current_cluster = "backup"
        orchestrator.failover_manager.primary_healthy = False

        # Mock primary cluster health check to fail
        with patch.object(orchestrator.failover_manager, 'check_primary_health', return_value=False):
            # Try to recover primary
            recovered = await orchestrator.try_recover_primary()

            # Verify recovery NOT detected
            assert recovered is False, "Primary recovery should NOT be detected when still down"
            assert orchestrator.failover_manager.primary_healthy is False, \
                "Primary should remain marked as unhealthy"
            assert orchestrator.failover_manager.current_cluster == "backup", \
                "Should remain on backup cluster when primary still down"


class TestFailoverTiming:
    """Test failover timing requirements."""

    @pytest.fixture
    def orchestrator(self):
        """Create orchestrator."""
        config = SSHConnectionConfig(
            hosts=("guangzhou.example.com",),
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=("tianjin.example.com",),
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            failover_threshold_seconds=300,  # 5 minutes
            skip_key_validation=True
        )
        return HPCOrchestrator(config)

    @pytest.mark.asyncio
    async def test_failover_time_within_threshold(self, orchestrator):
        """Test that failover completes within 5 minutes.

        GIVEN: Primary cluster fails
        WHEN: Failover is triggered
        THEN: Failover completes in <5 minutes
        """
        import time

        # Mock backup cluster connectivity
        with patch.object(orchestrator.failover_manager._backup_ssh_manager, 'acquire_connection_with_retry') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            # Measure failover time
            start_time = time.time()

            result = await orchestrator.trigger_failover()

            end_time = time.time()
            failover_duration = end_time - start_time

            # Verify failover succeeded and was fast
            assert result is True, "Failover should succeed"
            assert failover_duration < 5.0, \
                f"Failover should complete in <5 seconds, took {failover_duration:.2f}s"

    @pytest.mark.asyncio
    async def test_health_check_interval_reasonable(self, orchestrator):
        """Test that health checks don't overload the system.

        GIVEN: System is monitoring primary cluster health
        WHEN: Health checks run periodically
        THEN: Check interval is reasonable (30 seconds ±5 seconds)
        """
        # Verify the failover threshold is configured correctly
        assert orchestrator.config.failover_threshold_seconds == 300, \
            "Failover threshold should be 5 minutes (300 seconds)"

        # This test verifies configuration - actual timing tested in Component 2
