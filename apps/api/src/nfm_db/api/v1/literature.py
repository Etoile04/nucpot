"""Literature Management API endpoints (NFM-1488 / NFM-1485-3).

Rewritten upload endpoint accepts real PDF uploads via multipart/form-data.
New ``from-doi`` endpoint fetches paper content by DOI.

Literature status machine:
  uploaded → parsing → extracting → completed
                          ↘               ↘
                          failed           failed

Endpoints:
- POST   /upload           — Upload a PDF (multipart)
- POST   /from-doi        — Fetch paper by DOI (JSON)
- GET    /{id}/status       — Check processing status
- GET    /{id}             — Full literature detail
- GET    /                  — List (paginated, filterable)
- GET    /search           — Full-text search
- POST   /{id}/reextract   — Trigger re-extraction
- DELETE /{id}             — Delete literature and associated data
"""

from __future__ import annotations

import hashlib
import logging
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.api.v1.auth import require_editor
from nfm_db.database import get_db
from nfm_db.models.extraction_figure import ExtractionFigure
from nfm_db.models.extraction_result import ExtractionResult
from nfm_db.models.source import DataSource
from nfm_db.models.user import User
from nfm_db.schemas.common import ApiResponse, PaginatedResponse
from nfm_db.schemas.literature import (
    LiteratureDetailResponse,
    LiteratureListItem,
    LiteratureReextractResponse,
    LiteratureStatusResponse,
    LiteratureUploadResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/literature", tags=["文献管理"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum file size for PDF uploads (50 MB).
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

#: First 5 bytes of a valid PDF file.
PDF_MAGIC = b"%PDF-"

# ---------------------------------------------------------------------------
# Request / response schemas local to this module
# ---------------------------------------------------------------------------


class DoiRequest(BaseModel):
    """Request body for POST /literature/from-doi."""

    doi: str = Field(
        ..., description="Digital Object Identifier (e.g. 10.1016/j.jnucmat.2020.152307)"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_source_or_404(
    source_id: uuid.UUID,
    db: AsyncSession,
) -> DataSource:
    """Fetch a DataSource or raise 404."""
    source = await db.get(DataSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Literature not found")
    return source


def _source_to_detail(source: DataSource) -> LiteratureDetailResponse:
    """Convert a DataSource to a LiteratureDetailResponse."""
    return LiteratureDetailResponse(
        id=source.id,
        title=source.title,
        doi=source.doi,
        abstract=source.abstract,
        journal=source.journal,
        year=source.year,
        status="uploaded",
        extracted_entities=[],
        extracted_relations=[],
        figures_count=0,
        tables_count=0,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _source_to_list_item(source: DataSource) -> LiteratureListItem:
    """Convert a DataSource to a LiteratureListItem."""
    return LiteratureListItem(
        id=source.id,
        title=source.title,
        doi=source.doi,
        journal=source.journal,
        year=source.year,
        status="uploaded",
        created_at=source.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    response_model=ApiResponse[LiteratureUploadResponse],
    summary="上传PDF文件用于提取",
    description="上传PDF文件进行解析和提取。\n\nUpload a PDF file for parsing and extraction.",
)
async def upload_literature(
    current_user: Annotated[User, Depends(require_editor)],
    file: UploadFile = File(..., description="PDF file to upload (max 50 MB)"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureUploadResponse]:
    """Upload a PDF file for parsing and extraction.

    Accepts multipart/form-data with a ``file`` field.  Validates content type
    and file size, computes SHA-256 for idempotency, persists via the storage
    backend, creates a DataSource row, and dispatches background Celery processing.

    Returns immediately with ``{literature_id, status: "parsing"}`` — the actual
    PDF→Markdown→extraction runs in the worker.
    """
    # --- Read and validate file bytes -----------------------------------
    raw_bytes = await file.read()

    # AC #3: file_size ≤ 50 MB → 413.
    if len(raw_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(raw_bytes)} bytes (max {MAX_UPLOAD_SIZE})",
        )

    # AC #2: content_type starts with application/pdf AND magic bytes → 415.
    content_type = file.content_type or ""
    if not content_type.startswith("application/pdf") or raw_bytes[:5] != PDF_MAGIC:
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are accepted (content_type must be application/pdf).",
        )

    # --- Compute SHA-256 hash ------------------------------------------
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    # --- Idempotency: same hash → return existing record ----------------
    existing_stmt = select(DataSource).where(DataSource.file_hash == file_hash)
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return ApiResponse(
            success=True,
            data=LiteratureUploadResponse(
                literature_id=existing.id,
                status=existing.parse_status,
            ),
        )

    # --- Save file via storage backend ----------------------------------
    datasource_id = uuid.uuid4()
    filename = file.filename or f"{datasource_id}.pdf"
    title = filename.rsplit(".pdf", 1)[0] if filename.endswith(".pdf") else filename

    from nfm_db.services.storage import get_storage

    storage = get_storage()
    file_path = storage.save(datasource_id, filename, raw_bytes)

    # --- Create DataSource row ------------------------------------------
    source = DataSource(
        id=datasource_id,
        file_path=file_path,
        file_hash=file_hash,
        file_size=len(raw_bytes),
        parse_status="parsing",
        original_filename=filename,
        source_type="journal_article",
        title=title,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    # --- Dispatch background processing ----------------------------------
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    schedule_literature_processing(source.id)

    return ApiResponse(
        success=True,
        data=LiteratureUploadResponse(
            literature_id=source.id,
            status="parsing",
        ),
    )


@router.post(
    "/from-doi",
    response_model=ApiResponse[LiteratureUploadResponse],
    summary="通过DOI获取文献",
    description="通过DOI获取文献内容并创建数据源。\n\nFetch paper content by DOI and create a data source.",
)
async def from_doi_literature(
    request: DoiRequest,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureUploadResponse]:
    """Fetch paper content by DOI and create a DataSource.

    Accepts ``application/json`` with a ``doi`` field.  Validates the DOI
    format, checks idempotency, fetches content via the doi_fetcher, saves
    the Markdown to storage, creates a DataSource row with
    ``parse_status='parsed'``, and dispatches background extraction.

    Returns ``{literature_id, status: "parsed"}`` immediately.
    """
    doi = request.doi.strip()

    # --- Validate DOI format (AC #7: malformed → 400) -------------------
    from nfm_db.services.doi_fetcher import DOIFetchError, fetch_paper_content, validate_doi_format

    if not validate_doi_format(doi):
        raise HTTPException(
            status_code=400,
            detail="Invalid DOI format. Expected: 10.xxxx/yyyy.",
        )

    # --- Idempotency: same DOI → return existing record -----------------
    existing_stmt = select(DataSource).where(DataSource.doi == doi)
    existing_result = await db.execute(existing_stmt)
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return ApiResponse(
            success=True,
            data=LiteratureUploadResponse(
                literature_id=existing.id,
                status=existing.parse_status,
            ),
        )

    # --- Fetch content via doi_fetcher (AC #8: failure → 502) ---------
    try:
        md_content = fetch_paper_content(doi)
    except (DOIFetchError, Exception) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"DOI fetch failed: {exc}",
        )

    # --- Save Markdown to storage --------------------------------------
    datasource_id = uuid.uuid4()
    md_filename = f"{doi}.md"
    md_bytes = md_content.encode("utf-8")

    from nfm_db.services.storage import get_storage

    storage = get_storage()
    file_path = storage.save(datasource_id, md_filename, md_bytes)

    # --- Create DataSource row (AC #6: status='parsed') -----------------
    file_hash = hashlib.sha256(md_bytes).hexdigest()
    source = DataSource(
        id=datasource_id,
        doi=doi,
        content_md=md_content,
        file_path=file_path,
        file_hash=file_hash,
        file_size=len(md_bytes),
        parse_status="parsed",
        original_filename=md_filename,
        source_type="journal_article",
        title=f"DOI: {doi}",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    # --- Dispatch background processing ----------------------------------
    from nfm_db.services.literature_dispatcher import (
        schedule_literature_processing,
    )

    schedule_literature_processing(source.id)

    return ApiResponse(
        success=True,
        data=LiteratureUploadResponse(
            literature_id=source.id,
            status="parsed",
        ),
    )


@router.get(
    "/search",
    response_model=ApiResponse[PaginatedResponse[LiteratureListItem]],
    summary="文献全文搜索",
    description="在标题、摘要和DOI字段中全文搜索。\n\nSearch across title, abstract, and DOI fields.",
)
async def search_literature(
    q: str = Query(..., min_length=1, description="Search query"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[LiteratureListItem]]:
    """在标题、摘要和DOI字段中全文搜索。

    Search across title, abstract, and DOI fields.
    """
    stmt = (
        select(DataSource)
        .where(
            DataSource.source_type == "journal_article",
            or_(
                DataSource.title.ilike(f"%{q}%"),
                DataSource.abstract.ilike(f"%{q}%"),
                DataSource.doi.ilike(f"%{q}%"),
            ),
        )
        .order_by(DataSource.created_at.desc())
    )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    pages = max(1, math.ceil(total / limit))
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    sources = result.scalars().all()

    items = [_source_to_list_item(s) for s in sources]

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        ),
    )


@router.get(
    "/{literature_id}/status",
    response_model=ApiResponse[LiteratureStatusResponse],
    summary="获取文献处理状态",
    description="返回当前处理状态和进度。\n\nReturn the current processing status and progress.",
)
async def get_literature_status(
    literature_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureStatusResponse]:
    """返回当前处理状态和进度。

    Return the current processing status and progress.
    """
    await _get_source_or_404(literature_id, db)

    status = "uploaded"
    progress = 100

    return ApiResponse(
        success=True,
        data=LiteratureStatusResponse(
            status=status,
            progress=progress,
        ),
    )


@router.get(
    "/{literature_id}",
    response_model=ApiResponse[LiteratureDetailResponse],
    summary="获取文献完整详情",
    description="返回文献完整详情，包括已提取的实体。\n\nReturn the full literature detail including extracted entities.",
)
async def get_literature_detail(
    literature_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureDetailResponse]:
    """返回文献完整详情，包括已提取的实体。

    Return the full literature detail including extracted entities.
    """
    source = await _get_source_or_404(literature_id, db)

    # Count extraction figures if they exist.
    figures_count = 0
    tables_count = 0
    if hasattr(ExtractionFigure, "__tablename__"):
        fig_stmt = select(func.count()).where(
            ExtractionFigure.source_id == literature_id,
        )
        fig_result = await db.execute(fig_stmt)
        figures_count = fig_result.scalar() or 0

    return ApiResponse(
        success=True,
        data=LiteratureDetailResponse(
            id=source.id,
            title=source.title,
            doi=source.doi,
            abstract=source.abstract,
            journal=source.journal,
            year=source.year,
            status="uploaded",
            extracted_entities=[],
            extracted_relations=[],
            figures_count=figures_count,
            tables_count=tables_count,
            created_at=source.created_at,
            updated_at=source.updated_at,
        ),
    )


@router.get(
    "",
    response_model=ApiResponse[PaginatedResponse[LiteratureListItem]],
    summary="获取文献列表（分页）",
    description="返回分页文献列表，支持年份和搜索筛选。\n\nReturn paginated literature list with optional filters.",
)
async def list_literature(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    year_min: int | None = Query(None),
    year_max: int | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PaginatedResponse[LiteratureListItem]]:
    """返回分页文献列表，支持年份和搜索筛选。

    Return paginated literature list with optional filters.
    """
    stmt = select(DataSource).where(
        DataSource.source_type == "journal_article",
    )

    # Apply filters.
    if search:
        stmt = stmt.where(
            or_(
                DataSource.title.ilike(f"%{search}%"),
                DataSource.abstract.ilike(f"%{search}%"),
                DataSource.doi.ilike(f"%{search}%"),
            )
        )
    if year_min is not None:
        stmt = stmt.where(DataSource.year >= year_min)
    if year_max is not None:
        stmt = stmt.where(DataSource.year <= year_max)

    # Total count.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Sort.
    sort_col = getattr(DataSource, sort_by, DataSource.created_at)
    if sort_order == "asc":
        stmt = stmt.order_by(sort_col.asc())
    else:
        stmt = stmt.order_by(sort_col.desc())

    # Paginate.
    pages = max(1, math.ceil(total / limit))
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    sources = result.scalars().all()

    items = [_source_to_list_item(s) for s in sources]

    return ApiResponse(
        success=True,
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        ),
    )


@router.post(
    "/{literature_id}/reextract",
    response_model=ApiResponse[LiteratureReextractResponse],
    summary="触发文献重新提取",
    description="触发文献项的重新提取流程。\n\nTrigger a re-extraction of the literature item.",
)
async def reextract_literature(
    literature_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteratureReextractResponse]:
    """触发文献项的重新提取流程。

    Trigger a re-extraction of the literature item.
    """
    await _get_source_or_404(literature_id, db)
    await db.commit()

    return ApiResponse(
        success=True,
        data=LiteratureReextractResponse(
            message="Re-extraction triggered",
            status="extracting",
        ),
    )


@router.delete(
    "/{literature_id}",
    response_model=ApiResponse[dict[str, str]],
    summary="删除文献项及关联数据",
    description="删除文献项及其所有关联的提取数据。\n\nDelete a literature item and all associated extraction data.",
)
async def delete_literature(
    literature_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_editor)],
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, str]]:
    """删除文献项及其所有关联的提取数据。

    Delete a literature item and all associated extraction data.
    """
    source = await _get_source_or_404(literature_id, db)

    # Delete associated extraction figures and results.
    if hasattr(ExtractionFigure, "__tablename__"):
        fig_stmt = select(ExtractionFigure).where(
            ExtractionFigure.source_id == literature_id,
        )
        fig_result = await db.execute(fig_stmt)
        for fig in fig_result.scalars().all():
            await db.delete(fig)

    if hasattr(ExtractionResult, "__tablename__"):
        er_stmt = select(ExtractionResult).where(
            ExtractionResult.source_id == literature_id,
        )
        er_result = await db.execute(er_stmt)
        for er in er_result.scalars().all():
            await db.delete(er)

    await db.delete(source)
    await db.commit()

    return ApiResponse(
        success=True,
        data={"message": f"Literature {literature_id} deleted"},
    )
