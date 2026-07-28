"""DFT calculation REST API endpoints (NFM-1678).

Endpoints (mounted at /api/v1/dft):

- GET  /dft/calculations              -- paginated, filterable list
- GET  /dft/calculations/{calc_id}    -- single calculation by calc_id
- GET  /dft/calculations/by-uuid/{id} -- single calculation by UUID
- POST /dft/calculations/import       -- bulk import from JSON/CSV file
- GET  /dft/stats                     -- aggregate counts by source/functional/status

The router reuses ``nfm_db.ml.dft_import.run_import`` for the import path,
so validation, idempotent bulk insert, and outlier detection all run as
designed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.database import get_db
from nfm_db.ml.dft_import import (
    ImportReport,
    run_import,
)
from nfm_db.models.dft_calculation import DFTCalculation
from nfm_db.schemas.common import ApiResponse, PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dft", tags=["DFT 计算"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class DFTCalculationResponse(BaseModel):
    """A single DFT calculation record."""

    id: UUID
    calculation_id: str
    material_id: UUID | None = None
    functional: str
    cutoff_energy: float
    kpoint_mesh: str | None = None
    kpoint_density: float | None = None
    convergence_criteria: str | None = None
    exchange_correlation: str | None = None
    pseudopotential: str | None = None
    formation_energy: float | None = None
    cohesive_energy: float | None = None
    lattice_distortion: float | None = None
    source: str | None = None
    status: str
    notes: str | None = None
    computation_metadata: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime


class DFTImportResponse(BaseModel):
    """Aggregate result of a bulk DFT import."""

    total: int
    inserted: int
    skipped: int
    failed: int
    source: str
    formation_energy_outliers: list[float] = Field(default_factory=list)
    binding_energy_outliers: list[float] = Field(default_factory=list)
    summary: str


class DFTStatsBucket(BaseModel):
    """One bucket of a grouped-count query."""

    key: str | None
    count: int


class DFTStatsResponse(BaseModel):
    """Aggregate stats over all DFT calculations."""

    total: int
    by_source: list[DFTStatsBucket]
    by_functional: list[DFTStatsBucket]
    by_status: list[DFTStatsBucket]


def _to_response(row: DFTCalculation) -> DFTCalculationResponse:
    return DFTCalculationResponse(
        id=row.id,
        calculation_id=row.calculation_id,
        material_id=row.material_id,
        functional=row.functional,
        cutoff_energy=float(row.cutoff_energy),
        kpoint_mesh=row.kpoint_mesh,
        kpoint_density=(
            float(row.kpoint_density) if row.kpoint_density is not None else None
        ),
        convergence_criteria=row.convergence_criteria,
        exchange_correlation=row.exchange_correlation,
        pseudopotential=row.pseudopotential,
        formation_energy=(
            float(row.formation_energy) if row.formation_energy is not None else None
        ),
        cohesive_energy=(
            float(row.cohesive_energy) if row.cohesive_energy is not None else None
        ),
        lattice_distortion=(
            float(row.lattice_distortion)
            if row.lattice_distortion is not None
            else None
        ),
        source=row.source,
        status=row.status,
        notes=row.notes,
        computation_metadata=row.computation_metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _import_report_to_response(report: ImportReport) -> DFTImportResponse:
    return DFTImportResponse(
        total=report.total,
        inserted=report.inserted,
        skipped=report.skipped,
        failed=report.failed,
        source=report.source,
        formation_energy_outliers=sorted(report.formation_energy_outliers),
        binding_energy_outliers=sorted(report.binding_energy_outliers),
        summary=report.summary(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/calculations",
    response_model=ApiResponse[PaginatedResponse[DFTCalculationResponse]],
    summary="分页查询 DFT 计算记录",
    description=(
        "支持按 source / functional / status / material_id 筛选，按创建时间倒序。\n\n"
        "Paginated list of DFT calculation records, newest first, "
        "with optional filters by source/functional/status/material."
    ),
)
async def list_calculations(
    pagination: PaginationParams = Depends(PaginationParams),
    source: str | None = Query(None, description="Filter by data source tag"),
    functional: str | None = Query(None, description="Filter by XC functional"),
    status_filter: Literal[
        "pending", "running", "completed", "failed", "cancelled"
    ] | None = Query(None, alias="status", description="Filter by status"),
    material_id: UUID | None = Query(None, description="Filter by linked material"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[DFTCalculationResponse]]:
    """List DFT calculation records with optional filters."""
    stmt = select(DFTCalculation).order_by(DFTCalculation.created_at.desc())
    count_stmt = select(func.count(DFTCalculation.id))

    if source is not None:
        stmt = stmt.where(DFTCalculation.source == source)
        count_stmt = count_stmt.where(DFTCalculation.source == source)
    if functional is not None:
        stmt = stmt.where(DFTCalculation.functional == functional)
        count_stmt = count_stmt.where(DFTCalculation.functional == functional)
    if status_filter is not None:
        stmt = stmt.where(DFTCalculation.status == status_filter)
        count_stmt = count_stmt.where(DFTCalculation.status == status_filter)
    if material_id is not None:
        stmt = stmt.where(DFTCalculation.material_id == material_id)
        count_stmt = count_stmt.where(DFTCalculation.material_id == material_id)

    total = (
        await db.execute(count_stmt)
    ).scalar_one()
    rows = (
        await db.execute(stmt.limit(pagination.per_page).offset(pagination.offset))
    ).scalars().all()
    pages = (total + pagination.per_page - 1) // pagination.per_page if total else 0

    return ApiResponse(
        success=True,
        data=PaginatedResponse[DFTCalculationResponse](
            items=[_to_response(r) for r in rows],
            total=total,
            page=pagination.page,
            limit=pagination.per_page,
            pages=pages,
        ),
    )


@router.get(
    "/calculations/by-uuid/{row_id}",
    response_model=ApiResponse[DFTCalculationResponse],
    summary="通过 UUID 查询单条 DFT 计算",
    description="Get a single DFT calculation by its primary UUID.",
)
async def get_calculation_by_uuid(
    row_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DFTCalculationResponse]:
    """Fetch a single calculation by UUID."""
    row = await db.get(DFTCalculation, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="DFT calculation not found")
    return ApiResponse(success=True, data=_to_response(row))


@router.get(
    "/calculations/{calc_id}",
    response_model=ApiResponse[DFTCalculationResponse],
    summary="通过 calculation_id 查询 DFT 计算",
    description=(
        "通过外部计算标识（如 VASP job ID）查询单条 DFT 计算。\n\n"
        "Get a single DFT calculation by its external ``calculation_id`` "
        "(e.g. VASP job ID)."
    ),
)
async def get_calculation_by_calc_id(
    calc_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DFTCalculationResponse]:
    """Fetch a single calculation by external calculation_id."""
    stmt = select(DFTCalculation).where(DFTCalculation.calculation_id == calc_id)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="DFT calculation not found")
    return ApiResponse(success=True, data=_to_response(row))


@router.post(
    "/calculations/import",
    response_model=ApiResponse[DFTImportResponse],
    status_code=201,
    summary="从 JSON/CSV 文件批量导入 DFT 计算",
    description=(
        "上传 JSON 或 CSV 格式的 DFT 数据文件，自动解析、验证、\n"
        "批量去重入库，并返回 ±3σ 离群点检测报告。\n\n"
        "Upload a JSON or CSV file of DFT records.  Records are validated, "
        "deduplicated against existing ``calculation_id``s, and inserted "
        "in a single bulk query.  Returns an ImportReport including "
        "±3σ outliers."
    ),
)
async def import_calculations(
    source: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="数据源标签（必填），用于审计与去重",
    ),
    file: UploadFile = File(..., description="JSON 或 CSV 文件"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DFTImportResponse]:
    """Bulk-import DFT records from an uploaded JSON/CSV file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="uploaded file has no filename")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".json", ".csv"):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file extension {suffix!r}; use .json or .csv",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    # Materialise the upload to a temporary path so dft_import's Path-based
    # parser can pick it up uniformly.  This keeps validation rules and
    # bulk-insert logic in one place (ml/dft_import.py).
    import tempfile

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix
        ) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        report = await run_import(db, tmp_path, source=source)
        await db.commit()
        return ApiResponse(
            success=True, data=_import_report_to_response(report)
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@router.get(
    "/stats",
    response_model=ApiResponse[DFTStatsResponse],
    summary="DFT 计算聚合统计",
    description=(
        "返回按 source / functional / status 分组的 DFT 计算计数。\n\n"
        "Return aggregate counts of DFT calculations grouped by source, "
        "XC functional, and status."
    ),
)
async def stats(
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DFTStatsResponse]:
    """Aggregate stats: total + by source/functional/status."""
    total = (await db.execute(select(func.count(DFTCalculation.id)))).scalar_one()

    async def _bucket(col: Any) -> list[DFTStatsBucket]:
        stmt = select(col, func.count(DFTCalculation.id)).group_by(col)
        rows = (await db.execute(stmt)).all()
        return [
            DFTStatsBucket(key=str(key) if key is not None else None, count=cnt)
            for key, cnt in rows
        ]

    by_source = await _bucket(DFTCalculation.source)
    by_functional = await _bucket(DFTCalculation.functional)
    by_status = await _bucket(DFTCalculation.status)

    return ApiResponse(
        success=True,
        data=DFTStatsResponse(
            total=total,
            by_source=by_source,
            by_functional=by_functional,
            by_status=by_status,
        ),
    )
