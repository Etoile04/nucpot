"""Unit tests for VerificationResultMD model and CRUD operations.

NFM-373 / NFM-369.3: Tests the new verification_results_md table
and extended md_verification_jobs columns.

Uses SQLite in-memory via conftest.py fixtures (no PostgreSQL required).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.models import (
    ExecutionStatus,
    JobType,
    MDSimulationResult,
    MDVerificationJob,
    VerificationResultMD,
)


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite session with only MD verification tables.

    Overrides conftest.db_session to avoid JSONB incompatibility
    with HPCFailoverEvent model on SQLite.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Create only MD verification related tables
    tables_to_create = [
        MDVerificationJob.__table__,
        MDSimulationResult.__table__,
        VerificationResultMD.__table__,
    ]
    async with engine.begin() as conn:
        for table in tables_to_create:
            await conn.run_sync(table.create)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        for table in reversed(tables_to_create):
            await conn.run_sync(table.drop)

    await engine.dispose()


@pytest.fixture
async def sample_job(db_session: AsyncSession) -> MDVerificationJob:
    """Create a sample MD verification job."""
    job = MDVerificationJob(
        potential_id="test_potential_001",
        element_system="Cu-Ag",
        phase="bcc",
        config={"temperature": 300, "pressure": 0},
        job_type=JobType.MD_SIMULATION,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.fixture
async def sample_simulation_result(
    db_session: AsyncSession,
    sample_job: MDVerificationJob,
) -> MDSimulationResult:
    """Create a sample MD simulation result linked to sample_job."""
    result = MDSimulationResult(
        verification_job_id=sample_job.id,
        simulation_time_ps=100.5,
        steps_completed=50000,
        final_energy=-3.45,
        final_temperature=300.0,
    )
    db_session.add(result)
    await db_session.commit()
    await db_session.refresh(result)
    return result


class TestVerificationResultMDModel:
    """Test VerificationResultMD ORM model."""

    @pytest.mark.asyncio
    async def test_create_verification_result_md(
        self,
        db_session: AsyncSession,
        sample_simulation_result: MDSimulationResult,
    ):
        """Test creating a verification_results_md record with all fields."""
        vrmd = VerificationResultMD(
            simulation_result_id=sample_simulation_result.id,
            vacancies=10,
            interstitials=8,
            frenkel_pairs=5,
            displaced_atoms=42,
            replaced_atoms=3,
            arc_dpa_b=0.85,
            arc_dpa_c=1.2,
            r_squared=0.97,
            sample_size=200,
            raw_dump_ref="/data/dumps/cascade_001.dump",
        )
        db_session.add(vrmd)
        await db_session.commit()
        await db_session.refresh(vrmd)

        assert vrmd.id is not None
        assert vrmd.vacancies == 10
        assert vrmd.interstitials == 8
        assert vrmd.frenkel_pairs == 5
        assert vrmd.displaced_atoms == 42
        assert vrmd.replaced_atoms == 3
        assert vrmd.arc_dpa_b == pytest.approx(0.85)
        assert vrmd.arc_dpa_c == pytest.approx(1.2)
        assert vrmd.r_squared == pytest.approx(0.97)
        assert vrmd.sample_size == 200
        assert vrmd.raw_dump_ref == "/data/dumps/cascade_001.dump"
        assert vrmd.created_at is not None
        assert vrmd.updated_at is not None

    @pytest.mark.asyncio
    async def test_create_with_nullable_fields(
        self,
        db_session: AsyncSession,
        sample_simulation_result: MDSimulationResult,
    ):
        """Test creating a record with only required fields (fitting metrics nullable)."""
        vrmd = VerificationResultMD(
            simulation_result_id=sample_simulation_result.id,
            vacancies=0,
            interstitials=0,
            frenkel_pairs=0,
            displaced_atoms=0,
            replaced_atoms=0,
        )
        db_session.add(vrmd)
        await db_session.commit()
        await db_session.refresh(vrmd)

        assert vrmd.arc_dpa_b is None
        assert vrmd.arc_dpa_c is None
        assert vrmd.r_squared is None
        assert vrmd.sample_size is None
        assert vrmd.raw_dump_ref is None

    @pytest.mark.asyncio
    async def test_read_by_id(
        self,
        db_session: AsyncSession,
        sample_simulation_result: MDSimulationResult,
    ):
        """Test reading a record by primary key."""
        vrmd = VerificationResultMD(
            simulation_result_id=sample_simulation_result.id,
            vacancies=15,
            interstitials=12,
            frenkel_pairs=10,
            displaced_atoms=100,
            replaced_atoms=5,
        )
        db_session.add(vrmd)
        await db_session.commit()
        await db_session.refresh(vrmd)

        found = await db_session.get(VerificationResultMD, vrmd.id)
        assert found is not None
        assert found.vacancies == 15
        assert found.frenkel_pairs == 10

    @pytest.mark.asyncio
    async def test_query_by_simulation_result_id(
        self,
        db_session: AsyncSession,
        sample_simulation_result: MDSimulationResult,
        sample_job: MDVerificationJob,
    ):
        """Test querying verification_results_md by simulation_result_id."""
        # Create multiple results for the same simulation
        for i in range(3):
            vrmd = VerificationResultMD(
                simulation_result_id=sample_simulation_result.id,
                vacancies=i * 5,
                interstitials=i * 4,
                frenkel_pairs=i * 3,
                displaced_atoms=i * 20,
                replaced_atoms=i * 2,
            )
            db_session.add(vrmd)
        await db_session.commit()

        # Query by simulation_result_id
        stmt = select(VerificationResultMD).where(
            VerificationResultMD.simulation_result_id == sample_simulation_result.id
        )
        result = await db_session.execute(stmt)
        rows = result.scalars().all()

        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_delete_verification_result_md(
        self,
        db_session: AsyncSession,
        sample_simulation_result: MDSimulationResult,
    ):
        """Test deleting a verification_results_md record directly."""
        vrmd = VerificationResultMD(
            simulation_result_id=sample_simulation_result.id,
            vacancies=1,
            interstitials=1,
            frenkel_pairs=1,
            displaced_atoms=1,
            replaced_atoms=1,
        )
        db_session.add(vrmd)
        await db_session.commit()
        vrmd_id = vrmd.id

        # Verify it exists
        found = await db_session.get(VerificationResultMD, vrmd_id)
        assert found is not None

        # Delete it
        await db_session.delete(vrmd)
        await db_session.commit()

        # Verify it's gone
        found = await db_session.get(VerificationResultMD, vrmd_id)
        assert found is None

    @pytest.mark.asyncio
    async def test_repr(self, db_session: AsyncSession, sample_simulation_result):
        """Test __repr__ output."""
        vrmd = VerificationResultMD(
            simulation_result_id=sample_simulation_result.id,
            vacancies=10,
            interstitials=8,
            frenkel_pairs=5,
            displaced_atoms=42,
            replaced_atoms=3,
            r_squared=0.97,
        )
        db_session.add(vrmd)
        await db_session.commit()
        await db_session.refresh(vrmd)

        repr_str = repr(vrmd)
        assert "VerificationResultMD" in repr_str
        assert "vacancies=10" in repr_str
        assert "frenkel_pairs=5" in repr_str
        assert "r_squared=0.97" in repr_str


class TestMDVerificationJobExtension:
    """Test extended md_verification_jobs columns."""

    @pytest.mark.asyncio
    async def test_default_job_type_is_lookup(self, db_session: AsyncSession):
        """Test that job_type defaults to 'lookup' for backward compatibility."""
        job = MDVerificationJob(
            potential_id="test_pot_002",
            element_system="Fe",
            config={"temp": 600},
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.job_type == JobType.LOOKUP

    @pytest.mark.asyncio
    async def test_md_simulation_job_type(self, db_session: AsyncSession):
        """Test creating a job with md_simulation type."""
        job = MDVerificationJob(
            potential_id="test_pot_003",
            element_system="W",
            config={"temp": 1000},
            job_type=JobType.MD_SIMULATION,
            hpc_job_id="12345",
            hpc_backend="guangzhou",
            execution_status=ExecutionStatus.RUNNING,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.job_type == JobType.MD_SIMULATION
        assert job.hpc_job_id == "12345"
        assert job.hpc_backend == "guangzhou"
        assert job.execution_status == ExecutionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_query_by_job_type(self, db_session: AsyncSession):
        """Test filtering jobs by job_type."""
        # Create lookup jobs
        for i in range(2):
            job = MDVerificationJob(
                potential_id=f"lookup_pot_{i}",
                element_system="Cu",
                config={"temp": 300},
            )
            db_session.add(job)

        # Create MD simulation jobs
        for i in range(3):
            job = MDVerificationJob(
                potential_id=f"md_pot_{i}",
                element_system="Fe",
                config={"temp": 800},
                job_type=JobType.MD_SIMULATION,
                hpc_backend="tianjin",
            )
            db_session.add(job)

        await db_session.commit()

        stmt = select(MDVerificationJob).where(MDVerificationJob.job_type == JobType.MD_SIMULATION)
        result = await db_session.execute(stmt)
        md_jobs = result.scalars().all()

        assert len(md_jobs) == 3

    @pytest.mark.asyncio
    async def test_query_by_execution_status(self, db_session: AsyncSession):
        """Test filtering jobs by execution_status."""
        job_running = MDVerificationJob(
            potential_id="pot_running",
            element_system="Ni",
            config={"temp": 500},
            job_type=JobType.MD_SIMULATION,
            execution_status=ExecutionStatus.RUNNING,
            hpc_job_id="999",
        )
        job_pending = MDVerificationJob(
            potential_id="pot_pending",
            element_system="Ni",
            config={"temp": 500},
            job_type=JobType.MD_SIMULATION,
            execution_status=ExecutionStatus.PENDING,
        )
        db_session.add(job_running)
        db_session.add(job_pending)
        await db_session.commit()

        stmt = select(MDVerificationJob).where(
            MDVerificationJob.execution_status == ExecutionStatus.RUNNING
        )
        result = await db_session.execute(stmt)
        running_jobs = result.scalars().all()

        assert len(running_jobs) == 1
        assert running_jobs[0].hpc_job_id == "999"

    @pytest.mark.asyncio
    async def test_hpc_fields_nullable(self, db_session: AsyncSession):
        """Test that hpc_job_id, hpc_backend, execution_status are nullable."""
        job = MDVerificationJob(
            potential_id="pot_nullable",
            element_system="Cu",
            config={"temp": 300},
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        assert job.hpc_job_id is None
        assert job.hpc_backend is None
        assert job.execution_status is None

    @pytest.mark.asyncio
    async def test_full_lifecycle(
        self,
        db_session: AsyncSession,
    ):
        """Test the full lifecycle: job → simulation → cascade result."""
        # 1. Create job
        job = MDVerificationJob(
            potential_id="lifecycle_pot",
            element_system="UO2",
            config={"temp": 1500},
            job_type=JobType.MD_SIMULATION,
            hpc_job_id="555",
            hpc_backend="guangzhou",
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)

        # 2. Create simulation result
        sim_result = MDSimulationResult(
            verification_job_id=job.id,
            simulation_time_ps=50.0,
            steps_completed=25000,
        )
        db_session.add(sim_result)
        await db_session.commit()
        await db_session.refresh(sim_result)

        # 3. Create cascade result
        vrmd = VerificationResultMD(
            simulation_result_id=sim_result.id,
            vacancies=25,
            interstitials=20,
            frenkel_pairs=15,
            displaced_atoms=80,
            replaced_atoms=7,
            arc_dpa_b=0.92,
            arc_dpa_c=1.1,
            r_squared=0.95,
            sample_size=500,
        )
        db_session.add(vrmd)
        await db_session.commit()
        await db_session.refresh(vrmd)

        # 4. Verify the full chain
        assert job.job_type == JobType.MD_SIMULATION
        assert sim_result.verification_job_id == job.id
        assert vrmd.simulation_result_id == sim_result.id
        assert vrmd.r_squared == pytest.approx(0.95)
