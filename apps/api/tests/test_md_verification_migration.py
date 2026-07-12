"""Integration tests for MD verification database migration.

Tests the creation of 5 new tables for LAMMPS integration:
- md_verification_jobs
- hpc_jobs
- md_simulation_results
- defect_analysis_results
- potential_fitting_results
"""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def migration_db_session():
    """Create a PostgreSQL test database for migration testing.

    This fixture creates a fresh database for each test,
    applies migrations, and provides an async session.
    """
    # Create test database URL
    test_db_url = "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db_test"

    # Create async engine
    engine = create_async_engine(test_db_url, echo=False)

    # Create all tables via migration
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", test_db_url)

    # Run upgrade to head
    async with engine.begin() as conn:
        await conn.run_sync(lambda conn: command.upgrade(alembic_config, "head"))

    # Create session factory
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        yield session

    # Cleanup: downgrade and dispose
    async with engine.begin() as conn:
        await conn.run_sync(lambda conn: command.downgrade(alembic_config, "base"))

    await engine.dispose()


@pytest.mark.integration
class TestMDVerificationMigration:
    """Test MD verification migration creation and constraints."""

    @pytest.mark.asyncio
    async def test_md_verification_jobs_table_exists(self, migration_db_session):
        """Test that md_verification_jobs table was created with correct columns."""
        # Query to check table exists and has correct columns
        result = await migration_db_session.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'md_verification_jobs'
            ORDER BY ordinal_position;
        """))

        columns = {row[0]: {"type": row[1], "nullable": row[2]} for row in result}

        # Verify key columns exist
        assert "id" in columns
        assert "potential_id" in columns
        assert "element_system" in columns
        assert "config" in columns
        assert "status" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

        # Verify config column is JSONB
        assert columns["config"]["type"] == "jsonb"
        assert columns["config"]["nullable"] == "NO"

    @pytest.mark.asyncio
    async def test_hpc_jobs_table_exists(self, migration_db_session):
        """Test that hpc_jobs table was created with foreign key constraint."""
        # Query to check table exists
        result = await migration_db_session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'hpc_jobs'
            ORDER BY ordinal_position;
        """))

        columns = {row[0]: row[1] for row in result}

        # Verify foreign key column exists
        assert "verification_job_id" in columns

        # Query to check foreign key constraint exists
        fk_result = await migration_db_session.execute(text("""
            SELECT
                tc.constraint_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_name = 'hpc_jobs'
                AND kcu.column_name = 'verification_job_id';
        """))

        fk_info = fk_result.fetchone()
        assert fk_info is not None
        assert fk_info[2] == "md_verification_jobs"

    @pytest.mark.asyncio
    async def test_md_simulation_results_table_exists(self, migration_db_session):
        """Test that md_simulation_results table was created."""
        result = await migration_db_session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'md_simulation_results'
            ORDER BY ordinal_position;
        """))

        columns = {row[0]: row[1] for row in result}

        # Verify key columns exist
        assert "id" in columns
        assert "verification_job_id" in columns
        assert "trajectory_file_path" in columns
        assert "thermodynamic_data" in columns

        # Verify thermodynamic_data is JSONB
        assert columns["thermodynamic_data"] == "jsonb"

    @pytest.mark.asyncio
    async def test_defect_analysis_results_table_exists(self, migration_db_session):
        """Test that defect_analysis_results table was created."""
        result = await migration_db_session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'defect_analysis_results'
            ORDER BY ordinal_position;
        """))

        columns = {row[0]: row[1] for row in result}

        # Verify key columns exist
        assert "id" in columns
        assert "verification_job_id" in columns
        assert "defect_type" in columns
        assert "concentration" in columns
        assert "metadata" in columns

        # Verify metadata is JSONB
        assert columns["metadata"] == "jsonb"

    @pytest.mark.asyncio
    async def test_potential_fitting_results_table_exists(self, migration_db_session):
        """Test that potential_fitting_results table was created."""
        result = await migration_db_session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'potential_fitting_results'
            ORDER BY ordinal_position;
        """))

        columns = {row[0]: row[1] for row in result}

        # Verify key columns exist
        assert "id" in columns
        assert "verification_job_id" in columns
        assert "fitting_method" in columns
        assert "parameters" in columns
        assert "quality_metrics" in columns

        # Verify parameters and quality_metrics are JSONB
        assert columns["parameters"] == "jsonb"
        assert columns["quality_metrics"] == "jsonb"

    @pytest.mark.asyncio
    async def test_indexes_created(self, migration_db_session):
        """Test that all performance indexes were created."""
        result = await migration_db_session.execute(text("""
            SELECT indexname, tablename
            FROM pg_indexes
            WHERE indexname LIKE 'idx_%'
                AND tablename IN (
                    'md_verification_jobs',
                    'hpc_jobs',
                    'defect_analysis_results',
                    'potential_fitting_results'
                )
            ORDER BY indexname;
        """))

        indexes = {row[0]: row[1] for row in result}

        # Verify all indexes exist
        assert "idx_md_jobs_status" in indexes
        assert indexes["idx_md_jobs_status"] == "md_verification_jobs"

        assert "idx_md_jobs_potential" in indexes
        assert indexes["idx_md_jobs_potential"] == "md_verification_jobs"

        assert "idx_hpc_jobs_verification" in indexes
        assert indexes["idx_hpc_jobs_verification"] == "hpc_jobs"

        assert "idx_hpc_jobs_cluster" in indexes
        assert indexes["idx_hpc_jobs_cluster"] == "hpc_jobs"

        assert "idx_defect_results_job" in indexes
        assert indexes["idx_defect_results_job"] == "defect_analysis_results"

        assert "idx_fitting_results_job" in indexes
        assert indexes["idx_fitting_results_job"] == "potential_fitting_results"

    @pytest.mark.asyncio
    async def test_foreign_key_cascade_delete(self, migration_db_session):
        """Test that ON DELETE CASCADE works correctly for foreign keys."""
        # Insert a test md_verification_jobs record
        verification_job_id = uuid4()

        await migration_db_session.execute(text("""
            INSERT INTO md_verification_jobs (id, potential_id, element_system, config, status)
            VALUES (:id, :potential_id, :element_system, :config, 'pending')
        """), {
            "id": str(verification_job_id),
            "potential_id": "test_potential_001",
            "element_system": "Cu-Ag",
            "config": '{"temperature": 300, "pressure": 0}'
        })

        await migration_db_session.commit()

        # Insert related hpc_jobs record
        await migration_db_session.execute(text("""
            INSERT INTO hpc_jobs (verification_job_id, hpc_cluster, status)
            VALUES (:verification_job_id, 'guangzhou', 'pending')
        """), {"verification_job_id": str(verification_job_id)})

        # Insert related md_simulation_results record
        await migration_db_session.execute(text("""
            INSERT INTO md_simulation_results (verification_job_id, simulation_time_ps)
            VALUES (:verification_job_id, 100.5)
        """), {"verification_job_id": str(verification_job_id)})

        await migration_db_session.commit()

        # Verify records exist
        result = await migration_db_session.execute(text("""
            SELECT COUNT(*) FROM hpc_jobs WHERE verification_job_id = :id
        """), {"id": str(verification_job_id)})
        hpc_count = result.scalar()
        assert hpc_count == 1

        # Delete parent record (should cascade)
        await migration_db_session.execute(text("""
            DELETE FROM md_verification_jobs WHERE id = :id
        """), {"id": str(verification_job_id)})
        await migration_db_session.commit()

        # Verify cascade worked - child records should be deleted
        result = await migration_db_session.execute(text("""
            SELECT COUNT(*) FROM hpc_jobs WHERE verification_job_id = :id
        """), {"id": str(verification_job_id)})
        hpc_count_after = result.scalar()
        assert hpc_count_after == 0

        result = await migration_db_session.execute(text("""
            SELECT COUNT(*) FROM md_simulation_results WHERE verification_job_id = :id
        """), {"id": str(verification_job_id)})
        sim_count_after = result.scalar()
        assert sim_count_after == 0

    @pytest.mark.asyncio
    async def test_check_constraints(self, migration_db_session):
        """Test that check constraints are enforced."""
        # Test md_verification_jobs status constraint
        with pytest.raises(Exception):  # Should raise database constraint error
            await migration_db_session.execute(text("""
                INSERT INTO md_verification_jobs
                (id, potential_id, element_system, config, status)
                VALUES (gen_random_uuid(), 'test', 'Cu', '{}', 'invalid_status')
            """))
            await migration_db_session.commit()

        await migration_db_session.rollback()

        # Test defect_analysis_results defect_type constraint
        with pytest.raises(Exception):  # Should raise database constraint error
            await migration_db_session.execute(text("""
                INSERT INTO defect_analysis_results
                (id, verification_job_id, defect_type, concentration)
                VALUES (
                    gen_random_uuid(),
                    (SELECT id FROM md_verification_jobs LIMIT 1),
                    'invalid_defect',
                    0.5
                )
            """))
            await migration_db_session.commit()

        await migration_db_session.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_rollback():
    """Test that migration can be rolled back successfully."""
    test_db_url = "postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_db_test_rollback"
    engine = create_async_engine(test_db_url, echo=False)

    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", test_db_url)

    try:
        # Upgrade
        async with engine.begin() as conn:
            await conn.run_sync(lambda conn: command.upgrade(alembic_config, "head"))

        # Verify tables exist
        async with engine.begin() as conn:
            result = await conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'md_verification_jobs'
            """))
            assert result.fetchone() is not None

        # Downgrade
        async with engine.begin() as conn:
            await conn.run_sync(lambda conn: command.downgrade(alembic_config, "9c15710c6321"))

        # Verify tables are dropped
        async with engine.begin() as conn:
            result = await conn.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = 'md_verification_jobs'
            """))
            assert result.fetchone() is None

    finally:
        await engine.dispose()
