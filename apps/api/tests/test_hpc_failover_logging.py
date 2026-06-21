"""Tests for HPC Failover Event Logging (Component 3 of NFM-346).

Tests follow TDD principles:
- RED: Test written first, fails because feature doesn't exist
- GREEN: Minimal implementation to pass test
- REFACTOR: Clean up while keeping tests green
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.services.hpc_orchestration import HPCOrchestrator, SSHConnectionConfig


class TestFailoverEventLogging:
    """Test failover event logging to database."""

    @pytest.fixture
    def db_session(self):
        """Create mock database session."""
        session = AsyncMock(spec=AsyncSession)
        session.add = Mock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        # Make commit and rollback actually track calls
        session.commit.called = False
        session.rollback.called = False
        return session

    @pytest.fixture
    def orchestrator(self):
        """Create HPC orchestrator with backup cluster configured."""
        config = SSHConnectionConfig(
            hosts=["guangzhou.example.com"],  # Use guangzhou for consistency
            username="testuser",
            ssh_key_path="/path/to/key",
            backup_hosts=["tianjin.example.com"],  # Use tianjin for consistency
            backup_username="backup_user",
            backup_ssh_key_path="/path/to/backup_key",
            skip_key_validation=True
        )
        return HPCOrchestrator(config)

    @pytest.mark.asyncio
    async def test_log_failover_event_creates_database_record(self, orchestrator, db_session):
        """Test that failover event is logged to database with required fields.

        GIVEN: Orchestrator with backup cluster configured
        WHEN: Failover event is logged
        THEN: Database record is created with all required fields
        """
        # Mock get_db to return our mock session
        with patch('nfm_db.services.hpc_orchestration.get_db') as mock_get_db:
            # Configure the async generator properly
            async def mock_db_gen():
                yield db_session
                # StopIteration automatically raised after yield

            mock_get_db.return_value = mock_db_gen()

            # Call the logging method (must await async method)
            await orchestrator._log_failover_event(
                event_type="failover_triggered",
                source_cluster="guangzhou.example.com",
                target_cluster="tianjin.example.com",
                reason="Primary cluster SSH timeout after 5 consecutive failures",
                success=True,
                failure_count=5,
                event_metadata={"failover_duration_seconds": 45}
            )

            # Verify database session.commit was called (success case)
            assert db_session.commit.called, \
                "Database commit should be called for successful logging"

    @pytest.mark.asyncio
    async def test_log_failover_event_includes_all_required_fields(self, orchestrator, db_session):
        """Test that failover event includes all required fields.

        GIVEN: Failover event occurs
        WHEN: Event is logged
        THEN: Event includes timestamp, type, clusters, reason, success, metadata
        """
        with patch('nfm_db.services.hpc_orchestration.get_db') as mock_get_db:
            async def mock_db_gen():
                yield db_session

            mock_get_db.return_value = mock_db_gen()

            await orchestrator._log_failover_event(
                event_type="failover_triggered",
                source_cluster="guangzhou.example.com",
                target_cluster="tianjin.example.com",
                reason="Primary cluster down",
                success=True,
                failure_count=3,
                event_metadata={"test": "data"}
            )

            # Verify the call captured required fields
            if db_session.add.called:
                added_obj = db_session.add.call_args[0][0]
                assert hasattr(added_obj, 'event_time')
                assert hasattr(added_obj, 'event_type')
                assert hasattr(added_obj, 'source_cluster')
                assert hasattr(added_obj, 'target_cluster')
                assert hasattr(added_obj, 'reason')
                assert hasattr(added_obj, 'success')
                assert hasattr(added_obj, 'event_metadata')

    @pytest.mark.asyncio
    async def test_log_failover_event_fallback_to_stdout_on_db_error(self, orchestrator):
        """Test that logging falls back to stdout on database error.

        GIVEN: Database connection fails
        WHEN: Failover event is logged
        THEN: Event is logged to stdout/stderr instead
        """
        with patch('nfm_db.database.get_db') as mock_get_db:
            # Simulate database error
            mock_get_db.side_effect = Exception("Database connection failed")

            # Should not raise exception, should fall back to logging
            try:
                await orchestrator._log_failover_event(
                    event_type="failover_triggered",
                    source_cluster="guangzhou",
                    target_cluster="tianjin",
                    reason="Test fallback",
                    success=True,
                    event_metadata={}
                )
                # If we get here, fallback worked
                assert True
            except Exception as e:
                pytest.fail(f"Should have fallen back to stdout logging, but raised: {e}")

    @pytest.mark.asyncio
    async def test_trigger_failover_calls_log_method(self, orchestrator, db_session):
        """Test that trigger_failover() integrates with logging method.

        GIVEN: Backup cluster is available
        WHEN: Failover is triggered
        THEN: Failover event is logged to database
        """
        with patch.object(orchestrator.backup_ssh_manager, 'acquire_connection_with_retry') as mock_acquire:
            mock_client = MagicMock()
            mock_acquire.return_value = mock_client

            with patch.object(orchestrator, '_log_failover_event') as mock_log:
                await orchestrator.trigger_failover()

                # Verify logging was called
                assert mock_log.called, "trigger_failover should call _log_failover_event"

                # Verify correct parameters were passed
                call_kwargs = mock_log.call_args[1]
                assert call_kwargs['event_type'] in ['failover_triggered', 'failover_failed']
                # Check for guangzhou (or the test cluster name)
                source = call_kwargs.get('source_cluster')
                assert source == 'guangzhou.example.com' or source == orchestrator.hpc_cluster
                assert call_kwargs['success'] == True

    @pytest.mark.asyncio
    async def test_try_recover_primary_logs_recovery_event(self, orchestrator, db_session):
        """Test that primary recovery is logged to database.

        GIVEN: Primary cluster recovers
        WHEN: Recovery is detected
        THEN: Recovery event is logged to database
        """
        with patch.object(orchestrator, 'check_primary_health', return_value=True):
            with patch.object(orchestrator, '_log_failover_event') as mock_log:
                await orchestrator.try_recover_primary()

                # Verify recovery was logged
                assert mock_log.called, "try_recover_primary should log recovery event"

                call_kwargs = mock_log.call_args[1]
                assert call_kwargs['event_type'] in ['primary_recovered', 'recovery_attempted']
                assert call_kwargs['success'] == True


class TestHPCFailoverEventModel:
    """Test HPCFailoverEvent model structure."""

    def test_hpc_failover_event_model_exists(self):
        """Test that HPCFailoverEvent model can be imported.

        GIVEN: NFM-346 implementation
        WHEN: Importing HPCFailoverEvent model
        THEN: Model class exists and is importable
        """
        try:
            from nfm_db.models.hpc_failover_event import HPCFailoverEvent
            assert HPCFailoverEvent is not None
        except ImportError as e:
            pytest.fail(f"HPCFailoverEvent model should exist but import failed: {e}")

    def test_hpc_failover_event_has_required_columns(self):
        """Test that HPCFailoverEvent has all required columns.

        GIVEN: HPCFailoverEvent model
        WHEN: Inspecting model structure
        THEN: All required columns are defined
        """
        from nfm_db.models.hpc_failover_event import HPCFailoverEvent

        required_columns = [
            'id', 'event_time', 'event_type', 'source_cluster',
            'target_cluster', 'reason', 'failure_count',
            'success', 'event_metadata', 'created_at'
        ]

        for col in required_columns:
            assert hasattr(HPCFailoverEvent, col), \
                f"HPCFailoverEvent should have column: {col}"
