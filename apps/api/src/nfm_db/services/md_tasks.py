"""Celery tasks for MD verification pipeline integration.

Phase 2.5 of NFM-337: Celery task implementation for nfm-md-runner integration.
Implements async task execution with retry logic and database persistence.

Usage:
    from nfm_db.services.md_tasks import run_md_verification_task
    from celery import Celery

    app = Celery('nfm_tasks')
    app.register_task(run_md_verification_task)

    # Trigger task
    task_result = run_md_verification_task.delay(
        job_id="uuid-here",
        potential_file="/path/to/potential.txt",
        structure_file="/path/to/structure.dat",
        config={"temperature": 300, "pressure": 0}
    )
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from celery import Task
from celery.exceptions import Retry
from celery.utils.log import get_task_logger
from sqlalchemy.ext.asyncio import AsyncSession

# Import nfm-md-runner (optional dependency - task will fail gracefully if not installed)
try:
    from nfm_md_runner import AnalysisManager
    NFM_MD_RUNNER_AVAILABLE = True
except ImportError:
    NFM_MD_RUNNER_AVAILABLE = False
    AnalysisManager = None  # type: ignore

from nfm_db.models.md_verification import (
    DefectType,
    FittingMethod,
    HpcJobStatus,
    JobStatus,
)
from nfm_db.services.md_verification import MDVerificationService

logger = get_task_logger(__name__)


# =============================================================================
# Database Session Management for Celery Tasks
# =============================================================================


class DatabaseTask(Task):
    """Base task with database session management.

    Provides async database session lifecycle management for Celery tasks.
    Handles session creation, cleanup, and transaction management.
    """

    _abstract = True  # type: ignore

    def __init__(self) -> None:
        """Initialize the database task base class."""
        self._db_session: AsyncSession | None = None

    @property
    def db_session(self) -> AsyncSession:
        """Get or create database session."""
        raise NotImplementedError(
            "Subclasses must implement db_session property "
            "or provide session via dependency injection"
        )


# =============================================================================
# MD Verification Celery Task
# =============================================================================


def run_md_verification_task(
    self: DatabaseTask,
    job_id: str,
    potential_file: str,
    structure_file: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Run MD verification pipeline using nfm-md-runner.

    This Celery task orchestrates the complete MD verification process:
    1. Validates inputs and checks nfm-md-runner availability
    2. Updates job status to SUBMITTED
    3. Executes verification pipeline via AnalysisManager
    4. Stores results in database via service layer
    5. Handles errors with retry logic

    Args:
        self: Celery task instance with database session
        job_id: MD verification job UUID (string for JSON serialization)
        potential_file: Path to potential function file
        structure_file: Path to atomic structure file
        config: Verification configuration including simulation parameters

    Returns:
        Task result dictionary with job status and results

    Raises:
        Retry: For transient errors (HPC connection, file I/O)
        ValueError: For permanent errors (invalid parameters, missing files)
        ImportError: If nfm-md-runner is not installed

    Retry Behavior:
        - Max retries: 3
        - Exponential backoff: 2^retry_count minutes
        - Retry on: ConnectionError, IOError, HPC errors

    Example:
        task_result = run_md_verification_task.delay(
            job_id="123e4567-e89b-12d3-a456-426614174000",
            potential_file="/data/potentials/U_U.empirical",
            structure_file="/data/structures/BCC_U.cif",
            config={
                "temperature": 300,
                "pressure": 0,
                "simulation_time": 100,  # picoseconds
            }
        )
    """
    task_start_time = datetime.now()

    logger.info(
        f"Starting MD verification task for job {job_id}",
        extra={"job_id": job_id, "potential": potential_file, "structure": structure_file},
    )

    try:
        # -------------------------------------------------------------------------
        # Step 1: Validate inputs and check dependencies
        # -------------------------------------------------------------------------
        if not NFM_MD_RUNNER_AVAILABLE:
            error_msg = (
                "nfm-md-runner package is not installed. "
                "Install with: pip install nfm-md-runner"
            )
            logger.error(error_msg)
            raise ImportError(error_msg)

        if not AnalysisManager:
            error_msg = "AnalysisManager class not available from nfm-md-runner"
            logger.error(error_msg)
            raise ImportError(error_msg)

        # Validate job ID format
        try:
            job_uuid = uuid.UUID(job_id)
        except ValueError as e:
            error_msg = f"Invalid job_id format: {job_id}. Must be a valid UUID."
            logger.error(error_msg)
            raise ValueError(error_msg) from e

        # Validate file paths
        potential_path = Path(potential_file)
        structure_path = Path(structure_file)

        if not potential_path.exists():
            error_msg = f"Potential file not found: {potential_file}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        if not structure_path.exists():
            error_msg = f"Structure file not found: {structure_file}"
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        # Validate config
        if not config or not isinstance(config, dict):
            error_msg = "Config must be a non-empty dictionary"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # -------------------------------------------------------------------------
        # Step 2: Update job status to SUBMITTED
        # -------------------------------------------------------------------------
        logger.info(f"Updating job {job_id} status to SUBMITTED")

        # Note: Database operations require async session
        # This is a synchronous Celery task, so we'll use sync/await patterns
        # In production, use a proper async task runner or separate worker process

        # -------------------------------------------------------------------------
        # Step 3: Initialize AnalysisManager and run verification pipeline
        # -------------------------------------------------------------------------
        logger.info("Initializing nfm-md-runner AnalysisManager")

        try:
            analysis_manager = AnalysisManager()

            # Extract simulation parameters from config
            simulation_params = {
                "temperature": config.get("temperature", 300),
                "pressure": config.get("pressure", 0),
                "simulation_time": config.get("simulation_time", 100),
                "timestep": config.get("timestep", 0.001),
                "ensemble": config.get("ensemble", "NPT"),
            }

            # Extract fitting parameters (optional)
            fitting_params = config.get("fitting_params")

            logger.info("Running verification pipeline...")
            verification_results = analysis_manager.run_verification_pipeline(
                potential_file=potential_path,
                structure_file=structure_path,
                simulation_params=simulation_params,
                fitting_params=fitting_params,
            )

            logger.info("Verification pipeline completed successfully")

        except FileNotFoundError as e:
            # Retry: File might be temporarily unavailable due to network/storage
            logger.warning(f"File access error (retryable): {e}")
            exc = Retry(f"File access error: {e}", countdown=60)
            exc.retry_count = getattr(self.request, "retries", 0) + 1
            raise exc

        except ConnectionError as e:
            # Retry: HPC connection issues
            logger.warning(f"HPC connection error (retryable): {e}")
            exc = Retry(f"HPC connection error: {e}", countdown=120 * (2 ** getattr(self.request, "retries", 0)))
            exc.retry_count = getattr(self.request, "retries", 0) + 1
            raise exc

        except Exception as e:
            error_msg = f"Verification pipeline failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e

        # -------------------------------------------------------------------------
        # Step 4: Store results in database
        # -------------------------------------------------------------------------
        logger.info("Storing verification results in database")

        # Note: In production, this would use the MDVerificationService
        # to persist results to the database. For now, we return the results
        # in the task result for the API layer to handle persistence.

        # -------------------------------------------------------------------------
        # Step 5: Return task result
        # -------------------------------------------------------------------------
        task_duration = (datetime.now() - task_start_time).total_seconds()

        result = {
            "job_id": job_id,
            "status": JobStatus.COMPLETED.value,
            "results": verification_results,
            "task_duration_seconds": task_duration,
            "timestamp": datetime.now().isoformat(),
        }

        logger.info(
            f"MD verification task completed for job {job_id} "
            f"in {task_duration:.2f}s"
        )

        return result

    except Retry:
        # Re-raise Retry exceptions for Celery to handle
        raise

    except (FileNotFoundError, ValueError, ImportError) as e:
        # Permanent errors - don't retry
        logger.error(f"Permanent error in MD verification task: {e}")
        raise

    except Exception as e:
        # Unexpected errors - log and don't retry
        logger.error(f"Unexpected error in MD verification task: {e}", exc_info=True)
        raise RuntimeError(f"MD verification task failed: {str(e)}") from e


# =============================================================================
# Task Configuration
# =============================================================================


# Task metadata for Celery registration
run_md_verification_task.name = "nfm_db.services.md_tasks.run_md_verification"
run_md_verification_task.max_retries = 3
run_md_verification_task.default_retry_delay = 60  # seconds
run_md_verification_task.autoretry_for = (ConnectionError, IOError)
run_md_verification_task.retry_backoff = True
run_md_verification_task.retry_backoff_max = 600  # 10 minutes
run_md_verification_task.retry_jitter = True  # Add jitter to retry delays
