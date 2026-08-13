"""Additional edge-case tests for MD verification service (NFM-583).

Covers uncovered lines from the 89% baseline:
- get_job with owner_id filter
- list_jobs with owner_id filter
- update_job no-op (empty update values)
- HPC job not-found paths
- simulation result not-found update
- defect result update field mapping (metadata → analysis_metadata)
- fitting result update no-op
- get_job_with_results when job has no related data
- Composite queries returning empty results
- Pydantic schema validation errors
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models.md_verification import DefectType, FittingMethod, HpcJobStatus
from nfm_db.services.md_verification import (
    DefectAnalysisResultCreate,
    DefectAnalysisResultUpdate,
    HpcJobCreate,
    HpcJobUpdate,
    MDSimulationResultCreate,
    MDSimulationResultUpdate,
    MDVerificationJobCreate,
    MDVerificationJobUpdate,
    MDVerificationService,
    PotentialFittingResultCreate,
    PotentialFittingResultUpdate,
)

JOB_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")

# Deterministic IDs matching conftest seed users.
_OWNER_ID_A = uuid.UUID("a0000000-0000-0000-0000-000000000001")
_OWNER_ID_B = uuid.UUID("a0000000-0000-0000-0000-000000000003")


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    """Tests for Pydantic schema validation."""

    def test_job_create_requires_element_system(self) -> None:
        with pytest.raises(ValidationError):
            MDVerificationJobCreate(potential_id="U", config={})

    def test_job_create_requires_config(self) -> None:
        with pytest.raises(ValidationError):
            MDVerificationJobCreate(potential_id="U", element_system="UO2")

    def test_hpc_job_create_requires_verification_job_id(self) -> None:
        with pytest.raises(ValidationError):
            HpcJobCreate(hpc_cluster="cluster1")

    def test_simulation_result_create_requires_verification_job_id(self) -> None:
        with pytest.raises(ValidationError):
            MDSimulationResultCreate()

    def test_defect_result_create_requires_defect_type(self) -> None:
        with pytest.raises(ValidationError):
            DefectAnalysisResultCreate(verification_job_id=JOB_ID, concentration=0.01)

    def test_fitting_result_create_requires_fitting_method(self) -> None:
        with pytest.raises(ValidationError):
            PotentialFittingResultCreate(verification_job_id=JOB_ID, parameters={})

    def test_hpc_update_all_optional(self) -> None:
        update = HpcJobUpdate()
        assert update.hpc_job_id is None
        assert update.status is None

    def test_job_update_all_optional(self) -> None:
        update = MDVerificationJobUpdate()
        assert update.status is None
        assert update.priority is None


# ---------------------------------------------------------------------------
# Service creation tests - dict input
# ---------------------------------------------------------------------------


class TestServiceDictInput:
    """Tests for dict-based creation (already covered in main test file but adds missing lines)."""

    @pytest.mark.asyncio
    async def test_create_hpc_job_from_dict(self, db_session: AsyncSession) -> None:
        # First create a verification job
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(potential_id="UO2", element_system="UO2", config={"temp": 300})
        )
        hpc = await svc.create_hpc_job(
            {
                "verification_job_id": job.id,
                "hpc_cluster": "cluster1",
            }
        )
        assert hpc.hpc_cluster == "cluster1"

    @pytest.mark.asyncio
    async def test_create_fitting_from_dict(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(potential_id="UO2", element_system="UO2", config={})
        )
        fitting = await svc.create_fitting_result(
            {
                "verification_job_id": job.id,
                "fitting_method": "arc-dpa",
                "parameters": {"a": 1.0},
            }
        )
        assert fitting.fitting_method == FittingMethod.ARC_DPA


# ---------------------------------------------------------------------------
# get_job with owner_id filter (line 344)
# ---------------------------------------------------------------------------


class TestGetJobOwnerFilter:
    """Tests for get_job with ownership filtering."""

    @pytest.mark.asyncio
    async def test_get_job_with_wrong_owner_returns_none(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(
                potential_id="UO2", element_system="UO2", config={}, owner_id=_OWNER_ID_A
            )
        )
        result = await svc.get_job(job.id, owner_id=_OWNER_ID_B)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_job_with_correct_owner_returns_job(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(
                potential_id="UO2", element_system="UO2", config={}, owner_id=_OWNER_ID_A
            )
        )
        result = await svc.get_job(job.id, owner_id=_OWNER_ID_A)
        assert result is not None
        assert result.owner_id == _OWNER_ID_A

    @pytest.mark.asyncio
    async def test_get_job_without_owner_returns_any(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(
                potential_id="UO2", element_system="UO2", config={}, owner_id=_OWNER_ID_A
            )
        )
        result = await svc.get_job(job.id)
        assert result is not None


# ---------------------------------------------------------------------------
# list_jobs with owner_id filter (line 352)
# ---------------------------------------------------------------------------


class TestListJobsOwnerFilter:
    """Tests for list_jobs with ownership filtering."""

    @pytest.mark.asyncio
    async def test_list_jobs_by_owner(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        await svc.create_job(
            MDVerificationJobCreate(
                potential_id="A", element_system="A", config={}, owner_id=_OWNER_ID_A
            )
        )
        await svc.create_job(
            MDVerificationJobCreate(
                potential_id="B", element_system="B", config={}, owner_id=_OWNER_ID_B
            )
        )
        results = await svc.list_jobs(owner_id=_OWNER_ID_A)
        assert len(results) == 1
        assert results[0].potential_id == "A"


# ---------------------------------------------------------------------------
# update_job no-op path (lines 433-435)
# ---------------------------------------------------------------------------


class TestUpdateJobNoOp:
    """Tests for update_job when all values are None."""

    @pytest.mark.asyncio
    async def test_update_job_with_all_none_returns_existing(
        self, db_session: AsyncSession
    ) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(potential_id="UO2", element_system="UO2", config={"temp": 300})
        )
        result = await svc.update_job(job.id, MDVerificationJobUpdate())
        assert result is not None
        assert result.id == job.id
        assert result.config == {"temp": 300}


# ---------------------------------------------------------------------------
# HPC job not-found update (line 558-561)
# ---------------------------------------------------------------------------


class TestUpdateHpcJobNotFound:
    """Tests for HPC job update when job doesn't exist."""

    @pytest.mark.asyncio
    async def test_update_nonexistent_hpc_job_returns_none(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        result = await svc.update_hpc_job(uuid.uuid4(), HpcJobUpdate(status=HpcJobStatus.RUNNING))
        assert result is None

    @pytest.mark.asyncio
    async def test_update_hpc_job_all_none_returns_none(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        result = await svc.update_hpc_job(uuid.uuid4(), HpcJobUpdate())
        assert result is None


# ---------------------------------------------------------------------------
# Simulation result update not-found (line 607)
# ---------------------------------------------------------------------------


class TestUpdateSimulationResultNotFound:
    """Tests for simulation result update when not found."""

    @pytest.mark.asyncio
    async def test_update_nonexistent_sim_result_returns_none(
        self, db_session: AsyncSession
    ) -> None:
        svc = MDVerificationService(db_session)
        result = await svc.update_simulation_result(
            uuid.uuid4(),
            MDSimulationResultUpdate(final_energy=100.0),
        )
        assert result is None


# ---------------------------------------------------------------------------
# Defect result update field mapping (line 629-637)
# ---------------------------------------------------------------------------


class TestUpdateDefectResultFieldMapping:
    """Tests that defect result update maps 'metadata' to 'analysis_metadata'."""

    @pytest.mark.asyncio
    async def test_defect_update_maps_metadata_to_column(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(potential_id="UO2", element_system="UO2", config={})
        )
        await svc.create_defect_result(
            DefectAnalysisResultCreate(
                verification_job_id=job.id,
                defect_type=DefectType.VACANCY,
                concentration=0.01,
                metadata={},
            )
        )
        defects = await svc.list_defect_results(verification_job_id=job.id)
        assert len(defects) == 1

        updated = await svc.update_defect_result(
            defects[0].id,
            DefectAnalysisResultUpdate(concentration=0.02, metadata={"note": "updated"}),
        )
        assert updated is not None
        assert updated.concentration == 0.02


# ---------------------------------------------------------------------------
# Fitting result update no-op (line 917-927)
# ---------------------------------------------------------------------------


class TestUpdateFittingResultNoOp:
    """Tests for fitting result update when no values provided."""

    @pytest.mark.asyncio
    async def test_update_fitting_all_none_returns_existing(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(potential_id="UO2", element_system="UO2", config={})
        )
        fitting = await svc.create_fitting_result(
            PotentialFittingResultCreate(
                verification_job_id=job.id,
                fitting_method=FittingMethod.ARC_DPA,
                parameters={"a": 1.0, "b": 2.0},
            )
        )
        result = await svc.update_fitting_result(fitting.id, PotentialFittingResultUpdate())
        assert result is not None
        assert result.parameters == {"a": 1.0, "b": 2.0}


# ---------------------------------------------------------------------------
# get_job_with_results empty results (line 824)
# ---------------------------------------------------------------------------


class TestGetJobWithResultsEmpty:
    """Tests for composite query when job has no related data."""

    @pytest.mark.asyncio
    async def test_job_with_results_no_hpc_jobs(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        job = await svc.create_job(
            MDVerificationJobCreate(potential_id="UO2", element_system="UO2", config={})
        )
        result = await svc.get_job_with_results(job.id)
        assert result is not None
        assert result["hpc_jobs"] == []
        assert result["simulation_result"] is None
        assert result["defect_results"] == []
        assert result["fitting_results"] == []

    @pytest.mark.asyncio
    async def test_job_with_results_not_found(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        result = await svc.get_job_with_results(uuid.uuid4())
        assert result is None


# ---------------------------------------------------------------------------
# Composite queries empty (line 847, 893)
# ---------------------------------------------------------------------------


class TestCompositeQueriesEmpty:
    """Tests for composite queries returning None."""

    @pytest.mark.asyncio
    async def test_simulation_by_job_not_found(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        result = await svc.get_simulation_result_by_job(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_fitting_results_by_job_not_found(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        results = await svc.list_fitting_results_by_job(uuid.uuid4())
        assert results == []


# ---------------------------------------------------------------------------
# Delete not-found paths (lines 698, 732)
# ---------------------------------------------------------------------------


class TestDeleteNotFound:
    """Tests for delete operations when entity not found."""

    @pytest.mark.asyncio
    async def test_delete_defect_result_not_found(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        assert await svc.delete_defect_result(uuid.uuid4()) is False

    @pytest.mark.asyncio
    async def test_delete_fitting_result_not_found(self, db_session: AsyncSession) -> None:
        svc = MDVerificationService(db_session)
        assert await svc.delete_fitting_result(uuid.uuid4()) is False
